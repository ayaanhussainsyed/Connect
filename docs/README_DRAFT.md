# <Team name> – Connect Suggests

> Draft by the ML agent for the sections it knows best (data sources, what didn't work, no history, live sources,
> results, ML licences). Sections marked **(builder)** are for the app side to fill. Every number below comes from
> Fonecta's own scorer or from scripts in this repo, and can be re-run (commands at the end of *Results*).

## Run it
**(builder)** Commands to start the demo, plus any keys or settings needed.

The score engine on its own:
```bash
.venv/bin/python scripts/train.py --refresh --backtest   # ~6 min: CV on dev, final model, predictions, back-test
.venv/bin/python dataset/FONECTA_CONNECT_SUGGESTS_2026/evaluation/evaluate.py --predictions outputs/engine_predictions_2026.csv
.venv/bin/pytest tests/engine tests/test_live.py -q
```
The live calendar needs one extra package: `.venv/bin/pip install holidays` (MIT). Without it, the calendar falls back
to the Nager.Date API.

## The five steps
**(builder)** One line each: detect, suggest, contact, decide (no login), apply + confirm.

## Data sources that mattered most

We measured each source by removing it and re-running the whole cross-validated pipeline (ablations on the 2026 dev
season; full engine = 61.67).

| Signal | How we used it | What it is worth (measured) | Where Connect would get it in production |
|---|---|---|---|
| **The business's own past entries for the same holiday** | The strongest evidence for both "is it closed?" and "which hours?"; owners' entries are 99% accurate | Without any own history the score falls from 61.7 to **33.4** | Connect's audit log of entered holiday hours (Yext `holidayHours` history) |
| **The entry from two seasons back** | Fills the gaps when last season wasn't entered (16% of the needed changes) | Without it: **−5.9 points** (55.8) | The same log, kept for at least three seasons |
| **The business's other holidays** | A café closed on Christmas Day is 89% likely to be closed on New Year's Day (36% otherwise); we use the mix of a business's entries, its linked holidays and its habit type | Without any of it: **−1.3 points and +21 harmful** suggestions at the same safety margin | The same log |
| **Owners' past decisions** (2025 pilot) | Accepted and edited answers become history; a declined suggestion is never repeated, and a declined peer/mall suggestion switches off guesses for that owner | On the 199 pilot owners: score **58.3 → 59.6** and harmful **79 → 75** with the answers; harmful **75 → 72** with the declines | Connect's own suggestion and decision log |
| **The shopping centre's hours** | Candidate hours for shops inside a centre (right 83% of the time when present) | Small: **−0.15 points** without it (−0.35 at the same margin) | Centres' integrations (`API_INTEGRATION` entries) or their websites, published 4–8 weeks ahead |

Two more inputs every suggestion depends on: **what Google shows now** (our logic matches Fonecta's displayed hours on
100% of dev rows; production: the Yext Knowledge Graph *Entities* API), and **reviews that state new weekly hours**
("Numera öppet bara mån–lör 11–22"), which drive the weekly-hours drift detector (production: Yext *Reviews*).

## What we tried that didn't work
- **Habit types didn't add points.** Our mixture model of habit types (EM over closed / regular / special, 10 types)
  is accurate and readable, but removing it changes the score by +0.2 (within the ±0.5 noise of the method). Simpler
  summaries of a business's entries carry the same information. We keep it for explanations ("businesses like yours
  close on all church holidays") and for the season card's question, where its role is also modest: an owner's answer
  barely changes the other days' decisions (+0.006 points); the question's value comes from *which* date we ask about.
- **Peers, reviews, map-user edits and events added no points** on top of the business's own history (removing each
  changes the score by +0.2 to +0.4, within noise). Peers still help businesses with no history.
- **Fonecta's `peer_benchmarks.csv` leaks the future** in a back-test: it pools 2023–2025, so it contains the 2025
  answers. We never read it and compute peer statistics ourselves from entries made before the snapshot.
- **Analytics as a closure signal:** 8 dev businesses show a collapse in search views over 30 days; none of them closed
  for good (seasonal dips). Closure reviews and "closed" user edits are what work; analytics only adds weight to them.
- **Re-applying last year's hours blindly** (a naive "every year" permission) gets open/closed wrong in **10.1%** of
  the automatic changes, because about 8% of habits change each year. Applying a learned answer only where this year's
  model agrees (and asking otherwise) cuts that to **3.5%**.
- **Detecting changed weekly hours from map-user edits alone:** 364 dev businesses have such edits, only 8 of them
  really changed their hours; the edits never reach a 50% probability, so they are not enough to message an owner.
- **Matching businesses to company registers** (PRH/YTJ, Brønnøysund): ruled out. The businesses are synthetic, so
  they are not in the registers, and registers hold no opening hours anyway.
- **Bigger models:** more and deeper trees raised the score only by suggesting more, with more harmful suggestions;
  no gain at the same level of harm.

## Customer with no history / no previous actions
**`FI-D0017` Kahvila Kipinä**, a café in Turku that joined on 12 Oct 2026, has no entered exception hours and no
previous decisions. What the engine does:
- It starts from what similar businesses do (cafés in Finnish cities of the same size, from entries made before the snapshot) and its
  segment's habit-type prior, never from a guess presented as fact.
- **It suggests only what is near-certain:** New Year's Day closed (80%, from similar businesses). On the other 13 days
  it stays quiet. For example, Christmas Day is 69% likely closed, but a change from "open" to "closed" that isn't backed
  by the owner's own history needs stronger evidence.
- **It asks one question** instead of guessing: the date where one answer gains the most expected points, here New
  Year's Day (+0.90 expected points) or Christmas Day (+0.87). The answer becomes the owner's first learned fact, and
  next season the business is no longer "new".

Measured:
- On the 16 dev businesses that really have no entries, the engine scores **36.8 against the baseline's 14.2**
  (harmful 2 vs 4), mostly by staying quiet where the baseline guesses.
- As a stress test, hiding every dev business's own history drops both methods to about a third of their normal
  score (engine 34.1, baseline 33.2). Without a business's own past, peers and shopping centres can't recover its
  habits, which is why the one question matters most for new customers.

## Final customer messages (as text)
**(builder)**

## Bonus: live source

### 1. The special-day calendar, built live (`cs/live/calendar.py`)
We replaced `calendar_special_days.csv` with a live calendar:
- **Official public holidays** come from the Python `holidays` package (MIT, offline, versioned). The fallback is the
  Nager.Date API (`date.nager.at/api/v3/PublicHolidays/{year}/{FI|SE|NO}`).
- **Our own rules** cover the days that matter for opening hours but are not public holidays: the eves (Epiphany,
  Maundy Thursday, Easter Saturday, May Day, Whitsun, Midsummer, Christmas, New Year's), 23 December, the days between
  Christmas and New Year (27–30 Dec except Sundays), Lucia (Sweden), Black Friday (the day after the fourth Thursday of
  November), Father's Day (second Sunday of November) and Mother's Day (each country's own Sunday).
- Each day is mapped to the dataset's `day_key`.

| | Result |
|---|---|
| **Coverage** | All **244** dataset calendar rows for 2024 – 6 Jan 2027 reproduced (FI, SE, NO): **0 missing, 0 extra**; type, class and weekday identical |
| **Quality** | The "official public holiday" flag agrees on 100% of rows with the `holidays` package. **Nager.Date gets every date right but flags 4 days differently:** Swedish Midsummer Eve, Christmas Eve and New Year's Eve, and Norwegian Christmas Eve, are listed as public holidays (they are de-facto days off, not legal holidays) |
| **Gaps** | Public-holiday sources stop at public holidays: the other 10–14 kinds of days per country (eves, observances, commercial days) are our own rules. Event nights and school breaks need other sources (LinkedEvents, municipal calendars); school breaks differ by municipality |
| **Impact** | With the live calendar, the 41,032 calendar target rows of `targets_2026.csv` are rebuilt **exactly**, so the score is unchanged (61.67). In production the calendar runs years ahead without a dataset file |

Re-run: `.venv/bin/python -m cs.live.calendar` (agreement report) and `.venv/bin/pytest tests/test_live.py`.

### 2. The Yext sandbox (`SinceAi04`), read and written live
Measured on 7 Nov 2026 (details in `docs/SETUP_LOG.md`):
- **Read:** the entity reads live.
- **Write:** a future `holidayHours` date writes and reads back correctly, and the weekday hours are untouched.
- **API behaviour we measured:**
  - a list we send replaces the stored list, so we always send the full list;
  - leaving the `holidayHours` key out keeps the stored list;
  - `[]` clears it;
  - past dates are accepted but silently dropped.
- The sandbox entity now shows our demo café **Kahvila Aurora** (FI-D0001's name, address and hours), so what we detect
  is what we write. The original entity is backed up.
- **Gaps:**
  - Listings, publisher suggestions, reviews and posts answer 200 but are **empty** for sandbox entities.
  - **Analytics and the Knowledge Graph Suggestions API return 403**, so there is no native approval queue, and our
    own audit trail is the record.
  - The mock stays the source for detection signals; the sandbox proves the write path.

## Results

Frozen from `outputs/engine_predictions_2026.csv` (md5 `ba95c12bb2510892d42b5a8edd43c570`). In that file the dev rows
hold the **cross-validated** decisions: every dev business is predicted by models that never saw it, with the
calibration and the safety margin chosen on other businesses. That makes this the honest dev number. Test and demo
rows come from the final model.

| | Score | Harmful | Exact precision | Recall | Search views protected | Closed-door views prevented |
|---|---|---|---|---|---|---|
| **Our engine, dev 2026** | **61.67** | **219** | 0.859 | 0.670 | 65.0% | 72.9% |
| Fonecta baseline, dev 2026 | 54.49 | 224 | 0.901 | 0.590 | 57.2% | 69.2% |
| **Our engine, 2025 back-test** | **55.42** | **211** | 0.866 | 0.597 | 56.3% | 66.2% |
| Fonecta baseline, 2025 back-test | 53.23 | 249 | 0.899 | 0.578 | 55.1% | 67.7% |

- **Confidence:** resampling dev businesses to the size of the hidden test set gives a 95% interval of **60.4–62.9**
  for our score and **+6.3 to +8.1** points over the baseline. Harmful suggestions, scaled to 11,100 rows, fall in
  200–239: at the baseline's level, not guaranteed below it.
- **The back-test** uses only data stamped before 6 Nov 2025: no pooled peer file, no later entries or pilot decisions,
  models trained only on earlier periods. It gains less because the entry history was then only one season deep.
  - It fixes more lookups overall, but slightly fewer closed-door ones than the baseline (66.2% vs 67.7%).
- **Beyond the scored rows:**
  - **Closed for good:** 1 of the 2 dev closures whose listing still looked open is flagged, with no false flags.
  - **Weekly hours that drifted:** precision 1.00, recall 0.55 (12 of 22). The proposed new week is exactly right
    for all 12, and the demo restaurant SE-D0016 is flagged (two reviews state its new hours).
  - **Season Autopilot:** the share of 2026 at-risk lookups fixed with zero owner replies depends on how many owners
    grant permission. The grant rates are assumptions:

    | Owners granting "every year" | 8% (2025 pilot) | 12% | 30% | 50% |
    |---|---|---|---|---|
    | Lookups fixed with no reply | 5.1% | 7.6% | 19.5% | 32.2% |
    | With the season card for everyone else (47% reply) | 36.6% | 38.0% | 44.4% | 51.6% |

    The season card alone, at the pilot's 47% reply rate, fixes 33.8%.

**Charts** (`artifacts/charts/`, made by `notebooks/02_results_for_pitch.ipynb`):
- `a_score_vs_baseline.png`
- `b_harmful_vs_baseline.png`
- `c_closed_door_views.png`
- `d_learning_feedback.png`
- `e_no_history.png`
- `f_autopilot_coverage.png`

The full analysis is in `notebooks/01_data_and_models.ipynb` and the plain-language summary in `docs/ML_FINDINGS.md`.

`python evaluate.py --predictions outputs/engine_predictions_2026.csv --json`:
```json
{
  "score_0_100": 61.67,
  "rows": 11100,
  "rows_needing_action": 5398,
  "suggestions_made": 4195,
  "precision_exact": 0.859,
  "recall_exact": 0.67,
  "harmful_suggestions": 219,
  "search_views_protected_pct": 65.0,
  "closed_door_views_prevented_pct": 72.9,
  "businesses_contacted": 750,
  "avg_dates_per_contacted_business": 5.59,
  "breakdown": {
    "correct_silence": 5438,
    "missed": 1449,
    "exact": 3604,
    "same_state_off": 164,
    "harmful_wrong_state": 219,
    "spam_wrong_hours": 203,
    "flag_correct": 11,
    "near": 1,
    "missed_closed_business": 7,
    "suggest_to_closed_business": 4
  },
  "ignored_prediction_rows_not_in_labels": 30655
}
```

To re-run:
- `scripts/train.py --refresh --backtest`;
- `scripts/eval_ablations.py`;
- `scripts/eval_question.py`;
- `scripts/eval_drift.py`;
- `scripts/eval_autopilot.py`;
- then execute both notebooks.

## Third-party code, data and licences

| Component | Licence |
|---|---|
| Fonecta Connect Suggests dataset (Since AI ry 2026) | CC BY 4.0 (data), MIT (code; our display logic follows its baseline) |
| pandas, NumPy, SciPy, scikit-learn, joblib | BSD-3-Clause |
| PyArrow | Apache-2.0 |
| Matplotlib | Matplotlib licence (PSF-based, BSD-compatible) |
| `holidays` (Python package) | MIT |
| Nager.Date API (fallback calendar) | Free public API, no key; the project is open source (MIT) |
| httpx | BSD-3-Clause |
| Jupyter / nbconvert, pytest | BSD-3-Clause, MIT |
| **(builder)** Flask, HTMX, Tailwind, SDKs … | |

No GPL/AGPL components.
