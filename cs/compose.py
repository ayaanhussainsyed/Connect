"""A7 – The owner messages: deterministic templates in fi / sv / nb / en that carry every fact.

Facts (dates, hours, numbers) come only from the inputs; the wording comes from the templates
below. An LLM may polish *only* the long reason sentence (``cs.llm.polish_reason``), and
``slot_check`` throws the polish away if any date, time or number changed.

Main entry points (all return ``{"text", "buttons", "parts"}``)::

    compose_card_message(business, suggestions, lang, channel, first_contact, question, deadline,
                         value_line_views, *, value_line_day=None, link=None, today=None)
    compose_question(question, lang, channel)
    compose_confirmation(business, changes, where_it_shows, lang, channel, *, every_year=False)
    compose_followup(decision, lang, *, ...)            # APPROVE | APPROVE_EVERY_YEAR | EDIT | DECLINE | REMINDER | STOP | UNDO
    compose_emergency(business, day, lang, *, ...)       # never takes or shows a reason
    compose_autopilot_note(business, applied, lang, *, ...)

``buttons`` is a list of ``{"id", "title", "digit"}`` (titles ≤ 20 characters). On SMS the
buttons list is empty and the text carries "1 = …, 2 = …" instead; ``parts["reply_digits"]``
maps each digit to its button id.

Local formats: dates "ke 24.12." (fi), "ons 24/12" (sv), "ons. 24.12." (nb), "Wed 24 Dec" (en);
hours "7.30–13" (fi) and "07:30–13:00" (sv, nb, en).

CLI::

    python -m cs.compose FI-D0001 --lang fi [--channel SMS] [--returning]
    python -m cs.compose --all-demo
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from cs.hours import normalise

LANGS = ("fi", "sv", "nb", "en")
CHANNELS = ("WHATSAPP", "SMS", "EMAIL", "OUTBOX", "APP_PUSH")
WHATSAPP_BODY_LIMIT = 1024       # interactive / template body
BUTTON_TITLE_LIMIT = 20          # WhatsApp reply-button title
WHATSAPP_MAX_BUTTONS = 3         # WhatsApp interactive reply buttons
SMS_LIMIT = 480                  # "kept short": a few concatenated segments
CHANNEL_LIMITS = {"WHATSAPP": WHATSAPP_BODY_LIMIT, "SMS": SMS_LIMIT}
TEMPLATE_PARAM_LIMIT = 900       # our cap for the WhatsApp template's {{1}} (one line)
DEFAULT_BRAND = "Connect Suggests"

# Bases whose evidence is the owner's own past: stated plainly. Everything else is phrased as
# a question and the hours get a "?" (peer claims are rejected more often).
OWN_BASES = frozenset({"OWN_LAST_YEAR", "OWN_2_SEASONS", "LINKED_HOLIDAY", "OWNER_ANSWER", "MODEL",
                       "LAST_YEAR"})
ASK_BASES = frozenset({"PEERS", "REVIEW", "USER_EDIT"})
BASIS_ALIASES = {"LAST_YEAR": "OWN_LAST_YEAR", "SHOPPING_CENTRE": "MALL"}

WD = {
    "fi": ["ma", "ti", "ke", "to", "pe", "la", "su"],
    "sv": ["mån", "tis", "ons", "tor", "fre", "lör", "sön"],
    "nb": ["man", "tir", "ons", "tor", "fre", "lør", "søn"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------
T: dict[str, dict[str, str]] = {
    "fi": {
        "disclosure": "{brand} · automaattinen avustaja",
        "hi": "Hei {name}!",
        "intro": "{title} – ehdotuksemme:",
        "title_xmas": "Joulun ja uudenvuoden aukioloajat",
        "title_generic": "Tulevien erikoispäivien aukioloajat",
        "closed": "kiinni", "regular": "tavalliset ajat",
        "q_marker": "← yksi kysymys: kiinni, tavalliset ajat vai muut?",
        "shown_list": "Jos et tee mitään, Google näyttää: {items}",
        "shown_same": "Jos et tee mitään, Google näyttää näinä päivinä: {hours}",
        "deadline": "Vastaa viimeistään {date}, jotta myös tekoälyavustajat ehtivät näyttää ajat oikein.",
        "deadline_past": "Vastaathan pian – ensimmäinen päivä on jo lähellä.",
        "value_day": "{phrase} ~{n} ihmistä haki teitä.",
        "value_generic": "Viime vuonna vastaavina päivinä ~{n} ihmistä haki teitä.",
        "scope": "\"Joka vuosi\" = samat päivät samoin ajoin jatkossakin; uudet asiat kysymme aina. Voit perua milloin vain.",
        "own_words": "Voit myös vastata omin sanoin tai \"ei kiitos\".",
        "link": "Muokkaa tai katso perustelut: {link}",
        "digits": "Vastaa: {items}",
        "more": "+ {n} muuta päivää",
        "all_reason": "Kaikki päivät: {reason}.",
        "saved": "✓ Tallennettu: {items}",
        "shows_on": "Näkyy: {items}",
        "not_live": "({items})",
        "undo_hint": "Voit perua milloin tahansa.",
        "scope_granted": "Ensi vuonna asetamme samat ajat samoille päiville vähintään 14 päivää etukäteen ja kerromme siitä. Uudet asiat kysymme aina.",
        "approve": "Kiitos! Päivitämme ajat nyt. Saat vahvistuksen, kun ne näkyvät.",
        "edit_prompt": "Selvä! Kirjoita uudet ajat omin sanoin, niin tarkistamme ne kanssasi.",
        "edit_link": "Selvä! Muokkaa ajat tästä: {link}",
        "readback": "Tallennetaanko nämä?",
        "decline": "Selvä, emme muuta mitään. Voit muuttaa aukioloaikoja itse Connectissa milloin tahansa.",
        "reminder": "Muistutus: {title} odottavat vastaustasi.",
        "stop": "Selvä, emme lähetä enää ehdotuksia. Voit ottaa ne takaisin käyttöön Connectissa.",
        "undone": "Peruttu – palautamme aiemmat ajat.",
        "em_today": "kiinni tänään {date}", "em_tomorrow": "kiinni huomenna {date}", "em_day": "kiinni {date}",
        "em_range": "kiinni {d1}–{d2}", "em_open_today": "auki tänään {date} {hours}", "em_open_day": "auki {date} {hours}",
        "em_back_tomorrow": "Tavalliset ajat palaavat huomenna.", "em_back_on": "Tavalliset ajat palaavat {date}.",
        "em_open_ended": "kiinni toistaiseksi alkaen {date}",
        "em_ask_daily": "Kysymme päivittäin, avaatteko taas.",
        "em_slow": "Hitaammat palvelut ({items}) eivät ehdi päivittyä heti.",
        "auto_saved": "Asetimme {title} kuten sovimme:",
        "auto_short": "Päivä oli alle 14 päivän päässä, joten asetimme ajat heti.",
        "auto_hint": "Peru = palautamme aiemmat ajat. Lopeta = emme tee tätä enää ilman lupaasi.",
        "q_day": "Yksi kysymys: miten olette auki {date}?",
        "q_habit": "Yksi kysymys: oletteko yleensä kiinni arkipyhinä?",
        "q_other_eg": "muut ajat (esim. {hours})",
        "subject": "{title} – ehdotuksemme",
        "subject_other": "Connect Suggests: aukioloajat",
    },
    "sv": {
        "disclosure": "{brand} · automatisk assistent",
        "hi": "Hej {name}!",
        "intro": "{title} – vårt förslag:",
        "title_xmas": "Öppettider över jul och nyår",
        "title_generic": "Öppettider för kommande särskilda dagar",
        "closed": "stängt", "regular": "ordinarie tider",
        "q_marker": "← en fråga: stängt, ordinarie tider eller andra?",
        "shown_list": "Om du inte gör något visar Google: {items}",
        "shown_same": "Om du inte gör något visar Google dessa dagar: {hours}",
        "deadline": "Svara senast {date} så att även AI-assistenter hinner visa rätt tider.",
        "deadline_past": "Svara gärna snart – första dagen närmar sig.",
        "value_day": "{phrase} sökte ~{n} personer efter er.",
        "value_generic": "Motsvarande dagar i fjol sökte ~{n} personer efter er.",
        "scope": "\"Varje år\" = samma dagar med samma tider framöver; allt nytt frågar vi om. Du kan ångra när som helst.",
        "own_words": "Du kan också svara med egna ord eller \"nej tack\".",
        "link": "Ändra eller se varför: {link}",
        "digits": "Svara: {items}",
        "more": "+ {n} dagar till",
        "all_reason": "Alla dagar: {reason}.",
        "saved": "✓ Sparat: {items}",
        "shows_on": "Syns på: {items}",
        "not_live": "({items})",
        "undo_hint": "Du kan ångra när som helst.",
        "scope_granted": "Nästa år lägger vi in samma tider för samma dagar minst 14 dagar i förväg och meddelar dig. Allt nytt frågar vi om.",
        "approve": "Tack! Vi uppdaterar tiderna nu. Du får en bekräftelse när de syns.",
        "edit_prompt": "Okej! Skriv de nya tiderna med egna ord, så stämmer vi av dem med dig.",
        "edit_link": "Okej! Ändra tiderna här: {link}",
        "readback": "Ska vi spara detta?",
        "decline": "Okej, vi ändrar ingenting. Du kan ändra öppettiderna själv i Connect när som helst.",
        "reminder": "Påminnelse: {title} väntar på ditt svar.",
        "stop": "Okej, vi skickar inga fler förslag. Du kan slå på dem igen i Connect.",
        "undone": "Ångrat – vi återställer de tidigare tiderna.",
        "em_today": "stängt idag {date}", "em_tomorrow": "stängt i morgon {date}", "em_day": "stängt {date}",
        "em_range": "stängt {d1}–{d2}", "em_open_today": "öppet idag {date} {hours}", "em_open_day": "öppet {date} {hours}",
        "em_back_tomorrow": "Ordinarie tider gäller igen i morgon.", "em_back_on": "Ordinarie tider gäller igen {date}.",
        "em_open_ended": "stängt tills vidare från {date}",
        "em_ask_daily": "Vi frågar varje dag om ni öppnar igen.",
        "em_slow": "Långsammare tjänster ({items}) hinner inte uppdateras direkt.",
        "auto_saved": "Vi har lagt in {title} som vi kom överens om:",
        "auto_short": "Dagen var mindre än 14 dagar bort, så vi lade in tiderna direkt.",
        "auto_hint": "Ångra = vi återställer de tidigare tiderna. Sluta = vi gör inte detta igen utan ditt lov.",
        "q_day": "En fråga: hur har ni öppet {date}?",
        "q_habit": "En fråga: brukar ni ha stängt på helgdagar?",
        "q_other_eg": "andra tider (t.ex. {hours})",
        "subject": "{title} – vårt förslag",
        "subject_other": "Connect: öppettider",
    },
    "nb": {
        "disclosure": "{brand} · automatisk assistent",
        "hi": "Hei {name}!",
        "intro": "{title} – vårt forslag:",
        "title_xmas": "Åpningstider i jul og nyttår",
        "title_generic": "Åpningstider for kommende spesielle dager",
        "closed": "stengt", "regular": "vanlige tider",
        "q_marker": "← ett spørsmål: stengt, vanlige tider eller andre?",
        "shown_list": "Hvis du ikke gjør noe, viser Google: {items}",
        "shown_same": "Hvis du ikke gjør noe, viser Google disse dagene: {hours}",
        "deadline": "Svar innen {date}, så rekker også KI-assistenter å vise riktige tider.",
        "deadline_past": "Svar gjerne snart – første dag nærmer seg.",
        "value_day": "{phrase} søkte ~{n} personer etter dere.",
        "value_generic": "Tilsvarende dager i fjor søkte ~{n} personer etter dere.",
        "scope": "\"Hvert år\" = samme dager med samme tider fremover; alt nytt spør vi om. Du kan angre når som helst.",
        "own_words": "Du kan også svare med egne ord eller \"nei takk\".",
        "link": "Endre eller se hvorfor: {link}",
        "digits": "Svar: {items}",
        "more": "+ {n} dager til",
        "all_reason": "Alle dager: {reason}.",
        "saved": "✓ Lagret: {items}",
        "shows_on": "Vises på: {items}",
        "not_live": "({items})",
        "undo_hint": "Du kan angre når som helst.",
        "scope_granted": "Neste år legger vi inn de samme tidene for de samme dagene minst 14 dager i forveien og gir deg beskjed. Alt nytt spør vi om.",
        "approve": "Takk! Vi oppdaterer tidene nå. Du får en bekreftelse når de vises.",
        "edit_prompt": "Greit! Skriv de nye tidene med egne ord, så sjekker vi dem med deg.",
        "edit_link": "Greit! Endre tidene her: {link}",
        "readback": "Skal vi lagre dette?",
        "decline": "Greit, vi endrer ingenting. Du kan endre åpningstidene selv i Connect når som helst.",
        "reminder": "Påminnelse: {title} venter på svaret ditt.",
        "stop": "Greit, vi sender ikke flere forslag. Du kan slå dem på igjen i Connect.",
        "undone": "Angret – vi setter tilbake de tidligere tidene.",
        "em_today": "stengt i dag {date}", "em_tomorrow": "stengt i morgen {date}", "em_day": "stengt {date}",
        "em_range": "stengt {d1}–{d2}", "em_open_today": "åpent i dag {date} {hours}", "em_open_day": "åpent {date} {hours}",
        "em_back_tomorrow": "Vanlige tider gjelder igjen i morgen.", "em_back_on": "Vanlige tider gjelder igjen {date}.",
        "em_open_ended": "stengt inntil videre fra {date}",
        "em_ask_daily": "Vi spør hver dag om dere åpner igjen.",
        "em_slow": "Tregere tjenester ({items}) rekker ikke å oppdateres med en gang.",
        "auto_saved": "Vi har lagt inn {title} som avtalt:",
        "auto_short": "Dagen var under 14 dager unna, så vi la inn tidene med en gang.",
        "auto_hint": "Angre = vi setter tilbake de tidligere tidene. Slutt = vi gjør ikke dette igjen uten lov.",
        "q_day": "Ett spørsmål: hvordan har dere åpent {date}?",
        "q_habit": "Ett spørsmål: har dere vanligvis stengt på helligdager?",
        "q_other_eg": "andre tider (f.eks. {hours})",
        "subject": "{title} – vårt forslag",
        "subject_other": "Connect: åpningstider",
    },
    "en": {
        "disclosure": "{brand} · automated assistant",
        "hi": "Hi {name}!",
        "intro": "{title} – our suggestion:",
        "title_xmas": "Christmas and New Year hours",
        "title_generic": "Hours for the coming special days",
        "closed": "closed", "regular": "regular hours",
        "q_marker": "← one question: closed, regular hours or other?",
        "shown_list": "If you do nothing, Google shows: {items}",
        "shown_same": "If you do nothing, Google shows on these days: {hours}",
        "deadline": "Please answer by {date} so AI assistants show it in time.",
        "deadline_past": "Please answer soon – the first date is close.",
        "value_day": "~{n} people looked you up {phrase}.",
        "value_generic": "~{n} people looked you up on these days last year.",
        "scope": "\"Every year\" = the same days with the same hours from now on; anything new is still asked. Undo anytime.",
        "own_words": "You can also reply in your own words, or \"no thanks\".",
        "link": "Edit or see why: {link}",
        "digits": "Reply: {items}",
        "more": "+ {n} more dates",
        "all_reason": "All dates: {reason}.",
        "saved": "✓ Saved: {items}",
        "shows_on": "Shows on: {items}",
        "not_live": "({items})",
        "undo_hint": "You can undo at any time.",
        "scope_granted": "Next year we'll set the same hours for the same days at least 14 days ahead and let you know. Anything new is still asked.",
        "approve": "Thanks! We're updating your hours now. You'll get a confirmation when they show.",
        "edit_prompt": "Sure! Write the new hours in your own words and we'll read them back to you.",
        "edit_link": "Sure! Edit the hours here: {link}",
        "readback": "Shall we save this?",
        "decline": "OK, we won't change anything. You can update your hours yourself in Connect at any time.",
        "reminder": "Reminder: your {title} are waiting for your answer.",
        "stop": "OK, we won't send more suggestions. You can switch them back on in Connect.",
        "undone": "Undone – we're restoring your previous hours.",
        "em_today": "closed today, {date}", "em_tomorrow": "closed tomorrow, {date}", "em_day": "closed {date}",
        "em_range": "closed {d1}–{d2}", "em_open_today": "open today, {date}, {hours}", "em_open_day": "open {date}, {hours}",
        "em_back_tomorrow": "Normal hours return tomorrow.", "em_back_on": "Normal hours return {date}.",
        "em_open_ended": "closed until further notice from {date}",
        "em_ask_daily": "We'll ask each day if you're open again.",
        "em_slow": "Slower services ({items}) won't update right away.",
        "auto_saved": "We set your {title} as agreed:",
        "auto_short": "The date was less than 14 days away, so we set them right away.",
        "auto_hint": "Undo = we restore your previous hours. Stop = we won't do this again without asking.",
        "q_day": "One question: what are your hours on {date}?",
        "q_habit": "One question: are you usually closed on public holidays?",
        "q_other_eg": "other hours (e.g. {hours})",
        "subject": "{title} – our suggestion",
        "subject_other": "Connect Suggests: your opening hours",
    },
}

BUTTONS: dict[str, dict[str, str]] = {
    "fi": {"APPROVE": "Hyväksy", "APPROVE_EVERY_YEAR": "Hyväksy + joka vuosi", "EDIT": "Muokkaa",
           "DECLINE": "Ei kiitos", "UNDO": "Peru", "STOP": "Lopeta tämä", "YES": "Kyllä", "NO": "Ei",
           "Q_CLOSED": "Kiinni", "Q_REGULAR": "Tavalliset ajat", "Q_OTHER": "Muut ajat",
           "H_YES": "Kyllä", "H_NO": "Ei", "H_VARIES": "Vaihtelee", "CLOSED_TODAY": "Kiinni tänään"},
    "sv": {"APPROVE": "Godkänn", "APPROVE_EVERY_YEAR": "Godkänn + varje år", "EDIT": "Ändra",
           "DECLINE": "Nej tack", "UNDO": "Ångra", "STOP": "Sluta med detta", "YES": "Ja", "NO": "Nej",
           "Q_CLOSED": "Stängt", "Q_REGULAR": "Ordinarie tider", "Q_OTHER": "Andra tider",
           "H_YES": "Ja", "H_NO": "Nej", "H_VARIES": "Det varierar", "CLOSED_TODAY": "Stängt idag"},
    "nb": {"APPROVE": "Godkjenn", "APPROVE_EVERY_YEAR": "Godkjenn + hvert år", "EDIT": "Endre",
           "DECLINE": "Nei takk", "UNDO": "Angre", "STOP": "Slutt med dette", "YES": "Ja", "NO": "Nei",
           "Q_CLOSED": "Stengt", "Q_REGULAR": "Vanlige tider", "Q_OTHER": "Andre tider",
           "H_YES": "Ja", "H_NO": "Nei", "H_VARIES": "Det varierer", "CLOSED_TODAY": "Stengt i dag"},
    "en": {"APPROVE": "Approve", "APPROVE_EVERY_YEAR": "Approve + every year", "EDIT": "Edit",
           "DECLINE": "No thanks", "UNDO": "Undo", "STOP": "Stop doing this", "YES": "Yes", "NO": "No",
           "Q_CLOSED": "Closed", "Q_REGULAR": "Regular hours", "Q_OTHER": "Other hours",
           "H_YES": "Yes", "H_NO": "No", "H_VARIES": "It varies", "CLOSED_TODAY": "Closed today"},
}
CARD_BUTTON_IDS = ("APPROVE", "APPROVE_EVERY_YEAR", "EDIT", "DECLINE")

# Short reasons on each date line, by basis. {mall} is the shopping centre's name.
REASONS: dict[str, dict[str, str]] = {
    "fi": {"OWN_LAST_YEAR": "viime vuonna sama", "OWN_2_SEASONS": "kuten 2 vuotta sitten",
           "LINKED_HOLIDAY": "kuten muina pyhinä viime vuonna", "OWNER_ANSWER": "kuten kerroitte",
           "MODEL": "arvio aiempien aikojenne perusteella",
           "MALL_closed": "{mall} on silloin kiinni", "MALL_open": "{mall} on auki samoin",
           "PEERS_closed": "useimmat vastaavat ovat kiinni – entä te?",
           "PEERS_open": "vastaavat ovat usein auki näin – entä te?",
           "REVIEW": "asiakkaan arvion perusteella – pitääkö paikkansa?",
           "USER_EDIT": "Google-käyttäjä ehdotti tätä – pitääkö paikkansa?",
           "DEFAULT": "ehdotus", "mall_fallback": "kauppakeskuksenne"},
    "sv": {"OWN_LAST_YEAR": "samma som i fjol", "OWN_2_SEASONS": "som för 2 år sedan",
           "LINKED_HOLIDAY": "som era andra helgdagar i fjol", "OWNER_ANSWER": "som ni berättade",
           "MODEL": "uppskattat utifrån era tidigare tider",
           "MALL_closed": "{mall} har stängt då", "MALL_open": "{mall} har samma tider",
           "PEERS_closed": "de flesta liknande har stängt – ni också?",
           "PEERS_open": "liknande har ofta dessa tider – ni också?",
           "REVIEW": "enligt en kundrecension – stämmer det?",
           "USER_EDIT": "en Google-användare föreslog detta – stämmer det?",
           "DEFAULT": "förslag", "mall_fallback": "ert köpcentrum"},
    "nb": {"OWN_LAST_YEAR": "samme som i fjor", "OWN_2_SEASONS": "som for 2 år siden",
           "LINKED_HOLIDAY": "som de andre helligdagene i fjor", "OWNER_ANSWER": "som dere sa",
           "MODEL": "anslått ut fra tidligere tider",
           "MALL_closed": "{mall} har stengt da", "MALL_open": "{mall} har samme tider",
           "PEERS_closed": "de fleste lignende har stengt – dere også?",
           "PEERS_open": "lignende har ofte disse tidene – dere også?",
           "REVIEW": "ifølge en kundeanmeldelse – stemmer det?",
           "USER_EDIT": "en Google-bruker foreslo dette – stemmer det?",
           "DEFAULT": "forslag", "mall_fallback": "kjøpesenteret deres"},
    "en": {"OWN_LAST_YEAR": "same as last year", "OWN_2_SEASONS": "as 2 years ago",
           "LINKED_HOLIDAY": "like your other holidays last year", "OWNER_ANSWER": "as you told us",
           "MODEL": "estimated from your past hours",
           "MALL_closed": "{mall} is closed then", "MALL_open": "{mall} has the same hours",
           "PEERS_closed": "most similar places close – you too?",
           "PEERS_open": "similar places often keep these hours – you too?",
           "REVIEW": "from a customer review – is that right?",
           "USER_EDIT": "a Google user suggested this – is that right?",
           "DEFAULT": "suggestion", "mall_fallback": "your shopping centre"},
}

# Long reason sentences (card page "Why ▸", and the only text an LLM may polish).
REASON_SENTENCES: dict[str, dict[str, str]] = {
    "fi": {"OWN_LAST_YEAR": "Merkitsitte viime vuonna samalle päivälle samat ajat: {hours}.",
           "OWN_2_SEASONS": "Merkitsitte 2 vuotta sitten samalle päivälle samat ajat: {hours}.",
           "LINKED_HOLIDAY": "Viime vuonna aukioloaikanne muina vastaavina pyhinä olivat samat: {hours}.",
           "OWNER_ANSWER": "Kerroitte meille nämä ajat: {hours}.",
           "MODEL": "Arvioimme ajat aiempien aukioloaikojenne perusteella: {hours}.",
           "MALL": "{mall} on ilmoittanut tälle päivälle ajat: {hours}.",
           "PEERS_closed": "Useimmat vastaavat yritykset lähistöllä ovat silloin kiinni. Pitääkö tämä paikkansa myös teillä?",
           "PEERS_open": "Vastaavat yritykset lähistöllä ovat usein auki {hours}. Sopiiko tämä teille?",
           "REVIEW": "Asiakkaan arvio viittaa tähän: {hours}. Pitääkö se paikkansa?",
           "USER_EDIT": "Google-käyttäjä ehdotti tätä: {hours}. Pitääkö se paikkansa?",
           "DEFAULT": "Ehdotus: {hours}."},
    "sv": {"OWN_LAST_YEAR": "Förra året angav ni samma tider för den här dagen: {hours}.",
           "OWN_2_SEASONS": "För 2 år sedan angav ni samma tider för den här dagen: {hours}.",
           "LINKED_HOLIDAY": "I fjol hade ni samma tider på liknande helgdagar: {hours}.",
           "OWNER_ANSWER": "Ni berättade dessa tider för oss: {hours}.",
           "MODEL": "Vi har uppskattat tiderna utifrån era tidigare öppettider: {hours}.",
           "MALL": "{mall} har angett dessa tider för dagen: {hours}.",
           "PEERS_closed": "De flesta liknande företag i närheten har stängt då. Stämmer det för er också?",
           "PEERS_open": "Liknande företag i närheten har ofta öppet {hours}. Passar det er?",
           "REVIEW": "En kundrecension tyder på detta: {hours}. Stämmer det?",
           "USER_EDIT": "En Google-användare föreslog detta: {hours}. Stämmer det?",
           "DEFAULT": "Förslag: {hours}."},
    "nb": {"OWN_LAST_YEAR": "I fjor la dere inn de samme tidene for denne dagen: {hours}.",
           "OWN_2_SEASONS": "For 2 år siden la dere inn de samme tidene for denne dagen: {hours}.",
           "LINKED_HOLIDAY": "I fjor hadde dere de samme tidene på lignende helligdager: {hours}.",
           "OWNER_ANSWER": "Dere ga oss disse tidene: {hours}.",
           "MODEL": "Vi har anslått tidene ut fra de tidligere åpningstidene deres: {hours}.",
           "MALL": "{mall} har lagt inn disse tidene for dagen: {hours}.",
           "PEERS_closed": "De fleste lignende bedrifter i nærheten har stengt da. Stemmer det for dere også?",
           "PEERS_open": "Lignende bedrifter i nærheten har ofte åpent {hours}. Passer det for dere?",
           "REVIEW": "En kundeanmeldelse tyder på dette: {hours}. Stemmer det?",
           "USER_EDIT": "En Google-bruker foreslo dette: {hours}. Stemmer det?",
           "DEFAULT": "Forslag: {hours}."},
    "en": {"OWN_LAST_YEAR": "Last year you entered the same hours for this day: {hours}.",
           "OWN_2_SEASONS": "Two seasons ago you entered the same hours for this day: {hours}.",
           "LINKED_HOLIDAY": "Last year you kept these hours on similar holidays: {hours}.",
           "OWNER_ANSWER": "You told us these hours: {hours}.",
           "MODEL": "We estimated these hours from your past opening hours: {hours}.",
           "MALL": "{mall} has published these hours for the day: {hours}.",
           "PEERS_closed": "Most similar businesses nearby are closed that day. Is that true for you too?",
           "PEERS_open": "Similar businesses nearby are often open {hours}. Does that suit you?",
           "REVIEW": "A customer review points to this: {hours}. Is that right?",
           "USER_EDIT": "A Google user suggested this: {hours}. Is that right?",
           "DEFAULT": "Suggestion: {hours}."},
}

# "last Christmas Eve" etc. for the value line (fi uses the essive case).
VALUE_DAY: dict[str, dict[str, str]] = {
    "fi": {"XMAS_EVE": "Viime jouluaattona", "XMAS_DAY": "Viime joulupäivänä", "BOXING_DAY": "Viime tapaninpäivänä",
           "DEC23": "Viime aatonaattona", "NYE": "Viime uudenvuodenaattona", "NEW_YEARS_DAY": "Viime uudenvuodenpäivänä",
           "EPIPHANY_EVE": "Viime loppiaisaattona", "EPIPHANY": "Viime loppiaisena", "FATHERS_DAY": "Viime isänpäivänä",
           "INDEPENDENCE_DAY": "Viime itsenäisyyspäivänä", "BLACK_FRIDAY": "Viime Black Fridayna",
           "INTERDAY": "Viime välipäivinä", "LUCIA": "Viime Lucian päivänä"},
    "sv": {"XMAS_EVE": "På julafton i fjol", "XMAS_DAY": "På juldagen i fjol", "BOXING_DAY": "På annandag jul i fjol",
           "DEC23": "På lillejulafton i fjol", "NYE": "På nyårsafton i fjol", "NEW_YEARS_DAY": "På nyårsdagen i fjol",
           "EPIPHANY_EVE": "På trettondagsafton i fjol", "EPIPHANY": "På trettondedag jul i fjol",
           "FATHERS_DAY": "På fars dag i fjol", "BLACK_FRIDAY": "På Black Friday i fjol",
           "INTERDAY": "Under mellandagarna i fjol", "LUCIA": "På Lucia i fjol",
           "INDEPENDENCE_DAY": "På självständighetsdagen i fjol"},
    "nb": {"XMAS_EVE": "På julaften i fjor", "XMAS_DAY": "På første juledag i fjor", "BOXING_DAY": "På andre juledag i fjor",
           "DEC23": "På lille julaften i fjor", "NYE": "På nyttårsaften i fjor", "NEW_YEARS_DAY": "På første nyttårsdag i fjor",
           "FATHERS_DAY": "På farsdag i fjor", "BLACK_FRIDAY": "På Black Friday i fjor", "INTERDAY": "I romjulen i fjor"},
    "en": {"XMAS_EVE": "last Christmas Eve", "XMAS_DAY": "last Christmas Day", "BOXING_DAY": "last Boxing Day",
           "DEC23": "on 23 Dec last year", "NYE": "last New Year's Eve", "NEW_YEARS_DAY": "last New Year's Day",
           "EPIPHANY_EVE": "on Epiphany Eve last year", "EPIPHANY": "on Epiphany last year",
           "FATHERS_DAY": "last Father's Day", "INDEPENDENCE_DAY": "last Independence Day",
           "BLACK_FRIDAY": "last Black Friday", "INTERDAY": "between Christmas and New Year last year",
           "LUCIA": "on Lucia last year"},
}

# Publisher names in owner messages (publisherId -> short name); AI assistants are translated.
PUBLISHER_SHORT = {"GOOGLEMYBUSINESS": "Google", "APPLE": "Apple Maps", "BING": "Bing", "FACEBOOK": "Facebook",
                   "TRIPADVISOR": "Tripadvisor", "FOURSQUARE": "Foursquare", "WAZE": "Waze",
                   "FONECTA_FINDER": "Finder", "FONECTA_CALLER": "Fonecta Caller", "HITTA": "Hitta",
                   "ENIRO": "Eniro", "GULESIDER": "Gule Sider", "1881": "1881"}
AI_ASSISTANTS = {"fi": "tekoälyavustajat", "sv": "AI-assistenter", "nb": "KI-assistenter", "en": "AI assistants"}
STATUS_TEXT = {
    "fi": {"OPTED_OUT": "ei yhdistetty", "WAITING_ON_PUBLISHER": "odottaa julkaisijaa"},
    "sv": {"OPTED_OUT": "inte ansluten", "WAITING_ON_PUBLISHER": "väntar på publiceraren"},
    "nb": {"OPTED_OUT": "ikke tilkoblet", "WAITING_ON_PUBLISHER": "venter på utgiveren"},
    "en": {"OPTED_OUT": "not connected", "WAITING_ON_PUBLISHER": "pending at the publisher"},
}
ETA_UNITS = {  # (minutes, hours, one day, many days)
    "fi": ("min", "h", "vrk", "vrk"), "sv": ("min", "tim", "dygn", "dygn"),
    "nb": ("min", "t", "døgn", "døgn"), "en": ("min", "h", "day", "days"),
}
FAST_SECONDS = 3600  # "today" changes reach these publishers in time


# ---------------------------------------------------------------------------
# Small helpers: language, dates, hours
# ---------------------------------------------------------------------------
def norm_lang(lang: str | None) -> str:
    """'fi' / 'sv' / 'nb' / 'en'; 'no' and 'nn' count as 'nb'; anything else -> 'en'."""
    lang = (lang or "en").strip().lower()[:2]
    if lang in ("no", "nn"):
        return "nb"
    return lang if lang in LANGS else "en"


def _to_date(d: date | datetime | str) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def fmt_date(d: date | str, lang: str, weekday: bool = True) -> str:
    """'ke 24.12.' (fi), 'ons 24/12' (sv), 'ons. 24.12.' (nb), 'Wed 24 Dec' (en)."""
    d, lang = _to_date(d), norm_lang(lang)
    wd = WD[lang][d.weekday()]
    if lang == "fi":
        core = f"{d.day}.{d.month}."
    elif lang == "sv":
        core = f"{d.day}/{d.month}"
    elif lang == "nb":
        core, wd = f"{d.day}.{d.month}.", wd + "."
    else:
        core = f"{d.day} {EN_MONTHS[d.month - 1]}"
    return f"{wd} {core}" if weekday else core


def fmt_time(t: str, lang: str) -> str:
    """'07:30' -> '7.30' (fi) / '07:30' (others); '13:00' -> '13' (fi)."""
    if norm_lang(lang) != "fi":
        return t
    h, m = t.split(":")
    return str(int(h)) + ("" if m == "00" else "." + m)


def fmt_hours(h: str | None, lang: str) -> str:
    """Canonical hours string -> local text: '07:30-13:00' -> '7.30–13' (fi) / '07:30–13:00'."""
    lang = norm_lang(lang)
    if h is None:
        return "–"
    s = str(h).strip()
    if s.lower() == "closed":
        return T[lang]["closed"]
    if s.lower() == "regular":
        return T[lang]["regular"]
    out = []
    for iv in s.split(","):
        a, b = iv.strip().split("-")
        if a == "00:00" and b == "23:59":
            out.append("0–24" if lang == "fi" else "00:00–24:00")
        else:
            out.append(f"{fmt_time(a, lang)}–{fmt_time(b, lang)}")
    return ", ".join(out)


def fmt_eta(seconds: int | float, lang: str) -> str:
    """120 -> '~2 min'; 3600 -> '~1 h'; 172800 -> '~2 vrk' / '~2 dygn' / '~2 døgn' / '~2 days'."""
    lang = norm_lang(lang)
    mi, hr, day1, days = ETA_UNITS[lang]
    s = max(0.0, float(seconds))
    if s < 3600:
        return f"~{max(1, round(s / 60))} {mi}"
    if s < 86400:
        return f"~{max(1, round(s / 3600))} {hr}"
    n = max(1, round(s / 86400))
    return f"~{n} {day1 if n == 1 else days}"


def publisher_name(row: dict, lang: str) -> str:
    pid = row.get("publisher_id") or row.get("publisherId") or ""
    if pid == "CHATGPT_SEARCH":
        return AI_ASSISTANTS[norm_lang(lang)]
    return PUBLISHER_SHORT.get(pid) or row.get("publisher") or row.get("publisherName") or pid


def _row_seconds(row: dict) -> int:
    for k in ("typical_seconds", "typicalUpdateSpeedSeconds", "seconds"):
        if row.get(k) is not None:
            return int(row[k])
    return 0


def _business(business: Any) -> dict:
    if business is None:
        return {}
    if isinstance(business, str):
        return {"name": business}
    return dict(business)


def _brand(biz: dict) -> str:
    return biz.get("brand") or DEFAULT_BRAND


def _get(s: Any, name: str, default=None):
    if isinstance(s, dict):
        return s.get(name, default)
    return getattr(s, name, default)


def _basis(s: Any) -> str:
    b = (_get(s, "basis") or "").upper()
    return BASIS_ALIASES.get(b, b)


def _is_closed(v: str | None) -> bool:
    return str(v or "").strip().lower() == "closed"


def season_title(dates: Iterable[date], lang: str, *, lower: bool = False) -> str:
    """'Joulun ja uudenvuoden aukioloajat' if every date is 20 Dec – 7 Jan, else the generic title."""
    lang = norm_lang(lang)
    ds = list(dates)
    xmas = bool(ds) and all((d.month == 12 and d.day >= 20) or (d.month == 1 and d.day <= 7) for d in ds)
    t = T[lang]["title_xmas" if xmas else "title_generic"]
    if lower and not t.startswith("Christmas"):
        t = t[0].lower() + t[1:]
    return t


def short_reason(s: Any, lang: str, mall_name: str | None = None) -> str:
    """The short reason shown in brackets on a date line (by basis)."""
    lang = norm_lang(lang)
    R = REASONS[lang]
    basis, value = _basis(s), _get(s, "value")
    mall = mall_name or R["mall_fallback"]
    if basis == "MALL":
        return R["MALL_closed" if _is_closed(value) else "MALL_open"].format(mall=mall)
    if basis == "PEERS":
        return R["PEERS_closed" if _is_closed(value) else "PEERS_open"]
    return R.get(basis, R["DEFAULT"])


def reason_sentence(s: Any, lang: str, mall_name: str | None = None) -> str:
    """A full reason sentence for the card page ("Why ▸"); the only text an LLM may polish."""
    lang = norm_lang(lang)
    R = REASON_SENTENCES[lang]
    basis, value = _basis(s), _get(s, "value")
    hours = fmt_hours(value, lang)
    mall = mall_name or REASONS[lang]["mall_fallback"]
    if basis == "PEERS":
        key = "PEERS_closed" if _is_closed(value) else "PEERS_open"
    else:
        key = basis if basis in R else "DEFAULT"
    text = R[key].format(hours=hours, mall=mall)
    return text[0].upper() + text[1:]


def asks(s: Any) -> bool:
    """True if the line is phrased as a question (evidence other than the owner's own past)."""
    return _basis(s) in ASK_BASES


def make_buttons(ids: Iterable[str], lang: str) -> list[dict]:
    lang = norm_lang(lang)
    return [{"id": i, "title": BUTTONS[lang][i], "digit": str(n)} for n, i in enumerate(ids, 1)]


def _digit_line(buttons: list[dict], lang: str) -> str:
    items = ", ".join(f"{b['digit']} = {b['title']}" for b in buttons)
    return T[norm_lang(lang)]["digits"].format(items=items)


def _finish(text_lines: list[str], buttons: list[dict], channel: str, lang: str, parts: dict) -> dict:
    """Common tail: SMS gets digits instead of buttons; WhatsApp keeps at most 3 buttons."""
    channel = (channel or "OUTBOX").upper()
    lines = [x for x in text_lines if x]
    if channel == "SMS" and buttons:
        lines.append(_digit_line(buttons, lang))
        parts["reply_digits"] = {b["digit"]: b["id"] for b in buttons}
        buttons = []
    elif channel == "WHATSAPP" and len(buttons) > WHATSAPP_MAX_BUTTONS:
        parts["dropped_buttons"] = [b["id"] for b in buttons[WHATSAPP_MAX_BUTTONS:]]
        buttons = buttons[:WHATSAPP_MAX_BUTTONS]
    text = "\n".join(lines)
    parts.update({"channel": channel, "lang": norm_lang(lang), "length": len(text)})
    return {"text": text, "buttons": buttons, "parts": parts}


# ---------------------------------------------------------------------------
# The season card message (O1)
# ---------------------------------------------------------------------------
@dataclass
class _Line:
    date: date
    hours: str | None          # canonical value (None for a pure question line)
    text_full: str
    text_bare: str             # without the reason
    reason: str | None
    basis: str
    question: bool = False
    extra: dict = field(default_factory=dict)


def _card_lines(suggestions: list, lang: str, mall_name: str | None, question: dict | None) -> list[_Line]:
    lang = norm_lang(lang)
    qdate = _to_date(question["date"]) if question and question.get("date") else None
    lines: list[_Line] = []
    seen: set[date] = set()
    for s in sorted(suggestions, key=lambda x: _to_date(_get(x, "date"))):
        d = _to_date(_get(s, "date"))
        if d in seen:
            continue
        seen.add(d)
        value = _get(s, "value")
        is_q = qdate == d
        mark = " ?" if (asks(s) or is_q) else ""
        bare = f"• {fmt_date(d, lang)} {fmt_hours(value, lang)}{mark}"
        reason = T[lang]["q_marker"] if is_q else short_reason(s, lang, mall_name)
        full = f"{bare} {reason}" if is_q else f"{bare} ({reason})"
        lines.append(_Line(d, value, full, bare if not is_q else full, None if is_q else reason, _basis(s), is_q))
    if qdate is not None and qdate not in seen:
        cand = question.get("candidate")
        hours_txt = f" {fmt_hours(cand, lang)}" if cand else ""
        full = f"• {fmt_date(qdate, lang)}{hours_txt} ? {T[lang]['q_marker']}"
        lines.append(_Line(qdate, cand, full, full, None, "QUESTION", True))
        lines.sort(key=lambda ln: ln.date)
    return lines


def _shown_line(suggestions: list, lang: str, grouped: bool, max_items: int | None) -> str | None:
    lang = norm_lang(lang)
    pairs = []
    seen = set()
    for s in sorted(suggestions, key=lambda x: _to_date(_get(x, "date"))):
        d, shown = _to_date(_get(s, "date")), _get(s, "shown")
        if shown is None or d in seen:
            continue
        seen.add(d)
        pairs.append((d, shown))
    if not pairs:
        return None
    values = {v for _, v in pairs}
    if len(values) == 1 and len(pairs) > 1:
        return T[lang]["shown_same"].format(hours=fmt_hours(pairs[0][1], lang))
    if grouped and len(pairs) > 1:
        groups: dict[str, list[date]] = {}
        for d, v in pairs:
            groups.setdefault(v, []).append(d)
        items = []
        for v, ds in groups.items():
            shown_ds = ds if max_items is None else ds[:max_items]
            more = "" if len(shown_ds) == len(ds) else " …"
            items.append(f"{fmt_hours(v, lang)} ({', '.join(fmt_date(d, lang) for d in shown_ds)}{more})")
        return T[lang]["shown_list"].format(items="; ".join(items))
    shown_pairs = pairs if max_items is None else pairs[:max_items]
    items = " · ".join(f"{fmt_date(d, lang)} {fmt_hours(v, lang)}" for d, v in shown_pairs)
    if len(shown_pairs) < len(pairs):
        items += " · …"
    return T[lang]["shown_list"].format(items=items)


def _value_line(n: int | None, day_key: str | None, lang: str) -> str | None:
    if n is None:
        return None
    lang = norm_lang(lang)
    phrase = VALUE_DAY[lang].get((day_key or "").upper())
    if phrase:
        return T[lang]["value_day"].format(phrase=phrase, n=int(n))
    return T[lang]["value_generic"].format(n=int(n))


def _deadline_line(deadline: date | None, lang: str, today: date) -> str | None:
    if deadline is None:
        return None
    deadline = _to_date(deadline)
    if deadline < today:
        return T[lang]["deadline_past"]
    return T[lang]["deadline"].format(date=fmt_date(deadline, lang))


def compose_card_message(business, suggestions: list, lang: str, channel: str = "WHATSAPP",
                         first_contact: bool = True, question: dict | None = None,
                         deadline: date | None = None, value_line_views: int | None = None, *,
                         value_line_day: str | None = None, link: str | None = None,
                         today: date | None = None, limit: int | None = None) -> dict:
    """The season card as one message.

    ``business``: ``{"name", "mall_name"?, "brand"?, "entity_id"?}`` (or just the name).
    ``suggestions``: ``Suggestion`` objects or dicts with ``date, value, shown, basis, action``;
    only ``action == "SUGGEST"`` rows with a value are used (FLAG never becomes a message).
    ``question``: ``{"date", "candidate"?}`` — the one question picked by the card builder.
    ``deadline``: answer-by date (``cs.confirm.deadline_for`` / ``cs.deadlines``).
    ``value_line_views`` + ``value_line_day`` (a day_key): "~140 people looked you up last Christmas Eve".
    Returns ``{"text", "buttons", "parts"}``; ``parts["level"]`` tells how much was compacted
    to fit the channel limit (WhatsApp 1,024, SMS 480).
    """
    lang = norm_lang(lang)
    channel = (channel or "OUTBOX").upper()
    biz = _business(business)
    today = today or date.today()
    sugs = [s for s in suggestions
            if (_get(s, "action") or "SUGGEST") == "SUGGEST" and _get(s, "value") is not None
            and _get(s, "date")]
    if not sugs and not (question and question.get("date")):
        raise ValueError("nothing to send: no SUGGEST rows and no question")
    lines = _card_lines(sugs, lang, biz.get("mall_name"), question)
    dates = [ln.date for ln in lines]
    title = season_title(dates, lang)
    limit = limit if limit is not None else CHANNEL_LIMITS.get(channel)
    buttons = make_buttons(CARD_BUTTON_IDS, lang)

    header = T[lang]["disclosure"].format(brand=_brand(biz)) if first_contact else None
    greeting = T[lang]["hi"].format(name=biz.get("name") or "").replace(" !", "!")
    intro = f"{greeting} {T[lang]['intro'].format(title=title)}".strip()
    deadline_txt = _deadline_line(deadline, lang, today)
    value_txt = _value_line(value_line_views, value_line_day, lang)
    link_txt = T[lang]["link"].format(link=link) if link else None
    own_words = T[lang]["own_words"] if channel in ("WHATSAPP", "SMS") else None
    scope = T[lang]["scope"]

    # Compaction steps, applied in order until the text fits the channel limit; after the last
    # step, date lines are elided from the end ("+ N more dates"). WhatsApp always keeps the
    # "own words / no thanks" line because it has no "No thanks" button.
    steps = ["collapse", "shown_short", "no_scope", "no_reasons", "no_value"]
    if channel != "WHATSAPP":
        steps.insert(2, "no_own_words")

    def render(on: set[str], keep_lines: int | None) -> tuple[list[str], dict]:
        reasons = [ln.reason for ln in lines if ln.reason]
        same_reason = len(lines) >= 2 and len(set(reasons)) == 1 and len(reasons) == len(lines)
        collapse = "collapse" in on and same_reason and "no_reasons" not in on
        kept = lines if keep_lines is None else lines[:keep_lines]
        body = [ln.text_bare if ("no_reasons" in on or collapse) else ln.text_full for ln in kept]
        if keep_lines is not None and keep_lines < len(lines):
            body.append(T[lang]["more"].format(n=len(lines) - keep_lines))
        if collapse:
            body.append(T[lang]["all_reason"].format(reason=reasons[0]))
        kept_dates = {ln.date for ln in kept}
        shown_txt = _shown_line([s for s in sugs if _to_date(_get(s, "date")) in kept_dates], lang,
                                grouped="collapse" in on, max_items=2 if "shown_short" in on else None)
        p = {"header": header, "intro": intro, "lines": body, "shown": shown_txt, "deadline": deadline_txt,
             "value": None if "no_value" in on else value_txt,
             "scope": None if "no_scope" in on else scope,
             "own_words": None if "no_own_words" in on else own_words,
             "link": link_txt, "dates_shown": [ln.date.isoformat() for ln in kept]}
        out = [header, intro, *body, shown_txt, deadline_txt, p["value"], p["scope"], p["own_words"], link_txt]
        return out, p

    def total_len(out: list[str]) -> int:
        n = len("\n".join(x for x in out if x))
        if channel == "SMS":
            n += 1 + len(_digit_line(buttons, lang))
        return n

    on: set[str] = set()
    keep = None
    out, parts = render(on, keep)
    if limit:
        pending = list(steps)
        while total_len(out) > limit:
            if pending:
                on.add(pending.pop(0))
            else:
                keep = (len(lines) if keep is None else keep) - 1
                if keep < 1:
                    break
            out, parts = render(on, keep)
    level = len(on) + (0 if keep is None else len(lines) - keep)
    parts.update({
        "level": level, "compaction": [x for x in steps if x in on], "limit": limit,
        "first_contact": bool(first_contact),
        "dates": [d.isoformat() for d in dates], "elided": 0 if keep is None else len(lines) - keep,
        "subject": T[lang]["subject"].format(title=title), "title": title,
        "reasons": {_to_date(_get(s, "date")).isoformat(): reason_sentence(s, lang, biz.get("mall_name"))
                    for s in sugs},
        "question": question,
        "template_params": compose_template_params(biz, sugs, lang, deadline, question, today=today),
    })
    return _finish(out, buttons, channel, lang, parts)


# ---------------------------------------------------------------------------
# WhatsApp template parameter (first contact, outside the 24-hour session)
# ---------------------------------------------------------------------------
# Template params may not contain newlines or tabs or more than 4 spaces in a row, and the
# template body can't start or end with a variable, so the first-contact template carries a
# one-line summary in {{1}}; the full card follows as an interactive message once the owner's
# first tap opens the session. Template texts: docs/WHATSAPP_TEMPLATE.md.
TPL_DEADLINE = {"fi": "vastaa viimeistään {date}", "sv": "svara senast {date}", "nb": "svar innen {date}",
                "en": "please answer by {date}"}
TPL_SOON = {"fi": "vastaathan pian", "sv": "svara gärna snart", "nb": "svar gjerne snart", "en": "please answer soon"}
_WS_BREAK_RE = re.compile(r"[\r\n\v\f\u2028\u2029]+")  # line breaks -> " · "; tabs -> a space
_SEP_RUN_RE = re.compile(r"(?:\s*·\s*){2,}")


def sanitize_template_param(text: str | None, max_len: int = TEMPLATE_PARAM_LIMIT) -> str:
    """One WhatsApp template parameter: no newlines or tabs (line breaks become " · ", tabs a
    space), every run of whitespace collapsed to one space, at most ``max_len`` characters, never empty."""
    s = _WS_BREAK_RE.sub(" · ", str(text or ""))
    s = re.sub(r"\s+", " ", s)
    s = _SEP_RUN_RE.sub(" · ", s).strip(" ·")
    if len(s) > max_len:
        s = s[:max_len - 1].rstrip(" ·") + "…"
    return s or "-"


def compose_template_params(business, suggestions: list, lang: str, deadline: date | None = None,
                            question: dict | None = None, *, today: date | None = None,
                            max_len: int = TEMPLATE_PARAM_LIMIT) -> list[str]:
    """``[{{1}}]`` for the ``connect_season_card`` template: the dates as one line, e.g.
    "to 24.12. 7.30–13 · pe 25.12. kiinni · pe 1.1. kiinni · ke 6.1. 10–16 · vastaa viimeistään to 17.12".

    The template text follows ``{{1}}`` with ".", so a trailing full stop is removed here.
    If the line would be longer than ``max_len``, dates are cut from the end ("+ N muuta päivää")."""
    lang = norm_lang(lang)
    today = today or date.today()
    sugs = [s for s in suggestions
            if (_get(s, "action") or "SUGGEST") == "SUGGEST" and _get(s, "value") is not None and _get(s, "date")]
    qdate = _to_date(question["date"]) if question and question.get("date") else None
    items: list[tuple[date, str]] = []
    seen: set[date] = set()
    for s in sorted(sugs, key=lambda x: _to_date(_get(x, "date"))):
        d = _to_date(_get(s, "date"))
        if d in seen:
            continue
        seen.add(d)
        mark = " ?" if (asks(s) or d == qdate) else ""
        items.append((d, f"{fmt_date(d, lang)} {fmt_hours(_get(s, 'value'), lang)}{mark}"))
    if qdate is not None and qdate not in seen:
        cand = question.get("candidate")
        items.append((qdate, f"{fmt_date(qdate, lang)}{' ' + fmt_hours(cand, lang) if cand else ''} ?"))
        items.sort(key=lambda x: x[0])
    tail = []
    if deadline is not None:
        dl = _to_date(deadline)
        tail = [TPL_SOON[lang] if dl < today else TPL_DEADLINE[lang].format(date=fmt_date(dl, lang))]
    texts = [t for _, t in items]
    keep = len(texts)
    while True:
        more = [T[lang]["more"].format(n=len(texts) - keep)] if keep < len(texts) else []
        line = " · ".join(texts[:keep] + more + tail)
        if len(line) <= max_len or keep <= 1:
            break
        keep -= 1
    return [sanitize_template_param(line, max_len).rstrip(".")]


# ---------------------------------------------------------------------------
# The one question (B1) and the new-customer question (O6)
# ---------------------------------------------------------------------------
def compose_question(question: dict, lang: str, channel: str = "WHATSAPP") -> dict:
    """``{"date", "regular"?, "candidate"?}`` -> "Yksi kysymys: miten olette auki la 26.12.?
    1 = kiinni · 2 = tavalliset ajat (10–16) · 3 = muut ajat".
    ``{"kind": "HABIT"}`` -> "Are you usually closed on public holidays? 1 = yes · 2 = no · 3 = it varies"."""
    lang = norm_lang(lang)
    B = BUTTONS[lang]
    if (question.get("kind") or "DAY").upper() == "HABIT":
        ids = ("H_YES", "H_NO", "H_VARIES")
        head = T[lang]["q_habit"]
        opts = [B[i].lower() for i in ids]
        qdate = None
    else:
        ids = ("Q_CLOSED", "Q_REGULAR", "Q_OTHER")
        qdate = _to_date(question["date"])
        head = T[lang]["q_day"].format(date=fmt_date(qdate, lang))
        regular = question.get("regular")
        cand = question.get("candidate")
        opts = [T[lang]["closed"],
                T[lang]["regular"] + (f" ({fmt_hours(regular, lang)})" if regular and not _is_closed(regular) else ""),
                T[lang]["q_other_eg"].format(hours=fmt_hours(cand, lang)) if cand and not _is_closed(cand)
                else B["Q_OTHER"].lower()]
    options = " · ".join(f"{n} = {o}" for n, o in enumerate(opts, 1))
    buttons = make_buttons(ids, lang)
    parts = {"question": head, "options": options, "date": qdate.isoformat() if qdate else None,
             "reply_digits": {b["digit"]: b["id"] for b in buttons}}
    # The digits are always in the text (they double as the SMS legend), so no extra line on SMS.
    channel = (channel or "OUTBOX").upper()
    res = _finish([head, options], [] if channel == "SMS" else buttons, channel, lang, parts)
    return res


# ---------------------------------------------------------------------------
# Confirmation (O3), follow-ups, emergency (O4), autopilot (O5)
# ---------------------------------------------------------------------------
def _sentence(text: str) -> str:
    """End with exactly one full stop ('… 12.12.' stays, '… kiinni' gets one)."""
    return text if text.endswith((".", "!", "?")) else text + "."


def _changes_list(changes, lang: str) -> list[tuple[date, str]]:
    """dict {date: hours} or a list of Suggestions/dicts -> sorted [(date, hours)]."""
    if not changes:
        return []
    if isinstance(changes, dict):
        items = [(_to_date(k), v) for k, v in changes.items()]
    else:
        items = [(_to_date(_get(c, "date")), _get(c, "value") if _get(c, "value") is not None else _get(c, "hours"))
                 for c in changes]
    return sorted(items, key=lambda x: x[0])


def _changes_text(changes, lang: str) -> str:
    return ", ".join(f"{fmt_date(d, lang)} {fmt_hours(v, lang)}" for d, v in _changes_list(changes, lang))


def shows_on_text(where_it_shows: list[dict], lang: str, *, only_fast: bool = False) -> tuple[str | None, str | None]:
    """('Google ~2 min · Finder ~10 min · …', 'Facebook: ei yhdistetty') from where_it_shows rows."""
    lang = norm_lang(lang)
    live: dict[str, list[str]] = {}
    order: list[tuple[int, str]] = []
    not_live = []
    for r in where_it_shows or []:
        status = (r.get("status") or "LIVE").upper()
        name = publisher_name(r, lang)
        if status != "LIVE":
            not_live.append(f"{name}: {STATUS_TEXT[lang].get(status, status.lower())}")
            continue
        secs = _row_seconds(r)
        if only_fast and secs > FAST_SECONDS:
            continue
        eta = fmt_eta(secs, lang)
        if eta not in live:
            live[eta] = []
            order.append((secs, eta))
        live[eta].append(name)
    order.sort()
    shows = " · ".join(f"{', '.join(live[eta])} {eta}" for _, eta in order) or None
    return shows, ("; ".join(not_live) or None)


def compose_confirmation(business, changes, where_it_shows: list[dict], lang: str,
                         channel: str = "WHATSAPP", *, every_year: bool = False) -> dict:
    """O3: "✓ Saved: Thu 24 Dec 07:30–13:00. Shows on: Google ~2 min · … (Facebook: not connected)" + [Undo].

    ``changes``: {date: hours} or the approved Suggestions. ``where_it_shows``: the rows of
    ``cs.confirm.where_it_shows`` (``publisher_id, publisher, status, typical_seconds``) or plain
    dicts with ``publisherId/publisherName/status/typicalUpdateSpeedSeconds``."""
    lang = norm_lang(lang)
    shows, not_live = shows_on_text(where_it_shows, lang)
    lines = [_sentence(T[lang]["saved"].format(items=_changes_text(changes, lang))),
             T[lang]["shows_on"].format(items=shows) if shows else None,
             T[lang]["not_live"].format(items=not_live) if not_live else None,
             T[lang]["scope_granted"] if every_year else None,
             T[lang]["undo_hint"]]
    parts = {"saved": lines[0], "shows_on": lines[1], "not_live": lines[2], "every_year": every_year,
             "changes": [(d.isoformat(), v) for d, v in _changes_list(changes, lang)]}
    return _finish(lines, make_buttons(("UNDO",), lang), channel, lang, parts)


FOLLOWUPS = ("APPROVE", "APPROVE_EVERY_YEAR", "EDIT", "DECLINE", "REMINDER", "STOP", "UNDO")


def compose_followup(decision: str, lang: str = "fi", *, business=None, channel: str = "WHATSAPP",
                     values: dict | None = None, deadline: date | None = None, link: str | None = None,
                     dates: Iterable[date | str] | None = None, today: date | None = None) -> dict:
    """The reply after an owner's decision, or the reminder.

    - ``APPROVE`` / ``APPROVE_EVERY_YEAR`` (adds the permission scope) — thanks, confirmation follows;
    - ``EDIT`` — with ``values`` ({date: hours}) a read-back "Shall we save this?" [Yes][No]
      (B6: repeat before saving); without, a prompt (and ``link`` if given);
    - ``DECLINE`` — nothing changes; ``STOP`` — no more suggestions; ``UNDO`` — restoring;
    - ``REMINDER`` — "your Christmas hours are waiting", the deadline, and the card buttons.
    """
    lang = norm_lang(lang)
    decision = (decision or "").upper()
    if decision not in FOLLOWUPS:
        raise ValueError(f"unknown decision {decision!r}; one of {FOLLOWUPS}")
    t = T[lang]
    today = today or date.today()
    buttons: list[dict] = []
    lines: list[str | None]
    if decision == "APPROVE":
        lines = [t["approve"]]
    elif decision == "APPROVE_EVERY_YEAR":
        lines = [t["approve"], t["scope_granted"], t["undo_hint"]]
    elif decision == "EDIT":
        if values:
            lines = [t["readback"], *(f"• {fmt_date(d, lang)} {fmt_hours(v, lang)}"
                                      for d, v in _changes_list(values, lang))]
            buttons = make_buttons(("YES", "NO"), lang)
        else:
            lines = [t["edit_link"].format(link=link) if link else t["edit_prompt"]]
    elif decision == "DECLINE":
        lines = [t["decline"]]
    elif decision == "STOP":
        lines = [t["stop"]]
    elif decision == "UNDO":
        lines = [t["undone"]]
    else:  # REMINDER
        ds = [_to_date(d) for d in (dates or (values or {}).keys())]
        title = season_title(ds, lang, lower=True) if ds else T[lang]["title_generic"].lower()
        lines = [t["reminder"].format(title=title),
                 *(f"• {fmt_date(d, lang)} {fmt_hours(v, lang)}" for d, v in _changes_list(values, lang)),
                 _deadline_line(deadline, lang, today),
                 t["link"].format(link=link) if link else None]
        buttons = make_buttons(CARD_BUTTON_IDS, lang)
    parts = {"decision": decision, "values": {d.isoformat(): v for d, v in _changes_list(values, lang)}}
    return _finish(lines, buttons, channel, lang, parts)


def compose_emergency(business, day: date | str, lang: str, *, hours: str = "closed",
                      until: date | str | None = None, until_further_notice: bool = False,
                      today: date | None = None, where_it_shows: list[dict] | None = None,
                      channel: str = "WHATSAPP") -> dict:
    """O4: "✓ Saved: closed today, Sat 12 Dec. Normal hours return tomorrow. Shows on: Google ~2 min" + [Undo].

    Deliberately has **no reason parameter**: the owner's reason ("I'm sick") is never stored
    or shown (GDPR special-category data, PLAN Part F). ``until`` is the last day of a
    multi-day closure (inclusive); ``until_further_notice`` adds the daily "open again?" check.
    Only fast publishers (≤ 1 h) are listed as updating; slower ones are named honestly."""
    lang = norm_lang(lang)
    t = T[lang]
    d = _to_date(day)
    today = today or date.today()
    end = _to_date(until) if until else d
    ds = fmt_date(d, lang)
    if until_further_notice:
        what = t["em_open_ended"].format(date=ds)
        back = t["em_ask_daily"]
    elif _is_closed(hours):
        if end > d:
            what = t["em_range"].format(d1=ds, d2=fmt_date(end, lang))
        elif d == today:
            what = t["em_today"].format(date=ds)
        elif d == today + timedelta(days=1):
            what = t["em_tomorrow"].format(date=ds)
        else:
            what = t["em_day"].format(date=ds)
        nxt = end + timedelta(days=1)
        back = t["em_back_tomorrow"] if nxt == today + timedelta(days=1) else t["em_back_on"].format(date=fmt_date(nxt, lang))
    else:
        what = (t["em_open_today"] if d == today else t["em_open_day"]).format(date=ds, hours=fmt_hours(hours, lang))
        nxt = end + timedelta(days=1)
        back = t["em_back_tomorrow"] if nxt == today + timedelta(days=1) else t["em_back_on"].format(date=fmt_date(nxt, lang))
    saved = _sentence(t["saved"].format(items=what))
    fast, _ = shows_on_text(where_it_shows or [], lang, only_fast=True)
    slow_rows = [r for r in (where_it_shows or []) if (r.get("status") or "LIVE").upper() == "LIVE"
                 and _row_seconds(r) > FAST_SECONDS]
    slow_rows.sort(key=_row_seconds)
    slow = ", ".join(f"{publisher_name(r, lang)} {fmt_eta(_row_seconds(r), lang)}" for r in slow_rows[:3])
    lines = [saved, back,
             t["shows_on"].format(items=fast) if fast else None,
             t["em_slow"].format(items=slow) if slow else None]
    parts = {"saved": saved, "back": back, "shows_on": fast, "slow": slow or None,
             "date": d.isoformat(), "until": end.isoformat(), "until_further_notice": until_further_notice}
    return _finish(lines, make_buttons(("UNDO",), lang), channel, lang, parts)


def compose_autopilot_note(business, applied, lang: str, *, channel: str = "WHATSAPP",
                           where_it_shows: list[dict] | None = None, short_notice: bool = False,
                           first_contact: bool = False) -> dict:
    """O5: "We set your Christmas and New Year hours as agreed: Thu 24 Dec closed, … [Undo] [Stop doing this]".

    ``applied``: {date: hours} or the applied Suggestions. ``short_notice``: the date was < 14 days
    away, so the hours were applied at once (logged by the caller)."""
    lang = norm_lang(lang)
    t = T[lang]
    biz = _business(business)
    items = _changes_list(applied, lang)
    title = season_title([d for d, _ in items], lang, lower=True)
    shows, _ = shows_on_text(where_it_shows or [], lang)
    lines = [T[lang]["disclosure"].format(brand=_brand(biz)) if first_contact else None,
             T[lang]["hi"].format(name=biz.get("name") or "").replace(" !", "!"),
             t["auto_saved"].format(title=title),
             *(f"• {fmt_date(d, lang)} {fmt_hours(v, lang)}" for d, v in items),
             t["auto_short"] if short_notice else None,
             t["shows_on"].format(items=shows) if shows else None,
             t["auto_hint"]]
    parts = {"applied": [(d.isoformat(), v) for d, v in items], "short_notice": short_notice}
    return _finish(lines, make_buttons(("UNDO", "STOP"), lang), channel, lang, parts)


# ---------------------------------------------------------------------------
# Slot extraction: every date, time and number in a text (used by tests and cs.llm.slot_check)
# ---------------------------------------------------------------------------
_TIME = r"(\d{1,2})(?:[:.](\d{2}))?"
_RANGE_RE = re.compile(rf"(?<![\d.:/]){_TIME}\s*[–—‒−-]\s*{_TIME}(?!\d)")
_DATE_NUM_RE = re.compile(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})(?:\.(?!\d))?(?![\d/]|:\d)")
_MONTH_WORDS = {
    1: ("jan", "januari", "january", "januar"), 2: ("feb", "februari", "february", "februar"),
    3: ("mar", "mars", "march"), 4: ("apr", "april"), 5: ("maj", "may", "mai"), 6: ("jun", "juni", "june"),
    7: ("jul", "juli", "july"), 8: ("aug", "augusti", "august"), 9: ("sep", "sept", "september"),
    10: ("okt", "oct", "oktober", "october"), 11: ("nov", "november"), 12: ("dec", "december", "des", "desember"),
}
_FI_MONTHS = ("tammi", "helmi", "maalis", "huhti", "touko", "kesä", "heinä", "elo", "syys", "loka", "marras", "joulu")
_MONTH_ALT = ("(?:" + "|".join(sorted({w for ws in _MONTH_WORDS.values() for w in ws}, key=len, reverse=True))
              + r")\b\.?|(?:" + "|".join(_FI_MONTHS) + r")kuu\w*")
_DATE_DM_RE = re.compile(rf"(?<![\d.:/])(\d{{1,2}})\.?\s+({_MONTH_ALT})", re.IGNORECASE)
_DATE_MD_RE = re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)", re.IGNORECASE)
_TIME_RE = re.compile(r"(?<![\d.:/])(\d{1,2})[:.](\d{2})(?!\d)")
_NUM_RE = re.compile(r"\d+")
_URL_RE = re.compile(r"https?://\S+")


def _month_of(word: str) -> int | None:
    w = word.lower().rstrip(".")
    for m, words in _MONTH_WORDS.items():
        if w in words:
            return m
    for i, p in enumerate(_FI_MONTHS, 1):
        if w.startswith(p + "kuu"):
            return i
    return None


def _canon_interval(h1, m1, h2, m2) -> str | None:
    try:
        return normalise(f"{int(h1)}:{m1 or '00'}-{int(h2)}:{m2 or '00'}")
    except (ValueError, TypeError):
        return None


@dataclass
class Slots:
    intervals: set[str] = field(default_factory=set)        # canonical 'HH:MM-HH:MM'
    dates: set[tuple[int, int]] = field(default_factory=set)  # (day, month)
    times: set[str] = field(default_factory=set)            # standalone 'HH:MM'
    numbers: set[int] = field(default_factory=set)          # any other number

    def as_dict(self) -> dict:
        return {"intervals": sorted(self.intervals), "dates": sorted(self.dates),
                "times": sorted(self.times), "numbers": sorted(self.numbers)}


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def extract_slots(text: str) -> Slots:
    """Every time range, date, standalone time and number in ``text`` (any of the 4 languages).

    Time ranges are read first ('7.30–13', '07:30–13:00', '0–24'), then dates ('24.12.',
    '24/12', '24 Dec', 'Dec 24'), then lone times ('14:00'), then bare numbers."""
    s = Slots()
    text = _URL_RE.sub(" ", text or "")  # digits inside links are not facts
    spans = []
    for m in _RANGE_RE.finditer(text):
        iv = _canon_interval(*m.groups())
        if iv:
            for part in iv.split(","):
                s.intervals.add(part)
            spans.append(m.span())
    text = _blank(text, spans)
    spans = []
    for m in _DATE_NUM_RE.finditer(text):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            s.dates.add((d, mo))
            spans.append(m.span())
    for m in _DATE_DM_RE.finditer(text):
        mo = _month_of(m.group(2))
        if mo and 1 <= int(m.group(1)) <= 31:
            s.dates.add((int(m.group(1)), mo))
            spans.append(m.span())
    for m in _DATE_MD_RE.finditer(text):
        mo = _month_of(m.group(1))
        if mo and 1 <= int(m.group(2)) <= 31:
            s.dates.add((int(m.group(2)), mo))
            spans.append(m.span())
    text = _blank(text, spans)
    spans = []
    for m in _TIME_RE.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 24 and mi <= 59:
            s.times.add(f"{h:02d}:{mi:02d}")
            spans.append(m.span())
    text = _blank(text, spans)
    s.numbers = {int(x) for x in _NUM_RE.findall(text)}
    return s


def value_intervals(value: str | None) -> set[str]:
    """Canonical intervals of an hours value ('closed' / None -> empty set)."""
    if not value or _is_closed(value) or str(value).lower() == "regular":
        return set()
    return {iv for iv in normalise(value).split(",")}


# ---------------------------------------------------------------------------
# CLI: build the season message for a demo business
# ---------------------------------------------------------------------------
DEMO_IDS = ("FI-D0001", "FI-D0002", "FI-D0003", "FI-D0004", "FI-D0005", "FI-D0006", "FI-D0007",
            "SE-D0008", "NO-D0009", "FI-D0010", "FI-D0011", "FI-D0012", "FI-D0013", "FI-D0014",
            "NO-D0015", "SE-D0016", "FI-D0017")
_BASELINE_BASIS = {"LAST_YEAR": "OWN_LAST_YEAR", "SHOPPING_CENTRE": "MALL", "PEERS": "PEERS",
                   "CLOSED_SIGNALS": "REVIEW"}


def _engine_rows(entity_id: str) -> list[dict] | None:
    """Rows for one business from artifacts/predictions/engine_2026.parquet, or None if missing."""
    from cs.config import settings
    path = settings.root / "artifacts" / "predictions" / "engine_2026.parquet"
    if not path.exists():
        return None
    import pandas as pd
    df = pd.read_parquet(path)
    df = df[df["entity_id"] == entity_id]
    rows = []
    for r in df.to_dict("records"):
        ev = r.get("evidence")
        r["evidence"] = [dict(e) for e in ev] if ev is not None else []
        rows.append(r)
    return rows


def _baseline_rows(entity_id: str) -> list[dict]:
    """Fallback: Fonecta's baseline (dataset examples/baseline.py) for every 2026 target row."""
    from cs import data
    sys.path.insert(0, str(data.dataset_path("examples")))
    from baseline import Baseline  # type: ignore  # noqa: E402
    bl = _baseline_rows.__dict__.setdefault("bl", Baseline(2026))
    rows = []
    t = data.targets(2026)
    for r in t[t["entity_id"] == entity_id].to_dict("records"):
        act, hours, basis, ev = bl.explain(entity_id, date.fromisoformat(r["date"]), r["day_key"])
        rows.append({"entity_id": entity_id, "date": r["date"], "day_key": r["day_key"], "action": act,
                     "hours": hours or None, "probability": None, "basis": _BASELINE_BASIS.get(basis or "", basis or ""),
                     "evidence": [{"source": _BASELINE_BASIS.get(basis or "", basis or ""), "detail": str(ev),
                                   "ref": None, "weight": 1.0}] if basis else []})
    return rows


def suggestions_for(entity_id: str, as_of: date | str = "2026-11-06") -> tuple[list, str]:
    """(Suggestions with ``shown`` filled in, source) for the demo CLI: engine parquet, else baseline."""
    from cs import display
    from cs.domain import Evidence, Suggestion
    rows = _engine_rows(entity_id)
    source = "engine_2026.parquet"
    if rows is None:
        rows, source = _baseline_rows(entity_id), "baseline"
    out = []
    for r in rows:
        hours = r.get("hours")
        if hours is not None and not isinstance(hours, str):
            hours = None  # NaN
        out.append(Suggestion(
            entity_id=entity_id, kind="HOLIDAY_HOURS", date=r["date"], day_key=r.get("day_key"),
            shown=display.shown_hours(entity_id, r["date"], as_of), action=r["action"], value=hours,
            probability=r.get("probability"), basis=r.get("basis") or "",
            evidence=[Evidence(**{k: e.get(k) for k in ("source", "detail", "ref", "weight")})
                      for e in r.get("evidence") or []],
            model_version="engine" if source != "baseline" else "baseline"))
    return out, source


def business_for(entity_id: str) -> dict:
    from cs import data
    e = data.entity(entity_id)
    mall_id = e.get("c_shoppingCenterEntityId")
    mall_name = data.entities().get(mall_id, {}).get("name") if mall_id else None
    return {"entity_id": entity_id, "name": e.get("name", entity_id), "country": e["meta"].get("countryCode"),
            "language": e["meta"].get("language"), "mall_name": mall_name}


def contact_for(entity_id: str) -> dict | None:
    from cs import data
    c = data.contacts()
    row = c[c["entity_id"] == entity_id]
    return None if row.empty else row.iloc[0].to_dict()


def views_last_year(entity_id: str, suggestions: list, as_of: date | str = "2026-11-06") -> tuple[int | None, str | None]:
    """(lookups, day_key) on last year's busiest matching special day: Google search + map views."""
    from cs import data
    keys = {s.day_key for s in suggestions if s.day_key}
    if not keys:
        return None, None
    cal = data.calendar()
    country = entity_id[:2]
    a = data.analytics(as_of)
    a = a[a["entity_id"] == entity_id]
    best = (None, None)
    as_of_d = _to_date(as_of)
    for key in keys:
        prev = cal[(cal["country"] == country) & (cal["day_key"] == key)
                   & (cal["date"] < as_of_d.isoformat())
                   & (cal["date"] >= (as_of_d - timedelta(days=366)).isoformat())]
        for d in prev["date"]:
            row = a[a["date"] == d]
            if row.empty:
                continue
            n = int(row["GOOGLE_SEARCH_VIEWS"].iloc[0]) + int(row["GOOGLE_MAP_VIEWS"].iloc[0])
            if best[0] is None or n > best[0]:
                best = (n, key)
    if best[0] is None:
        return None, None
    n = best[0]
    return (n if n < 20 else int(round(n, -1))), best[1]


def _has_holiday_history(entity_id: str, as_of: date | str = "2026-11-06") -> bool:
    from cs import data
    h = data.holiday_history(as_of)
    return bool(len(h)) and bool((h["entity_id"] == entity_id).any())


OWN_HISTORY_FOR_PERMISSION = frozenset({"OWN_LAST_YEAR", "OWN_2_SEASONS", "LINKED_HOLIDAY", "OWNER_ANSWER"})


def demo_message(entity_id: str, lang: str | None = None, channel: str | None = None,
                 first_contact: bool = True, today: date | None = None) -> dict:
    """Everything the CLI prints for one business: the card (or autopilot note), or why there is none."""
    from cs import deadlines
    biz = business_for(entity_id)
    contact = contact_for(entity_id) or {}
    lang = norm_lang(lang or contact.get("preferred_language") or biz.get("language"))
    channel = (channel or contact.get("preferred_channel") or "OUTBOX").upper()
    sugs, source = suggestions_for(entity_id)
    flagged = [s for s in sugs if s.action == "FLAG"]
    send = [s for s in sugs if s.action == "SUGGEST" and s.value]
    res: dict = {"entity_id": entity_id, "name": biz["name"], "lang": lang, "channel": channel, "source": source,
                 "messages": []}
    if flagged and not send:
        res["note"] = f"no message: {len(flagged)} date(s) FLAGGED for staff (likely closed for good)"
        return res
    if not send:
        if not _has_holiday_history(entity_id):
            # New customer with no history (A12 / O6): one habit question instead of a card.
            res["note"] = None
            res["messages"].append(("QUESTION (new customer, O6)", compose_question({"kind": "HABIT"}, lang, channel)))
            return res
        res["note"] = "no message: the listing already shows the right hours (stay quiet)"
        return res
    if int(contact.get("opted_out_of_suggestions") or 0):
        res["note"] = "no message: the owner opted out of suggestions"
        return res
    if int(contact.get("standing_permission_holidays") or 0):
        auto = [s for s in send if s.basis in OWN_HISTORY_FOR_PERMISSION]
        send = [s for s in send if s.basis not in OWN_HISTORY_FOR_PERMISSION]
        if auto:
            try:
                from cs.confirm import where_it_shows
                wis = where_it_shows(entity_id)
            except Exception:  # noqa: BLE001 - the note still works without it
                wis = []
            today_ = today or date.today()
            short = any((_to_date(s.date) - today_).days < 14 for s in auto)
            res["messages"].append(("AUTOPILOT", compose_autopilot_note(
                biz, auto, lang, channel=channel, where_it_shows=wis, short_notice=short)))
    if send:
        dl = deadlines.card_deadline(entity_id, [s.date for s in send])
        views, vday = views_last_year(entity_id, send)
        res["messages"].append(("CARD", compose_card_message(
            biz, send, lang, channel, first_contact, None, dl, views, value_line_day=vday, today=today)))
    return res


def _print_result(res: dict) -> None:
    print(f"=== {res['entity_id']} {res['name']} · lang={res['lang']} · channel={res['channel']} · "
          f"source={res['source']} ===")
    if res.get("note"):
        print(f"({res['note']})\n")
        return
    for kind, msg in res["messages"]:
        print(f"--- {kind} ({msg['parts']['length']} chars"
              + (f", limit {msg['parts'].get('limit')}" if msg['parts'].get('limit') else "") + ") ---")
        print(msg["text"])
        if msg["buttons"]:
            print("  " + "  ".join(f"[{b['title']}]" for b in msg["buttons"]))
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.compose", description="Print the season message for a demo business.")
    ap.add_argument("entity_id", nargs="?", default="FI-D0001")
    ap.add_argument("--lang", choices=LANGS, help="default: the owner's preferred language")
    ap.add_argument("--channel", choices=CHANNELS, help="default: the owner's preferred channel")
    ap.add_argument("--returning", action="store_true", help="not the first contact (no AI disclosure line)")
    ap.add_argument("--all-demo", action="store_true", help="all 17 demo businesses in their own languages")
    a = ap.parse_args(argv)
    ids = DEMO_IDS if a.all_demo else (a.entity_id,)
    for eid in ids:
        try:
            res = demo_message(eid, None if a.all_demo else a.lang, a.channel, first_contact=not a.returning)
        except KeyError:
            print(f"unknown entity: {eid}", file=sys.stderr)
            return 2
        _print_result(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
