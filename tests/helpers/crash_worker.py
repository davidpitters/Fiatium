"""Test-only crash checkpoints. Never imported by application code."""

import argparse
import json
import threading
from pathlib import Path

from confluent_kafka import Consumer, Producer
from fiatium.config import settings
from fiatium.processing import RetryLater, process
from fiatium.worker import consume_session, consumer_config, handle, publish_one, send_to_broker

parser = argparse.ArgumentParser()
parser.add_argument(
    "mode",
    choices=("processor-gap", "effect-gap", "outbox-gap", "broker-gap", "broker-publish-gap"),
)
parser.add_argument("event", type=Path)
parser.add_argument("checkpoint", type=Path)
parser.add_argument("--group")
args = parser.parse_args()
event = json.loads(args.event.read_text(encoding="utf-8"))


def checkpoint():
    args.checkpoint.write_text("ready", encoding="utf-8")
    threading.Event().wait(120)
    raise RuntimeError("Parent did not kill the test worker within its deadline")


if args.mode == "processor-gap":
    try:
        process(event, fail_before_post=True)
    except RetryLater:
        checkpoint()
elif args.mode == "effect-gap":
    process(event)
    checkpoint()
elif args.mode == "outbox-gap":
    publish_one(lambda key, payload: checkpoint(), event["id"])
elif args.mode == "broker-publish-gap":
    producer = Producer(
        {
            "bootstrap.servers": settings().broker,
            "enable.idempotence": True,
            "delivery.timeout.ms": 15000,
        }
    )

    def publish_then_wait(key, payload):
        send_to_broker(producer, key, payload)
        checkpoint()

    publish_one(publish_then_wait, event["id"])
elif args.mode == "broker-gap":

    def handle_then_wait(raw):
        received = json.loads(raw)
        if received["id"] != event["id"]:
            raise RuntimeError("Isolated drill topic contained an unexpected event")
        handle(raw)
        checkpoint()

    config = consumer_config()
    config.update({"group.id": args.group, "session.timeout.ms": 6000})
    consume_session(Consumer(config), handler=handle_then_wait)
