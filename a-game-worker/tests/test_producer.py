"""RabbitMQ publishing (`app/producer.py`).

The brain reads these messages with `json.loads(message.body)` and expects a bare
integer. Nothing else in the system pins that contract down, and it is exactly the
kind of thing that drifts silently — a producer that starts sending
`{"match_id": 1}` breaks the consumer with no compile-time signal at all.

aio_pika is replaced wholesale rather than run against a broker: what matters here
is the routing key, the body, and the durability flags.
"""

import json

import aio_pika
import pytest

from app import producer
from app.config import settings


class FakeExchange:
    def __init__(self):
        self.published: list[tuple[aio_pika.Message, str]] = []

    async def publish(self, message, routing_key):
        self.published.append((message, routing_key))


class FakeChannel:
    def __init__(self, exchange):
        self.default_exchange = exchange
        self.declared: list[tuple[str, bool]] = []

    async def declare_queue(self, name, durable=False):
        self.declared.append((name, durable))
        return type("Queue", (), {"name": name})()


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel
        self.closed = False

    async def channel(self):
        return self._channel

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False


@pytest.fixture
def broker(monkeypatch):
    """Swap `aio_pika.connect_robust` and hand back the fake exchange."""
    exchange = FakeExchange()
    channel = FakeChannel(exchange)
    connection = FakeConnection(channel)
    urls: list[str] = []

    async def fake_connect(url, *args, **kwargs):
        urls.append(url)
        return connection

    monkeypatch.setattr(producer.aio_pika, "connect_robust", fake_connect)

    return type(
        "Broker",
        (),
        {
            "exchange": exchange,
            "channel": channel,
            "connection": connection,
            "urls": urls,
        },
    )


async def test_publishes_one_message_per_match(broker):
    await producer.run_producer([538107, 538108, 538109])

    assert len(broker.exchange.published) == 3


async def test_body_is_a_bare_integer(broker):
    """The consumer does `match_id: int = json.loads(message.body)`.

    An object or a string here breaks the brain at runtime with a type error
    three frames deep, so the contract is asserted from the producer side.
    """
    await producer.run_producer([538107])

    message, _ = broker.exchange.published[0]
    decoded = json.loads(message.body)

    assert decoded == 538107
    assert isinstance(decoded, int)


async def test_routing_key_is_the_configured_queue(broker):
    await producer.run_producer([538107])

    _, routing_key = broker.exchange.published[0]

    assert routing_key == settings.rabbitmq_queue


async def test_queue_is_declared_durable(broker):
    """Durable queue plus persistent messages — a broker restart must not lose
    jobs the outbox has already deleted."""
    await producer.run_producer([538107])

    assert broker.channel.declared == [(settings.rabbitmq_queue, True)]


async def test_messages_are_persistent(broker):
    await producer.run_producer([538107])

    message, _ = broker.exchange.published[0]

    assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert message.content_type == "application/json"


async def test_empty_list_publishes_nothing(broker):
    """A no-change run reaches here with an empty list and must be a no-op."""
    await producer.run_producer([])

    assert broker.exchange.published == []


async def test_connection_is_closed(broker):
    await producer.run_producer([538107])

    assert broker.connection.closed is True


async def test_connection_failure_propagates(monkeypatch):
    """`main` deletes outbox rows only after `run_producer` returns.

    If a broker failure were swallowed, the events would be deleted without ever
    being published and those fixtures would never get a prediction.
    """

    async def boom(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(producer.aio_pika, "connect_robust", boom)

    with pytest.raises(ConnectionError):
        await producer.run_producer([538107])
