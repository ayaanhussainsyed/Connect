"""The whole-profile features (A13) and Ask Connect (B10), wired into the owner's conversation.

Built before as library + CLI only (``cs.profile.drift``, ``cs.profile.review_reply``, ``cs.kg``);
this module puts them in the same thread as the season card, on the owner's own channel:

    offer_drift(entity_id)            "Your weekly hours look like they've changed" + Approve / Edit / No thanks
    offer_review_replies(entity_id)   "Reply to the customer who found you closed?" + Approve / Edit / No thanks
    handle_reply(entity_id, card_id, button_id=, text=)   the owner's answer to either offer
    is_question(text) / answer_question(entity_id, text)  "Why do you think we close on the 26th?" -> Ask Connect

Offers are stored in Mongo ``profile_offers`` (one open offer per business and kind, so a second
"Check" never sends the same message twice). Replies carry the card id ``drift:<id>`` or ``rr:<id>``
(buttons, SMS digits and WhatsApp payloads all route here from ``cs.decide_flow.handle_reply``).
Owner wins: ``cs.profile.drift.apply_drift`` re-reads the live week before writing.
"""
from __future__ import annotations

import re

from pymongo.database import Database

from cs import audit, compose_bridge, data
from cs import db as dbmod
from cs.domain import Suggestion, new_id, utcnow

COLLECTION = "profile_offers"
DRIFT, REVIEW_REPLY = "DRIFT", "REVIEW_REPLY"
PREFIX = {DRIFT: "drift", REVIEW_REPLY: "rr"}

T = {
    "fi": {"drift_done": "Valmis: uudet viikoittaiset aukioloaikanne näkyvät Googlessa ja muissa palveluissa.",
           "drift_cancel": "Emme muuttaneet mitään: aikoja oli jo muutettu sen jälkeen, kun kysyimme.",
           "drift_fail": "Emme saaneet päivitettyä aikoja juuri nyt. Yritämme uudelleen.",
           "rr_done": "Valmis: vastauksenne on tallennettu ja julkaistaan arvion alle.",
           "declined": "Selvä, emme muuta mitään.",
           "edit": "Kirjoittakaa oikeat tiedot omin sanoin, niin tarkistamme ne ennen kuin mitään muuttuu.",
           "edit_saved": "Kiitos! Tarkistamme tämän ennen kuin mitään muuttuu.",
           "dont_know": "En löydä tähän vastausta tiedoistanne."},
    "sv": {"drift_done": "Klart: era nya ordinarie öppettider visas på Google och i andra tjänster.",
           "drift_cancel": "Vi ändrade inget: tiderna hade redan ändrats sedan vi frågade.",
           "drift_fail": "Vi kunde inte uppdatera tiderna just nu. Vi försöker igen.",
           "rr_done": "Klart: ert svar är sparat och publiceras under recensionen.",
           "declined": "Okej, vi ändrar ingenting.",
           "edit": "Skriv de rätta uppgifterna med egna ord, så kontrollerar vi dem innan något ändras.",
           "edit_saved": "Tack! Vi kontrollerar detta innan något ändras.",
           "dont_know": "Jag hittar inget svar på det i era uppgifter."},
    "nb": {"drift_done": "Ferdig: de nye faste åpningstidene deres vises på Google og i andre tjenester.",
           "drift_cancel": "Vi endret ingenting: tidene var allerede endret etter at vi spurte.",
           "drift_fail": "Vi fikk ikke oppdatert tidene akkurat nå. Vi prøver igjen.",
           "rr_done": "Ferdig: svaret deres er lagret og publiseres under anmeldelsen.",
           "declined": "Greit, vi endrer ingenting.",
           "edit": "Skriv de riktige opplysningene med egne ord, så sjekker vi dem før noe endres.",
           "edit_saved": "Takk! Vi sjekker dette før noe endres.",
           "dont_know": "Jeg finner ikke noe svar på det i opplysningene deres."},
    "en": {"drift_done": "Done: your new weekly hours show on Google and the other apps.",
           "drift_cancel": "Nothing changed: the hours had already been changed since we asked.",
           "drift_fail": "We couldn't update the hours just now. We'll try again.",
           "rr_done": "Done: your reply is saved and goes up under the review.",
           "declined": "OK, we won't change anything.",
           "edit": "Tell us the right details in your own words, and we'll check them before anything changes.",
           "edit_saved": "Thanks! We'll check this before anything changes.",
           "dont_know": "I can't find an answer to that in your details."},
}
# Words that start a question in the owner's languages (a reply like "ok?" or "kiinni 26.12.?" is not one).
QUESTION_WORDS = (
    "why", "what", "when", "where", "which", "who", "how",
    "miksi", "mitä", "mikä", "milloin", "missä", "miten", "kuinka", "minkä",
    "varför", "vad", "när", "var", "vilka", "vilken", "hur",
    "hvorfor", "hva", "når", "hvor", "hvilke", "hvilken", "hvordan",
)
_FIRST_WORD = re.compile(r"^\W*(\w+)", re.UNICODE)


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def _lang(entity_id: str) -> str:
    lang = compose_bridge.contact(entity_id).get("lang") or "en"
    return lang if lang in T else "en"


def _say(entity_id: str, text: str, kind: str, card_id: str | None, db: Database) -> dict:
    c = compose_bridge.contact(entity_id)
    return compose_bridge.send(entity_id, {"text": text, "buttons": [], "parts": {}, "composer": "cs.profile_flow"},
                               kind=kind, card_id=card_id, lang=c["lang"], channel=c["channel"], db=db)


def open_offer(entity_id: str, kind: str, db: Database | None = None) -> dict | None:
    return _db(db)[COLLECTION].find_one({"entity_id": entity_id, "kind": kind, "status": "OPEN"}, {"_id": 0},
                                        sort=[("created_at", -1)])


def _business(entity_id: str) -> dict:
    ent = data.entity(entity_id)
    return {"name": ent.get("name", entity_id)}


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------
ANSWERED = ("APPLIED", "DECLINED", "CANCELLED", "EDIT_ASKED", "EDIT_RECEIVED")   # DEMO_RESET does not count


def answered(entity_id: str, kind: str, db: Database | None = None) -> bool:
    return _db(db)[COLLECTION].find_one({"entity_id": entity_id, "kind": kind, "status": {"$in": list(ANSWERED)}},
                                        {"_id": 1}) is not None


def handled_review_ids(entity_id: str, db: Database | None = None) -> set[str]:
    """Reviews the owner already replied to, or chose not to reply to, through Connect."""
    from cs.profile import review_reply as rr
    db = _db(db)
    ids = {r["review_id"] for r in db[rr.COLLECTION].find({"entity_id": entity_id, "status": {"$ne": "DEMO_RESET"}},
                                                          {"review_id": 1}) if r.get("review_id")}
    for o in db[COLLECTION].find({"entity_id": entity_id, "kind": REVIEW_REPLY, "status": {"$in": list(ANSWERED)}},
                                 {"drafts": 1}):
        for d in o.get("drafts", []):
            rid = rr.review_id_of(Suggestion.from_doc(d))
            if rid:
                ids.add(rid)
    return ids


def reset_offers(entity_id: str, db: Database | None = None) -> int:
    """Demo reset only: forget the owner's answers to these offers (the audit log keeps them)."""
    from cs.profile import review_reply as rr
    db = _db(db)
    n = db[COLLECTION].update_many({"entity_id": entity_id, "status": {"$ne": "DEMO_RESET"}},
                                   {"$set": {"status": "DEMO_RESET", "updated_at": utcnow()}}).modified_count
    db[rr.COLLECTION].update_many({"entity_id": entity_id}, {"$set": {"status": "DEMO_RESET"}})
    return n


def offer_drift(entity_id: str, db: Database | None = None) -> dict | None:
    """Ask about weekly hours that changed (once while the offer is open). None if there is no drift."""
    from cs.profile import drift
    db = _db(db)
    s = drift.drift_suggestion(entity_id)
    if s is None:
        return None
    changes = drift.changed_days(drift.week_of(s.shown), drift.week_of(s.value))
    summary = (f"The weekly hours look out of date on {len(changes)} line(s) of the week "
               "(from customer reviews): the owner is asked about those too.")
    existing = open_offer(entity_id, DRIFT, db)
    if existing:
        return {"summary": summary, "offer_id": existing["id"], "sent": False}
    if answered(entity_id, DRIFT, db) or compose_bridge.contact(entity_id).get("opted_out"):
        return None                                   # the owner already answered: a "no" means no
    c = compose_bridge.contact(entity_id)
    offer_id = new_id("pof")
    composed = drift.compose_drift_message(_business(entity_id), s, c["lang"], c["channel"])
    composed["composer"] = "cs.profile.drift"
    sent = compose_bridge.send(entity_id, composed, kind="DRIFT", card_id=f"drift:{offer_id}", lang=c["lang"],
                               channel=c["channel"], db=db)
    db[COLLECTION].insert_one({"id": offer_id, "entity_id": entity_id, "kind": DRIFT, "status": "OPEN",
                               "suggestion": s.to_doc(), "message_id": sent.get("message_id"),
                               "created_at": utcnow()})
    audit.log("PROFILE_OFFER", {"offer_id": offer_id, "kind": DRIFT, "changes": len(changes),
                                "summary": summary}, entity_id=entity_id, db=db)
    return {"summary": summary, "offer_id": offer_id, "sent": True}


def offer_review_replies(entity_id: str, db: Database | None = None) -> dict | None:
    """Offer a reply to unanswered "it was closed" reviews (once while open). None if there are none."""
    from cs.profile import review_reply as rr
    db = _db(db)
    existing = open_offer(entity_id, REVIEW_REPLY, db)
    if existing:
        return {"summary": existing.get("summary", ""), "offer_id": existing["id"], "sent": False}
    if compose_bridge.contact(entity_id).get("opted_out"):
        return None
    c = compose_bridge.contact(entity_id)
    done = handled_review_ids(entity_id, db)          # replied, or the owner said no: never offered again
    drafts = [d for d in rr.review_reply_drafts(entity_id, c["lang"]) if rr.review_id_of(d["suggestion"]) not in done]
    if not drafts:
        return None
    shown = drafts[:2]
    offer_id = new_id("pof")
    composed = rr.compose_review_reply_offer(_business(entity_id), shown, c["lang"], c["channel"])
    composed["composer"] = "cs.profile.review_reply"
    summary = f"{len(drafts)} customer review(s) say the door was closed: offered the owner a reply."
    sent = compose_bridge.send(entity_id, composed, kind="REVIEW_REPLY", card_id=f"rr:{offer_id}",
                               lang=c["lang"], channel=c["channel"], db=db)
    db[COLLECTION].insert_one({"id": offer_id, "entity_id": entity_id, "kind": REVIEW_REPLY, "status": "OPEN",
                               "drafts": [d["suggestion"].to_doc() for d in shown], "summary": summary,
                               "message_id": sent.get("message_id"), "created_at": utcnow()})
    audit.log("PROFILE_OFFER", {"offer_id": offer_id, "kind": REVIEW_REPLY, "drafts": len(shown),
                                "summary": summary}, entity_id=entity_id, db=db)
    return {"summary": summary, "offer_id": offer_id, "sent": True}


# ---------------------------------------------------------------------------
# The owner's answer
# ---------------------------------------------------------------------------
def awaiting_text(entity_id: str, db: Database | None = None) -> str | None:
    """The offer card id waiting for the owner's own words after they pressed Edit (last 24 h), or None."""
    from datetime import timedelta
    o = _db(db)[COLLECTION].find_one({"entity_id": entity_id, "status": "EDIT_ASKED",
                                      "updated_at": {"$gte": utcnow() - timedelta(hours=24)}},
                                     {"_id": 0, "id": 1, "kind": 1}, sort=[("updated_at", -1)])
    return f"{PREFIX[o['kind']]}:{o['id']}" if o else None


def is_offer_card(card_id: str | None) -> bool:
    return bool(card_id) and card_id.split(":", 1)[0] in PREFIX.values()


def handle_reply(entity_id: str | None, card_id: str, *, button_id: str | None = None, text: str | None = None,
                 channel: str = "WHATSAPP", db: Database | None = None) -> dict:
    """APPROVE / EDIT / DECLINE (button, digit, or a plain yes/no) on a drift or review-reply offer."""
    db = _db(db)
    offer_id = card_id.split(":", 1)[1]
    offer = db[COLLECTION].find_one({"id": offer_id}, {"_id": 0})
    if offer is None:
        return {"handled": False, "reason": "that offer is not known"}
    entity_id = offer["entity_id"]
    lang = _lang(entity_id)
    compose_bridge.record_inbound(entity_id, None if button_id else text, channel=channel, card_id=card_id, db=db,
                                  button_payload=f"{button_id}|{card_id}" if button_id else None)
    action = (button_id or "").upper() or _plain_answer(text)
    if offer["status"] == "EDIT_ASKED" and text and not button_id:
        db[COLLECTION].update_one({"id": offer_id}, {"$set": {"status": "EDIT_RECEIVED", "owner_text_chars": len(text),
                                                              "updated_at": utcnow()}})
        audit.log("PROFILE_OFFER_EDIT", {"offer_id": offer_id, "kind": offer["kind"], "text_chars": len(text)},
                  entity_id=entity_id, db=db)
        _say(entity_id, T[lang]["edit_saved"], "FOLLOWUP", card_id, db)
        return {"handled": True, "action": "EDIT_RECEIVED", "kind": offer["kind"]}
    if offer["status"] != "OPEN":
        return {"handled": False, "reason": f"this offer is already {offer['status'].lower()}"}
    if action == "DECLINE":
        _close(db, offer, "DECLINED")
        _say(entity_id, T[lang]["declined"], "FOLLOWUP", card_id, db)
        return {"handled": True, "action": "DECLINE", "kind": offer["kind"]}
    if action == "EDIT":
        db[COLLECTION].update_one({"id": offer_id}, {"$set": {"status": "EDIT_ASKED", "updated_at": utcnow()}})
        _say(entity_id, T[lang]["edit"], "FOLLOWUP", card_id, db)
        return {"handled": True, "action": "EDIT", "kind": offer["kind"]}
    if action != "APPROVE":
        return {"handled": False, "reason": "reply with one of the buttons (or 1, 2, 3)"}
    if offer["kind"] == DRIFT:
        return _apply_drift(db, offer, lang, card_id)
    return _approve_replies(db, offer, lang, card_id)


def _plain_answer(text: str | None) -> str | None:
    t = (text or "").strip().lower().rstrip(".!")
    if t in ("ok", "okay", "yes", "kyllä", "joo", "ja", "japp", "sure", "hyväksy", "godkänn", "godkjenn"):
        return "APPROVE"
    if t in ("no", "ei", "nej", "nei", "no thanks", "ei kiitos", "nej tack", "nei takk"):
        return "DECLINE"
    return None


def _close(db: Database, offer: dict, status: str, **extra) -> None:
    db[COLLECTION].update_one({"id": offer["id"]}, {"$set": {"status": status, "updated_at": utcnow(), **extra}})
    audit.log("PROFILE_OFFER_DONE", {"offer_id": offer["id"], "kind": offer["kind"], "status": status,
                                     **{k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))}},
              entity_id=offer["entity_id"], db=db)


def _apply_drift(db: Database, offer: dict, lang: str, card_id: str) -> dict:
    from cs import season
    from cs.profile import drift
    eid = offer["entity_id"]
    s = Suggestion.from_doc(offer["suggestion"])
    target = season.default_target(eid)
    try:
        with season.yext_client(target, db) as yc:   # the same client choice as every other write
            res = drift.apply_drift(eid, s, target=target, decision_id=f"pof:{offer['id']}", client=yc)
    except Exception as ex:  # noqa: BLE001 - e.g. the mock is not running: tell the owner, keep the offer open
        res = {"status": "failed", "detail": f"could not reach the listing ({type(ex).__name__})"}
    status = res.get("status")
    if status == "applied":
        _close(db, offer, "APPLIED", detail=res.get("detail", ""))
        _say(eid, T[lang]["drift_done"], "CONFIRMATION", card_id, db)
    elif status == "cancelled":
        _close(db, offer, "CANCELLED", detail=res.get("detail", ""))
        _say(eid, T[lang]["drift_cancel"], "FOLLOWUP", card_id, db)
    else:
        _say(eid, T[lang]["drift_fail"], "FOLLOWUP", card_id, db)
        return {"handled": False, "action": "APPROVE", "kind": DRIFT, "reason": res.get("detail", status)}
    return {"handled": True, "action": "APPROVE", "kind": DRIFT, "status": status}


def _approve_replies(db: Database, offer: dict, lang: str, card_id: str) -> dict:
    from cs.profile import review_reply as rr
    saved = [rr.approve_review_reply(Suggestion.from_doc(d), via="message", db=db)["id"] for d in offer["drafts"]]
    _close(db, offer, "APPLIED", replies=len(saved))
    _say(offer["entity_id"], T[lang]["rr_done"], "CONFIRMATION", card_id, db)
    return {"handled": True, "action": "APPROVE", "kind": REVIEW_REPLY, "replies": saved}


# ---------------------------------------------------------------------------
# Ask Connect (B10): the owner asks, the business's own graph answers, with sources
# ---------------------------------------------------------------------------
def is_question(text: str | None) -> bool:
    """A real question ("Why do you think…", "Miksi…", "Hur…"), not a short reply like "ok?"."""
    if not text:
        return False
    m = _FIRST_WORD.match(text.strip().lower())
    return bool(m) and m.group(1) in QUESTION_WORDS and len(text.split()) >= 3


def ask(entity_id: str, question: str, lang: str | None = None) -> dict:
    """``cs.kg.ask`` without the inline source tags; ``sources`` lists what the answer rests on."""
    from cs.kg import ask as kg_ask
    lang = lang or _lang(entity_id)
    res = kg_ask(entity_id, question, lang)
    answer = re.sub(r"\s*\[[^\]]+\]", "", res.get("answer") or "").strip() or T[lang if lang in T else "en"]["dont_know"]
    sources = [_source_name(c) for c in res.get("citations") or []]
    return {"answer": answer, "sources": sorted(set(s for s in sources if s)), "mode": res.get("mode")}


def _source_name(ref: str) -> str:
    kind = ref.split(":", 1)[0]
    return {"learned_facts": "what you told us", "holiday_hours_history": "your past holiday hours",
            "regular_hours_history": "your weekly hours", "reviews": "customer reviews",
            "peer_benchmarks": "similar businesses nearby", "entities": "your listing",
            "calendar_special_days": "the holiday calendar", "suggestions": "our suggestion",
            "publisher_suggestions": "edits from Google users"}.get(kind, kind.replace("_", " "))


def answer_question(entity_id: str, text: str, *, channel: str = "WHATSAPP", db: Database | None = None) -> dict:
    """Answer an owner's question in the thread (B10)."""
    db = _db(db)
    compose_bridge.record_inbound(entity_id, text, channel=channel, db=db)
    res = ask(entity_id, text)
    body = res["answer"] + (f"\n({', '.join(res['sources'])})" if res["sources"] else "")
    _say(entity_id, body, "FOLLOWUP", None, db)
    audit.log("OWNER_QUESTION", {"mode": res["mode"], "sources": res["sources"], "text_chars": len(text)},
              entity_id=entity_id, db=db)
    return {"handled": True, "action": "ASK", "answer": res["answer"], "sources": res["sources"]}
