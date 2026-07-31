"""Broker factory: one interface, three interchangeable backends."""

from __future__ import annotations

from ..config import settings
from .base import Broker, Message
from .dbq import DbBroker

_instance: Broker | None = None


def build_broker(consumer: str | None = None) -> Broker:
    kind = settings.broker
    if kind == "kafka":
        from .kafka import KafkaBroker

        return KafkaBroker(consumer)
    if kind == "redis":
        from .redis_streams import RedisStreamBroker

        return RedisStreamBroker(consumer)
    return DbBroker(consumer)


def get_broker(consumer: str | None = None) -> Broker:
    global _instance
    if _instance is None:
        _instance = build_broker(consumer)
    return _instance


async def reset_broker() -> None:
    global _instance
    if _instance is not None:
        await _instance.stop()
    _instance = None


__all__ = ["Broker", "DbBroker", "Message", "build_broker", "get_broker", "reset_broker"]
