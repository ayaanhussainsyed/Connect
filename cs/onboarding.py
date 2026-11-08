"""Onboarding on the owner's own channel: no separate interface, just the first conversation.

Fonecta's sign-up already gives us the owner's contact details, channel and language. What it doesn't
give us is how the owner usually handles public holidays, which is exactly what a brand-new customer
has no history for. So the welcome asks:

    1. "On public holidays you're usually: Closed / Shorter hours / Open as normal"
       (Shorter hours -> "What hours, usually? e.g. 10-14")
    2. "Shall I set your public holidays this way every year? Yes, every year / Ask me each time"

The answers become learned facts for this season's public holidays (source ONBOARDING), so the first
season card already says "as you told us" instead of guessing from similar businesses, and "every
year" makes the first approval a standing permission (Autopilot next season).

    send_welcome(entity_id)                  -> {"onboarding_id", "sent"}
    handle_reply(entity_id, card_id, button_id=, text=)   card ids "ob:<id>" (buttons, SMS digits, free text)
    awaiting(entity_id)                      -> the card id waiting for a typed answer, or None
    prefers_every_year(entity_id)            -> bool
"""
from __future__ import annotations

import re

from pymongo.database import Database

from cs import audit, compose_bridge, data
from cs import db as dbmod
from cs.domain import new_id, utcnow
from cs.hours import CLOSED, REGULAR

COLLECTION = "onboarding"
SEASON = 2026
OPEN_STATES = ("SENT", "HOURS", "YEAR")

T = {
    "en": {"intro": "Connect Suggests · automated assistant",
           "welcome": "Welcome to Connect, {name}! Before each holiday season I'll check what Google, Apple Maps and AI "
                      "assistants will show, and message you here only if something is wrong. One tap to approve, "
                      "no login.\n\nOne question to start: on public holidays you're usually…",
           "OB_CLOSED": "Closed", "OB_SHORT": "Shorter hours", "OB_NORMAL": "Open as normal",
           "hours": "Got it. What hours, usually? For example 10-14.",
           "year": "Thanks! Shall I set your public holidays this way every year? I'll tell you each time, and you "
                   "can always undo.",
           "OB_EVERY_YEAR": "Yes, every year", "OB_ASK_EACH": "Ask me each time",
           "done_yes": "All set. I'll take care of your public holidays and tell you each time. Anything else, I ask "
                       "you first.",
           "done_no": "All set. I'll ask you before each holiday season.",
           "unclear": "Sorry, I didn't catch that. Tap a button, or reply with its number.",
           "stop": "OK, no more messages from Connect.", "digits": "Reply: {items}", "optout": "Reply STOP to opt out."},
    "fi": {"intro": "Connect Suggests · automaattinen avustaja",
           "welcome": "Tervetuloa Connectiin, {name}! Ennen jokaista juhlakautta tarkistan, mitä Google, Apple Maps "
                      "ja tekoälyavustajat näyttävät, ja viestin sinulle vain, jos jokin on pielessä. Yksi napautus "
                      "riittää, ei kirjautumista.\n\nAluksi yksi kysymys: arkipyhinä olette yleensä…",
           "OB_CLOSED": "Kiinni", "OB_SHORT": "Lyhyemmät ajat", "OB_NORMAL": "Auki normaalisti",
           "hours": "Selvä. Mihin aikaan yleensä? Esimerkiksi 10-14.",
           "year": "Kiitos! Asetanko arkipyhänne näin joka vuosi? Kerron aina etukäteen, ja voit perua milloin "
                   "tahansa.",
           "OB_EVERY_YEAR": "Kyllä, joka vuosi", "OB_ASK_EACH": "Kysy joka kerta",
           "done_yes": "Valmista. Hoidan arkipyhänne ja kerron aina. Muusta kysyn ensin.",
           "done_no": "Valmista. Kysyn ennen jokaista juhlakautta.",
           "unclear": "Anteeksi, en ymmärtänyt. Napauta painiketta tai vastaa sen numerolla.",
           "stop": "Selvä, Connect ei lähetä enää viestejä.", "digits": "Vastaa: {items}",
           "optout": "Vastaa STOP, jos et halua viestejä."},
    "sv": {"intro": "Connect Suggests · automatisk assistent",
           "welcome": "Välkommen till Connect, {name}! Före varje helgsäsong kontrollerar jag vad Google, Apple Maps "
                      "och AI-assistenter visar, och skriver bara hit om något är fel. Ett tryck räcker, ingen "
                      "inloggning.\n\nEn fråga till att börja med: på helgdagar har ni oftast…",
           "OB_CLOSED": "Stängt", "OB_SHORT": "Kortare tider", "OB_NORMAL": "Öppet som vanligt",
           "hours": "Uppfattat. Vilka tider brukar ni ha? Till exempel 10-14.",
           "year": "Tack! Ska jag ställa in era helgdagar så här varje år? Jag säger till varje gång, och du kan "
                   "alltid ångra.",
           "OB_EVERY_YEAR": "Ja, varje år", "OB_ASK_EACH": "Fråga varje gång",
           "done_yes": "Klart. Jag sköter era helgdagar och säger till varje gång. Om annat frågar jag först.",
           "done_no": "Klart. Jag frågar inför varje helgsäsong.",
           "unclear": "Förlåt, det förstod jag inte. Tryck på en knapp eller svara med dess nummer.",
           "stop": "Okej, Connect skickar inga fler meddelanden.", "digits": "Svara: {items}",
           "optout": "Svara STOP om du inte vill ha meddelanden."},
    "nb": {"intro": "Connect Suggests · automatisk assistent",
           "welcome": "Velkommen til Connect, {name}! Før hver høytid sjekker jeg hva Google, Apple Maps og "
                      "KI-assistenter viser, og skriver bare hit hvis noe er feil. Ett trykk holder, ingen "
                      "innlogging.\n\nFørst ett spørsmål: på helligdager har dere vanligvis…",
           "OB_CLOSED": "Stengt", "OB_SHORT": "Kortere tider", "OB_NORMAL": "Åpent som vanlig",
           "hours": "Skjønner. Hvilke tider pleier dere ha? For eksempel 10-14.",
           "year": "Takk! Skal jeg legge inn helligdagene deres slik hvert år? Jeg sier fra hver gang, og du kan "
                   "alltid angre.",
           "OB_EVERY_YEAR": "Ja, hvert år", "OB_ASK_EACH": "Spør hver gang",
           "done_yes": "Ferdig. Jeg ordner helligdagene og sier fra hver gang. Om annet spør jeg først.",
           "done_no": "Ferdig. Jeg spør før hver høytid.",
           "unclear": "Beklager, det forsto jeg ikke. Trykk på en knapp eller svar med nummeret.",
           "stop": "Greit, Connect sender ikke flere meldinger.", "digits": "Svar: {items}",
           "optout": "Svar STOP hvis du ikke vil ha meldinger."},
}
HABIT = ("OB_CLOSED", "OB_SHORT", "OB_NORMAL")
YEAR = ("OB_EVERY_YEAR", "OB_ASK_EACH")
WORDS = {
    "OB_CLOSED": ("closed", "close", "kiinni", "suljettu", "stängt", "stängd", "stengt"),
    "OB_SHORT": ("short", "shorter", "lyhyem", "lyhyt", "lyhennet", "kortare", "kortere", "kort"),
    "OB_NORMAL": ("normal", "normally", "open", "normaal", "normaalisti", "tavalli", "auki", "vanlig", "vanligt",
                  "öppet", "åpent", "som vanlig"),
    "OB_EVERY_YEAR": ("yes", "every year", "kyllä", "joo", "joka vuosi", "ja", "varje år", "hvert år", "ok"),
    "OB_ASK_EACH": ("no", "ask", "each time", "ei", "kysy", "nej", "fråga", "nei", "spør"),
}
STOP_WORDS = ("stop", "lopeta", "sluta", "stopp")


def _db(db: Database | None) -> Database:
    return db if db is not None else dbmod.get_db()


def _t(lang: str) -> dict:
    return T.get(lang, T["en"])


def _buttons(ids: tuple[str, ...], lang: str) -> list[dict]:
    return [{"id": i, "title": _t(lang)[i], "digit": str(n)} for n, i in enumerate(ids, 1)]


def _compose(text: str, ids: tuple[str, ...], lang: str, channel: str) -> dict:
    """Buttons on WhatsApp and email; on SMS the same choices as digits in the text."""
    buttons = _buttons(ids, lang)
    if channel == "SMS" and buttons:
        items = ", ".join(f"{b['digit']} = {b['title']}" for b in buttons)
        text = f"{text}\n{_t(lang)['digits'].format(items=items)}"
    return {"text": text, "buttons": buttons, "parts": {"reply_digits": {b["digit"]: b["id"] for b in buttons}},
            "composer": "cs.onboarding"}


def _send(entity_id: str, ob_id: str, text: str, ids: tuple[str, ...], db: Database, kind: str = "FOLLOWUP") -> dict:
    c = compose_bridge.contact(entity_id)
    return compose_bridge.send(entity_id, _compose(text, ids, c["lang"], c["channel"]), kind=kind,
                               card_id=f"ob:{ob_id}", lang=c["lang"], channel=c["channel"], db=db)


def _open(entity_id: str, db: Database) -> dict | None:
    return db[COLLECTION].find_one({"entity_id": entity_id, "status": {"$in": list(OPEN_STATES)}}, {"_id": 0},
                                   sort=[("created_at", -1)])


# ---------------------------------------------------------------------------
def send_welcome(entity_id: str, db: Database | None = None) -> dict:
    """The welcome, once while one is open (opt-outs get nothing)."""
    db = _db(db)
    existing = _open(entity_id, db)
    if existing:
        return {"onboarding_id": existing["id"], "sent": False}
    c = compose_bridge.contact(entity_id)
    if c.get("opted_out"):
        return {"onboarding_id": None, "sent": False}
    t = _t(c["lang"])
    name = data.entity(entity_id).get("name", entity_id)
    ob_id = new_id("ob")
    db[COLLECTION].insert_one({"id": ob_id, "entity_id": entity_id, "status": "SENT", "habit": None, "hours": None,
                               "every_year": None, "lang": c["lang"], "channel": c["channel"],
                               "created_at": utcnow(), "updated_at": utcnow()})
    text = f"{t['intro']}\n{t['welcome'].format(name=name)}\n{t['optout']}"
    sent = _send(entity_id, ob_id, text, HABIT, db, kind="WELCOME")   # first contact: a proactive kind
    audit.log("ONBOARDING_WELCOME", {"onboarding_id": ob_id, "channel": c["channel"], "lang": c["lang"]},
              entity_id=entity_id, db=db)
    return {"onboarding_id": ob_id, "sent": True, "delivery": sent.get("status")}


def awaiting(entity_id: str, db: Database | None = None) -> str | None:
    o = _open(entity_id, _db(db))
    return f"ob:{o['id']}" if o else None


def prefers_every_year(entity_id: str, db: Database | None = None) -> bool:
    o = _db(db)[COLLECTION].find_one({"entity_id": entity_id, "status": "DONE"}, {"_id": 0, "every_year": 1},
                                     sort=[("updated_at", -1)])
    return bool(o and o.get("every_year"))


def _choice(text: str | None, ids: tuple[str, ...]) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    if t.isdigit() and 1 <= int(t) <= len(ids):
        return ids[int(t) - 1]
    for i in ids:
        if any(re.search(rf"(^|\W){re.escape(w)}", t) for w in WORDS.get(i, ())):
            return i
    return None


def _hours(text: str | None) -> str | None:
    from cs.hours import normalise
    m = re.search(r"(\d{1,2}(?:[:.]\d{2})?)\s*[-–]\s*(\d{1,2}(?:[:.]\d{2})?)", text or "")
    if not m:
        return None
    try:
        return normalise(f"{m.group(1)}-{m.group(2)}".replace(".", ":"))
    except ValueError:
        return None


def _public_holidays(entity_id: str) -> list[dict]:
    t = data.targets(SEASON)
    rows = t[(t["entity_id"] == entity_id) & (t["day_type"] == "PUBLIC_HOLIDAY")]
    return rows[["date", "day_key"]].to_dict("records")


def _remember(entity_id: str, ob_id: str, value: str, db: Database) -> int:
    from cs import learn
    days = _public_holidays(entity_id)
    for d in days:
        learn.record_fact(entity_id, d["day_key"], d["date"], value, SEASON, source="ONBOARDING",
                          batch_id=f"ob:{ob_id}", db=db)
    return len(days)


def handle_reply(entity_id: str | None, card_id: str, *, button_id: str | None = None, text: str | None = None,
                 channel: str = "WHATSAPP", db: Database | None = None) -> dict:
    db = _db(db)
    ob_id = card_id.split(":", 1)[1]
    ob = db[COLLECTION].find_one({"id": ob_id}, {"_id": 0})
    if ob is None:
        return {"handled": False, "reason": "that welcome is not known"}
    entity_id = ob["entity_id"]
    lang = compose_bridge.contact(entity_id)["lang"]
    t = _t(lang)
    compose_bridge.record_inbound(entity_id, None if button_id else text, channel=channel, card_id=card_id, db=db,
                                  button_payload=f"{button_id}|{card_id}" if button_id else None)
    if text and text.strip().lower() in STOP_WORDS:
        db[COLLECTION].update_one({"id": ob_id}, {"$set": {"status": "STOPPED", "updated_at": utcnow()}})
        _send(entity_id, ob_id, t["stop"], (), db)
        return {"handled": True, "action": "STOP"}
    status = ob["status"]
    if status not in OPEN_STATES:
        return {"handled": False, "reason": f"this welcome is already {status.lower()}"}

    if status == "SENT":
        habit = button_id if button_id in HABIT else _choice(text, HABIT)
        if habit is None:
            _send(entity_id, ob_id, t["unclear"], HABIT, db)
            return {"handled": True, "action": "UNCLEAR"}
        if habit == "OB_SHORT":
            db[COLLECTION].update_one({"id": ob_id}, {"$set": {"status": "HOURS", "habit": habit,
                                                               "updated_at": utcnow()}})
            _send(entity_id, ob_id, t["hours"], (), db)
            return {"handled": True, "action": habit}
        n = _remember(entity_id, ob_id, CLOSED if habit == "OB_CLOSED" else REGULAR, db)
        db[COLLECTION].update_one({"id": ob_id}, {"$set": {"status": "YEAR", "habit": habit, "facts": n,
                                                           "updated_at": utcnow()}})
        audit.log("ONBOARDING_HABIT", {"onboarding_id": ob_id, "habit": habit, "facts": n}, entity_id=entity_id, db=db)
        _send(entity_id, ob_id, t["year"], YEAR, db)
        return {"handled": True, "action": habit, "facts": n}

    if status == "HOURS":
        hours = _hours(text)
        if hours is None:
            _send(entity_id, ob_id, t["hours"], (), db)
            return {"handled": True, "action": "UNCLEAR"}
        n = _remember(entity_id, ob_id, hours, db)
        db[COLLECTION].update_one({"id": ob_id}, {"$set": {"status": "YEAR", "hours": hours, "facts": n,
                                                           "updated_at": utcnow()}})
        audit.log("ONBOARDING_HABIT", {"onboarding_id": ob_id, "habit": "OB_SHORT", "hours": hours, "facts": n},
                  entity_id=entity_id, db=db)
        _send(entity_id, ob_id, t["year"], YEAR, db)
        return {"handled": True, "action": "HOURS", "hours": hours}

    choice = button_id if button_id in YEAR else _choice(text, YEAR)
    if choice is None:
        _send(entity_id, ob_id, t["unclear"], YEAR, db)
        return {"handled": True, "action": "UNCLEAR"}
    every = choice == "OB_EVERY_YEAR"
    db[COLLECTION].update_one({"id": ob_id}, {"$set": {"status": "DONE", "every_year": every, "updated_at": utcnow()}})
    audit.log("ONBOARDING_DONE", {"onboarding_id": ob_id, "every_year": every}, entity_id=entity_id, db=db)
    _send(entity_id, ob_id, t["done_yes" if every else "done_no"], (), db)
    return {"handled": True, "action": choice}
