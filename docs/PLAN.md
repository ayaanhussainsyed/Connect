# Connect Suggests – Customer Approves: final build plan

Since AI Hackathon 2026 × Fonecta · Turku, 6–8 Nov 2026 · final plan, Fri 6 Nov (revised after an independent judge review)

## Context

**Sources.** Fonecta's challenge replaces our earlier plan:
- the slides, `~/Desktop/Connect suggests – Since AI 2026_Fonecta.pdf`;
- the brief, `~/Desktop/Fonecta_SinceAI2026/Fonecta_x_SinceAI_2026_Challenge_Brief_EN.pdf`;
- the dataset zip, `~/Desktop/Fonecta_SinceAI2026/FONECTA_CONNECT_SUGGESTS_2026.zip`.

**The task.**
- Build a working demo of the whole chain:
  1. **Detect:** which data says the hours must change?
  2. **Suggest:** which hours, and why?
  3. **Contact:** a message that states the suggestion and the reason.
  4. **Decide:** approve, edit or decline, with no login.
  5. **Apply:** write to Yext and tell the owner where it shows.
- "A complete chain beats a perfect single step."
- Fonecta's slide 4 sets the goal: "Passive customers get value too – and the customer always has the final say."

**The scored part.**
- For 3,000 synthetic businesses (Finland, Sweden, Norway) and every special day from 7 Nov 2026 to 6 Jan 2027, we output `SUGGEST` + hours, `NO_ACTION`, or `FLAG`.
- 41,755 rows; the organisers score the 30,398 test rows on hidden answers.
- Points per row:
  - exact +1;
  - within 30 min +0.6;
  - same open/closed state +0.2;
  - **open/closed backwards −1**;
  - an unneeded message −0.1 to −0.5;
  - FLAG: +1 on a business closed for good, +0.05 on a row needing a change, −0.1 on a correct row.
- **Score** = points ÷ rows that need a change or a flag (5,398 on dev). Confidence is ignored, missing rows count as `NO_ACTION`, and contact preferences are ignored.
- Baseline: **54.5** on dev; exact precision 0.90, recall 0.59, 224 harmful.

**How we're judged:**
- suggestion quality (the scorer);
- customer experience;
- reliability;
- extensibility;
- learning;
- all five steps shown.

**Rules:**
- Synthetic data only.
- Yext is the backend: the mock for detection, **our sandbox** for the write path.
- Credentials stay out of the repo and the videos.
- No GPL/AGPL.
- Honest back-tests.
- Respect opt-outs and standing permission.
- Nothing published without approval or standing permission.
- An audit trail.
- The dataset is CC BY 4.0, so we credit it.

**What we submit:**
- a 2–3 min video;
- the repo, with a README in their template;
- the 3–5 key data sources, where Connect would get them in production, and what didn't work;
- the messages, as text;
- the no-history case;
- a short deck;
- the bonuses: live sources and `predictions_2026.csv`.

**Our sandbox.**
- `SinceAi04`, "Sharp Scissors Barbershop", Kauppakatu 21, Jyväskylä. Mon–Sat 10–18, Sun closed, no holiday hours. Read access verified.
- We may rename it and change its details.

**Decisions taken:**
- **Channels:** all of them, routed by each owner's preference.
  - **WhatsApp:** live, the main demo channel.
  - **Email:** live, with one-tap links.
  - **SMS:** live only to a non-Finnish team phone, because Traficom's regulation 28 L/2025 (§10) makes Finnish operators block unverified API senders from 2 Nov 2026 ([regulation](https://www.traficom.fi/files/media/file/M%2028%20L2025_en_0.pdf); [Verkkouutiset](https://www.verkkouutiset.fi/a/huijausviestien-torjunta-kiristyy-epailyttava-lahettaja-merkitaan-roskapostiksi/)).
  - **App push:** simulated.
- **AI:** ML decides the hours. Opus 5.5 reads and writes language. GPT-6 Astra is the independent judge in our message-quality evaluation.
- **Web app:** Flask + Jinja + HTMX + Tailwind + SQLite. The CSV is an export from the database.
- **Scope:** the whole profile, not just hours, plus emergency changes, EU rules and Fonecta/Yext fit.
- **Fonecta contacts:** Q&A at Joki on Sat 9–12 and 14:30–18, and on Discord; none on Sunday.

## What the data says

| # | Finding | Number | Design consequence |
|---|---|---|---|
| 1 | Habits repeat | 92% of businesses keep exactly the same hours on a holiday year to year (94% on rows needing change; 83% across two seasons) | A business's own past is the strongest evidence |
| 2 | Owner entries are accurate, but biased | 99% match the truth, but owners mostly enter a day *when it's special* | Use entries as features and for hours; train the open/closed model on the unbiased dev truth |
| 3 | Where the needed changes are (5,376 dev rows) | 44% last season · **16% only two seasons back** · **36% only other holidays** · 1.4% nothing | About half of the needed changes are invisible to "copy last year" |
| 4 | Holidays are linked | P(closed New Year's Day \| closed Christmas Day) 0.89 vs 0.36; Boxing Day 0.67 vs 0.23 | One latent "habit type" per business explains many days at once |
| 5 | Habits belong to each holiday | Blanket rules per type of day get 65% exact, against 94% for same-holiday memory | No blanket rulebooks |
| 6 | The days where businesses are split | Closed share in the dev 2026 labels: Christmas Day 83%, New Year's Day 80% (near-certain) · Christmas Eve 61%, Boxing Day 60%, Independence Day 60%, Epiphany 57%, Father's Day 53% (split across businesses). Across all seasons, the judge measured Christmas Day 82%, Boxing Day 58%, Independence Day 55% | A day can be split across businesses and still certain for one of them, so the question is chosen per business (B1) |
| 7 | Peers | Peer closed-share predicts closed with AUC 0.78 | A prior for new customers |
| 8 | Reviews and user edits | User edits that name a date are 90% right; 78% of hours-complaint reviews follow a "shown open, actually closed" day | Evidence and reasons, about 4% extra coverage |
| 9 | Analytics | Weak closure signal (AUC 0.65) | Use them for priority and the value line ("people looked you up"), not for deciding hours |
| 10 | Stakes | 63% of needed changes are "shown open, actually closed" | Closed doors are the main harm |
| 11 | Pilot: channels | Replies: WhatsApp 65%, SMS 50%, email 40%, push 32%; **53% never replied** | **The real bottleneck is silence, not taps** |
| 12 | Pilot: trust and format | Owners accepted 95% of own-history suggestions vs 81% of peer-based ones; bundles of 1–6 dates got the same reply rate; 8% granted standing permission | One card per season; offer permission when the evidence is the owner's own history |
| 13 | Publisher update speed (`listings.json`) | Google 2 min · Facebook/Finder/Hitta 10 min · Caller 1 h · Bing, 1881, Eniro 1 day · **Apple 2 days** · Tripadvisor 3 days · **AI assistants and Foursquare 7 days**. Some listings are opted out or waiting | **Deadlines:** to show on every channel, approval must come about 7 days before the date |

---

## Measured results (7 Nov, 01:40; ML agent, verified with the official scorer)
| | Score | Harmful | Exact precision | Recall | Closed-door lookups prevented |
|---|---|---|---|---|---|
| **Our engine, dev 2026** (business-grouped CV) | **61.67** (95% CI 60.4–62.9) | 219 (CI 200–239) | 0.859 | 0.670 | 72.9% |
| Fonecta's baseline, dev 2026 | 54.49 | 224 | 0.901 | 0.590 | 69.2% |
| Our engine, 2025 back-test (data before 6 Nov 2025 only) | **55.42** | 211 | 0.866 | 0.597 | — |
| Fonecta's baseline, 2025 back-test | 53.23 | 249 | 0.899 | 0.578 | — |

- **Gain:** +7.2 points over the baseline (paired bootstrap 95% CI 6.3–8.1).
- **No history:** for the 16 dev businesses with no entries, 36.8 vs 14.2.
- **Learning (pilot owners, with vs without 2025 feedback):** 59.56 vs 58.27 (+1.3), harmful 75 vs 79. The decline rule then takes harmful from 75 to 72.
- **Closed for good:** 1 of 2 hidden closures flagged, with no false flags.

**Honest corrections from the results** (these change what we claim):
- **What lifts the score** is the business's own history, including two seasons back (without that entry: −5.9), plus time-respecting training. **The habit-type model (M1), peers and reviews add no measurable points on top of own history.** Information from other holidays matters (−1.3 without it), but simple per-business summaries carry it as well as M1. **We don't claim M1 lifts the score.**
- **B1's one question** adds +0.57 points per business, against +0.11 for a random date. **Almost all of that comes from choosing the right date;** the spillover to other dates is about 0. **We drop the "one tap refreshes the other days" claim and demo moment.** The claim becomes: *Connect asks about the one date where your answer matters most.*
- **Learned facts decay by 0.95 per season** (the same hours repeat 92% / 87% / 81% after 1 / 2 / 3 seasons).
- **Season Autopilot needs a second check (changes B1).** Re-applying last year's approved hours blindly gets open/closed wrong **10.1%** of the time, because habits change. So the rule is: **apply a learned fact automatically only where this year's model agrees (P ≥ 0.5); otherwise put that date on the card and ask.** Automatic changes are then **95% exact, with 3.5% wrong open/closed**, and coverage drops by about a third.
- **Coverage of next season's at-risk lookups** (dev; the grant rates are assumptions: 8% pilot, 12% simulator, 30% and 50% hypothetical):

  | Grant rate | Card only (47% reply) | Card + autopilot |
  |---|---|---|
  | 8% | 33.8% | 36.6% |
  | 12% | 33.8% | 38.0% |
  | 30% | 33.8% | 44.4% |
  | 50% | 33.8% | 51.6% |
- **Drift detector (A13):** precision 1.00, recall 0.55 on the 22 outdated dev listings. All 12 flagged businesses get exactly the right new weekly hours, found from reviews like "Numera öppet bara mån–lör 11–22". SE-D0016 is flagged.
- **One honest caveat for the pitch.** On the 2025 back-test, closed-door lookups prevented are 66.2% for us against 67.7% for the baseline, even though we fix more wrong-hours lookups overall (56.3% vs 55.1%) and make far fewer harmful suggestions (211 vs 249). We quote dev and back-test side by side.
- **Charts** for the deck are in `artifacts/charts/a–f*.png` (`notebooks/02_results_for_pitch.ipynb`).

## Phase 0 results (7 Nov)
- **Baseline reproduced:** 54.49 on dev, 53.23 on the 2025 back-test.
- **LightGBM swapped for scikit-learn's `HistGradientBoosting`.** LightGBM needs a system library (`libomp`) that isn't installed; the replacement is the same family of models and BSD-licensed. Wherever this plan says LightGBM, read HistGradientBoosting.
- **Sandbox behaviour, measured:**
  - sending only `holidayHours` keeps the weekday hours;
  - a sent list replaces the stored one;
  - leaving the key out keeps it unchanged;
  - `[]` clears it;
  - past dates are accepted but silently dropped.
- **Sandbox endpoints:**
  - Listings, publisher suggestions, reviews and posts work but are empty;
  - the **Knowledge Graph Suggestions API and Analytics return 403**, so there's no Yext approval queue and our own audit trail is the record.
- **The sandbox entity is now Kahvila Aurora** (FI-D0001's name, address and hours); the original is backed up.
- Details: `docs/SETUP_LOG.md`; the Part A build plan: `docs/BUILD_PLAN_A.md`.

## Part A — What Fonecta expects (compulsory)

| ID | Feature | Step | Done when |
|---|---|---|---|
| A1 | **"What Google shows now" engine.** For any business and date: regular-hours version, entered exceptions, the regular-hours flag, reopen date, closed flag. Reuses the baseline's `displayed()` | Detect | Matches `displayed_hours_at_snapshot` on 100% of dev rows |
| A2 | **Days to check.** Uses `targets_2026.csv` as given. A live calendar adapter (bonus) is compared against the dataset calendar | Detect | Agreement reported |
| A3 | **Suggestion with hours and reasons.** Saved to the database and exported to `predictions_2026.csv`. The CSV includes every row, whatever the contact preferences | Suggest | The scorer runs; dev score with a confidence interval |
| A4 | **Prevent wrong suggestions** (details below) | Suggest | Harmful ≤ 224 on dev, with a bootstrap CI at test size |
| A5 | **Flag businesses closed for good,** decided per business, never messaged (FI-D0011) | Suggest | Validated on `dev_regular_hours_truth.permanently_closed` |
| A6 | **Stay quiet when it's already right** (FI-D0013, NO-D0015) | Suggest | No message |
| A7 | **The message:** the owner's language (fi/sv/nb/en) and preferred channel; it states the suggestion, the reason, the deadline and the value | Contact | Messages for all 17 demo businesses |
| A8 | **No-login decision:** approve, edit, decline | Decide | Works on a phone with no account |
| A9 | **Apply and confirm.** Writes to the Yext sandbox following Yext's rules; then tells the owner where and when it shows, including listings that aren't live | Apply | The sandbox entity updates; a confirmation is sent |
| A10 | **Guardrails.** Nothing published without approval or permission; opt-outs; one evidence record per suggestion, used as audit, "why?" source and ops view | All | An audit report per run |
| A11 | **Learning.** Better after an edit or decline, plus standing permission | Decide/Apply | Measured with the pilot-log replay (B5) |
| A12 | **No history** (FI-D0017) | Suggest/Contact | Measured by masking dev businesses' history |
| A13 | **The whole profile, through the same loop** (table below) | All | Two extra measured types, plus closed for good and holiday posts |
| A14 | **Deliverables.** Video, README, data sources, messages, deck, licences (incl. CC BY 4.0 attribution), hour budget, code freeze, a 5-person message test | — | Part E checklist |

### A4. Prevent wrong suggestions
**In plain words.** Before sending, Connect works out how sure it is and what a mistake would cost. Saying "closed" when the business is open is the worst mistake, so that needs strong evidence. If Connect isn't sure enough, it stays quiet, or asks the owner instead of claiming something.

**How:**
1. **Calibrated probabilities** for each candidate set of hours: when it says 80%, it's right about 80% of the time. The calibration is fitted on dev with folds grouped by business.
2. **The best action by expected points.** For every option (suggest X, stay quiet, flag), Connect computes the average points using the scorer's exact table, **FLAG included**, and picks the best.
3. **A safety margin λ on open/closed flips.** It's tuned by business-grouped cross-validation to maximise the score while keeping harmful ≤ the baseline. We report a bootstrap confidence interval for harmful at test size, an honest number rather than a "guarantee".
4. **Hard rules in code,** each unit-tested:
   - never contradict the owner's own entries (99% accurate);
   - never message an owner who opted out;
   - never write past dates;
   - every hours string must pass the validator;
   - a business closed for good goes to a person.

### A9. Yext writing rules
From [Yext's API spec](https://github.com/yext/openapi/blob/main/yaml/knowledgeapi.yaml):
- **Send the complete list.** `holidayHours` must be sent in full; any date left out is deleted.
- **Ordered, no duplicates.**
- **One kind of value per date:** exactly one of `isClosed`, `isRegularHours` or `openIntervals`.
- **Read, change, write.** We always read the whole `hours` object, change it, and write it back.
- **Past dates.** The spec says past dates are rejected, but dropping them from the list may delete history. **Test this in the sandbox on day 1** and follow what it does. The mock doesn't check this.

### A13. The whole profile (what the data supports)

| Profile item | Data | What Connect does | Status |
|---|---|---|---|
| Holiday hours | Everything | The season card | **Build** (scored) |
| Regular weekly hours that drifted | Regular-hours history, 523 user edits to regular hours, reviews; labels in `dev_regular_hours_truth.csv` (22 dev businesses outdated) | "Your Saturday hours look like 10–16 now. Update?" (SE-D0016) | **Build, measured** with precision/recall on those labels |
| Reply to closed-door reviews | 60k reviews with owner replies | A drafted apology plus the correct hours, approved in the same thread | **Build, measured** with a 5-person clarity test |
| Closed for good | Closure reviews, "closed" user edits, collapsing analytics | Sent to a human queue | **Build** (A5) |
| Holiday post | The approved hours | "Closed 24–26.12. Merry Christmas!" with one tap; Yext Posts API if the sandbox allows, else the mock | **Build** (cheap) |
| Description | Only the sandbox entity has one | Flags when it contradicts the facts, with a suggested fix | Stretch |
| Photos | No data | Design only, with the `Suggestion` interface shown | Roadmap |
| Phone, website, address | Fake values, no signals | — | Not built; stated honestly |

---

## Part B — Innovation: a step ahead of strong teams

**The bar.** Assume every strong team ships:
- copy last year, then the shopping centre, then peers, plus a gradient-boosted model;
- a confidence threshold;
- LLM-written messages in four languages;
- WhatsApp or email buttons and a no-login page;
- the Yext write and a publisher list;
- a standing-permission toggle;
- a dashboard.

None of that is our innovation.

**Our thread.** Others treat each date, and each season, separately. We treat a business's habits as one connected picture across days and across years. The real enemy is the 53% who never reply, so the best help reaches owners who don't answer.

### ★ B1 (SPOTLIGHT). Season Autopilot: one card this year, nothing to do next year
**In plain words:**
- Each owner gets **one message for the whole season**, already filled in from their own habits.
- It says **what customers will see** if they do nothing, and **the date to answer by** so every app shows it in time ("Answer by 18 Dec, so AI assistants show you closed on 25 Dec").
- There are **two buttons of equal size**: **"Approve"** (this season only) and **"Approve + every year"**. The second gives Connect permission for next time, so next year the owner does nothing.
  - Both are honest choices. Permission is never hidden inside the main button.
  - One sentence on the card states exactly what "every year" covers: the same holidays with the same hours you approved. Anything new or different is still asked.
- **The permission is always under the owner's control:**
  - Next year Connect applies the agreed hours **at least 14 days ahead**, then informs the owner with a one-tap undo.
  - An undo or "stop" ends the permission, and so does opting out of messages.
  - An **edit doesn't end it**: it updates the agreed hours. Owners who engage aren't punished.
- If one day is genuinely uncertain for **this** business, the card asks **one** question about it. The algorithm picks the day; we don't script it.
- If Connect would change a day from open to closed (or back) based on anything other than the owner's own past, that day needs its own tick. One "yes" never quietly flips a day.

**Why it's needed:**
- 53% of owners never reply. Fewer taps don't fix that; one reply covering this year *and* next does.
- It's Fonecta's own slide 4 goal: "Passive customers get value too."
- The evidence supports offering permission right away: owners accepted 95% of suggestions based on their own history.

**How it works (simply):**
1. **The card is filled by the score engine (B2),** which marks each date as "your own history", "linked holidays", "shopping centre" or "similar businesses".
2. **Deadlines** come from each publisher's update speed in `listings.json`: Google 2 minutes, Apple 2 days, AI assistants 7 days.
3. **Picking the one question:**
   - **The habit-type model (B2) updates the probabilities.** It gives updated odds for every other day when one day is answered. The answer can be one of three: closed, regular hours, or special hours.
   - **Each possible question is valued through the whole pipeline.** For each candidate day and each possible answer:
     1. update the habit-type probabilities;
     2. feed them into the LightGBM model;
     3. recompute the expected points for every other day, weighted by how many people look the business up.

     This way it never asks about a day the business's own history already settles.
   - **The result:** Connect asks about the day whose answer gains the most expected points, or asks nothing if no question is worth it.
   - **Speed:** the answer updates that date on the card immediately. Measured: the gain comes from choosing the date, not from updating other dates.
4. **The permission is a scoped contract,** and every grant, use, edit and undo is logged.
   - **The model must agree** (measured). Next season, a learned fact is applied automatically only if this year's model gives it P ≥ 0.5. Otherwise that date goes on the card as a question. This cuts wrong open/closed automatic changes from 10.1% to 3.5%.
   - **Dates less than 14 days away when permission already exists** (e.g. FI-D0012's dates from 8 Nov): apply now, inform with a one-tap undo, and log that the 14-day window was shortened. Covered by a unit test.
   - **Button text fits WhatsApp's 20-character limit in all four languages:** "Approve + every year" (20), "Hyväksy + joka vuosi" (20), "Godkänn + varje år" (18), "Godkjenn + hvert år" (19). A test checks every button label.

**How we prove it** (honest numbers; the simulator can't prove everything):
- **The simulator's limit, stated plainly.** Fonecta's owner simulator grants permission at a fixed 12% of acceptances, whatever button the owner sees. So it can't show whether our button design raises the grant rate. We say so.
- **What we can measure honestly.** A "coverage curve": for grant rates of 8% (the 2025 pilot), 12% (the simulator), 30% and 50% (hypotheses), we show:
  - how many of the 2026 at-risk lookups get covered with **zero** owner replies;
  - **how often those automatic changes would be wrong** (checked against the dev labels).

  The uplift from the button design is a hypothesis for a production A/B test in Connect.
- **Question value.** Oracle-owner tests (answers taken from the true labels) measure how much the one question improves the card's expected points, against no question.
- **Disclosure.** The simulator's hidden owner profiles (`sim_data.json`) are used only through its API, never read directly.

**Why other teams won't have it:**
- They'll build a permission *toggle*. We earn permission at the moment of highest trust.
- They'll list dates. We tie each date to each app's real update deadline.
- They'll ask about every date, or none. We ask only where an answer moves the most expected lost points.

**Fallback.** A pre-filled card with "approve all", without the question.

### B2. The score engine: learns each business's habits
**In plain words.** It guesses each business's hours by learning from what all 3,000 businesses entered in past years, and how holidays are linked. New businesses borrow from similar ones nearby.

**How:**
- **Two simple models that work together:**
  1. **A "habit type" model.** A small mixture model, fitted with EM (the standard algorithm for this), groups businesses into a handful of habit types, e.g. "closed on all church holidays, short on eves".
     - **It's fitted on the unbiased dev truth,** not on owner entries.
     - **The number of types is chosen** by held-out likelihood, on folds grouped by business.
     - **At prediction time,** a business's own entries count as partial evidence. The model gives a probability for each type, which fills in unknown days and updates instantly when the owner answers (B1).
     - **It matches the data:** the dataset was generated with habits linked across holidays.
  2. **A LightGBM model,** trained on the **unbiased** dev truth (58,711 rows, 4 seasons, folds grouped by business). It decides closed / regular / special. Features:
     - the business's own entries for this holiday, one and two seasons back;
     - its linked holidays;
     - the habit-type probabilities;
     - peer statistics we compute ourselves from entries before the snapshot (this fixes a leak in the provided peer file, which mixes 2023–2025);
     - the shopping centre;
     - weekday and regular hours;
     - B3 evidence;
     - events and school breaks.
- **A ranker** picks the exact hours among the candidates. Candidates:
  - closed;
  - the shown hours;
  - the regular hours;
  - this holiday one and two seasons back;
  - linked holidays' hours;
  - the shopping centre's hours;
  - the peer median;
  - user-edit hours;
  - event-extended hours.

  It's trained on entries, assuming entries are unbiased once we know the day is special. We check that assumption on dev special days that have no entry.
- **Then A4 decides.**
- **Honest back-test:** for 2025, only data before 6 Nov 2025, with no pooled peer file, no pilot decisions from after the snapshot, and the regular-hours version as of the snapshot.

**Why ahead.** It fixes the label bias and the leaks most teams won't notice, and it uses the 16% + 36% of needed changes that "copy last year" misses.

**Proof:** dev score with a CI against 54.5; the 2025 back-test; an ablation table.

### B3. Proof from reviews and Google edits
**In plain words.** Connect reads reviews like "We came on New Year's Day and it was closed" and Google user edits. It uses them as proof, and quotes them when the owner asks "why?".

**How:**
- **Regex first;** Opus 5.5 with a JSON schema for unclear reviews (which holiday, what state, closed for good).
- **User edits** are weighted by each publisher's measured accuracy.
- **Answering "why?"** reads the stored evidence record only, so nothing is invented.
- **Measured** automatically against `dev_truth_history.csv`: extractor precision and recall.

### B4. Reach owners in time
**In plain words.** Connect contacts the highest-stakes owners first, in their usual hours, early enough for every app to update, with one reminder.

**How:**
- **A fixed, explainable rule:**
  - approve-by date = day − 7 days (the slowest publishers);
  - send between 08 and 17, inside the owner's contact window;
  - one reminder at approve-by − 3 days.
- **Priority** = P(wrong) × expected lookups × weight for "shown open, actually closed".
- **The value line** in plain words: "About 140 people looked you up last Christmas Eve."
- **Measured in the simulator:** reply rate, and the share of at-risk lookups reached before the deadline.
- **A caveat we state.** The simulator treats every message as an independent coin flip, so a reminder's gain is mechanically 1 − (1 − p)². Real non-responders tend to stay silent, so the real gain is likely smaller.

### B5. Learns from every answer (the brief's "Learning")
**In plain words.**
- When an owner edits, Connect treats it as the truth for that day and its linked days, now and next year.
- When an owner declines a "similar businesses are closed" suggestion, Connect stops sending that kind to them.
- Permission is earned (B1).

**How:**
- **One mechanism.** Decisions become top-priority own-history evidence in B2. Declines switch off that type of evidence for that owner.
- **Learned facts age out and are superseded** (borrowed from Diadrome slowstruct's learning layer).
  - Every owner answer is stored as a *learned fact*: business, holiday, hours, season, source, `superseded_by`.
  - **A newer answer for the same holiday replaces the older one.** The older fact stays in the audit trail but stops counting.
  - **Older facts count less.** The weight by age is measured from the data, not guessed: habits repeat 94% after one season and 83% after two, so a two-season-old fact gets about 0.83/0.94 ≈ 0.88 of a fresh one's weight.
  - **Facts older than three seasons drop out** unless reconfirmed.
  - A standing permission always uses the newest fact for each holiday.
  - Tests: superseding, decay and age-out.
- **Measured exactly as the brief words it:** replay the 2025 pilot log as feedback, and compare the dev 2026 score **with vs without** that feedback.
  - **Removing a hidden overlap.** The 1,329 accepted and edited pilot decisions already appear in the entry history as `CONNECT_SUGGESTION` rows. So the "without" version removes those rows, otherwise the comparison measures nothing.
  - **The genuinely new signal is the 92 rejections** (about 25 on dev).
  - **Results are reported on the pilot-owner subset,** where feedback exists.

### B6. Reply in your own words
**In plain words.** Owners can tap, or write "we close at 2 on the 23rd" in any of four languages. Connect repeats it back before saving.

**How:** one Opus 5.5 call that returns structured output, then a validator, then the read-back. No agent.

### B7. The owner always wins
**In plain words.** If the owner changes their hours themselves, Connect never overwrites them. After publishing, Connect checks that the change really shows.

**How:**
- **Re-read the entity right before every write;** if that date changed, cancel.
- **Read back after the expected update time.**
- **Handle listings that aren't live** (opted out, or waiting on the publisher) honestly in the confirmation.

### B8. Emergency changes: "closed today" (your idea)
**In plain words.**
- If something unexpected happens (illness, a burst pipe, no staff), the owner writes "kiinni tänään" ("closed today") or taps "Closed today" in any Connect thread.
  - The fast channels update within the hour: Google in ~2 min; Facebook, Finder and Hitta in ~10 min; Caller in ~1 h. Those are the ones that matter for today.
  - Slower apps (Apple 2 days, AI assistants 7 days) won't catch a same-day closure, and Connect tells the owner that honestly.
- It ends by itself: tomorrow, normal hours are back.
- For "closed until further notice", Connect asks once a day: "Open again tomorrow?"
- Connect keeps track of these events. If a pattern appears (e.g. closed most Mondays in winter), it suggests updating the regular hours.

**How:**
- **Reuses B6** (parsing) and **A9** (writing). The owner started it, so it's applied immediately.
- **Short closures** become `holidayHours` for those dates; longer ones use `reopenDate`. Both expire on their own.
- **Safety:**
  - urgent changes only from the owner's registered phone or email, or a signed link; anything else needs a confirm step;
  - rate limits;
  - undo;
  - audit.
- **Privacy:** the reason ("I'm sick") is never stored or shown. Only "closed 12 Dec" is kept (Part F).
- **The confirmation is honest:** "Google ~2 min; Apple may take 2 days."
- **Stretch:** a fresh "closed today" review triggers a question to the owner, never a change.

### B9. Season radar for Fonecta
**In plain words.** One chart in the ops console:
- how many listings will show wrong hours on each day;
- how many owners have answered;
- who support should call first.

**How:** sums B4's priorities. One chart and a call list.

### B10 (STRETCH). Business memory graph and grounded chat (Graph RAG)
**Built only after the five steps, the score engine and learning are done.** It's the first stretch item, and the first cut if time runs out.

**In plain words.** The owner can ask Connect anything about their listing: "What did I do last Easter?", "Is the shopping centre open on the 26th?", "What do cafés nearby do on Boxing Day?". Connect answers only from facts it has, and says where each fact comes from.

**How:**
- **A small graph per business, in MongoDB** (two collections, `kg_nodes` and `kg_edges`).
  - **Nodes:** the business, special days, entries, learned facts (with their age and "replaced by" links), the shopping centre, its group of similar businesses (groups of ≥ 10 only), reviews and nearby events.
  - **Typed edges:** ENTERED, DECIDED, SUPERSEDES, IN_MALL, SIMILAR_TO, MENTIONS, LINKED_HOLIDAY (with its correlation weight), NEAR_EVENT.
- **Retrieval follows links,** not embeddings: 1–2 hops from the business and the asked date.
- **The answer:** the LLM (claude-opus-5) answers only from the retrieved facts, with a citation for each. A slot check confirms every date and time appears in the retrieved facts; otherwise the answer says "I don't know".
- **Same graph, other uses:** the ops console's card detail and the "why?" answers.

**Not used for predicting hours.** The scored predictions stay with the tabular models.

### Fonecta's six recommended approaches: the bar, and our step ahead

| Their approach | Strong teams | Our step ahead |
|---|---|---|
| Pattern learning | A gradient-boosted model on dev labels, or copy last year | Habit-type mixture + LightGBM trained on unbiased truth; leak-free peers; two seasons back and linked holidays (B2) |
| Rules + ML with confidence | A threshold | Expected points including FLAG, a tuned flip margin, a bootstrap CI; ask only about genuinely split days (A4, B1) |
| Visibility-aware priority | Sort by views | Priority by closed-door stakes, plus **per-publisher deadlines** (B1, B4) |
| NLP / LLMs | An LLM reads reviews and writes messages | Reviews become measured evidence; templates hold every fact; GPT-6 Astra judges message clarity offline; a grounded "why?" (A7, B3, B6) |
| Learning from feedback | Remember the last edit; a toggle | Decisions become evidence; declines switch off a type of evidence; permission earned at first contact; measured as "with vs without feedback" (B1, B5) |
| Guardrails | An approve button and a log | Hard rules as unit-tested code; owner-wins re-read; signed single-use links; one evidence record per suggestion (A4, A10, B7) |

### Parked (tested or judged not worth it)
- **A blanket holiday rulebook:** 65% exact, against 94% for same-holiday memory.
- **Conformal "guarantees":** the 2025 and 2026 seasons aren't comparable enough for the guarantee to hold, so we report a bootstrap CI instead.
- **Thompson sampling for send times:** there's no traffic to learn from in two days.
- **An LLM agent in a state machine, and three message checkers:** more parts, same safety.
- **Yext webhooks:** can't reach a laptop without a tunnel; we re-read before writing instead.
- **Weather and event monitoring:** the forecast reaches 10 days, December is weeks away, and events are niche.
- **React:** HTMX is faster to build and still a real web app.

---

## Part C — How we build it

### Architecture
```
 dataset ─► loaders ─► features
 DETECT   what-shows-now (A1) · targets (A2) · evidence: reviews/user edits (B3) · drift + closed-for-good (A13, A5)
 SUGGEST  habit-type mixture + LightGBM state + ranker (B2) ─► expected points + guardrails (A4) ─► Suggestion{type, value, evidence, prob}
 CONTACT  priority + deadlines (B4) ─► season card + one question (B1) ─► templates (+ Opus wording) ─► WhatsApp / email / SMS / push-sim
 DECIDE   signed links + live card page · WhatsApp/SMS webhooks · reply parser (B6) · owner simulator
 APPLY    Yext writer (A9) + owner-wins re-read (B7) ─► confirmation with per-publisher times ─► read-back ─► extras (post, review reply)
 LEARN    decisions → evidence; permissions (B1, B5)            AUDIT  one evidence record per suggestion → ops console, "why?", audit log
```

**Web app** (Flask + Jinja + HTMX + Tailwind; SQLite):
- **Ops console:** queue by stakes, evidence cards, flag queue, season radar (B9), audit, Export CSV.
- **Owner page** (no login): the live season card. Signed single-use link; nothing is approved on page load.
- **Demo view:** a phone mock next to the live sandbox entity.
- **Results page:** score with CI against the baseline, ablations, and the B1/B4/B5 measurements.

**Repo** (`/Users/ayaanhussain/Desktop/quick`):
```
cs/        data.py hours.py(MIT, from dataset) display.py evidence.py habits.py model.py decide.py
           season.py planner.py compose.py parse.py channels/ yext.py learn.py emergency.py profile.py audit.py db.py live/
app/       Flask routes + Jinja/HTMX templates
scripts/   backtest.py train.py predict.py(→DB→predictions_2026.csv) replay.py demo.py
tests/     README.md  predictions_2026.csv  .env.example  dataset/ (unzipped, gitignored)
```

**Stack** (no GPL/AGPL):
- **Python and data:** Python 3.11, pandas, pyarrow, scikit-learn, LightGBM, Flask, Jinja2, HTMX (BSD), Tailwind (MIT), SQLite, Pillow (the card image).
- **LLM SDKs:** Anthropic and OpenAI.
- **Reused code:** the dataset's MIT code, notices kept.
- **Attribution:** "Connect Suggests simulator, Since AI ry 2026" (CC BY 4.0).

### Timeline (one person; adjust if more)

**Deadline: Sunday 8 Nov, 10:00, video included.** Parts A and B are built on Saturday. Code freeze is Sun 05:00. The video and deck are made 05:00–09:00, and we submit by 09:30.

| When | Phase | Builds | Exit check |
|---|---|---|---|
| Fri evening (~4 h) | **0. Setup** | Unzip the dataset, set up the repo, `.env`; run the baseline and scorer; sandbox GET/PUT test (incl. past-date behaviour); **make the sandbox entity match the demo business:** copy Kahvila Aurora's (FI-D0001) name, address and regular hours into `SinceAi04`, so what Detect shows and what Apply writes are the same business; submit the WhatsApp utility template | Baseline reproduces 54.5; the sandbox write and restore work; the sandbox shows Kahvila Aurora |
| Sat to noon (~5 h) | **1. The whole chain, baseline quality** (record a backup screen capture of the sandbox write, read-back and confirmation as soon as it works) | A1, A3 (v0 = baseline), A5–A6 rules, templates fi/sv/nb/en, email + WhatsApp, signed links, **the season card UI built now on baseline values** (so the video's centrepiece exists early), simulator, Yext writer (A9) + confirmation, audit | `demo.py FI-D0001` runs all five steps on the mock and the sandbox, and the card is shown |
| Sat afternoon–evening (~7 h) | **2. Score engine** | B2 (habit types, LightGBM on truth, ranker), leak-free back-test, A4 decision + λ + CI, A5 per business, B3 regex + Opus | Dev score with CI > 54.5, harmful ≤ 224; back-test reported |
| Sat night (~3 h) | **3. Spotlight logic** | B1 on the existing card: deadlines, two buttons + scope sentence, the one question valued through the full pipeline, explicit flips; B4 rule + reminder | Coverage curve + error rate of automatic changes; oracle-owner question value |
| Sun morning (~4 h) | **4. Learning, emergency, profile** | B5 replay measurement (overlap removed); B6 parser; B8 "closed today"; A13 drift + review reply; B7 re-read; FI-D0012 next-season auto-apply flow | Each has its test or number |
| Sun, before freeze (~4 h) | **5. Ship** | Bonuses (sandbox + `holidays` live sources), README, messages, deck, 5-person message test, GPT-6 Astra clarity scores, video (script below) | Part E checklist complete |
| Only if ahead of schedule | **Extras** | B9 season radar chart, holiday posts, description check, LinkedEvents live source, the "closed today" review signal | Never at the expense of the five steps or the score |

**If two people:** one builds the score engine (Phases 2–3 maths), the other builds the app, the messages and the sandbox flow (Phases 1, 3 UI, 4), and both meet at the replay numbers on Saturday night.

## Part D — Testing and evaluation

**Commands:**
- `pytest`
- `python scripts/backtest.py --season 2025`
- `python scripts/predict.py && python cs/evaluate.py --predictions predictions_2026.csv`
- `python scripts/replay.py` (B1/B5)
- `python scripts/demo.py --scenario all`

**What we report:**
- **Score:** score with bootstrap CI, exact precision and recall, harmful (with CI), search views protected, closed-door views prevented. Dev and the 2025 back-test, against the baseline.
- **No history:** dev score with history masked (A12).
- **Learning:** dev 2026 score with vs without the 2025 feedback (B5).
- **Spotlight:**
  - lookups covered;
  - owners covered with zero replies;
  - question value (B1).
- **Reaching owners:** reply rate and lookups reached before the deadline (B4).
- **Drift detector:** precision and recall on `dev_regular_hours_truth` (A13).
- **Review extractor:** precision and recall against the truth history (B3).
- **Messages:** a 5-person comprehension test, plus GPT-6 Astra clarity scores per language.

**Tests:**
- every A4 hard rule;
- A9 list handling (no deletions, past dates, ordering);
- owner-wins re-read;
- token expiry, reuse and tampering;
- link scanners never approve anything;
- the emergency path, including spoofed senders and auto-expiry;
- reasons are never stored;
- a leakage test for the back-test.

| Rule | How we comply |
|---|---|
| Owner has the last word | Nothing written without approval or scoped permission; explicit ticks for inferred flips; undo; owner wins conflicts |
| Contact preferences | Preferred channel; opt-outs skipped (in messaging only; the CSV is complete); contact windows |
| Audit trail | One evidence record per suggestion; every write logged |
| Yext backend | All writes via Yext Management API calls (mock for bulk, sandbox for demo) |
| Keys | `.env` gitignored; keys never on screen |
| Licences | No GPL/AGPL; licence table; CC BY 4.0 attribution |
| Honest back-test | Time-filtered features; leak-free peers; a leakage test |
| Synthetic data | Dataset, sandbox and open public sources only |

## Part E — Video, README, deliverables

**Video (2.5 min):**
1. **(0:00) The problem:** "Shown open, actually closed", and how many people look businesses up on holidays.
2. **(0:20) Kahvila Aurora's season card on WhatsApp:**
   - what customers will see if nothing changes;
   - "answer by 18 Dec so AI assistants show it";
   - the one question the algorithm picked for this café;
   - the answered date updates on the card after the tap.
3. **(0:55) The owner taps "Approve + every year":**
   - the scope sentence is shown;
   - written to the Yext sandbox;
   - confirmation with per-publisher times;
   - a review reply offered.
4. **(1:25) Next season, FI-D0012:**
   - applied automatically ≥ 14 days ahead;
   - the owner is informed, with a one-tap undo;
   - shown with the "better after feedback" number.
5. **(1:45) "Kiinni tänään":** closed today on Google within minutes, expires tonight, and the owner is told honestly which apps are slower.
6. **(2:00) Quick cuts:**
   - FI-D0011 (flag);
   - FI-D0013 (silence);
   - FI-D0017 (no history);
   - SE-D0016 (drift).
7. **(2:15) Numbers:**
   - score with CI against 54.5;
   - harmful, with CI;
   - the coverage curve with the error rate of automatic changes;
   - "better after feedback" on pilot owners.

**README data sources** (confirmed by ablations):

| Signal | How we use it | Where Connect would get it in production |
|---|---|---|
| Own exception history | Habit types, two seasons back, linked holidays | Connect's audit log of entered hours |
| What's published now | The what-shows-now engine | Yext Knowledge Graph Entities API |
| Peers and shopping centres | Priors; the shopping-centre reason | Connect's aggregated data; centres' published hours |
| Reviews and user edits | Evidence, "why?", flags, review replies | Yext Reviews and Publisher Suggestions |
| Past decisions, contacts, listings | Learning, permission, timing, deadlines | Connect's suggestion log, CRM, Yext Listings |

**Frozen numbers.** All reported numbers come from one tagged commit:
- `python cs/evaluate.py --predictions predictions_2026.csv --json` is run on that commit and its JSON is pasted into the README;
- the video's numbers slide is built from the same output;
- judges who re-run the scorer get exactly what we show.

**What didn't work:**
- analytics as a closure detector (AUC 0.65);
- blanket day-type rules (65% exact);
- registry matching (synthetic businesses);
- conformal guarantees across seasons;
- weather for December targets (10-day horizon).

**Checklist:**
- the README in their template (`docs/README_TEMPLATE.md` in the zip);
- messages as text (fi/sv/nb/en, plus the approve, edit and decline follow-ups);
- the no-history case;
- the deck (problem, solution, how it learns, results, next steps);
- the licence table and CC BY 4.0 attribution;
- `predictions_2026.csv`;
- the live-source notes (coverage, quality, gaps, impact);
- the video.

## Part F — EU rules and Fonecta/Yext fit

**GDPR:**
- **Lawful basis:** contract (delivering the service) for owner contacts and replies; legitimate interest for public reviews.
- **Minimisation:**
  - we keep structured outcomes, not raw chat text;
  - raw messages are deleted after 30 days;
  - review author names aren't stored.
- **Special-category data:** reasons like illness are never stored or shown.
- **Aggregation:** peer statistics only from groups of 10 or more.

**AI Act Art. 50:** the first message says it's Connect's automated assistant.

**LLM calls:**
- EU data terms, with no training on our data;
- no names, phone numbers or emails in prompts;
- everything also works without the LLM.

**Messaging:** service messages under contract, not marketing; opt-outs honoured; Traficom's SMS rules followed.

**Security:**
- signed, single-use links;
- verified senders for urgent changes;
- secrets in `.env`;
- role-based ops console;
- EU hosting in production.

**Fonecta fit:**
- Yext stays the system of record. We use only Yext Management API calls (entities, `holidayHours`, `reopenDate`, reviews, posts, listings), the same backend Connect's UI uses.
- In production it runs as a sidecar service. Every dataset file maps to a production source, so going live is configuration.
- Built for Fonecta Connect (FI), Hitta Connect (SE) and the Norway pilot.

## Risks
- **The score engine misses its target.** Mitigation: the baseline remains the v0; ablations show what works.
- **WhatsApp template approval is slow.** Mitigation: email live, plus the phone mock.
- **Finnish SMS blocking.** Mitigation: a non-Finnish team phone only.
- **The sandbox lacks Posts, Reviews or Listings.** Mitigation: mock fallbacks, stated in the README.
- **Time.** Mitigation: the whole chain by Saturday noon, a code freeze, and video time reserved.

## Judge review log
- **Round 1 (independent agent): 66/100.** Main problems:
  - too much scope;
  - the spotlight couldn't move the score and couldn't be tested;
  - biased labels;
  - back-test leaks;
  - misread scorer metrics.

  Every point is addressed in this version: scope cut; spotlight re-aimed at the silent 53% and made measurable; truth-trained state model; leak-free peers; FLAG in the decision; corrected publisher times (Apple 2 days, AI assistants 7 days).
- **Round 2: 78/100.** Remaining issues, all addressed in this version:
  - **Permission on the main button:** now two equal buttons, a scope sentence, auto-apply ≥ 14 days ahead, revoke on undo (not on edit).
  - **Circular replay:** replaced by a coverage curve over grant rates, plus the error rate of automatic changes.
  - **Habit-type model:** now fitted on truth, with the question valued through the full pipeline and three possible answers.
  - **The learning measurement:** the hidden overlap is removed.
  - **B8's "every channel at once":** corrected.
  - **Saturday load:** the card UI now comes in Phase 1, extras are moved after the core.
  - **The FI-D0012 cut** is added to the video.
- **Round 3 (final): 84/100.** No rule violations or factual errors left. The last three improvements are applied:
  - the sandbox entity matches the demo business;
  - a rule and test for dates less than 14 days away, plus a button-length check;
  - all numbers frozen from one commit.

  The remaining gap is structural, and we accept it: the spotlight improves owner coverage, not the CSV score, and the build is heavy for one person.

## Open items
- **Sandbox demo identity:** done in Phase 0. The sandbox shows Kahvila Aurora. The category is still Fonecta `K1650` (barbershop); ask Tiia for the café code.
- **Ask Tiia Halme (sandbox):**
  - past-date behaviour in `holidayHours`;
  - Posts, Reviews and Listings in the sandbox.
- **The exact submission deadline** (the Since AI app).

## Fonecta's guidance on the Yext data (7 Nov)
1. **Knowledge Graph fields are fixed.** We can change values (hours, holidayHours, description …) but can't add fields. So no `c_` custom fields; our audit, permissions and learned facts live in MongoDB.
2. **Publishers choose which fields they show,** but the core fields are supported almost everywhere, so we don't need to handle that.
3. **Listings are fixed, and adding publishers is out of scope.** For the confirmation step, use `listings.json` from the mock dataset: each business's publishers and how fast each updates. For the sandbox café, that's FI-D0001's listings.
