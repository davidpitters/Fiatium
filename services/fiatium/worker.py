"""Run `python -m fiatium.worker publish|consume|local-once`."""

import argparse
import hashlib
import json
import logging
import signal
import threading
from datetime import datetime
from typing import Literal
from uuid import UUID

from confluent_kafka import Consumer, KafkaException, Producer
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from fiatium.config import settings
from fiatium.db import engine, one
from fiatium.processing import RetryLater, park, process

log = logging.getLogger("fiatium.worker")
stopping = threading.Event()


class DeliveryRetry(RuntimeError):
    """A broker acknowledgement was not confirmed; keep the record replayable."""


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    version: Literal[1]
    type: Literal["payment.requested"]
    tenant: str
    payment_id: UUID
    partition_key: UUID
    correlation_id: UUID
    causation_id: UUID | None
    occurred_at: datetime


def handle(raw: bytes):
    """Return only after an effect or parking record is durable; SQL errors propagate."""
    try:
        event = Event.model_validate_json(raw).model_dump(mode="json")
        if event["partition_key"] != event["payment_id"] or len(event["tenant"]) > 64:
            raise ValueError("Invalid event routing")
    except (ValidationError, ValueError):
        park(hashlib.sha256(raw).hexdigest(), None, None, "Invalid event envelope")
        return
    for attempt in range(3):
        try:
            process(event)
            # Explicit duplicate callback scenario exercises the identical business operation.
            with engine().connect() as conn:
                payment = one(
                    conn,
                    "SELECT scenario FROM payments WHERE tenant=:t AND id=:p",
                    t=event["tenant"],
                    p=event["payment_id"],
                )
            if payment and payment["scenario"] == "duplicate":
                process(event)
            log.info("processed event=%s correlation=%s", event["id"], event["correlation_id"])
            return
        except RetryLater:
            if attempt < 2 and stopping.wait(0.25 * 2**attempt):
                raise
        except ValueError:
            park(event["id"], event["tenant"], event["payment_id"], "Unknown payment reference")
            return
    park(
        event["id"],
        event["tenant"],
        event["payment_id"],
        "Retry budget exhausted; inspect processor and journal evidence",
    )


def publish_one(send, event_id=None):
    """Hold one row until broker acknowledgement. A crash after send causes redelivery."""
    with engine().begin() as conn:
        row = one(
            conn,
            """SELECT TOP (1) * FROM outbox_events WITH (UPDLOCK,READPAST,ROWLOCK)
            WHERE sent_at IS NULL AND (:event_id IS NULL OR id=:event_id)
            ORDER BY created_at,id""",
            event_id=event_id,
        )
        if not row:
            return False
        send(row["payment_id"], row["payload"].encode())
        conn.execute(
            text("UPDATE outbox_events SET sent_at=SYSUTCDATETIME() WHERE id=:id"),
            {"id": row["id"]},
        )
        return True


def send_to_broker(producer, key, payload):
    outcomes = []

    def delivered(error, message):
        outcomes.append(error)

    try:
        producer.produce(settings().topic, key=key, value=payload, on_delivery=delivered)
    except BufferError:
        # Serve pending delivery callbacks so a full local queue can drain before retry.
        producer.poll(0)
        raise
    remaining = producer.flush(20)
    if remaining or len(outcomes) != 1 or outcomes[0] is not None:
        raise DeliveryRetry("Broker acknowledgement was not confirmed")


def publish():
    producer = Producer(
        {
            "bootstrap.servers": settings().broker,
            "enable.idempotence": True,
            "delivery.timeout.ms": 15000,
        }
    )

    while not stopping.is_set():
        try:
            if not publish_one(lambda key, payload: send_to_broker(producer, key, payload)):
                stopping.wait(0.5)
        except (DBAPIError, DeliveryRetry, KafkaException, BufferError):
            log.warning("Publisher dependency unavailable; outbox retained")
            stopping.wait(2)


def consumer_config():
    return {
        "bootstrap.servers": settings().broker,
        "group.id": "payment-worker-v1",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "max.poll.interval.ms": 300000,
    }


def consume_session(consumer, handler=handle, stop=stopping):
    """A dependency failure ends this assignment without polling past the failed record."""
    try:
        consumer.subscribe([settings().topic])
        while not stop.is_set():
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                raise KafkaException(message.error())
            handler(message.value() or b"")
            offsets = consumer.commit(message=message, asynchronous=False)
            if not offsets or any(partition.error for partition in offsets):
                raise DeliveryRetry("Offset acknowledgement was not confirmed")
    finally:
        consumer.close()


def consume():
    while not stopping.is_set():
        try:
            consume_session(Consumer(consumer_config()))
        except (DBAPIError, RetryLater, DeliveryRetry, KafkaException):
            # Rejoin after failures instead of holding a dead assignment through max.poll.interval.
            # Closing never auto-commits: redelivery is handled by the durable SQL guards.
            log.warning("Worker dependency/commit failed; rejoining from committed offsets")
            stopping.wait(2)


def local_once():
    """Explicit development transport; does not claim to test Kafka delivery."""
    count = 0
    # Avoid holding the publisher row lock while the worker opens its own transactions.
    while True:
        with engine().connect() as conn:
            row = one(
                conn,
                "SELECT TOP (1) id,payload FROM outbox_events "
                "WHERE sent_at IS NULL ORDER BY created_at,id",
            )
        if not row:
            break
        handle(row["payload"].encode())
        with engine().begin() as conn:
            conn.execute(
                text("UPDATE outbox_events SET sent_at=SYSUTCDATETIME() WHERE id=:id"),
                {"id": row["id"]},
            )
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("publish", "consume", "local-once"))
    mode = parser.parse_args().mode
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    if mode == "local-once":
        print(json.dumps({"processed": local_once(), "transport": "direct-development"}))
    else:
        {"publish": publish, "consume": consume}[mode]()


if __name__ == "__main__":
    main()
