"""Uczestnicy z brakującymi zgodami – kafelek pulpitu, CSV koordynatora i ``consent_gate_report``.

Reguła jest ta sama, co w bramce (``state.missing_from``), liczona w Pythonie na jednym zapytaniu:
profil + aktywne wpisy w ``ArraySubquery``. Przepisanie jej na SQL (wiek z daty urodzenia, 29 lutego,
stara reguła rocznikowa) dałoby drugą implementację ``is_minor``, która rozjedzie się z pierwszą przy
najbliższej zmianie – a kilka tysięcy wierszy z czterema kolumnami Python przelicza w milisekundach.

Liczymy konta **aktywne** i nieanonimizowane: zaproszony uczeń bez uruchomionego konta złoży zgody na
ekranie zaproszenia, a konto zanonimizowane nie ma już kogo prosić.
"""

from __future__ import annotations

from django.core.cache import cache
from django.utils.translation import gettext as _

from apps.accounts.consents import ConsentKind
from apps.accounts.models import Participant
from apps.core.exports import Dataset

from . import state

#: Kafelek pulpitu – minuta pamięci, tak jak liczniki „co wymaga uwagi” (``coordinator_nav``).
COUNT_TTL = 60


def _rows(competition):
    consents = state.consents_for(competition)
    queryset = (
        Participant.objects.for_competition(competition)
        .exclude_anonymised()
        .filter(user__is_active=True)
        .annotate(consent_pairs=state.records_subquery())
        .order_by("public_code")
        .values_list(
            "public_code",
            "user__first_name",
            "user__last_name",
            "user__email",
            "birth_date",
            "birth_year",
            "consent_pairs",
        )
    )
    for code, first_name, last_name, email, birth_date, birth_year, raw in queryset.iterator(chunk_size=1000):
        missing = state.missing_from(consents, birth_date, birth_year, state.pairs(raw))
        if missing:
            yield code, first_name, last_name, email, missing


def gaps(competition) -> list[tuple]:
    """``[(kod, imię, nazwisko, e-mail, brakujące zgody), …]`` – konta z brakami, po kodzie."""
    return list(_rows(competition))


def count(competition) -> int:
    """Liczba uczestników z brakami – z minutową pamięcią, bo pulpit otwiera się często."""
    key = f"{state.CACHE_PREFIX}:gap-count:{competition.pk}"
    value = cache.get(key)
    if value is None:
        value = sum(1 for _row in _rows(competition))
        cache.set(key, value, COUNT_TTL)
    return value


def describe(consent) -> str:
    """„akceptacja regulaminu (wersja z 20 września 2026)” – rodzaj i wersja, której brakuje."""
    name = dict(ConsentKind.choices).get(consent.kind, consent.kind)
    return _("%(kind)s (wersja %(version)s)") % {"kind": name, "version": consent.version}


def dataset(competition) -> Dataset:
    """CSV braków. Kolumny = dane, które koordynator już widzi na liście kont – nic ponad nie wychodzi."""
    rows = gaps(competition)
    return Dataset(
        header=[_("Kod uczestnika"), _("Imię"), _("Nazwisko"), _("E-mail"), _("Brakujące zgody")],
        rows=iter(
            [code, first_name, last_name, email, "; ".join(describe(consent) for consent in missing)]
            for code, first_name, last_name, email, missing in rows
        ),
        count=len(rows),
        title=_("Brakujące zgody"),
        filename="brakujace-zgody",
    )
