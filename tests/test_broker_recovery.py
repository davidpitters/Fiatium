"""Opt-in real-broker acceptance drill; never substitutes a mocked transport."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from fiatium.config import settings
from fiatium.db import engine, one
from fiatium.domain import InvoiceInput, PaymentInput
from fiatium.payments import create_invoice, submit_payment
from fiatium.reconciliation import reconcile
from fiatium.worker import consume_session, consumer_config, handle, publish_one, send_to_broker
from sqlalchemy import text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_BROKER_TESTS") != "1",
        reason="Requires an explicitly enabled real broker",
    ),
]


@pytest.mark.parametrize("boundary", ["broker-gap", "broker-publish-gap"])
def test_real_broker_redelivery_after_process_kill(boundary, tmp_path, monkeypatch):
    suffix = uuid4().hex
    topic, group, tenant = f"fiatium.drill.{suffix}", f"drill-{suffix}", f"test-{suffix}"
    admin = AdminClient({"bootstrap.servers": settings().broker})
    admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])[topic].result(30)
    monkeypatch.setattr(settings(), "topic", topic)
    consumer = None
    child = None
    try:
        customer_id = str(uuid4())
        with engine().begin() as conn:
            conn.execute(
                text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,'Broker drill')"),
                {"id": customer_id, "t": tenant},
            )
        invoice = create_invoice(tenant, InvoiceInput(customer_id=customer_id, amount=7900))
        payment, _ = submit_payment(
            tenant, PaymentInput(invoice_id=invoice["id"], amount=7900), str(uuid4()), str(uuid4())
        )
        with engine().connect() as conn:
            event = json.loads(
                one(conn, "SELECT payload FROM outbox_events WHERE payment_id=:p", p=payment["id"])[
                    "payload"
                ]
            )
        producer = Producer(
            {
                "bootstrap.servers": settings().broker,
                "enable.idempotence": True,
                "delivery.timeout.ms": 15000,
            }
        )

        def send(key, payload):
            send_to_broker(producer, key, payload)

        if boundary == "broker-gap":
            assert publish_one(send, event["id"])
        event_file, checkpoint = tmp_path / "event.json", tmp_path / "ready"
        event_file.write_text(json.dumps(event), encoding="utf-8")
        environment = {**os.environ, "FIATIUM_TOPIC": topic}
        helper = Path(__file__).parent / "helpers" / "crash_worker.py"
        with (tmp_path / "child.log").open("w", encoding="utf-8") as output:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(helper),
                    boundary,
                    str(event_file),
                    str(checkpoint),
                    "--group",
                    group,
                ],
                env=environment,
                stdout=output,
                stderr=output,
            )
            deadline = time.monotonic() + 40
            while not checkpoint.exists():
                assert child.poll() is None, "Drill child exited before its durable checkpoint"
                assert time.monotonic() < deadline, "Broker checkpoint timed out"
                time.sleep(0.1)
            child.kill()
            child.wait(timeout=10)
        if boundary == "broker-publish-gap":
            with engine().connect() as conn:
                assert (
                    one(conn, "SELECT sent_at FROM outbox_events WHERE id=:id", id=event["id"])[
                        "sent_at"
                    ]
                    is None
                )
            assert publish_one(send, event["id"])
        seen = []
        stop = threading.Event()
        expected = 1 if boundary == "broker-gap" else 2

        def receive(raw):
            assert json.loads(raw)["id"] == event["id"]
            handle(raw)
            seen.append(raw)
            if len(seen) == expected:
                stop.set()

        config = consumer_config()
        config.update({"group.id": group, "session.timeout.ms": 6000})
        probe = Consumer(config)
        try:
            assert probe.committed([TopicPartition(topic, 0)], timeout=10)[0].offset < 0
        finally:
            probe.close()
        consumer = Consumer(config)
        timer = threading.Timer(40, stop.set)
        timer.start()
        try:
            consume_session(consumer, receive, stop)
        finally:
            consumer = None  # consume_session owns close, including on failure
            timer.cancel()
        assert len(seen) == expected
        probe = Consumer(config)
        try:
            assert probe.committed([TopicPartition(topic, 0)], timeout=10)[0].offset == expected
        finally:
            probe.close()
        with engine().connect() as conn:
            assert (
                one(
                    conn,
                    "SELECT COUNT(*) AS n FROM journal_transactions "
                    "WHERE tenant=:t AND operation=:op",
                    t=tenant,
                    op=f"settlement:{payment['id']}",
                )["n"]
                == 1
            )
            assert (
                one(conn, "SELECT COUNT(*) AS n FROM processor_attempts WHERE tenant=:t", t=tenant)[
                    "n"
                ]
                == 1
            )
        assert reconcile(tenant)["findings"] == []
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if consumer is not None:
            consumer.close()
        # Only the randomly generated drill topic is removed. SQL audit history remains.
        admin.delete_topics([topic])[topic].result(30)
