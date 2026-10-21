"""Dane pulpitu „Start” komisji (UX-PANEL-03 § 4) – same liczby, bez jednej danej uczestnika.

Ocenianie jest ślepe (PROJEKT.md 2.2): nawet kod publiczny nie jest tu potrzebny, bo kafle odpowiadają
na pytania „ile”, „na kiedy” i „gdzie kliknąć”, a nie „czyja praca”. Zestawienie per etap to jedno
zapytanie ``GROUP BY`` po własnych przydziałach (``reviews_for_reviewer`` – konkurs przed rolą) i jedno
po etapy – niezależnie od liczby prac.
"""

from __future__ import annotations

from django.db.models import Count, Min, Q
from django.urls import reverse

from apps.competitions.models import Stage


def stage_rows(profile, competition, now) -> list[dict]:
    """Etapy z przydziałami recenzenta: przydzielone, wystawione, otwarte, po terminie, najbliższy termin."""
    from apps.grading.deadlines import OPEN_STATUSES
    from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
    from apps.grading.services import reviews_for_reviewer

    if profile is None:
        return []
    open_q = Q(status__in=OPEN_STATUSES)
    aggregates = list(
        reviews_for_reviewer(profile, competition)
        # Zestawienie, a nie wiersze: dociąganie plików i relacji z serwisu kolejki nie ma tu odbiorcy.
        .select_related(None)
        .prefetch_related(None)
        .order_by()
        .values("submission__entry__stage_id")
        .annotate(
            total=Count("pk"),
            submitted=Count("pk", filter=Q(status=ReviewStatus.SUBMITTED)),
            open=Count("pk", filter=open_q),
            overdue=Count("pk", filter=open_q & Q(due_at__lt=now)),
            tiebreak_open=Count("pk", filter=open_q & Q(round=ROUND_TIEBREAK)),
            next_due=Min("due_at", filter=open_q),
        )
    )
    if not aggregates:
        return []
    stages = {
        stage.pk: stage
        for stage in Stage.objects.filter(pk__in=[row["submission__entry__stage_id"] for row in aggregates])
    }
    rows = []
    for row in aggregates:
        stage = stages.get(row["submission__entry__stage_id"])
        if stage is None:  # pragma: no cover - etap z przydziałem istnieje z definicji
            continue
        total = row["total"] or 0
        rows.append(
            {
                "stage": stage,
                "total": total,
                "submitted": row["submitted"] or 0,
                "open": row["open"] or 0,
                "overdue": row["overdue"] or 0,
                "tiebreak_open": row["tiebreak_open"] or 0,
                "next_due": row["next_due"],
                "review_deadline": stage.review_deadline_at,
                "percent": round(100 * (row["submitted"] or 0) / total) if total else 0,
                "url": f"{reverse('web:review-list')}?stage={stage.pk}",
            }
        )
    # Najpilniejsze najpierw: etap z otwartą pracą przed etapem skończonym, potem po najbliższym terminie.
    rows.sort(
        key=lambda item: (
            item["open"] == 0,
            item["next_due"] is None,
            item["next_due"] or now,
            item["stage"].pk,
        )
    )
    return rows


def upcoming(rows: list[dict], meetings: dict, now) -> list[dict]:
    """„Najbliższe terminy”: termin przydziału, koniec oceniania etapu, rozmowa, webinar – do pięciu.

    Rozmowa wchodzi bez nazwisk i bez liczby uczestników z imionami – sama godzina i etap (ta sama
    zasada, co karta rozmów: lista terminów nie może być kluczem do anonimowości ocen).
    """
    items: list[dict] = []
    for row in rows:
        if row["open"] and row["next_due"] is not None:
            items.append({"at": row["next_due"], "kind": "due", "stage": row["stage"], "url": row["url"]})
        deadline = row["review_deadline"]
        if row["open"] and deadline is not None and deadline >= now and deadline != row["next_due"]:
            items.append({"at": deadline, "kind": "stage_deadline", "stage": row["stage"], "url": row["url"]})
    for interview in meetings.get("committee_interviews") or []:
        items.append(
            {"at": interview["slot"].starts_at, "kind": "interview", "stage": interview["stage"], "url": ""}
        )
    for webinar in meetings.get("committee_webinars") or []:
        items.append(
            {
                "at": webinar.starts_at,
                "kind": "webinar",
                "title": webinar.title,
                "url": reverse("web:webinars"),
            }
        )
    items.sort(key=lambda item: item["at"])
    return items[:5]
