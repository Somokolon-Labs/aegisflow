"""Fault injection engine.

Faults live in the database so any replica of any service picks them up within
one second. Every service calls ``chaos.apply("<target>")`` at the points where
a real outage would hurt, which is what makes the resilience report credible.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import timedelta

from sqlalchemy import select, update

from .db import as_utc, session_scope
from .models import ChaosFault, new_id, utcnow
from .observability import chaos_active
from .resilience import TransientError

TARGETS = ("gateway", "worker", "model", "db", "broker")
MODES = ("latency", "error", "drop", "pause", "crash")
# Every service polls the fault table at most this often, so an injected fault
# is live everywhere within ~500ms.
CACHE_TTL_S = 0.5


class ChaosDrop(TransientError):
    """Simulated message loss: redeliver instead of failing the job."""


class ChaosInjected(TransientError):
    """Simulated dependency error."""


class ChaosEngine:
    def __init__(self) -> None:
        self._cache: list[dict] = []
        self._fetched_at: float = 0.0

    # ---- persistence ----------------------------------------------------
    async def inject(
        self,
        target: str,
        mode: str,
        *,
        probability: float = 1.0,
        latency_ms: int = 0,
        ttl_s: int = 60,
        note: str | None = None,
        created_by: str = "operator",
    ) -> dict:
        if target not in TARGETS:
            raise ValueError(f"unknown target '{target}' (expected one of {TARGETS})")
        if mode not in MODES:
            raise ValueError(f"unknown mode '{mode}' (expected one of {MODES})")
        fault = ChaosFault(
            id=new_id("chaos"),
            target=target,
            mode=mode,
            probability=max(0.0, min(1.0, probability)),
            latency_ms=max(0, latency_ms),
            note=note,
            created_by=created_by,
            expires_at=utcnow() + timedelta(seconds=max(1, ttl_s)),
        )
        async with session_scope() as session:
            session.add(fault)
        self._fetched_at = 0.0
        return serialise(fault)

    async def clear(self, target: str | None = None, fault_id: str | None = None) -> int:
        stmt = update(ChaosFault).where(ChaosFault.cleared_at.is_(None)).values(cleared_at=utcnow())
        if target:
            stmt = stmt.where(ChaosFault.target == target)
        if fault_id:
            stmt = stmt.where(ChaosFault.id == fault_id)
        async with session_scope() as session:
            result = await session.execute(stmt)
        self._fetched_at = 0.0
        return int(result.rowcount or 0)

    async def list_active(self, force: bool = False) -> list[dict]:
        if not force and (time.monotonic() - self._fetched_at) < CACHE_TTL_S:
            return self._cache
        now = utcnow()
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(ChaosFault)
                    .where(ChaosFault.cleared_at.is_(None), ChaosFault.expires_at > now)
                    .order_by(ChaosFault.created_at.desc())
                )
            ).scalars().all()
        self._cache = [serialise(row) for row in rows]
        self._fetched_at = time.monotonic()
        counts: dict[str, int] = {t: 0 for t in TARGETS}
        for fault in self._cache:
            counts[fault["target"]] = counts.get(fault["target"], 0) + 1
        for target, count in counts.items():
            chaos_active.labels(target=target).set(count)
        return self._cache

    async def history(self, limit: int = 25) -> list[dict]:
        async with session_scope() as session:
            rows = (
                await session.execute(select(ChaosFault).order_by(ChaosFault.created_at.desc()).limit(limit))
            ).scalars().all()
        return [serialise(row) for row in rows]

    # ---- runtime hooks --------------------------------------------------
    async def faults_for(self, target: str) -> list[dict]:
        return [f for f in await self.list_active() if f["target"] == target]

    async def is_paused(self, target: str) -> bool:
        return any(f["mode"] == "pause" for f in await self.faults_for(target))

    async def is_disrupted(self, target: str) -> bool:
        """True when the target should be treated as unreachable right now."""
        return any(f["mode"] in ("error", "drop", "pause", "crash") for f in await self.faults_for(target))

    async def apply(self, target: str) -> None:
        """Inject whatever the operator asked for at this call site."""
        for fault in await self.faults_for(target):
            if random.random() > fault["probability"]:
                continue
            mode = fault["mode"]
            if mode == "latency":
                await asyncio.sleep(fault["latency_ms"] / 1000)
            elif mode == "error":
                raise ChaosInjected(f"chaos: injected {target} error ({fault['id']})")
            elif mode == "drop":
                raise ChaosDrop(f"chaos: dropped message at {target} ({fault['id']})")
            elif mode == "crash" and os.environ.get("AEGISFLOW_ALLOW_CRASH", "0") == "1":
                os._exit(9)


def serialise(fault: ChaosFault) -> dict:
    expires = as_utc(fault.expires_at)
    remaining = 0
    if expires:
        remaining = max(0, int((expires - utcnow()).total_seconds()))
    return {
        "id": fault.id,
        "target": fault.target,
        "mode": fault.mode,
        "probability": fault.probability,
        "latency_ms": fault.latency_ms,
        "note": fault.note,
        "created_by": fault.created_by,
        "created_at": as_utc(fault.created_at).isoformat() if fault.created_at else None,
        "expires_at": expires.isoformat() if expires else None,
        "expires_in_s": remaining,
        "cleared_at": as_utc(fault.cleared_at).isoformat() if fault.cleared_at else None,
        "active": fault.cleared_at is None and remaining > 0,
    }


chaos = ChaosEngine()
