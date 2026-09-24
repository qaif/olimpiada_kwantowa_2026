"""Usunięcie konta czyści **resztę** danych profilu – i usunięty uczeń znika z panelu nauczyciela.

Decyzja organizatora z 24.09.2026 (wydanie v0.34.0). Do tej wersji ``anonymise_account`` wycierało
imię, nazwisko, adres, telefon, szkołę i datę urodzenia, ale zostawiało w profilu cztery pola:
adres rodzica (``guardian_email``), adres opiekuna szkolnego (``supervisor_email``), nazwę placówki
wpisaną ręcznie (``institution_name``) i dowiązanie do słownika placówek organizatora
(``custom_institution_ref``). Skutek widoczny: panel opiekuna („Moi uczniowie”) dopasowuje uczniów
**po adresie opiekuna**, więc uczeń, który skorzystał z prawa do usunięcia danych, dalej stał na
liście nauczyciela i w jego licznikach.

Testy pilnują trzech rzeczy:

- **pola znikają** przy anonimizacji (razem z uwagą i potrzebami szczególnymi z formularza
  przyjazdu oraz pseudonimami widza materiałów z warsztatów),
- **panel nauczyciela** nie pokazuje usuniętego ucznia – także profilu wytartego **przed** tą
  zmianą, który adres opiekuna nadal ma (filtr ``exclude_anonymised`` w ``students_of``),
- **migracja danych** ``accounts.0034`` wyrównuje profile wytarte wcześniej do nowej reguły.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.models import GROUP_SUPERVISOR, Participant, SchoolSupervisor
from apps.accounts.profile import anonymise_account
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.logistics import ArrivalForm, LogisticsNeed
from apps.competitions.tests.factories import StageEntryFactory
from apps.schools.custom import CustomInstitution
from apps.tenancy.tests.factories import current_or_default_competition
from apps.workshop_materials.models import WorkshopMaterialViewer
from apps.workshop_materials.stats import record_view, viewer_hash
from apps.workshop_materials.tests.helpers import make_material

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "wychowawczyni@szkola.test"


@pytest.fixture
def logged_supervisor(web_client):
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Ewa", last_name="Wychowawczyni")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    supervisor = SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )
    web_client.force_login(user)
    return supervisor


def _student(**kwargs) -> Participant:
    return ParticipantFactory(supervisor_email=SUPERVISOR_EMAIL, **kwargs)


def test_anonymisation_clears_remaining_profile_fields():
    competition = current_or_default_competition()
    institution = CustomInstitution.objects.create(competition=competition, name="Szkoła Muzyczna im. X")
    participant = _student(
        guardian_email="mama@example.test",
        institution_name="Szkoła Muzyczna im. X",
        custom_institution_ref=institution,
    )

    anonymise_account(participant.user)

    participant.refresh_from_db()
    assert participant.guardian_email == ""
    assert participant.supervisor_email == ""
    assert participant.institution_name == ""
    assert participant.custom_institution_ref is None
    # To, co z założenia zostaje – kod publiczny wiąże profil z ogłoszonymi wynikami.
    assert participant.public_code


def test_deleted_student_leaves_the_teachers_panel_and_counts(web_client, logged_supervisor, elim_stage):
    stays = _student()
    StageEntryFactory(stage=elim_stage, participant=stays)
    leaves = _student()
    StageEntryFactory(stage=elim_stage, participant=leaves)

    before = web_client.get(reverse("web:supervisor-students"))
    assert leaves.public_code in before.content.decode()
    assert len(before.context["rows"]) == 2

    anonymise_account(leaves.user)

    dashboard = web_client.get(reverse("web:supervisor")).content.decode()
    students = web_client.get(reverse("web:supervisor-students"))
    assert leaves.public_code not in dashboard
    assert leaves.public_code not in students.content.decode()
    assert stays.public_code in students.content.decode()
    assert len(students.context["rows"]) == 1


def test_profile_anonymised_before_the_fix_is_hidden_as_well(web_client, logged_supervisor, elim_stage):
    """Profil wytarty przed v0.34.0 ma jeszcze adres opiekuna – panel i tak go nie pokazuje."""
    legacy = _student()
    StageEntryFactory(stage=elim_stage, participant=legacy)
    anonymise_account(legacy.user)
    Participant.objects.filter(pk=legacy.pk).update(supervisor_email=SUPERVISOR_EMAIL)

    response = web_client.get(reverse("web:supervisor-students"))

    assert legacy.public_code not in response.content.decode()
    assert response.context["rows"] == []


def test_anonymisation_clears_special_needs_but_keeps_the_stay(elim_stage):
    entry = StageEntryFactory(stage=elim_stage)
    ArrivalForm.objects.create(
        entry=entry,
        needs=[LogisticsNeed.ACCOMMODATION, LogisticsNeed.DIET, LogisticsNeed.ACCESSIBILITY],
        note="dieta bezglutenowa",
    )

    anonymise_account(entry.participant.user)

    form = ArrivalForm.objects.get(entry=entry)
    assert form.note == ""
    assert form.needs == [LogisticsNeed.ACCOMMODATION]


def test_anonymisation_erases_workshop_viewer_pseudonyms():
    competition = current_or_default_competition()
    material = make_material(competition)
    viewer = ParticipantFactory().user
    other = ParticipantFactory().user
    record_view(material, viewer)
    record_view(material, other)

    anonymise_account(viewer)

    hashes = set(WorkshopMaterialViewer.objects.values_list("viewer_hash", flat=True))
    assert hashes == {viewer_hash(material.pk, other.pk)}
    material.refresh_from_db()
    assert material.view_count == 2


def test_data_migration_aligns_profiles_anonymised_earlier():
    migration = importlib.import_module("apps.accounts.migrations.0034_clear_anonymised_profile_data")
    competition = current_or_default_competition()
    institution = CustomInstitution.objects.create(competition=competition, name="Placówka")
    legacy = _student(guardian_email="tata@example.test", institution_name="Placówka")
    legacy.user.email = f"deleted-{legacy.user.pk}@invalid.olimpiadakwantowa.pl"
    legacy.user.save(update_fields=["email"])
    Participant.objects.filter(pk=legacy.pk).update(custom_institution_ref=institution)
    alive = _student(guardian_email="rodzic@example.test")

    migration.clear_anonymised_profiles(django_apps, None)

    legacy.refresh_from_db()
    alive.refresh_from_db()
    assert (legacy.guardian_email, legacy.supervisor_email, legacy.institution_name) == ("", "", "")
    assert legacy.custom_institution_ref is None
    assert alive.guardian_email == "rodzic@example.test"
    assert alive.supervisor_email == SUPERVISOR_EMAIL
