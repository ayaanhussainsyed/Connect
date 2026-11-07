# ---------------------------------------------------------------------------
# Lines below up to "Connect Suggests additions" are copied unchanged from
# dataset/FONECTA_CONNECT_SUGGESTS_2026/mock_api/hours.py
# Source: Connect Suggests simulator, Since AI ry 2026, MIT licence.
# © 2026 Since AI ry. Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software, to deal in the software without restriction,
# including the rights to use, copy, modify, merge, publish, distribute, sublicense
# and sell copies, provided the copyright notice and this permission notice are
# included. The software is provided "as is", without warranty of any kind.
# ---------------------------------------------------------------------------
"""Opening-hours primitives shared by the generator, the mock API and the scorer.

Canonical text form ("hours string"):
    "closed"                       -> closed all day
    "09:00-17:00"                  -> one interval
    "10:00-14:00,17:00-22:00"      -> split day
    "18:00-02:00"                  -> past midnight (end < start)
    "00:00-23:59"                  -> open 24 hours (Yext convention)
"""
from __future__ import annotations

import re

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_ABBR = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}


def t2m(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def m2t(m: int) -> str:
    m = int(m) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_week(spec: str) -> list[list[tuple[int, int]]]:
    """'mo-fr 09:00-17:00; sa 10:00-14:00' -> 7 lists of (start,end) minutes.

    end may exceed 1440 for intervals that run past midnight.
    """
    week: list[list[tuple[int, int]]] = [[] for _ in range(7)]
    for part in [p.strip() for p in spec.split(";") if p.strip()]:
        days, *ivs = part.split()
        if "-" in days:
            a, b = days.split("-")
            idx = list(range(_ABBR[a], _ABBR[b] + 1))
        else:
            idx = [_ABBR[d] for d in days.split(",")]
        for iv in ",".join(ivs).split(","):
            if not iv:
                continue
            s, e = iv.split("-")
            sm, em = t2m(s), t2m(e)
            if em <= sm and not (s == "00:00" and e == "23:59"):
                em += 1440
            for d in idx:
                week[d].append((sm, em))
    return week


def ivs_to_str(ivs: list[tuple[int, int]]) -> str:
    if not ivs:
        return "closed"
    return ",".join(f"{m2t(s)}-{m2t(e) if e != 1439 else '23:59'}" for s, e in ivs)


def str_to_ivs(s: str) -> list[tuple[int, int]]:
    s = (s or "").strip().lower()
    if s in ("", "closed"):
        return []
    out = []
    for iv in s.split(","):
        a, b = iv.strip().split("-")
        sm, em = t2m(a), t2m(b)
        if em <= sm and not (a == "00:00" and b == "23:59"):
            em += 1440
        out.append((sm, em))
    return out


HOURS_RE = re.compile(r"^(closed|\d{2}:\d{2}-\d{2}:\d{2}(,\d{2}:\d{2}-\d{2}:\d{2})*)$")


def is_valid_hours(s: str) -> bool:
    if not isinstance(s, str) or not HOURS_RE.match(s.strip().lower()):
        return False
    try:
        for a, b in (iv.split("-") for iv in s.split(",")) if s != "closed" else []:
            for t in (a, b):
                h, m = map(int, t.split(":"))
                if h > 23 or m > 59:
                    return False
    except ValueError:
        return False
    return True


def week_to_yext(week) -> dict:
    out = {}
    for i, d in enumerate(DAYS):
        ivs = week[i]
        if not ivs:
            out[d] = {"isClosed": True}
        else:
            out[d] = {"openIntervals": [{"start": m2t(s), "end": "23:59" if e == 1439 else m2t(e)} for s, e in ivs]}
    return out


def hours_str_to_yext_day(s: str) -> dict:
    ivs = str_to_ivs(s)
    if not ivs:
        return {"isClosed": True}
    return {"openIntervals": [{"start": m2t(a), "end": "23:59" if b == 1439 else m2t(b)} for a, b in ivs]}


def yext_day_to_str(d: dict) -> str:
    if not d or d.get("isClosed"):
        return "closed"
    ivs = d.get("openIntervals") or []
    if not ivs:
        return "closed"
    return ",".join(f"{iv['start']}-{iv['end']}" for iv in ivs)


def yext_to_week(h: dict) -> list[list[tuple[int, int]]]:
    return [str_to_ivs(yext_day_to_str(h.get(d, {"isClosed": True}))) for d in DAYS]


def same_state(a: str, b: str) -> bool:
    return (a == "closed") == (b == "closed")


def boundary_diff(a: str, b: str) -> int:
    """Max absolute minute difference between first open / last close. Large if shapes differ."""
    ia, ib = str_to_ivs(a), str_to_ivs(b)
    if not ia or not ib:
        return 0 if (not ia and not ib) else 9999
    return max(abs(ia[0][0] - ib[0][0]), abs(ia[-1][1] - ib[-1][1]))


# ---------------------------------------------------------------------------
# Connect Suggests additions (our code, not part of the simulator's file)
# ---------------------------------------------------------------------------
import datetime as _dt  # noqa: E402

CLOSED = "closed"
REGULAR = "REGULAR"  # sentinel for a holidayHours item with isRegularHours = true

# Words owners and staff type for "closed" (fi / sv / nb / en).
_CLOSED_WORDS = frozenset({"closed", "suljettu", "kiinni", "stängt", "stengt", "lukittu"})
_DASHES = str.maketrans({"–": "-", "—": "-", "‒": "-", "−": "-"})
_LOOSE_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")
_STRICT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _loose_minutes(t: str) -> int:
    """'9' / '09' / '9:30' / '9.30' / '24:00' -> minutes since midnight (0..1440)."""
    m = _LOOSE_TIME_RE.match(t.strip())
    if not m:
        raise ValueError(f"not a time: {t!r}")
    h, mm = int(m.group(1)), int(m.group(2) or 0)
    if mm > 59 or h > 24 or (h == 24 and mm != 0):
        raise ValueError(f"not a time: {t!r}")
    return h * 60 + mm


def normalise(s: str) -> str:
    """Turn a lenient hours string into the canonical form, or raise ``ValueError``.

    Accepts upper case, spaces, 'H' / 'H:MM' / 'H.MM' times, en/em dashes, ';' as the
    interval separator, local words for closed (suljettu, stängt, stengt …) and '24:00'
    as an end time ('00:00-24:00' -> '00:00-23:59', the Yext 24-hour convention;
    otherwise '24:00' -> '00:00', i.e. closing at midnight). Intervals are sorted by start.
    The result always passes ``is_valid``.
    """
    if s is None:
        raise ValueError("hours missing")
    t = str(s).strip().lower().translate(_DASHES)
    if t in _CLOSED_WORDS:
        return CLOSED
    parts = [p.strip() for p in t.replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError(f"no hours in {s!r}")
    ivs: list[tuple[int, int]] = []
    for p in parts:
        bits = p.split("-")
        if len(bits) != 2:
            raise ValueError(f"interval needs exactly one '-': {p!r}")
        a, b = _loose_minutes(bits[0]), _loose_minutes(bits[1])
        if a == 1440:
            raise ValueError(f"an interval cannot start at 24:00: {p!r}")
        ivs.append((a, b))
    ivs.sort(key=lambda iv: iv[0])
    out = []
    for a, b in ivs:
        if a == 0 and b in (1439, 1440):
            out.append("00:00-23:59")
        else:
            out.append(f"{m2t(a)}-{m2t(b)}")  # m2t wraps 1440 -> 00:00 (closes at midnight)
    res = ",".join(out)
    if not is_valid(res):
        raise ValueError(f"invalid hours after normalising {s!r}: {res!r}")
    return res


def _spans(s: str) -> list[tuple[int, int]]:
    out = []
    for iv in s.split(","):
        a, b = iv.split("-")
        sa, sb = t2m(a), t2m(b)
        out.append((sa, sb if sb > sa else sb + 1440))
    return out


def is_valid(s: str) -> bool:
    """Canonical and acceptable to Yext (the same interval rules as the mock API).

    Builds on ``is_valid_hours`` (format and clock ranges) and adds: no surrounding
    spaces or capitals, start != end (use 00:00-23:59 for 24 h), no overlapping intervals.
    """
    if not isinstance(s, str) or s != s.strip() or s != s.lower():
        return False
    if not is_valid_hours(s):
        return False
    if s == CLOSED:
        return True
    for iv in s.split(","):
        a, b = iv.split("-")
        if not (_STRICT_TIME_RE.match(a) and _STRICT_TIME_RE.match(b)) or a == b:
            return False
    spans = sorted(_spans(s))
    return all(a2 >= b1 for (_, b1), (a2, _) in zip(spans, spans[1:]))


def to_yext_day(s: str, date: "_dt.date | str | None" = None) -> dict:
    """Hours string (or ``REGULAR``) -> Yext day object; with ``date`` -> a holidayHours item."""
    if s == REGULAR:
        obj: dict = {"isRegularHours": True}
    else:
        if not is_valid(s):
            raise ValueError(f"invalid hours: {s!r}")
        obj = hours_str_to_yext_day(s)
    if date is not None:
        iso = date.isoformat() if isinstance(date, _dt.date) else str(date)
        _dt.date.fromisoformat(iso)  # raises on a bad date
        obj = {"date": iso, **obj}
    return obj


def from_yext_day(obj: dict | None) -> str:
    """Yext day object or holidayHours item -> hours string; ``REGULAR`` for isRegularHours.

    A missing object counts as closed, like the simulator's ``yext_day_to_str``.
    """
    if obj and obj.get("isRegularHours"):
        return REGULAR
    return yext_day_to_str(obj or {})


def weekday_key(d: "_dt.date") -> str:
    """date -> 'monday' … 'sunday' (the Yext hours keys)."""
    return DAYS[d.weekday()]


def week_from_yext(hours: dict) -> list[str]:
    """Yext ``hours`` object -> 7 hours strings, Monday first."""
    return [yext_day_to_str(hours.get(d) or {"isClosed": True}) for d in DAYS]


def week_strs_to_yext(week: list[str]) -> dict:
    """7 hours strings, Monday first -> Yext weekday objects (no holidayHours)."""
    if len(week) != 7:
        raise ValueError("a week needs 7 days")
    return {d: to_yext_day(week[i]) for i, d in enumerate(DAYS)}
