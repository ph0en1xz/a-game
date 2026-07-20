import datetime

from pydantic import BaseModel

class Referee(BaseModel):
    id: int
    name: str
    type: str
    nationality: str

class Score(BaseModel):
    winner: str
    duration: str
    fullTime: list[dict]
    halfTime: list[dict]

class Match(BaseModel):
    area: list[dict]
    competition: list[dict]
    season: list[dict]
    id: int
    utcDate: datetime.datetime
    status: str
    matchday: int
    stage: str
    group: str | None
    homeTeam: list[dict]
    awayTeam: list[dict]
    score: Score
    referees: list[Referee]
