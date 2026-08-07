# A-Game — Input Specification (v1)

> The design contract the prediction engine is built against.
> Everything here is powered by the **football-data.org** free tier **except odds** (external feed).

**Status:** Draft v1 · **Last updated:** 2026-07-07

---

## 1. What this document is

A-Game turns football match data into (a) predictions, (b) analytics, and (c) AI-written previews + value-bet suggestions. This spec defines **every input signal** that feeds the engine, where each comes from, whether it's free, and what it powers — so the data client, the engine, and the AI layer can be built against a fixed contract.

### Pipeline

```
INPUTS (raw signals)  ->  DERIVED FEATURES (engine state)  ->  OUTPUT CONTRACT (JSON)
   from the API              Elo / Poisson params               -> value calc -> AI layer
   + external odds feed
```

The engine core is **pure math** (Elo + Poisson) — no AI. AI lives only at the output edge (writing previews and explaining value bets) and optionally as a natural-language front door. Numbers are computed deterministically; AI never invents a stat.

---

## 2. Core inputs — match results (the foundation)

Source: **`/competitions/{id}/matches?season={YYYY}`**, looped over the training window and cached locally.

| Signal | Field(s) | Free? | Feeds |
|---|---|---|---|
| Match result (W/D/L) | `score.winner` | Yes | Elo |
| Full-time score | `score.fullTime.home/away` | Yes | Poisson (goals for/against) |
| Half-time score | `score.halfTime` | Yes | Comeback rate, 1st/2nd-half models |
| Home vs away identity | `homeTeam` / `awayTeam` | Yes | Home-advantage estimation |
| Match date | `utcDate` | Yes | Recency decay, rest-day calc |
| Competition & season | `competition.id`, `season` | Yes | Cross-league normalization |
| Match status | `status` | Yes | Filter to `FINISHED` for training |
| Stage / matchday | `stage`, `matchday` | Yes | Weighting (cup vs league), context |

---

## 3. Contextual inputs — form, fatigue, venue

Derived from the same match dataset (no extra endpoint), plus standings.

| Signal | How obtained | Free? | Feeds |
|---|---|---|---|
| Recent form (W/D/L, last N) | Slice each team's last N matches | Yes | AI context; sanity-check |
| Opponent-adjusted form | Weight recent results by opponent Elo | Yes | Poisson nudge (real signal) |
| Goal-form (scored/conceded, last N) | Sum recent goals | Yes | Poisson lambda momentum |
| Recency weight per match | `e^(-lambda * days_ago)` from `utcDate` | Yes | Time-decayed strength (Dixon-Coles) |
| Rest days / congestion | Gap between a team's consecutive `utcDate`s | Yes | Fatigue adjustment |
| Home/away table splits | `/competitions/{id}/standings` (TOTAL/HOME/AWAY) | Yes | Home-advantage prior, validation |
| Live league position | `/standings` row | Yes | AI context only |

**Form modeling note:** Elo and Poisson already contain form implicitly (Elo updates each match; Poisson uses time decay). Do **not** bolt on a naive "last 5" bonus — it double-counts. Make form matter *more* via recency weighting, and only add explicit form features when they are **opponent-adjusted** or **goal-based** (those carry real extra signal).

---

## 4. Event-level inputs — depth (selective, per match)

Source: **`/matches/{id}`** — one call per fixture. Used for upcoming games and deep-dives, not bulk training.

| Signal | Field(s) | Free? | Feeds |
|---|---|---|---|
| Goal timing | `goals[].minute` | Yes | Late-goal profiles, 15-min buckets |
| Goal type | `goals[].type` (regular/pen/own) | Yes | Penalty dependency, non-pen goals |
| Assists | `goals[].assist` | Yes | Creator stats |
| Cards | `bookings[].minute/card` | Yes | Discipline, red-card impact |
| Substitutions | `substitutions[]` | Yes | Sub-impact analysis |
| Lineups / captain / coach | `lineup[]`, `bench[]`, `coach` | Yes | Context, availability |
| Head-to-head record | `head2head` block | Yes | Preview context, prior |
| Shots / possession / corners | statistics node | **No (paid)** | Real xG inputs — out of scope on free |

---

## 5. Player & squad inputs

| Signal | Source | Free? | Feeds |
|---|---|---|---|
| Top scorers (goals/assists/pens) | `/competitions/{id}/scorers` | Yes | Player stats, penalty dependency |
| Squad + positions | `/competitions/{id}/teams` | Yes | Squad age/nationality context |
| Player DOB / nationality | squad member fields | Yes | Age profiles |
| Player match log | `/persons/{id}/matches` | Yes | Minutes, availability, importance weight |

---

## 6. External input — odds (the value layer)

Not from the free API. Required only for the "best bet" feature.

| Signal | Source | Free? | Feeds |
|---|---|---|---|
| Decimal odds per market | football-data paid tier **or** an odds API (e.g. The Odds API) | **No** | Implied prob -> edge -> EV -> value bet |

**Value = model probability vs bookmaker's implied probability.** The best bet is the *positive-edge* one, not the most likely outcome. `implied_prob = 1 / decimal_odds`; `edge = model_prob - implied_prob`; `EV = model_prob * (odds - 1) - (1 - model_prob)`. Stake sizing via (fractional) Kelly.

---

## 7. Derived features the engine produces

What actually enters the prediction math:

| Feature | Built from | Model |
|---|---|---|
| Elo rating (per team, live) | Match results, updated chronologically | Elo |
| Home-field advantage | Home/away result splits | Both |
| Attack strength (per team) | Time-decayed goals scored / league avg | Poisson lambda |
| Defence strength (per team) | Time-decayed goals conceded / league avg | Poisson lambda |
| Expected goals lambda (per fixture) | Attack x opp Defence x HFA x form nudge | Poisson |
| Fatigue modifier | Rest-day gap | lambda adjustment |

---

## 8. Output contract (engine emits per fixture)

The boundary object. Flows into the value calc and the AI layer. Locking this lets the engine and AI layer be built independently.

```json
{
  "fixture": { "home": "", "away": "", "date": "", "competition": "", "venue": "" },
  "model": {
    "elo": { "home": 0, "away": 0, "hfa": 0 },
    "expected_goals": { "home": 1.75, "away": 1.0 },
    "result_prob": { "home_win": 0, "draw": 0, "away_win": 0 },
    "most_likely_scores": [ { "score": "1-0", "p": 0.116 } ],
    "over_2_5": 0.53,
    "btts": 0.50
  },
  "context": {
    "home_form_last5": "", "away_form_last5": "",
    "home_form_weighted": 0, "away_goal_form": "",
    "rest_days_home": 0, "rest_days_away": 0,
    "h2h": { "matches": 0, "home_wins": 0, "draws": 0, "away_wins": 0 }
  },
  "betting": {
    "recommended": { "market": "", "selection": "", "odds": 0, "model_prob": 0, "edge": 0, "ev": 0, "stake": "" },
    "all_evaluated": []
  }
}
```

---

## 9. Out of scope (v1)

- **Real xG** — needs shot data (paid). A goals-based proxy is possible, not true xG.
- **Injuries / suspensions** — see v2 below. Not available in football-data.org.
- **Lineups before kickoff, weather, referee tendencies beyond cards** — not available.
- **Historical standings** — only current; reconstruct from matches if needed.
- **Live in-play modeling** — `status=LIVE` exists but real-time is a separate build.

### v2 signal — injuries (needs external source)

Injuries are **not in football-data.org** on any tier. To add them:
1. Source an injury/suspension list (API-Football / API-Sports, Sportmonks, or scraping).
2. Weight each absence by **player importance** — derivable from the free API (minutes via `/persons/{id}/matches`, goal contribution via `/scorers`). "3 players out" is noise; "top scorer + first-choice CB out" is signal.
3. Dock the team's attack/defence strength accordingly.

Deferred to v2: it is the hardest input to do well, data quality is patchy, and it forces a paid second source. Build and calibrate the core engine first.

---

## 10. Design decisions — locked in ADR 0010

**All six are decided. `docs/adr/0010-prediction-engine-parameters.md` is authoritative;**
this table is the summary. Section references below point into that ADR.

| # | Decision | Decided | ADR § |
|---|---|---|---|
| 1 | Training window (how many past seasons) | 3 seasons (~1,140 PL matches) — matches the ADR 0007 §4 backfill | §4 |
| 2 | Leagues at launch | Premier League only; widen to the 12 free comps once the chain is validated end to end | §1 |
| 3 | Decay half-life (how fast old matches lose weight) | ~6 months, on the Poisson goal data — **not** the Elo dial, which is K (§6) | §5, §7 |
| 4 | Cold-start (promoted teams / new signings) | League average minus a small penalty. No exception for the second-division champion — it washes out inside ~6 matches | §9 |
| 5 | Cross-league play (Champions League shared Elo scale) | Out of scope. Two reasons, not one: league Elo scales aren't comparable, **and** a single team rating doesn't hold across competitions (rotation, priority, setup) | §3 |
| 6 | Min sample before trusting a team's strength | 5–6 matches; lean on the prior and blend below that | §10 |

Four decisions this list missed, also settled in ADR 0010 — the engine could not be written
without them:

| Decision | Decided | ADR § |
|---|---|---|
| Elo K-factor | 20, scaled by goal difference, damped at the top end | §6 |
| Home advantage | Fixed +70 Elo to the home side; fitted from data later | §8 |
| Elo ↔ Poisson relationship | Elo does **not** feed Poisson. Two parallel models: Elo is rating state, Poisson produces the probabilities | §11–12, §14 |
| Draw probabilities | From the Poisson score matrix, so no three-outcome Elo variant is needed | §12 |

How a change to any of these is judged — Brier/log loss, the three baselines a version must
beat, and the walk-forward backtest — is ADR 0010 §15–18.

---

## Free-tier bottom line

The entire prediction + form + context engine runs on the **free tier**. The only paid dependencies are **odds** (for value bets) and **shot-level stats** (for true xG, out of scope in v1).
