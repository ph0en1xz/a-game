import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Payload(BaseModel):
    """Base for football-data payload objects.

    extra="allow" keeps fields we don't model. The match blob column stores this
    back as JSONB, so dropping unknown keys would make the stored payload a lossy
    projection of the response instead of a copy of it.
    """

    model_config = ConfigDict(extra="allow")


class Area(_Payload):
    id: int
    name: str
    code: str | None = None
    flag: str | None = None


class Competition(_Payload):
    id: int
    name: str | None = None
    code: str | None = None
    type: str | None = None
    emblem: str | None = None


class Season(_Payload):
    id: int
    startDate: datetime.date
    endDate: datetime.date
    currentMatchday: int | None = None


class Team(_Payload):
    id: int
    name: str
    # the payload calls the crest URL `crest`; the team table column is `emblem`.
    shortName: str | None = None
    tla: str | None = None
    crest: str | None = None


class Referee(_Payload):
    id: int
    name: str
    type: str | None = None
    nationality: str | None = None


class ScoreLine(_Payload):
    """One half's goals. Both sides are null until the match is played."""

    home: int | None = None
    away: int | None = None


class Score(_Payload):
    fullTime: ScoreLine
    halfTime: ScoreLine
    winner: str | None = None
    duration: str | None = None


class Match(_Payload):
    """A fixture as returned by /competitions/{code}/matches.

    Required fields are the ones the match upsert depends on. Everything the
    ingest never reads is optional, so a field missing from a response can't fail
    a run over data nobody uses.
    """

    id: int
    utcDate: datetime.datetime
    status: str
    competition: Competition
    season: Season
    homeTeam: Team
    awayTeam: Team
    score: Score

    matchday: int | None = None
    stage: str | None = None
    group: str | None = None
    area: Area | None = None
    # empty for SCHEDULED fixtures — a referee is appointed closer to kick-off.
    referees: list[Referee] = Field(default_factory=list)
