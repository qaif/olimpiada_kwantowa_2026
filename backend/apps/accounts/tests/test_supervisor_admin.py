"""``/admin/`` dla opiekunów szkolnych: wgląd operatora w dowody zgód (M1, 22.09.2026).

Panel koordynatora (``/coordinator/accounts/?role=supervisor``) jest drogą samoobsługową dla
organizatora; ten ekran jest dla operatora platformy, który dostaje pytanie „na co i kiedy ten
nauczyciel się zgodził” i nie ma po co odpowiadać na nie SQL-em w konsoli.

Test jest smoke-testem HTTP, a nie testem reguły domenowej – żadnej tu nie ma (patrz docstring
``SchoolSupervisorAdmin``). To, czego test naprawdę pilnuje: że ``ConsentRecordInline`` pod tym
adminem wskazuje właściwy klucz obcy (``supervisor``, nie ``participant``) – błąd w ``fk_name``
wywraca stronę zmiany wyjątkiem, a nie cichym brakiem wierszy, więc 200 na obu adresach jest
dowodem, że model dobrze się skonfigurował.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.consents import ConsentKind
from apps.accounts.models import ConsentRecord, SchoolSupervisor, User
from apps.accounts.tests.factories import UserFactory
from apps.tenancy.tests.factories import current_or_default_competition

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client() -> Client:
    User.objects.filter(email="operator@example.test").delete()
    User.objects.create_superuser(email="operator@example.test", password="Poprawne-Haslo-2026")
    client = Client()
    client.login(username="operator@example.test", password="Poprawne-Haslo-2026")
    return client


@pytest.fixture
def supervisor():
    user = UserFactory(email="admin.opiekun@szkola.test")
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


def test_the_changelist_answers(admin_client, supervisor):
    response = admin_client.get(reverse("admin:accounts_schoolsupervisor_changelist"))

    assert response.status_code == 200
    assert supervisor.user.email in response.content.decode()


def test_the_change_page_shows_the_consent_inline(admin_client, supervisor):
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.TERMS, document_version="1.0", source="web"
    )

    response = admin_client.get(reverse("admin:accounts_schoolsupervisor_change", args=[supervisor.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "akceptacja regulaminu" in body


def test_the_supervisor_inline_never_shows_a_participants_consent(admin_client, supervisor):
    """Ograniczenie po ``fk_name`` znaczy: cudza zgoda (uczestnika) nie wycieka do tego ekranu."""
    from apps.accounts.tests.factories import ParticipantFactory

    participant = ParticipantFactory()
    ConsentRecord.objects.create(
        participant=participant, kind=ConsentKind.PRIVACY, document_version="own-1.0", source="web"
    )

    response = admin_client.get(reverse("admin:accounts_schoolsupervisor_change", args=[supervisor.pk]))

    assert "own-1.0" not in response.content.decode()
