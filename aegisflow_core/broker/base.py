"""Broker abstraction.

Three interchangeable backends implement it:

* ``db``    - durable queue inside Postgres/SQLite with lease based visibility
              timeouts. Zero extra infrastructure, used for local dev and for
              cheap single-node deployments.
* ``redis`` - Redis Streams with consumer groups.
* ``kafka`` - Kafka / Redpanda with manual offset commits (production path).

Every backend guarantees at-least-once delivery. Effectively-once processing is
achieved one layer up, by the inbox/dedupe table in the worker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    id: str
    topic: str
    key: str
    payload: dict
    attempt: int = 1
    handle: Any = None
    received_at: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.topic}:{self.id}"


class Broker(ABC):
    kind: str = "base"

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def publish(self, topic: str, key: str, payload: dict, *, delay_s: float = 0.0) -> None: ...

    @abstractmethod
    async def poll(self, topic: str, *, max_messages: int = 8, wait_s: float = 1.0) -> list[Message]: ...

    @abstractmethod
    async def ack(self, message: Message) -> None: ...

    @abstractmethod
    async def nack(self, message: Message, *, delay_s: float = 0.0) -> None: ...

    async def depth(self, topic: str) -> int:
        return -1

    async def healthy(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"kind": self.kind}
