"""Zamknięta lista województw: normalizacja, walidacja wejść i migracja danych.

Pole ``district`` przez kilka wersji było wolnym tekstem, więc każdy z tych testów pilnuje
innej granicy tej samej reguły: do bazy wchodzi wyłącznie wartość z ``Voivodeship``, a to,
co już w bazie leży, ma zostać na nią przetłumaczone bez wywracania wdrożenia.
"""

import importlib.util
from pathlib import Path

import pytest

from apps.accounts.models import CommitteeMember, Participant, Voivodeship, normalize_voivodeship
from apps.accounts.serializers import ParticipantRegisterSerializer, VerifyDistrictSerializer
from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.web.forms import ParticipantRegisterForm, VerifyDistrictForm

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "0007_district_normalise_voivodeship.py"


def load_migration():
    """Ładuje moduł migracji po ścieżce – nie jest importowalny normalną nazwą pakietu."""
    spec = importlib.util.spec_from_file_location("migration_0007_district", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mazowieckie", "mazowieckie"),
        ("Mazowieckie", "mazowieckie"),
        ("  MAZOWIECKIE  ", "mazowieckie"),
        ("mazowiecki", "mazowieckie"),  # forma przymiotnikowa z dawnych danych
        ("Woj. Mazowieckie", "mazowieckie"),
        ("województwo mazowieckie", "mazowieckie"),
        ("Małopolskie", "malopolskie"),  # etykieta z diakrytykami
        ("małopolski", "malopolskie"),
        ("łódzkie", "lodzkie"),
        ("ŁÓDZKIE", "lodzkie"),
        ("kujawsko-pomorski", "kujawsko-pomorskie"),
        ("warmińsko-mazurskie", "warminsko-mazurskie"),
        ("Atlantyda", None),
        ("woj.", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_voivodeship_maps_known_spellings(raw, expected):
    assert normalize_voivodeship(raw) == expected


def test_every_choice_value_and_label_maps_to_itself():
    """Nazwa własna każdego z 16 województw musi wracać jako jego wartość – w obu zapisach."""
    for value, label in Voivodeship.choices:
        assert normalize_voivodeship(value) == value
        assert normalize_voivodeship(label) == value


def test_serializers_reject_a_value_outside_the_list():
    payload = {
        "email": "kandydat@example.com",
        "password": "Poprawne-Haslo-2026",
        "first_name": "Jan",
        "last_name": "Kowalski",
        "school": "LO nr 1",
        "district": "Atlantyda",
        "birth_year": 2008,
        "gdpr_consent": True,
    }
    serializer = ParticipantRegisterSerializer(data=payload)

    assert serializer.is_valid() is False
    assert "district" in serializer.errors

    verify = VerifyDistrictSerializer(data={"district": "mazowiecki"})
    # Serializer jest twardszy od helpera: API dostaje listę wartości w schemacie OpenAPI,
    # więc nie ma powodu przyjmować od klienta wariantów zapisu.
    assert verify.is_valid() is False


def test_forms_reject_a_value_outside_the_list():
    form = ParticipantRegisterForm(
        {
            "email": "kandydat@example.com",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Jan",
            "last_name": "Kowalski",
            "school": "LO nr 1",
            "district": "Atlantyda",
            "birth_year": 2008,
            "gdpr_consent": True,
        }
    )

    assert form.is_valid() is False
    assert "district" in form.errors
    assert VerifyDistrictForm({"district": "Atlantyda"}).is_valid() is False
    assert VerifyDistrictForm({"district": "mazowieckie"}).is_valid() is True


def test_register_form_offers_an_empty_first_option():
    """Bez pustej pozycji przeglądarka przypisałaby uczestnikowi pierwsze województwo z listy."""
    choices = list(ParticipantRegisterForm().fields["district"].choices)

    assert choices[0] == ("", "— wybierz województwo —")
    assert len(choices) == len(Voivodeship.choices) + 1


@pytest.mark.django_db
def test_data_migration_normalises_legacy_spellings_and_keeps_the_unknown_ones(capsys):
    """Stare zapisy dostają wartość z listy; nierozpoznane zostają i idą do ostrzeżenia.

    Migracja nie ma prawa się wywrócić na jednym egzotycznym wierszu – produkcja musi się
    zmigrować, a poprawka takiego wpisu jest decyzją koordynatora, nie wdrożenia.
    """
    from django.apps import apps as app_registry

    legacy = ParticipantFactory(district="Mazowiecki")
    exotic = ParticipantFactory(district="Atlantyda")
    member = ActiveReviewerFactory(district="woj. Małopolskie")

    load_migration().normalise(app_registry, None)

    assert Participant.objects.get(pk=legacy.pk).district == "mazowieckie"
    assert Participant.objects.get(pk=exotic.pk).district == "Atlantyda"
    assert CommitteeMember.objects.get(pk=member.pk).district == "malopolskie"
    assert "Atlantyda" in capsys.readouterr().out
