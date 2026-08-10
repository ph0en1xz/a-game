"""HTTP layer (`app/api_client.py`).

Driven through `httpx.MockTransport` rather than a mocking library: the functions
already take the client as an argument, so a transport swap exercises the real
request-building path — URL joining, query strings, headers — without a network.

Retry tests monkeypatch `asyncio.sleep`; the real thing waits 3s per attempt and
would make this file take half a minute.
"""

import datetime

import httpx
import pytest

from app import api_client
from app.config import settings
from tests.conftest import match_payload

BASE_URL = "https://api.football-data.org/v4"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Collapse every backoff and rate-limit pause to nothing."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", _instant)


def make_client(handler) -> httpx.AsyncClient:
    """Same shape as `api_client.make_client`, but wired to a fake transport."""
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"X-Auth-Token": "test-token"},
        transport=httpx.MockTransport(handler),
    )


# --------------------------------------------------------------------------
# make_client
# --------------------------------------------------------------------------


async def test_make_client_carries_the_base_url_and_token():
    """One client per run, reused across every call — the base URL is what lets
    every endpoint setting stay a relative path."""
    async with api_client.make_client() as client:
        assert str(client.base_url).rstrip("/") == BASE_URL
        assert client.headers["X-Auth-Token"] == settings.sports_api_key
        assert client.timeout.read == 10.0


# --------------------------------------------------------------------------
# _get retry policy
# --------------------------------------------------------------------------


async def test_get_retries_a_500_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler) as client:
        resp = await api_client._get(client, "competitions")

    assert resp.json() == {"ok": True}
    assert len(calls) == 3


async def test_get_retries_a_429():
    """429 is the free tier's rate limit — the one 4xx worth retrying."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json={"ok": True})

    async with make_client(handler) as client:
        await api_client._get(client, "competitions")

    assert len(calls) == 2


async def test_get_does_not_retry_a_403():
    """A permanent 4xx means a bad token or a season outside the free tier.

    Retrying it burns two more requests against a 10/min ceiling and still fails,
    which is exactly what you don't want mid-backfill.
    """
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, text="forbidden")

    async with make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await api_client._get(client, "competitions/PL/matches")

    assert len(calls) == 1


async def test_get_gives_up_after_three_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async with make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await api_client._get(client, "competitions")

    assert len(calls) == 3


async def test_get_gives_up_on_a_persistent_transport_error():
    """DNS failure or a dead broker pod. Three attempts, then the error escapes —
    a run that can't reach the API must fail, not return an empty list."""
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("name or service not known", request=request)

    async with make_client(handler) as client:
        with pytest.raises(httpx.ConnectError):
            await api_client._get(client, "competitions")

    assert len(calls) == 3


async def test_get_retries_transport_errors():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("no route to host", request=request)
        return httpx.Response(200, json={})

    async with make_client(handler) as client:
        await api_client._get(client, "competitions")

    assert len(calls) == 2


# --------------------------------------------------------------------------
# get_all_competitions
# --------------------------------------------------------------------------


async def test_get_all_competitions_unwraps_the_envelope():
    """The response is {"count": .., "filters": {..}, "competitions": [..]}."""

    def handler(request):
        assert request.url.path == "/v4/competitions"
        return httpx.Response(
            200,
            json={
                "count": 2,
                "filters": {},
                "competitions": [
                    {"id": 2021, "code": "PL"},
                    {"id": 2014, "code": "PD"},
                ],
            },
        )

    async with make_client(handler) as client:
        competitions = await api_client.get_all_competitions(client)

    assert [c["code"] for c in competitions] == ["PL", "PD"]


# --------------------------------------------------------------------------
# get_matches_per_competition
# --------------------------------------------------------------------------


async def test_get_matches_per_competition_builds_one_request_per_league():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"matches": [match_payload()]})

    async with make_client(handler) as client:
        matches = await api_client.get_matches_per_competition(["PL", "PD"], client)

    assert [u.path for u in seen] == [
        "/v4/competitions/PL/matches",
        "/v4/competitions/PD/matches",
    ]
    assert len(matches) == 2


async def test_get_matches_per_competition_filters_to_a_forward_window():
    """status=SCHEDULED plus a dateFrom/dateTo window.

    The window is what decides whether a run finds anything — too narrow and a
    pre-season run returns zero fixtures while still exiting 0.
    """
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"matches": []})

    async with make_client(handler) as client:
        await api_client.get_matches_per_competition(["PL"], client)

    params = seen[0].params

    now = datetime.datetime.now(datetime.UTC)
    season_start = now.year if now.month >= 7 else now.year - 1
    
    assert params["status"] == "SCHEDULED"
    assert params["season"] == str(season_start)


async def test_get_matches_per_competition_returns_parsed_models():
    def handler(request):
        return httpx.Response(200, json={"matches": [match_payload()]})

    async with make_client(handler) as client:
        matches = await api_client.get_matches_per_competition(["PL"], client)

    assert matches[0].id == 538107
    assert matches[0].homeTeam.name == "Manchester City FC"


# --------------------------------------------------------------------------
# get_historic_matches
# --------------------------------------------------------------------------


async def test_historic_request_keeps_both_query_filters():
    """Regression test for the params/query-string collision.

    The endpoint carries ?season=..&status=FINISHED. httpx REPLACES a URL's query
    when `params=` is also passed — it does not merge — so passing
    params={"season": season} alongside silently dropped status=FINISHED and
    backfilled every status. Asserting on the built URL is the only way to see it;
    the call still returns 200 either way.
    """
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"matches": []})

    async with make_client(handler) as client:
        await api_client.get_historic_matches(client, ["PL"])

    assert seen, "no request was made"
    for url in seen:
        assert url.params["status"] == "FINISHED"
        assert "season" in url.params


async def test_historic_matches_covers_every_season_and_league():
    """One request per (season, league) pair — the loop is nested."""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"matches": []})

    async with make_client(handler) as client:
        await api_client.get_historic_matches(client, ["PL", "PD"])

    seasons = {url.params["season"] for url in seen}
    paths = {url.path for url in seen}

    assert len(seen) == len(api_client.seasons) * 2
    assert len(seasons) == len(api_client.seasons)
    assert paths == {"/v4/competitions/PL/matches", "/v4/competitions/PD/matches"}


async def test_historic_matches_returns_none_when_nothing_came_back():
    """Callers branch on None, so an empty list must not leak through."""

    def handler(request):
        return httpx.Response(200, json={"matches": []})

    async with make_client(handler) as client:
        assert await api_client.get_historic_matches(client, ["PL"]) is None


async def test_historic_matches_stops_on_a_permanent_error():
    """The free tier serves three seasons; a fourth is a 403.

    `seasons` asks for four, so the 403 is expected and the loop breaks rather
    than burning the remaining requests. Whatever was collected before it is kept.
    """
    calls = []

    def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            return httpx.Response(200, json={"matches": [match_payload()]})
        return httpx.Response(403, text="not available on your plan")

    async with make_client(handler) as client:
        matches = await api_client.get_historic_matches(client, ["PL"])

    assert matches is not None
    assert len(matches) == 1
    assert len(calls) == 2


async def test_seasons_are_the_four_most_recent_completed_years():
    """`seasons` is computed at import from today's date."""
    import datetime

    this_year = datetime.datetime.now(datetime.UTC).year

    assert api_client.seasons == [this_year - i for i in range(1, 5)]
    assert this_year not in api_client.seasons


def test_auth_header_is_set_from_settings():
    assert api_client.HEADERS == {"X-Auth-Token": settings.sports_api_key}
