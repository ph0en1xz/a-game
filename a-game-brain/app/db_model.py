from datetime import datetime

from pydantic import BaseModel


class Match(BaseModel):
    """A match record as returned by fetch_match_via_id()."""

    id: int
    season_id: int
    # Joined in from season. The engine keys ratings and strengths by
    # competition (ADR 0010 §2), so the fixture has to carry it too.
    competition_id: int
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    matchday: int | None
    utc_date: datetime
    status: str
    home_goals: int | None
    away_goals: int | None
    fulltime_outcome: str | None