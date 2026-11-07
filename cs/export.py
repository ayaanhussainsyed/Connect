"""Build -> guardrails -> MongoDB -> CSV -> official score.

    from cs import export
    res = export.run(season=2026, engine="baseline")      # also: scripts/predict.py

- ``build``: one guarded Suggestion per target row.
- ``write_csv``: the scorer's format (entity_id, date, action, hours, confidence) with **every**
  SUGGEST/FLAG row, whatever the opt-outs (``message_ok`` only matters for messaging).
- ``save_to_mongo``: bulk upsert into ``suggestions`` by the stable id
  ``sug:<model_version>:HOLIDAY_HOURS:<entity_id>:<date>`` (one id per entity_id+date+model_version).
- ``score_csv``: runs the official ``evaluation/evaluate.py --json`` on the dev labels.
- ``points``: the scorer's per-row points (same table as evaluate.py), for the UI.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from pymongo import ReplaceOne
from pymongo.database import Database

from cs import data, guardrails
from cs.config import settings
from cs.domain import Suggestion
from cs.hours import str_to_ivs
from cs.suggest import get_suggestions

CSV_COLUMNS = ("entity_id", "date", "action", "hours", "confidence")
LABEL_FILES = {2026: ("labels", "dev_labels_2026.csv"), 2025: ("labels", "dev_labels_2025_backtest.csv")}


def default_csv_path(engine: str, season: int = 2026) -> Path:
    return settings.root / "outputs" / f"predictions_{engine}_{season}.csv"


def build(season: int = 2026, engine: str = "baseline", today: date | str | None = None,
          entity_ids: list[str] | set[str] | None = None) -> list[Suggestion]:
    raw = get_suggestions(season, engine, entity_ids)
    ctx = guardrails.make_context(data.SNAPSHOT[season], today)
    return [guardrails.apply_guardrails(s, ctx) for s in raw]


def write_csv(suggestions: list[Suggestion], path: str | Path) -> int:
    """Every SUGGEST/FLAG row; returns the number of rows written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for s in suggestions:
            if s.action == "NO_ACTION":
                continue
            k = (s.entity_id, s.date or "")
            if k in seen:
                raise ValueError(f"duplicate prediction row {k}")
            seen.add(k)
            conf = "" if s.probability is None else f"{s.probability:.4f}"
            w.writerow([s.entity_id, s.date, s.action, s.value if s.action == "SUGGEST" else "", conf])
            n += 1
    return n


def _content_hash(doc: dict) -> str:
    from cs.audit import canonical_json
    body = {k: v for k, v in doc.items() if k not in ("created_at", "content_hash")}
    return hashlib.sha1(canonical_json(body).encode("utf-8")).hexdigest()[:20]


def save_to_mongo(suggestions: list[Suggestion], db: Database | None = None, batch: int = 2000,
                  skip_unchanged: bool = True, workers: int = 4) -> dict:
    """Bulk upsert by id (= entity_id + date + model_version), unordered, in parallel batches.

    Each document carries ``content_hash`` (everything except ``created_at``); with
    ``skip_unchanged`` only rows whose content changed since the last save are written.
    """
    if db is None:
        from cs.db import get_db
        db = get_db()
    coll = db["suggestions"]
    docs = []
    for s in suggestions:
        d = s.to_doc()
        d["content_hash"] = _content_hash(d)
        docs.append(d)
    if skip_unchanged and docs:
        versions = sorted({d["model_version"] for d in docs})
        have = {x["id"]: x.get("content_hash") for x in
                coll.find({"model_version": {"$in": versions}}, {"_id": 0, "id": 1, "content_hash": 1})}
        todo = [d for d in docs if have.get(d["id"]) != d["content_hash"]]
    else:
        todo = docs

    def write(chunk: list[dict]) -> tuple[int, int]:
        res = coll.bulk_write([ReplaceOne({"id": d["id"]}, d, upsert=True) for d in chunk], ordered=False)
        return res.upserted_count, res.modified_count

    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    upserted = modified = 0
    if chunks:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chunks)))) as pool:
            for u, m in pool.map(write, chunks):
                upserted += u
                modified += m
    return {"total": len(docs), "written": len(todo), "unchanged": len(docs) - len(todo),
            "upserted": upserted, "modified": modified}


def labels_path(season: int = 2026) -> Path:
    return data.dataset_path(*LABEL_FILES[season])


def score_csv(path: str | Path, season: int = 2026) -> dict:
    """Run the official scorer (read-only) and return its JSON. Raises on format errors."""
    scorer = data.dataset_path("evaluation", "evaluate.py")
    proc = subprocess.run([sys.executable, str(scorer), "--predictions", str(path),
                           "--labels", str(labels_path(season)), "--json"],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"evaluate.py failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# The scorer's per-row points (mirrors evaluation/evaluate.py; tested against it)
# ---------------------------------------------------------------------------
def _near(a: str, b: str, tol: int = 30) -> bool:
    ia, ib = str_to_ivs(a), str_to_ivs(b)
    if not ia or not ib:
        return not ia and not ib
    return abs(ia[0][0] - ib[0][0]) <= tol and abs(ia[-1][1] - ib[-1][1]) <= tol


def points(action: str, hours: str | None, label: dict) -> tuple[float, str]:
    """(points, outcome) for one row, given a dev label row (dict with expected_action,
    truth_hours, displayed_hours_at_snapshot)."""
    h = (hours or "").strip().lower() or "closed"
    exp, truth, shown = label["expected_action"], label["truth_hours"], label["displayed_hours_at_snapshot"]
    if exp == "SUGGEST":
        if action == "SUGGEST":
            if h == truth:
                return 1.0, "exact"
            if (h == "closed") != (truth == "closed"):
                return -1.0, "harmful_wrong_state"
            if _near(h, truth):
                return 0.6, "near"
            return 0.2, "same_state_off"
        if action == "FLAG":
            return 0.05, "flag_on_change"
        return 0.0, "missed"
    if exp == "FLAG":
        if action == "FLAG":
            return 1.0, "flag_correct"
        if action == "SUGGEST":
            return (0.3 if h == "closed" else -1.0), "suggest_to_closed_business"
        return 0.0, "missed_closed_business"
    if action == "SUGGEST":
        if h == shown:
            return -0.1, "redundant_confirm"
        if (h == "closed") != (shown == "closed"):
            return -1.0, "harmful_wrong_state"
        return -0.5, "spam_wrong_hours"
    if action == "FLAG":
        return -0.1, "unneeded_flag"
    return 0.0, "correct_silence"


def score_in_process(suggestions: list[Suggestion], season: int = 2026) -> float:
    """Score with ``points`` (no subprocess); equals evaluate.py's score_0_100."""
    by_key = {(s.entity_id, s.date): s for s in suggestions}
    pts, mx = 0.0, 0
    for lab in data.labels(season).to_dict("records"):
        s = by_key.get((lab["entity_id"], lab["date"]))
        a, h = (s.action, s.value) if s else ("NO_ACTION", None)
        pts += points(a, h, lab)[0]
        mx += lab["expected_action"] in ("SUGGEST", "FLAG")
    return round(max(0.0, 100 * pts / mx), 2) if mx else 0.0


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------
@dataclass
class ExportResult:
    engine: str
    season: int
    model_versions: dict[str, int]
    rows: int
    actions: dict[str, int]
    downgraded: dict[str, int]
    not_messageable: int
    csv_path: str
    csv_rows: int
    saved: dict | None = None
    score: dict | None = None
    seconds: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def run(season: int = 2026, engine: str = "baseline", today: date | str | None = None,
        out: str | Path | None = None, save: bool = True, do_score: bool = True,
        db: Database | None = None, audit: bool = True,
        entity_ids: list[str] | set[str] | None = None) -> ExportResult:
    t0 = time.time()
    sugs = build(season, engine, today, entity_ids)
    t1 = time.time()
    path = Path(out) if out else default_csv_path(engine, season)
    n_csv = write_csv(sugs, path)
    res = ExportResult(
        engine=engine, season=season,
        model_versions=dict(Counter(s.model_version for s in sugs)),
        rows=len(sugs),
        actions=dict(Counter(s.action for s in sugs)),
        downgraded=dict(Counter(s.guardrail_notes[0].split(":")[0] for s in sugs if s.original_action)),
        not_messageable=sum(1 for s in sugs if s.action == "SUGGEST" and not s.message_ok),
        csv_path=str(path), csv_rows=n_csv,
        seconds={"build": round(t1 - t0, 2)},
    )
    if do_score and season in LABEL_FILES:
        t = time.time()
        res.score = score_csv(path, season)
        res.seconds["score"] = round(time.time() - t, 2)
    if save:
        t = time.time()
        res.saved = save_to_mongo(sugs, db=db)
        res.seconds["mongo"] = round(time.time() - t, 2)
        if audit:
            from cs import audit as audit_log
            audit_log.log("EXPORT", {k: v for k, v in res.as_dict().items() if k != "seconds"}, db=db)
    return res
