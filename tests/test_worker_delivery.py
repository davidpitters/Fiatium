import threading
from types import SimpleNamespace

import pytest
from confluent_kafka import KafkaError, KafkaException
from fiatium.processing import RetryLater
from fiatium.worker import DeliveryRetry, consume_session, consumer_config, send_to_broker
from sqlalchemy.exc import OperationalError


class ConsumerDouble:
    def __init__(self, stop, commit_error=None):
        self.stop = stop
        self.commit_error = commit_error
        self.calls = []

    def subscribe(self, topics):
        self.calls.append("subscribe")

    def poll(self, timeout):
        self.calls.append("poll")
        return SimpleNamespace(error=lambda: None, value=lambda: b"event")

    def commit(self, *, message, asynchronous):
        assert not asynchronous
        self.calls.append("commit")
        self.stop.set()
        if isinstance(self.commit_error, Exception):
            raise self.commit_error
        return [SimpleNamespace(error=self.commit_error)]

    def close(self):
        self.calls.append("close")


def test_sql_effect_precedes_offset_commit_and_close():
    stop = threading.Event()
    consumer = ConsumerDouble(stop)
    consume_session(consumer, lambda raw: consumer.calls.append("effect"), stop)
    assert consumer.calls == ["subscribe", "poll", "effect", "commit", "close"]
    assert consumer_config()["enable.auto.commit"] is False
    assert consumer_config()["enable.auto.offset.store"] is False


@pytest.mark.parametrize("failure", [RetryLater("retry"), OperationalError("", {}, Exception())])
def test_failed_effect_cannot_commit_or_poll_another_message(failure):
    stop = threading.Event()
    consumer = ConsumerDouble(stop)

    def fail(raw):
        raise failure

    with pytest.raises(type(failure)):
        consume_session(consumer, fail, stop)
    assert consumer.calls == ["subscribe", "poll", "close"]


@pytest.mark.parametrize(
    "error", [KafkaError(KafkaError._TIMED_OUT), KafkaException(KafkaError(KafkaError._TRANSPORT))]
)
def test_commit_errors_end_assignment_without_advancing(error):
    stop = threading.Event()
    consumer = ConsumerDouble(stop, error)
    with pytest.raises((DeliveryRetry, KafkaException)):
        consume_session(consumer, lambda raw: None, stop)
    assert consumer.calls == ["subscribe", "poll", "commit", "close"]


@pytest.mark.parametrize("mode", ["timeout", "callback_error", "missing_callback", "queue_full"])
def test_publisher_requires_delivery_acknowledgement(mode):
    class ProducerDouble:
        polled = False

        def poll(self, timeout):
            assert timeout == 0
            self.polled = True

        def produce(self, topic, *, key, value, on_delivery):
            if mode == "queue_full":
                raise BufferError("queue full")
            self.callback = on_delivery

        def flush(self, timeout):
            if mode == "callback_error":
                self.callback(KafkaError(KafkaError._MSG_TIMED_OUT), None)
            return 1 if mode == "timeout" else 0

    producer = ProducerDouble()
    with pytest.raises((DeliveryRetry, BufferError)):
        send_to_broker(producer, "payment", b"event")
    assert producer.polled == (mode == "queue_full")


def test_publisher_accepts_confirmed_delivery():
    class ProducerDouble:
        def produce(self, topic, *, key, value, on_delivery):
            self.callback = on_delivery

        def flush(self, timeout):
            self.callback(None, None)
            return 0

    send_to_broker(ProducerDouble(), "payment", b"event")


def test_worker_rejoins_after_dependency_failure(monkeypatch):
    import fiatium.worker as worker

    stop = threading.Event()
    sessions = []
    waits = []

    class StopWithoutDelay:
        def is_set(self):
            return stop.is_set()

        def wait(self, seconds):
            waits.append(seconds)
            return stop.is_set()

    def factory(config):
        return object()

    def session(consumer):
        sessions.append(consumer)
        if len(sessions) == 1:
            raise DeliveryRetry("Offset ownership lost")
        stop.set()

    monkeypatch.setattr(worker, "stopping", StopWithoutDelay())
    monkeypatch.setattr(worker, "Consumer", factory)
    monkeypatch.setattr(worker, "consume_session", session)
    worker.consume()
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert waits == [2]
