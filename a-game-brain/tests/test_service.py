"""Config, connection factories, consumer loop and HTTP surface.

The small stuff, but two pieces of it caused real outages.

`readyz` returning 503 while `health` returns 200 is the whole reason the pod sat
0/1 for three days after a cluster restart: the liveness probe only ever checked
`/health`, which knows nothing about RabbitMQ. And `amqp_display_url` exists so a
log line can never carry the broker password.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import consumer, main, stores
from app.config import Settings, settings


def _kwargs(**overrides) -> dict:
    base = {
        "rabbitmq_host": "rabbit",
        "rabbitmq_port": 5672,
        "rabbitmq_default_user": "user",
        "rabbitmq_default_pass": "hunter2",
        "rabbitmq_queue": "sports-data-queue",
        "redis_host": "redis",
        "redis_port": 6379,
        "postgres_host": "pg",
        "postgres_port": 5432,
        "postgres_user": "pguser",
        "postgres_password": "pw",
        "postgres_db": "a_game_db",
        "litellm_host": "litellm",
        "litellm_port": 4000,
    }
    base.update(overrides)
    return base


def _settings(**overrides) -> Settings:
    """`_env_file=None` because there is a real `.env` in this directory, and
    without it these assertions would test that file instead of the code."""
    return Settings(_env_file=None, **_kwargs(**overrides))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_litellm_url_points_at_the_v1_path():
    """The OpenAI client appends its own paths to this, so the /v1 suffix has to
    be here — the gateway speaks the OpenAI wire format."""
    assert _settings().litellm_url == "http://litellm:4000/v1"


def test_redis_url():
    assert _settings().redis_url == "redis://redis:6379"


def test_amqp_url_carries_the_credentials():
    url = _settings().amqp_url

    assert url.startswith("amqp://")
    assert "user:hunter2" in url


def test_the_display_url_has_no_credentials():
    """This is the one that gets logged. If the password ever leaks into it, it
    leaks into every pod log and every log aggregator downstream."""
    display = _settings().amqp_display_url

    assert "hunter2" not in display
    assert "user" not in display
    assert display == "amqp://rabbit:5672/"


def test_postgres_url():
    url = _settings().postgres_url

    assert url.startswith("postgresql://")
    assert url.endswith("@pg:5432/a_game_db")


def test_a_missing_variable_is_a_hard_failure(monkeypatch):
    """All three sources have to be silenced: the kwarg, the env var conftest
    sets, and the local `.env`. Miss one and this passes for the wrong reason."""
    monkeypatch.delenv("LITELLM_HOST", raising=False)
    kwargs = _kwargs()
    del kwargs["litellm_host"]

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **kwargs)

    assert "litellm_host" in str(exc.value)


def test_module_settings_came_from_the_environment():
    assert settings.litellm_host == "test-litellm"
    assert settings.rabbitmq_queue == "test-queue"


# --------------------------------------------------------------------------
# stores — connection factories
# --------------------------------------------------------------------------


def test_the_llm_client_targets_the_gateway():
    """Never a provider SDK pointed at the internet (ADR 0008). The application
    holds no provider credential — the placeholder key proves it."""
    client = stores.make_llm_client()

    assert str(client.base_url).startswith(settings.litellm_url)
    assert client.api_key == "sk-noop"


async def test_the_pg_pool_retries_before_giving_up(monkeypatch):
    """Startup ordering isn't guaranteed. The brain has to survive Postgres being
    slower to come up than it is."""
    attempts = []

    async def flaky(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("not ready")
        return "pool"

    monkeypatch.setattr(stores.asyncpg, "create_pool", flaky)
    monkeypatch.setattr(stores.asyncio, "sleep", _instant)

    assert await stores.make_pg_pool() == "pool"
    assert len(attempts) == 3


async def test_the_pg_pool_raises_after_exhausting_retries(monkeypatch):
    async def always_fails(**kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(stores.asyncpg, "create_pool", always_fails)
    monkeypatch.setattr(stores.asyncio, "sleep", _instant)

    with pytest.raises(ConnectionError):
        await stores.make_pg_pool()


async def test_redis_is_pinged_not_merely_constructed(monkeypatch):
    """`from_url` is lazy — it succeeds against a host that doesn't exist. Without
    the ping, startup would report success and the first cache write would fail.
    """
    pinged = []

    class FakeRedis:
        async def ping(self):
            pinged.append(1)
            return True

    monkeypatch.setattr(stores.redis, "from_url", lambda *a, **kw: FakeRedis())

    await stores.make_redis()

    assert pinged == [1]


async def test_redis_raises_after_exhausting_retries(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("no route")

    monkeypatch.setattr(stores.redis, "from_url", boom)
    monkeypatch.setattr(stores.asyncio, "sleep", _instant)

    with pytest.raises(ConnectionError):
        await stores.make_redis()


async def _instant(_seconds):
    return None


# --------------------------------------------------------------------------
# consumer
# --------------------------------------------------------------------------


class _State:
    def __init__(self):
        self.is_rabbitmq_ready = False
        self.pg_pool = object()
        self.redis_client = object()
        self.llm_client = object()


class _Message:
    def __init__(self, body: bytes):
        self.body = body

    def process(self):
        class _CM:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _CM()


class _QueueIterator:
    def __init__(self, messages):
        self._messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class _Queue:
    def __init__(self, messages):
        self._messages = messages

    def iterator(self):
        return _QueueIterator(self._messages)


class _Channel:
    def __init__(self, messages):
        self._messages = messages
        self.qos = None

    async def set_qos(self, prefetch_count):
        self.qos = prefetch_count

    async def declare_queue(self, name, durable=False):
        self.declared = (name, durable)
        return _Queue(self._messages)


class _Connection:
    def __init__(self, channel):
        self._channel = channel
        self.closed = False

    async def channel(self):
        return self._channel

    async def close(self):
        self.closed = True


@pytest.fixture
def broker(monkeypatch):
    def _configure(messages=()):
        channel = _Channel(list(messages))
        connection = _Connection(channel)
        processed: list[int] = []

        async def fake_connect(url):
            return connection

        async def fake_process_job(match_id, pg, redis, client):
            processed.append(match_id)

        monkeypatch.setattr(consumer.aio_pika, "connect_robust", fake_connect)
        monkeypatch.setattr(consumer, "process_job", fake_process_job)
        monkeypatch.setattr(consumer.asyncio, "sleep", _instant)

        return channel, connection, processed

    return _configure


async def test_the_consumer_decodes_a_bare_integer_body(broker):
    """The worker publishes `json.dumps(match_id)`. Any other shape breaks here."""
    _, _, processed = broker([_Message(b"538107"), _Message(b"538108")])
    state = _State()

    await consumer.run_consumer(state)

    assert processed == [538107, 538108]


async def test_prefetch_is_one(broker):
    """One unacked message at a time. Predictions are expensive and ordering
    matters more than throughput at this scale."""
    channel, _, _ = broker()

    await consumer.run_consumer(_State())

    assert channel.qos == 1


async def test_the_queue_is_declared_durable(broker):
    channel, _, _ = broker()

    await consumer.run_consumer(_State())

    assert channel.declared == (settings.rabbitmq_queue, True)


async def test_readiness_is_set_then_cleared(broker):
    """`is_rabbitmq_ready` is what /readyz reports. It has to go false again when
    the consumer stops, or a dead consumer keeps serving traffic."""
    broker()
    state = _State()

    await consumer.run_consumer(state)

    assert state.is_rabbitmq_ready is False


async def test_the_connection_is_closed_on_exit(broker):
    _, connection, _ = broker()

    await consumer.run_consumer(_State())

    assert connection.closed is True


async def test_the_initial_connect_is_retried(monkeypatch):
    """RabbitMQ is a StatefulSet and is routinely slower to start than the brain.

    Without this retry the pod crash-loops on every cluster restart — which is
    exactly what a 16-second startup race looks like from the outside.
    """
    attempts = []

    async def flaky(url):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("connection refused")
        return _Connection(_Channel([]))

    monkeypatch.setattr(consumer.aio_pika, "connect_robust", flaky)
    monkeypatch.setattr(consumer.asyncio, "sleep", _instant)

    await consumer.run_consumer(_State())

    assert len(attempts) == 3


async def test_the_consumer_gives_up_eventually(monkeypatch):
    async def always_fails(url):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(consumer.aio_pika, "connect_robust", always_fails)
    monkeypatch.setattr(consumer.asyncio, "sleep", _instant)

    with pytest.raises(ConnectionError):
        await consumer.run_consumer(_State())


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """TestClient with the lifespan's I/O stubbed out."""

    async def fake_pool():
        class _Pool:
            async def close(self):
                return None

        return _Pool()

    async def fake_redis():
        class _Redis:
            async def aclose(self):
                return None

        return _Redis()

    def fake_llm():
        class _LLM:
            async def close(self):
                return None

        return _LLM()

    async def fake_consumer(state):
        state.is_rabbitmq_ready = True
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "make_pg_pool", fake_pool)
    monkeypatch.setattr(main, "make_redis", fake_redis)
    monkeypatch.setattr(main, "make_llm_client", fake_llm)
    monkeypatch.setattr(main, "run_consumer", fake_consumer)

    with TestClient(main.app) as c:
        yield c


def test_health_is_always_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_reports_ready_once_the_consumer_is_up(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_is_503_while_the_consumer_is_down(client):
    """The structural gap worth knowing about: /health stays 200 in this state.

    Liveness only checks /health, so a brain whose consumer died is unhealthy,
    reports itself alive, and is never restarted. This test pins the readiness
    half; the liveness half is a probe configuration problem, not a code one.
    """
    main.app.state.is_rabbitmq_ready = False

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert client.get("/health").status_code == 200
