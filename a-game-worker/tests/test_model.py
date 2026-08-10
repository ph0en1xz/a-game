"""Payload parsing (`app/model.py`).

These are the worker's only external boundary. football-data.org can add fields,
omit optional ones, and returns nulls for anything that depends on a match having
been played — the model has to absorb all three without a run dying.
"""

import datetime

import pytest
from pydantic import ValidationError

from app.model import Match
from tests.conftest import match_payload


def test_parses_a_finished_match():
    match = Match.model_validate(match_payload())

    assert match.id == 538107
    assert match.status == "FINISHED"
    assert match.competition.id == 2021
    assert match.season.id == 2502
    assert match.homeTeam.name == "Manchester City FC"
    assert match.score.fullTime.home == 2
    assert match.score.fullTime.away == 1
    assert match.score.halfTime.home == 1


def test_utc_date_becomes_an_aware_datetime():
    """The Z suffix must survive as UTC, not become a naive datetime.

    `utc_date` is a TIMESTAMPTZ and feeds Elo's recency decay. A naive value here
    would be stored as whatever the session timezone happens to be.
    """
    match = Match.model_validate(match_payload())

    assert match.utcDate.tzinfo is not None
    assert match.utcDate.utcoffset() == datetime.timedelta(0)


def test_unknown_fields_are_kept():
    """extra="allow" is what makes the blob column a copy of the response.

    `lastUpdated` and `odds` are in the payload and not on the model. If they were
    dropped, the JSONB blob would silently stop being the insurance policy it
    exists to be (schema.md §2, `match.blob`).
    """
    match = Match.model_validate(match_payload())
    dumped = match.model_dump()

    assert dumped["lastUpdated"] == "2026-08-22T16:05:11Z"
    assert "odds" in dumped


def test_scheduled_match_has_null_scores_and_no_referee():
    """A fixture that hasn't kicked off yet is the common case for the daily run."""
    match = Match.model_validate(
        match_payload(status="SCHEDULED", home_goals=None, away_goals=None, referees=False)
    )

    assert match.score.fullTime.home is None
    assert match.score.fullTime.away is None
    assert match.score.winner is None
    assert match.referees == []


def test_referees_defaults_to_empty_when_absent():
    """Not merely null — absent. A SCHEDULED payload omits the key entirely."""
    payload = match_payload()
    del payload["referees"]

    assert Match.model_validate(payload).referees == []


def test_optional_fields_may_be_missing():
    """matchday is null for some cup stages; area/stage/group aren't always sent."""
    payload = match_payload()
    for key in ("matchday", "stage", "group", "area"):
        payload.pop(key, None)

    match = Match.model_validate(payload)

    assert match.matchday is None
    assert match.area is None


@pytest.mark.parametrize(
    "missing", ["id", "utcDate", "status", "competition", "season", "homeTeam", "score"]
)
def test_missing_required_field_is_rejected(missing):
    """Required = the upsert depends on it. Failing loudly beats a NOT NULL violation
    surfacing three call frames later inside a transaction."""
    payload = match_payload()
    del payload[missing]

    with pytest.raises(ValidationError):
        Match.model_validate(payload)


def test_team_crest_maps_to_the_model_field_named_crest():
    """The payload says `crest`, the column says `emblem`. The rename happens in
    db.py, not here — if the model ever gained an `emblem` alias, the insert would
    start writing None."""
    match = Match.model_validate(match_payload())

    assert match.homeTeam.crest == "https://crests.football-data.org/65.png"
    assert not hasattr(match.homeTeam, "emblem")
