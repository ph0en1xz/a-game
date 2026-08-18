"""Does the model obey `SYSTEM_PROMPT`?

Every rule checked here is written down in the prompt and asserted nowhere else.
`tests/test_commentary.py` proves the rules are *in* the prompt - see
`test_the_system_prompt_forbids_value_claims`, which asserts on the prompt string
itself. This proves the model actually follows them.
"""

import itertools
import re

import pytest

from app.commentary import SUGGESTED_BETS, Commentary, _user_prompt
from evals.conftest import CASES, IDS, Case

# "71 percent" has to count as a number too. The first real recording spelled
# three of them out in words and slid straight past a %-only pattern.
# The \b matters: "17 percentage points" is a difference the model worked out
# from two numbers it was given, not a probability it quoted.
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent\b)")
DECIMAL = re.compile(r"\d+\.\d+")

# Market names are capitalised mid-sentence by rights - "Under 2.5 goals is the
# model's standout call" is correct prose, not an invented entity.
# Lowercased: the model title-cases market names mid-sentence ("Both Teams to
# Score") and that is ordinary prose, not an invented entity.
MARKET_WORDS = {word.lower() for bet in SUGGESTED_BETS for word in bet.split()}

# A decimal point is not a sentence boundary - "over 2.5 goals" must not count
# as two. Requiring whitespace after the stop is what keeps it from doing so.
SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")

CERTAINTY = [
    "will win",
    "will beat",
    "guaranteed",
    "certain to",
    "sure to",
    "must win",
    "cannot lose",
]

# Deliberately strict, and the first list likely to need loosening: "value" is a
# perfectly ordinary word that will eventually appear in prose that is fine.
# Loosen a term here when it fires on good output, not before.
VALUE_CLAIMS = [
    "value",
    "profitable",
    "odds",
    "bookmaker",
    "bookie",
    "good price",
    "worth backing",
    "worth a bet",
]

MARKDOWN = ["**", "##", "`", "\n- ", "\n* ", "\n1. "]


def _scoreline_totals(probabilities) -> set[str]:
    """Sums of the most-likely scorelines, which the model legitimately adds up.

    "1-0, 1-1 and 0-0 together account for 37%" is arithmetic on three adjacent
    numbers it was given, not a figure it invented, and no wording of the prompt
    has stopped it doing this. Deliberately narrow: only these three values and
    only their own sums, so an invented percentage still has nothing to match.
    """
    shown = [round(float(s["prob"]) * 100) for s in probabilities.most_likely_scores]
    return {
        f"{sum(combo)}%"
        for size in (2, 3)
        for combo in itertools.combinations(shown, size)
    }


def _numbers(text: str) -> set[str]:
    percentages = {
        re.sub(r"\s*(?:%|percent)$", "%", match) for match in PERCENT.findall(text)
    }
    return percentages | set(DECIMAL.findall(text))


@pytest.fixture(params=CASES, ids=IDS)
def case(request: pytest.FixtureRequest) -> Case:
    return request.param


@pytest.fixture
def preview(case: Case, previews: dict[str, Commentary | None]) -> Commentary:
    generated = previews[case.name]
    assert generated is not None, f"write_preview returned None for {case.name}"
    return generated


def _body(preview: Commentary) -> str:
    return f"{preview.text} {preview.suggested_bet_reason}"


def test_both_teams_are_named(case: Case, preview: Commentary) -> None:
    for team in (case.match.home_team, case.match.away_team):
        short = team.removesuffix(" FC")
        assert short in preview.text or short.split()[0] in preview.text, (
            f"{team} is never mentioned: {preview.text!r}"
        )


def test_no_invented_numbers(case: Case, preview: Commentary) -> None:
    """The rule the whole design rests on: the LLM phrases numbers, never makes
    them up (ADR 0008). Only the user message counts - the worked example in
    SYSTEM_PROMPT is an illustration, so echoing its 39% here is a failure."""
    allowed = _numbers(_user_prompt(case.match, case.probabilities))
    allowed |= _scoreline_totals(case.probabilities)
    used = _numbers(_body(preview))
    assert used <= allowed, (
        f"numbers absent from the prompt: {sorted(used - allowed)} "
        f"in {preview.text!r}"
    )


def test_no_certainty(preview: Commentary) -> None:
    hits = [phrase for phrase in CERTAINTY if phrase in _body(preview).lower()]
    assert not hits, f"certainty language {hits} in {preview.text!r}"


def test_no_value_claims(preview: Commentary) -> None:
    hits = [phrase for phrase in VALUE_CLAIMS if phrase in _body(preview).lower()]
    assert not hits, f"value claim {hits} in {preview.text!r}"


def test_no_markdown(preview: Commentary) -> None:
    hits = [token for token in MARKDOWN if token in preview.text]
    assert not hits, f"markdown {hits} in {preview.text!r}"


def test_two_to_four_sentences(preview: Commentary) -> None:
    count = len([s for s in SENTENCE_END.split(preview.text) if s.strip()])
    assert 2 <= count <= 4, f"{count} sentences in {preview.text!r}"


def _proper_nouns(text: str) -> set[str]:
    """Capitalised words that are not opening a sentence.

    The first word of a sentence is capitalised by grammar rather than by being
    a name, so counting it would flag every "The" and "Both".
    """
    found = set()
    for sentence in SENTENCE_END.split(text):
        for word in sentence.split()[1:]:
            token = word.strip(".,;:()'\"").removesuffix("'s")
            if re.fullmatch(r"[A-Z][a-zA-Z]+", token):
                found.add(token)
    return found


def test_no_outside_entities(case: Case, preview: Commentary) -> None:
    """No name the prompt did not supply.

    SYSTEM_PROMPT rules out the stadium, the city, rivalries, history, form and
    league position. A proper noun the user message never mentioned is the model
    reaching for what it knows about these clubs from somewhere else.
    """
    allowed = set(
        re.findall(r"\b[A-Z][a-zA-Z]+\b", _user_prompt(case.match, case.probabilities))
    )
    used = {
        word
        for word in _proper_nouns(_body(preview))
        if word.lower() not in MARKET_WORDS
    }
    assert used <= allowed, (
        f"names absent from the prompt: {sorted(used - allowed)} in {preview.text!r}"
    )
