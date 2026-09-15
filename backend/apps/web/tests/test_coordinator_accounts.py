"""Konta wszystkich ról w panelu koordynatora: lista, edycja, blokada, usunięcie.

Dlaczego ten ekran w ogóle istnieje: organizator odbiera telefony w rodzaju „zapisałem się
z literówką w adresie e-mail” i „proszę wykreślić moje dziecko z olimpiady”, a ``/admin/`` nie zna
ani jednej reguły tej domeny – kasuje kaskadą prace i recenzje, nie sprząta wpisów allauth i nie
zostawia śladu w audycie razem z resztą historii sprawy.

Czego pilnują te testy poza samym zapisem:

- **granica ról** – cały ``/coordinator/accounts/`` jest wyłącznie dla koordynatora,
- **konta chronione** – koordynatora i superużytkownika nie da się stąd ani zmienić, ani skasować,
  bo dwóch koordynatorów mogłoby się nawzajem zablokować jednym kliknięciem,
- **audyt bez danych osobowych** – wpis mówi, co się zmieniło, ale nie na co; tak stanowi
  ``apps.core.models``, bo wpisy czyta też ktoś bez prawa do danych uczestnika.
"""

import pytest

from apps.accounts.models import CommitteeStatus, Participant, User, Voivodeship
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/accounts/"


def edit_url(user) -> str:
    return f"/coordinator/accounts/{user.pk}/"


def delete_url(user) -> str:
    return f"/coordinator/accounts/{user.pk}/delete/"


def account_fields(user, **overrides) -> dict:
    """Blok „Konto” wypełniony tym, co konto ma teraz. Test nadpisuje to, czego dotyczy."""
    data = {
        "account-first_name": user.first_name,
        "account-last_name": user.last_name,
        "account-email": user.email,
        "account-is_active": "on" if user.is_active else "",
    }
    data.update(overrides)
    return data


def participant_fields(participant, **overrides) -> dict:
    """Blok „Dane uczestnika”. Szkoła wolnym tekstem – wariant działający bez słownika SIO."""
    data = {
        "participant-phone": participant.phone,
        "participant-district": participant.district,
        "participant-grade": str(participant.grade),
        "participant-birth_year": str(participant.birth_year),
        "participant-school_custom": "on",
        "participant-school": participant.school,
    }
    data.update(overrides)
    return data


def committee_fields(member, **overrides) -> dict:
    data = {
        "committee-status": member.status,
        "committee-district": member.district or "",
        "committee-is_appeals_committee": "on" if member.is_appeals_committee else "",
    }
    data.update(overrides)
    return data


# --- lista --------------------------------------------------------------------------------------


def test_the_list_shows_every_account_with_its_role_and_public_code(
    web_client, coordinator, participant, reviewer
):
    web_client.force_login(coordinator)

    body = web_client.get(LIST_URL).content.decode()

    assert participant.user.email in body
    assert participant.public_code in body
    assert reviewer.user.email in body
    assert "uczestnik" in body
    assert "członek komitetu" in body


def test_the_coordinator_row_is_marked_as_protected(web_client, coordinator):
    web_client.force_login(coordinator)

    body = web_client.get(LIST_URL).content.decode()

    assert coordinator.email in body
    assert "chronione" in body


def test_the_search_finds_an_account_by_email_name_and_public_code(web_client, coordinator, participant):
    other = UserFactory(email="ktos-inny@example.test", first_name="Ktoś", last_name="Inny")
    web_client.force_login(coordinator)

    for query in (participant.user.email, participant.user.last_name, participant.public_code):
        body = web_client.get(LIST_URL, {"q": query}).content.decode()
        assert participant.user.email in body
        assert other.email not in body


def test_the_role_filter_narrows_the_list(web_client, coordinator, participant, reviewer):
    web_client.force_login(coordinator)

    participants = web_client.get(LIST_URL, {"role": "participant"}).content.decode()
    committee = web_client.get(LIST_URL, {"role": "committee"}).content.decode()
    others = web_client.get(LIST_URL, {"role": "other"}).content.decode()

    assert participant.user.email in participants
    assert reviewer.user.email not in participants
    assert reviewer.user.email in committee
    assert participant.user.email not in committee
    # „Pozostałe” to konta bez żadnego profilu roli – koordynator jest tu jedynym takim kontem.
    assert coordinator.email in others
    assert participant.user.email not in others


def test_the_list_is_paginated_by_fifty_and_keeps_the_filter(web_client, coordinator):
    from apps.web.views.coordinator_accounts import ACCOUNTS_PER_PAGE

    UserFactory.create_batch(ACCOUNTS_PER_PAGE + 5)
    web_client.force_login(coordinator)

    first = web_client.get(LIST_URL)
    second = web_client.get(LIST_URL, {"role": "other", "page": 2})

    assert len(first.context["rows"]) == ACCOUNTS_PER_PAGE
    assert first.context["page_obj"].has_next() is True
    # Filtr przeżywa przejście na kolejną stronę – inaczej druga strona pokazywałaby wszystko.
    assert second.context["role"] == "other"
    assert second.context["page_obj"].number == 2


def test_the_dashboard_links_to_the_account_list(web_client, coordinator):
    web_client.force_login(coordinator)

    assert LIST_URL in web_client.get("/coordinator/").content.decode()


# --- edycja konta -------------------------------------------------------------------------------


def test_the_coordinator_edits_the_names_of_an_account_without_a_role(web_client, coordinator):
    user = UserFactory(first_name="Jan", last_name="Kowalski")
    web_client.force_login(coordinator)

    response = web_client.post(edit_url(user), account_fields(user, **{"account-first_name": "Janina"}))

    user.refresh_from_db()
    assert response.status_code == 302
    assert user.first_name == "Janina"


def test_unchecking_the_active_box_blocks_the_login_without_deleting_anything(
    web_client, coordinator, participant
):
    web_client.force_login(coordinator)

    web_client.post(
        edit_url(participant.user),
        {
            **account_fields(participant.user, **{"account-is_active": ""}),
            **participant_fields(participant),
        },
    )

    participant.user.refresh_from_db()
    assert participant.user.is_active is False
    # Blokada to nie usunięcie: profil i kod publiczny zostają.
    assert Participant.objects.filter(pk=participant.pk).exists()


def test_the_email_changes_at_once_and_allauth_forgets_the_old_address(web_client, coordinator, participant):
    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(
        user=participant.user, email=participant.user.email, verified=True, primary=True
    )
    web_client.force_login(coordinator)

    web_client.post(
        edit_url(participant.user),
        {
            **account_fields(participant.user, **{"account-email": "nowy-adres@example.test"}),
            **participant_fields(participant),
        },
    )

    participant.user.refresh_from_db()
    assert participant.user.email == "nowy-adres@example.test"
    # Wpis ze starym adresem znika: inaczej konto dałoby się dalej połączyć z Google po adresie,
    # który za chwilę może należeć do kogoś innego.
    assert not EmailAddress.objects.filter(user=participant.user).exists()


def test_a_taken_email_is_refused_and_nothing_is_saved(web_client, coordinator, participant):
    UserFactory(email="zajety@example.test")
    web_client.force_login(coordinator)

    response = web_client.post(
        edit_url(participant.user),
        {
            **account_fields(
                participant.user,
                **{"account-email": "ZAJETY@example.test", "account-first_name": "Zmieniona"},
            ),
            **participant_fields(participant),
        },
    )

    participant.user.refresh_from_db()
    assert response.status_code == 400
    assert participant.user.email == "uczestnik@example.test"
    assert participant.user.first_name != "Zmieniona"


def test_the_participant_block_saves_school_grade_and_voivodeship(web_client, coordinator, participant):
    web_client.force_login(coordinator)

    response = web_client.post(
        edit_url(participant.user),
        {
            **account_fields(participant.user),
            **participant_fields(
                participant,
                **{
                    "participant-phone": "600 300 400",
                    "participant-district": Voivodeship.POMORSKIE,
                    "participant-grade": "2",
                    "participant-birth_year": "2009",
                    "participant-school": "Technikum nr 3",
                },
            ),
        },
    )

    participant.refresh_from_db()
    assert response.status_code == 302
    assert participant.phone == "+48600300400"
    assert participant.district == Voivodeship.POMORSKIE
    assert participant.grade == 2
    assert participant.birth_year == 2009
    assert participant.school == "Technikum nr 3"


def test_an_invalid_phone_stops_the_whole_save(web_client, coordinator, participant):
    """Walidacja przed zapisem: zły numer nie ma prawa zostawić zapisanego nowego adresu e-mail."""
    web_client.force_login(coordinator)

    response = web_client.post(
        edit_url(participant.user),
        {
            **account_fields(participant.user, **{"account-email": "inny@example.test"}),
            **participant_fields(participant, **{"participant-phone": "600 SZEŚĆ"}),
        },
    )

    participant.user.refresh_from_db()
    assert response.status_code == 400
    assert participant.user.email == "uczestnik@example.test"


def test_setting_the_committee_status_to_active_grants_the_reviewer_groups(web_client, coordinator):
    member = CommitteeMemberFactory(status=CommitteeStatus.PENDING, is_appeals_committee=False)
    web_client.force_login(coordinator)

    web_client.post(
        edit_url(member.user),
        {
            **account_fields(member.user),
            **committee_fields(
                member,
                **{
                    "committee-status": CommitteeStatus.ACTIVE,
                    "committee-is_appeals_committee": "on",
                },
            ),
        },
    )

    member.refresh_from_db()
    assert member.status == CommitteeStatus.ACTIVE
    assert member.approved_at is not None
    # Grupy idą tą samą drogą, co przycisk „Zatwierdź” na pulpicie – razem z komisją odwoławczą.
    assert set(member.user.groups.values_list("name", flat=True)) >= {"reviewer", "appeals"}


def test_setting_and_clearing_the_committee_voivodeship_moves_the_verified_flag(web_client, coordinator):
    member = ActiveReviewerFactory(district=None, district_verified=False)
    web_client.force_login(coordinator)

    web_client.post(
        edit_url(member.user),
        {
            **account_fields(member.user),
            **committee_fields(member, **{"committee-district": Voivodeship.SLASKIE}),
        },
    )
    member.refresh_from_db()
    assert member.district == Voivodeship.SLASKIE
    assert member.district_verified is True

    web_client.post(
        edit_url(member.user),
        {**account_fields(member.user), **committee_fields(member, **{"committee-district": ""})},
    )
    member.refresh_from_db()
    # Puste województwo to decyzja („ocenia prace z całego kraju”), a nie niewypełnione pole –
    # więc nie ma już czego potwierdzać.
    assert member.district is None
    assert member.district_verified is False


def test_the_coordinator_account_is_read_only(web_client, coordinator):
    other = CoordinatorFactory(email="drugi-koordynator@example.test")
    web_client.force_login(coordinator)

    body = web_client.get(edit_url(other)).content.decode()
    assert "Konto koordynatora – dane zmienia administrator w /admin/." in body
    assert "Usuń konto" not in body

    response = web_client.post(edit_url(other), account_fields(other, **{"account-last_name": "Podmiana"}))
    other.refresh_from_db()
    assert response.status_code == 400
    assert other.last_name != "Podmiana"


def test_the_audit_entry_names_the_changed_fields_without_their_values(web_client, coordinator, participant):
    web_client.force_login(coordinator)

    web_client.post(
        edit_url(participant.user),
        {
            **account_fields(
                participant.user,
                **{"account-first_name": "Zofia", "account-email": "zofia@example.test"},
            ),
            **participant_fields(participant, **{"participant-district": Voivodeship.LUBELSKIE}),
        },
    )

    entry = AuditLog.objects.get(action="account.updated_by_coordinator")
    assert entry.actor == coordinator
    assert entry.diff["first_name"] is True
    assert entry.diff["email"] is True
    assert entry.diff["email_changed_without_confirmation"] is True
    # Województwo nie jest daną osobową samą w sobie – tu wartości zostają, jak w profilu.
    assert entry.diff["district"] == {"from": Voivodeship.MAZOWIECKIE, "to": Voivodeship.LUBELSKIE}
    serialised = str(entry.diff)
    assert "Zofia" not in serialised
    assert "zofia@example.test" not in serialised


# --- usunięcie ----------------------------------------------------------------------------------


def test_the_confirmation_page_explains_the_anonymisation(web_client, coordinator, participant, entry):
    web_client.force_login(coordinator)

    body = web_client.get(delete_url(participant.user)).content.decode()

    assert "dane osobowe zostaną usunięte" in body
    assert participant.public_code in body


def test_the_confirmation_page_explains_the_full_deletion(web_client, coordinator, participant):
    web_client.force_login(coordinator)

    body = web_client.get(delete_url(participant.user)).content.decode()

    assert "usunięte w całości" in body


def test_deleting_an_account_with_a_footprint_anonymises_it(web_client, coordinator, participant, entry):
    web_client.force_login(coordinator)

    response = web_client.post(delete_url(participant.user))

    participant.refresh_from_db()
    participant.user.refresh_from_db()
    assert response.status_code == 302
    assert participant.user.email.endswith("@invalid.olimpiadakwantowa.pl")
    assert participant.user.first_name == ""
    # Pseudonimowy wiersz zostaje – ogłoszone tabele wyników mają się do czego odwołać.
    assert participant.public_code
    assert AuditLog.objects.filter(action="account.deleted_by_coordinator").exists()


def test_deleting_an_account_without_a_footprint_removes_the_row(web_client, coordinator):
    user = UserFactory(email="do-skasowania@example.test")
    web_client.force_login(coordinator)

    response = web_client.post(delete_url(user))

    assert response.status_code == 302
    assert not User.objects.filter(pk=user.pk).exists()
    entry = AuditLog.objects.get(action="account.deleted_by_coordinator")
    assert entry.diff == {"result": "deleted", "had_footprint": False}


def test_the_coordinator_cannot_delete_their_own_account_from_this_screen(web_client, coordinator):
    web_client.force_login(coordinator)

    response = web_client.post(delete_url(coordinator), follow=True)

    assert User.objects.filter(pk=coordinator.pk).exists()
    assert "Własnego konta nie usuwa się z tego ekranu" in response.content.decode()


def test_the_coordinator_cannot_delete_another_coordinator(web_client, coordinator):
    other = CoordinatorFactory(email="drugi-koordynator@example.test")
    web_client.force_login(coordinator)

    response = web_client.post(delete_url(other), follow=True)

    assert User.objects.filter(pk=other.pk).exists()
    assert "Konto koordynatora" in response.content.decode()


# --- uprawnienia --------------------------------------------------------------------------------


def test_other_roles_get_403_and_anonymous_is_sent_to_the_login(web_client, participant, reviewer):
    urls = (LIST_URL, edit_url(participant.user), delete_url(participant.user))

    for url in urls:
        assert web_client.get(url).status_code == 302

    web_client.force_login(participant.user)
    for url in urls:
        assert web_client.get(url).status_code == 403

    web_client.force_login(reviewer.user)
    for url in urls:
        assert web_client.get(url).status_code == 403


def test_a_participant_cannot_delete_a_foreign_account_with_a_post(web_client, participant):
    victim = ParticipantFactory()
    web_client.force_login(participant.user)

    response = web_client.post(delete_url(victim.user))

    assert response.status_code == 403
    assert User.objects.filter(pk=victim.user.pk).exists()
