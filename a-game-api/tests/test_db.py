"""The one query the API runs (`app/db.py`), driven through a fake connection.

No Postgres here. What's pinned down is the column the row is read by and the
missing-row case — the two things that silently return the wrong thing if the
schema moves under this function.
"""

from app import db
from tests.conftest import FakeConnection


async def test_returns_the_prediction_column():
    """`commentary.prediction` holds the prose. The pydantic field on the brain
    side is called `text`, and reading by that name is a runtime error, not a
    type error — nothing else catches it."""
    conn = FakeConnection(fetchrow_results=[{"prediction": "Preview prose."}])

    assert await db.get_prediction(conn, 538107) == "Preview prose."


async def test_returns_none_when_no_row_exists():
    conn = FakeConnection(fetchrow_results=[None])

    assert await db.get_prediction(conn, 538107) is None


async def test_queries_the_commentary_table_by_match_id():
    conn = FakeConnection(fetchrow_results=[{"prediction": "x"}])

    await db.get_prediction(conn, 538107)

    query, args = conn.fetchrow_calls[0]

    assert "a_game.commentary" in query
    assert args == (538107,)


async def test_match_id_is_a_bound_parameter():
    """Placeholder, never interpolation. A match id arrives from the URL."""
    conn = FakeConnection(fetchrow_results=[None])

    await db.get_prediction(conn, 538107)

    query, _ = conn.fetchrow_calls[0]

    assert "$1" in query
    assert "538107" not in query
