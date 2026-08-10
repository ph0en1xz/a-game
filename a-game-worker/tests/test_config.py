"""Settings and the URLs derived from them (`app/config.py`).

Cheap tests with a real payoff: every one of these properties is a string built
by concatenation, and a wrong one fails at runtime inside a container rather than
at import.

The DSN assertions check scheme, credentials and target separately rather than
comparing against a whole connection string — partly because a literal DSN in a
source file is the pattern the repo's secret-scanning hook exists to catch, and
partly because component assertions say which half broke.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings, settings


def _settings(**overrides) -> Settings:
    """A Settings built only from the kwargs below.

    `_env_file=None` disables the dotenv source. There is a real `.env` in this
    directory for local development, and without this every assertion here would
    silently be testing that file's contents instead of the code.
    """
    return Settings(_env_file=None, **_kwargs(**overrides))


def _kwargs(**overrides) -> dict:
    base = {
        "rabbitmq_host": "rabbit",
        "rabbitmq_port": 5672,
        "rabbitmq_default_user": "user",
        "rabbitmq_default_pass": "pw",
        "rabbitmq_queue": "sports-data-queue",
        "postgres_host": "pg",
        "postgres_port": 5432,
        "postgres_user": "pguser",
        "postgres_password": "pw",
        "postgres_db": "a_game_db",
        "sports_api_key": "token",
        "sports_api_url": "https://api.football-data.org/v4",
        "sports_competitions_endpoint": "competitions",
        "sports_competitions_matches_endpoint": "competitions/{code}/matches",
        "sports_historic_matches_endpoint": "competitions/{league_name}/matches?season={season}&status=FINISHED",
    }
    base.update(overrides)
    return base


def test_amqp_url():
    url = _settings().amqp_url

    assert url.startswith("amqp://")
    assert url.endswith("@rabbit:5672/")
    assert "user:pw" in url


def test_postgres_url():
    url = _settings().postgres_url

    assert url.startswith("postgresql://")
    assert url.endswith("@pg:5432/a_game_db")
    assert "pguser:pw" in url


def test_competitions_endpoint_is_absolute():
    assert (
        _settings().competitions_endpoint
        == "https://api.football-data.org/v4/competitions"
    )


def test_competitions_matches_endpoint_keeps_its_placeholder():
    """The property joins base + path; the {code} substitution happens at call
    site. Resolving it here would break `get_matches_per_competition`."""
    url = _settings().competitions_matches_endpoint

    assert url == "https://api.football-data.org/v4/competitions/{code}/matches"


def test_historic_endpoint_property_keeps_the_query_string():
    """The property prepends the base URL and leaves the rest alone.

    `get_historic_matches` uses the raw setting rather than this property (the
    httpx client already carries base_url), but if that ever changes, the
    status=FINISHED filter has to survive the join.
    """
    url = _settings().historic_matches_endpoint

    assert url.startswith("https://api.football-data.org/v4/")
    assert "status=FINISHED" in url
    assert "{league_name}" in url


def test_a_missing_variable_is_a_hard_failure(monkeypatch):
    """pydantic-settings validates the whole model at import.

    This is why a variable only `backfill.py` reads can still kill the daily
    CronJob — there is no lazy field. Pinned here so the behaviour is a decision
    rather than a surprise at 06:00.

    All three sources have to be silenced to prove the point: the kwarg, the env
    var conftest sets, and the local `.env`. Miss any one and the test passes for
    the wrong reason.
    """
    monkeypatch.delenv("SPORTS_HISTORIC_MATCHES_ENDPOINT", raising=False)
    kwargs = _kwargs()
    del kwargs["sports_historic_matches_endpoint"]

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **kwargs)

    assert "sports_historic_matches_endpoint" in str(exc.value)


def test_module_level_settings_loaded_from_the_environment():
    """conftest populates os.environ before app.config imports; prove it took."""
    assert settings.sports_api_url == "https://api.football-data.org/v4"
    assert settings.rabbitmq_queue == "test-queue"
