import httpx
import asyncio

import logging

from app.config import settings

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


async def get_competition_matches(self) -> httpx.Response:
  log.info("Getting competition matches...")
  params = {
    "status": "SCHEDULED",
    "dateFrom": "2024-06-01",
    "dateTo": "2024-06-30"
  }
  
  client = await self.https_client()

  max_retries = 3
  response = None
  while max_retries > 0:
    try:
      log.info(f"Making GET request to {settings.competitions_matches_endpoint} with params: {params}")
      response = await client.get(f"{settings.competitions_matches_endpoint}?status=SCHEDULED", params=params)
      log.info(f"Response status code: {response.status_code}")
      break  # If successful, exit the loop

    except httpx.RequestError as e:
      log.error(f"An error occurred while requesting {e.request.url!r}: {str(e)}")
      if max_retries > 0:
        log.info(f"Retrying... ({max_retries} attempts left)")
        max_retries -= 1
        await asyncio.sleep(1)  # Wait for 1 second before retrying
    
    if response and response.status_code != 200:
      log.error(f"Error getting competition matches: {response.text}")
      raise Exception(f"Error getting competition matches: {response.text}")
  
  return response