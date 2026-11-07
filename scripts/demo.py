#!/usr/bin/env python3
"""The five steps end to end in the terminal: detect -> suggest -> card + message -> decide -> apply + confirm.

    .venv/bin/python scripts/demo.py FI-D0001 --target sandbox          # the real Yext sandbox (SinceAi04)
    .venv/bin/python scripts/demo.py FI-D0001 --target mock             # the dataset's mock (scripts/run_mock.py)
    .venv/bin/python scripts/demo.py FI-D0001 --target sandbox --keep   # leave the hours on the sandbox

Decide: the dataset's owner simulator (``/sim/v1/owner-response`` on the mock) answers for each date if
the mock is reachable; otherwise (or with ``--auto-approve``) every date is approved, flips ticked.
Clean-up: the sandbox is always restored with ``clear_holiday_hours`` unless ``--keep``; on the mock,
``/mock/reset`` undoes the writes unless ``--keep``.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from cs import audit, data, decide_flow, season  # noqa: E402
from cs.config import settings  # noqa: E402

SIM_TO_DECISION = {"ACCEPTED": "APPROVE", "REJECTED": "DECLINE"}


def _say(step: str, text: str = "") -> None:
    print(f"\n== {step} " + "=" * max(0, 60 - len(step)))
    if text:
        print(text)


def _mock_up(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def simulate(card, mock_url: str) -> tuple[dict, list[str], bool, list[str]]:
    """Ask the dataset's owner simulator about each date. Returns (per_date, ticks, every_year, log)."""
    per_date, ticks, every_year, lines = {}, [], False, []
    for i in card.items:
        r = httpx.post(f"{mock_url}/sim/v1/owner-response", timeout=10,
                       json={"entityId": card.entity_id, "date": i["date"], "proposedHours": i["value"],
                             "channel": card.channel})
        if r.status_code != 200:
            lines.append(f"  {i['date']}: simulator unavailable ({r.status_code}); approving")
            per_date[i["date"]], _ = "APPROVE", ticks.append(i["date"])
            continue
        body = r.json()
        res = body.get("response", body) if isinstance(body, dict) else {}
        dec = res.get("decision")
        lines.append(f"  {i['date']}: proposed {i['value']} -> {dec}"
                     + (f" ({res.get('final_hours')})" if dec == "EDITED" else ""))
        if dec == "EDITED" and res.get("final_hours"):
            per_date[i["date"]] = f"EDIT:{res['final_hours']}"
        elif dec in SIM_TO_DECISION:
            per_date[i["date"]] = SIM_TO_DECISION[dec]
            if dec == "ACCEPTED":
                ticks.append(i["date"])  # an explicit yes to exactly this value
        every_year = every_year or bool(res.get("standing_permission"))
    return per_date, ticks, every_year, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entity_id")
    ap.add_argument("--target", choices=("mock", "sandbox"), default=None)
    ap.add_argument("--engine", choices=("ml", "baseline"), default=None)
    ap.add_argument("--mock-url", default=settings.yext_base_url, help="the mock (simulator, and mock target)")
    ap.add_argument("--auto-approve", action="store_true", help="approve every date instead of asking the simulator")
    ap.add_argument("--every-year", action="store_true", help="also grant the standing permission")
    ap.add_argument("--no-question", action="store_true", help="skip the one question (faster)")
    ap.add_argument("--keep", action="store_true", help="don't restore the sandbox / reset the mock at the end")
    a = ap.parse_args(argv)

    eid = a.entity_id.upper()
    target = a.target or season.default_target(eid)
    from cs.suggest import engine_adapter
    engine = a.engine or ("ml" if engine_adapter.available(2026) else "baseline")
    mock_url = a.mock_url.rstrip("/")
    if target == "mock" or a.mock_url != settings.yext_base_url:
        from cs.yext import YextClient
        base = season.yext_client

        def client_for(t: str = "mock", db=None):
            return YextClient("mock", base_url=mock_url, db=db) if t == "mock" else base(t, db)
        season.yext_client = client_for
    ent = data.entity(eid)
    print(f"{eid} {ent.get('name')} · engine {engine} · Yext {target}")
    t0 = time.time()
    rc = 0
    try:
        # 1-3 -------------------------------------------------------------
        try:
            card = season.build_card(eid, 2026, engine, target=target, ask_question=not a.no_question)
        except season.NoCard as e:
            _say("1 DETECT · 2 SUGGEST", f"no card: {e}")
            audit.log("NO_CARD", {"reason": str(e), "engine": engine}, entity_id=eid)
            return 0
        _say("1 DETECT", "\n".join(f"  {i['date']} {i['day_key']:<16} Google shows {i['shown']}" for i in card.items)
             + (f"\n  {card.question['date']} {card.question.get('day_key') or '':<16} Google shows {card.question['shown']}"
                if card.question else ""))
        _say("2 SUGGEST", "\n".join(
            f"  {i['date']}  {i['shown']} -> {i['value']}  [{i['basis']}]"
            + ("  (open/closed flip: needs a tick)" if i["flip_tick_required"] else "")
            + (f"\n      why: {i['evidence'][0]['detail']}" if i["evidence"] else "") for i in card.items)
             + (f"\n  one question: {card.question['date']} (expected gain {card.question['value']:.2f})"
                if card.question else ""))
        msg, token = season.send_card(card)
        _say("3 CONTACT", f"  card {card.id} -> {card.status}; answer by {card.approve_by}; "
                          f"snapshot {card.expected_current}\n  {msg.channel} message ({msg.composer}, {len(msg.text)} chars):\n"
             + "\n".join("    | " + ln for ln in msg.text.splitlines())
             + f"\n  link: /c/{token}")
        # 4 ---------------------------------------------------------------
        if card.items and not a.auto_approve and _mock_up(mock_url):
            per_date, ticks, every_year, lines = simulate(card, mock_url)
            how = "the dataset's owner simulator"
        else:
            per_date = {i["date"]: "APPROVE" for i in card.items}
            ticks, every_year, lines = [i["date"] for i in card.items], False, []
            how = "auto-approve (no simulator reachable)" if not a.auto_approve else "auto-approve"
        every_year = every_year or a.every_year
        answer = None
        if card.question and not per_date:
            answer = {card.question["date"]: "regular"}
        if not per_date and not answer:
            _say("4 DECIDE", f"  via {how}:\n" + "\n".join(lines) + "\n  no response: the card stays SENT "
                                                                     "(a reminder would follow)")
            return 0
        res = decide_flow.handle_decision(card.id, {"per_date": per_date, "ticks": ticks, "every_year": every_year,
                                                    "answer": answer}, via="sim" if lines else "demo")
        _say("4 DECIDE", f"  via {how}:\n" + "\n".join(lines) + "\n" + "\n".join(
            f"  {d['date']} {d['decision']:<8} {d['final_value'] or '':<14} -> {d['status']}"
            + (f" ({d['note']})" if d["note"] else "") for d in res["decisions"])
             + (f"\n  needs a tick (not applied): {res['needs_tick']}" if res["needs_tick"] else ""))
        # 5 ---------------------------------------------------------------
        w = res.get("write") or {}
        lines5 = [f"  write {w.get('id')} to {w.get('target')}: HTTP {w.get('status')}, read-back "
                  f"{'OK' if w.get('readback_ok') else 'MISMATCH'}" if w else "  nothing to write",
                  f"  applied {res['applied']}"]
        if res["cancelled"]:
            lines5.append(f"  cancelled, owner changed them: {res['cancelled']}")
        if w.get("readback") is not None:
            lines5.append(f"  holidayHours now: {w['readback']}")
        if res["where_it_shows"]:
            lines5.append("  where it shows: " + " · ".join(
                f"{r['publisher']} {r['eta_text']}" for r in res["where_it_shows"]))
        if res.get("permission"):
            lines5.append(f"  standing permission {res['permission']['id']}: {res['permission']['scope']['values']}")
        if res.get("message"):
            lines5.append("  confirmation message:\n" + "\n".join("    | " + ln for ln in res["message"].splitlines()))
        lines5.append(f"  card status {res['status']}; audit chain {'OK' if audit.verify_chain().ok else 'BROKEN'}")
        _say("5 APPLY + CONFIRM", "\n".join(lines5))
        if res.get("error"):
            rc = 1
    finally:
        if not a.keep:
            try:
                if target == "sandbox":
                    with season.yext_client("sandbox") as yc:
                        rec = yc.clear_holiday_hours(eid, decision_id="demo-cleanup")
                    print(f"\n(cleanup: sandbox holidayHours cleared, read-back {'OK' if rec.readback_ok else 'MISMATCH'})")
                elif _mock_up(mock_url):
                    httpx.post(f"{mock_url}/mock/reset", timeout=10)
                    print("\n(cleanup: mock reset)")
            except Exception as ex:  # noqa: BLE001
                print(f"\nCLEANUP FAILED: {ex}", file=sys.stderr)
                rc = 1
        print(f"({time.time() - t0:.1f} s)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
