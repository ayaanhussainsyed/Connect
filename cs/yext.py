"""The Yext writer (BUILD_PLAN_A §A9, A13, B8): hours to the mock or the sandbox, owner-wins, read-back.

    client = YextClient("mock")                      # or "sandbox"
    rec = client.set_holiday_hours("FI-D0001", {"2026-12-24": "07:30-13:00"},
                                   expected_current={"2026-12-24": None}, decision_id="dec-…")
    rec.readback_ok, rec.request["applied"], rec.request["cancelled"]

Writes (each one: GET, owner-wins, PUT, GET read-back, WriteRecord + audit event):

- ``set_holiday_hours``: merge dates into the future holidayHours (past dates dropped, one entry
  per date, sorted) and PUT only ``{"hours": {"holidayHours": [...]}}``. The sent list replaces
  the stored one, so it always carries every future date. Weekday hours stay as they are.
- ``remove_holiday_dates``: the same list without some dates (``[]`` if none are left). Undo
  uses this to truly remove a date instead of writing ``isRegularHours``.
- ``clear_holiday_hours``: an explicit ``[]`` (test restore).
- ``set_regular_hours``: weekday hours (drift, A13). holidayHours and reopenDate are never touched.
- ``set_reopen_date``: ``hours.reopenDate`` for long emergency closures; ``None`` clears it.

Measured on the sandbox (7 Nov): ``hours`` sub-fields merge. A PUT with one weekday changes only
that weekday (holidayHours kept); ``{"hours": {"reopenDate": "…"}}`` sets it, keeping weekdays and
holidayHours, and ``"reopenDate": null`` removes it. So on the sandbox we send only the changed
sub-fields. The dataset's mock is stricter: it wants all seven weekdays when any is sent and
replaces the whole ``hours`` object (a reopenDate-only PUT would even empty holidayHours). On the
mock, weekday and reopenDate writes therefore send the full ``hours`` object built from the GET
(read-modify-write), with holidayHours unchanged; the read-back proves nothing else moved.

Owner-wins: with ``expected_current`` / ``expected_current_week``, a date or weekday whose live
value differs from the expected one (and isn't already the value we want) was changed by the owner
meanwhile, so it is dropped and recorded as cancelled. ``None`` as an expected holiday value means
"no holidayHours entry"; snapshot with ``get_holiday_hours()`` / ``get_week()`` when suggesting.

Hours values: ``"closed"``, ``"HH:MM-HH:MM[,…]"`` (lenient input is normalised by
``cs.hours.normalise``) or, for holiday dates only, ``"REGULAR"`` (``isRegularHours: true``).
Weekday keys: ``monday`` … ``sunday`` (also ``mon``/``mo`` …).

``WriteRecord.request`` holds the whole story of one call: ``op``, ``yext_entity_id``,
``decision_id``, what was asked, ``applied``, ``cancelled``, the exact PUT ``body`` (None if nothing
was sent), the ``readback`` and the "nothing else changed" checks. The API key never goes into a
record, an event, a log line or an error message.

Entity ids: the demo shows dataset business FI-D0001 but writes to the sandbox entity
(``SANDBOX_MAP``, used only when ``target == "sandbox"``). Records and audit events keep the
caller's id (FI-D0001) and store the Yext id in ``request["yext_entity_id"]``.

CLI (``--target mock|sandbox``, default mock; writes take ``--no-db``)::

    python -m cs.yext show        FI-D0001 [--json]
    python -m cs.yext set         FI-D0001 2026-12-24 07:30-13:00 [--expect none|HOURS]
    python -m cs.yext remove      FI-D0001 2026-12-24 [2026-12-31 …] [--expect DATE=HOURS …]
    python -m cs.yext clear       FI-D0001
    python -m cs.yext set-regular SE-D0016 sat=10:00-16:00 [sun=closed …] [--expect DAY=HOURS …]
    python -m cs.yext reopen      FI-D0001 2026-12-01|none
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

import httpx

from cs.config import settings
from cs.domain import WriteRecord
from cs.hours import DAYS, REGULAR, from_yext_day, normalise, to_yext_day, week_from_yext

TARGETS = ("mock", "sandbox")
ACCOUNT = "me"

# Dataset business -> sandbox entity. Applied only for target="sandbox"; override per client.
SANDBOX_MAP: dict[str, str] = (
    {"FI-D0001": settings.yext_sandbox_entity_id} if settings.yext_sandbox_entity_id else {}
)

# Audit event types written by this module.
EV_WRITE = "YEXT_WRITE"                       # set_holiday_hours
EV_REMOVE = "YEXT_REMOVE_HOLIDAY_DATES"       # remove_holiday_dates
EV_CLEAR = "YEXT_CLEAR"                       # clear_holiday_hours
EV_WRITE_REGULAR = "YEXT_WRITE_REGULAR"       # set_regular_hours
EV_REOPEN = "YEXT_SET_REOPEN_DATE"            # set_reopen_date
EV_SKIPPED = "YEXT_WRITE_SKIPPED"             # any op with nothing left to send
EV_FAILED = "YEXT_WRITE_FAILED"               # the PUT failed
EV_OWNER_WINS = "OWNER_WINS_CANCELLED"        # extra event whenever something was cancelled

_MISSING = object()
_DAY_ALIASES = {**{d: d for d in DAYS}, **{d[:3]: d for d in DAYS}, **{d[:2]: d for d in DAYS}}


class YextError(RuntimeError):
    """An HTTP error from Yext (the message never contains the API key)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class YextWriteError(YextError):
    """The PUT failed. ``record`` is the saved WriteRecord (readback_ok=False)."""

    def __init__(self, message: str, status: int | None, record: WriteRecord):
        super().__init__(message, status)
        self.record = record


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def canonical_hours(value: str) -> str:
    """'REGULAR' (any case) stays REGULAR; anything else goes through ``cs.hours.normalise``."""
    if isinstance(value, str) and value.strip().upper() == REGULAR:
        return REGULAR
    return normalise(value)


def canonical_weekday_hours(value: str) -> str:
    h = canonical_hours(value)
    if h == REGULAR:
        raise ValueError("REGULAR is for holiday dates only, not for weekday hours")
    return h


def weekday_name(key: str) -> str:
    """'sat' / 'Saturday' / 'sa' -> 'saturday'."""
    d = _DAY_ALIASES.get(str(key).strip().lower())
    if not d:
        raise ValueError(f"not a weekday: {key!r}")
    return d


def _iso(d: date | str) -> str:
    iso = d.isoformat() if isinstance(d, date) else str(d).strip()
    date.fromisoformat(iso)  # raises ValueError on a bad date
    return iso


def _canon_or_raw(v: str) -> str:
    try:
        return canonical_hours(v)
    except ValueError:
        return v


def _live_value(item: dict | None) -> str | None:
    """A holidayHours item -> canonical hours string (None if there is no item)."""
    return None if item is None else _canon_or_raw(from_yext_day(item))


def holiday_map(entity: dict) -> dict[str, str]:
    """{date: hours string} of an entity's holidayHours (the last item wins on a duplicate date)."""
    out: dict[str, str] = {}
    for item in (entity.get("hours") or {}).get("holidayHours") or []:
        if isinstance(item, dict) and item.get("date"):
            out[str(item["date"])] = _live_value(item)  # type: ignore[assignment]
    return dict(sorted(out.items()))


def week_map(entity: dict) -> dict[str, str]:
    """{monday: hours, …, sunday: hours} of an entity's weekday hours (a missing day is closed)."""
    return {d: _canon_or_raw(s) for d, s in zip(DAYS, week_from_yext(entity.get("hours") or {}))}


def reopen_date_of(entity: dict) -> str | None:
    return (entity.get("hours") or {}).get("reopenDate") or None


def _future_items(existing: Iterable[dict], today: date) -> dict[str, dict]:
    by_date: dict[str, dict] = {}
    for item in existing or []:
        d = str(item.get("date", ""))
        try:
            if date.fromisoformat(d) < today:
                continue
        except ValueError:
            continue
        by_date[d] = {k: v for k, v in item.items() if k in ("date", "isClosed", "isRegularHours",
                                                              "openIntervals")}
    return by_date


def merge_holiday_hours(existing: list[dict], changes: dict[str, str], today: date,
                        remove: Iterable[str] = ()) -> list[dict]:
    """Existing future items + changes (canonical strings) - ``remove``; past dates dropped,
    one item per date, sorted by date."""
    by_date = _future_items(existing, today)
    for d, hours in changes.items():
        if date.fromisoformat(d) >= today:
            by_date[d] = to_yext_day(hours, d)
    for d in remove:
        by_date.pop(d, None)
    return [by_date[d] for d in sorted(by_date)]


def _full_hours(before_hours: dict) -> dict:
    """The whole hours object from a GET, for the mock's read-modify-write."""
    full = {d: before_hours.get(d) or {"isClosed": True} for d in DAYS}
    for k in ("holidayHours", "reopenDate"):
        if before_hours.get(k) is not None:
            full[k] = before_hours[k]
    return full


def regular_hours_body(before_hours: dict, applied: dict[str, str], *, patch: bool) -> dict:
    """PUT body for weekday changes: only the changed days (sandbox) or the full object (mock)."""
    days = {d: to_yext_day(applied[d]) for d in DAYS if d in applied}
    if patch:
        return {"hours": days}
    return {"hours": {**_full_hours(before_hours), **days}}


def reopen_body(before_hours: dict, reopen: str | None, *, patch: bool) -> dict:
    """PUT body for reopenDate: the sub-field alone (sandbox; null clears) or the full object (mock)."""
    if patch:
        return {"hours": {"reopenDate": reopen}}
    full = _full_hours(before_hours)
    full.pop("reopenDate", None)
    if reopen:
        full["reopenDate"] = reopen
    return {"hours": full}


def _diff(want: dict, got: dict) -> dict:
    return {k: {"sent": want.get(k), "read": got.get(k)} for k in sorted(set(want) | set(got))
            if want.get(k) != got.get(k)}


def _note(applied: dict, cancelled: dict, dropped_past: Iterable[str] = (), unchanged: Iterable[str] = (),
          verb: str = "applied") -> str:
    parts = [f"{verb} " + ", ".join(f"{k}={v}" for k, v in applied.items())] if applied else []
    if cancelled:
        parts.append("cancelled (owner changed it) " + ", ".join(
            f"{k}: expected {c['expected']}, live {c['live']}" for k, c in cancelled.items()))
    if dropped_past:
        parts.append(f"dropped past {', '.join(dropped_past)}")
    if unchanged:
        parts.append(f"already live {', '.join(unchanged)}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class YextClient:
    """Knowledge Graph reads and hours writes for one target ("mock" or "sandbox")."""

    def __init__(self, target: str = "mock", *, base_url: str | None = None, api_key: str | None = None,
                 version: str | None = None, id_map: dict[str, str] | None = None,
                 db=None, persist: bool = True, timeout: float = 30.0):
        target = (target or "mock").lower()
        if target not in TARGETS:
            raise ValueError(f"target must be one of {TARGETS}, got {target!r}")
        self.target = target
        if target == "sandbox":
            self.base_url = (base_url or settings.yext_sandbox_url).rstrip("/")
            self._key = api_key or settings.yext_sandbox_key
        else:
            self.base_url = (base_url or settings.yext_base_url).rstrip("/")
            self._key = api_key or settings.yext_api_key
        if not self._key:
            raise RuntimeError(f"no API key for the Yext {target} (set it in .env)")
        self.version = version or settings.yext_api_version
        self.id_map = dict(SANDBOX_MAP if id_map is None else id_map)
        # The sandbox merges hours sub-fields; the mock needs the full hours object.
        self.patch_hours = target == "sandbox"
        self._db = db
        self.persist = persist
        self._http = httpx.Client(timeout=timeout, headers={"api-key": self._key,
                                                            "Content-Type": "application/json"})

    def __repr__(self) -> str:  # never shows the key
        return f"YextClient(target={self.target!r}, base_url={self.base_url!r}, v={self.version!r})"

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- ids ---------------------------------------------------------------
    def yext_id(self, entity_id: str) -> str:
        """The id to call Yext with (dataset id -> sandbox id on the sandbox only)."""
        return self.id_map.get(entity_id, entity_id) if self.target == "sandbox" else entity_id

    @property
    def target_label(self) -> str:
        return self.target.upper()  # WriteRecord.target: MOCK | SANDBOX

    # -- HTTP ----------------------------------------------------------------
    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"{self.base_url}/v2/accounts/{ACCOUNT}/{path}"
        try:
            r = self._http.request(method, url, params={"v": self.version}, json=body)
        except httpx.HTTPError as ex:
            raise YextError(self._scrub(f"{method} {path}: {type(ex).__name__}: {ex}")) from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            errs = (data.get("meta") or {}).get("errors") or []
            msg = "; ".join(str(e.get("message", e)) for e in errs) or r.text[:300]
            raise YextError(self._scrub(f"{method} {path} -> {r.status_code}: {msg}"), r.status_code)
        return r.status_code, data

    # -- reads ---------------------------------------------------------------
    def get_entity(self, entity_id: str) -> dict:
        """The entity as Yext returns it (``response`` of GET entities/{id})."""
        _, data = self._request("GET", f"entities/{self.yext_id(entity_id)}")
        return data.get("response") or {}

    def get_holiday_hours(self, entity_id: str) -> dict[str, str]:
        """{date: hours} of the live holidayHours. Use it as the ``expected_current`` snapshot."""
        return holiday_map(self.get_entity(entity_id))

    def get_week(self, entity_id: str) -> dict[str, str]:
        """{monday: hours, …} of the live weekday hours. Use it as ``expected_current_week``."""
        return week_map(self.get_entity(entity_id))

    # -- holiday hours -------------------------------------------------------
    def set_holiday_hours(self, entity_id: str, changes: dict[Any, str],
                          expected_current: dict[Any, str | None] | None = None,
                          decision_id: str | None = None, *, today: date | None = None) -> WriteRecord:
        """Write holiday hours for some dates (merge into the future list, PUT only holidayHours).

        Raises ``ValueError`` (bad date/hours, before any call), ``YextError`` (the first GET
        failed, nothing written) or ``YextWriteError`` (the PUT failed; ``.record`` was saved).
        """
        today = today or date.today()
        wanted = {_iso(d): canonical_hours(h) for d, h in (changes or {}).items()}
        if not wanted:
            raise ValueError("no changes given")
        expected = None if expected_current is None else {
            _iso(d): (None if v is None else canonical_hours(v)) for d, v in expected_current.items()}

        before = self.get_entity(entity_id)
        live = holiday_map(before)
        future = {d: h for d, h in sorted(wanted.items()) if date.fromisoformat(d) >= today}
        dropped_past = [d for d in sorted(wanted) if d not in future]
        applied, cancelled = _owner_wins(future, live, expected)

        request = self._request_doc("set_holiday_hours", entity_id, decision_id, changes=wanted,
                                    applied=applied, cancelled=cancelled, dropped_past=dropped_past,
                                    weekdays_unchanged=None)
        if not applied:
            return self._skip(entity_id, request, cancelled=cancelled, dropped_past=dropped_past)
        items = merge_holiday_hours((before.get("hours") or {}).get("holidayHours") or [], applied, today)
        body = {"hours": {"holidayHours": items}}
        return self._write(entity_id, body, request, _note(applied, cancelled, dropped_past), EV_WRITE,
                           self._check_holidays(before, body, request))

    def remove_holiday_dates(self, entity_id: str, dates: Iterable[Any],
                             expected_current: dict[Any, str | None] | None = None,
                             decision_id: str | None = None, *, today: date | None = None) -> WriteRecord:
        """Remove single holidayHours dates: PUT the future list without them (``[]`` if empty).

        Owner-wins: with ``expected_current`` (e.g. the value we wrote), a date whose live value
        differs was changed by the owner and is kept (cancelled). Dates with no entry are listed in
        ``not_present``; past dates in ``dropped_past``.
        """
        today = today or date.today()
        ds = sorted({_iso(d) for d in dates})
        if not ds:
            raise ValueError("no dates given")
        expected = None if expected_current is None else {
            _iso(d): (None if v is None else canonical_hours(v)) for d, v in expected_current.items()}

        before = self.get_entity(entity_id)
        live = holiday_map(before)
        removed: dict[str, str] = {}
        cancelled: dict[str, dict] = {}
        not_present: list[str] = []
        dropped_past: list[str] = []
        for d in ds:
            exp = _MISSING if expected is None else expected.get(d, _MISSING)
            if date.fromisoformat(d) < today:
                dropped_past.append(d)
            elif d not in live:
                not_present.append(d)
            elif exp is not _MISSING and live[d] != exp:
                cancelled[d] = {"expected": exp, "live": live[d], "wanted": None}
            else:
                removed[d] = live[d]

        request = self._request_doc("remove_holiday_dates", entity_id, decision_id, dates=ds, removed=removed,
                                    cancelled=cancelled, not_present=not_present, dropped_past=dropped_past,
                                    weekdays_unchanged=None)
        if not removed:
            return self._skip(entity_id, request, cancelled=cancelled, dropped_past=dropped_past,
                              not_present=not_present)
        items = merge_holiday_hours((before.get("hours") or {}).get("holidayHours") or [], {}, today,
                                    remove=removed)
        body = {"hours": {"holidayHours": items}}
        note = _note(removed, cancelled, dropped_past, verb="removed")
        if not_present:
            note += f"; no entry for {', '.join(not_present)}"
        return self._write(entity_id, body, request, note, EV_REMOVE, self._check_holidays(before, body, request))

    def clear_holiday_hours(self, entity_id: str, decision_id: str | None = None) -> WriteRecord:
        """Remove every holidayHours entry (sends an explicit ``[]``; used to restore the sandbox)."""
        before = self.get_entity(entity_id)
        body = {"hours": {"holidayHours": []}}
        request = self._request_doc("clear_holiday_hours", entity_id, decision_id, removed=holiday_map(before),
                                    weekdays_unchanged=None)
        return self._write(entity_id, body, request, "cleared holidayHours", EV_CLEAR,
                           self._check_holidays(before, body, request))

    # -- weekday hours -------------------------------------------------------
    def set_regular_hours(self, entity_id: str, week: dict[str, str],
                          expected_current_week: dict[str, str] | None = None,
                          decision_id: str | None = None) -> WriteRecord:
        """Change some weekday hours (drift, A13). Only changed weekdays are sent on the sandbox;
        holidayHours and reopenDate are never changed (checked on read-back).

        ``request`` has ``changes``, ``applied``, ``cancelled`` (owner-wins), ``unchanged``
        (already live, not sent), ``readback`` (the full week) and ``others_unchanged``.
        """
        wanted = {weekday_name(k): canonical_weekday_hours(v) for k, v in (week or {}).items()}
        if not wanted:
            raise ValueError("no weekday changes given")
        expected = None if expected_current_week is None else {
            weekday_name(k): canonical_weekday_hours(v) for k, v in expected_current_week.items()}

        before = self.get_entity(entity_id)
        live = week_map(before)
        ordered = {d: wanted[d] for d in DAYS if d in wanted}
        candidates, cancelled = _owner_wins(ordered, live, expected)
        unchanged = [d for d, h in candidates.items() if live.get(d) == h]
        applied = {d: h for d, h in candidates.items() if d not in unchanged}

        request = self._request_doc("set_regular_hours", entity_id, decision_id, changes=ordered,
                                    applied=applied, cancelled=cancelled, unchanged=unchanged,
                                    strategy="patch" if self.patch_hours else "full",
                                    others_unchanged=None)
        if not applied:
            return self._skip(entity_id, request, cancelled=cancelled, unchanged=unchanged)
        body = regular_hours_body(before.get("hours") or {}, applied, patch=self.patch_hours)
        want_week = {**live, **applied}

        def check(after: dict) -> list[str]:
            got = week_map(after)
            request["readback"] = got
            problems = [] if got == want_week else [f"weekdays {json.dumps(_diff(want_week, got))}"]
            hol_same = holiday_map(after) == holiday_map(before)
            reopen_same = reopen_date_of(after) == reopen_date_of(before)
            request["others_unchanged"] = hol_same and reopen_same
            return (problems + ([] if hol_same else ["holidayHours changed"])
                    + ([] if reopen_same else ["reopenDate changed"]))

        return self._write(entity_id, body, request, _note(applied, cancelled, unchanged=unchanged),
                           EV_WRITE_REGULAR, check)

    # -- reopen date -----------------------------------------------------------
    def set_reopen_date(self, entity_id: str, reopen: date | str | None, decision_id: str | None = None,
                        *, today: date | None = None) -> WriteRecord:
        """Set ``hours.reopenDate`` (a long emergency closure) or clear it with ``None``.

        A past date is refused (ValueError). Weekday hours and holidayHours are kept (checked).
        What publishers then show (e.g. "temporarily closed") can't be observed in the sandbox.
        """
        today = today or date.today()
        iso = None if reopen in (None, "", "none") else _iso(reopen)  # type: ignore[arg-type]
        if iso and date.fromisoformat(iso) < today:
            raise ValueError(f"reopen date {iso} is in the past")
        before = self.get_entity(entity_id)
        request = self._request_doc("set_reopen_date", entity_id, decision_id, reopen_date=iso,
                                    previous=reopen_date_of(before), others_unchanged=None)
        body = reopen_body(before.get("hours") or {}, iso, patch=self.patch_hours)

        def check(after: dict) -> list[str]:
            got = reopen_date_of(after)
            request["readback"] = {"reopenDate": got}
            problems = [] if got == iso else [f"reopenDate sent {iso}, read {got}"]
            others = week_map(after) == week_map(before) and holiday_map(after) == holiday_map(before)
            request["others_unchanged"] = others
            return problems + ([] if others else ["weekday hours or holidayHours changed"])

        note = f"reopenDate {reopen_date_of(before)} -> {iso}"
        return self._write(entity_id, body, request, note, EV_REOPEN, check)

    # -- internals -------------------------------------------------------------
    def _request_doc(self, op: str, entity_id: str, decision_id: str | None, **fields) -> dict[str, Any]:
        return {"op": op, "yext_entity_id": self.yext_id(entity_id), "decision_id": decision_id,
                **fields, "body": None, "readback": None}

    def _check_holidays(self, before: dict, body: dict, request: dict) -> Callable[[dict], list[str]]:
        sent = {item["date"]: _live_value(item) for item in body["hours"]["holidayHours"]}

        def check(after: dict) -> list[str]:
            got = holiday_map(after)
            same_week = week_map(after) == week_map(before)
            request["readback"] = got
            request["weekdays_unchanged"] = same_week
            problems = [] if got == sent else [f"holidayHours {json.dumps(_diff(sent, got))}"]
            return problems + ([] if same_week else ["weekday hours changed"])

        return check

    def _skip(self, entity_id: str, request: dict, *, cancelled=None, dropped_past=(), not_present=(),
              unchanged=()) -> WriteRecord:
        why = []
        if cancelled:
            why.append(f"owner changed {', '.join(cancelled)}")
        if dropped_past:
            why.append(f"past {', '.join(dropped_past)}")
        if not_present:
            why.append(f"no entry for {', '.join(not_present)}")
        if unchanged:
            why.append(f"already live {', '.join(unchanged)}")
        rec = WriteRecord(entity_id=entity_id, target=self.target_label, request=request,
                          note="nothing to write: " + ("; ".join(why) or "no changes"))
        self._persist(rec, EV_SKIPPED)
        return rec

    def _write(self, entity_id: str, body: dict, request: dict, note: str, event_type: str,
               check: Callable[[dict], list[str]]) -> WriteRecord:
        """PUT ``body``, GET again, run ``check`` (returns problems), save record + audit event."""
        request["body"] = body
        rec = WriteRecord(entity_id=entity_id, target=self.target_label, request=request)
        try:
            status, _ = self._request("PUT", f"entities/{self.yext_id(entity_id)}", body)
        except YextError as ex:
            rec.response_status, rec.readback_ok, rec.note = ex.status, False, f"PUT failed: {ex}"
            self._persist(rec, EV_FAILED)
            raise YextWriteError(str(ex), ex.status, rec) from None
        rec.response_status = status
        problems = check(self.get_entity(entity_id))
        rec.readback_ok = not problems
        rec.note = note + ("" if not problems else "; READ-BACK MISMATCH: " + "; ".join(problems))
        self._persist(rec, event_type)
        return rec

    def _persist(self, rec: WriteRecord, event_type: str) -> None:
        if not self.persist:
            return
        from cs import audit, db as dbmod

        database = self._db if self._db is not None else dbmod.get_db()
        dbmod.save(rec, db=database)
        payload = {"write_id": rec.id, "target": rec.target, "response_status": rec.response_status,
                   "readback_ok": rec.readback_ok, "note": rec.note,
                   **{k: v for k, v in rec.request.items() if k not in ("body", "readback")}}
        audit.log(event_type, payload, entity_id=rec.entity_id, db=database)
        if rec.request.get("cancelled"):
            audit.log(EV_OWNER_WINS, {"write_id": rec.id, "op": rec.request.get("op"),
                                      "decision_id": rec.request.get("decision_id"),
                                      "cancelled": rec.request["cancelled"]},
                      entity_id=rec.entity_id, db=database)


def _owner_wins(wanted: dict[str, str], live: dict[str, str | None],
                expected: dict[str, str | None] | None) -> tuple[dict[str, str], dict[str, dict]]:
    """Split ``wanted`` into (applied, cancelled): a key is cancelled when an expected value was
    given, the live value differs from it, and the live value isn't already what we want."""
    applied: dict[str, str] = {}
    cancelled: dict[str, dict] = {}
    for k, h in wanted.items():
        exp = _MISSING if expected is None else expected.get(k, _MISSING)
        now = live.get(k)
        if exp is not _MISSING and now != exp and now != h:
            cancelled[k] = {"expected": exp, "live": now, "wanted": h}
        else:
            applied[k] = h
    return applied, cancelled


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_entity(client: YextClient, entity_id: str, ent: dict) -> None:
    yid = client.yext_id(entity_id)
    print(f"{ent.get('name', '?')}  [{client.target}: {yid}"
          + (f" for {entity_id}" if yid != entity_id else "") + "]")
    for day, h in week_map(ent).items():
        print(f"  {day[:3].title()}  {h}")
    hol = holiday_map(ent)
    print("  holidayHours:" + (" (none)" if not hol else ""))
    for d, h in hol.items():
        print(f"    {d}  {'regular hours' if h == REGULAR else h}")
    if reopen_date_of(ent):
        print(f"  reopenDate: {reopen_date_of(ent)}")


def _db_ok(no_db: bool) -> bool:
    if no_db:
        return False
    from cs import db as dbmod

    if settings.has("mongodb_uri") and dbmod.ping():
        return True
    print("warning: MongoDB not reachable, so no WriteRecord / audit event is saved", file=sys.stderr)
    return False


def _pairs(items: list[str] | None, what: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep:
            raise ValueError(f"expected {what}=HOURS, got {it!r}")
        out[k.strip()] = None if v.strip().lower() == "none" else v.strip()
    return out


_READBACK_LABEL = {"set_regular_hours": "weekly hours now", "set_reopen_date": "now"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.yext", description="Read and write Yext hours.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    helps = {"show": "print weekday hours, holidayHours and reopenDate", "set": "set one holiday date",
             "remove": "remove holiday dates", "clear": "remove every holiday date (sends [])",
             "set-regular": "change weekday hours, e.g. sat=10:00-16:00 sun=closed",
             "reopen": "set hours.reopenDate, or 'none' to clear it"}
    p = {}
    for name, text in helps.items():
        p[name] = sub.add_parser(name, help=text)
        p[name].add_argument("entity_id")
        p[name].add_argument("--target", choices=TARGETS, default="mock")
        if name == "show":
            p[name].add_argument("--json", action="store_true", help="print the raw entity JSON")
        else:
            p[name].add_argument("--no-db", action="store_true", help="don't save a WriteRecord / audit event")
    p["set"].add_argument("date")
    p["set"].add_argument("hours", help="closed | HH:MM-HH:MM[,…] | REGULAR")
    p["set"].add_argument("--expect", help="owner-wins: the value you expect live now ('none' = no entry)")
    p["remove"].add_argument("dates", nargs="+")
    p["remove"].add_argument("--expect", action="append", metavar="DATE=HOURS", help="owner-wins, repeatable")
    p["set-regular"].add_argument("days", nargs="+", metavar="DAY=HOURS")
    p["set-regular"].add_argument("--expect", action="append", metavar="DAY=HOURS", help="owner-wins, repeatable")
    p["reopen"].add_argument("date", help="YYYY-MM-DD or none")
    a = ap.parse_args(argv)

    persist = a.cmd != "show" and _db_ok(a.no_db)
    with YextClient(a.target, persist=persist) as client:
        try:
            if a.cmd == "show":
                ent = client.get_entity(a.entity_id)
                if a.json:
                    print(json.dumps(ent, ensure_ascii=False, indent=2))
                else:
                    _print_entity(client, a.entity_id, ent)
                return 0
            if a.cmd == "clear":
                rec = client.clear_holiday_hours(a.entity_id)
            elif a.cmd == "remove":
                rec = client.remove_holiday_dates(a.entity_id, a.dates, _pairs(a.expect, "DATE") or None)
            elif a.cmd == "set-regular":
                days = {k: v for k, v in _pairs(a.days, "DAY").items() if v is not None}
                exp = {k: v for k, v in _pairs(a.expect, "DAY").items() if v is not None} or None
                rec = client.set_regular_hours(a.entity_id, days, exp)
            elif a.cmd == "reopen":
                rec = client.set_reopen_date(a.entity_id, None if a.date.lower() == "none" else a.date)
            else:
                expected = None
                if a.expect is not None:
                    expected = {a.date: None if a.expect.lower() == "none" else a.expect}
                rec = client.set_holiday_hours(a.entity_id, {a.date: a.hours}, expected)
        except (YextError, ValueError) as ex:
            print(f"error: {ex}", file=sys.stderr)
            return 1
    saved = "" if persist else " (not saved to MongoDB)"
    if rec.response_status is None:
        print(f"{rec.target}: nothing sent ({rec.id}){saved}")
    else:
        print(f"{rec.target} write {rec.id}: HTTP {rec.response_status}, read-back "
              f"{'OK' if rec.readback_ok else 'MISMATCH'}{saved}")
    print(f"  {rec.note}")
    if rec.request.get("readback") is not None:
        label = _READBACK_LABEL.get(rec.request["op"], "holidayHours now")
        print(f"  {label}: {json.dumps(rec.request['readback'], ensure_ascii=False)}")
    if a.cmd in ("set", "set-regular") and rec.request.get("applied"):
        from cs.confirm import format_confirmation, where_it_shows

        print(format_confirmation(where_it_shows(a.entity_id, rec.at, target=a.target)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
