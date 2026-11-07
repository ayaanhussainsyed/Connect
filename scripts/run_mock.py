#!/usr/bin/env python3
"""Run the dataset's mock Yext API with a throw-away state file.

    .venv/bin/python scripts/run_mock.py                    # serve on 127.0.0.1:8787 (Ctrl+C to stop)
    .venv/bin/python scripts/run_mock.py --port 8788        # another port
    .venv/bin/python scripts/run_mock.py --state /tmp/x.json  # keep state in a file of your choice
    .venv/bin/python scripts/run_mock.py reset [--port 8787]  # wipe all writes on a running mock
    .venv/bin/python scripts/run_mock.py status [--port 8787] # health check

Like ``examples/quickstart.py``, this points ``server.STATE_FILE`` at a temporary file before the
store is built, so the dataset's ``mock_api/state.json`` is never read or written. The dataset
folder stays read-only: a ``--state`` path inside it is refused.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs.config import settings  # noqa: E402

MOCK_DIR = settings.dataset_dir / "mock_api"


def _serve(host: str, port: int, state: str | None) -> int:
    if not (MOCK_DIR / "server.py").exists():
        print(f"mock server not found at {MOCK_DIR}", file=sys.stderr)
        return 2
    tmp_dir = None
    if state:
        state_path = Path(state).resolve()
        if settings.dataset_dir.resolve() in state_path.parents:
            print("refusing a --state file inside the read-only dataset folder", file=sys.stderr)
            return 2
    else:
        tmp_dir = tempfile.mkdtemp(prefix="yext-mock-")
        state_path = Path(tmp_dir) / "state.json"

    sys.dont_write_bytecode = True  # no __pycache__ inside the read-only dataset folder
    sys.path.insert(0, str(MOCK_DIR))
    import server  # the dataset's mock (also imports its own hours.py / simulator.py)

    server.STATE_FILE = str(state_path)  # never touch mock_api/state.json
    assert Path(server.STATE_FILE).resolve() != (MOCK_DIR / "state.json").resolve()
    server.STORE = server.Store()
    srv = ThreadingHTTPServer((host, port), server.Handler)
    print(f"Yext mock on http://{host}:{port} ({len(server.STORE.base)} entities), "
          f"state file {state_path}{' (temporary)' if tmp_dir else ''}. Ctrl+C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0


def _call(method: str, host: str, port: int, path: str) -> int:
    import httpx

    url = f"http://{host}:{port}{path}"
    try:
        r = httpx.request(method, url, timeout=10)
    except httpx.HTTPError as ex:
        print(f"no mock reachable at http://{host}:{port} ({type(ex).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(r.json().get("response", {}), ensure_ascii=False))
    return 0 if r.status_code == 200 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="serve", choices=("serve", "reset", "status"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8787)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--state", help="state file to use instead of a temporary one (never inside the dataset)")
    a = ap.parse_args(argv)
    os.environ.setdefault("MOCK_QUIET", "1")
    if a.command == "reset":
        return _call("POST", a.host, a.port, "/mock/reset")
    if a.command == "status":
        return _call("GET", a.host, a.port, "/health")
    return _serve(a.host, a.port, a.state)


if __name__ == "__main__":
    raise SystemExit(main())
