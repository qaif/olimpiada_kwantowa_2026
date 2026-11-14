"""Planowana publikacja treści zadań (PUB-01, ``docs/tasks/PUB-01.md``).

ZOZ Etapu I (§ 3 ust. 1): treść zadania jest niedostępna przed otwarciem etapu, a system
udostępnia pliki od chwili otwarcia. Koordynator może dodatkowo **zaplanować** publikację
pojedynczego zadania (albo wszystkich zadań etapu naraz) na późniejszą chwilę –
``Problem.publish_at``. Puste pole znaczy „razem z otwarciem etapu”.

**Reguła jawności zadania jest jedna i ma dwie połowy:**

1. bramka **etapu** – ``Stage.has_opened`` albo, w konkursie z oknami czasowymi,
   ``apps.time_windows.access.statements_visible`` (start okna ucznia, ujawnienie),
2. bramka **zadania** – ``Problem.is_published``: minęła chwila ``publish_at`` (zegar serwera).

Ten moduł nie powtarza połowy pierwszej – woła ją. Dokłada drugą w dwóch postaciach: filtr
zapytania (:func:`published_q` – listy zadań, liczniki) i sprawdzenie pojedynczego obiektu
(:func:`problem_visible` – PDF, notatnik, LaTeX, upload). Personel (koordynator, komisja) widzi
treść zawsze; o tym rozstrzygają widoki, które personel obsługują, a nie te funkcje.

**Zadania okresowego nie ma**: porównanie z ``timezone.now()`` przy każdym żądaniu wystarcza,
a zadanie, które „otwiera” zadania o północy, mogłoby się spóźnić. Strony z treścią zadań nie
stoją w pełnostronicowym cache'u (``apps.web.page_cache.ALLOWED_PATHS``), a odpowiedzi API djcms
mają ``Cache-Control: no-store`` – moment publikacji widać więc od pierwszego żądania po nim.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone


def published_q(now=None, prefix: str = "") -> Q:
    """Warunek „zadanie opublikowane” do ``filter()``; ``prefix`` dla relacji (``"problem__"``)."""
    now = now or timezone.now()
    return Q(**{f"{prefix}publish_at__isnull": True}) | Q(**{f"{prefix}publish_at__lte": now})


def published_problems(queryset, now=None):
    """Queryset zadań zawężony do opublikowanych – druga połowa reguły jawności."""
    return queryset.filter(published_q(now))


def problem_visible(problem, *, user=None, participant=None, competition=None, now=None, stage=None) -> bool:
    """Czy treść **tego zadania** jest jawna dla tej osoby (``user=None`` – publicznie).

    ``stage`` pozwala podać kopię etapu z oknem ucznia (``time_windows.access.personal_stage``);
    domyślnie bramka etapu liczy się na etapie zadania.
    """
    from apps.time_windows.access import statements_visible

    now = now or timezone.now()
    if not problem.is_published(now):
        return False
    return statements_visible(
        stage if stage is not None else problem.stage,
        user=user,
        participant=participant,
        competition=competition,
        now=now,
    )


def next_publication(stage, now=None):
    """Najbliższa przyszła chwila publikacji zadania w etapie albo ``None``.

    Uczestnik widzi ją w panelu jako „kolejne zadania: <data>” – bez liczby i bez tytułów: chwila
    publikacji jest ogłaszana, treść nie.
    """
    from .models import Problem

    now = now or timezone.now()
    return (
        Problem.objects.filter(stage_id=stage.pk, publish_at__gt=now)
        .order_by("publish_at")
        .values_list("publish_at", flat=True)
        .first()
    )
