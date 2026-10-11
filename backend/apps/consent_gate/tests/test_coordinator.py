"""Koordynator (CONS-01 § 5): kafelek na pulpicie, CSV braków, komenda dla operatora.

Liczy się izolacja (cudzy konkurs nie wchodzi ani do liczby, ani do pliku) i to, że plik niesie
wyłącznie dane, które koordynator już widzi na liście kont.
"""

from __future__ import annotations

import csv
import io

import pytest
from django.core.management import call_command

from apps.accounts.consents import ConsentKind
from apps.accounts.models import CompetitionRole
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog

from .conftest import give, make_adult

pytestmark = pytest.mark.django_db

EXPORT = "/coordinator/consents/missing.csv"


@pytest.fixture
def coordinator(competition):
    user = UserFactory()
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)
    return user


@pytest.fixture
def population(competition, other_competition):
    complete = make_adult(competition=competition)
    give(complete)
    missing = make_adult(competition=competition)
    give(missing, {ConsentKind.PRIVACY})
    inactive = make_adult(competition=competition)
    inactive.user.is_active = False
    inactive.user.save(update_fields=["is_active"])
    foreign = make_adult(competition=other_competition)
    return {"complete": complete, "missing": missing, "inactive": inactive, "foreign": foreign}


def _csv(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(body), delimiter=";"))


def test_dashboard_shows_the_count_and_the_export_link(web, coordinator, population):
    web.force_login(coordinator)

    html = web.get("/coordinator/").content.decode()

    assert 'id="brakujace-zgody"' in html
    assert f'href="{EXPORT}"' in html
    assert '<span class="attention__value">1</span>' in html


def test_export_lists_only_this_competitions_active_participants_with_gaps(web, coordinator, population):
    web.force_login(coordinator)

    response = web.get(EXPORT)

    assert response.status_code == 200
    rows = _csv(response)
    header, *body = rows
    assert header == ["Kod uczestnika", "Imię", "Nazwisko", "E-mail", "Brakujące zgody"]
    missing = population["missing"]
    assert [row[0] for row in body] == [missing.public_code]
    assert body[0][3] == missing.user.email
    assert "akceptacja regulaminu" in body[0][4]
    entry = AuditLog.objects.get(action="consent_gate.exported")
    assert entry.actor == coordinator
    assert entry.diff == {"rows": 1}


def test_export_is_for_the_coordinator_only(web, population):
    web.force_login(population["missing"].user)

    assert web.get(EXPORT).status_code == 403


def test_command_reports_counts_without_personal_data(population):
    out = io.StringIO()

    call_command("consent_gate_report", stdout=out)

    text = out.getvalue()
    assert "kwantowa: 1 uczestników z brakującymi zgodami" in text
    assert "TERMS" in text
    assert population["missing"].user.email not in text
    assert population["missing"].public_code not in text
