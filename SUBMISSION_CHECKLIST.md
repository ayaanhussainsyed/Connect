# Submission checklist (for Ayaan to review)

Drafted by shlokmakharia-cc4 on Sun 8 Nov, about 08:00 Helsinki time. Every item was checked against `main` at `accd30e`.
This file is not committed, and no other file was edited. Delete it once it has been dealt with.

## Must do before submitting

1. **Push the final work.** The "Connect Suggests" rename and `deck/Connect_Suggests_pitch.pptx` exist only in
   Ayaan's working tree so far.
2. **Restore the `.env` lines in `.gitignore`.** `accd30e` removed these 4 lines:
   ```
   # secrets — never commit
   .env
   .env.*
   !.env.example
   ```
   `.env` itself is not in `main`'s tree or history (checked). But without these lines, the next `git add -A`
   commits every key. Synteny keeps `.env` out of its snapshots anyway, so restoring them doesn't affect key sharing.
3. **Delete the `team-env` branch on GitHub** (commit `45e9f89`, "Team settings (.env) for the build. Private;
   delete this branch before submission"). Its `.env` holds real values for `YEXT_SANDBOX_KEY`, `OPENAI_API_KEY`,
   `ANTHROPIC_FOUNDRY_API_KEY`, `SMTP_URL` (with the password), `MONGODB_URI` and `LINK_SECRET`. If judges get
   access to the repo, or it goes public, while the branch exists, they can read all of them. Deleting the branch
   does not erase the commit everywhere, so rotate the keys as well (item 9).
4. **Video: 3:00 at most.** The brief says "a 2–3 minute video". It must show all five steps (Detect, Suggest,
   Contact, Decide, Apply), plus the customer with no history and the learning. The brief also says: "Keep sandbox
   credentials out of public repositories and videos". So keep keys, `.env`, terminals and the ngrok dashboard
   off screen.
5. **Stop ngrok once recording is done.** While it runs, the app is reachable from the internet, and Meta's
   webhook points at it.
6. **One wording fix on `/how`** (`app/templates/ui/how.html`): "One smart question — For new customers, …".
   The +0.50 vs +0.09 was measured on 150 businesses that need a change, not only new customers
   (`scripts/eval_question.py`). Drop "For new customers", or say "(150 businesses)".
7. **Regenerate `docs/MESSAGES.md` after the rename** (`scripts/gen_messages.py`). The brief asks for "your final
   customer messages as text", so they should match what the app now sends ("Connect Suggests", no "Fonecta").
8. **Ask Fonecta for the exact deadline and how to submit** (do judges need repo access, an upload, or a link?).
   There is no Q&A desk on Sunday. Use the Fonecta channel on the Since AI Discord: Tiia Halme (`tiia_fonecta`)
   or Inka Gyllenbögel (`inka_76919`).

## After the hackathon

9. **Rotate every key:**
   - the Yext sandbox key
   - the OpenAI/Azure key
   - the Anthropic Foundry key
   - the SMTP password
   - the Mongo credentials
   - `LINK_SECRET`
   - the WhatsApp app secret and access token. They were pasted in chat and sent over the floor; the token is
     valid until 6 Jan 2027.

   Then put the `.env` lines back in `.gitignore` for good.

## Already OK (verified)

- **README vs Fonecta's template** (`dataset/.../docs/README_TEMPLATE.md`): every section is covered, in
  README §9 and §13. That covers run it, the five steps, the 3–5 data sources that mattered, what didn't work,
  the customer with no history, final messages as text, the live-source bonus, and third-party licences.
- **Licences:** none of the 51 installed Python packages is GPL/AGPL (scanned from package metadata). HTMX
  (unpkg) and the Google Fonts (Inter, Lora, JetBrains Mono, all OFL) are listed in README §13.
- **Numbers vs the official `evaluate.py`** (re-run on the frozen files), all exact:

  | | Score | Harmful | Precision | Recall | Protected | Closed-door |
  |---|---|---|---|---|---|---|
  | Engine, dev 2026 (`predictions_2026.csv`) | 61.67 | 219 | 0.859 | 0.670 | 65.0% | 72.9% |
  | Baseline, dev 2026 | 54.49 | 224 | 0.901 | 0.590 | 57.2% | 69.2% |
  | Engine, 2025 back-test | 55.42 | 211 | 0.866 | 0.597 | 56.3% | 66.2% |
  | Baseline, 2025 back-test | 53.23 | 249 | 0.899 | 0.578 | 55.1% | 67.7% |

  The other headline figures also check out:
  - no history: 36.8 vs 14.2 (16 businesses)
  - autopilot: 10.1% → 3.5% wrong open/closed
  - drift: precision 1.00, recall 0.55, 12 of 12 weeks exact
  - one question: +0.50 vs +0.09
  - Detect: 100% match on 22,218 label rows (covered by a test)

  Not re-run (they need a full retrain): the CI 60.4–62.9 and the learning figures 58.3 → 59.6 and 79 → 72.
- **Secrets:** none in `main`'s tree or history. Scanned for token, key and password patterns.
- **Tests on `main`:** 570 pass. One is flaky: `tests/test_channels.py::test_outbox_write_and_read` fails when
  the outgoing and incoming messages get the same millisecond timestamp, and passes when re-run.

## Worth a look

- README §7 says "exact versions: requirements.lock.txt". On Shlok's machine (Python 3.11) that file would not
  install because of the `python-dateutil==4.0.0` pin, so the deps were installed from `requirements.txt`
  instead. A judge copying the README could hit the same error. Test it in a clean venv, or say "use
  requirements.txt".
- If the demo sends real WhatsApp messages to anyone other than +91 98311 22132 (a judge, say), first add their
  number to the allowed list on Meta's API-testing page.
