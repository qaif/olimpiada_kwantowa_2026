"""Oś czasu etapów bieżącej edycji – wspólne źródło dla strony głównej i bloku „terminy etapów”.

Moduł stoi osobno od ``apps.cms.models``, bo czytają go **oba** końce importu: ``models.py``
(strona główna) i ``blocks.py`` (blok ``stage_timeline`` w treści redakcyjnej). ``models.py``
importuje ``blocks.py``, więc funkcja w którymkolwiek z tych plików robiłaby cykl albo kopię
reguły – a reguła jest jedna: **terminy pochodzą z ``competitions.Stage`` i tylko stamtąd**.
Redaktor opisuje etapy słowem, ale nie przepisuje dat; strona nie może pokazać innego terminu
niż ten, który egzekwuje serwer.
"""

from __future__ import annotations

from django.utils import timezone

from apps.competitions.models import Edition, Stage
from apps.competitions.services import current_edition
from apps.results.models import ResultsPublication

#: Stan etapu na osi czasu. Klucz jest maszynowy (klasa CSS, test), etykieta – dla czytelnika.
#: Kolejność jest kolejnością rozstrzygania: ogłoszone wyniki wygrywają z „zamknięty”, bo to
#: ostatnia rzecz, która się z etapem stała, i jedyna, po której czytelnik ma gdzie kliknąć.
STATUS_PUBLISHED = ("published", "wyniki ogłoszone")
STATUS_OPEN = ("open", "otwarty")
STATUS_CLOSED = ("closed", "zamknięty")
STATUS_UPCOMING = ("upcoming", "nadchodzący")

#: Ton odznaki per stan – wyłącznie prezentacja, ta sama paleta co ``web_extras.badge_class``.
STATUS_BADGE = {
    "published": "badge badge--ok",
    "open": "badge badge--accent",
    "closed": "badge badge--neutral",
    "upcoming": "badge badge--neutral",
}


def _status(stage: Stage, *, has_results: bool, now) -> tuple[str, str]:
    if has_results:
        return STATUS_PUBLISHED
    if stage.is_open_for_submissions(now):
        return STATUS_OPEN
    if stage.has_opened(now):
        return STATUS_CLOSED
    return STATUS_UPCOMING


def stage_rows(edition: Edition | None = None, now=None) -> list[dict]:
    """Etapy edycji uporządkowane po ``opens_at`` wraz ze stanem i informacją o wynikach.

    Bez argumentu bierze edycję bieżącą – tak woła ją blok w treści redakcyjnej, który nie ma
    skąd znać edycji. Jedno zapytanie o etapy i jedno o publikacje: lista rośnie o wiersze,
    nie o zapytania, niezależnie od liczby etapów.
    """
    if edition is None:
        edition = current_edition()
    if edition is None:
        return []
    now = now or timezone.now()
    stages = list(edition.stages.order_by("opens_at", "id"))
    published = set(ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True))
    rows = []
    for stage in stages:
        has_results = stage.pk in published
        status, label = _status(stage, has_results=has_results, now=now)
        rows.append(
            {
                "stage": stage,
                "is_open": stage.is_open_for_submissions(now),
                "has_opened": stage.has_opened(now),
                "has_results": has_results,
                "status": status,
                "status_label": label,
                "badge_class": STATUS_BADGE[status],
            }
        )
    return rows
