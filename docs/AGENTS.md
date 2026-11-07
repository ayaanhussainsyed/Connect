# Working agreement for the build agents

Three agents work in this repo at the same time:
- the **builder**, which builds the app;
- the **ML agent**, which builds the models and the notebook;
- the **Q&A agent**, which answers questions and is read-only.

The main session coordinates them.

## Rules for everyone
- **Read first:**
  - `docs/PLAN.md` (the final plan);
  - `docs/BUILD_PLAN_A.md`;
  - `docs/APP_DESIGN.md`;
  - `docs/SETUP_LOG.md`;
  - this file.
- **Python:** always `.venv/bin/python` / `.venv/bin/pytest`. Don't install packages without saying so in your report; everything planned is already installed (pandas, scikit-learn, flask, pymongo, certifi, openai, anthropic, jupyterlab, matplotlib, pillow, httpx, python-dotenv, pytest). **No LightGBM:** use scikit-learn's `HistGradientBoostingClassifier`.
- **Secrets:**
  - read them only through `cs/config.py` (python-dotenv);
  - never print, log, commit, or write a secret into a notebook output, a doc, a test or a fixture;
  - never `cat .env`.
- **Git:** don't commit. The user decides when to commit.
- **Dataset:** `dataset/FONECTA_CONNECT_SUGGESTS_2026/` is **read-only**. Don't read `mock_api/sim_data.json` directly; use the simulator only through the mock API.
- **MongoDB:** only the database named in `MONGODB_DB` (`connect_suggests`). Never touch other databases.
- **Yext sandbox:** only `cs/yext.py` writes to it, and any test that writes must restore the original state (`holidayHours: []`).
- **Honest evaluation:**
  - the dev labels are only for dev businesses;
  - back-tests use only data stamped before the snapshot;
  - never tune on what you report.
- **Stay inside your own files** (below). If you need a change in a file someone else owns, write it in your report.

## File ownership

| Owner | Files |
|---|---|
| **Builder** | `cs/__init__.py`, `cs/config.py`, `cs/hours.py`, `cs/data.py`, `cs/domain.py`, `cs/db.py`, `cs/audit.py`, `cs/display.py`, `cs/suggest/`, `cs/guardrails.py`, `cs/export.py`, `cs/yext.py`, `cs/confirm.py`, `cs/compose.py`, `cs/channels/`, `cs/links.py`, `cs/llm.py`, `cs/learn.py`, `cs/season.py`, `cs/planner.py`, `cs/emergency.py`, `app/`, `scripts/predict.py`, `scripts/demo.py`, `scripts/run_mock.py`, `tests/` (except `tests/engine/`) |
| **ML agent** | `notebooks/`, `cs/engine/`, `scripts/train.py`, `scripts/backtest.py`, `scripts/replay.py`, `scripts/eval_*.py`, `artifacts/models/`, `artifacts/predictions/`, `tests/engine/`, `docs/ML_FINDINGS.md`, `docs/RESULTS.md` |
| **Builder: Yext helper** (from 01:30) | `cs/yext.py`, `cs/confirm.py`, `scripts/run_mock.py`, `tests/test_yext_*.py`, `tests/test_confirm.py` |
| **Builder: message helper** (from 01:30) | `cs/compose.py`, `cs/deadlines.py`, `cs/llm.py`, `cs/channels/`, `tests/test_compose.py`, `tests/test_channels.py`, `tests/test_llm.py` |
| **Message helper, extra** (from ~03:00) | `cs/profile/` (drift and review-reply suggestions), `tests/test_profile.py` |
| **ML agent, extra** (from ~03:00) | `cs/live/` (bonus live sources), `tests/test_live.py`, `docs/README_DRAFT.md`, **`cs/kg/` and `tests/test_kg.py`** (B10 Graph RAG, from ~02:05) |
| **Q&A agent** | none (read-only) |

The main builder no longer owns the helper files above; it owns everything else in its row.

## The contract between the ML agent and the builder
The ML agent delivers `cs/engine/api.py` with:
```python
def predict(season: int = 2026) -> "pd.DataFrame":
    """One row per target row (evaluation/targets_<season>.csv). Columns:
    entity_id, date, day_key, action ('SUGGEST'|'NO_ACTION'|'FLAG'), hours (str or None),
    probability (float, calibrated P(hours exact)), basis (str: OWN_LAST_YEAR|OWN_2_SEASONS|LINKED_HOLIDAY|MALL|PEERS|REVIEW|USER_EDIT|MODEL),
    evidence (list of {source, detail, ref, weight}), candidates (list of {hours, prob, basis}),
    p_closed_for_good (float)."""

class HabitModel:            # the habit-type mixture
    def posterior(self, entity_id: str, known: dict[str, str]) -> "np.ndarray": ...
    # known: {day_key or date: 'closed'|'regular'|'special'}; returns P(type)
    def day_probs(self, entity_id: str, day: str, known: dict[str, str]) -> dict[str, float]: ...
    # returns {'closed': p, 'regular': p, 'special': p}

def question_value(entity_id: str, season: int, known: dict[str, str]) -> list[tuple[str, float]]: ...
# candidate dates for B1's one question, sorted by expected points gained (0 means don't ask)
```
- **Saved files** the builder can read without importing heavy code:
  - `artifacts/predictions/engine_<season>.parquet` (the same columns as `predict`);
  - `artifacts/models/*.joblib`.
- **Until the engine exists,** the builder uses Fonecta's baseline (`cs/suggest/baseline.py`) behind the same interface (`ENGINE=baseline|ml`).
- **"What Google shows now" (A1):** `cs/display.py` is built first by the builder. Until then, the ML agent may use the dataset's `examples/baseline.py` `Baseline(season).displayed()` for the same logic.
