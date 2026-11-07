# App design: Connect Suggests

Who uses it, what they see, how it flows, what we build, and in what order.
Flask + Jinja + HTMX + Tailwind · SQLite · scikit-learn · Opus 5.5 / GPT-6 Astra · Yext sandbox.

---

## 1. Three kinds of user

| User | Where they meet the app | What they need |
|---|---|---|
| **Business owner** (a Connect customer, e.g. Kahvila Aurora) | WhatsApp / SMS / email message → a no-login season card page | Understand at once, answer in one tap, see where it shows, undo anytime |
| **Fonecta staff** (support or ops) | The ops console (`/ops`) | See what's being suggested and why; handle flagged businesses; see results; export the CSV |
| **Judges / video viewers** | The demo view (`/demo`) | Watch the five steps happen live: phone on the left, the real Yext sandbox entity on the right |

---

## 2. The owner's journey (all A and B features in order)

```
 (nightly)          (scheduled)            (owner, any time)                 (Connect)                     (later)
 1 DETECT ──► 2 SUGGEST ──► 3 CONTACT ──► 4 DECIDE ───────────────────► 5 APPLY + CONFIRM ──► LEARN ──► next season: AUTOPILOT
   A1 A2        A3 A4 A5 A6   A7 B4 B1     A8 B1 B6                          A9 B7 A10              A11 B5     B1 (apply ≥14 days ahead, inform, undo)
                B2 B3                                                                                                     
                                     ▲ emergency at any time: "kiinni tänään" → B8 → apply now → confirm → expires by itself
```

1. **Detect (A1, A2).** Every night, Connect works out what Google shows for each business and each upcoming special day.
2. **Suggest (A3–A6, B2, B3).**
   - The model predicts the real hours.
   - The decision rule keeps only suggestions worth sending.
   - Businesses closed for good go to staff; correct listings stay quiet.
   - Each suggestion carries its evidence: own history, shopping centre, peers, reviews.
3. **Build the season card (B1).**
   - All of a business's suggestions are bundled into one card.
   - Each date gets its "answer by" deadline, from how fast each app updates.
   - Connect decides whether one question is worth asking, and which.
   - Any date flipping between open and closed without the owner's own history behind it gets a separate tick.
4. **Contact (A7, B4).** The planner orders businesses by what's at stake and sends inside each owner's contact window, on their preferred channel and in their language. A reminder goes 3 days before the deadline if there's no answer.
5. **Decide (A8, B1, B6).** The owner taps a button, replies 1/2/3, or writes freely ("we close at 2 on the 23rd"), or opens the card page:
   - **Approve** (this season);
   - **Approve + every year** (standing permission, scope shown);
   - **Edit** a date;
   - **No thanks**.
6. **Apply and confirm (A9, B7, A10).**
   - The guardrails check, and the listing is re-read just before writing; if the owner changed it themselves, our change is cancelled.
   - Only the holiday list is written to Yext, then read back.
   - The confirmation lists each app and its update time, with an undo.
   - Every step goes to the audit log.
7. **Learn (A11, B5).** Answers become the strongest evidence for that date and similar dates. Declines switch off that kind of evidence for the owner.
8. **Next season (B1 autopilot).** For "every year" owners, the agreed hours are applied ≥ 14 days ahead, then a note with undo is sent.
9. **Emergency, any time (B8).**
   - The owner writes "kiinni tänään", or taps "Closed today". Connect applies it immediately, confirms (Google in ~2 min), and the change expires by itself.
   - For "closed until further notice", Connect asks once a day whether they're open again.
10. **Extras (A13).**
    - "Your Saturday hours look like they changed" (drift).
    - A drafted reply to a "door was closed" review.
    - Both are approved in the same thread.

**Silent owners.** Nothing is published without approval or permission. They get one reminder. For businesses that are flagged, staff decide.

---

## 3. Screens to design

### Owner side (no login anywhere)

**O1. The WhatsApp message** (the SMS version uses digits; email has the same text plus a link button):
```
Fonecta Connect (automaattinen avustaja)
Hei Kahvila Aurora! Joulun aukioloajat:
• ke 24.12. 7.30–13   (viime vuonna sama)
• to 25.12. kiinni    (viime vuonna sama)
• pe 26.12. 10–16 ?   ← yksi kysymys
Jos et tee mitään, Google näyttää tavalliset ajat.
Vastaa 18.12. mennessä, jotta myös tekoälyavustajat näyttävät oikein.
Viime jouluaattona ~140 ihmistä haki teitä.
[Hyväksy]  [Hyväksy + joka vuosi]  [Muokkaa]
```
In English, the message says:
- Hi Kahvila Aurora! Christmas opening hours:
  - Wed 24.12. 7:30–13 (same as last year)
  - Thu 25.12. closed (same as last year)
  - Fri 26.12. 10–16? ← one question
- If you do nothing, Google shows your regular hours.
- Answer by 18.12. so AI assistants show it correctly too.
- About 140 people looked you up last Christmas Eve.
- [Approve] [Approve + every year] [Edit]

**O2. The season card page** (`/c/<token>`): opening the link changes nothing.
```
┌ Kahvila Aurora · Joulu 2026 ───────────────────────── Vastaa 18.12. mennessä ┐
│ What customers see now        │ Our suggestion            │ Why                    │
│ Wed 24.12  7:30–18 (regular)  │ 7:30–13      [edit]       │ You entered this last year ▸ │
│ Thu 25.12  10–16 (regular)    │ closed ☐ tick to confirm  │ Similar cafés nearby ▸  │  ← open/closed flip without own history → separate tick
│ Fri 26.12  ?                  │ ( ) closed ( ) regular ( ) other hours  ← the one question │
├───────────────────────────────────────────────────────────────────────────┤
│ [ Approve ]   [ Approve + every year ]   [ No thanks ]                     │
│ "Every year" = the same holidays, same hours. Anything new is still asked. Undo anytime. │
└ [ Closed today? ] ────────────────────────────────────────────────────────────┘
```
- Answering the question refreshes the other dates (HTMX).
- The "Why ▸" links expand to show the evidence.

**O3. Confirmation** (message plus page):
```
✓ Saved. Google ~2 min · Facebook, Finder ~10 min · Fonecta Caller ~1 h · Bing ~1 day · Apple Maps ~2 days · AI assistants ~7 days
(Tripadvisor: not connected)                                   [ Undo ]
Also: reply to the 2 customers who found you closed last year?    [ View reply ]
```

**O4. Emergency:** "Closed today" button → "Saved: closed today 12.12. Normal hours return tomorrow. Google ~2 min. [Undo]"

**O5. Autopilot note** (next season): "We set your Christmas hours as agreed (24.12 7:30–13 …). [Undo] [Stop doing this]"

**O6. New customer** (FI-D0017): "Are you usually closed on public holidays? [Yes] [No] [It varies]"

### Fonecta staff side (`/ops`)

| Screen | What it shows |
|---|---|
| **S1. Queue** | Cards ordered by stakes (lookups at risk). Filters for country, status and channel. Each row shows the business, dates, status (scheduled / sent / answered / applied / no reply) and deadline. |
| **S2. Card detail** | The evidence for each date, model probabilities, the decision path, messages sent, the owner's answers, Yext writes, and the audit trail (with a "verify chain" check) |
| **S3. Flag queue** | Businesses likely closed for good, with the evidence, for a person to handle |
| **S4. Results** | Score with CI against the baseline, harmful count, ablations, the coverage curve, learning with vs without feedback, drift precision/recall. **Export CSV** button. |
| **S5. Season radar** | One chart (stretch goal): wrong listings expected per day, and how many have been answered |

### Demo side (`/demo`)

```
┌ Phone (outbox thread) ┐ ┌ Yext sandbox: SinceAi04 (live GET) ┐ ┌ Five steps ┐
│ WhatsApp-style chat   │ │ name, weekly hours, holidayHours   │ │ ● Detect   │
│ buttons are clickable │ │ refreshes after each write         │ │ ● Suggest  │
│                       │ │                                    │ │ ● Contact  │
│                       │ │                                    │ │ ● Decide   │
│                       │ │                                    │ │ ● Apply    │
└───────────────────────┘ └────────────────────────────────────┘ └────────────┘
```
Scenario picker: FI-D0001 Aurora · FI-D0011 flag · FI-D0012 autopilot · FI-D0013 quiet · FI-D0017 new · SE-D0016 drift · "Closed today".

---

## 4. What we design and build (components)

### 4.1 Data and domain (foundation)
- **Data loaders** with an `as_of` cutoff on everything historical, so there are no leaks.
- **Domain objects:** Suggestion, Evidence, Card, Message, Decision, WriteRecord, Permission.
- **SQLite tables:** suggestions, cards, messages, decisions, writes, permissions, audit_events.
- **States:**
  - **Card:** `DRAFT → SCHEDULED → SENT → (QUESTION_ANSWERED) → DECIDED → APPLIED → CONFIRMED`, with branches `NO_REPLY → REMINDED → EXPIRED` and `UNDONE`.
  - **Permission:** `NONE → GRANTED(scope) → USED … → REVOKED (undo / stop / opt-out)`. An edit updates the scope's values.

### 4.2 Machine learning (only A3 is a real model; the others reuse it)

| ID | Component | What it does | Trained on | Used by |
|---|---|---|---|---|
| **M0** | Baseline rules (Fonecta's) | Copy last year → shopping centre → peers | — | Version 0 of A3 (the fallback) |
| **M1** | **Habit-type model** (mixture, EM) | Groups businesses into habit types and fills in unknown days; updates instantly after an owner's answer | Dev true hours (`dev_truth_history.csv`) | A3 features, B1 question choice, A12 new customers |
| **M2** | **State model** (scikit-learn gradient-boosted trees) | Closed / regular hours / special hours for each business and date | Dev true hours, folds grouped by business | A3 |
| **M3** | **Hours ranker** + calibration | Picks the exact hours among the candidates (own past, linked holidays, shopping centre, peers, user edits) and gives honest probabilities | Owner entries + dev truth | A3, A4 |
| **M4** | **Decision rule** (tuned, not trained) | Expected points under the scorer's table; flip margin λ chosen by cross-validation | Dev (grouped CV) | A4, A6 |
| **M5** | Closed-for-good scorer | Combines closure reviews, "closed" user edits and an analytics collapse | Rules; checked on 7 dev examples | A5 |
| **M6** | Drift scorer | Spots weekly hours that changed (user edits to regular hours, reviews) | Rules / logistic regression; checked on 22 dev examples | A13 |

### 4.3 Language (LLM) parts: facts never come from an LLM

| ID | Part | Model | Safety net |
|---|---|---|---|
| L1 | Review reader: which holiday, closed or not, closed for good | Regex first, then Opus 5.5 (JSON) | Measured against the truth history |
| L2 | Free-text reply parser ("we close at 2 on the 23rd") | Opus 5.5 (JSON) | Hours validator plus a read-back before saving |
| L3 | Polishing the reason sentence and review-reply wording | Opus 5.5 | A slot check: every date and time must match the data, otherwise the template is used |
| L4 | Message clarity judge (offline, for results) | GPT-6 Astra | — |

### 4.4 Product parts

| Component | Feature | Notes |
|---|---|---|
| Display engine | A1 | Must match Fonecta's displayed hours 100% |
| Guardrails | A4, A10 | Pure functions, each unit-tested |
| Card builder | B1 | Bundles dates; deadlines from `listings.json`; question by value of information (M1→M2/M3→M4); flip ticks |
| Planner | B4 | Priority = P(wrong) × lookups × closed-door weight; send time in the contact window; one reminder |
| Composer | A7 | fi/sv/nb/en templates, local date formats, channel length limits, AI-assistant disclosure |
| Channels | A7, A8 | Outbox (always) · WhatsApp Cloud API · email (SMTP) · SMS stub |
| Links | A8 | Signed, expiring, single-use; GET never decides |
| Decision handler | A8, B1, A11 | Records decisions and permission grants |
| Yext writer and confirmation | A9, B7 | Owner-wins re-read; sends only `holidayHours`; read-back; per-app times |
| Learning | A11, B5 | Decisions become evidence; declines switch off a basis; **learned facts age out and are superseded** (newest answer wins, decay by season age, drop after 3 seasons); replay measurement |
| Emergency handler | B8 | Owner-started changes apply now; they expire by themselves; the reason is never stored |
| Profile modules | A13 | Drift (M6) and review reply (L1+L3) |
| Audit | A10 | A hash-chained log |
| Flask app | all | Owner routes, ops routes, demo route, webhooks, CSV export |
| Scripts | — | `train`, `predict` (→ DB → CSV), `backtest`, `replay`, `demo` |

---

## 5. Order of building (Saturday; freeze Sun 05:00, submit by 09:30)

**Principle.** The whole five-step chain works first, on the baseline. Then the model makes it smarter, then the B features build on top. A demo exists from step 6 onward.

| # | Build | Feature(s) | Depends on | Done when | ~Time |
|---|---|---|---|---|---|
| 1 | Foundation: config, loaders, hours utils, domain objects, DB, audit log | — | — | Tests pass | 1 h |
| 2 | Display engine | A1 | 1 | 100% match on dev | 0.5 h |
| 3 | Baseline M0 → Suggestions + guardrails + CSV export | A3 v0, A4 rules, A5/A6 v0 | 2 | Score 54.49 | 1 h |
| 4 | Yext writer (mock + sandbox) + confirmation | A9, B7 | 1 | Sandbox write + read-back | 1 h |
| 5 | Composer (4 languages) + outbox channel | A7 | 3 | Messages for 17 demo businesses | 1 h |
| 6 | Links + Flask card page + decision handler → **the full chain** | A8 | 3–5 | `demo.py FI-D0001` runs all 5 steps | 1.5 h |
| 7 | **M1 habit types** | B2 | 1 | Held-out likelihood picks the number of types | 1 h |
| 8 | **M2 state model + M3 ranker + calibration** | A3 v1, B2 | 7 | Dev score above 54.49 | 2.5 h |
| 9 | **M4 decision rule** (λ, FLAG, bootstrap CI) + **M5** closed for good | A4, A5, A6 | 8 | Harmful ≤ 224, with CI | 1 h |
| 10 | Card builder: deadlines, two buttons + permission, the one question, flip ticks | B1 | 6, 7–9 | Question value measured (oracle owner) | 2 h |
| 11 | Planner: priority, send time, reminder | B4 | 10 | Simulator reply rate reported | 0.5 h |
| 12 | Learning + autopilot next season + replay measurement | A11, B5, B1 | 6, 8 | With vs without feedback | 1 h |
| 13 | Free-text parser (L2) + emergency "closed today" | B6, B8 | 6 | Parser tests; the emergency demo | 1 h |
| 14 | Review reader (L1) + drift (M6) + review reply (L3) | B3, A13 | 8 | Drift precision/recall; reply demo | 1.5 h |
| 15 | No-history evaluation (FI-D0017 + masked dev) | A12 | 8 | Number in RESULTS | 0.5 h |
| 16 | Ops console + demo view + results page | all | 6–15 | All screens work | 1.5 h |
| 17 | Real WhatsApp (when credentials exist) + email | A7, A8 | 6 | A message arrives on a phone | 0.5–1 h |
| 18 | Bonus: live calendar adapter + sandbox notes | A14 | 2 | Agreement reported | 0.5 h |
| — | **Freeze (Sun 05:00):** frozen numbers, README, messages as text, deck, video (05:00–09:00), submit by 09:30 | A14 | all | Checklist done | — |

That's about 20 hours of building for one person; two people bring it to about 12 hours. **If two people:**
- **Person 1 (ML):** steps 7–9, 12 (replay), 14 (M6), 15.
- **Person 2 (app):** steps 4–6, 10 (UI), 11, 13, 16, 17.
- Steps 1–3 together first.

**Stretch (after step 18, if time allows):** B10, the business memory graph with grounded chat (Graph RAG over MongoDB `kg_nodes`/`kg_edges`), about 2–3 h.

**If time runs short, cut in this order:**
1. B9 season radar;
2. holiday posts and the description check;
3. the review reply;
4. the email channel;
5. the question in B1 (keep the card and the two buttons).

**Never cut** the five steps, the score engine, guardrails, the Yext write, learning, or the no-history case.
