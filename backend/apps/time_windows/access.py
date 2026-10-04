"""Bramki okien czasowych wołane z innych aplikacji (docs/tasks/TZ-01.md § 2–3).

Jedno miejsce, z którego zawody, wyniki, test online, forum i czat pytają „czy ten uczeń / ten
świat może już to zobaczyć albo oddać”. Każda funkcja zaczyna od tej samej bramki – czy etap
**w ogóle** pracuje w oknach – i ta bramka nie dotyka bazy, dopóki konkurs nie ma flagi
``stage_time_windows``: konkurs bez flagi (Olimpiada Kwantowa) płaci za całą funkcję zero zapytań
i dostaje z każdej bramki dokładnie tę odpowiedź, którą dawał kod sprzed zmiany.

Odpowiedzi, które daje ten moduł:

- ``personal_stage`` – kopia etapu z terminami **ucznia** (start jego okna, jego termin). Panel
  uczestnika podmienia nią etap raz, na wejściu, i wszystko niżej (nagłówek „Co teraz”, karty
  zadań, odliczanie, ``is_open_for_submissions``) liczy się już z własnych terminów. Kopii nie da
  się zapisać (``Stage.save`` odmawia) – inaczej rama etapu zostałaby nadpisana oknem jednej osoby,
- ``statements_visible`` – treść zadań: uczniowi od startu jego okna, światu po ujawnieniu,
- ``release_at`` / ``windows_running`` – moment ujawnienia i czas wymuszonej premoderacji,
- ``quiz_window`` / ``quiz_results_at`` – okno startu testu i chwila pokazania wyniku,
- ``assert_results_publishable`` – publikacja wyników dopiero po ujawnieniu.
"""

from __future__ import annotations

import copy
from datetime import datetime

from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError

from .models import FLAG

#: Atrybut instancji etapu z wczytaną migawką planu. ``_MISSING`` odróżnia „jeszcze nie pytano”
#: od „pytano – etap nie ma okien”, żeby drugi odczyt w tym samym żądaniu nie szedł do bazy.
_CACHE = "_time_windows_plan"
_MISSING = object()


def enabled(competition) -> bool:
    """Czy konkurs ma tryb okien. Pole wiersza, który wołający już trzyma – bez zapytania."""
    return competition is not None and competition.has_feature(FLAG)


def _competition_of(stage, competition=None):
    """Konkurs etapu – bez zapytania, gdy to możliwe.

    Podany konkurs jest wiarygodny, jeśli zgadza się z wczytaną edycją (wołający zakresuje etap po
    nim). Bez podanego: edycja z pamięci obiektu i konkurs kontekstu żądania, gdy to ten sam;
    w każdym innym razie odczyt z bazy – poprawność przed oszczędnością.
    """
    from apps.tenancy.context import current_competition

    edition = stage._state.fields_cache.get("edition")
    if competition is not None and (edition is None or edition.competition_id == competition.pk):
        return competition
    if edition is None:
        edition = stage.edition
    cached = edition._state.fields_cache.get("competition")
    if cached is not None:
        return cached
    context = current_competition()
    if context is not None and context.pk == edition.competition_id:
        return context
    return edition.competition


def plan_for(stage, competition=None):
    """Migawka planu okien etapu albo ``None`` (bez flagi, rozmowa, trening, brak planu).

    Kopia ucznia (``personal_stage``) też odpowiada ``None``: jej terminy **są już** oknem,
    a drugie nałożenie okna na okno przesunęłoby je po raz drugi.
    """
    if stage is None or getattr(stage, "_personal_window", None) is not None:
        return None
    if stage.is_interview or stage.is_training:
        return None
    cached = stage.__dict__.get(_CACHE, _MISSING)
    if cached is not _MISSING:
        return cached
    if not enabled(_competition_of(stage, competition)):
        return None
    from .services import load_plan

    view = load_plan(stage)
    if view is not None and not view.windows:
        view = None
    stage.__dict__[_CACHE] = view
    return view


def effective_window(stage, participant, competition=None):
    """Okno ucznia w etapie albo ``None`` (etap bez okien albo brak ucznia)."""
    if participant is None:
        return None
    view = plan_for(stage, competition)
    if view is None:
        return None
    from .services import resolve

    return resolve(view, participant)


def personal_stage(stage, participant, competition=None):
    """Etap z terminami ucznia – albo ten sam obiekt, gdy okien nie ma.

    Zmieniają się wyłącznie ``opens_at`` i ``deadline_at`` (z dodatkowym czasem); tolerancja
    ``grace_seconds``, recenzje, reklamacje i ``closed_at`` zostają etapowe.
    """
    if stage is None:
        return None
    effective = effective_window(stage, participant, competition)
    if effective is None:
        return stage
    personal = copy.copy(stage)
    personal.opens_at = effective.opens_at
    personal.deadline_at = effective.deadline_at
    personal._personal_window = effective
    return personal


def window_of(stage):
    """Okno ucznia niesione przez kopię z ``personal_stage`` (dla szablonów) albo ``None``."""
    return getattr(stage, "_personal_window", None)


def release_at(stage, competition=None) -> datetime | None:
    """Moment ujawnienia etapu z oknami albo ``None`` (etap bez okien)."""
    view = plan_for(stage, competition)
    return view.release_at if view is not None else None


def windows_running(stage, now=None, competition=None) -> bool:
    """Czy trwa którekolwiek okno (od startu pierwszego do ujawnienia) – premoderacja forum i czatu."""
    view = plan_for(stage, competition)
    return view is not None and view.running(now or timezone.now())


def statements_visible(stage, *, user=None, participant=None, competition=None, now=None) -> bool:
    """Czy treść zadań etapu jest widoczna dla tej osoby (``user=None`` – publicznie).

    Najpierw stara reguła (``has_opened``, w kopii ucznia – start jego okna), potem okna: po
    ujawnieniu wszyscy, przed nim wyłącznie uczeń, którego okno już się zaczęło.
    """
    now = now or timezone.now()
    if stage is None or not stage.has_opened(now):
        return False
    view = plan_for(stage, competition)
    if view is None:
        return True
    if view.release_at is not None and now >= view.release_at:
        return True
    if participant is None and user is not None and getattr(user, "is_authenticated", False):
        from apps.accounts.services import participant_for

        participant = participant_for(user, _competition_of(stage, competition))
    if participant is None:
        return False
    from .services import resolve

    effective = resolve(view, participant)
    return effective is not None and effective.opens_at <= now


def quiz_window(quiz, participant, competition=None) -> tuple[datetime, datetime]:
    """Okno startu podejścia: okno ucznia w etapie z oknami, w pozostałych – ``Quiz.window``.

    W trybie okien własne terminy testu są **pomijane**: jedno globalne okno testu zaprzeczałoby
    oknom etapu (uczeń z okna C dostałby test zamknięty albo otwarty w środku nocy).
    """
    effective = effective_window(quiz.stage, participant, competition)
    if effective is None:
        return quiz.window
    return (effective.opens_at, effective.deadline_at)


def quiz_results_at(quiz, competition=None) -> datetime:
    """Chwila, od której wolno pokazać wynik „po zamknięciu testu”."""
    released = release_at(quiz.stage, competition)
    return released if released is not None else quiz.window[1]


def assert_results_publishable(stage, now=None, competition=None) -> None:
    """Publikacja wyników etapu z oknami dopiero po ujawnieniu – inaczej tabela zdradza zadania."""
    released = release_at(stage, competition)
    if released is not None and (now or timezone.now()) < released:
        raise DomainError(
            "Nie wszystkie okna czasowe etapu się zakończyły – wyniki można ogłosić po "
            f"{timezone.localtime(released):%Y-%m-%d %H:%M} (czas polski).",
            "WINDOWS_NOT_FINISHED",
            status.HTTP_409_CONFLICT,
        )


def windows_fit_envelope(stage, opens_at, deadline_at) -> bool:
    """Czy okna etapu zmieszczą się w nowej ramie (zmiana terminów etapu w ``update_stage``)."""
    from .services import windows_fit

    return windows_fit(stage, opens_at, deadline_at)
