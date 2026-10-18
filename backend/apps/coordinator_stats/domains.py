"""Grupy domen adresów e-mail: Gmail, WP/O2/Onet/Interia, Microsoft, szkolne, inne (STATS-02 § 3).

Po co grupy, a nie domeny: organizator pyta „czy Microsoft nas odrzuca” i „czy uczniowie z Gmaila
aktywują konta”, a nie o każdą z kilkuset domen szkół z osobna. Lista domen szkół na wykresie byłaby
zresztą listą, z której da się wyczytać pojedyncze osoby – grupa ma ich setki.

**Jedna tablica, dwa silniki.** Te same wyrażenia regularne klasyfikują adres w Pythonie (porcja
wysyłki, odbicie z DSN) i filtrują profile w PostgreSQL (``iregex`` w lejku). Dlatego wyrażenia
używają wyłącznie wspólnego podzbioru składni (alternatywa, klasy znaków, kotwice ``@`` i ``$``) –
test sprawdza, że obie strony zaliczają adres do tej samej grupy. Wyrażenie dopasowujemy do całego
adresu, a nie do domeny, bo filtr SQL widzi wyłącznie kolumnę ``email``.

Kolejność grup jest kolejnością sprawdzania i wyświetlania; „inne” to dopełnienie pozostałych.
"""

from __future__ import annotations

import re

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

GMAIL = "gmail"
PORTALS = "portals"
MICROSOFT = "microsoft"
SCHOOL = "school"
OTHER = "other"

#: Grupa → wyrażenie dopasowane do **całego adresu** (bez rozróżniania wielkości liter).
PATTERNS: dict[str, str] = {
    GMAIL: r"@(gmail|googlemail)\.com$",
    # Rodziny domen czterech portali: WP (wp.pl, o2.pl, tlen.pl, go2.pl, prokonto.pl), Onet (onet.*,
    # op.pl, vp.pl, spoko.pl, autograf.pl, buziaczek.pl, amorki.pl) i Interia (interia.*, poczta.fm,
    # intmail.pl, adresik.net, pacz.to, ogarnij.se, interiowy.pl).
    PORTALS: (
        r"@(wp\.pl|o2\.pl|tlen\.pl|go2\.pl|prokonto\.pl"
        r"|onet\.pl|onet\.eu|onet\.com\.pl|poczta\.onet\.pl|poczta\.onet\.eu|op\.pl|vp\.pl|spoko\.pl"
        r"|autograf\.pl|buziaczek\.pl|amorki\.pl"
        r"|interia\.pl|interia\.eu|interia\.com|vip\.interia\.pl|poczta\.fm|intmail\.pl|adresik\.net"
        r"|pacz\.to|ogarnij\.se|interiowy\.pl)$"
    ),
    # Domeny konsumenckie Microsoftu w każdym kraju (outlook.com, hotmail.co.uk, live.pl, msn.com).
    # Końcówka zamknięta na kod kraju i „com/co.<kraj>”, żeby „live.szkola.pl” nie było Microsoftem.
    MICROSOFT: r"@(outlook|hotmail|live|msn|windowslive)\.(com|[a-z][a-z]|co\.[a-z][a-z]|com\.[a-z][a-z])$",
    # Poczta placówek oświatowych: domeny w ``edu`` (zsp1.edu.pl, uni.edu) i platforma miejska
    # Warszawy. Szkoła z domeną „lo5.krakow.pl” wpada do „innych” – nie da się jej odróżnić od firmy.
    SCHOOL: r"(@|\.)edu(\.[a-z][a-z][a-z]?)?$|@([a-z0-9-]+\.)*eduwarszawa\.pl$",
}

#: Grupy w kolejności wyświetlania – z „innymi” na końcu.
GROUPS: tuple[str, ...] = (GMAIL, PORTALS, MICROSOFT, SCHOOL, OTHER)

LABELS = {
    GMAIL: _("Gmail"),
    PORTALS: _("WP / O2 / Onet / Interia"),
    MICROSOFT: _("Microsoft (Outlook, Hotmail, Live)"),
    SCHOOL: _("szkolne (edu)"),
    OTHER: _("inne"),
}

CHOICES = [(group, LABELS[group]) for group in GROUPS]

_COMPILED = {group: re.compile(pattern, re.IGNORECASE) for group, pattern in PATTERNS.items()}


def group_of(email: str) -> str:
    """Grupa adresu (jedna z :data:`GROUPS`). Adres bez ``@`` to „inne”."""
    address = (email or "").strip().strip("<>").strip().lower()
    for group, pattern in _COMPILED.items():
        if pattern.search(address):
            return group
    return OTHER


def label(group: str) -> str:
    return str(LABELS.get(group, group))


def email_q(group: str, field: str = "user__email") -> Q:
    """Warunek SQL „adres w ``field`` należy do grupy” – ta sama tablica, co :func:`group_of`.

    ``iregex`` to w PostgreSQL ``~*``. Grupa „inne” jest dopełnieniem pozostałych, a nieznana
    wartość nie dopasowuje niczego (filtr z błędnym parametrem ma dać pusty wynik, nie „wszystkich”).
    """
    if group == OTHER:
        condition = Q()
        for pattern in PATTERNS.values():
            condition &= ~Q(**{f"{field}__iregex": pattern})
        return condition
    pattern = PATTERNS.get(group)
    if pattern is None:
        return Q(pk__in=[])
    return Q(**{f"{field}__iregex": pattern})
