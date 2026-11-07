"""cs.guardrails: every rule on its own, then apply_guardrails."""
from __future__ import annotations

from datetime import date

import pytest

from cs import data, guardrails as G
from cs.domain import Permission, Suggestion
from cs.hours import REGULAR

AS_OF = data.SNAPSHOT[2026]


@pytest.fixture(scope="module")
def ctx():
    return G.make_context(AS_OF)


def sug(entity_id="FI-D0001", d="2026-12-24", action="SUGGEST", value="07:30-13:00", day_key="XMAS_EVE",
        shown="07:30-18:00") -> Suggestion:
    return Suggestion(entity_id=entity_id, kind="HOLIDAY_HOURS", date=d, day_key=day_key, shown=shown,
                      action=action, value=value if action == "SUGGEST" else None, basis="OWN_LAST_YEAR")


def _entity_with_entry(kind: str) -> tuple[str, str, dict]:
    """An open business with an exception entered before the 2026 snapshot (REGULAR or hours)."""
    for eid, e in data.entities().items():
        if e.get("closed") or (e.get("hours") or {}).get("reopenDate"):
            continue
        for item in (e.get("hours") or {}).get("holidayHours") or []:
            is_reg = bool(item.get("isRegularHours"))
            if (kind == "regular") == is_reg:
                return eid, item["date"], data.holiday_entries(eid, item["date"], AS_OF)[-1]
    pytest.fail(f"no entity with a {kind} entry")


def _contact(pred) -> str:
    return next(r["entity_id"] for r in data.contacts().to_dict("records") if pred(r))


# --- not_past -----------------------------------------------------------------
def test_not_past(ctx):
    assert G.not_past(sug(d="2026-11-05"), ctx)[0] is False
    assert G.not_past(sug(d=AS_OF.isoformat()), ctx) == G.OK            # today is fine
    assert G.not_past(sug(d="2026-12-24"), ctx) == G.OK
    assert G.not_past(sug(d="2026-11-05", action="NO_ACTION"), ctx) == G.OK
    later = G.make_context(AS_OF, today="2026-12-25")
    ok, reason = G.not_past(sug(d="2026-12-24", action="FLAG"), later)
    assert not ok and "before today" in reason


# --- valid_hours --------------------------------------------------------------
@pytest.mark.parametrize("value, ok", [("07:30-13:00", True), ("closed", True), ("18:00-02:00", True),
                                       ("7:30-13", False), ("", False), (None, False), ("09:00-09:00", False)])
def test_valid_hours(ctx, value, ok):
    assert G.valid_hours(sug(value=value), ctx)[0] is ok


def test_valid_hours_ignores_non_suggest(ctx):
    assert G.valid_hours(sug(action="FLAG"), ctx) == G.OK


# --- listing_active -----------------------------------------------------------
def test_listing_active(ctx):
    ok, reason = G.listing_active(sug(entity_id="FI-01794", value="10:00-13:00"), ctx)   # closed for good
    assert not ok and "closed for good" in reason
    ok, reason = G.listing_active(sug(entity_id="FI-00026", value="11:00-23:00"), ctx)   # reopenDate
    assert not ok and "temporarily closed" in reason
    assert G.listing_active(sug(entity_id="FI-00026", value="closed"), ctx) == G.OK
    assert G.listing_active(sug(), ctx) == G.OK


# --- respects_owner_entry -----------------------------------------------------
def test_respects_owner_entry_hours(ctx):
    eid, d, entry = _entity_with_entry("hours")
    other = "closed" if entry["hours"] != "closed" else "10:00-14:00"
    ok, reason = G.respects_owner_entry(sug(entity_id=eid, d=d, value=other), ctx)
    assert not ok and entry["source"] in reason
    assert G.respects_owner_entry(sug(entity_id=eid, d=d, value=entry["hours"]), ctx) == G.OK


def test_respects_owner_entry_regular(ctx):
    eid, d, entry = _entity_with_entry("regular")
    assert entry["hours"] == REGULAR
    ok, reason = G.respects_owner_entry(sug(entity_id=eid, d=d, value="closed", shown="09:00-17:00"), ctx)
    assert not ok and "regular hours" in reason


def test_respects_owner_entry_without_entry(ctx):
    assert G.respects_owner_entry(sug(), ctx) == G.OK   # FI-D0001 has nothing entered for 2026-12-24


def test_respects_owner_entry_ignores_entries_after_snapshot():
    """An entry made on/after as_of is invisible, so it cannot block (FI-00154, entered 2025-12-17)."""
    early = G.make_context("2025-12-17")
    assert G.respects_owner_entry(sug(entity_id="FI-00154", d="2025-12-26", value="10:00-14:00"), early) == G.OK
    late = G.make_context("2025-12-18")
    assert G.respects_owner_entry(sug(entity_id="FI-00154", d="2025-12-26", value="10:00-14:00"), late)[0] is False


# --- flag_not_message ---------------------------------------------------------
def test_flag_not_message(ctx):
    assert G.flag_not_message(sug(action="FLAG"), ctx)[0] is False
    assert G.flag_not_message(sug(), ctx) == G.OK


# --- contact_allowed ----------------------------------------------------------
def test_contact_allowed(ctx):
    opted = _contact(lambda r: r["opted_out_of_suggestions"] == 1)
    ok, reason = G.contact_allowed(sug(entity_id=opted), ctx)
    assert not ok and "opted out" in reason
    fine = _contact(lambda r: r["opted_out_of_suggestions"] == 0)
    assert G.contact_allowed(sug(entity_id=fine), ctx) == G.OK
    assert G.contact_allowed(sug(entity_id="XX-NONE"), ctx)[0] is False


# --- permission_scope ---------------------------------------------------------
def test_permission_scope_autopilot_rule():
    """Apply a standing permission only if this season's engine gives the permitted hours P >= 0.5."""
    p = Permission(entity_id="FI-D0012", scope={"day_keys": ["XMAS_EVE"], "values": {"XMAS_EVE": "08:00-12:00"}})
    ctx = G.make_context(AS_OF, permissions={"FI-D0012": [p]})
    s = sug(entity_id="FI-D0012", value="08:00-12:00")
    s.probability = 0.8
    ok, reason = G.permission_scope(s, ctx)
    assert ok and p.id in reason and "P=0.80" in reason
    s.probability = 0.3                                                   # engine doubts it -> ask
    ok, reason = G.permission_scope(s, ctx)
    assert not ok and "P=0.30" in reason and "ask on the card" in reason
    s.probability = None                                                  # no engine view -> ask
    assert G.permission_scope(s, ctx)[0] is False
    other = sug(entity_id="FI-D0012", value="08:00-13:00")                # value differs: use candidates
    other.probability, other.candidates = 0.7, [{"hours": "08:00-12:00", "prob": 0.55, "basis": "OWNER_ANSWER"}]
    assert G.permission_scope(other, ctx)[0] is True
    other.candidates = [{"hours": "08:00-12:00", "prob": 0.2, "basis": "OWNER_ANSWER"}]
    assert G.permission_scope(other, ctx)[0] is False
    nye = sug(entity_id="FI-D0012", value="08:00-12:00", day_key="NYE")
    nye.probability = 0.9
    assert G.permission_scope(nye, ctx)[0] is False                       # not in scope
    assert G.permission_scope(sug(entity_id="FI-D0012", action="FLAG"), ctx)[0] is False
    revoked = Permission(entity_id="FI-D0012", scope=p.scope, status="REVOKED", revoked_reason="STOP")
    assert G.permission_scope(s, G.make_context(AS_OF, permissions={"FI-D0012": [revoked]}))[0] is False


def test_apply_logs_why_autopilot_did_not_apply():
    p = Permission(entity_id="FI-D0001", scope={"day_keys": ["XMAS_EVE"], "values": {"XMAS_EVE": "07:30-13:00"}})
    s = sug()
    s.probability = 0.2
    out = G.apply_guardrails(s, G.make_context(AS_OF, permissions={"FI-D0001": [p]}))
    assert out.auto_apply_ok is False and out.action == "SUGGEST" and out.message_ok is True
    assert any(n.startswith("permission_scope: standing permission") and "P=0.20" in n for n in out.guardrail_notes)


def test_permission_scope_crm_flag_is_not_a_scope(ctx):
    eid = _contact(lambda r: r["standing_permission_holidays"] == 1)
    ok, reason = G.permission_scope(sug(entity_id=eid), ctx)
    assert not ok and "CRM" in reason


# --- apply_guardrails ---------------------------------------------------------
def test_apply_does_not_mutate_and_passes_clean_rows(ctx):
    s = sug()
    out = G.apply_guardrails(s, ctx)
    assert s.guardrail_notes == [] and s.message_ok is None
    assert out.action == "SUGGEST" and out.value == "07:30-13:00" and out.original_action is None
    assert out.message_ok is True and out.auto_apply_ok is False and out.guardrail_notes == []


def test_apply_downgrades_with_reason(ctx):
    out = G.apply_guardrails(sug(entity_id="FI-01794", value="10:00-13:00"), ctx)
    assert out.action == "NO_ACTION" and out.value is None and out.original_action == "SUGGEST"
    assert out.message_ok is False
    assert len(out.guardrail_notes) == 1 and out.guardrail_notes[0].startswith("listing_active:")


def test_apply_invalid_hours_becomes_flag(ctx):
    out = G.apply_guardrails(sug(value="9-13"), ctx)
    assert out.action == "FLAG" and out.original_action == "SUGGEST"
    assert out.guardrail_notes[0].startswith("valid_hours:") and out.message_ok is False


def test_apply_first_failing_rule_decides(ctx):
    out = G.apply_guardrails(sug(entity_id="FI-01794", d="2026-11-01", value="nonsense"), ctx)
    assert out.guardrail_notes[0].startswith("not_past:") and out.action == "NO_ACTION"


def test_apply_opt_out_keeps_the_row_for_the_csv(ctx):
    opted = _contact(lambda r: r["opted_out_of_suggestions"] == 1)
    out = G.apply_guardrails(sug(entity_id=opted), ctx)
    assert out.action == "SUGGEST" and out.message_ok is False
    assert any(n.startswith("contact_allowed:") for n in out.guardrail_notes)


def test_apply_flag_is_never_messageable(ctx):
    out = G.apply_guardrails(sug(action="FLAG"), ctx)
    assert out.action == "FLAG" and out.message_ok is False
    assert any(n.startswith("flag_not_message:") for n in out.guardrail_notes)


def test_apply_marks_auto_apply_when_permitted():
    p = Permission(entity_id="FI-D0001", scope={"day_keys": ["XMAS_EVE"], "values": {"XMAS_EVE": "07:30-13:00"}})
    s = sug()
    s.probability = 0.9
    out = G.apply_guardrails(s, G.make_context(AS_OF, permissions={"FI-D0001": [p]}))
    assert out.auto_apply_ok is True and any(n.startswith("permission_scope:") for n in out.guardrail_notes)


def test_context_today_defaults_to_snapshot():
    c = G.make_context("2025-11-06")
    assert c.today == date(2025, 11, 6) and c.as_of == date(2025, 11, 6)
