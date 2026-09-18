"""Kafle wydania K na pulpicie uczestnika: „Wpisowe” i „Formularz przyjazdu”.

Przedmiotem są cztery rzeczy:

- **pulpit Konkursu #1 nie zmienia się o nic.** Bez flag ``fees`` i ``onsite_logistics`` żaden
  z kafli nie pojawia się na ``/me/`` i nie kosztuje ani jednego zapytania: obie funkcje kontekstu
  wychodzą na pierwszym warunku (``docs/UNIWERSALNY-ETAP-2.md`` § 0.1, § 5.6),
- uczestnik widzi **swoją** należność i tylko ją: kwotę, stan, termin i oznaczenie do przelewu,
- rachunku **nie wydaje sobie sam** – dopóki organizator go nie wydał, adres oddaje 404; po wydaniu
  plik powstaje z **zapamiętanej** wersji szablonu, więc pobrany za rok wyjdzie taki sam,
- formularz przyjazdu pyta o dietę i dostępność **wyłącznie** przy świadomie włączonym zbieraniu
  potrzeb szczególnych (decyzja D21); przy wyłączonym tych pól nie ma na ekranie w ogóle, a serwis
  i tak by ich nie zapisał.

Adresy są w ``apps/web/urls.py`` – patrz docstring ``test_coordinator_fees.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.competitions.logistics import (
    ArrivalForm,
    LogisticsNeed,
    create_venue,
    set_special_needs_collection,
)
from apps.tenancy.documents import DOCUMENTS_FLAG, DocumentKind, set_current_template
from apps.tenancy.fees import (
    FEATURE as FEES_FLAG,
)
from apps.tenancy.fees import (
    assign_fee,
    create_fee_schedule,
    issue_fee_document,
)

pytestmark = pytest.mark.django_db

ME_URL = "/me/"
DOCUMENT_URL = "/me/fees/document/"

LOGISTICS_FLAG = "onsite_logistics"
AMOUNT = Decimal("49.99")


def enable(competition, *flags: str):
    competition.feature_flags = {**(competition.feature_flags or {}), **{flag: True for flag in flags}}
    competition.save(update_fields=["feature_flags"])
    return competition


@pytest.fixture
def participant_client(client_for, competition, participant):
    client = client_for(competition)
    client.force_login(participant.user)
    return client


@pytest.fixture
def fee(competition, edition, participant):
    enable(competition, FEES_FLAG)
    create_fee_schedule(competition, edition=edition, name="Wpisowe 2026", amount=AMOUNT)
    return assign_fee(participant, edition)


# --- Konkurs #1: pulpit bez zmian ------------------------------------------------------------------


def test_dashboard_has_no_new_cards_without_the_flags(participant_client, entry):
    response = participant_client.get(ME_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Wpisowe" not in content
    assert "Formularz przyjazdu" not in content
    assert "fee" not in response.context
    assert "arrival_form" not in response.context


def test_disabled_areas_cost_no_queries(competition, entry):
    """Obie funkcje kontekstu wychodzą na pierwszym warunku – i to jest cała cena Konkursu #1.

    Konkurs jest **argumentem**, a nie wyliczany z profilu: ``participant.competition`` byłoby
    kluczem obcym, czyli zapytaniem – a to jest dokładnie ten koszt, którego budżet ``/me/``
    (``apps/tenancy/tests/test_invariants.py``) nie przepuszcza.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.web.views.participant_fees import fee_card_context
    from apps.web.views.participant_logistics import arrival_card_context

    with CaptureQueriesContext(connection) as captured:
        assert fee_card_context(competition, entry.participant, entry.stage.edition) == {}
        assert arrival_card_context(competition, entry.stage, entry) == {}

    assert len(captured) == 0


# --- kafel „Wpisowe” --------------------------------------------------------------------------------


def test_fee_card_shows_amount_status_and_reference(participant_client, fee, entry):
    content = participant_client.get(ME_URL).content.decode()

    assert "Wpisowe" in content
    assert "do zapłaty" in content
    assert fee.reference in content


def test_fee_card_has_no_download_before_the_document_is_issued(participant_client, fee, entry):
    content = participant_client.get(ME_URL).content.decode()

    assert "Pobierz rachunek" not in content
    assert participant_client.get(DOCUMENT_URL).status_code == 404


def test_issued_document_is_downloadable_by_the_participant(participant_client, competition, fee, entry):
    enable(competition, DOCUMENTS_FLAG)
    set_current_template(
        competition,
        DocumentKind.INVOICE,
        version="1.0 z 1 marca 2027",
        title="Rachunek za wpisowe",
        statement="Do zapłaty {amount} {currency}.",
    )
    from apps.tenancy.documents import render_document

    issue_fee_document(fee, render_document)

    content = participant_client.get(ME_URL).content.decode()
    response = participant_client.get(DOCUMENT_URL)

    assert "Pobierz rachunek" in content
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_document_comes_from_the_remembered_version(participant_client, competition, fee, entry):
    """Poprawka szablonu nie zmienia rachunku, który ktoś ma już w segregatorze."""
    enable(competition, DOCUMENTS_FLAG)
    set_current_template(
        competition,
        DocumentKind.INVOICE,
        version="1.0",
        title="Rachunek za wpisowe",
        statement="Do zapłaty {amount} {currency}.",
    )
    from apps.tenancy.documents import render_document

    issue_fee_document(fee, render_document)
    set_current_template(
        competition,
        DocumentKind.INVOICE,
        version="2.0",
        title="Nowy tytuł",
        statement="Inne zdanie.",
    )

    fee.refresh_from_db()
    assert fee.document_version == "1.0"
    assert participant_client.get(DOCUMENT_URL).status_code == 200


# --- kafel „Formularz przyjazdu” ---------------------------------------------------------------------


@pytest.fixture
def arrival_ready(competition, entry):
    """Konkurs z logistyką, jednym miejscem zawodów i uczestnikiem zapisanym do etapu."""
    enable(competition, LOGISTICS_FLAG)
    return create_venue(competition, name="Wydział Fizyki", city="Kraków")


def test_arrival_card_needs_a_venue(participant_client, competition, entry):
    """Formularz przyjazdu do etapu bez wpisanego miejsca byłby pytaniem bez odpowiedzi."""
    enable(competition, LOGISTICS_FLAG)

    content = participant_client.get(ME_URL).content.decode()

    assert "Formularz przyjazdu" not in content


def test_arrival_card_asks_only_about_ordinary_needs_by_default(participant_client, arrival_ready):
    content = participant_client.get(ME_URL).content.decode()

    assert "Formularz przyjazdu" in content
    assert "nocleg" in content
    assert "dieta szczególna" not in content
    assert "Nie podawaj diagnoz" not in content


def test_arrival_card_asks_about_special_needs_once_the_organiser_decides(
    participant_client, competition, arrival_ready
):
    set_special_needs_collection(competition, enabled=True)

    content = participant_client.get(ME_URL).content.decode()

    assert "dieta szczególna" in content
    assert "Nie podawaj diagnoz" in content


def test_declaration_is_saved(participant_client, arrival_ready, entry):
    response = participant_client.post(
        f"/me/stages/{entry.stage.pk}/arrival/",
        {
            "venue": arrival_ready.pk,
            "arrives_on": "2027-04-10",
            "departs_on": "2027-04-11",
            "needs": [LogisticsNeed.ACCOMMODATION, LogisticsNeed.MEAL],
        },
    )

    declaration = ArrivalForm.objects.get(entry=entry)
    assert response.status_code == 302
    assert declaration.venue_id == arrival_ready.pk
    assert declaration.needs == [LogisticsNeed.ACCOMMODATION, LogisticsNeed.MEAL]


def test_note_is_dropped_while_the_collection_is_off(participant_client, arrival_ready, entry):
    """Druga bramka D21 stoi w serwisie: uwagi wysłanej ręcznie nie ma czego zapisać.

    Pola uwag nie ma na ekranie, więc żeby je wysłać, trzeba złożyć żądanie samodzielnie. Wtedy
    ``save_arrival_form`` zapisuje uwagę jako pustą – i to jest druga, niezależna bramka, a nie
    ta sama reguła powtórzona.
    """
    participant_client.post(
        f"/me/stages/{entry.stage.pk}/arrival/",
        {
            "venue": arrival_ready.pk,
            "needs": [LogisticsNeed.ACCOMMODATION],
            "note": "dieta bezmięsna",
        },
    )

    declaration = ArrivalForm.objects.get(entry=entry)
    assert declaration.note == ""
    assert declaration.needs == [LogisticsNeed.ACCOMMODATION]


def test_special_need_is_not_even_a_valid_choice_while_the_collection_is_off(
    participant_client, arrival_ready, entry
):
    """Pierwsza bramka D21 stoi w formularzu: „dieta” nie jest wtedy wartością do wybrania.

    Deklaracja nie powstaje w ogóle – i tak ma być. Wartość spoza listy pytań tego konkursu jest
    błędem żądania, a nie danymi do cichego przefiltrowania: ciche przyjęcie połowy odpowiedzi
    zostawiałoby uczestnika w przekonaniu, że zgłosił coś, czego nikt nie zobaczy.
    """
    response = participant_client.post(
        f"/me/stages/{entry.stage.pk}/arrival/",
        {"venue": arrival_ready.pk, "needs": [LogisticsNeed.DIET]},
    )

    assert response.status_code == 302
    assert not ArrivalForm.objects.filter(entry=entry).exists()


def test_departure_before_arrival_is_refused(participant_client, arrival_ready, entry):
    participant_client.post(
        f"/me/stages/{entry.stage.pk}/arrival/",
        {"venue": arrival_ready.pk, "arrives_on": "2027-04-11", "departs_on": "2027-04-10"},
    )

    assert not ArrivalForm.objects.filter(entry=entry).exists()


def test_declaration_of_a_foreign_entry_is_404(participant_client, arrival_ready, competition, edition):
    from apps.accounts.tests.factories import ParticipantFactory
    from apps.competitions.tests.factories import StageEntryFactory, StageFactory

    other_stage = StageFactory(edition=edition, kind="DISTRICT")
    foreign_entry = StageEntryFactory(participant=ParticipantFactory(), stage=other_stage)

    response = participant_client.post(f"/me/stages/{other_stage.pk}/arrival/", {"venue": arrival_ready.pk})

    assert response.status_code == 404
    assert not ArrivalForm.objects.filter(entry=foreign_entry).exists()
