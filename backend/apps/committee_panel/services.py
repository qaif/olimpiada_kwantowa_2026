"""Odczyty ramy panelu komisji: kto tu jest (recenzent, komisja odwoławcza) i ile na niego czeka.

Role bierzemy z **tych samych** funkcji, co bramki istniejących paneli – ``active_reviewer_profile``
(``ReviewerRequiredMixin``), ``appeals_committee_profile`` + rola ``appeals``
(``AppealsCommitteeRequiredMixin``)
i ``video_rooms.committee_member`` (jedno albo drugie). Menu, Start i bramki nie mogą się rozjechać:
pozycja menu prowadząca w 403 byłaby błędem, a ekran otwarty komuś, kogo bramka kolejki nie wpuszcza –
wyciekiem.

Liczniki są **zapamiętane na żądaniu** (atrybut ``request``): menu i kafle Startu pytają o te same
liczby w jednym żądaniu, a każda z nich to jedno zapytanie niezależne od liczby prac.
"""

from __future__ import annotations

from django.db.models import Count, Q

_MEMO = "_committee_panel_memo"


def _memo(request) -> dict:
    memo = getattr(request, _MEMO, None)
    if memo is None:
        memo = {}
        setattr(request, _MEMO, memo)
    return memo


def _remember(request, key: str, compute):
    memo = _memo(request)
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def reviewer_profile(request):
    """Aktywny profil recenzenta tego konkursu albo ``None`` – definicja ``ReviewerRequiredMixin``."""
    from apps.accounts.services import active_reviewer_profile

    return _remember(
        request,
        "reviewer",
        lambda: active_reviewer_profile(
            getattr(request, "user", None), getattr(request, "competition", None)
        ),
    )


def appeals_profile(request):
    """Profil komisji odwoławczej tego konkursu albo ``None`` (jak ``AppealsCommitteeRequiredMixin``)."""

    def compute():
        from apps.accounts.models import CompetitionRole
        from apps.accounts.services import has_role
        from apps.appeals.services import appeals_committee_profile

        user = getattr(request, "user", None)
        competition = getattr(request, "competition", None)
        if competition is None or not has_role(user, competition, CompetitionRole.APPEALS):
            return None
        return appeals_committee_profile(user, competition)

    return _remember(request, "appeals", compute)


def member(request):
    """Członek komisji tego konkursu: recenzent **albo** komisja odwoławcza (jak ``committee_member``)."""
    return reviewer_profile(request) or appeals_profile(request)


def review_counts(request) -> dict:
    """Przydziały recenzenta (``todo``, ``tiebreak``, ``tiebreak_open``, ``submitted``, ``total``).

    Jedno zapytanie.

    Recenzje anulowane się nie liczą (``reviews_for_reviewer`` domyślnie je pomija): odebrana praca nie
    jest ani „do oceny”, ani „oceniona”.
    """

    def compute():
        from apps.grading.deadlines import OPEN_STATUSES
        from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
        from apps.grading.services import reviews_for_reviewer

        profile = reviewer_profile(request)
        empty = {"todo": 0, "tiebreak": 0, "tiebreak_open": 0, "submitted": 0, "total": 0}
        if profile is None:
            return empty
        row = (
            reviews_for_reviewer(profile, request.competition)
            .order_by()
            .aggregate(
                todo=Count("pk", filter=Q(status__in=OPEN_STATUSES)),
                tiebreak=Count("pk", filter=Q(round=ROUND_TIEBREAK)),
                tiebreak_open=Count("pk", filter=Q(round=ROUND_TIEBREAK, status__in=OPEN_STATUSES)),
                submitted=Count("pk", filter=Q(status=ReviewStatus.SUBMITTED)),
                total=Count("pk"),
            )
        )
        return {key: int(row.get(key) or 0) for key in empty}

    return _remember(request, "review_counts", compute)


def appeals_count(request) -> int:
    """Reklamacje w kolejce tego członka komisji (bez spraw z konfliktem interesów) – jedno ``COUNT``."""

    def compute():
        from apps.appeals.services import appeals_queue

        profile = appeals_profile(request)
        if profile is None:
            return 0
        return appeals_queue(profile, request.competition).order_by().count()

    return _remember(request, "appeals_count", compute)


def jitsi_passes() -> bool:
    """Czy platforma wystawia przepustki Jitsi – warunek kart rozmów i pokoi komisji (bez zapytań)."""
    from apps.competitions.jitsi_jwt import jwt_enabled

    return jwt_enabled()
