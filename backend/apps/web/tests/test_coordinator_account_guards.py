"""Bramki czynności koordynatora na cudzym koncie – poprawki po audycie 10.10.2026.

- **W3** – konto współdzielone między konkursami (nauczyciel: opiekun w B, recenzent w A). Adres
  e-mail, blokada, reset hasła, zdjęcie 2FA, eksport i usunięcie działają na **całym** koncie, więc
  koordynator B nie może ich zrobić – inaczej zmiana adresu na swój i „Nie pamiętasz hasła?”
  dawały mu konto recenzenta A,
- **S7** – reset 2FA i reset hasła kont chronionych (koordynator, operator ``is_staff``) i konta
  własnego,
- **S8** – eksport RODO z panelu koordynatora zawężony do jego konkursu,
- **S13** – żądanie bez konkursu nie widzi żadnego konta,
- **nowa funkcja** – „Usuń konto” na ekranie aktywacji.

Każdy przypadek odmowy sprawdza **skutek** (adres się nie zmienił, konto istnieje, list nie
wyszedł, urządzenie zostało), a nie tylko kod odpowiedzi: przekierowanie wygląda tak samo przy
odmowie i przy sukcesie.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.core import mail

from apps.accounts import twofactor
from apps.accounts.models import CompetitionRole, SchoolSupervisor, User
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def edit_url(user) -> str:
    return f"/coordinator/accounts/{user.pk}/"


def enable_2fa(user) -> None:
    device = twofactor.begin_setup(user)
    code = twofactor.totp_code(device.plain_secret(), twofactor.current_counter())
    twofactor.confirm_setup(user, code)


def logged_in(client_for, competition, user):
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_a_user(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def coordinator_a(client_for, competition, coordinator_a_user):
    return logged_in(client_for, competition, coordinator_a_user)


@pytest.fixture
def coordinator_b_user(other_competition):
    user = CoordinatorFactory()
    grant_membership(user, other_competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def coordinator_b(client_for, other_competition, coordinator_b_user):
    return logged_in(client_for, other_competition, coordinator_b_user)


@pytest.fixture
def shared_teacher(competition, other_competition):
    """Nauczyciel z decyzji D4: recenzent w konkursie A i opiekun szkolny w konkursie B."""
    member = ActiveReviewerFactory(competition=competition, user__email="nauczyciel@example.test")
    grant_membership(member.user, competition, CompetitionRole.REVIEWER)
    SchoolSupervisor.objects.create(user=member.user, school="XIV LO", competition=other_competition)
    grant_membership(member.user, other_competition, CompetitionRole.SUPERVISOR)
    return member.user


# --- W3: konto współdzielone ---------------------------------------------------------------------


def test_the_shared_account_is_visible_to_coordinator_b_but_marked(coordinator_b, shared_teacher):
    response = coordinator_b.get(edit_url(shared_teacher))

    assert response.status_code == 200
    body = response.content.decode()
    assert "ma role także w innym konkursie" in body
    # Przycisków, których serwis nie przepuści, nie ma na stronie.
    assert f"/coordinator/accounts/{shared_teacher.pk}/delete/" not in body
    assert f"/coordinator/accounts/{shared_teacher.pk}/password-reset/" not in body
    assert f"/coordinator/accounts/{shared_teacher.pk}/export/" not in body


def test_coordinator_b_cannot_change_the_email_of_a_shared_account(coordinator_b, shared_teacher):
    response = coordinator_b.post(
        edit_url(shared_teacher),
        {
            "account-first_name": shared_teacher.first_name,
            "account-last_name": shared_teacher.last_name,
            "account-email": "koordynator-b@example.test",
            # Odznaczone „Konto aktywne” – blokada też jest zmianą całego konta.
        },
    )

    assert response.status_code in (302, 400)
    shared_teacher.refresh_from_db()
    assert shared_teacher.email == "nauczyciel@example.test"
    assert shared_teacher.is_active is True


def test_the_service_refuses_the_email_change_with_account_shared(
    as_competition, other_competition, coordinator_b_user, shared_teacher
):
    from apps.accounts.profile import update_account_by_coordinator

    with as_competition(other_competition), pytest.raises(DomainError) as refused:
        update_account_by_coordinator(
            shared_teacher, actor=coordinator_b_user, account={"email": "koordynator-b@example.test"}
        )

    assert refused.value.machine_code == "ACCOUNT_SHARED"
    assert "innym konkursie" in str(refused.value.detail)
    shared_teacher.refresh_from_db()
    assert shared_teacher.email == "nauczyciel@example.test"


def test_the_names_of_a_shared_account_can_still_be_corrected(
    as_competition, other_competition, coordinator_b_user, shared_teacher
):
    """Imię i nazwisko nie przenoszą konta – literówka w nazwisku zostaje sprawą koordynatora."""
    from apps.accounts.profile import update_account_by_coordinator

    with as_competition(other_competition):
        update_account_by_coordinator(
            shared_teacher,
            actor=coordinator_b_user,
            account={"first_name": "Poprawione", "email": shared_teacher.email, "is_active": True},
        )

    shared_teacher.refresh_from_db()
    assert shared_teacher.first_name == "Poprawione"


def test_coordinator_b_cannot_delete_a_shared_account(coordinator_b, shared_teacher):
    response = coordinator_b.post(f"/coordinator/accounts/{shared_teacher.pk}/delete/")

    assert response.status_code == 400
    assert User.objects.filter(pk=shared_teacher.pk, email="nauczyciel@example.test").exists()


def test_coordinator_b_cannot_reset_the_2fa_of_a_shared_account(settings, coordinator_b, shared_teacher):
    settings.TWO_FACTOR_ENABLED = True
    enable_2fa(shared_teacher)

    response = coordinator_b.post(f"/coordinator/accounts/{shared_teacher.pk}/2fa-reset/")

    assert response.status_code == 302
    assert twofactor.confirmed_device(shared_teacher) is not None
    assert not AuditLog.objects.filter(action="2fa.reset").exists()


def test_coordinator_b_cannot_send_a_password_reset_to_a_shared_account(coordinator_b, shared_teacher):
    response = coordinator_b.post(f"/coordinator/accounts/{shared_teacher.pk}/password-reset/")

    assert response.status_code == 302
    assert mail.outbox == []
    assert not AuditLog.objects.filter(action="password.reset_sent").exists()


def test_coordinator_b_cannot_export_a_shared_account(coordinator_b, shared_teacher):
    response = coordinator_b.post(f"/coordinator/accounts/{shared_teacher.pk}/export/")

    assert response.status_code == 302
    assert not AuditLog.objects.filter(action="account.exported_by_coordinator").exists()


def test_an_account_of_this_competition_only_keeps_the_old_behaviour(coordinator_b, other_competition):
    """Kontrola dodatnia: konto wyłącznie konkursu B koordynator B dalej poprawia od razu."""
    own = UserFactory(email="tylko-b@example.test")
    grant_membership(own, other_competition, CompetitionRole.PARTICIPANT)

    coordinator_b.post(
        edit_url(own),
        {
            "account-first_name": "Jan",
            "account-last_name": "Kowalski",
            "account-email": "poprawiony@example.test",
            "account-is_active": "on",
        },
    )

    own.refresh_from_db()
    assert own.email == "poprawiony@example.test"


def test_a_super_coordinator_may_manage_a_shared_account(as_competition, other_competition, shared_teacher):
    """Superkoordynator jest koordynatorem każdego konkursu – rola u sąsiada nie jest cudzą sprawą."""
    from apps.accounts import super_coordinator
    from apps.accounts.shared_accounts import is_shared_for

    actor = CoordinatorFactory()
    super_coordinator.grant(actor)
    actor = User.objects.get(pk=actor.pk)

    assert is_shared_for(shared_teacher, other_competition, actor=actor) is False


# --- S7: konta chronione i własne -----------------------------------------------------------------


def test_another_coordinators_2fa_cannot_be_reset(settings, coordinator_a, competition):
    settings.TWO_FACTOR_ENABLED = True
    colleague = CoordinatorFactory()
    grant_membership(colleague, competition, CompetitionRole.COORDINATOR)
    enable_2fa(colleague)

    response = coordinator_a.post(f"/coordinator/accounts/{colleague.pk}/2fa-reset/")

    assert response.status_code == 302
    assert twofactor.confirmed_device(colleague) is not None


def test_the_own_2fa_cannot_be_reset_from_the_accounts_screen(settings, coordinator_a, coordinator_a_user):
    settings.TWO_FACTOR_ENABLED = True
    enable_2fa(coordinator_a_user)

    coordinator_a.post(f"/coordinator/accounts/{coordinator_a_user.pk}/2fa-reset/")

    assert twofactor.confirmed_device(coordinator_a_user) is not None


def test_the_service_refuses_a_2fa_reset_of_a_protected_account(competition, coordinator_a_user):
    staff = UserFactory(is_staff=True)
    with pytest.raises(DomainError) as refused:
        twofactor.reset_by_coordinator(staff, actor=coordinator_a_user)

    assert refused.value.machine_code == "COORDINATOR_PROTECTED"


def test_another_coordinator_gets_no_password_reset_link(coordinator_a, competition):
    colleague = CoordinatorFactory()
    grant_membership(colleague, competition, CompetitionRole.COORDINATOR)

    coordinator_a.post(f"/coordinator/accounts/{colleague.pk}/password-reset/")

    assert mail.outbox == []


def test_a_staff_account_without_a_group_is_protected(coordinator_a):
    """Konto z dostępem do ``/admin/``, bez grupy i bez członkostwa – dawniej „niczyje” i do przejęcia."""
    staff = UserFactory(is_staff=True, email="operator@example.test")

    coordinator_a.post(
        edit_url(staff),
        {"account-first_name": "X", "account-last_name": "Y", "account-email": "obcy@example.test"},
    )
    response = coordinator_a.post(f"/coordinator/accounts/{staff.pk}/delete/")

    assert response.status_code == 400
    staff.refresh_from_db()
    assert staff.email == "operator@example.test"
    coordinator_a.post(f"/coordinator/accounts/{staff.pk}/password-reset/")
    assert mail.outbox == []


# --- S8: eksport zawężony do konkursu -------------------------------------------------------------


def test_the_coordinator_export_leaves_out_forum_posts_of_another_competition(
    coordinator_a, competition, other_competition
):
    from apps.forum.tests.factories import ForumPostFactory

    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    ForumPostFactory(competition=competition, author=participant.user, body="Wpis tutejszy")
    ForumPostFactory(competition=other_competition, author=participant.user, body="Wpis u sąsiada")

    response = coordinator_a.post(f"/coordinator/accounts/{participant.user.pk}/export/")

    assert response.status_code == 200
    package = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    data = json.loads(package.read("dane.json").decode("utf-8"))
    bodies = [row["tresc"] for row in data["wpisy_na_forum"]]
    assert bodies == ["Wpis tutejszy"]


def test_the_own_export_still_has_every_competition(competition, other_competition):
    """Właściciel konta pyta o siebie, a nie o konkurs – jego paczka zostaje kompletna."""
    from apps.accounts.data_export import export_payload
    from apps.forum.tests.factories import ForumPostFactory

    participant = ParticipantFactory(competition=competition)
    ForumPostFactory(competition=competition, author=participant.user)
    ForumPostFactory(competition=other_competition, author=participant.user)

    assert len(export_payload(participant.user)["wpisy_na_forum"]) == 2
    assert len(export_payload(participant.user, competition)["wpisy_na_forum"]) == 1


def test_a_protected_account_is_not_exported_by_a_coordinator(coordinator_a, competition):
    colleague = CoordinatorFactory()
    grant_membership(colleague, competition, CompetitionRole.COORDINATOR)

    response = coordinator_a.post(f"/coordinator/accounts/{colleague.pk}/export/")

    assert response.status_code == 302
    assert not AuditLog.objects.filter(action="account.exported_by_coordinator").exists()


def test_manual_qualification_is_hidden_before_the_results_are_published(competition):
    from apps.accounts.data_export import export_payload
    from apps.competitions.models import ManualQualification
    from apps.competitions.tests.factories import StageEntryFactory

    qualified = next(value for value in ManualQualification.values if value)
    entry = StageEntryFactory(manual_qualification=qualified, manual_qualification_reason="Decyzja komitetu")
    entry.stage.results_published_at = None
    entry.stage.save(update_fields=["results_published_at"])

    rows = export_payload(entry.participant.user, entry.participant.competition)["zgloszenia_do_etapow"]

    assert rows[0]["kwalifikacja_reczna"] is None


# --- S13: żądanie bez konkursu ---------------------------------------------------------------------


def test_a_request_without_a_competition_sees_no_account():
    from apps.web.views.coordinator_accounts import participant_ids, users_for_competition

    UserFactory()
    ParticipantFactory()

    assert not users_for_competition(None).exists()
    assert not participant_ids(None).exists()


# --- „Usuń konto” na ekranie aktywacji --------------------------------------------------------------


def pending_account(competition, email: str):
    user = UserFactory(email=email, is_active=False, email_verified_at=None)
    grant_membership(user, competition, CompetitionRole.PARTICIPANT)
    return user


def test_the_activation_queue_offers_the_delete_button(coordinator_a, competition):
    user = pending_account(competition, "literowka@example.test")

    body = coordinator_a.get("/coordinator/activations/").content.decode()

    assert f"/coordinator/activations/{user.pk}/delete/" in body


def test_deleting_from_the_activation_queue_removes_the_account(coordinator_a, competition):
    user = pending_account(competition, "literowka@example.test")

    confirmation = coordinator_a.get(f"/coordinator/activations/{user.pk}/delete/")
    assert confirmation.status_code == 200
    assert "usunięte w całości" in confirmation.content.decode()
    assert 'href="/coordinator/activations/"' in confirmation.content.decode()

    response = coordinator_a.post(f"/coordinator/activations/{user.pk}/delete/")

    assert response.status_code == 302
    assert response["Location"] == "/coordinator/activations/"
    assert not User.objects.filter(pk=user.pk).exists()
    entry = AuditLog.objects.get(action="account.deleted_by_coordinator")
    assert entry.diff["via"] == "activations"
    assert entry.diff["result"] == "deleted"
    assert (
        f"/coordinator/activations/{user.pk}/delete/"
        not in coordinator_a.get("/coordinator/activations/").content.decode()
    )


def test_a_pending_account_of_another_competition_is_not_found(coordinator_a, other_competition):
    stranger = pending_account(other_competition, "obcy@example.test")

    assert coordinator_a.get(f"/coordinator/activations/{stranger.pk}/delete/").status_code == 404
    assert coordinator_a.post(f"/coordinator/activations/{stranger.pk}/delete/").status_code == 404
    assert User.objects.filter(pk=stranger.pk).exists()


def test_an_active_account_cannot_be_deleted_through_the_activation_queue(coordinator_a, competition):
    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)

    assert coordinator_a.post(f"/coordinator/activations/{participant.user.pk}/delete/").status_code == 404
    assert User.objects.filter(pk=participant.user.pk).exists()


def test_a_protected_pending_account_is_refused(coordinator_a, competition):
    colleague = CoordinatorFactory(is_active=False, email_verified_at=None)
    grant_membership(colleague, competition, CompetitionRole.COORDINATOR)

    response = coordinator_a.post(f"/coordinator/activations/{colleague.pk}/delete/")

    assert response.status_code == 400
    assert User.objects.filter(pk=colleague.pk).exists()


# --- stan „zablokowane” przed „nieaktywowane” (``User.blocked_at``) ----------------------------------


def test_an_account_blocked_before_activation_is_shown_as_blocked(coordinator_a, competition):
    """Konto zablokowane przed potwierdzeniem adresu ma te same dwa pola, co świeża rejestracja."""
    from apps.web.views.coordinator_accounts import STATUS_BLOCKED, account_status

    user = UserFactory(email="zablokowane@example.test", email_verified_at=None)
    grant_membership(user, competition, CompetitionRole.PARTICIPANT)
    user = User.objects.get(pk=user.pk)
    user.is_active = False
    user.save(update_fields=["is_active"])
    user.refresh_from_db()

    assert user.blocked_at is not None
    assert account_status(user) == STATUS_BLOCKED
    pending = UserFactory(email_verified_at=None, is_active=False)
    assert account_status(pending) != STATUS_BLOCKED

    body = coordinator_a.get("/coordinator/accounts/?sort=stan").content.decode()
    row = body[body.index("zablokowane@example.test") :][:2000]
    assert STATUS_BLOCKED in row
