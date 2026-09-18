"""Wgranie wykazu placówek ``/coordinator/institutions/import/`` (etap 2, T23).

Przedmiotem jest **krok podglądu**, bo to on odróżnia ten ekran od zwykłego formularza z plikiem:

- podgląd liczy wszystko i **nie zapisuje ani jednego wiersza** – ani placówki, ani wpisu
  audytowego, bo nic się nie stało,
- błąd wiersza nie przerywa wgrania i wraca z **numerem linii w pliku**,
- zatwierdzenie jest drugim żądaniem z tym samym plikiem i dopiero ono zapisuje; ustawienia
  z kroku pierwszego (wygaszanie nieobecnych, źródło) przeżywają drogę do kroku drugiego,
- ``deactivate_missing`` wygasza, a nie kasuje – wiersz zostaje w bazie razem z profilami, które
  go wskazują,
- wpis audytowy (``custom_directory.imported``) niesie **same liczniki**: nazwa placówki jest daną
  kontrahenta organizatora i nie ma czego robić w tabeli, którą czyta operator platformy.

Reguł samego parsera (kodowanie, separator, aliasy nagłówków, upsert) ten plik nie powtarza –
pilnuje ich ``apps/schools/tests/test_custom_institutions_import.py``, czyli test zadania T24.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.core.models import AuditLog
from apps.schools.custom import AUDIT_IMPORTED, CustomInstitution
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

IMPORT_URL = "/coordinator/institutions/import/"
FEATURE = "custom_school_directory"

HEADER = "nazwa;identyfikator;rodzaj;kraj;region;miejscowosc;kod_pocztowy;adres"


@pytest.fixture
def coordinator_client(client_for, competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


def upload(*rows: str, name: str = "uczelnie.csv") -> SimpleUploadedFile:
    content = "\r\n".join((HEADER, *rows))
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


def post(client, *rows: str, **fields):
    data = {"file": upload(*rows), **fields}
    return client.post(IMPORT_URL, data)


def body(response) -> str:
    return response.content.decode()


# --- wejście ----------------------------------------------------------------------------------


def test_the_screen_lists_the_columns_and_the_limits(coordinator_client):
    response = coordinator_client.get(IMPORT_URL)

    assert response.status_code == 200
    content = body(response)
    assert "nazwa" in content
    assert "identyfikator" in content
    assert str(response.context["max_rows"]) in content


def test_the_screen_is_off_without_the_flag(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)

    assert client.get(IMPORT_URL).status_code == 404
    assert client.post(IMPORT_URL, {"file": upload("Uczelnia;;;;;;;")}).status_code == 404


# --- podgląd ----------------------------------------------------------------------------------


def test_the_preview_counts_everything_and_saves_nothing(coordinator_client, competition):
    response = post(coordinator_client, "Uniwersytet;A1;UNIVERSITY;;;Kraków;;", "Ośrodek;A2;OTHER;;;Gdańsk;;")

    assert response.status_code == 200
    report = response.context["report"]
    assert (report.created, report.updated, report.unchanged, report.skipped) == (2, 0, 0, 0)
    assert report.dry_run is True
    assert CustomInstitution.objects.count() == 0
    assert not AuditLog.objects.filter(action=AUDIT_IMPORTED).exists()


def test_the_preview_shows_the_line_number_of_every_broken_row(coordinator_client):
    response = post(
        coordinator_client,
        "Uniwersytet;A1;UNIVERSITY;;;Kraków;;",
        "X;A2;OTHER;;;Gdańsk;;",
        "Ośrodek;A3;NIEZNANY;;;Gdynia;;",
    )

    issues = {issue.line: issue.message for issue in response.context["report"].errors}
    assert set(issues) == {3, 4}
    assert "nazwy" in issues[3]
    assert "NIEZNANY" in issues[4]
    content = body(response)
    assert "Podgląd" in content
    # Wiersz z błędem nie przerywa wgrania: jeden dobry wiersz nadal jest policzony.
    assert response.context["report"].created == 1


def test_the_preview_carries_the_settings_into_the_confirmation_step(coordinator_client):
    response = post(
        coordinator_client,
        "Uniwersytet;A1;UNIVERSITY;;;Kraków;;",
        deactivate_missing="on",
        source_label="wykaz 2026",
    )

    form = response.context["form"]
    assert form.is_bound is False
    assert form.initial["deactivate_missing"] is True
    assert form.initial["source_label"] == "wykaz 2026"
    assert form.initial["confirm"] is True


def test_a_file_that_is_not_a_csv_is_refused_under_the_field(coordinator_client):
    data = {"file": SimpleUploadedFile("wykaz.xlsx", b"PK\x03\x04", content_type="application/zip")}

    response = coordinator_client.post(IMPORT_URL, data)

    assert response.status_code == 400
    assert "CSV" in response.context["form"].errors["file"][0]
    assert CustomInstitution.objects.count() == 0


def test_a_file_without_the_required_column_says_which_headers_are_expected(coordinator_client):
    data = {"file": SimpleUploadedFile("wykaz.csv", b"miasto;adres\nKrakow;ul. Testowa", "text/csv")}

    response = coordinator_client.post(IMPORT_URL, data)

    assert response.status_code == 400
    assert "nazwa" in body(response)
    assert CustomInstitution.objects.count() == 0


# --- zatwierdzenie ----------------------------------------------------------------------------


def test_the_confirmation_saves_the_rows_and_writes_one_audit_entry(coordinator_client, competition):
    response = post(
        coordinator_client,
        "Uniwersytet;A1;UNIVERSITY;;;Kraków;;",
        "Ośrodek;A2;OTHER;DE;;Berlin;;",
        confirm="True",
    )

    assert response.status_code == 302
    assert response["Location"] == "/coordinator/institutions/"
    rows = {row.external_id: row for row in CustomInstitution.objects.for_competition(competition)}
    assert set(rows) == {"A1", "A2"}
    assert rows["A2"].country == "DE"
    entry = AuditLog.objects.get(action=AUDIT_IMPORTED)
    assert entry.diff == {"created": 2, "updated": 0, "unchanged": 0, "deactivated": 0, "skipped": 0}


def test_the_source_label_defaults_to_the_file_name_with_a_date(coordinator_client, competition):
    post(coordinator_client, "Uniwersytet;A1;UNIVERSITY;;;Kraków;;", confirm="True")

    row = CustomInstitution.objects.get()
    assert row.source_label.startswith("uczelnie.csv, ")


def test_the_typed_source_label_wins(coordinator_client, competition):
    post(
        coordinator_client,
        "Uniwersytet;A1;UNIVERSITY;;;Kraków;;",
        confirm="True",
        source_label="wykaz z rektoratu",
    )

    assert CustomInstitution.objects.get().source_label == "wykaz z rektoratu"


def test_a_row_missing_from_the_file_is_never_deleted(coordinator_client, competition):
    stale = CustomInstitution.objects.create(competition=competition, name="Dawny Ośrodek", external_id="A9")
    ParticipantFactory(custom_institution_ref=stale)

    post(
        coordinator_client,
        "Uniwersytet;A1;UNIVERSITY;;;Kraków;;",
        confirm="True",
        deactivate_missing="on",
    )

    stale.refresh_from_db()
    assert stale.is_active is False
    assert CustomInstitution.objects.filter(pk=stale.pk).exists()


def test_without_the_checkbox_the_missing_rows_stay_active(coordinator_client, competition):
    stale = CustomInstitution.objects.create(competition=competition, name="Dawny Ośrodek", external_id="A9")

    post(coordinator_client, "Uniwersytet;A1;UNIVERSITY;;;Kraków;;", confirm="True")

    stale.refresh_from_db()
    assert stale.is_active is True


def test_the_second_upload_of_the_same_file_changes_nothing(coordinator_client, competition):
    post(coordinator_client, "Uniwersytet;A1;UNIVERSITY;;;Kraków;;", confirm="True")

    post(coordinator_client, "Uniwersytet;A1;UNIVERSITY;;;Kraków;;", confirm="True")

    assert CustomInstitution.objects.for_competition(competition).count() == 1
    entries = AuditLog.objects.filter(action=AUDIT_IMPORTED).order_by("id")
    assert entries.count() == 2
    assert entries.last().diff["unchanged"] == 1
