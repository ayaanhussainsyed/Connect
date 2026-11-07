"""Plain dataclasses shared by every part of the app (BUILD_PLAN_A §0, APP_DESIGN §4.1).

Each object converts to a MongoDB document with ``to_doc()`` (dates become ISO strings,
datetimes stay datetimes, nested dataclasses become dicts) and back with ``from_doc()``.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from typing import Any, ClassVar


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def suggestion_id(model_version: str, entity_id: str, date: str | None, kind: str = "HOLIDAY_HOURS") -> str:
    """Stable id for a suggestion row, so re-running an export replaces it (cards keep their links)."""
    return f"sug:{model_version}:{kind}:{entity_id}:{date or '-'}"


def utcnow() -> datetime:
    """Timezone-aware UTC now, truncated to milliseconds (what MongoDB stores)."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------
EVIDENCE_SOURCES = ("OWN_LAST_YEAR", "OWN_2_SEASONS", "LINKED_HOLIDAY", "MALL", "PEERS",
                    "REVIEW", "USER_EDIT", "OWNER_ANSWER", "MODEL")
# Suggestion.basis: the main reason. The ML engine uses EVIDENCE_SOURCES names plus MODEL;
# the baseline adds CLOSED_SIGNALS (reviews / Google edits say closed for good).
BASES = EVIDENCE_SOURCES + ("CLOSED_SIGNALS",)
SUGGESTION_KINDS = ("HOLIDAY_HOURS", "REGULAR_HOURS", "REVIEW_REPLY", "CLOSED_FOR_GOOD")
ACTIONS = ("SUGGEST", "NO_ACTION", "FLAG")
DECISIONS = ("APPROVE", "EDIT", "DECLINE", "NO_RESPONSE")
WRITE_TARGETS = ("MOCK", "SANDBOX")

# Card life cycle (APP_DESIGN §4.1)
CARD_STATES = ("DRAFT", "SCHEDULED", "SENT", "QUESTION_ANSWERED", "DECIDED", "APPLIED",
               "CONFIRMED", "NO_REPLY", "REMINDED", "EXPIRED", "UNDONE")
CARD_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("SCHEDULED", "SENT"),
    "SCHEDULED": ("SENT",),
    "SENT": ("QUESTION_ANSWERED", "DECIDED", "NO_REPLY"),
    "QUESTION_ANSWERED": ("DECIDED", "NO_REPLY"),
    "NO_REPLY": ("REMINDED", "EXPIRED", "QUESTION_ANSWERED", "DECIDED"),   # a late answer still counts
    "REMINDED": ("QUESTION_ANSWERED", "DECIDED", "EXPIRED"),
    "DECIDED": ("APPLIED",),
    "APPLIED": ("CONFIRMED", "UNDONE"),
    "CONFIRMED": ("UNDONE",),
    "EXPIRED": (),
    "UNDONE": (),
}

# Standing permission life cycle: NONE -> GRANTED(scope) -> USED … -> REVOKED
PERMISSION_STATES = ("NONE", "GRANTED", "USED", "REVOKED")
PERMISSION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "NONE": ("GRANTED",),
    "GRANTED": ("USED", "REVOKED"),
    "USED": ("USED", "REVOKED"),
    "REVOKED": (),
}
REVOKE_REASONS = ("UNDO", "STOP", "OPT_OUT")


def can_transition(table: dict[str, tuple[str, ...]], current: str, new: str) -> bool:
    return new in table.get(current, ())


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _to_jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v  # pymongo stores datetimes natively
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


class _Doc:
    collection: ClassVar[str] = ""
    _nested: ClassVar[dict[str, type]] = {}

    def to_doc(self) -> dict:
        return _to_jsonable(asdict(self))  # type: ignore[arg-type]

    @classmethod
    def from_doc(cls, doc: dict):
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        kw = {k: v for k, v in doc.items() if k in names}
        for name, typ in cls._nested.items():
            if name in kw and kw[name] is not None:
                kw[name] = [typ.from_doc(x) if isinstance(x, dict) else x for x in kw[name]]
        return cls(**kw)


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------
@dataclass
class Evidence(_Doc):
    source: str              # one of EVIDENCE_SOURCES
    detail: str              # human-readable, e.g. "2025-12-24 you entered 07:30-13:00"
    ref: str | None = None   # dataset row id / key, for the audit and "why?"
    weight: float = 1.0


@dataclass
class Suggestion(_Doc):
    collection: ClassVar[str] = "suggestions"
    _nested: ClassVar[dict[str, type]] = {"evidence": Evidence}

    entity_id: str
    kind: str                       # one of SUGGESTION_KINDS
    date: str | None                # ISO date (None for REGULAR_HOURS / REVIEW_REPLY)
    day_key: str | None
    shown: str | None               # what Google shows now (cs.display)
    action: str                     # SUGGEST | NO_ACTION | FLAG
    value: str | None = None        # hours string, or draft text for REVIEW_REPLY
    probability: float | None = None
    basis: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    model_version: str = "baseline"
    season: int | None = None
    candidates: list[dict] = field(default_factory=list)   # [{hours, prob, basis}] (ML engine)
    p_closed_for_good: float | None = None
    # Set by cs.guardrails.apply_guardrails:
    guardrail_notes: list[str] = field(default_factory=list)  # "rule: reason", one per failed rule
    original_action: str | None = None   # action before a guardrail downgraded it (None = unchanged)
    message_ok: bool | None = None       # may this row go into an owner message? (FLAG / opt-out -> False)
    auto_apply_ok: bool | None = None    # covered by a standing permission? (permission_scope)
    id: str = field(default_factory=lambda: new_id("sug"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> tuple[str, str | None, str]:
        """Upsert key in MongoDB: (entity_id, date, model_version)."""
        return (self.entity_id, self.date, self.model_version)


@dataclass
class Card(_Doc):
    collection: ClassVar[str] = "cards"

    entity_id: str
    season: int
    suggestion_ids: list[str] = field(default_factory=list)
    deadline_by_publisher: dict[str, str] = field(default_factory=dict)  # publisherId -> ISO datetime
    question: dict | None = None
    status: str = "DRAFT"           # one of CARD_STATES
    # One item per date: {suggestion_id, date, day_key, shown, value, basis, probability, evidence,
    # flip, flip_tick_required, deadline}. Built by cs.season.build_card.
    items: list[dict] = field(default_factory=list)
    expected_current: dict[str, str | None] = field(default_factory=dict)  # live holidayHours at build time
    approve_by: str | None = None   # ISO date: answer by this day so every live publisher is in time
    target: str = "mock"            # Yext target the decision writes to: mock | sandbox
    engine: str = "baseline"
    owner_id: str = ""
    lang: str = "fi"
    channel: str = "OUTBOX"
    history: list[dict] = field(default_factory=list)   # [{from, to, at, reason}]
    batch_ids: list[str] = field(default_factory=list)  # decision batches (one per decide call)
    dates_shown: list[str] | None = None   # dates actually in the (possibly shortened) message text
    pending_reply: dict | None = None      # a free-text edit waiting for the owner's "yes" (read-back)
    result: dict | None = None      # the last confirmation (shown again when the link is reopened)
    id: str = field(default_factory=lambda: new_id("card"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Message(_Doc):
    collection: ClassVar[str] = "messages"

    card_id: str
    channel: str                    # OUTBOX | EMAIL | SMS | WHATSAPP | APP_PUSH
    language: str                   # fi | sv | nb | en
    text: str
    buttons: list[dict] = field(default_factory=list)
    token: str | None = None
    sent_at: datetime | None = None
    entity_id: str | None = None
    kind: str = "CARD"              # CARD | CONFIRMATION | FOLLOWUP | REPLY
    direction: str = "OUT"          # OUT (to the owner) | IN (the owner's reply)
    composer: str = ""              # cs.compose | fallback
    id: str = field(default_factory=lambda: new_id("msg"))


@dataclass
class Decision(_Doc):
    collection: ClassVar[str] = "decisions"

    card_id: str
    suggestion_id: str
    decision: str                   # APPROVE | EDIT | DECLINE | NO_RESPONSE
    final_value: str | None = None
    every_year: bool = False
    via: str = "web"                # web | whatsapp | sms | email | sim
    entity_id: str | None = None
    date: str | None = None
    batch_id: str | None = None     # all decisions from one submit share it (= WriteRecord decision_id)
    status: str = "RECORDED"        # RECORDED | APPLIED | CANCELLED | BLOCKED | DECLINED | UNDONE
    note: str = ""
    write_id: str | None = None
    undone_at: datetime | None = None
    at: datetime = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("dec"))


@dataclass
class WriteRecord(_Doc):
    collection: ClassVar[str] = "writes"

    entity_id: str
    target: str                     # MOCK | SANDBOX
    request: dict
    response_status: int | None = None
    readback_ok: bool | None = None
    note: str = ""
    at: datetime = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("wr"))


@dataclass
class Permission(_Doc):
    """A standing permission ("every year") with an explicit scope.

    ``scope`` example: ``{"day_keys": ["XMAS_EVE"], "values": {"XMAS_EVE": "07:30-13:00"},
    "min_days_ahead": 14}``. An owner's edit updates ``scope["values"]`` instead of ending it.
    """
    collection: ClassVar[str] = "permissions"

    entity_id: str
    scope: dict
    status: str = "GRANTED"         # one of PERMISSION_STATES
    granted_via: str = "web"
    source_decision_id: str | None = None   # the decision batch id
    source: str = "OWNER"           # OWNER (Approve + every year) | CRM_2025 | TIME_MACHINE_2025 (demo only)
    uses: list[dict] = field(default_factory=list)   # {date, value, write_id, at}
    revoked_reason: str | None = None                # UNDO | STOP | OPT_OUT
    revoked_at: datetime | None = None
    granted_at: datetime = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("perm"))


@dataclass
class LearnedFact(_Doc):
    """Something an owner told us (approve / edit), kept as evidence for later seasons.

    A newer fact for the same entity and day_key supersedes the old one (``superseded_by``).
    """
    collection: ClassVar[str] = "learned_facts"

    entity_id: str
    day_key: str
    date: str                       # ISO date the answer was for
    value: str                      # hours string
    season: int
    source: str = "OWNER_ANSWER"
    superseded_by: str | None = None
    batch_id: str | None = None     # the decision batch it came from (undo reverts it)
    created_at: datetime = field(default_factory=utcnow)
    id: str = field(default_factory=lambda: new_id("fact"))


DOMAIN_TYPES: tuple[type, ...] = (Suggestion, Card, Message, Decision, WriteRecord, Permission, LearnedFact)
