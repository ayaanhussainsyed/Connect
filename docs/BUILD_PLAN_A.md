# Build plan: Part A (everything Fonecta requires)

This plan turns Part A of `docs/PLAN.md` into concrete modules, functions, tests and an order of work. Part A is the compulsory chain. Part B (innovation) plugs into the interfaces defined here.

**Ground rules for all code:**
- **Facts come from data, never from an LLM.**
- **Everything that changes a listing goes through the guardrails (A4/A10) and leaves an audit record.**
- **Yext is the only write path.** The mock is used for bulk runs; the sandbox (`SinceAi04`, now Kahvila Aurora) for the demo.
- **Each module has its own test file;** `pytest` must stay green.
- **Web app:** Flask + Jinja + HTMX + Tailwind (CDN), with routes in `app/app.py`.
- **Deadline: Sunday 8 Nov, 10:00, video included.** Parts A and B are built on Saturday; code freeze Sun 05:00; video and deck 05:00–09:00.

---

## 0. Shared foundation (built first, used by everything)

| File | What it holds |
|---|---|
| `cs/config.py` | Loads `.env` (dataset path, Yext mock and sandbox URLs and keys, link secret, LLM keys, channel creds). One `settings` object. |
| `cs/hours.py` | A copy of the dataset's `mock_api/hours.py` (MIT, notice kept), plus `normalise()`, `is_valid()`, `to_yext_day()`, `from_yext_day()`, `same_state()`, `boundary_diff()`. |
| `cs/data.py` | Cached loaders for every dataset file, as pandas DataFrames or dicts: entities, holiday history, regular-hours history, calendar, targets (2026 and 2025), labels, contacts, listings, reviews, publisher suggestions, peers, pilot log, splits. Every loader that returns history takes an `as_of` date, so nothing after the snapshot can leak in. |
| `cs/domain.py` | Plain dataclasses shared everywhere (below). |
| `cs/db.py` | SQLite (`artifacts/connect.db`) with tables `suggestions`, `cards`, `messages`, `decisions`, `writes`, `permissions`, `audit_events`. Small repository functions (`save_suggestion`, `get_card`, …) and no ORM. |
| `cs/audit.py` | `log(event_type, payload)`: an append-only `audit_events` row whose `hash = sha256(prev_hash + json(payload))`. `verify_chain()` recomputes every hash. |

**Domain objects** (`cs/domain.py`):
```python
Evidence(source: str,          # OWN_LAST_YEAR | OWN_2_SEASONS | LINKED_HOLIDAY | MALL | PEERS | REVIEW | USER_EDIT | OWNER_ANSWER
         detail: str,          # human-readable, e.g. "2025-12-24 you entered 07:30–13:00"
         ref: str | None,      # row id in the dataset (for the audit / "why?")
         weight: float)

Suggestion(id, entity_id, kind,          # kind: HOLIDAY_HOURS | REGULAR_HOURS | REVIEW_REPLY | CLOSED_FOR_GOOD ...
           date, day_key, shown: str,     # what Google shows now
           action: str,                   # SUGGEST | NO_ACTION | FLAG
           value: str | None,             # hours string, or draft text for REVIEW_REPLY
           probability: float | None, basis: str, evidence: list[Evidence],
           model_version: str, created_at)

Card(id, entity_id, season, suggestion_ids, deadline_by_publisher: dict, question: dict | None, status)
Message(id, card_id, channel, language, text, buttons, token, sent_at)
Decision(id, card_id, suggestion_id, decision: APPROVE|EDIT|DECLINE|NO_RESPONSE, final_value, every_year: bool, at, via)
WriteRecord(id, entity_id, target: MOCK|SANDBOX, request, response_status, readback_ok, at)
```

**Tests:** `tests/test_hours.py` (round-trips, validation, past-midnight intervals) and `tests/test_audit.py` (the chain verifies; tampering is detected).

---

## Phase A-1: the whole five-step chain at baseline quality (Sat to noon, ~5 h)

**Goal.** `python scripts/demo.py FI-D0001` runs Detect → Suggest → Contact → Decide → Apply end to end: on the mock for the dataset business, and on the sandbox for the same café. The exported CSV reproduces 54.49.

### A1. "What Google shows now": `cs/display.py`
- `shown_hours(entity_id, date, as_of) -> str` uses the same logic as the baseline's `displayed()`:
  1. the regular-hours version with the latest `effective_from` before `as_of`;
  2. the latest holiday entry for that date with `entered_at < as_of`;
  3. `isRegularHours` means the regular hours apply;
  4. `reopenDate` and `closed` are respected.
- **Test:** `tests/test_display.py` compares with `displayed_hours_at_snapshot` on **all 11,100 dev rows (must be 100%)** and on the 2025 back-test labels.

### A2. Days to check: `cs/targets.py`
- `targets(season) -> DataFrame` from `evaluation/targets_2026.csv` (or the 2025 file).
- The live calendar adapter comes in A-4 (bonus); its interface is defined now: `special_days(country, start, end) -> list[(date, day_key, day_type)]`.

### A3 (v0). Suggestion engine, baseline version: `cs/suggest/baseline.py`
- Wraps the dataset's `Baseline.explain()` and converts each result into a `Suggestion` with `Evidence`:
  - last-year entry → `OWN_LAST_YEAR`;
  - shopping-centre entry → `MALL`;
  - peer share → `PEERS`;
  - closed signals → `FLAG`.
- `scripts/predict.py --engine baseline`:
  1. builds Suggestions for every target row;
  2. runs the guardrails;
  3. saves everything to the DB;
  4. `cs/export.py` writes `predictions_2026.csv`, containing **every** SUGGEST/FLAG row, whatever the opt-outs.
- **Test:** `tests/test_export.py` runs the official `evaluate.py` on the export and asserts a score of 54.49 ± 0.01.

### A4 (hard rules part) and A10: `cs/guardrails.py`
Each rule is a pure function returning `(ok, reason)`, with its own unit test:
| Rule | Check |
|---|---|
| `respects_owner_entry` | Never suggest against an exception the owner already entered for that date |
| `not_past` | Never act on a date before today |
| `valid_hours` | Hours pass the validator |
| `flag_not_message` | `FLAG` suggestions never become owner messages |
| `contact_allowed` | No message to opted-out owners (messaging only; the CSV keeps the row) |
| `permission_scope` | Automatic apply only if a standing permission covers this exact holiday and value |

`apply_guardrails(suggestion, context) -> Suggestion` downgrades to `NO_ACTION` or `FLAG` with a logged reason.

### A5/A6 (v0)
- Closed for good uses the baseline's closed signals (improved in A-2).
- Staying quiet falls out of A1 plus `respects_owner_entry`.
- **Tests:** FI-D0011 is flagged; FI-D0013 and NO-D0015 get no message.

### A7. The message: `cs/compose.py`
- **Templates in fi/sv/nb/en,** built on the dataset quickstart's wording and extended. Message parts:
  1. a greeting with the business name;
  2. the season summary: one line per date ("ke 24.12. — ehdotus 07:30–13:00 (viime vuonna sama)");
  3. what Google shows now if nothing changes;
  4. the "answer by" deadline (from A9's publisher speeds);
  5. a value line ("~140 ihmistä haki teitä viime jouluaattona", from analytics);
  6. buttons.
- **Local formats:**
  - day names per language;
  - dates as "ke 24.12." (fi), "ons 24/12" (sv), "ons. 24.12." (nb), "Wed 24 Dec" (en);
  - hours as "7.30–13" (fi) and "07:30–13:00" (en).
- **Reason sentences by evidence:**
  - own history → stated plainly;
  - shopping centre → named;
  - peers → phrased as a question.
- **Channel limits:**
  - WhatsApp body ≤ 1,024 characters and button titles ≤ 20;
  - SMS uses digits instead of buttons;
  - a check fails the build if any template breaks a limit in any language.
- **Optional:** Opus 5.5 may rewrite *only* the reason sentence. A regex check then confirms every date, time and number in the output matches the slots; otherwise the template sentence is kept.
- **Disclosure:** the first message to an owner says it's Connect's automated assistant (AI Act Art. 50).
- **Tests:** `tests/test_compose.py` covers 4 languages × each basis × single and multi-date, plus the character limits and button lengths.

### A8. No-login decision: `cs/links.py`, `app/app.py`
- **Tokens** are HMAC-SHA256 signed: `{card_id, owner_id, expires, nonce}`. The nonce is stored in the DB, which makes each token single-use and lets the server reject replays.
- **Routes:**
  | Route | What it does |
  |---|---|
  | `GET /c/{token}` | Renders the card (Jinja + HTMX). Changes nothing, so link scanners can't approve anything. |
  | `POST /c/{token}/decide` | Takes approve / edit per date / decline (and later the "every year" flag); writes `Decision` rows; triggers Apply. |
  | `POST /webhooks/whatsapp` | Button replies and text replies, mapped to the same handler. |
  | `POST /webhooks/sms` | Same, for SMS. |
- **Owner simulator** (`cs/sim.py`): for bulk runs, calls the mock's `/sim/v1/owner-response` once per date and records the decisions. Dev and demo businesses only.
- **Tests:** `tests/test_links.py` covers expired, reused and tampered tokens, and checks that a GET never creates a decision.

### A9. Apply and confirm: `cs/yext.py`, `cs/confirm.py`
- **`YextClient(target=MOCK|SANDBOX)`:**
  - **`set_holiday_hours(entity_id, changes: dict[date, hours])`:**
    1. GET the entity.
    2. **Owner-wins check:** if any target date changed since the suggestion was made, cancel that date.
    3. Merge the changes into the existing future `holidayHours`, drop past dates, sort, de-duplicate.
    4. PUT **only** `{"hours": {"holidayHours": [...]}}`. The sandbox merges sub-fields, so weekday hours stay untouched.
    5. Read back and compare.
    6. Write a `WriteRecord` and an audit event.
  - `clear_holiday_hours` sends an explicit `[]` (needed for test restore).
- **`confirm.py`:**
  - Lists the publishers where the business is live, with their expected update time (from `listings.json`, `typicalUpdateSpeedSeconds`).
  - Says plainly when a listing isn't live (opted out, or waiting on the publisher).
  - For the sandbox café it uses FI-D0001's listings and says so in the demo, because the sandbox Listings API is empty.
- **Tests:**
  - `tests/test_yext_mock.py` (against the mock server started in a fixture): merge keeps other dates, past dates dropped, weekdays untouched, owner-wins cancels.
  - `tests/test_yext_sandbox.py`, marked `@sandbox`: write one future date, read back, restore.

### Channels (A7/A8 delivery): `cs/channels/`
- **`base.py`:** a `Channel.send(message) -> DeliveryRecord` interface.
- **`outbox.py`:** stores every message for the on-screen phone and inbox. Always on, and used by tests.
- **`email.py`:** SMTP, if `SMTP_URL` is set.
- **`whatsapp.py`:** WhatsApp Cloud API. The first message uses the utility template with quick-reply buttons; replies inside the 24-hour window use interactive buttons.
- **`sms.py`:** stub plus an outbox. Real sending only to a non-Finnish test phone.
- **Routing:** each owner's preferred channel from `customer_contacts.csv`; opted-out owners are skipped.

### Demo and ops (thin version now, richer later): `api/`
- **`GET /ops`:** the suggestion queue, each suggestion's evidence, the flag queue, the audit trail, and an **Export CSV** button.
- **`GET /demo`:** a phone mock (the outbox thread) next to a live GET of the sandbox entity.

**Phase A-1 is done when:**
- `pytest` is green;
- `scripts/demo.py FI-D0001` prints all five steps;
- the café's holiday hours appear in the sandbox and are read back;
- the CSV scores 54.49;
- a backup screen capture of the sandbox write is recorded.

---

## Phase A-2: suggestion quality and prevention (Sat afternoon–evening, ~7 h)
This phase uses Part B's score engine (B2) inside Part A's interfaces.

### A3 (v1). The score engine: `cs/engine/`
- **`features.py`:** time-respecting features per target row: own entries for this holiday one and two seasons back, linked holidays, peer stats computed from entries before `as_of`, shopping centre, weekday and regular hours, review and user-edit evidence, events and school breaks.
- **`habits.py`:** the habit-type mixture model (EM), fitted on `dev_truth_history.csv`. The number of types is chosen by held-out likelihood.
- **`state_model.py`:** `HistGradientBoostingClassifier` (closed / regular / special), trained on the dev truth with folds grouped by business.
- **`ranker.py`:** scores the candidate hours, then calibrates with isotonic regression.
- **Output:** a probability for each candidate set of hours.

### A4. Prevent wrong suggestions: `cs/decide.py`
- **`expected_points(candidates, shown)`** uses the scorer's exact table, FLAG included.
- **The flip margin λ** is tuned by business-grouped cross-validation on dev: maximise score with harmful ≤ 224.
- **`bootstrap_ci()`** gives confidence intervals for the score and the harmful count at test size.
- **Tests:** the EV function is checked against `evaluate.py` on synthetic cases (it must give the same points), and λ is monotonic.

### A5 (v1). Closed for good: `cs/flags.py`
- **Decided per business,** using closure language in reviews (regex, then Opus), `closed` user edits, and collapsing analytics.
- **If P(closed for good) beats the expected value of suggesting,** every row for that business is flagged.
- **Measured** on `dev_regular_hours_truth.permanently_closed` (7 positives) for precision and recall.

### A6. Stay quiet
A result of A4 (NO_ACTION wins) plus the guardrails. FI-D0013 and NO-D0015 are tests.

### A12. No history: `scripts/eval_nohistory.py`
- Masks the entry history of dev businesses and reports the score and harmful count, against the full-history score.
- FI-D0017 is the demo case: pooled priors, cautious limits, and (via B1) one question.

**Phase A-2 is done when:**
- the dev score with CI is above 54.49 with harmful ≤ 224;
- the honest 2025 back-test is reported;
- the ablation table and the no-history number are in `docs/RESULTS.md`.

---

## Phase A-3: learning and the whole profile (Sun morning, ~4 h)

### A11. Learning: `cs/learn.py`
- **Learned facts age out and are superseded:** a newer answer for the same holiday replaces the old one; weight decays by season age (measured: 0.94 → 0.83 repeat rate); facts drop out after three seasons unless reconfirmed. Store: Mongo `learned_facts`.
- **Decisions become evidence.**
  - `APPROVE` and `EDIT` are stored as `OWNER_ANSWER` evidence, the highest priority, for that date and its linked holidays (now and next season).
  - `DECLINE` on a peer-based suggestion switches off `PEERS` evidence for that owner.
- **Standing permission:**
  - granted from the "every year" button with a stated scope;
  - applied ≥ 14 days ahead, or immediately with a logged note if the date is closer;
  - ended by undo, stop or opt-out;
  - an edit updates the agreed hours instead of ending it.
- **Measurement:** `scripts/replay.py` replays the 2025 pilot log as feedback, with and without it. The `CONNECT_SUGGESTION` overlap is removed, and results are reported on pilot owners.
- **Tests:** a permission covers only its scope; undo revokes; an edit updates; the < 14-day rule is logged.

### A13. The whole profile: `cs/profile/`
- **`drift.py`** (regular hours that drifted): signals are user edits to regular hours, reviews mentioning other hours, and regular-hours history. Output is a `REGULAR_HOURS` suggestion. **Measured** on `dev_regular_hours_truth.listing_is_outdated` (22 positives). Demo: SE-D0016.
- **`review_reply.py`** (replies to closed-door reviews): it finds complaint reviews with no owner reply and drafts an apology plus the confirmed hours from a template. Opus may tidy the wording; a slot check verifies the facts. Output is a `REVIEW_REPLY` suggestion, approved in the same thread. It writes to the mock's reviews (the sandbox has none).
- **Closed for good:** A5.
- **Holiday post and description check:** only if ahead of schedule, through the same `Suggestion` interface.

**Phase A-3 is done when:** the learning number, the drift precision/recall and the review-reply demo are in `docs/RESULTS.md`, and their tests pass.

---

## Phase A-4: deliverables and bonuses (Sun, before the code freeze, ~4 h)

### A14
- **Live sources (bonus):**
  - **`cs/live/calendar.py`:** the `holidays` package (or Nager.Date) plus our rules for eves, between-holiday days and commercial days. Agreement with `calendar_special_days.csv` is reported per country and day type.
  - **`cs/live/yext_sandbox.py`:** the live entity read and write, already used.
  - Coverage, quality, gaps and score impact are written up for both.
- **`README.md`** follows the dataset's template:
  - run instructions;
  - the five steps;
  - data sources (with production equivalents);
  - what didn't work;
  - the no-history case;
  - final messages as text (fi/sv/nb/en, plus the approve, edit and decline follow-ups);
  - the bonus;
  - the licence table, including "Connect Suggests simulator, Since AI ry 2026", CC BY 4.0.
- **Frozen numbers:** `evaluate.py --json` is run on the tagged commit and pasted into the README; the deck and video use the same JSON.
- **A 5-person message test** (friends, not real businesses), and GPT-6 Astra clarity scores per language, recorded in `docs/RESULTS.md`.
- **Video (2.5 min)** following `docs/PLAN.md` Part E. **Deck:** problem, solution, how it learns, results, next steps.
- **Final checks:** keys never in the repo or on screen (`git grep` for the key before tagging); no GPL/AGPL dependencies (`pip-licenses` check).

---

## Order of work in Phase A-1 (each step ends with a passing test)

1. `config`, `hours`, `data`, `domain`, `db`, `audit` (+ tests)
2. `display` (100% match test)
3. `suggest/baseline` + `guardrails` + `export` (54.49 test)
4. `yext` against the mock (+ tests), then the sandbox test
5. `compose` (+ tests), `channels/outbox`
6. `links` + `app/app.py` (card page, decide route) (+ tests)
7. `confirm` + `scripts/demo.py FI-D0001` end to end
8. `channels/email`, `channels/whatsapp` (once credentials are in `.env`)
9. `/ops` and `/demo` pages, thin versions
