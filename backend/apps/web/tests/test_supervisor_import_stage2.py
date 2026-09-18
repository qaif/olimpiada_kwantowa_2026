"""Ekrany importu po etapie 2: instrukcja, wzorcowy nagłówek i tabela podglądu (§ 4.4 T26).

Testy jednostkowe reguł stoją w ``apps/accounts/tests/test_bulk_registration_stage2.py``; tutaj
przedmiotem jest to, co widzi człowiek:

- **konkurs bez flag ma dzisiejszy ekran.** Ani jednej rubryki więcej w instrukcji, ani jednej
  kolumny więcej w podglądzie, ten sam wiersz nagłówka do skopiowania do arkusza (§ 0.1);
- **rubryka wymieniona w instrukcji jest rubryką widoczną w podglądzie.** Jedno źródło obu list
  (``bulk_registration.extra_columns``), bo kolumna, którą nauczyciel wypełni, a podgląd przemilczy,
  wygląda jak dane, które przepadły;
- **oba ekrany importu – nauczyciela i koordynatora – mówią to samo**, bo składa je ten sam
  fragment szablonu.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import GROUP_SUPERVISOR, Participant, SchoolSupervisor, Voivodeship
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.accounts.tests.factories import UserFactory
from apps.competitions.models import Category
from apps.tenancy.tests.factories import current_or_default_competition

pytestmark = pytest.mark.django_db

CATEGORIES_FLAG = "categories"

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"
ADULT_YEAR = timezone.localdate().year - 25

BASE_HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"

IMPORT_URL = "/supervisor/import/"
COORDINATOR_IMPORT_URL = "/coordinator/accounts/import/"


def enable(*flags):
    """Włącza flagi na konkursie kontekstu – tym samym, który rozstrzygnie żądanie."""
    competition = current_or_default_competition()
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])
    return competition


def upload(*rows: str, header: str = BASE_HEADER) -> SimpleUploadedFile:
    body = "\n".join([header, *rows]).encode("utf-8")
    return SimpleUploadedFile("klasa.csv", body, content_type="text/csv")


def student(email: str = "kasia@example.test", *, grade: int = 2, extra: str = "") -> str:
    return f"Kasia;Nowak;{email};{ADULT_YEAR};{grade};;;{extra}"


@pytest.fixture
def supervisor():
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


@pytest.fixture
def logged_supervisor(web_client, supervisor):
    web_client.force_login(supervisor.user)
    return supervisor


def labels(response) -> list[str]:
    return [column.label for column in response.context["columns"]]


# --- instrukcja i wzorcowy nagłówek --------------------------------------------------------------


def test_konkurs_bez_flag_ma_dzisiejsza_instrukcje(web_client, logged_supervisor):
    """Ekran opiekuna Konkursu #1: siedem rubryk i ten sam wiersz nagłówka, co przed etapem 2."""
    response = web_client.get(IMPORT_URL)

    assert response.status_code == 200
    assert response.context["header_line"] == BASE_HEADER
    assert response.context["show_region"] is False
    assert response.context["show_category"] is False
    assert response.context["show_institution"] is False


def test_flaga_regionow_doklada_rubryke_do_instrukcji(web_client, logged_supervisor):
    enable(CUSTOM_REGIONS_FLAG)

    response = web_client.get(IMPORT_URL)

    assert "region" in labels(response)
    assert response.context["header_line"].endswith(";region")
    assert "region" in response.content.decode()


def test_import_koordynatora_pokazuje_te_same_rubryki(web_client, coordinator):
    """Treść obu ekranów jest jednym fragmentem – zmiana w kodzie zmienia oba naraz."""
    enable(CATEGORIES_FLAG)
    web_client.force_login(coordinator)

    response = web_client.get(COORDINATOR_IMPORT_URL)

    assert "kategoria" in labels(response)
    # Kolumna koordynatora zostaje na swoim miejscu: rubryki konkursu stoją za nią, a nie zamiast.
    assert "e-mail opiekuna szkolnego" in labels(response)


# --- podgląd ------------------------------------------------------------------------------------


def test_podglad_bez_flag_ma_dzisiejsze_kolumny_tabeli(web_client, logged_supervisor):
    response = web_client.post(IMPORT_URL, {"file": upload(student())})

    assert response.status_code == 200
    body = response.content.decode()
    assert ">Region<" not in body
    assert ">Kategoria<" not in body
    assert ">Placówka<" not in body


def test_podglad_pokazuje_rozstrzygniety_region_i_kategorie(web_client, logged_supervisor):
    enable(CUSTOM_REGIONS_FLAG, CATEGORIES_FLAG)
    Category.objects.create(
        competition=current_or_default_competition(),
        code="mlodsza",
        name="Kategoria młodsza",
        grade_min=1,
        grade_max=5,
    )

    response = web_client.post(
        IMPORT_URL,
        {"file": upload(student(extra="pomorskie"), header=f"{BASE_HEADER};region")},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert ">Region<" in body
    assert ">Kategoria<" in body
    # Nazwa regionu, a nie jego kod: podgląd czyta nauczyciel, a nie filtr panelu.
    assert "pomorskie" in body
    assert "Kategoria młodsza" in body


def test_podglad_nazywa_rubryke_i_wartosc_w_odmowie(web_client, logged_supervisor):
    enable(CUSTOM_REGIONS_FLAG)

    response = web_client.post(
        IMPORT_URL,
        {"file": upload(student(extra="atlantyda"), header=f"{BASE_HEADER};region")},
    )

    assert response.context["preview"].to_create == 0
    assert "kolumna „region” („atlantyda”)" in response.content.decode()


# --- zatwierdzenie -------------------------------------------------------------------------------


def test_zatwierdzenie_zapisuje_region_wskazany_w_pliku(web_client, logged_supervisor):
    enable(CUSTOM_REGIONS_FLAG)
    preview = web_client.post(
        IMPORT_URL,
        {"file": upload(student(extra="pomorskie"), header=f"{BASE_HEADER};region")},
    )

    response = web_client.post(
        IMPORT_URL,
        {
            "rows": preview.context["preview"].token,
            "school": preview.context["school_name"],
        },
    )

    assert response.status_code == 302
    saved = Participant.objects.select_related("region").get(user__email="kasia@example.test")
    assert saved.district == Voivodeship.POMORSKIE
    assert saved.region.code == Voivodeship.POMORSKIE
