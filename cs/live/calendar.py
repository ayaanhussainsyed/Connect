"""Bonus live source: the special-day calendar, built live instead of read from calendar_special_days.csv.

    from cs.live.calendar import special_days, agreement
    special_days("FI", "2026-11-07", "2027-01-06")     # DataFrame in the dataset's calendar format
    agreement("2026-01-01", "2027-01-06")              # matched / missing / extra per country and day_type

Two layers:
1. **Official public holidays** from the Python ``holidays`` package (MIT; offline, maintained, versioned).
   Fallback when the package is missing: the Nager.Date API (``date.nager.at/api/v3/PublicHolidays/{year}/{cc}``).
   Their English names are mapped to the dataset's ``day_key``s.
2. **Our own rules** for the days that matter to opening hours but are not public holidays: eves (Epiphany Eve,
   Maundy Thursday, Easter Saturday, May Day Eve, Whitsun Eve, Midsummer Eve, Christmas Eve, New Year's Eve), the
   day before Christmas Eve, the days between Christmas and New Year, Lucia (SE), Black Friday, Father's Day and
   Mother's Day (each country's own Sunday).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import pandas as pd

COUNTRIES = ("FI", "SE", "NO")

# English holiday names (holidays package, en_US; Nager.Date 'name') -> dataset day_key
NAME_TO_KEY = {
    "New Year's Day": "NEW_YEARS_DAY", "Epiphany": "EPIPHANY", "Maundy Thursday": "MAUNDY_THURSDAY",
    "Good Friday": "GOOD_FRIDAY", "Easter Sunday": "EASTER_SUNDAY", "Easter Monday": "EASTER_MONDAY",
    "May Day": "MAY_DAY", "Labor Day": "MAY_DAY", "Labour Day": "MAY_DAY", "International Workers' Day": "MAY_DAY",
    "Ascension Day": "ASCENSION", "Pentecost": "PENTECOST", "Whit Sunday": "PENTECOST", "Whitsun": "PENTECOST",
    "Pentecost Monday": "WHIT_MONDAY", "Whit Monday": "WHIT_MONDAY", "Midsummer Eve": "MIDSUMMER_EVE",
    "Midsummer Day": "MIDSUMMER_DAY", "All Saints' Day": "ALL_SAINTS", "Independence Day": "INDEPENDENCE_DAY",
    "Christmas Eve": "XMAS_EVE", "Christmas Day": "XMAS_DAY", "Second Day of Christmas": "BOXING_DAY",
    "St. Stephen's Day": "BOXING_DAY", "Boxing Day": "BOXING_DAY", "National Day": "NATIONAL_DAY",
    "National Day of Sweden": "NATIONAL_DAY", "Constitution Day": "NATIONAL_DAY", "New Year's Eve": "NYE",
}

DAY_TYPE = {k: "EVE" for k in ("EPIPHANY_EVE", "MAUNDY_THURSDAY", "EASTER_SATURDAY", "MAY_EVE", "PENTECOST_EVE",
                               "MIDSUMMER_EVE", "XMAS_EVE", "NYE")}
DAY_TYPE.update({"FATHERS_DAY": "OBSERVANCE", "MOTHERS_DAY": "OBSERVANCE", "LUCIA": "OBSERVANCE",
                 "BLACK_FRIDAY": "COMMERCIAL", "DEC23": "COMMERCIAL", "INTERDAY": "BETWEEN_HOLIDAYS"})
DAY_CLASS = {"NEW_YEARS_DAY": "MAJOR", "GOOD_FRIDAY": "MAJOR", "EASTER_SUNDAY": "MAJOR", "MIDSUMMER_DAY": "MAJOR",
             "XMAS_DAY": "MAJOR", "XMAS_EVE": "EVE_MAJOR", "MIDSUMMER_EVE": "EVE_MAJOR", "INTERDAY": "INTER",
             "FATHERS_DAY": "OBS", "MOTHERS_DAY": "OBS", "LUCIA": "OBS", "BLACK_FRIDAY": "COMM", "DEC23": "COMM"}
for _k in ("EPIPHANY_EVE", "MAUNDY_THURSDAY", "EASTER_SATURDAY", "MAY_EVE", "PENTECOST_EVE", "NYE"):
    DAY_CLASS[_k] = "EVE_MINOR"
NAME_EN = {"EPIPHANY_EVE": "Epiphany Eve", "MAUNDY_THURSDAY": "Maundy Thursday", "EASTER_SATURDAY": "Easter Saturday",
           "MAY_EVE": "May Day Eve", "PENTECOST_EVE": "Whitsun Eve", "MIDSUMMER_EVE": "Midsummer Eve",
           "XMAS_EVE": "Christmas Eve", "NYE": "New Year's Eve", "DEC23": "Day before Christmas Eve",
           "INTERDAY": "Between Christmas and New Year", "LUCIA": "St Lucy's Day", "BLACK_FRIDAY": "Black Friday",
           "FATHERS_DAY": "Father's Day", "MOTHERS_DAY": "Mother's Day"}

# Which rule-based (non-public) days each country observes, as in Connect's markets
RULE_KEYS = {
    "FI": {"EPIPHANY_EVE", "MAUNDY_THURSDAY", "EASTER_SATURDAY", "MAY_EVE", "MIDSUMMER_EVE", "XMAS_EVE", "NYE", "DEC23",
           "INTERDAY", "BLACK_FRIDAY", "FATHERS_DAY", "MOTHERS_DAY"},
    "SE": {"EPIPHANY_EVE", "MAUNDY_THURSDAY", "EASTER_SATURDAY", "MAY_EVE", "PENTECOST_EVE", "MIDSUMMER_EVE", "XMAS_EVE",
           "NYE", "DEC23", "INTERDAY", "LUCIA", "BLACK_FRIDAY", "FATHERS_DAY", "MOTHERS_DAY"},
    "NO": {"MAUNDY_THURSDAY", "EASTER_SATURDAY", "PENTECOST_EVE", "XMAS_EVE", "NYE", "DEC23", "INTERDAY",
           "BLACK_FRIDAY", "FATHERS_DAY", "MOTHERS_DAY"},
}


def _easter(y: int) -> date:
    """Western (Gregorian) Easter Sunday, anonymous algorithm."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
    d = date(y, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, month: int, weekday: int) -> date:
    d = date(y, month + 1, 1) - timedelta(days=1) if month < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def rule_days(country: str, y: int) -> dict[str, list[date]]:
    """Our rules for one country and year (key -> dates)."""
    e = _easter(y)
    midsummer_day = date(y, 6, 20) + timedelta(days=(5 - date(y, 6, 20).weekday()) % 7)   # Saturday 20-26 June
    out = {
        "EPIPHANY_EVE": [date(y, 1, 5)],
        "MAUNDY_THURSDAY": [e - timedelta(days=3)],
        "EASTER_SATURDAY": [e - timedelta(days=1)],
        "MAY_EVE": [date(y, 4, 30)],
        "PENTECOST_EVE": [e + timedelta(days=48)],
        "MIDSUMMER_EVE": [midsummer_day - timedelta(days=1)],
        "XMAS_EVE": [date(y, 12, 24)],
        "NYE": [date(y, 12, 31)],
        "DEC23": [date(y, 12, 23)],
        "INTERDAY": [date(y, 12, d) for d in (27, 28, 29, 30) if date(y, 12, d).weekday() != 6],
        "LUCIA": [date(y, 12, 13)],
        "BLACK_FRIDAY": [_nth_weekday(y, 11, 3, 4) + timedelta(days=1)],      # day after the 4th Thursday of Nov
        "FATHERS_DAY": [_nth_weekday(y, 11, 6, 2)],                            # 2nd Sunday of November (FI/SE/NO)
        "MOTHERS_DAY": [{"FI": _nth_weekday(y, 5, 6, 2), "SE": _last_weekday(y, 5, 6),
                         "NO": _nth_weekday(y, 2, 6, 2)}[country]],
    }
    return {k: v for k, v in out.items() if k in RULE_KEYS[country]}


# ----------------------------------------------------------------------------- public holidays (live)
@lru_cache(maxsize=64)
def public_holidays(country: str, year: int, source: str = "auto") -> tuple[tuple[date, str, str], ...]:
    """(date, english name, local name) of official public holidays. source: auto | package | nager."""
    if source in ("auto", "package"):
        try:
            import holidays
            en = holidays.country_holidays(country, years=year, language="en_US")
            loc = holidays.country_holidays(country, years=year)
            return tuple(sorted((d, n, loc.get(d, n)) for d, n in en.items()))
        except ImportError:
            if source == "package":
                raise
    import httpx
    r = httpx.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}", timeout=10)
    r.raise_for_status()
    return tuple(sorted((date.fromisoformat(h["date"]), h["name"], h["localName"]) for h in r.json()))


def special_days(country: str, start: str, end: str, source: str = "auto") -> pd.DataFrame:
    """Live special-day calendar in the dataset's format (country, date, weekday, day_key, day_class, day_type,
    name_local, name_en, official_public_holiday), plus ``origin`` (PUBLIC_HOLIDAYS_LIVE | RULE) and, for public
    holidays whose name we could not map, day_key UNMAPPED."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    rows: dict[tuple[date, str], dict] = {}
    for y in range(s.year, e.year + 1):
        for d, en, loc in public_holidays(country, y, source):
            for part, part_loc in zip(en.split("; "), (loc.split("; ") if "; " in loc else [loc] * len(en.split("; ")))):
                key = NAME_TO_KEY.get(part, "UNMAPPED")
                rows[(d, key)] = {"date": d, "day_key": key, "name_en": part, "name_local": part_loc,
                                  "official_public_holiday": 1, "origin": "PUBLIC_HOLIDAYS_LIVE"}
        official_dates = {d for d, *_ in public_holidays(country, y, source)}
        for key, ds in rule_days(country, y).items():
            for d in ds:
                if key == "INTERDAY" and d in official_dates:
                    continue
                if (d, key) not in rows:
                    rows[(d, key)] = {"date": d, "day_key": key, "name_en": NAME_EN[key], "name_local": NAME_EN[key],
                                      "official_public_holiday": int(d in official_dates), "origin": "RULE"}
    df = pd.DataFrame(rows.values())
    df = df[(df.date >= s) & (df.date <= e)].copy()
    df["country"] = country
    df["weekday"] = [d.strftime("%A") for d in df.date]
    df["day_type"] = df.day_key.map(DAY_TYPE).fillna("PUBLIC_HOLIDAY")
    df["day_class"] = df.day_key.map(DAY_CLASS).fillna("SOFT")
    df["date"] = df.date.map(date.isoformat)
    cols = ["country", "date", "weekday", "day_key", "day_class", "day_type", "name_local", "name_en",
            "official_public_holiday", "origin"]
    return df[cols].sort_values(["date", "day_key"]).reset_index(drop=True)


def live_calendar(start: str, end: str, source: str = "auto") -> pd.DataFrame:
    return pd.concat([special_days(c, start, end, source) for c in COUNTRIES], ignore_index=True)


# ----------------------------------------------------------------------------- agreement with the dataset
def agreement(start: str = "2026-01-01", end: str = "2027-01-06", source: str = "auto") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per country and day_type: rows matched (same country, date and day_key), missing (in the dataset, not live)
    and extra (live, not in the dataset); also how often the official-holiday flag agrees on matched rows.
    Returns (summary, differences)."""
    from cs.engine import data as D
    ds = D.calendar()
    ds = ds[(ds.date >= start) & (ds.date <= end)]
    lv = live_calendar(start, end, source)
    k = ["country", "date", "day_key"]
    m = ds.merge(lv, on=k, how="outer", suffixes=("_ds", "_live"), indicator=True)
    m["day_type"] = m.day_type_ds.fillna(m.day_type_live)
    m["status"] = m["_merge"].map({"both": "matched", "left_only": "missing", "right_only": "extra"})
    m["flag_agrees"] = (m.official_public_holiday_ds == m.official_public_holiday_live)
    summ = m.pivot_table(index=["country", "day_type"], columns="status", values="date", aggfunc="count", fill_value=0)
    for col in ("matched", "missing", "extra"):
        if col not in summ:
            summ[col] = 0
    flag = m[m.status == "matched"].groupby(["country", "day_type"]).flag_agrees.mean()
    summ = summ[["matched", "missing", "extra"]].join(flag.rename("official_flag_agrees")).reset_index()
    diffs = m[m.status != "matched"][["country", "date", "day_key", "day_type", "status", "name_en_ds", "name_en_live"]]
    return summ, diffs.reset_index(drop=True)


def targets_from_live(season: int = 2026, source: str = "auto") -> pd.DataFrame:
    """Rebuild the season's calendar target rows (every business x every special day of its country) from the live
    calendar. Event nights and school-break days come from other sources and are left as in the targets file."""
    from cs.engine import data as D
    a, b = D.season_window(season)
    lv = live_calendar(a, b, source)
    ents = D.entities()
    ents = ents[ents.role.isna()][["entity_id", "country"]]
    t = ents.merge(lv[["country", "date", "day_key", "day_type"]], on="country")
    return t[["entity_id", "date", "day_key", "day_type", "country"]].sort_values(["entity_id", "date"]).reset_index(drop=True)


def targets_agreement(season: int = 2026, source: str = "auto") -> dict:
    """Compare live-calendar target rows with evaluation/targets_<season>.csv (calendar days only)."""
    from cs.engine import data as D
    tg = D.targets(season)
    tg = tg[~tg.day_key.isin(["EVENT", "SCHOOL_BREAK"])]
    lv = targets_from_live(season, source)
    k = ["entity_id", "date", "day_key"]
    m = tg[k].merge(lv[k], on=k, how="outer", indicator=True)
    return {"dataset_rows": len(tg), "live_rows": len(lv), "matched": int((m._merge == "both").sum()),
            "missing_from_live": int((m._merge == "left_only").sum()), "extra_in_live": int((m._merge == "right_only").sum())}


def report(start: str = "2026-01-01", end: str = "2027-01-06") -> dict:
    """Agreement of both live sources with the dataset calendar, and of the rebuilt 2026 target rows."""
    out = {}
    for src in ("package", "nager"):
        try:
            summ, diffs = agreement(start, end, src)
        except Exception as e:  # noqa: BLE001 - network or package missing
            out[src] = {"error": f"{type(e).__name__}: {e}"}
            continue
        flag_diff = []
        if src == "nager":
            from cs.engine import data as D
            ds = D.calendar()
            ds = ds[(ds.date >= start) & (ds.date <= end)]
            lv = live_calendar(start, end, src)
            m = ds.merge(lv, on=["country", "date", "day_key"], suffixes=("_ds", "_live"))
            flag_diff = m[m.official_public_holiday_ds != m.official_public_holiday_live][
                ["country", "date", "day_key"]].to_dict("records")
        out[src] = {"by_country_and_type": summ.to_dict("records"),
                    "totals": {k: int(summ[k].sum()) for k in ("matched", "missing", "extra")},
                    "differences": diffs.to_dict("records"), "official_flag_differences": flag_diff}
    out["targets_2026"] = targets_agreement(2026, "package")
    return out


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    rep = report(*(sys.argv[1:3] if len(sys.argv) > 2 else ()))
    path = Path(__file__).resolve().parents[2] / "artifacts" / "cache" / "live_calendar_agreement.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, indent=2, default=str))
    for src in ("package", "nager"):
        print(src, rep[src].get("totals", rep[src].get("error")), "official-flag differences:",
              rep[src].get("official_flag_differences", []))
    print("targets_2026 calendar rows:", rep["targets_2026"])
