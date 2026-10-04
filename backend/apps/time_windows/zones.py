"""Strefy czasowe krajów i walidacja nazw stref IANA (docs/tasks/TZ-01.md § 1).

**Mapa domyślna jest w kodzie**, tak jak lista krajów (``apps.accounts.countries``): kraj → strefa
**stolicy**. Stolica, a nie „strefa najludniejsza” ani pierwsza z ``zone.tab``: to jest reguła,
którą da się wytłumaczyć organizatorowi jednym zdaniem i sprawdzić bez danych demograficznych,
a ``zone.tab`` zaczyna Rosję od Kaliningradu, co dla przydziału okien jest po prostu błędem.
Kraje rozciągnięte na kilka stref (USA, Kanada, Rosja, Brazylia, Australia, Meksyk, Indonezja)
koordynator poprawia ekranem okien (``CountryTimezone``), a opiekun – strefą ucznia.

Strefa ucznia służy **wyłącznie do wyświetlania**; do przydziału okna liczy się strefa kraju
(uzasadnienie w ``apps.time_windows.models.ParticipantTimezone``).
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

#: Kod kraju (``Region.code``, mała alfa-2) → strefa IANA stolicy.
COUNTRY_TIMEZONES: dict[str, str] = {
    "af": "Asia/Kabul",
    "al": "Europe/Tirane",
    "dz": "Africa/Algiers",
    "ad": "Europe/Andorra",
    "ao": "Africa/Luanda",
    "ag": "America/Antigua",
    "ar": "America/Argentina/Buenos_Aires",
    "am": "Asia/Yerevan",
    "au": "Australia/Sydney",
    "at": "Europe/Vienna",
    "az": "Asia/Baku",
    "bs": "America/Nassau",
    "bh": "Asia/Bahrain",
    "bd": "Asia/Dhaka",
    "bb": "America/Barbados",
    "by": "Europe/Minsk",
    "be": "Europe/Brussels",
    "bz": "America/Belize",
    "bj": "Africa/Porto-Novo",
    "bt": "Asia/Thimphu",
    "bo": "America/La_Paz",
    "ba": "Europe/Sarajevo",
    "bw": "Africa/Gaborone",
    "br": "America/Sao_Paulo",
    "bn": "Asia/Brunei",
    "bg": "Europe/Sofia",
    "bf": "Africa/Ouagadougou",
    "bi": "Africa/Bujumbura",
    "cv": "Atlantic/Cape_Verde",
    "kh": "Asia/Phnom_Penh",
    "cm": "Africa/Douala",
    "ca": "America/Toronto",
    "cf": "Africa/Bangui",
    "td": "Africa/Ndjamena",
    "cl": "America/Santiago",
    "cn": "Asia/Shanghai",
    "co": "America/Bogota",
    "km": "Indian/Comoro",
    "cg": "Africa/Brazzaville",
    "cr": "America/Costa_Rica",
    "ci": "Africa/Abidjan",
    "hr": "Europe/Zagreb",
    "cu": "America/Havana",
    "cy": "Asia/Nicosia",
    "cz": "Europe/Prague",
    "cd": "Africa/Kinshasa",
    "dk": "Europe/Copenhagen",
    "dj": "Africa/Djibouti",
    "dm": "America/Dominica",
    "do": "America/Santo_Domingo",
    "ec": "America/Guayaquil",
    "eg": "Africa/Cairo",
    "sv": "America/El_Salvador",
    "gq": "Africa/Malabo",
    "er": "Africa/Asmara",
    "ee": "Europe/Tallinn",
    "sz": "Africa/Mbabane",
    "et": "Africa/Addis_Ababa",
    "fj": "Pacific/Fiji",
    "fi": "Europe/Helsinki",
    "fr": "Europe/Paris",
    "ga": "Africa/Libreville",
    "gm": "Africa/Banjul",
    "ge": "Asia/Tbilisi",
    "de": "Europe/Berlin",
    "gh": "Africa/Accra",
    "gr": "Europe/Athens",
    "gd": "America/Grenada",
    "gt": "America/Guatemala",
    "gn": "Africa/Conakry",
    "gw": "Africa/Bissau",
    "gy": "America/Guyana",
    "ht": "America/Port-au-Prince",
    "va": "Europe/Vatican",
    "hn": "America/Tegucigalpa",
    "hk": "Asia/Hong_Kong",
    "hu": "Europe/Budapest",
    "is": "Atlantic/Reykjavik",
    "in": "Asia/Kolkata",
    "id": "Asia/Jakarta",
    "ir": "Asia/Tehran",
    "iq": "Asia/Baghdad",
    "ie": "Europe/Dublin",
    "il": "Asia/Jerusalem",
    "it": "Europe/Rome",
    "jm": "America/Jamaica",
    "jp": "Asia/Tokyo",
    "jo": "Asia/Amman",
    "kz": "Asia/Almaty",
    "ke": "Africa/Nairobi",
    "ki": "Pacific/Tarawa",
    "xk": "Europe/Belgrade",
    "kw": "Asia/Kuwait",
    "kg": "Asia/Bishkek",
    "la": "Asia/Vientiane",
    "lv": "Europe/Riga",
    "lb": "Asia/Beirut",
    "ls": "Africa/Maseru",
    "lr": "Africa/Monrovia",
    "ly": "Africa/Tripoli",
    "li": "Europe/Vaduz",
    "lt": "Europe/Vilnius",
    "lu": "Europe/Luxembourg",
    "mo": "Asia/Macau",
    "mg": "Indian/Antananarivo",
    "mw": "Africa/Blantyre",
    "my": "Asia/Kuala_Lumpur",
    "mv": "Indian/Maldives",
    "ml": "Africa/Bamako",
    "mt": "Europe/Malta",
    "mh": "Pacific/Majuro",
    "mr": "Africa/Nouakchott",
    "mu": "Indian/Mauritius",
    "mx": "America/Mexico_City",
    "fm": "Pacific/Pohnpei",
    "md": "Europe/Chisinau",
    "mc": "Europe/Monaco",
    "mn": "Asia/Ulaanbaatar",
    "me": "Europe/Podgorica",
    "ma": "Africa/Casablanca",
    "mz": "Africa/Maputo",
    "mm": "Asia/Yangon",
    "na": "Africa/Windhoek",
    "nr": "Pacific/Nauru",
    "np": "Asia/Kathmandu",
    "nl": "Europe/Amsterdam",
    "nz": "Pacific/Auckland",
    "ni": "America/Managua",
    "ne": "Africa/Niamey",
    "ng": "Africa/Lagos",
    "kp": "Asia/Pyongyang",
    "mk": "Europe/Skopje",
    "no": "Europe/Oslo",
    "om": "Asia/Muscat",
    "pk": "Asia/Karachi",
    "pw": "Pacific/Palau",
    "ps": "Asia/Hebron",
    "pa": "America/Panama",
    "pg": "Pacific/Port_Moresby",
    "py": "America/Asuncion",
    "pe": "America/Lima",
    "ph": "Asia/Manila",
    "pl": "Europe/Warsaw",
    "pt": "Europe/Lisbon",
    "qa": "Asia/Qatar",
    "ro": "Europe/Bucharest",
    "ru": "Europe/Moscow",
    "rw": "Africa/Kigali",
    "kn": "America/St_Kitts",
    "lc": "America/St_Lucia",
    "vc": "America/St_Vincent",
    "ws": "Pacific/Apia",
    "sm": "Europe/San_Marino",
    "st": "Africa/Sao_Tome",
    "sa": "Asia/Riyadh",
    "sn": "Africa/Dakar",
    "rs": "Europe/Belgrade",
    "sc": "Indian/Mahe",
    "sl": "Africa/Freetown",
    "sg": "Asia/Singapore",
    "sk": "Europe/Bratislava",
    "si": "Europe/Ljubljana",
    "sb": "Pacific/Guadalcanal",
    "so": "Africa/Mogadishu",
    "za": "Africa/Johannesburg",
    "kr": "Asia/Seoul",
    "ss": "Africa/Juba",
    "es": "Europe/Madrid",
    "lk": "Asia/Colombo",
    "sd": "Africa/Khartoum",
    "sr": "America/Paramaribo",
    "se": "Europe/Stockholm",
    "ch": "Europe/Zurich",
    "sy": "Asia/Damascus",
    "tw": "Asia/Taipei",
    "tj": "Asia/Dushanbe",
    "tz": "Africa/Dar_es_Salaam",
    "th": "Asia/Bangkok",
    "tl": "Asia/Dili",
    "tg": "Africa/Lome",
    "to": "Pacific/Tongatapu",
    "tt": "America/Port_of_Spain",
    "tn": "Africa/Tunis",
    "tr": "Europe/Istanbul",
    "tm": "Asia/Ashgabat",
    "tv": "Pacific/Funafuti",
    "ug": "Africa/Kampala",
    "ua": "Europe/Kyiv",
    "ae": "Asia/Dubai",
    "gb": "Europe/London",
    "us": "America/New_York",
    "uy": "America/Montevideo",
    "uz": "Asia/Tashkent",
    "vu": "Pacific/Efate",
    "ve": "America/Caracas",
    "vn": "Asia/Ho_Chi_Minh",
    "ye": "Asia/Aden",
    "zm": "Africa/Lusaka",
    "zw": "Africa/Harare",
}


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Nazwy stref IANA dostępne w tej instalacji (pakiet ``tzdata`` albo system)."""
    return frozenset(available_timezones())


def is_valid_timezone(name: str) -> bool:
    """Czy ``name`` jest strefą IANA, którą da się otworzyć. Aliasy („Poland”) też przechodzą."""
    if not name or name not in known_timezones():
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:  # pragma: no cover - zależy od danych tzdata
        return False
    return True


@lru_cache(maxsize=1)
def timezone_choices() -> tuple[tuple[str, str], ...]:
    """Lista wyboru stref: wyłącznie nazwy „Kontynent/Miasto”, posortowane.

    Bez aliasów i stref technicznych (``Etc/GMT+5``, ``EST5EDT``, „Poland”): lista ma być do
    znalezienia własnego miasta, a nie do wybierania przesunięcia z odwróconym znakiem.
    """
    prefixes = ("Africa/", "America/", "Asia/", "Atlantic/", "Australia/", "Europe/", "Indian/", "Pacific/")
    names = sorted(name for name in known_timezones() if name.startswith(prefixes))
    return tuple((name, name.replace("_", " ")) for name in names)


def country_default_timezone(code: str | None) -> str | None:
    """Domyślna strefa kraju z mapy albo ``None`` (region spoza listy krajów)."""
    return COUNTRY_TIMEZONES.get((code or "").lower())


def utc_offset_label(moment, tz_name: str) -> str:
    """„UTC+09:00” – przesunięcie strefy **w tej chwili** (z czasem letnim)."""
    offset = moment.astimezone(ZoneInfo(tz_name)).utcoffset()
    total = int(offset.total_seconds()) if offset is not None else 0
    sign = "+" if total >= 0 else "-"
    hours, rest = divmod(abs(total), 3600)
    return f"UTC{sign}{hours:02d}:{rest // 60:02d}"
