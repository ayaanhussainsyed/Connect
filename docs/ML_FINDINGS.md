# ML findings: the score engine

Plain-language summary of every finding and number behind `cs/engine/`, for the README and the pitch.
The evidence (code, tables, plots) is in `notebooks/01_data_and_models.ipynb`.
All numbers come from Fonecta's own scorer (`evaluation/evaluate.py`) or the same rules re-implemented and unit-tested
against it (`tests/engine/`).

## Headline

| | Score | Harmful | Exact precision | Recall | Search views protected |
|---|---|---|---|---|---|
| **Engine, dev 2026** (business-grouped CV) | **61.67** (95% CI at test size 60.4–62.9) | **219** | 0.859 | 0.67 | 65.0% |
| Fonecta baseline, dev 2026 | 54.49 | 224 | 0.901 | 0.590 | 57.2% |
| **Engine, 2025 back-test** (only data before 6 Nov 2025) | **55.42** | **211** | 0.866 | 0.597 | 56.3% |
| Fonecta baseline, 2025 back-test | 53.23 | 249 | 0.899 | 0.578 | 55.1% |

- **+7.2 points over the baseline on dev 2026** (paired bootstrap at test size, 95% CI 6.3–8.1), with
  harmful suggestions at or below the baseline's 224. Harmful at test size, scaled to 11,100 rows, has a 95% interval
  of 200–239: around the baseline's level, not a guarantee.
- **The dev number is honest.** Every dev business is predicted by models that never saw it (5 business-grouped folds);
  the calibration and the safety margin λ for each fold are chosen on the other folds; no 2026 label is used for
  training. The dev rows of `outputs/engine_predictions_2026.csv` hold exactly these cross-validated decisions, so
  `evaluate.py` on that file reproduces the number. Test and demo rows come from the final model (trained on all dev
  businesses, including their 2026 season).
- **The back-test is honest.** Only data stamped before 6 Nov 2025: no pooled peer file, no entries or pilot decisions
  from after the snapshot, regular hours as of the snapshot, models trained only on periods ending before it.
  The back-test gains less (+2.2) because in 2025 there was only one earlier winter of entries (they start in
  Dec 2023), and "two seasons back" is one of the strongest signals (see ablations).

## What the data says

| # | Finding | Number | What we did with it |
|---|---|---|---|
| 1 | What Google shows now | Our display logic (via the builder's `cs/display.py`) matches Fonecta's shown hours on **100%** of dev rows in both seasons | Every "needs a change?" decision starts from the exact shown hours |
| 2 | Where the needed changes are | 5,376 of 11,100 dev rows (+22 closed-for-good rows). **63% "shown open, actually closed"**, 36% different hours, 1% shown closed but open | Closed doors are the main prize and the main risk |
| 3 | Leakage | **97% of the 2025-season entries were entered after 6 Nov 2025**; `peer_benchmarks.csv` pools 2023–2025; `entities.json` is the 2026 state | Every feature goes through `make_context(as_of)`; peers recomputed by us; a test proves features don't change when post-snapshot data is deleted |
| 4 | Owner entries are right but biased | Entries equal the truth **99.1%** of the time, but exist for 39% of special days, 29% of closed days and **2%** of regular days (missing not at random) | Entries are features and candidate hours; the state model and habit types are trained on the unbiased dev truth |
| 5 | Holidays are linked | P(closed New Year's Day \| closed Christmas Day) **0.89 vs 0.36**; Boxing Day 0.67 vs 0.20; even Good Friday predicts Christmas (0.89 vs 0.48) | M1 habit types; linked-holiday features and candidates |
| 6 | Habits repeat, and facts age | Same exact hours **92%** one season later, **87%** two, **81%** three | Decay weight 0.95 per extra season (0.87/0.92) for learned facts; a newer answer supersedes; facts older than 3 seasons drop out unless reconfirmed |
| 7 | "Copy last year" sees under half | Of the needed changes, **44%** have last season's entry, **16%** only one from two seasons back, **38%** only other holidays, 1.4% nothing | Two seasons back + linked holidays + M1 |
| 8 | Pilot rejections are informative | When an owner rejected a 2025 suggestion, the 2026 truth equals the rejected hours only **7%** of the time | B5 decline rule (below) |

## The models

| Model | What it is | Result |
|---|---|---|
| **M1** habit types | Latent-class mixture, EM, fitted on the dev truth; K chosen by held-out log-likelihood on business-grouped folds | **K = 10** (likelihood flattens after ~6). Types read as: closed on every church holiday, eves included (25%) or softer (13%); closed on Christmas and New Year's Day with special eves (20%); special hours on almost every holiday (11%); regular between holidays, split on the big days (14%); always regular (5.5%); seasonal, closed all winter (1%). One answer updates every other day in closed form (but rarely changes a decision, see B1) |
| **M2** state model | `HistGradientBoostingClassifier` (closed / regular / special), trained on dev truth | AUC for "closed" **0.970** (M1 alone 0.893, peers alone 0.748); over-confident between 0.6 and 0.8 (predicted 0.75, observed 0.66), corrected by M3's isotonic step |
| **M3** hours ranker | Gradient-boosted scorer over candidate hours (own past, two seasons back, linked holidays, shopping centre, peers, user edits, event-extended, shifted regular hours) + isotonic calibration | Candidates contain the true hours for **95% of needed changes**; on special days the top candidate is exact on **82%** of covered rows; calibration error 0.006 → 0.001 |
| **M4** decision rule | Expected points under the scorer's exact table (FLAG included) with two flip margins: λ_own when the owner's own past backs the flip, λ_inferred otherwise; λ chosen on other folds for harmful ≤ 0.95 × 224 | Final λ = (0.3, 0.5) (median of the folds). Own-history flips are right ~95% of the time and need little margin; inferred flips need more |
| **M5** closed for good | Rules: closure-language reviews (fi/sv/nb/en) in 150 days, `closed` user edits, analytics collapse | Of 7 dev closures, 5 already show closed (nothing to do); of the 2 still open, flags **1** (NO-00086) with **no false flags**; NO-00373 left no signal. Analytics collapse alone: 8 dev businesses, none closed (seasonal dips) |

**Hard rules in code** (unit-tested): never contradict the owner's own entry for that date; never message a listing
that already shows closed; never re-suggest hours the owner rejected; every output hours string passes the scorer's
format check.

## Ablations (dev 2026, the whole CV re-run without one signal group)

| Variant | Score (λ by CV) | Harmful | Score at fixed λ (0.2, 0.6) | Harmful at fixed λ | M2 log-loss |
|---|---|---|---|---|---|
| Full engine | 61.67 | 219 | 61.17 | 200 | 0.397 |
| Train on winter seasons only (no spring/autumn periods) | 60.91 | 218 | 60.83 | 211 | 0.420 |
| Without any own history (entries, two seasons back, M1, linked) | 33.43 | 198 | 34.26 | 204 | 0.530 |
| Without two-seasons-back | 55.75 | 194 | 55.64 | 192 | 0.429 |
| Without M1 habit types | 61.90 | 217 | 61.49 | 210 | 0.394 |
| Without linked holidays | 62.05 | 217 | 61.42 | 203 | 0.396 |
| Without M1 and linked holidays | 62.00 | 222 | 61.78 | 210 | 0.394 |
| Without anything from other holidays (M1, linked, entry-mix summaries) | 60.37 | 206 | 60.82 | 221 | 0.411 |
| Without the shopping centre | 61.52 | 219 | 60.82 | 206 | 0.405 |
| Without peers | 61.90 | 216 | 61.56 | 208 | 0.401 |
| Without user edits and reviews | 62.09 | 214 | 61.55 | 196 | 0.398 |
| Without events and school breaks | 62.05 | 215 | 61.55 | 203 | 0.399 |
| Without the pilot's accepted/edited entries | 61.17 | 216 | 61.45 | 216 | 0.397 |
| No-history test: every dev business's own entries hidden | 34.06 | 208 | 33.61 | 194 | 0.555 |
| Fonecta baseline | 54.49 | 224 | | | |
| Fonecta baseline, dev history hidden | 33.24 | 212 | | | |

**The score comes from the business's own past.** Without any own history the engine falls to 33.4 (−28 points); without the entry from **two seasons back** alone it loses 5.9 points (55.75), the single most valuable addition over "copy last year". Training on the extra spring and autumn periods adds 0.8 points and improves the state model's log-loss (0.420 → 0.397); removing the shopping centre costs 0.15 points (0.35 at fixed λ) and worsens the log-loss to 0.405. **Honest negatives:** M1 habit types, linked holidays (removing both changes the score by +0.33), peers, reviews/user edits and events add **no measurable points** on top of the rest; their differences are within the ±0.5-point noise of choosing λ. Removing *everything* from other holidays (M1, linked states and the simple entry-mix summaries) costs 1.30 points and adds 21 harmful suggestions at fixed λ (M2 log-loss 0.411): the information matters, M1 is just not the only way to carry it. The gradient-boosted state model already gets the cross-holiday signal from simple features (which share of a business's entries are closed or special). M1 stays as the readable "habit type" in the evidence and as B1's model of how an owner would answer, but its updates barely move the other days' decisions (13.5).

## No history (A12)

With every dev business's own entries hidden, the engine scores 34.06 (harmful 208) and the baseline, given the same blindness, 33.24 (harmful 212). Without a business's own past, peers, the shopping centre and the segment prior recover only about a third of the points, for any method. For the 16 dev businesses that genuinely have no entries (219 rows), the engine scores 36.8 against the baseline's 14.2 (harmful 2 vs 4), mostly by staying quiet where the baseline guesses. **Design consequence:** for a new customer like FI-D0017 the card suggests only what peers make near-certain (New Year's Day closed) and spends its one question on the day worth most (New Year's Day, +0.90 expected points, just ahead of Christmas Day, +0.87; see 13.5): one answer is worth more than any amount of peer data.

## Learning (B5)

On the 199 dev businesses in the 2025 pilot (2,842 rows), at a fixed λ: the accepted/edited pilot entries are part of the history the engine learns from (with them 59.56, without 58.27; harmful 75 vs 79). The decline rule keeps the score (59.56 → 59.51) and removes harmful suggestions (75 → 72): when an owner rejected a 2025 suggestion, the 2026 truth equals the rejected hours only 7% of the time, so not repeating it is pure safety. The effect is small because only 28 dev rows carry a rejection; what carries over to production is the mechanism (a decline switches off that kind of evidence for that owner), and every reply adds one more.

## The one question (B1)

On 150 dev businesses with at least one needed change, asking the question `question_value()` picks adds **+0.57 points per business** (card total 814 → 900), against +0.11 for a random date. The predicted value (0.69) is close to the realised gain, so the model knows what an answer is worth. **Honest caveat:** almost all of the gain comes from the asked date itself (+0.57); the spillover to the other dates through M1 → M2 → M4 is about zero (+0.006), consistent with the ablations (M1 adds no points to the scorer). The one question earns its value by **choosing the right date** (five times a random date's gain), not by propagating the answer. It matters most for new or unpredictable businesses, where the card has the least to go on.

## Weekly hours that drifted (M6, A13)

`cs/engine/drift.py`, evaluated by `scripts/eval_drift.py` (5 × 5-fold cross-validation over the 802 dev businesses,
labels `listing_is_outdated`, 22 positives).

| | Result |
|---|---|
| Precision | **1.00** (12 suggested, 12 really outdated) |
| Recall | **0.55** (12 of 22) |
| Proposed week equals `true_regular_hours_now` | **12 of 12** (all 40 changed weekdays right) |
| SE-D0016 Restaurang Fyren (demo) | **SUGGEST**, p ≈ 1.00: two reviews say "Numera öppet bara mån–lör 11–22"; proposed Mon–Sat 11:00–22:00, Sunday closed (listed 11–23 and Sun 12–21) |
| All 3,017 businesses | 46 suggestions (12 dev, 33 test, 1 demo) |

- **What works:** reviews that state the new week ("Now only open Mon–Fri …", "Nykyään auki vain …", "Numera öppet bara
  …", "Nå bare åpent …"). We parse them into a full week (days not named are closed) and only count them when they
  differ from the listing. They never fire on an up-to-date listing in dev.
- **What doesn't:** map-user edits to weekly hours on their own. 364 dev businesses have at least one, only 8 of those
  are outdated, and the model never gives them more than p = 0.49, so they don't trigger a message. They still improve
  the *proposed* hours: drift in this data is one rule for the whole week (open an hour later, close an hour earlier,
  a day now closed), so the best-supported edit is generalised to every open day. That makes 18 of 22 proposals exact
  when we do have a drift review or several edits.
- **Missed:** 10 outdated businesses; 2 have no signal at all and 8 only have user edits. A natural product step is a
  low-key "are your weekly hours still Mon–Fri 10–18?" line on the season card for the edit-only cases; it is not built.

**For the app:** read `artifacts/predictions/drift_2026.parquet` (one row per business): `entity_id`, `p_outdated`,
`proposed_week` (dict weekday → hours string), `evidence` (list of {source, detail, ref, weight}: the review texts and
user edits), `action` (`SUGGEST` | `NO_ACTION`), plus `basis` (REVIEW / USER_EDITS / NONE), `published_week` (what the
listing shows now, for a before/after view) and `split`. Dev rows carry their cross-validated probability.

## Season Autopilot (B1): coverage with zero replies

`scripts/eval_autopilot.py`, dev businesses, 50 seeds per rate. The 2025 card is approved (an approving owner fixes what
was wrong, so the learned facts are the true 2025 hours); a share of owners grants "Approve + every year"; in 2026
their dates are applied automatically from the newest learned fact (0.95 decay per season, newest wins), at least 14
days ahead (dates closer than 14 days, such as Father's Day on 8 Nov, are applied at once with a one-tap undo: about 4 automatic changes at the 8% rate).
**The grant rates are assumptions:** 8% is the 2025 pilot (57 of 761 owners), 12% the owner simulator's fixed rate, 30%
and 50% hypotheses for a better button. The simulator's hidden profiles are not read.

| Grant rate | Autopilot alone, zero replies (blind) | Autopilot alone, gated by the engine | Season card alone, 47% reply | Card + gated autopilot |
|---|---|---|---|---|
| 8% (pilot) | 7.1% | 5.1% | 33.8% | **36.6%** |
| 12% (simulator) | 10.7% | 7.6% | 33.8% | **38.0%** |
| 30% | 27.4% | 19.5% | 33.8% | **44.4%** |
| 50% | 45.4% | 32.2% | 33.8% | **51.6%** |

(% of the 2026 at-risk lookups, i.e. expected search views on dev rows that need a change, fixed exactly or within 30 min.)

- **Habits change, so blind re-application is risky.** Re-applying last season's approved hours is exact 81% of the time
  but **wrong open/closed in 10.1%** of the automatic changes (twice the engine's own harmful rate).
- **Gating by the engine fixes it.** Applying a learned fact only where the 2026 engine gives it P ≥ 0.5 (and asking
  otherwise, as the card already promises: "anything new or different is still asked") makes the automatic changes
  **95% exact and 3.5% wrong open/closed**, at the cost of about a third of the coverage.
- **Recommendation for B1:** gated autopilot plus the card for everyone else. With the pilot's 8% it adds ~3 points of
  coverage over the card alone; every extra 10% of owners who grant adds about 3.5 points, with no reply needed.

## Pitch charts

`notebooks/02_results_for_pitch.ipynb` (executed) writes six PNGs to `artifacts/charts/`, each with a "how to read
this" paragraph: (a) score with 95% intervals vs the baseline, (b) harmful, (c) closed-door and all wrong-hours lookups
fixed, (d) learning with vs without the pilot feedback, (e) no history, (f) the autopilot coverage curve and the error
rate of automatic changes. One honest wrinkle shows in (c): on the 2025 back-test the engine fixes more lookups overall
(56.3% vs 55.1%) but slightly fewer closed-door ones (66.2% vs 67.7%).

## What didn't work, and caveats

- **Habit types (M1) and linked holidays don't add points to the scorer** on top of own history (see ablations). We
  expected them to; the gradient-boosted state model already gets the cross-holiday signal from simple summaries of a
  business's entries. Likewise, an owner's answer to B1's question barely changes the other days' decisions; the
  question's value is in choosing the date. M1 stays because it explains a business's habits in words and gives B1 its
  P(answer), not because it adds points.
- **Bigger trees** (500 iterations, 63 leaves): higher score only by suggesting more, with more harmful; no gain at
  matched harm. Default settings kept.
- **Choosing λ is noisy:** ±0.5 points between near-identical configurations. We compare variants at a fixed λ as well.
- **Analytics as a closure detector:** an 8-business collapse signal with zero closures on dev; used only as extra
  weight next to reviews and user edits.
- **"Soft" FLAGs:** the table gives +0.05 for a FLAG on any row that needs a change, so flagging uncertain rows would add
  about +0.6 points (757 more flags on dev). We leave it **off**: a FLAG means "a person must look", and a human queue of
  that size is not a product. It is one switch (`scripts/train.py --soft-flag`) if the team wants it.
- **The bootstrap interval for harmful** at test size crosses 224, so "harmful ≤ baseline" is a design target met on
  dev, not a guarantee.
- The final test model also learns from the dev businesses' 2026 season; its dev-row predictions would be optimistic,
  which is why the CSV's dev rows carry the cross-validated decisions instead.

## For the app (integration)

```python
from cs.engine.api import predict, habit_model, question_value
df = predict(2026)   # 41,755 rows; reads artifacts/predictions/engine_2026.parquet (0.4 s)
hm = habit_model()   # M1 with every business's entries before 6 Nov 2026 as evidence
hm.posterior("FI-D0017", {"XMAS_DAY": "closed"}); hm.day_probs("FI-D0017", "2027-01-01", {"XMAS_DAY": "closed"})
question_value("FI-D0001", 2026, {})   # [(date, expected points gained), ...] best first; 1-3 s
```

- `probability`: calibrated P(the suggested hours are exact) for SUGGEST; P(closed for good) for FLAG; P(the shown
  hours are already right) for NO_ACTION.
- `basis` picks the most trusted source behind the suggestion (own history first), `evidence` lists every signal with
  a `ref` the "why?" view can open, `candidates` the top 5 alternatives with probabilities.
- Retrain: `.venv/bin/python scripts/train.py --refresh --backtest` (about 6 minutes). Ablations:
  `scripts/eval_ablations.py` (about 20 minutes). B1 oracle test: `scripts/eval_question.py`.
