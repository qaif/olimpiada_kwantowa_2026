"""Odnośnik „Materiały z warsztatów” w pasku konta i kafel na pulpicie uczestnika.

Decyzja organizatora z 24.09.2026: odnośnik jest wyłącznie wtedy, gdy prowadzi do czegoś – flaga
``workshop_materials`` włączona **i** choć jeden opublikowany, gotowy materiał. Przedmiotem jest
też koszt: konkurs bez tej funkcji nie pyta ani bazy, ani pamięci podręcznej, a z nią – pyta bazę
raz, a potem czyta z pamięci unieważnianej przy zapisie materiału.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import CompetitionRole, SchoolSupervisor
from apps.accounts.tests.factories import UserFactory
from apps.tenancy.tests.factories import grant_membership
from apps.workshop_materials.availability import cache_key
from apps.workshop_materials.models import MaterialStatus

from .helpers import enable, make_material

pytestmark = pytest.mark.django_db

LINK = 'href="/warsztaty/materialy/"'
CARD = 'id="materialy-z-warsztatow"'
TABLE = "workshop_materials_workshopmaterial"


def page(client, path="/me/") -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()


def queries_touching_materials(client, path="/me/") -> list[str]:
    with CaptureQueriesContext(connection) as captured:
        client.get(path)
    return [query["sql"] for query in captured.captured_queries if TABLE in query["sql"]]


def test_hidden_with_the_flag_off_even_with_published_materials(participant_client, competition):
    make_material(competition)

    content = page(participant_client)

    assert LINK not in content
    assert CARD not in content
    # Konkurs bez tej funkcji nie płaci ani zapytania, ani odczytu z pamięci podręcznej.
    assert queries_touching_materials(participant_client) == []
    assert cache.get(cache_key(competition.pk)) is None


def test_hidden_with_the_flag_on_but_no_materials(participant_client, competition):
    enable(competition)

    content = page(participant_client)

    assert LINK not in content
    assert CARD not in content


@pytest.mark.parametrize(
    ("published", "status"),
    [(False, MaterialStatus.READY), (True, MaterialStatus.SCANNING), (True, MaterialStatus.UPLOADING)],
)
def test_hidden_when_nothing_is_visible_yet(participant_client, competition, published, status):
    enable(competition)
    make_material(competition, kind="file", published=published, status=status)

    assert LINK not in page(participant_client)


def test_visible_in_the_account_bar_and_on_the_dashboard(participant_client, competition):
    enable(competition)
    make_material(competition)

    content = page(participant_client)

    assert LINK in content
    assert CARD in content
    assert "Materiały z warsztatów" in content


def test_visible_in_the_account_bar_of_a_supervisor(client_for, competition):
    enable(competition)
    make_material(competition)
    user = UserFactory(groups=[CompetitionRole.SUPERVISOR.value])
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    # Pasek konta pokazuje opiekuna dopiero z profilem (``supervisor_profile``) – jak cały serwis.
    SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=competition)
    client = client_for(competition)
    client.force_login(user)

    # Strona bez własnych wymagań co do roli – liczy się wyłącznie pasek konta.
    assert LINK in page(client, "/warsztaty/materialy/")


def test_hidden_for_an_account_without_a_role(client_for, competition):
    enable(competition)
    make_material(competition)
    client = client_for(competition)
    client.force_login(UserFactory())

    assert LINK not in page(client, "/account/profile/")


def test_warm_panel_does_not_ask_the_database_again(participant_client, competition):
    enable(competition)
    make_material(competition)
    page(participant_client)  # rozgrzanie: pierwsze żądanie liczy EXISTS i zapisuje wynik

    assert queries_touching_materials(participant_client) == []


def test_publishing_shows_the_link_without_waiting_for_the_cache(participant_client, competition):
    enable(competition)
    material = make_material(competition, published=False)
    assert LINK not in page(participant_client)

    material.is_published = True
    material.save()

    assert LINK in page(participant_client)

    material.delete()

    assert LINK not in page(participant_client)
