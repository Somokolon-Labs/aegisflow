"""Kafka / Redpanda backend - the production path.

Offsets are committed only after the worker has durably written the result, so
a rebalance or crash replays the message instead of dropping it. Delayed
retries are re-published to a retry topic carrying ``not_before`` so the retry
schedule survives a consumer restart.
"""

from __future__ import annotations

import asyncio
import json
import time

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition

from ..config import settings
from .base import Broker, Message


class KafkaBroker(Broker):
    kind = "kafka"

    def __init__(self, consumer: str | None = None) -> None:
        self.consumer_name = consumer or settings.instance_id
        self._producer: AIOKafkaProducer | None = None
        self._consumers: dict[str, AIOKafkaConsumer] = {}

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap,
            enable_idempotence=True,
            acks="all",
            linger_ms=5,
            compression_type="gzip",
            request_timeout_ms=8000,
        )
        await self._producer.start()

    async def stop(self) -> None:
        for consumer in self._consumers.values():
            await consumer.stop()
        self._consumers.clear()
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, key: str, payload: dict, *, delay_s: float = 0.0) -> None:
        if self._producer is None:
            raise RuntimeError("kafka broker not started")
        body = dict(payload)
        target = topic
        if delay_s > 0:
            body["not_before"] = time.time() + delay_s
            target = settings.topic_retry
        await self._producer.send_and_wait(target, json.dumps(body).encode(), key=key.encode())

    async def _consumer_for(self, topic: str) -> AIOKafkaConsumer:
        if topic not in self._consumers:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=settings.kafka_bootstrap,
                group_id=settings.consumer_group,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                max_poll_records=settings.worker_prefetch,
                session_timeout_ms=20000,
                heartbeat_interval_ms=5000,
            )
            await consumer.start()
            self._consumers[topic] = consumer
        return self._consumers[topic]

    async def poll(self, topic: str, *, max_messages: int = 8, wait_s: float = 1.0) -> list[Message]:
        consumer = await self._consumer_for(topic)
        batches = await consumer.getmany(timeout_ms=int(wait_s * 1000), max_records=max_messages)
        messages: list[Message] = []
        loop_time = asyncio.get_running_loop().time()
        for partition, records in batches.items():
            for record in records:
                payload = json.loads(record.value.decode())
                not_before = payload.pop("not_before", None)
                if not_before and not_before > time.time():
                    await asyncio.sleep(min(2.0, not_before - time.time()))
                messages.append(
                    Message(
                        id=f"{partition.topic}-{partition.partition}-{record.offset}",
                        topic=topic,
                        key=(record.key or b"").decode(),
                        payload=payload,
                        attempt=int(payload.get("attempt", 0)) + 1,
                        handle=(partition, record.offset),
                        received_at=loop_time,
                        meta={"partition": partition.partition, "offset": record.offset},
                    )
                )
        return messages

    async def ack(self, message: Message) -> None:
        partition, offset = message.handle
        consumer = await self._consumer_for(message.topic)
        await consumer.commit({partition: offset + 1})

    async def nack(self, message: Message, *, delay_s: float = 0.0) -> None:
        payload = dict(message.payload)
        payload["attempt"] = message.attempt
        await self.publish(message.topic, message.key, payload, delay_s=max(delay_s, 0.05))
        await self.ack(message)

    async def depth(self, topic: str) -> int:
        try:
            consumer = await self._consumer_for(topic)
            partitions = consumer.assignment() or {
                TopicPartition(topic, p) for p in (consumer.partitions_for_topic(topic) or set())
            }
            if not partitions:
                return -1
            end_offsets = await consumer.end_offsets(list(partitions))
            total = 0
            for tp, end in end_offsets.items():
                committed = await consumer.committed(tp)
                total += max(0, end - (committed or 0))
            return total
        except Exception:
            return -1

    async def healthy(self) -> bool:
        return self._producer is not None and not self._producer._closed
