"""Prediction engine parameters (ADR 0010).

Every value in this module is part of ENGINE_VERSION. Changing any of them MUST
bump it: calibration is only comparable within a version, never across one
(ADR 0005 §2, ADR 0010 §15). Keeping them in one file is what makes that rule
checkable rather than remembered.

Poisson's constants belong here too when that side is written.
"""

ENGINE_VERSION = "0.1.0"


# --- Elo (ADR 0010 §6, §8, §9) ---

# Where every team starts before it has played a rated match.
DEFAULT_RATING = 1500.0

# The standard football K. Scaled by goal difference and damped at the top end
# (§6): high enough to separate from the 1500 start, low enough not to overreact
# to one fluke result.
K_FACTOR = 20.0

# Fixed points added to the home side when computing the expected result (§8).
# Fitting it from data is a later engine version.
HOME_ADVANTAGE = 70.0

# Promoted teams seed at their competition's average minus this (§9). No special
# case for the second-division champion — any seeding error washes out inside
# roughly six matches.
PROMOTED_PENALTY = 50.0


# --- Poisson (ADR 0010 §5, §10, §12) ---

# Recency decay on the goal data feeding the attack/defence strengths (§5). A
# match this many days old counts half as much as one played today. This is the
# Poisson recency dial and has nothing to do with K_FACTOR above (§7) — tuning
# them as if they were one knob is the mistake that rule exists to prevent.
HALF_LIFE_DAYS = 182.5

# Effective matches at which a team's own goal record outweighs the league prior
# (§10). Below it the strength is pulled back toward 1.0, so a side that scores
# six in its first two games is not modelled as scoring three a game.
MIN_SAMPLE = 6.0

# Goals per side the score matrix runs to. At any realistic lambda the tail
# beyond 10 is worth well under 0.01% — far below the 4dp the probabilities are
# stored at — while the matrix cost grows as the square.
MAX_GOALS = 10
