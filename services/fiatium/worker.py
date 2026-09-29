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

from confluent_kafka import Consumer, Producer
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from fiatium.config import settings
from fiatium.db import engine, one
from fiatium.processing import RetryLater, park, process

log = logging.getLogger("fiatium.worker")
stopping = threading.Event()


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


def publish():
    producer = Producer(
        {
            "bootstrap.servers": settings().broker,
            "enable.idempotence": True,
            "delivery.timeout.ms": 15000,
        }
    )

    def send(key, payload):
        errors = []

        def delivered(error, message):
            if error:
                errors.append(error)

        producer.produce(settings().topic, key=key, value=payload, on_delivery=delivered)
        remaining = producer.flush(20)
        if remaining or errors:
            raise RuntimeError("Broker acknowledgement failed")

    while not stopping.is_set():
        try:
            if not publish_one(send):
                stopping.wait(0.5)
        except (DBAPIError, RuntimeError):
            log.warning("Publisher dependency unavailable; outbox retained")
            stopping.wait(2)


def consume():
    consumer = Consumer(
        {
            "bootstrap.servers": settings().broker,
            "group.id": "payment-worker-v1",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "max.poll.interval.ms": 300000,
        }
    )
    consumer.subscribe([settings().topic])
    try:
        while not stopping.is_set():
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                log.warning("Broker poll failed")
                continue
            # Do not poll beyond an uncommitted record following a DB failure.
            while not stopping.is_set():
                try:
                    handle(message.value() or b"")
                    consumer.commit(message=message, asynchronous=False)
                    break
                except (DBAPIError, RetryLater):
                    log.warning("SQL/effect unavailable; message remains unacknowledged")
                    stopping.wait(2)
    finally:
        consumer.close()


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
