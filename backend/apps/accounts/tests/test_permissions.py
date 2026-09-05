"""T-02, kryteria 5-6: zatwierdzanie komitetu przez koordynatora i klasy uprawnień."""

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.accounts.models import GROUP_APPEALS, GROUP_REVIEWER, CommitteeStatus
from apps.accounts.permissions import (
    IsActiveReviewer,
    IsAppealsCommittee,
    IsCoordinator,
    IsParticipant,
)

from .factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    PendingReviewerFactory,
    UserFactory,
)


class ReviewerOnlyView(APIView):
    """Widok testowy chroniony IsActiveReviewer (kryterium 6)."""

    permission_classes = [IsActiveReviewer]

    def get(self, request):
        return Response({"ok": True})


def _call(view_class, user):
    request = APIRequestFactory().get("/test/")
    force_authenticate(request, user=user)
    return view_class.as_view()(request)


@pytest.fixture
def api():
    return APIClient()


@pytest.mark.django_db
def test_kryterium_5_koordynator_zatwierdza_pending_na_active(api):
    """5. Koordynator zatwierdza PENDING → ACTIVE (+ grupa reviewer)."""
    coordinator = CoordinatorFactory()
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    api.force_authenticate(user=coordinator)

    listing = api.get("/api/auth/committee/pending/")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [member.id]

    resp = api.post(f"/api/auth/committee/{member.id}/approve/")

    assert resp.status_code == 200, resp.data
    member.refresh_from_db()
    assert member.status == CommitteeStatus.ACTIVE
    assert member.approved_by_id == coordinator.id
    assert member.approved_at is not None
    assert member.user.groups.filter(name=GROUP_REVIEWER).exists()


@pytest.mark.django_db
def test_kryterium_5_zatwierdzenie_odwolawczego_dodaje_grupe_appeals(api):
    """5. Zatwierdzenie członka z is_appeals_committee dodaje też grupę `appeals`."""
    api.force_authenticate(user=CoordinatorFactory())
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING, is_appeals_committee=True)

    resp = api.post(f"/api/auth/committee/{member.id}/approve/")

    assert resp.status_code == 200, resp.data
    assert member.user.groups.filter(name=GROUP_APPEALS).exists()


@pytest.mark.django_db
def test_kryterium_5_uczestnik_wywolujacy_approve_dostaje_403(api):
    """5. Uczestnik wywołujący approve → 403, status członka bez zmian."""
    participant = ParticipantFactory()
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    api.force_authenticate(user=participant.user)

    resp = api.post(f"/api/auth/committee/{member.id}/approve/")

    assert resp.status_code == 403
    member.refresh_from_db()
    assert member.status == CommitteeStatus.PENDING
    assert api.get("/api/auth/committee/pending/").status_code == 403


@pytest.mark.django_db
def test_kryterium_5_recenzent_nie_moze_zatwierdzac_innych(api):
    """5. Aktywny recenzent też nie jest koordynatorem – brak eskalacji uprawnień."""
    reviewer = ActiveReviewerFactory()
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    api.force_authenticate(user=reviewer.user)

    assert api.post(f"/api/auth/committee/{member.id}/approve/").status_code == 403
    member.refresh_from_db()
    assert member.status == CommitteeStatus.PENDING


@pytest.mark.django_db
def test_kryterium_6_widok_is_active_reviewer_daje_403_dla_pending_i_200_dla_active():
    """6. Endpoint chroniony IsActiveReviewer: 403 dla PENDING, 200 dla ACTIVE."""
    pending = PendingReviewerFactory()
    active = ActiveReviewerFactory()

    assert _call(ReviewerOnlyView, pending.user).status_code == 403
    assert _call(ReviewerOnlyView, active.user).status_code == 200


@pytest.mark.django_db
def test_kryterium_6_zawieszony_recenzent_i_uzytkownik_bez_profilu_dostaja_403():
    """6. SUSPENDED oraz konto bez profilu komitetu nie przechodzą IsActiveReviewer."""
    suspended = ActiveReviewerFactory(status=CommitteeStatus.SUSPENDED)
    outsider = UserFactory(groups=[GROUP_REVIEWER])

    assert _call(ReviewerOnlyView, suspended.user).status_code == 403
    assert _call(ReviewerOnlyView, outsider).status_code == 403


@pytest.mark.django_db
def test_klasy_uprawnien_rozrozniaja_role():
    """Macierz 2.3: każda rola przechodzi wyłącznie swoją klasę uprawnień."""
    participant = ParticipantFactory().user
    reviewer = ActiveReviewerFactory().user
    appeals = ActiveReviewerFactory(
        user=UserFactory(groups=[GROUP_REVIEWER, GROUP_APPEALS]), is_appeals_committee=True
    ).user
    coordinator = CoordinatorFactory()

    factory = APIRequestFactory()

    def allows(permission, user) -> bool:
        request = factory.get("/test/")
        request.user = user
        return permission().has_permission(request, None)

    assert [allows(IsParticipant, u) for u in (participant, reviewer, appeals, coordinator)] == [
        True,
        False,
        False,
        False,
    ]
    assert [allows(IsActiveReviewer, u) for u in (participant, reviewer, appeals, coordinator)] == [
        False,
        True,
        True,
        False,
    ]
    assert [allows(IsAppealsCommittee, u) for u in (participant, reviewer, appeals, coordinator)] == [
        False,
        False,
        True,
        False,
    ]
    assert [allows(IsCoordinator, u) for u in (participant, reviewer, appeals, coordinator)] == [
        False,
        False,
        False,
        True,
    ]


@pytest.mark.django_db
def test_superuser_nie_jest_automatycznie_koordynatorem():
    """Brak cichej eskalacji: superuser bez grupy `coordinator` nie przechodzi IsCoordinator."""
    request = APIRequestFactory().get("/test/")
    request.user = UserFactory(is_staff=True, is_superuser=True)
    assert IsCoordinator().has_permission(request, None) is False
