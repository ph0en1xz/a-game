# ADR 0010 — Prediction engine: scope, parameters, and model shape

- **Status:** Accepted (§11–14 confirmed 2026-08-07; amended 2026-08-07 — see Amendments)
- **Date:** 2026-08-07
- **Related:** ADR 0005 (Postgres as the prediction record, rows keyed by fixture + model
  version), ADR 0007 (daily change-gated cadence), ADR 0008 (AI layer phrases the numbers,
  never invents them)
- **Deciders:** Mario (Nexoro Tech)

## Context

`docs/input-spec.md` §10 listed six parameters to pin before writing the engine. None were
locked, and no engine code exists yet — `a-game-brain/app/` currently holds the consumer,
the LLM commentary path, and nothing that computes a rating.

Settling the six surfaced four more decisions §10 did not cover: the Elo K-factor, home
advantage, how Elo and Poisson relate to each other, and where draw probabilities come from.
The engine cannot be written without them, so they are decided here too.

Everything in this ADR is a versioned quantity. ADR 0005 §2 makes stored prediction history
the basis of the calibration claim, which only holds if a change to any parameter below
bumps `engine_version` and calibration is compared within a version rather than across one.

## Decision

### Scope

1. **Premier League only at launch.** Widen to the other free competitions once the full
   chain — ingest, compute, store, serve — is validated end to end.

2. **Ratings are keyed by team *and* competition from the start**, even with one competition
   in the table. Elo points are league-relative and do not transfer between competitions.
   Keying for it now costs nothing; retrofitting it once there are live ratings is a schema
   migration with data in it.

3. **Cross-league fixtures (Champions League) are out of scope.** Two independent reasons.
   Elo scales built in separate leagues are not comparable — no points ever crossed between
   them, so 1600 in the PL and 1600 in La Liga are not the same strength. And a single team
   rating does not hold across competitions: rotation, priority, and a different tactical
   setup mean a side can be genuinely different in Europe. Fixing the second would need a
   competition-specific rating, and a team plays too few CL matches a season to fit one.

### Data

4. **Training window: 3 seasons** (~1,140 PL matches). One season was considered and
   rejected: it gives the Poisson side only 19 home and 19 away matches per team, and it
   would have contradicted ADR 0007 §4, which already commits to backfilling three seasons —
   the engine would have discarded two thirds of data the pipeline was fetching anyway. The
   6-month half-life (§5) is what stops the older seasons dominating, so the extra history
   costs accuracy nothing while giving the goal-rate estimates room to stabilise.

5. **Recency decay: ~6-month half-life** applied to historical goal data feeding the Poisson
   strengths. This is *not* the Elo recency dial — see §7.

### Elo

6. **K-factor = 20, scaled by goal difference**, damped at the top end. Plain Elo treats 5-0
   and 1-0 identically, which discards real information; undamped goal difference inflates
   strong teams, who win big more often. 20 is the standard football value and sits between
   a rating that never separates from its 1500 start and one that overreacts to a single
   fluke result.

7. **K controls recency on the Elo side; the 6-month half-life (§5) controls it on the
   Poisson side.** They are separate dials on separate models and should not be tuned as if
   they were one.

8. **Home advantage: a fixed +70 Elo points** to the home side when computing the expected
   result. Fitting it from data is a later engine version.

9. **Cold start: promoted teams seed at league average minus a small penalty.** No exception
   for the second-division champion — Championship winners do not reliably outperform the
   other promoted sides, and any seeding error washes out within roughly six matches under
   §10, so the extra branch buys nothing.

10. **Min sample: 5–6 matches.** Below that, lean on the prior — the team's carried-over
    rating, or the league average for a promoted side — and blend current-season data in as
    it accumulates. Without this, a team scoring six in its first two games is modelled as
    scoring three a game.

### Poisson and probabilities

11. **Elo does not feed Poisson.** Two models run in parallel. Elo maintains persistent
    team-strength state across seasons and handles promotion and cold-start. Poisson turns
    decayed goal data into per-team attack and defence strengths and produces the goal
    distribution.

12. **All reported probabilities come from the Poisson score matrix** — `result_prob`
    (home/draw/away), `over_2_5`, `btts`, `most_likely_scores`. Draws need no separate Elo
    treatment as a result; they fall out of the matrix naturally, which is the reason for
    this split.

13. **Known limitation:** an independent Poisson model underestimates draws and low-scoring
    results. The Dixon-Coles low-score correction is the standard fix. Deferred — stored
    calibration data will show whether it is needed before adding it.

14. **`elo` is reported in the API payload** (`docs/api-spec.md` §5) as context and as a
    cross-check on the Poisson output, not as the probability source.

### Evaluation

15. **Success metric: Brier score on the 1X2 probabilities**, reported alongside log loss.
    Brier is the primary number because it is bounded, decomposes into calibration and
    resolution, and stays interpretable when a prediction is confidently wrong; log loss is
    reported because it punishes overconfidence harder and is the more sensitive tuning
    signal. Both are computed per `engine_version` — never pooled across versions, which is
    the whole reason ADR 0005 §2 stamps the version onto every row.

16. **Baselines an engine version must beat to ship.** A version that does not clear all
    three is a regression regardless of how it scores in isolation: the bookmaker-free
    naive prior (home 0.45 / draw 0.27 / away 0.28, the long-run PL split), a
    home-team-always model, and the previous `engine_version`. The first two make a bad
    engine obvious; the third is what makes tuning K (§6) and HFA (§8) an evidenced change
    rather than a preference.

17. **Backtest protocol: walk-forward, never random split.** Train on all matches strictly
    before a given matchday, predict that matchday, advance, repeat across a held-out
    season. A random train/test split leaks the future into the past — a model that has
    already seen May's results will look excellent predicting September's, and the number
    it produces is meaningless. Scoring runs on a season *outside* the §4 training window.

18. **The backtest is the eval harness's second gate.** ADR 0008 makes the CI eval harness
    the AI layer's flagship deliverable, currently specified only against the LLM's prose
    (fact-checking, LLM-as-judge). §15–17 give it a numeric gate for the engine as well:
    a merge that moves Brier the wrong way against the §16 baselines fails, the same way a
    preview that invents a statistic fails.

### Deferred

19. **Fixture congestion.** A midweek European match plausibly depresses the next domestic
    result, and `rest_days_home` / `rest_days_away` already exist in the API payload. It
    requires ingesting CL fixtures for scheduling purposes even though they never feed
    ratings. The measured effect is small — a couple of percentage points. Revisit when
    leagues widen.

20. **Injuries and player-level ratings.** Squad selection is the honest next frontier, but
    it needs per-match lineups, which football-data's free tier does not provide. Same wall
    as odds and shot-level xG.

21. **Rating persistence.** Whether ratings live in a `team_rating` table or are recomputed
    each cycle. ~1,140 matches recomputes in negligible time, so the real question is
    whether rating *history* is wanted for previews and debugging.

## Consequences

**Positive.** The engine is now writable — every parameter it needs has a value. Keying
ratings by competition (§2) makes the league widening a data change rather than a migration.
Sourcing probabilities from one model (§12) removes the reconciliation problem of having two
models disagree about who wins. And §15–18 give `engine_version` something to mean: a version
is better or worse by a number against fixed baselines, not by argument.

**Negative.** The fixed home advantage (§8) and K-factor (§6) are borrowed constants rather
than fitted ones. Defensible as starting points, and §15–17 are exactly what will replace
them with evidenced values — but until a backtest has run, the engine's headline parameters
rest on convention. The walk-forward protocol (§17) is also meaningfully more work to build
than a random split, and it needs a season held outside the §4 window, so the backfill has to
reach further back than the training window itself.

**Neutral.** Elo's role is narrower than the original design implied: rating state and
cold-start handling rather than the probability engine. It stays in the payload and stays
worth maintaining, but §12 means the 1X2 numbers do not depend on it.

## Amendments

### 2026-08-07 — Only three seasons are reachable, so §17's held-out season is redefined

Running the backfill established the real ceiling. On a free key, `/competitions/PL/matches`
returns 380 matches each for `season=2025`, `2024` and `2023`; `season=2022` returns

```
403 {"message":"The resource you are looking for is restricted and apparently not within
your permissions. Please check your subscription.","errorCode":403}
```

403 rather than 404 — the data exists and the plan will not serve it. §4 above and ADR 0007
§4 both assumed three seasons were obtainable *and* that a fourth could be held out beyond
them. Only the first is true.

**The boundary is undocumented.** football-data's pricing, coverage and v4 documentation
pages state competitions and rate limits but say nothing about historical depth, and the
only third-party claim found (a competing vendor's comparison post) says the free tier is
current-season-only — contradicted by the 1,140 matches actually retrieved. The measurement
is therefore the authority here, and whether the limit is a rolling three-season window or a
fixed cutoff date could not be determined. Both were consistent with the single observation.

**§4 stands.** The training window remains three seasons. That is what the live engine reads
when it rates a team and estimates a goal rate, and three is now also the maximum
obtainable, so there is no trade-off left to make.

**§17 changes.** A season outside the training window cannot be bought, so the protection it
was buying is bought differently — by quarantining a season from *tuning* rather than from
*training*:

- **Development scoring** — walk-forward over 2024/25, with 2023/24 as the Elo warm-up.
  This is the number consulted while tuning K (§6), HFA (§8), the half-life (§5), or
  anything else.
- **Acceptance scoring** — walk-forward over 2025/26, run once per candidate
  `engine_version` at merge time and never during tuning. §16's three baselines are judged
  on this number.

The walk-forward protocol itself is unchanged and still mandatory: at any matchday, state is
built strictly from matches before it, so neither set leaks. The reason a quarantined season
still matters is §16's third baseline — "beat the previous `engine_version`". Iterating
parameters against a score is fitting, even done by hand, and a score you have tuned against
stops being an estimate of future performance. Acceptance scoring keeps one honest number in
reserve.

**Data capture is now load-bearing.** If the limit turns out to be a rolling window, 2023/24
stops being fetchable during 2027 and the backfilled rows become unreproducible; if it is a
fixed cutoff, the corpus simply widens by a season each year and §17's original form becomes
affordable again. The conservative assumption costs almost nothing, so take it: what is in
Postgres is the only copy. That makes the Postgres volume a backup target rather than a
rebuildable cache — recorded here as a consequence, not as a solved problem. See Follow-ups.

**Rejected for now:** paying for a wider history, or adding a second source such as
football-data.co.uk's season CSVs. Both remain real options, and the second is free.
Deferred rather than dismissed — the engine does not exist yet, so there is no evidence the
two-season development set is too thin.

**Known weakness.** It is thin. One season of development scoring with one season of Elo
warm-up is a noisy signal, and if ratings need longer than a season to stabilise, tuning
against that number is partly tuning against noise. This is tolerable only because nothing
in the engine is fitted — every parameter in §5–§10 comes from convention and would only be
nudged — but it is the weakest part of this amendment and should be revisited as soon as a
fourth season exists.

**Follow-ups.**
- Back up the Postgres volume (or export the `match` blobs) before the corpus can be lost.
  No mechanism currently exists.
- Re-test `season=2022` after 2027-08 to settle rolling vs fixed. A 200 means fixed and the
  window is widening; a continued 403 means rolling and the backup is load-bearing.
