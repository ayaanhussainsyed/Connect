"""A4 hard rules + A10: pure guardrail functions, each ``(suggestion, ctx) -> (ok, reason)``.

Three kinds of rule:

| Kind | Rules | Effect when a rule fails |
|---|---|---|
| Suggestion rules | ``not_past``, ``valid_hours``, ``listing_active``, ``respects_owner_entry`` | the action is downgraded (SUGGEST -> NO_ACTION, or FLAG for invalid hours); this changes the CSV |
| Messaging rules | ``flag_not_message``, ``contact_allowed`` | ``message_ok = False``; the CSV keeps the row |
| Apply rule | ``permission_scope`` | ``auto_apply_ok`` (stub: only an explicit scoped Permission counts) |

``apply_guardrails`` never mutates its input; it returns a copy with ``guardrail_notes``
("rule: reason"), ``original_action`` (if downgraded), ``message_ok`` and ``auto_apply_ok``.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from cs import data, display
from cs.domain import Permission, Suggestion
from cs.hours import REGULAR, is_valid

log = logging.getLogger(__name__)

Result = tuple[bool, str]
OK: Result = (True, "")


@dataclass
class GuardContext:
    """Everything the rules may look at. ``today`` defaults to the snapshot day."""
    as_of: date
    today: date
    contacts: dict[str, dict] = field(default_factory=dict)        # entity_id -> customer_contacts row
    permissions: dict[str, list[Permission]] = field(default_factory=dict)

    def owner_entry(self, entity_id: str, d: str) -> dict | None:
        """Latest exception entered for that date before the snapshot (any source), or None."""
        entries = data.holiday_entries(entity_id, d, self.as_of)
        return entries[-1] if entries else None

    def shown_explanation(self, entity_id: str, d: str) -> dict:
        return display.explain_shown(entity_id, d, self.as_of)


@lru_cache(maxsize=1)
def _contacts_by_entity() -> dict[str, dict]:
    return {r["entity_id"]: r for r in data.contacts().to_dict("records")}


def make_context(as_of: date | str = data.DEFAULT_AS_OF, today: date | str | None = None,
                 permissions: dict[str, list[Permission]] | None = None) -> GuardContext:
    as_of = data.to_date(as_of)
    return GuardContext(as_of=as_of, today=data.to_date(today) if today else as_of,
                        contacts=_contacts_by_entity(), permissions=permissions or {})


# ---------------------------------------------------------------------------
# Suggestion rules (change the action)
# ---------------------------------------------------------------------------
def not_past(s: Suggestion, ctx: GuardContext) -> Result:
    """Never act on a date before today."""
    if s.action == "NO_ACTION" or not s.date:
        return OK
    if data.to_date(s.date) < ctx.today:
        return False, f"{s.date} is before today ({ctx.today.isoformat()})"
    return OK


def valid_hours(s: Suggestion, ctx: GuardContext) -> Result:
    """A SUGGEST for hours must carry hours that pass the validator."""
    if s.action != "SUGGEST" or s.kind not in ("HOLIDAY_HOURS",):
        return OK
    if not is_valid(s.value or ""):
        return False, f"invalid hours {s.value!r}"
    return OK


def listing_active(s: Suggestion, ctx: GuardContext) -> Result:
    """Never suggest hours for a listing the owner already marked closed for good, or
    temporarily closed (reopenDate) on that date: Google already shows closed."""
    if s.action != "SUGGEST" or not s.date:
        return OK
    ex = ctx.shown_explanation(s.entity_id, s.date)
    if ex["source"] == display.CLOSED_FOR_GOOD:
        return False, "the listing is marked closed for good"
    if ex["source"] == display.TEMPORARILY_CLOSED and s.value != "closed":
        return False, f"the listing is temporarily closed until {ex['reopen_date']}"
    return OK


def respects_owner_entry(s: Suggestion, ctx: GuardContext) -> Result:
    """Never suggest against an exception already entered for that date (owner, support,
    an approved Connect suggestion or the centre's integration)."""
    if s.action != "SUGGEST" or not s.date:
        return OK
    entry = ctx.owner_entry(s.entity_id, s.date)
    if entry is None:
        return OK
    entered = s.shown if entry["hours"] == REGULAR else entry["hours"]
    if s.value != entered:
        what = "regular hours" if entry["hours"] == REGULAR else entry["hours"]
        return False, (f"{entry['source']} entered {what} for {s.date} on {entry['entered_at'][:10]}; "
                       f"we don't suggest against it")
    return OK


# ---------------------------------------------------------------------------
# Messaging rules (never change the CSV)
# ---------------------------------------------------------------------------
def flag_not_message(s: Suggestion, ctx: GuardContext) -> Result:
    """FLAG rows go to a human, never into an owner message."""
    if s.action == "FLAG":
        return False, "flagged for human review; not sent to the owner"
    return OK


def contact_allowed(s: Suggestion, ctx: GuardContext) -> Result:
    """No message to owners who opted out of suggestions (messaging only)."""
    c = ctx.contacts.get(s.entity_id)
    if c is None:
        return False, "no contact record"
    if int(c.get("opted_out_of_suggestions") or 0):
        return False, "owner opted out of suggestions (the CSV keeps the row)"
    return OK


# ---------------------------------------------------------------------------
# Apply rule
# ---------------------------------------------------------------------------
AUTOPILOT_MIN_P = 0.5   # ML findings: blind re-application is wrong on open/closed 10.1%, with this check 3.5%


def engine_p(s: Suggestion, hours: str) -> float | None:
    """This season's engine probability for exactly ``hours`` on this date: the SUGGEST probability
    when the value matches, else the matching candidate's probability, 0.0 if the engine ranked
    candidates and these hours are not among them; None only if the engine is silent (baseline)."""
    if s.value == hours and s.probability is not None:
        return float(s.probability)
    for c in s.candidates or []:
        if c.get("hours") == hours and c.get("prob") is not None:
            return float(c["prob"])
    return 0.0 if s.candidates else None


def permission_scope(s: Suggestion, ctx: GuardContext) -> Result:
    """Automatic apply only if a standing permission covers this holiday, **and** this season's
    engine still gives the permitted hours P >= 0.5 (``AUTOPILOT_MIN_P``). Otherwise the date goes on
    the card and the owner is asked; the reason is returned (and logged by ``apply_guardrails``).

    Only explicit, scoped ``Permission`` objects in ``ctx.permissions`` count
    (``scope = {"day_keys": [...], "values": {day_key: hours}}``); the CRM flag
    ``standing_permission_holidays`` alone is not a scope.
    """
    if s.action != "SUGGEST" or not s.day_key:
        return False, "nothing to apply"
    for p in ctx.permissions.get(s.entity_id, []):
        if p.status not in ("GRANTED", "USED"):
            continue
        scope = p.scope or {}
        permitted = (scope.get("values") or {}).get(s.day_key)
        if s.day_key not in (scope.get("day_keys") or []) or permitted is None:
            continue
        prob = engine_p(s, permitted)
        if prob is None:
            return False, (f"standing permission {p.id} covers {s.day_key} = {permitted}, but this season's engine "
                           f"gives no probability for it: ask on the card")
        if prob < AUTOPILOT_MIN_P:
            return False, (f"standing permission {p.id} covers {s.day_key} = {permitted}, but this season's engine "
                           f"gives it P={prob:.2f} < {AUTOPILOT_MIN_P}: ask on the card")
        return True, f"standing permission {p.id} covers {s.day_key} = {permitted} (engine P={prob:.2f})"
    c = ctx.contacts.get(s.entity_id) or {}
    hint = " (CRM says standing permission, but no scoped permission yet)" if int(
        c.get("standing_permission_holidays") or 0) else ""
    return False, f"no standing permission covers {s.day_key} = {s.value}{hint}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
Rule = Callable[[Suggestion, GuardContext], Result]

# (rule, action to downgrade to). Order matters: the first failing rule decides.
SUGGESTION_RULES: tuple[tuple[Rule, str], ...] = (
    (not_past, "NO_ACTION"),
    (valid_hours, "FLAG"),
    (listing_active, "NO_ACTION"),
    (respects_owner_entry, "NO_ACTION"),
)
MESSAGING_RULES: tuple[Rule, ...] = (flag_not_message, contact_allowed)
ALL_RULES = tuple(r for r, _ in SUGGESTION_RULES) + MESSAGING_RULES + (permission_scope,)


def apply_guardrails(s: Suggestion, ctx: GuardContext) -> Suggestion:
    """Run every rule and return a checked copy of the suggestion (see module doc)."""
    out = dataclasses.replace(s, guardrail_notes=list(s.guardrail_notes), evidence=list(s.evidence))
    for rule, downgrade_to in SUGGESTION_RULES:
        ok, reason = rule(out, ctx)
        if not ok:
            out.guardrail_notes.append(f"{rule.__name__}: {reason}")
            if out.original_action is None:
                out.original_action = out.action
            log.info("guardrail %s downgraded %s %s %s -> %s: %s", rule.__name__, out.entity_id, out.date,
                     out.action, downgrade_to, reason)
            out.action = downgrade_to
            if downgrade_to != "SUGGEST":
                out.value = None
            break
    message_ok = out.action == "SUGGEST"
    for rule in MESSAGING_RULES:
        ok, reason = rule(out, ctx)
        if not ok:
            message_ok = False
            if out.action != "NO_ACTION":
                out.guardrail_notes.append(f"{rule.__name__}: {reason}")
    out.message_ok = message_ok
    ok, reason = permission_scope(out, ctx)
    out.auto_apply_ok = ok
    if ok or reason.startswith("standing permission"):
        out.guardrail_notes.append(f"permission_scope: {reason}")
        if not ok:
            log.info("autopilot: %s %s not applied: %s", out.entity_id, out.date, reason)
    return out
