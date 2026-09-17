"""Linki w listach wysyłanych **poza** żądaniem (zadania Celery, komendy).

To jest test naprawy istniejącego błędu, a nie nowej funkcji: do tej zmiany ``absolute_url`` bez
żądania sięgało po ``settings.SITE_URL``, którego nie definiuje ani ``config/settings``, ani
``.env.example``, ani compose – więc każde przypomnienie o recenzji, każde powiadomienie
o zamknięciu etapu i każde rozstrzygnięcie reklamacji niosło adres **względny**. W kliencie
pocztowym taki link nie jest linkiem.

Test pilnuje zarazem, że przy **obecnym** żądaniu nic się nie zmieniło: link z rejestracji ma nadal
pochodzić z hosta, pod który przyszło żądanie.
"""

import pytest
from django.test import RequestFactory
from wagtail.models import Site

from apps.accounts.activation import absolute_url, send_activation_email
from apps.accounts.models import User
from apps.tenancy.context import competition_context

from .conftest import HOST_A, make_site


def test_request_wins_over_everything(competition):
    """Zachowanie sprzed zmiany: w żądaniu adres buduje żądanie."""
    request = RequestFactory().get("/", HTTP_HOST=HOST_A)

    assert absolute_url("/me/", request) == f"http://{HOST_A}/me/"


def test_competition_from_context_gives_an_absolute_link(competition):
    with competition_context(competition):
        assert absolute_url("/me/") == f"https://{HOST_A}/me/"


def test_competition_may_be_passed_explicitly(competition, other_competition):
    """Zadanie chodzące po wszystkich konkursach wskazuje konkurs pracy, a nie kontekst."""
    assert absolute_url("/me/", competition=other_competition).startswith(
        f"https://{other_competition.primary_domain}"
    )


def test_without_a_competition_the_default_site_is_used(db):
    """Instalacja jednokonkursowa naprawia się sama – bez nowej zmiennej środowiskowej."""
    Site.objects.filter(is_default_site=True).update(is_default_site=False)
    make_site("sama-witryna.invalid", default=True)

    assert absolute_url("/me/") == "https://sama-witryna.invalid/me/"


def test_scheme_is_https_even_though_the_site_port_is_80(competition):
    """Port witryny to 80, bo TLS kończy się na Caddym – link ``http://`` dostawałby 301."""
    competition.primary_domain = ""
    competition.save(update_fields=["primary_domain"])

    with competition_context(competition):
        assert absolute_url("/me/").startswith("https://")


@pytest.mark.django_db
def test_mail_sent_outside_a_request_carries_a_clickable_link(
    competition, django_capture_on_commit_callbacks, mailoutbox
):
    """Tak wysyła listy zadanie Celery: bez żądania, z konkursem wskazanym kontekstem."""
    user = User.objects.create(email="uczestnik@example.invalid")

    with competition_context(competition):
        with django_capture_on_commit_callbacks(execute=True):
            send_activation_email(user)

    (message,) = mailoutbox
    assert f"https://{HOST_A}/activate/" in message.body
