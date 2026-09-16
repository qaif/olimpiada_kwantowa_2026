"""Poruszanie się po kolejce recenzenta: prace jednego zadania seriami.

Skąd to się wzięło (prośba organizatora): recenzent ocenia zwykle **jedno zadanie w wielu pracach**,
a nie wiele zadań w jednej pracy. Wracanie po każdej ocenie na listę i szukanie w niej kolejnego
wiersza jest wtedy czystym kosztem – przy osiemnastu pracach to siedemnaście powrotów. Panel daje
więc „Poprzednia praca / Następna praca” w obrębie tego samego zadania i licznik „5 z 18”, żeby
było widać, ile jeszcze zostało.

Wszystko liczy się po stronie serwera, jednym zapytaniem po identyfikatory – bez JavaScriptu
i bez trzymania stanu przeglądania w sesji (kolejka zmienia się w trakcie: koordynator dosyła
prace, odbiera je, uczestnik przysyła nową wersję).
"""

from __future__ import annotations

from django.db.models import Q

from .deadlines import OPEN_STATUSES, is_overdue
from .models import Review


def queue_ids(review) -> list[int]:
    """Identyfikatory prac tej serii: własne otwarte recenzje tego samego zadania, rosnąco.

    Bieżąca recenzja jest w serii **zawsze**, także po wystawieniu oceny: recenzent, który właśnie
    ją wysłał i wrócił na stronę, ma dalej widzieć, gdzie jest w kolejce i co jest następne.
    Kolejność po ``id`` (czyli po chwili przydziału) jest dowolna, ale stała – a stałość jest tu
    całą wartością: „następna” ma znaczyć to samo przy każdym kliknięciu.
    """
    return list(
        Review.objects.filter(
            reviewer_id=review.reviewer_id,
            submission__problem_id=review.submission.problem_id,
        )
        .filter(Q(status__in=OPEN_STATUSES) | Q(pk=review.pk))
        .order_by("id")
        .values_list("id", flat=True)
    )


def queue_position(review) -> dict:
    """Miejsce recenzji w serii wraz z sąsiadami: ``{previous, next, position, total}``.

    ``previous``/``next`` to identyfikatory recenzji (albo ``None``), bo tyle wystarczy szablonowi
    do zbudowania adresu – wczytywanie całych obiektów tylko po to, żeby wziąć z nich ``pk``,
    byłoby dwoma zapytaniami bez odbiorcy.
    """
    ids = queue_ids(review)
    if review.pk not in ids:  # pragma: no cover - bieżąca recenzja jest w serii z definicji
        return {"previous": None, "next": None, "position": None, "total": len(ids)}
    index = ids.index(review.pk)
    return {
        "previous": ids[index - 1] if index > 0 else None,
        "next": ids[index + 1] if index + 1 < len(ids) else None,
        "position": index + 1,
        "total": len(ids),
    }


def group_by_problem(reviews, now=None) -> list[dict]:
    """Kolejka recenzenta pogrupowana po etapie i zadaniu, z licznikami dla każdej grupy.

    Grupowanie odpowiada temu, jak wygląda praca: „zadanie 2 w eliminacjach – dwanaście prac, sześć
    do zrobienia, jedna po terminie”. Płaska lista trzydziestu wierszy niesie te same dane, ale
    odpowiedź na pytanie „ile mi jeszcze zostało z zadania 2” wymaga przy niej liczenia ręcznie.

    Kolejność grup bierze się z kolejności wierszy (``reviews_for_reviewer`` sortuje po etapie
    i numerze zadania), więc ta funkcja niczego nie sortuje – przestawianie grup po swojemu
    rozjeżdżałoby listę z kolejnością serii, po której chodzą „poprzednia/następna”.
    """
    groups: dict[tuple[int, int], dict] = {}
    for review in reviews:
        submission = review.submission
        key = (submission.entry.stage_id, submission.problem_id)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "stage": submission.entry.stage,
                "problem": submission.problem,
                "rows": [],
                "open": 0,
                "overdue": 0,
            }
        overdue = is_overdue(review, now)
        group["rows"].append({"review": review, "overdue": overdue})
        if review.status in OPEN_STATUSES:
            group["open"] += 1
        if overdue:
            group["overdue"] += 1
    return list(groups.values())
