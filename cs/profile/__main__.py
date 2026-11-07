"""CLI for A13.

    python -m cs.profile drift SE-D0016 --lang sv [--channel SMS]
    python -m cs.profile drift --list
    python -m cs.profile reviews FI-D0001 --lang fi [--polish]
    python -m cs.profile reviews --demo        # which demo businesses have unanswered complaints
"""
from __future__ import annotations

import argparse
import sys

from cs import compose as C
from cs.profile import drift, review_reply


def _print_msg(msg: dict) -> None:
    print(msg["text"])
    if msg["buttons"]:
        print("  " + "  ".join(f"[{b['title']}]" for b in msg["buttons"]))


def cmd_drift(a) -> int:
    if a.list or not a.entity_id:
        ids = drift.suggested_entities()
        print(f"{len(ids)} businesses with a drift suggestion:")
        print("  " + " ".join(ids))
        return 0
    s = drift.drift_suggestion(a.entity_id)
    biz = C.business_for(a.entity_id)
    if s is None:
        print(f"{a.entity_id} {biz['name']}: no drift suggestion (weekly hours look up to date)")
        return 0
    lang = a.lang or C.norm_lang(biz.get("language"))
    print(f"=== {a.entity_id} {biz['name']} · REGULAR_HOURS · p_outdated={s.probability:.3f} · basis={s.basis} ===")
    for e in s.evidence:
        print(f"  evidence {e.source:<9} w={e.weight:.1f}  {e.detail}  [{e.ref}]")
    print(f"--- message ({lang}, {a.channel}) ---")
    _print_msg(drift.compose_drift_message(biz, s, lang, a.channel))
    return 0


def cmd_reviews(a) -> int:
    if a.demo or not a.entity_id:
        found = review_reply.entities_with_complaints(C.DEMO_IDS)
        print("Demo businesses with unanswered closed-door / wrong-hours reviews:")
        for e, n in found.items():
            print(f"  {e} {C.business_for(e)['name']}: {n}")
        return 0
    biz = C.business_for(a.entity_id)
    lang = a.lang or C.norm_lang(biz.get("language"))
    drafts = review_reply.review_reply_drafts(a.entity_id, lang, polish=a.polish, use_db=not a.no_db)
    print(f"=== {a.entity_id} {biz['name']} · {len(drafts)} unanswered complaint(s) ===")
    if not drafts:
        found = review_reply.entities_with_complaints(C.DEMO_IDS)
        print("(no unanswered closed-door / wrong-hours reviews; demo businesses that have some: "
              + ", ".join(f"{e} ({n})" for e, n in found.items()) + ")")
        return 0
    for d in drafts:
        r, f = d["review"], d["facts"]
        print(f"- review {r['review_id']} · {r['review_date']} · rating {r['rating']} · day {r['day_key']} "
              f"({r['day_date']}) · reply in {d['lang']}")
        print(f"  customer: {r['content']}")
        print(f"  facts:    {f['date'] + ' ' + f['hours'] + ' [' + f['ref'] + ']' if f else 'none (no confirmed hours since)'}")
        print(f"  draft:    {d['suggestion'].value}")
    print(f"--- offer to the owner ({lang}, {a.channel}) ---")
    _print_msg(review_reply.compose_review_reply_offer(biz, drafts, lang, a.channel))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.profile")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drift", help="weekly hours that changed")
    d.add_argument("entity_id", nargs="?")
    d.add_argument("--lang", choices=C.LANGS)
    d.add_argument("--channel", default="WHATSAPP", choices=C.CHANNELS)
    d.add_argument("--list", action="store_true")
    r = sub.add_parser("reviews", help="replies to closed-door reviews")
    r.add_argument("entity_id", nargs="?")
    r.add_argument("--lang", choices=C.LANGS)
    r.add_argument("--channel", default="WHATSAPP", choices=C.CHANNELS)
    r.add_argument("--polish", action="store_true", help="let Claude polish the drafts (slot-checked)")
    r.add_argument("--no-db", action="store_true", help="don't read approved answers from MongoDB")
    r.add_argument("--demo", action="store_true")
    a = ap.parse_args(argv)
    try:
        return cmd_drift(a) if a.cmd == "drift" else cmd_reviews(a)
    except KeyError as e:
        print(f"unknown entity: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
