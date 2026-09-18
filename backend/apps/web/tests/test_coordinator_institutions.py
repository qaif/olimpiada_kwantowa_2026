"""Ekran „Słownik placówek” ``/coordinator/institutions/`` (etap 2, T23).

Przedmiotem jest to, czego nie widać po kodzie widoku:

- ekran jest za flagą ``custom_school_directory`` i bez niej daje **404**, a nie 403 (§ 2.1);
  uczestnik dostaje 403 niezależnie od stanu flagi,
- wykaz jednego konkursu jest niewidoczny w drugim – i to zarówno na liście, jak i pod adresem
  pojedynczego wiersza (404 z zawężonego querysetu, § 3.6),
- liczba dowiązanych uczestników przy wierszu kosztuje **jedno** zapytanie na cały ekran, a nie
  jedno na wiersz,
- placówki nikt tu nie kasuje: wycofanie to wyłączenie, a wpis audytowy niesie **sam
  identyfikator**, bo nazwa placówki jest daną kontrahenta organizatora,
- karta profilu rejestracji mówi wprost, kiedy wgrany wykaz nie karmi formularza (odznaczone
  ``allow_custom_directory`` przy włączonej fladze),
- plik eksportu ma nagłówki **importu**, więc da się go wgrać z powrotem bez poprawek.

Adresy są w ``apps/web/urls.py``: montaż wydania H rozwinął tam ``urls_institutions.urlpatterns``,
więc testy chodzą po mapie produkcyjnej. Ścieżki się nie zmieniły.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import CompetitionRole, RegistrationProfile
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.schools.custom import COLUMNS, CustomInstitution
from apps.schools.models import InstitutionType
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

LIST_URL = "/coordinator/institutions/"
EXPORT_URL = "/coordinator/institutions/export.csv"
TEMPLATE_URL = "/coordinator/institutions/template.csv"
FEATURE = "custom_school_directory"
PROFILE_FEATURE = "institution_types"


def enable(competition, *flags: str):
    """Włącza flagi obszaru tą samą drogą, którą zrobi to wdrożenie: wartością w bazie."""
    competition.feature_flags = {
        **(competition.feature_flags or {}),
        **dict.fromkeys(flags or (FEATURE,), True),
    }
    competition.save(update_fields=["feature_flags"])
    return competition


def coordinator_for(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def coordinator_client(client_for, competition):
    enable(competition)
    return coordinator_for(client_for, competition)


def institution(competition, **fields) -> CustomInstitution:
    data = {
        "name": "Uniwersytet Jagielloński",
        "city": "Kraków",
        "institution_type": InstitutionType.UNIVERSITY,
    }
    data.update(fields)
    return CustomInstitution.objects.create(competition=competition, **data)


def payload(**overrides) -> dict:
    data = {
        "name": "Ośrodek Badawczy",
        "institution_type": InstitutionType.OTHER,
        "external_id": "A7",
        "country": "",
        "region_code": "",
        "city": "Gdańsk",
        "postal_code": "",
        "address": "",
        "is_active": "on",
    }
    data.update(overrides)
    return data


def body(response) -> str:
    return response.content.decode()


def streamed(response) -> str:
    return b"".join(response.streaming_content).decode("utf-8")


# --- przełącznik ekranu -----------------------------------------------------------------------


def test_screen_is_off_for_a_competition_with_default_flags(client_for, competition):
    client = coordinator_for(client_for, competition)
    assert not competition.has_feature(FEATURE)

    assert client.get(LIST_URL).status_code == 404
    assert client.post(f"{LIST_URL}new/", payload()).status_code == 404
    assert client.get(f"{LIST_URL}import/").status_code == 404
    assert client.get(EXPORT_URL).status_code == 404
    assert client.get(TEMPLATE_URL).status_code == 404


def test_participant_gets_403_regardless_of_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(ParticipantFactory().user)

    assert client.get(LIST_URL).status_code == 403

    enable(competition)

    assert client.get(LIST_URL).status_code == 403


def test_anonymous_is_sent_to_the_login_page(client_for, competition):
    enable(competition)

    response = client_for(competition).get(LIST_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --- wykaz ------------------------------------------------------------------------------------


def test_the_list_shows_the_institutions_of_this_competition(coordinator_client, competition):
    institution(competition, name="Ośrodek Pomiarowy", city="Toruń")

    content = body(coordinator_client.get(LIST_URL))

    assert "Ośrodek Pomiarowy" in content
    assert "Toruń" in content


def test_another_competitions_dictionary_is_invisible(coordinator_client, competition, other_competition):
    theirs = institution(other_competition, name="Cudza Uczelnia", city="Poznań")

    content = body(coordinator_client.get(LIST_URL))

    assert "Cudza Uczelnia" not in content
    # Wiersz cudzego konkursu **nie istnieje** dla tego koordynatora – 404, nie 403 (§ 3.6).
    assert coordinator_client.get(f"{LIST_URL}{theirs.pk}/").status_code == 404
    assert coordinator_client.post(f"{LIST_URL}{theirs.pk}/deactivate/").status_code == 404


def test_the_search_narrows_by_name_and_by_city(coordinator_client, competition):
    institution(competition, name="Ośrodek Pomiarowy", city="Toruń")
    institution(competition, name="Klub Fizyczny", city="Kraków")

    by_name = body(coordinator_client.get(LIST_URL, {"q": "pomiarowy"}))
    by_city = body(coordinator_client.get(LIST_URL, {"q": "krakow"}))

    assert "Ośrodek Pomiarowy" in by_name
    assert "Klub Fizyczny" not in by_name
    # Składanie znaków jest **to samo**, co w wyszukiwarce rejestracji: „krakow” znajduje „Kraków”.
    assert "Klub Fizyczny" in by_city
    assert "Ośrodek Pomiarowy" not in by_city


def test_the_filters_narrow_by_kind_and_by_state(coordinator_client, competition):
    institution(competition, name="Uczelnia Partnerska", institution_type=InstitutionType.UNIVERSITY)
    institution(
        competition, name="Wygaszony Ośrodek", institution_type=InstitutionType.OTHER, is_active=False
    )

    by_kind = body(coordinator_client.get(LIST_URL, {"type": InstitutionType.UNIVERSITY}))
    by_state = body(coordinator_client.get(LIST_URL, {"state": "inactive"}))
    unknown = body(coordinator_client.get(LIST_URL, {"type": "NIEZNANY", "state": "cokolwiek"}))

    assert "Uczelnia Partnerska" in by_kind
    assert "Wygaszony Ośrodek" not in by_kind
    assert "Wygaszony Ośrodek" in by_state
    assert "Uczelnia Partnerska" not in by_state
    # Śmieci w adresie nie są błędem człowieka: filtr spoza listy po prostu nie zawęża.
    assert "Uczelnia Partnerska" in unknown
    assert "Wygaszony Ośrodek" in unknown


def test_the_row_counts_the_participants_linked_to_it(coordinator_client, competition):
    row = institution(competition, name="Ośrodek Pomiarowy")
    ParticipantFactory(custom_institution_ref=row)
    ParticipantFactory(custom_institution_ref=row)
    ParticipantFactory()

    context = coordinator_client.get(LIST_URL).context

    assert [(item["institution"].pk, item["participants"]) for item in context["rows"]] == [(row.pk, 2)]


def test_the_cost_does_not_grow_with_the_number_of_rows(coordinator_client, competition):
    """Liczba dowiązań idzie zapytaniem grupującym, więc koszt nie zależy od liczby wierszy."""
    institution(competition, name="Pierwsza Placówka")
    ParticipantFactory(custom_institution_ref=institution(competition, name="Druga Placówka"))
    # Rozgrzewka: liczniki menu mają minutową pamięć podręczną, więc pierwsze wejście na
    # **którykolwiek** ekran panelu kosztuje o pięć zapytań agregujących więcej niż następne.
    coordinator_client.get(LIST_URL)

    with CaptureQueriesContext(connection) as small:
        assert coordinator_client.get(LIST_URL).status_code == 200

    for index in range(12):
        ParticipantFactory(custom_institution_ref=institution(competition, name=f"Placówka {index}"))

    with CaptureQueriesContext(connection) as large:
        assert coordinator_client.get(LIST_URL).status_code == 200

    assert len(large) == len(small)


# --- wpis ręczny ------------------------------------------------------------------------------


def test_a_row_can_be_added_by_hand(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}new/", payload())

    assert response.status_code == 302
    row = CustomInstitution.objects.for_competition(competition).get()
    assert (row.name, row.city, row.external_id) == ("Ośrodek Badawczy", "Gdańsk", "A7")
    # Kolumny wyliczane liczy ``save()`` modelu – wiersz ma być od razu do znalezienia.
    assert "osrodek" in row.search_text
    assert AuditLog.objects.filter(action="custom_institution.created").exists()


def test_the_audit_entry_carries_the_identifier_and_no_name(coordinator_client, competition):
    coordinator_client.post(f"{LIST_URL}new/", payload())

    entry = AuditLog.objects.get(action="custom_institution.created")
    assert entry.diff == {"id": CustomInstitution.objects.get().pk}
    assert "Ośrodek" not in str(entry.diff)


def test_a_duplicate_identifier_is_refused_under_the_field(coordinator_client, competition):
    institution(competition, external_id="A7")

    response = coordinator_client.post(f"{LIST_URL}new/", payload())

    assert response.status_code == 400
    assert response.context["form"].errors["external_id"] == [
        "Placówka o tym identyfikatorze już jest w tym konkursie."
    ]
    assert CustomInstitution.objects.for_competition(competition).count() == 1


def test_the_same_identifier_is_free_in_another_competition(
    coordinator_client, competition, other_competition
):
    institution(other_competition, external_id="A7")

    assert coordinator_client.post(f"{LIST_URL}new/", payload()).status_code == 302


def test_the_country_has_to_be_a_two_letter_code(coordinator_client, competition):
    """Dwie litery, a nie dwa dowolne znaki – komunikat jest ten sam, co przy imporcie pliku."""
    response = coordinator_client.post(f"{LIST_URL}new/", payload(country="12"))

    assert response.status_code == 400
    assert response.context["form"].errors["country"] == [
        "Kraj podaj dwuliterowym kodem (ISO 3166-1), na przykład „DE”."
    ]


def test_the_country_is_stored_upper_case(coordinator_client, competition):
    coordinator_client.post(f"{LIST_URL}new/", payload(country="de"))

    assert CustomInstitution.objects.get().country == "DE"


def test_a_two_character_name_is_refused(coordinator_client, competition):
    response = coordinator_client.post(f"{LIST_URL}new/", payload(name="LO"))

    assert response.status_code == 400
    assert response.context["form"].errors["name"] == ["Podaj nazwę placówki – co najmniej trzy znaki."]


def test_the_edit_screen_saves_the_row_and_lists_changed_fields(coordinator_client, competition):
    row = institution(competition, name="Ośrodek Pomiarowy", city="Toruń")

    response = coordinator_client.post(
        f"{LIST_URL}{row.pk}/", payload(name="Ośrodek Pomiarowy II", city="Toruń", external_id="")
    )

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.name == "Ośrodek Pomiarowy II"
    entry = AuditLog.objects.get(action="custom_institution.updated")
    assert entry.diff["id"] == row.pk
    assert "name" in entry.diff["fields"]
    # Nazwy pól – tak, wartości – nie.
    assert "Ośrodek Pomiarowy II" not in str(entry.diff)


# --- wyłączanie i włączanie -------------------------------------------------------------------


def test_deactivation_hides_the_row_without_deleting_it(coordinator_client, competition):
    row = institution(competition)
    ParticipantFactory(custom_institution_ref=row)

    response = coordinator_client.post(f"{LIST_URL}{row.pk}/deactivate/")

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.is_active is False
    assert CustomInstitution.objects.filter(pk=row.pk).exists()
    assert AuditLog.objects.get(action="custom_institution.updated").diff == {"id": row.pk}


def test_activation_brings_the_row_back(coordinator_client, competition):
    row = institution(competition, is_active=False)

    coordinator_client.post(f"{LIST_URL}{row.pk}/activate/")

    row.refresh_from_db()
    assert row.is_active is True


def test_repeating_the_action_does_not_write_a_second_audit_entry(coordinator_client, competition):
    row = institution(competition)

    coordinator_client.post(f"{LIST_URL}{row.pk}/deactivate/")
    coordinator_client.post(f"{LIST_URL}{row.pk}/deactivate/")

    assert AuditLog.objects.filter(action="custom_institution.updated").count() == 1


# --- karta profilu rejestracji ----------------------------------------------------------------


def test_the_card_warns_that_registration_does_not_read_the_dictionary(coordinator_client, competition):
    """Najważniejsze zdanie tego ekranu: wykaz bez zgody profilu jest tabelą, do której nikt nie zagląda."""
    content = body(coordinator_client.get(LIST_URL))

    assert "nie korzysta" in content


def test_the_card_stops_warning_once_the_profile_allows_the_dictionary(coordinator_client, competition):
    RegistrationProfile.objects.create(competition=competition, allow_custom_directory=True)

    content = body(coordinator_client.get(LIST_URL))

    assert "nie korzysta" not in content


def test_the_card_links_to_the_profile_screen_only_behind_its_own_flag(client_for, competition):
    enable(competition)
    client = coordinator_for(client_for, competition)

    assert "/coordinator/registration-profile/" not in body(client.get(LIST_URL))

    enable(competition, PROFILE_FEATURE)

    assert "/coordinator/registration-profile/" in body(client.get(LIST_URL))


# --- pliki ------------------------------------------------------------------------------------


def test_the_export_has_the_import_header(coordinator_client, competition):
    institution(competition, name="Ośrodek Pomiarowy", city="Toruń", external_id="A7")

    content = streamed(coordinator_client.get(EXPORT_URL))

    header = content.splitlines()[0].lstrip("﻿")
    assert header == ";".join(column.label for column in COLUMNS)
    assert "Ośrodek Pomiarowy" in content
    assert "A7" in content


def test_the_export_takes_the_same_narrowing_as_the_screen(coordinator_client, competition):
    institution(competition, name="Ośrodek Pomiarowy", city="Toruń")
    institution(competition, name="Klub Fizyczny", city="Kraków")

    content = streamed(coordinator_client.get(EXPORT_URL, {"q": "klub"}))

    assert "Klub Fizyczny" in content
    assert "Ośrodek Pomiarowy" not in content


def test_the_template_is_the_header_alone(coordinator_client, competition):
    institution(competition, name="Ośrodek Pomiarowy")

    content = streamed(coordinator_client.get(TEMPLATE_URL))

    assert content.lstrip("﻿").strip() == ";".join(column.label for column in COLUMNS)
    assert "Ośrodek" not in content
