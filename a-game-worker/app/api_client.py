import asyncio
import datetime
import logging

import httpx

from app.config import settings
from app.model import Match

log = logging.getLogger("worker.api_client")

HEADERS = {"X-Auth-Token": settings.sports_api_key}


def make_client() -> httpx.AsyncClient:
  """Build the client. The caller owns it — `async with make_client() as c: ...` —
  so one client (and its connection pool) is reused across every call in a run."""
  return httpx.AsyncClient(
    base_url=settings.sports_api_url,
    headers=HEADERS,
    timeout=10.0,
  )


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None,
               attempts: int = 3) -> httpx.Response:
  """GET with retry. Retries transport errors and 5xx/429; a 4xx that isn't 429 is
  permanent (bad token, wrong path) — retrying it just burns rate limit."""
  for attempt in range(1, attempts + 1):
    try:
      resp = await client.get(path, params=params)
      resp.raise_for_status()
      return resp

    except httpx.HTTPStatusError as e:
      permanent = 400 <= e.response.status_code < 500 and e.response.status_code != 429
      if permanent or attempt == attempts:
        log.error("GET %s -> %s: %s", path, e.response.status_code, e.response.text)
        raise
      log.warning("GET %s -> %s, retry %d/%d", path, e.response.status_code, attempt, attempts)
      await asyncio.sleep(3)

    except httpx.RequestError as e:
      if attempt == attempts:
        log.error("GET %s failed after %d attempts: %s", path, attempts, e)
        raise
      log.warning("GET %s failed (%s), retry %d/%d", path, e, attempt, attempts)
      await asyncio.sleep(3)

  raise AssertionError("unreachable")


async def get_all_competitions(client: httpx.AsyncClient) -> list[dict]:
  """Returns the inner `competitions` list — the response is an envelope
  {"count": 13, "filters": {...}, "competitions": [...]}."""
  
  log.info("Fetching competitions...")
  resp = await _get(client, settings.sports_competitions_endpoint)
  competitions: list[dict] = resp.json()["competitions"]
  log.info("Fetched %d competitions", len(competitions))
  return competitions


async def get_matches_per_competition(league_codes: list[str], client: httpx.AsyncClient) -> list[Match]:
  """Returns upcoming matches per competition — the response is an envelope
  {"count": 13, "resultSet": {...}, "competition: {...}", "matches": [...]}."""
  
  today = datetime.datetime.now(datetime.UTC).date()
  one_week = today + datetime.timedelta(days=7)
  params = {
    "status": "SCHEDULED",
    "dateFrom": today.isoformat(),
    "dateTo": one_week.isoformat(),
  }

  matches: list[Match] = []
  for code in league_codes:
    await asyncio.sleep(2)
    url = settings.sports_competitions_matches_endpoint.format(code=code)
    resp = await _get(client, url, params=params)

    league_matches = resp.json()["matches"]
    log.info("Fetched %d matches for %s", len(league_matches), code)
    matches.extend(Match.model_validate(m) for m in league_matches)

  return matches