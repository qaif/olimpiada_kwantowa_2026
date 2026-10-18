"""Podgląd importu: „Czy chodziło o …?” z „Popraw” / „Tak, adres jest poprawny” (REG-UX-01 § 1.3)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor, User
from apps.accounts.tests.factories import UserFactory
from apps.tenancy.tests.factories import current_or_default_competition
from apps.web.tests.test_supervisor_import import IMPORT_URL, SUPERVISOR_EMAIL, confirm_from, line, upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged_supervisor(web_client):
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    supervisor = SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )
    web_client.force_login(user)
    return supervisor


def _preview(client, *emails):
    return client.post(IMPORT_URL, {"file": upload(*(line(email) for email in emails))})


def test_podglad_pyta_o_literowke_i_domyslnie_proponuje_poprawke(web_client, logged_supervisor):
    response = _preview(web_client, "kasia@gmial.com", "ola@lo1.edu.pl")

    assert response.status_code == 200
    body = response.content.decode()
    assert "Czy chodziło o kasia@gmail.com?" in body
    assert 'form="import-confirm" name="email_fix_2" value="fix" checked' in body
    assert 'name="email_fix_2" value="keep"' in body
    # Domena szkoły nie dostaje podpowiedzi.
    assert "email_fix_3" not in body
    assert 'id="import-confirm"' in body


def test_popraw_zaklada_konto_na_poprawiony_adres(
    web_client, logged_supervisor, mailoutbox, django_capture_on_commit_callbacks
):
    preview = _preview(web_client, "kasia@gmial.com")

    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview, email_fix_2="fix")

    assert response.status_code == 302
    assert User.objects.filter(email="kasia@gmail.com").exists()
    assert not User.objects.filter(email="kasia@gmial.com").exists()
    assert mailoutbox[0].to == ["kasia@gmail.com"]


def test_tak_adres_jest_poprawny_zostawia_adres(
    web_client, logged_supervisor, django_capture_on_commit_callbacks
):
    preview = _preview(web_client, "kasia@gmial.com")

    with django_capture_on_commit_callbacks(execute=True):
        confirm_from(web_client, preview, email_fix_2="keep")

    assert User.objects.filter(email="kasia@gmial.com").exists()
    assert not User.objects.filter(email="kasia@gmail.com").exists()


def test_poprawka_na_adres_z_kontem_nie_zaklada_duplikatu(
    web_client, logged_supervisor, django_capture_on_commit_callbacks
):
    # Właśnie ten przypadek z produkcji: literówka dawała drugie konto tej samej osoby.
    User.objects.create_user(email="kasia@gmail.com", password=None)
    preview = _preview(web_client, "kasia@gmial.com")

    with django_capture_on_commit_callbacks(execute=True):
        confirm_from(web_client, preview, email_fix_2="fix")

    assert User.objects.filter(email__in=["kasia@gmail.com", "kasia@gmial.com"]).count() == 1


def test_numer_wiersza_spoza_koszyka_niczego_nie_zmienia(
    web_client, logged_supervisor, django_capture_on_commit_callbacks
):
    preview = _preview(web_client, "kasia@wp.pl")

    with django_capture_on_commit_callbacks(execute=True):
        confirm_from(web_client, preview, email_fix_2="fix", email_fix_x="fix")

    assert User.objects.filter(email="kasia@wp.pl").exists()
