"""CLI for the business memory graph.

    python -m cs.kg build FI-D0001          # one business (or: python -m cs.kg build --demo)
    python -m cs.kg ask FI-D0001 "Mitä tein viime pääsiäisenä?" --lang fi [--no-llm] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.kg")
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("entity_id", nargs="?")
    b.add_argument("--demo", action="store_true", help="build every demo business")
    a = sp.add_parser("ask")
    a.add_argument("entity_id")
    a.add_argument("question")
    a.add_argument("--lang", default="fi")
    a.add_argument("--no-llm", action="store_true")
    a.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        from .build import build, build_demo
        res = build_demo() if args.demo or not args.entity_id else [build(args.entity_id)]
        for r in res:
            print(json.dumps(r))
        return 0
    from .answer import ask
    r = ask(args.entity_id, args.question, args.lang, use_llm=False if args.no_llm else None)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    else:
        print(r["answer"])
        print(f"\n[{r['mode']}, grounded={r['grounded']}] citations: {', '.join(r['citations']) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
