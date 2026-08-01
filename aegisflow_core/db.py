"""Async database access shared by all services."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _prepare_sqlite_path(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    tail = url.split("///")[-1]
    if tail and tail != ":memory:":
        Path(tail).parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _prepare_sqlite_path(settings.database_url)
        kwargs: dict = {"echo": False, "pool_pre_ping": True, "future": True}
        if settings.is_sqlite:
            kwargs["connect_args"] = {"timeout": 30}
        else:
            kwargs.update(
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_pool_size,
                pool_recycle=1800,
                connect_args={"server_settings": {"application_name": f"aegisflow-{settings.service_name}"}},
            )
        _engine = create_async_engine(settings.database_url, **kwargs)

        if settings.is_sqlite:
            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - infra glue
                # SQLite is the zero-infrastructure mode. WAL + a generous busy
                # timeout let several processes share the file; on a laptop we
                # also relax fsync, which is what makes the local queue keep up
                # with a few hundred writes per second.
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=" + ("OFF" if settings.app_env == "local" else "NORMAL"))
                cur.execute("PRAGMA busy_timeout=15000")
                cur.execute("PRAGMA wal_autocheckpoint=4000")
                cur.execute("PRAGMA temp_store=MEMORY")
                cur.execute("PRAGMA cache_size=-64000")
                cur.close()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session: commit on success, rollback on any error."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def missing_tables() -> list[str]:
    """Which expected tables do not exist yet. A read, so it takes no write lock."""

    def _inspect(sync_conn) -> list[str]:
        present = set(inspect(sync_conn).get_table_names())
        return sorted(set(Base.metadata.tables) - present)

    async with get_engine().connect() as conn:
        return await conn.run_sync(_inspect)


async def init_db(retries: int = 10, base_delay_s: float = 0.4) -> None:
    """Create the schema safely when several processes boot at once.

    ``create_all`` checks for tables and then creates them, which is a
    time-of-check/time-of-use race: gateway, workers, relay and lab start
    together, several pass the check, and one loses the CREATE. Kubernetes
    replicas against an empty database hit the same thing.

    Checking first means only the first process takes a write lock; the others
    read, see a complete schema and move on. If they catch the winner
    mid-creation they wait and re-check rather than fighting for the lock.
    """
    for attempt in range(1, retries + 1):
        try:
            missing = await missing_tables()
        except Exception:
            missing = sorted(Base.metadata.tables)

        if not missing:
            return

        try:
            async with get_engine().begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            if not await missing_tables():
                return
        except (OperationalError, ProgrammingError, IntegrityError):
            # Another process is creating the schema right now; wait it out.
            pass

        if attempt == retries:
            raise RuntimeError(f"schema still incomplete after {retries} attempts: {missing}")
        await asyncio.sleep(min(2.0, base_delay_s * attempt))


async def ping() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalise everything to aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    aware = as_utc(value)
    return aware.isoformat() if aware else None


def supports_skip_locked() -> bool:
    return not settings.is_sqlite


os.environ.setdefault("PYTHONUNBUFFERED", "1")
