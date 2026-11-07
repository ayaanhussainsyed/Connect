# Phase 0 — setup log (7 Nov 2026)

## Environment
- Repo: `~/Desktop/quick` (git initialised, nothing committed yet).
- Python 3.11.1 in `.venv`. Pinned versions are in `requirements.lock.txt`.
- **LightGBM was dropped:** it needs the system library `libomp`, which isn't installed. We use scikit-learn's `HistGradientBoostingClassifier` instead (same gradient-boosted-trees family, BSD, no system dependency).
- The dataset is unzipped to `dataset/FONECTA_CONNECT_SUGGESTS_2026/` (gitignored). Checksums match `MANIFEST.json`.
- Secrets are in `.env` (gitignored, mode 600). `.env.example` is committed instead.

## Baseline reproduced
| Run | Score | Exact precision | Recall | Harmful |
|---|---|---|---|---|
| dev 2026 | **54.49** | 0.901 | 0.590 | 224 |
| dev 2025 back-test | **53.23** | 0.899 | 0.578 | 249 |

## Yext sandbox (`SinceAi04`): what we learned
| Test | Result | What it means for our writer |
|---|---|---|
| Write one future `holidayHours` date | 200; read back correctly; weekday hours unchanged | The write path works |
| Include a past date | 200, but the past date is **silently dropped** | Past dates aren't rejected, just ignored; we still drop them ourselves |
| Send only `{"hours": {"holidayHours": [...]}}` | Weekday hours **kept** | `hours` sub-fields merge, so we send only `holidayHours` |
| Send `hours` without the `holidayHours` key | The existing holiday list is **kept** | Leaving the key out means "unchanged" |
| Send `holidayHours: []` | The list is cleared | To delete everything, send an empty list explicitly |
| Send a list with dates A and B | The stored list becomes exactly A and B | A sent list replaces the old one; always send the full list of future dates |
| Listings / publisher suggestions / reviews / posts | 200, but **empty** | The confirmation uses the dataset listings for FI-D0001; posts get a test write later |
| Knowledge Graph Suggestions API | **403** (no access) | No native approval queue; we keep our own audit trail |
| Analytics | **403** | Mock only |

## Sandbox identity (decided in the plan)
- The original entity is backed up to `artifacts/sandbox_entity_backup_original.json` ("Sharp Scissors Barbershop", Jyväskylä).
- It now shows **Kahvila Aurora**, Myllykatu 17, 00830 Helsinki, with FI-D0001's regular hours (Mon–Fri 07:30–18, Sat 09–18, Sun 10–16), no holiday hours, and a café description.
- The category is still Fonecta `K1650` (barbershop). We don't know the café category code yet; ask Tiia.

## Still to do by the user
- **WhatsApp Cloud API:**
  1. Create a Meta developer app and add the WhatsApp product.
  2. Use the free test number and add each demo phone as a recipient.
  3. Create a **utility** template with three quick-reply buttons.
  4. Put `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env`.
- **LLM keys:** put `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env`. Everything also runs without them.
- **Email:** an `SMTP_URL` (e.g. a Gmail app password) in `.env`. Without it, emails go to the on-screen outbox.
- **Deadline:** confirm the exact submission deadline in the Since AI app.

## Fonecta's guidance on the Yext data (7 Nov)
1. **Knowledge Graph fields are fixed.** We can change values (hours, holidayHours, description …) but can't add fields. So no `c_` custom fields; our audit, permissions and learned facts live in MongoDB.
2. **Publishers choose which fields they show,** but the core fields are supported almost everywhere, so we don't need to handle that.
3. **Listings are fixed, and adding publishers is out of scope.** For the confirmation step, use `listings.json` from the mock dataset: each business's publishers and how fast each updates. For the sandbox café, that's FI-D0001's listings.

## Connections tested (7 Nov, ~00:40)
| Service | Status | Notes |
|---|---|---|
| MongoDB Atlas | OK | ping, write, delete; database `connect_suggests` (only this database is touched) |
| GPT-6 Astra (Azure OpenAI v1 Responses API) | OK | `OpenAI(base_url=…/openai/v1/, api_key=…)`, `responses.create(model="gpt-6-astra")` |
| Claude Opus 5 (Microsoft Foundry) | OK | `anthropic.AnthropicFoundry(api_key=…, base_url=…/anthropic)`; the main language model |
| Claude Sonnet 4.6 (Foundry) | OK | the fast model (`CLAUDE_FAST_MODEL`) |
| Claude Haiku 4.5 | not ready | Azure deployment still provisioning |
| Claude Sonnet 5 | not available | marketplace subscription missing |

## More sandbox behaviour (Yext helper, ~02:15)
| Test | Result |
|---|---|
| PUT `{"hours": {"saturday": {...}}}` | Only Saturday changes; other weekdays and holidayHours stay |
| PUT `{"hours": {"reopenDate": "2026-12-01"}}` | Accepted and updatable; weekdays and holidayHours kept |
| PUT `{"hours": {"reopenDate": null}}` | Clears reopenDate |
| `isRegularHours: true` holiday items | Accepted |
| **Mock vs sandbox** | The dataset mock rejects partial weekday PUTs and replaces the whole `hours` object; a reopenDate-only PUT empties holidayHours. `cs/yext.py` sends only the changed fields to the sandbox and the full `hours` object to the mock; `request["strategy"]` records which |
