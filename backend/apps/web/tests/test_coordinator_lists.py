"""Listy osób w panelu koordynatora: konta usunięte i sortowanie kolumn.

Zgłoszenie organizatora z 24.09.2026 miało dwie połowy i obie są tu sprawdzane skutkiem na ekranie:

- „Koordynator widzi skasowanych użytkowników jako ‚deleted’” – konta po anonimizacji
  (``apps.accounts.anonymised``) mają być domyślnie **schowane** na każdej liście przeglądania,
  w wyszukiwarce i w arkuszu uczestników, z przełącznikiem „Pokaż usunięte konta (N)”, który
  przeżywa stronicowanie, sortowanie i filtry. Tam, gdzie wiersz zostać musi (przydziały, wyniki,
  karta, audyt, eksport recenzji), ma stać „Konto usunięte”, a nigdy adres ``deleted-…@invalid.…``,
- „koordynator powinien móc sortować uczestników po różnych polach” – nagłówki kolumn listy kont
  i listy uczestników sortują po stronie serwera, po kluczach z listy dopuszczonych (nic z adresu
  nie idzie do ``order_by``), stabilnie (``pk`` jako remis) i bez zapytań na wiersz.
"""

import csv
import io
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.anonymised import (
    DELETED_ACCOUNT_LABEL,
    anonymised_q,
    is_anonymised,
    is_anonymised_email,
    person_label,
)
from apps.accounts.models import CommitteeStatus, Participant, User, Voivodeship
from apps.accounts.profile import anonymise_account
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CommitteeMemberFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.competitions.tests.factories import StageEntryFactory
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.web.list_controls import ListControls, SortKey
from apps.web.tests.test_coordinator_certificates import workshops_page  # noqa: F401 - fikstura harmonogramu
from apps.web.views.coordinator_accounts import ACCOUNTS_PER_PAGE

pytestmark = pytest.mark.django_db

ACCOUNTS = "/coordinator/accounts/"
PARTICIPANTS = {"role": "participant"}

#: Adres techniczny konta po anonimizacji – ``deleted-<pk>@invalid.…``. Wzorzec, a nie sam napis
#: „deleted-”, bo ten stoi też w nazwie klasy przełącznika (``deleted-toggle``).
TECHNICAL_ADDRESS = re.compile(r"deleted-\d+@")


def shows_technical_address(body: str) -> bool:
    """Czy adres techniczny widać na stronie – w tekście albo w atrybucie ``title``.

    Parametr zapytania w odnośniku (``?actor=deleted-…%40…`` do przeglądarki audytu) jest
    zakodowany i nie trafia pod ten wzorzec – i słusznie: to filtr, a nie napis na ekranie.
    """
    return bool(TECHNICAL_ADDRESS.search(body))


def deleted_participant(**kwargs) -> Participant:
    """Uczestnik, który usunął konto – przez tę samą funkcję, co w produkcji (``anonymise_account``)."""
    participant = ParticipantFactory(**kwargs)
    anonymise_account(participant.user)
    participant.refresh_from_db()
    participant.user.refresh_from_db()
    return participant


def person(last: str, first: str = "Jan", email: str | None = None, **participant) -> Participant:
    email = email or f"{last.lower()}.{first.lower()}@example.test"
    user = UserFactory(email=email, first_name=first, last_name=last, groups=["participant"])
    return ParticipantFactory(user=user, **participant)


def codes(response) -> list[str]:
    return [row["public_code"] for row in response.context["rows"]]


def emails(response) -> list[str]:
    return [row["user"].email for row in response.context["rows"]]


@pytest.fixture
def logged(web_client, coordinator):
    web_client.force_login(coordinator)
    return web_client


# --- reguła rozpoznania ---------------------------------------------------------------------------


def test_the_rule_recognises_both_forms_of_the_anonymised_domain():
    assert is_anonymised_email("deleted-7@invalid.olimpiadakwantowa.pl")
    assert is_anonymised_email("deleted-7@invalid.olimpiadajuniorow.pl")
    assert is_anonymised_email("DELETED-7@INVALID.olimpiadakwantowa.pl")
    # Część lokalna „invalid.” nie jest domeną – to prawdziwy adres i ma zostać widoczny.
    assert not is_anonymised_email("invalid.jan@example.com")
    assert not is_anonymised_email("")
    assert not is_anonymised(None)


def test_the_queryset_rule_matches_the_python_rule():
    kept = UserFactory(email="invalid.jan@example.com", first_name="", last_name="")
    blocked = UserFactory(email="zablokowany@example.test", is_active=False)
    gone = deleted_participant()

    assert set(User.objects.anonymised()) == {gone.user}
    assert kept in User.objects.exclude_anonymised()
    # Konto zablokowane przez organizatora ma dane i ma zostać na liście – ``is_active`` to nie reguła.
    assert blocked in User.objects.exclude_anonymised()
    assert list(Participant.objects.anonymised()) == [gone]
    assert gone not in Participant.objects.exclude_anonymised()
    assert User.objects.filter(anonymised_q()).count() == 1


def test_the_label_never_carries_the_technical_address():
    gone = deleted_participant()

    assert person_label(gone.user, gone.public_code) == f"{DELETED_ACCOUNT_LABEL} ({gone.public_code})"
    assert not shows_technical_address(person_label(gone.user))


# --- lista kont: przełącznik kont usuniętych ------------------------------------------------------


def test_deleted_accounts_are_hidden_from_the_account_list_by_default(logged, participant):
    gone = deleted_participant()

    response = logged.get(ACCOUNTS)
    body = response.content.decode()

    assert participant.user.email in body
    assert gone.user.email not in body
    assert not shows_technical_address(body)
    assert "Pokaż usunięte konta (1)" in body


def test_the_toggle_shows_deleted_accounts_with_a_neutral_label(logged):
    gone = deleted_participant()

    body = logged.get(ACCOUNTS, {"usuniete": "1"}).content.decode()

    assert gone.public_code in body
    assert DELETED_ACCOUNT_LABEL in body
    assert "Ukryj usunięte konta" in body
    # Adres techniczny nie wychodzi na ekran ani w tekście, ani w nagłówku strony.
    assert gone.user.email not in body


def test_the_hidden_count_follows_the_search(logged):
    gone = deleted_participant()
    deleted_participant()

    body = logged.get(ACCOUNTS, {"q": gone.public_code}).content.decode()

    assert "Pokaż usunięte konta (1)" in body


def test_the_toggle_survives_pagination_role_filter_and_search(logged):
    UserFactory.create_batch(ACCOUNTS_PER_PAGE + 2)
    deleted_participant()

    response = logged.get(ACCOUNTS, {"usuniete": "1", "sort": "-email"})
    body = response.content.decode()

    assert "usuniete=1" in response.context["filter_query"]
    assert "sort=-email" in response.context["filter_query"]
    assert "page=2" in body and "usuniete=1&amp;sort=-email&amp;page=2" in body
    # Odnośniki filtra roli niosą przełącznik i sortowanie, a formularz wyszukiwania – ukryte pola.
    assert "?usuniete=1&amp;sort=-email&amp;role=participant" in body
    assert '<input type="hidden" name="usuniete" value="1">' in body
    assert '<input type="hidden" name="sort" value="-email">' in body


# --- lista kont: sortowanie ------------------------------------------------------------------------


def test_the_default_order_is_still_by_email(logged):
    UserFactory(email="c@example.test")
    UserFactory(email="a@example.test")
    UserFactory(email="b@example.test")

    listed = [email for email in emails(logged.get(ACCOUNTS)) if email.endswith("@example.test")]

    assert listed == sorted(listed)


@pytest.mark.parametrize("raw", ["password", "-password", "user__password", "email;drop", "--email", "pk"])
def test_an_unknown_sort_key_falls_back_to_the_default_without_an_error(logged, raw):
    UserFactory(email="b@example.test")
    UserFactory(email="a@example.test")

    response = logged.get(ACCOUNTS, {"sort": raw})

    assert response.status_code == 200
    assert response.context["controls"].sort == "email"
    assert response.context["controls"].explicit is False
    listed = emails(response)
    assert listed == sorted(listed)
    # Śmieć z adresu nie wędruje dalej po odnośnikach stronicowania.
    assert "sort=" not in response.context["filter_query"]


def test_account_list_sorts_by_each_column(logged):
    older = UserFactory(email="zz@example.test", first_name="Adam", last_name="Zielinski")
    older.date_joined = timezone.now() - timedelta(days=30)
    older.save(update_fields=["date_joined"])
    UserFactory(email="aa@example.test", first_name="Ewa", last_name="Abacka")
    pending = UserFactory(
        email="mm@example.test", last_name="Malinowski", email_verified_at=None, is_active=False
    )

    by_email_desc = emails(logged.get(ACCOUNTS, {"role": "other", "sort": "-email"}))
    by_name = emails(logged.get(ACCOUNTS, {"role": "other", "sort": "nazwisko"}))
    by_joined_desc = emails(logged.get(ACCOUNTS, {"role": "other", "sort": "-zalozone"}))
    by_status = emails(logged.get(ACCOUNTS, {"role": "other", "sort": "-stan"}))

    assert by_email_desc.index("zz@example.test") < by_email_desc.index("aa@example.test")
    assert (
        by_name.index("aa@example.test") < by_name.index("mm@example.test") < by_name.index("zz@example.test")
    )
    assert by_joined_desc.index("zz@example.test") == len(by_joined_desc) - 1
    # Malejąco po stanie: najpierw nieaktywowane.
    assert by_status[0] == pending.email


def test_the_active_column_carries_aria_sort_and_the_reverse_link(logged):
    body = logged.get(ACCOUNTS, {"sort": "nazwisko"}).content.decode()

    assert 'aria-sort="ascending"' in body
    assert "?sort=-nazwisko" in body
    # Kolumna nieaktywna nie ma ``aria-sort`` wcale – tylko jedna kolumna jest posortowana.
    assert body.count("aria-sort=") == 1


def test_pagination_keeps_the_sort_and_is_stable(logged):
    UserFactory.create_batch(ACCOUNTS_PER_PAGE + 5, first_name="Jan", last_name="Kowalski")

    first = logged.get(ACCOUNTS, {"sort": "nazwisko"})
    second = logged.get(ACCOUNTS, {"sort": "nazwisko", "page": 2})

    assert "sort=nazwisko" in first.context["filter_query"]
    seen_first = {row["user"].pk for row in first.context["rows"]}
    seen_second = {row["user"].pk for row in second.context["rows"]}
    # Remis na nazwisku rozstrzyga ``pk`` – żaden wiersz nie trafia na obie strony naraz.
    assert not seen_first & seen_second
    assert len(seen_first) + len(seen_second) == first.context["paginator"].count


# --- lista uczestników (?role=participant) ---------------------------------------------------------


def test_participant_list_hides_deleted_accounts_and_shows_profile_columns(logged, participant):
    gone = deleted_participant()

    response = logged.get(ACCOUNTS, PARTICIPANTS)
    body = response.content.decode()

    assert response.context["participant_mode"] is True
    assert codes(response) == [participant.public_code]
    assert gone.public_code not in body
    assert "Szkoła" in body and "Województwo" in body and "Zgoda opiekuna" in body
    assert "Pokaż usunięte konta (1)" in body


def test_participant_list_toggle_shows_the_code_and_no_address(logged):
    gone = deleted_participant()

    body = logged.get(ACCOUNTS, {**PARTICIPANTS, "usuniete": "1"}).content.decode()

    assert gone.public_code in body
    assert DELETED_ACCOUNT_LABEL in body
    assert not shows_technical_address(body)


@pytest.fixture
def three(elim_stage):
    """Trzy profile różniące się każdą sortowalną kolumną – kolejność zna test."""
    a = person(
        "Adamska", "Zofia", school="LO Bielsko", district=Voivodeship.SLASKIE, grade=1, guardian_consent=True
    )
    b = person(
        "Borowski",
        "Adam",
        school="LO Arkadia",
        district=Voivodeship.MAZOWIECKIE,
        grade=3,
        guardian_consent=False,
    )
    c = person(
        "Cichy", "Marek", school="LO Chełm", district=Voivodeship.LUBELSKIE, grade=2, guardian_consent=False
    )
    # Prace: Borowski oddał dwa zadania (jedno w dwóch wersjach), Cichy jedno, Adamska nic.
    entry_b = StageEntryFactory(participant=b, stage=elim_stage)
    entry_c = StageEntryFactory(participant=c, stage=elim_stage)
    first = SubmissionFactory(entry=entry_b, problem__stage=elim_stage)
    SubmissionFactory(entry=entry_b, problem=first.problem, version=2)
    SubmissionFactory(entry=entry_b, problem__stage=elim_stage)
    SubmissionFactory(entry=entry_c, problem__stage=elim_stage)
    return a, b, c


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("nazwisko", "abc"),
        ("-nazwisko", "cba"),
        ("imie", "bca"),
        ("email", "abc"),
        ("szkola", "bac"),
        ("wojewodztwo", "cba"),
        ("klasa", "acb"),
        ("-opiekun", "a"),
        ("-prace", "bca"),
        ("prace", "acb"),
    ],
)
def test_participant_list_sorts_by_each_column(logged, three, sort, expected):
    by_letter = dict(zip("abc", three, strict=True))

    listed = codes(logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": sort}))

    wanted = [by_letter[letter].public_code for letter in expected]
    assert listed[: len(wanted)] == wanted


def test_voivodeships_sort_by_their_polish_names_not_by_slugs(logged):
    """Slug ``lodzkie`` jest przed ``lubelskie``, ale nazwa „łódzkie” stoi w alfabecie po „lubuskie”."""
    lodz = person("Lodz", district=Voivodeship.LODZKIE)
    lublin = person("Lublin", district=Voivodeship.LUBELSKIE)
    lubusz = person("Lubusz", district=Voivodeship.LUBUSKIE)

    listed = codes(logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": "wojewodztwo"}))

    assert listed == [lublin.public_code, lubusz.public_code, lodz.public_code]


def test_participant_list_sorts_by_code_and_date(logged, three):
    newest = three[2]
    newest.user.date_joined = timezone.now() + timedelta(minutes=5)
    newest.user.save(update_fields=["date_joined"])

    by_code = codes(logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": "kod"}))
    by_joined = codes(logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": "-zalozone"}))

    assert by_code == sorted(by_code)
    assert by_joined[0] == newest.public_code


def test_participant_list_shows_the_work_count(logged, three):
    rows = {row["public_code"]: row["works"] for row in logged.get(ACCOUNTS, PARTICIPANTS).context["rows"]}

    assert rows[three[1].public_code] == 2
    assert rows[three[2].public_code] == 1
    assert rows[three[0].public_code] == 0


def test_a_participant_sort_key_is_not_accepted_on_the_plain_account_list(logged, three):
    response = logged.get(ACCOUNTS, {"sort": "szkola"})

    assert response.status_code == 200
    assert response.context["controls"].sort == "email"


def test_participant_list_query_count_does_not_grow_with_rows(logged, elim_stage):
    def measure(sort: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            assert logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": sort}).status_code == 200
        return len(queries)

    for index in range(3):
        entry = StageEntryFactory(participant=person(f"Nazwisko{index}"), stage=elim_stage)
        SubmissionFactory(entry=entry, problem__stage=elim_stage)
    measure("nazwisko")  # rozgrzewka: sesja, konkurs i menu liczą się raz na proces, nie na wiersz
    few = {sort: measure(sort) for sort in ("nazwisko", "-prace", "szkola", "kod")}
    for index in range(3, 12):
        entry = StageEntryFactory(participant=person(f"Nazwisko{index}"), stage=elim_stage)
        SubmissionFactory(entry=entry, problem__stage=elim_stage)
    many = {sort: measure(sort) for sort in few}

    assert many == few


def test_participant_list_stays_within_its_competition(logged, other_competition, participant):
    stranger = ParticipantFactory(competition=other_competition)

    listed = codes(logged.get(ACCOUNTS, {**PARTICIPANTS, "sort": "kod"}))

    assert participant.public_code in listed
    assert stranger.public_code not in listed


# --- wyszukiwarka ----------------------------------------------------------------------------------


def test_search_hides_deleted_participants_and_counts_them(logged):
    gone = deleted_participant()

    body = logged.get("/coordinator/search/", {"q": gone.public_code}).content.decode()

    assert "Pokaż usunięte konta (1)" in body
    assert not shows_technical_address(body)
    assert f">{DELETED_ACCOUNT_LABEL}<" not in body


def test_search_toggle_shows_deleted_participants_with_the_label(logged):
    gone = deleted_participant()

    body = logged.get("/coordinator/search/", {"q": gone.public_code, "usuniete": "1"}).content.decode()

    assert DELETED_ACCOUNT_LABEL in body
    assert gone.public_code in body
    assert not shows_technical_address(body)
    assert "Ukryj usunięte konta" in body


def test_search_for_the_technical_address_finds_nothing_by_default(logged):
    deleted_participant()

    body = logged.get("/coordinator/search/", {"q": "invalid"}).content.decode()

    assert not shows_technical_address(body)
    assert "Nic nie znaleziono" in body


# --- komisja ----------------------------------------------------------------------------------------


def deleted_member(**kwargs):
    member = ActiveReviewerFactory(**kwargs)
    anonymise_account(member.user)
    member.user.refresh_from_db()
    return member


def test_members_list_hides_deleted_accounts_with_a_toggle(logged, reviewer):
    gone = deleted_member()

    hidden = logged.get("/coordinator/members/").content.decode()
    shown = logged.get("/coordinator/members/", {"usuniete": "1"}).content.decode()

    assert reviewer.user.email in hidden
    assert f"/coordinator/members/{gone.pk}/" not in hidden
    assert "Pokaż usunięte konta (1)" in hidden
    assert f"/coordinator/members/{gone.pk}/" in shown
    assert DELETED_ACCOUNT_LABEL in shown
    assert not shows_technical_address(shown)


def test_committee_queues_and_the_badge_skip_deleted_accounts(logged):
    gone = deleted_member()
    pending = CommitteeMemberFactory(status=CommitteeStatus.PENDING)
    anonymise_account(pending.user)

    body = logged.get("/coordinator/committee/").content.decode()

    assert not shows_technical_address(body)
    assert gone.user.email not in body
    from apps.web.coordinator_nav import attention_counters

    assert attention_counters(competition=gone.competition)["committee"] == 0


def test_member_card_of_a_deleted_account_shows_the_label(logged):
    gone = deleted_member()

    body = logged.get(f"/coordinator/members/{gone.pk}/").content.decode()

    assert DELETED_ACCOUNT_LABEL in body
    assert not shows_technical_address(body)


# --- listy, które konta usunięte muszą pokazać ------------------------------------------------------


def test_participant_card_of_a_deleted_account_shows_the_code_not_the_address(logged, entry):
    anonymise_account(entry.participant.user)

    body = logged.get(f"/coordinator/participants/{entry.participant.pk}/").content.decode()

    assert entry.participant.public_code in body
    assert DELETED_ACCOUNT_LABEL in body
    assert not shows_technical_address(body)


def test_assignments_keep_the_work_of_a_deleted_account_under_its_code(logged, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0])
    anonymise_account(entry.participant.user)

    body = logged.get(f"/coordinator/stages/{entry.stage.pk}/assignments/").content.decode()

    assert entry.participant.public_code in body
    assert not shows_technical_address(body)


def test_results_preview_keeps_the_public_code_of_a_deleted_account(logged, entry):
    """Wynik zostaje pod kodem publicznym – tabela wyników to dokumentacja zawodów, nie lista osób."""
    anonymise_account(entry.participant.user)

    response = logged.post(f"/coordinator/stages/{entry.stage.pk}/results/compute/")
    body = response.content.decode()

    assert response.status_code == 200
    assert entry.participant.public_code in body
    assert not shows_technical_address(body)


def test_the_audit_log_names_a_deleted_actor_neutrally(logged, participant):
    anonymise_account(participant.user)  # wpis „account.anonymised” z wykonawcą = samo konto

    body = logged.get("/coordinator/audit/").content.decode()

    assert "account.anonymised" in body
    assert DELETED_ACCOUNT_LABEL in body
    assert not shows_technical_address(body)


def test_the_account_edit_page_of_a_deleted_account_has_a_neutral_heading(logged):
    gone = deleted_participant()

    body = logged.get(f"/coordinator/accounts/{gone.user.pk}/").content.decode()

    assert f"<title>Konto {DELETED_ACCOUNT_LABEL}" in body


# --- warsztaty --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("workshops_page")
def test_workshop_attendance_hides_deleted_participants_with_a_toggle(logged, elim_stage):
    kept = person("Obecna")
    StageEntryFactory(participant=kept, stage=elim_stage)
    gone = ParticipantFactory()
    StageEntryFactory(participant=gone, stage=elim_stage)
    anonymise_account(gone.user)

    hidden = logged.get("/coordinator/workshops/attendance/").content.decode()
    shown = logged.get("/coordinator/workshops/attendance/", {"usuniete": "1"}).content.decode()

    assert kept.public_code in hidden
    assert f'name="participant" value="{gone.pk}"' not in hidden
    assert "Pokaż usunięte konta (1)" in hidden
    assert f'name="participant" value="{gone.pk}"' in shown
    assert not shows_technical_address(shown)
    # Zapis strony wraca z przełącznikiem – tabela po zapisie wygląda tak samo jak przed.
    response = logged.post(
        "/coordinator/workshops/attendance/", {"participant": [gone.pk], "usuniete": "1", "query": "Ob ec"}
    )
    assert response.status_code == 302
    assert "usuniete=1" in response["Location"]
    assert "q=Ob%20ec" in response["Location"]


# --- eksporty ---------------------------------------------------------------------------------------


def _csv(response) -> list[list[str]]:
    text = b"".join(response.streaming_content).decode("utf-8").lstrip("﻿")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def test_participant_export_skips_deleted_accounts_by_default(logged, entry):
    gone = ParticipantFactory()
    StageEntryFactory(participant=gone, stage=entry.stage)
    anonymise_account(gone.user)

    rows = _csv(logged.get("/coordinator/export/participants/csv/"))
    page = logged.get("/coordinator/export/").content.decode()

    assert [row[0] for row in rows[1:]] == [entry.participant.public_code]
    assert "Pokaż usunięte konta (1)" in page


def test_participant_export_with_the_toggle_uses_the_label(logged, entry):
    gone = ParticipantFactory()
    StageEntryFactory(participant=gone, stage=entry.stage)
    anonymise_account(gone.user)

    rows = _csv(logged.get("/coordinator/export/participants/csv/", {"usuniete": "1"}))
    page = logged.get("/coordinator/export/", {"usuniete": "1"}).content.decode()

    by_code = {row[0]: row for row in rows[1:]}
    assert DELETED_ACCOUNT_LABEL in by_code[gone.public_code]
    assert not any(shows_technical_address(cell) for row in rows for cell in row)
    assert "/coordinator/export/participants/csv/?usuniete=1" in page


def test_review_export_keeps_the_review_of_a_deleted_reviewer_without_the_address(logged, entry, problems):
    submission = SubmissionFactory(entry=entry, problem=problems[0])
    gone = deleted_member()
    ReviewFactory(submission=submission, reviewer=gone)

    rows = _csv(logged.get("/coordinator/export/reviews/csv/", {"stage": entry.stage.pk}))

    assert len(rows) == 2
    assert DELETED_ACCOUNT_LABEL in rows[1]
    assert not any(shows_technical_address(cell) for cell in rows[1])


# --- pomocnik -------------------------------------------------------------------------------------


def test_list_controls_never_order_by_a_raw_parameter(rf):
    keys = (SortKey("nazwisko", "Nazwisko", ("last_name",)),)
    controls = ListControls(rf.get("/", {"sort": "-password"}), keys, default="nazwisko")

    assert controls.sort == "nazwisko"
    ordering = controls.order(User.objects.all()).query.order_by
    assert "password" not in str(ordering)
