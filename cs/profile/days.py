"""Holiday names in owners' and customers' words (fi / sv / nb / en), for reading reviews and
writing replies. ``detect_day_key("…stängt på långfredagen…") -> "GOOD_FRIDAY"``."""
from __future__ import annotations

import re

# (day_key, regex). Order matters: the more specific name comes first.
DAY_WORDS: list[tuple[str, str]] = [
    ("EASTER_MONDAY", r"(?<!\d)2\.\s*pääsiäispäiv|toi(?:nen|sena|sen) pääsiäispäiv|annandag påsk|andre påskedag|easter monday"),
    ("EASTER_SATURDAY", r"pääsiäislauantai|påskafton|påskeaften|easter saturday|holy saturday"),
    ("EASTER_SUNDAY", r"pääsiäispäiv|påskdag|første påskedag|easter sunday|easter day"),
    ("GOOD_FRIDAY", r"pitkäperjantai|långfredag|langfredag|good friday"),
    ("MAUNDY_THURSDAY", r"kiirastorstai|skärtorsdag|skjærtorsdag|maundy thursday"),
    ("MAY_EVE", r"vappuaat|valborg|may day eve"),
    ("MAY_DAY", r"vappu|första maj|(?<!\d)1\.?\s*maj|(?<!\d)1\.\s*mai|arbeidernes dag|may day"),
    ("ASCENSION", r"helatorstai|kristi himmelsfärd|kristi himmelfart|ascension"),
    ("PENTECOST_EVE", r"helluntaiaat|pingstafton|pinseaften|whitsun eve"),
    ("WHIT_MONDAY", r"andre pinsedag|annandag pingst|whit monday"),
    ("PENTECOST", r"helluntai|pingstdag|pinsedag|pentecost|whit sunday"),
    ("NATIONAL_DAY", r"nationaldag|(?<!\d)17\.\s*mai|grunnlovsdag|national day"),
    ("MIDSUMMER_EVE", r"juhannusaat|midsommarafton|sankthansaften|midsummer eve"),
    ("MIDSUMMER_DAY", r"juhannuspäiv|juhannus|midsommardag|midsommar|midtsommer|midsummer"),
    ("ALL_SAINTS", r"pyhäinpäiv|alla helgons|allehelgen|all saints"),
    ("FATHERS_DAY", r"isänpäiv|fars dag|farsdag|father'?s day"),
    ("MOTHERS_DAY", r"äitienpäiv|mors dag|morsdag|mother'?s day"),
    ("BLACK_FRIDAY", r"black friday"),
    ("INDEPENDENCE_DAY", r"itsenäisyyspäiv|självständighetsdag|independence day"),
    ("LUCIA", r"lucia"),
    ("DEC23", r"aatonaat|lillejulafton|lille julaften"),
    ("XMAS_EVE", r"jouluaat|julafton|julaften|christmas eve"),
    ("BOXING_DAY", r"tapaninpäiv|annandag jul|andre juledag|boxing day|st\.? stephen"),
    ("INTERDAY", r"välipäiv|mellandag|romjul|between christmas"),
    ("NYE", r"uudenvuodenaat|nyårsafton|nyttårsaften|new year'?s eve"),
    ("NEW_YEARS_DAY", r"uudenvuodenpäiv|nyårsdag|nyttårsdag|new year'?s day"),
    ("EPIPHANY_EVE", r"loppiaisaat|trettondagsafton|helligtrekongersaften|epiphany eve"),
    ("EPIPHANY", r"loppiai|trettondag|helligtrekonger|epiphany"),
    ("XMAS_DAY", r"joulupäiv|juldag|første juledag|christmas day|christmas"),
    ("EVENT", r"tapahtumail|evenemangskväll|arrangementskveld|event night"),
]
_DAY_RES = [(k, re.compile(p, re.IGNORECASE)) for k, p in DAY_WORDS]

# How to say "on <holiday>" (fi: the essive case).
ON_DAY: dict[str, dict[str, str]] = {
    "fi": {"XMAS_EVE": "jouluaattona", "XMAS_DAY": "joulupäivänä", "BOXING_DAY": "tapaninpäivänä",
           "DEC23": "aatonaattona", "INTERDAY": "välipäivinä", "NYE": "uudenvuodenaattona",
           "NEW_YEARS_DAY": "uudenvuodenpäivänä", "EPIPHANY_EVE": "loppiaisaattona", "EPIPHANY": "loppiaisena",
           "INDEPENDENCE_DAY": "itsenäisyyspäivänä", "FATHERS_DAY": "isänpäivänä", "MOTHERS_DAY": "äitienpäivänä",
           "BLACK_FRIDAY": "Black Fridayna", "ALL_SAINTS": "pyhäinpäivänä", "MAUNDY_THURSDAY": "kiirastorstaina",
           "GOOD_FRIDAY": "pitkäperjantaina", "EASTER_SATURDAY": "pääsiäislauantaina",
           "EASTER_SUNDAY": "pääsiäispäivänä", "EASTER_MONDAY": "toisena pääsiäispäivänä",
           "MAY_EVE": "vappuaattona", "MAY_DAY": "vappuna", "ASCENSION": "helatorstaina",
           "PENTECOST_EVE": "helluntaiaattona", "PENTECOST": "helluntaina", "WHIT_MONDAY": "helluntain jälkeen",
           "NATIONAL_DAY": "kansallispäivänä", "MIDSUMMER_EVE": "juhannusaattona", "MIDSUMMER_DAY": "juhannuspäivänä",
           "LUCIA": "Lucian päivänä", "EVENT": "tapahtumailtana"},
    "sv": {"XMAS_EVE": "på julafton", "XMAS_DAY": "på juldagen", "BOXING_DAY": "på annandag jul",
           "DEC23": "på lillejulafton", "INTERDAY": "i mellandagarna", "NYE": "på nyårsafton",
           "NEW_YEARS_DAY": "på nyårsdagen", "EPIPHANY_EVE": "på trettondagsafton", "EPIPHANY": "på trettondedag jul",
           "INDEPENDENCE_DAY": "på självständighetsdagen", "FATHERS_DAY": "på fars dag", "MOTHERS_DAY": "på mors dag",
           "BLACK_FRIDAY": "på Black Friday", "ALL_SAINTS": "på alla helgons dag", "MAUNDY_THURSDAY": "på skärtorsdagen",
           "GOOD_FRIDAY": "på långfredagen", "EASTER_SATURDAY": "på påskafton", "EASTER_SUNDAY": "på påskdagen",
           "EASTER_MONDAY": "på annandag påsk", "MAY_EVE": "på valborgsmässoafton", "MAY_DAY": "på första maj",
           "ASCENSION": "på Kristi himmelsfärdsdag", "PENTECOST_EVE": "på pingstafton", "PENTECOST": "på pingstdagen",
           "WHIT_MONDAY": "på annandag pingst", "NATIONAL_DAY": "på nationaldagen",
           "MIDSUMMER_EVE": "på midsommarafton", "MIDSUMMER_DAY": "på midsommardagen", "LUCIA": "på Lucia",
           "EVENT": "på evenemangskvällen"},
    "nb": {"XMAS_EVE": "på julaften", "XMAS_DAY": "på første juledag", "BOXING_DAY": "på andre juledag",
           "DEC23": "på lille julaften", "INTERDAY": "i romjulen", "NYE": "på nyttårsaften",
           "NEW_YEARS_DAY": "på første nyttårsdag", "EPIPHANY_EVE": "på helligtrekongersaften",
           "EPIPHANY": "på helligtrekongersdag", "INDEPENDENCE_DAY": "på Finlands nasjonaldag",
           "FATHERS_DAY": "på farsdag", "MOTHERS_DAY": "på morsdag", "BLACK_FRIDAY": "på Black Friday",
           "ALL_SAINTS": "på allehelgensdag", "MAUNDY_THURSDAY": "på skjærtorsdag", "GOOD_FRIDAY": "på langfredag",
           "EASTER_SATURDAY": "på påskeaften", "EASTER_SUNDAY": "på første påskedag",
           "EASTER_MONDAY": "på andre påskedag", "MAY_EVE": "på valborgsaften", "MAY_DAY": "på arbeidernes dag",
           "ASCENSION": "på Kristi himmelfartsdag", "PENTECOST_EVE": "på pinseaften", "PENTECOST": "på første pinsedag",
           "WHIT_MONDAY": "på andre pinsedag", "NATIONAL_DAY": "på grunnlovsdagen",
           "MIDSUMMER_EVE": "på sankthansaften", "MIDSUMMER_DAY": "på midtsommerdagen", "LUCIA": "på Luciadagen",
           "EVENT": "på arrangementskvelden"},
    "en": {"XMAS_EVE": "on Christmas Eve", "XMAS_DAY": "on Christmas Day", "BOXING_DAY": "on Boxing Day",
           "DEC23": "on the day before Christmas Eve", "INTERDAY": "between Christmas and New Year",
           "NYE": "on New Year's Eve", "NEW_YEARS_DAY": "on New Year's Day", "EPIPHANY_EVE": "on Epiphany Eve",
           "EPIPHANY": "on Epiphany", "INDEPENDENCE_DAY": "on Independence Day", "FATHERS_DAY": "on Father's Day",
           "MOTHERS_DAY": "on Mother's Day", "BLACK_FRIDAY": "on Black Friday", "ALL_SAINTS": "on All Saints' Day",
           "MAUNDY_THURSDAY": "on Maundy Thursday", "GOOD_FRIDAY": "on Good Friday",
           "EASTER_SATURDAY": "on Easter Saturday", "EASTER_SUNDAY": "on Easter Sunday",
           "EASTER_MONDAY": "on Easter Monday", "MAY_EVE": "on May Day Eve", "MAY_DAY": "on May Day",
           "ASCENSION": "on Ascension Day", "PENTECOST_EVE": "on Whitsun Eve", "PENTECOST": "on Pentecost",
           "WHIT_MONDAY": "on Whit Monday", "NATIONAL_DAY": "on National Day", "MIDSUMMER_EVE": "on Midsummer Eve",
           "MIDSUMMER_DAY": "on Midsummer Day", "LUCIA": "on St Lucy's Day", "EVENT": "on the event night"},
}


def detect_day_key(text: str) -> str | None:
    """The special day a review names, or None."""
    t = text or ""
    for key, rx in _DAY_RES:
        if rx.search(t):
            return key
    return None


def on_day(day_key: str | None, lang: str) -> str | None:
    return ON_DAY.get(lang, ON_DAY["en"]).get(day_key or "")


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
