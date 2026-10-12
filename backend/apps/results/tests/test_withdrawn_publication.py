"""Wycofane ogłoszenie wyników – audyt bezpieczeństwa 10.10.2026, S2.

Wycofanie to wyczyszczenie ``Stage.results_published_at``; rekord ``ResultsPublication`` zostaje
jako ślad. Każda ścieżka, która coś **pokazuje** na podstawie publikacji, ma od tej chwili mówić
404 / „brak”, tak jak przed pierwszą publikacją. Jedna definicja: ``ResultsPublication.objects.live()``.
"""

import pytest
from django.test import Client

from apps.competitions.models import Stage
from apps.results import statistics as stats
from apps.results.feedback import participant_feedback
from apps.results.models import Anonymization, ResultsPublication
from apps.results.services import publish_results, published_results, results_for_participant

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db


@pytest.fixture
def published():
    """Etap z ogłoszoną tabelą i jednym uczestnikiem z oceną."""
    stage = make_stage(problems=1)
    entry = graded_entry(stage, [6])
    publish_results(stage, None, Anonymization.CODE)
    stage.refresh_from_db()
    return stage, entry


def _withdraw(stage):
    """Wycofanie tak, jak robi je koordynator: zapis etapu z wyczyszczonym znacznikiem."""
    stage.results_published_at = None
    stage.save(update_fields=["results_published_at"])


def test_live_excludes_a_withdrawn_publication(published):
    stage, _ = published
    assert ResultsPublication.objects.live().filter(stage=stage).exists()

    _withdraw(stage)

    assert ResultsPublication.objects.filter(stage=stage).exists(), "rekord zostaje jako ślad"
    assert not ResultsPublication.objects.live().filter(stage=stage).exists()
    assert published_results(stage.pk) is None


def test_public_api_is_404_after_withdrawal():
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    publish_results(stage, None, Anonymization.CODE)
    client = Client()
    assert client.get(f"/api/public/results/{stage.pk}/").status_code == 200

    _withdraw(Stage.objects.get(pk=stage.pk))

    assert client.get(f"/api/public/results/{stage.pk}/").status_code == 404


def test_public_page_is_404_after_withdrawal(published):
    stage, _ = published
    client = Client()
    assert client.get(f"/results/{stage.pk}/").status_code == 200

    _withdraw(stage)

    assert client.get(f"/results/{stage.pk}/").status_code == 404


def test_participant_feedback_and_my_results_disappear_after_withdrawal(published):
    stage, entry = published
    participant = entry.participant
    assert participant_feedback(participant, stage) is not None

    _withdraw(stage)

    assert participant_feedback(participant, stage) is None
    assert results_for_participant(participant.user, stage.edition.competition) == []


def test_statistics_drop_a_withdrawn_stage_and_the_cache_is_invalidated(
    published, django_capture_on_commit_callbacks
):
    stage, _ = published
    stats.invalidate()
    assert [row["stage_id"] for row in stats.statistics()] == [stage.pk]

    # Odbiornik zapisu etapu zrzuca wpis po commicie – bez tego wycofana tabela wisiałaby
    # w statystykach jeszcze przez dziesięć minut życia wpisu.
    with django_capture_on_commit_callbacks(execute=True):
        _withdraw(stage)

    assert stats.statistics() == []
    assert stats.published_stages() == []
    content = Client().get("/statystyki/").content.decode()
    assert f"/results/{stage.pk}/" not in content


def test_statistics_page_filters_a_withdrawn_stage_even_from_a_stale_cache(published):
    """Wpis w pamięci sprzed wycofania nie może przepchnąć etapu na stronę (filtr przy wejściu)."""
    stage, _ = published
    stats.invalidate()
    stats.statistics()  # wpis z etapem jeszcze ogłoszonym

    # ``update()`` omija sygnały – symuluje wpis, którego nikt nie zdążył zrzucić.
    Stage.objects.filter(pk=stage.pk).update(results_published_at=None)

    content = Client().get("/statystyki/").content.decode()
    assert f"/results/{stage.pk}/" not in content
    stats.invalidate()
