"""Sekcja „Zaproszenia e-mailem” w panelu koordynatora: formularz, wysyłka, tabela, akcje wiersza.

Sekcja jest wyłącznie dla koordynatora – to on zaprasza do komitetu (PROJEKT.md 2.3), a adresy
zaproszonych są danymi osobowymi, których żadna inna rola w tym serwisie nie ogląda.
"""

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import InvitationCode
from apps.accounts.services import MAX_INVITATION_EMAILS
from apps.accounts.tests.factories import (
    CommitteeMemberFactory,
    InvitationCodeFactory,
    UserFactory,
)
from apps.web.forms import BulkInvitationForm

pytestmark = pytest.mark.django_db

SEND_URL = "/coordinator/invitations/send/"


def form_data(emails: str, **overrides) -> dict:
    data = {"emails": emails, "district": "", "valid_days": 14, "note": ""}
    data.update(overrides)
    return data


def test_formularz_rozbija_liste_usuwa_powtorzenia_i_ignoruje_wielkosc_liter():
    form = BulkInvitationForm(form_data("Ala@example.test, bob@example.test\nALA@example.test"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["emails"] == ["ala@example.test", "bob@example.test"]


def test_formularz_wypisuje_bledne_adresy_i_nie_wysyla_niczego():
    form = BulkInvitationForm(form_data("ala@example.test\nbezmalpy"))

    assert not form.is_valid()
    error = " ".join(form.errors["emails"])
    assert "bezmalpy" in error
    assert "nie wysyłamy żadnego zaproszenia" in error


def test_formularz_odmawia_listy_dluzszej_niz_limit():
    emails = "\n".join(f"os{n}@example.test" for n in range(MAX_INVITATION_EMAILS + 1))

    form = BulkInvitationForm(form_data(emails))

    assert not form.is_valid()
    assert str(MAX_INVITATION_EMAILS) in " ".join(form.errors["emails"])


def test_wyslanie_zaproszen_tworzy_kody_i_melduje_liczbe(
    web_client, coordinator, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            SEND_URL, form_data("ala@example.test\nbob@example.test", district="mazowieckie")
        )

    assert response.status_code == 302
    assert InvitationCode.objects.filter(sent_at__isnull=False).count() == 2
    assert len(mail.outbox) == 2
    content = web_client.get("/coordinator/").content.decode()
    assert "Wysłano 2 zaproszeń." in content
    # Kod jawny nie ma prawa trafić na ekran – jego jedynym egzemplarzem jest list.
    assert "Kod zaproszenia (widoczny tylko teraz" not in content


def test_pominiete_adresy_sa_wypisane_z_powodem(web_client, coordinator, django_capture_on_commit_callbacks):
    member = CommitteeMemberFactory(user=UserFactory(email="recenzent@example.test"))
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        web_client.post(SEND_URL, form_data(f"{member.user.email}\nnowy@example.test"))

    content = web_client.get("/coordinator/").content.decode()
    assert "Pominięto 1 adresów: recenzent@example.test (ma już konto komisji)." in content
    assert "Wysłano 1 zaproszeń." in content


def test_bledny_adres_dociera_do_koordynatora_jako_komunikat(web_client, coordinator):
    web_client.force_login(coordinator)

    response = web_client.post(SEND_URL, form_data("bezmalpy"), follow=True)

    assert "bezmalpy" in response.content.decode()
    assert InvitationCode.objects.count() == 0
    assert mail.outbox == []


def test_tabela_pokazuje_adres_i_stan_zaproszenia(web_client, coordinator):
    now = timezone.now()
    InvitationCodeFactory(email="wyslane@example.test", sent_at=now)
    InvitationCodeFactory(plain_code="kod-2", email="uzyte@example.test", sent_at=now, used_count=1)
    InvitationCodeFactory(plain_code="kod-3", email="uniewaznione@example.test", sent_at=now, revoked_at=now)
    # Kod bez adresu (komenda CLI, sekcja „Kod zaproszenia”) do tej tabeli nie wchodzi.
    InvitationCodeFactory(plain_code="kod-4")
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "Zaproszenia e-mailem" in content
    for email, label in [
        ("wyslane@example.test", "wysłane"),
        ("uzyte@example.test", "użyte"),
        ("uniewaznione@example.test", "unieważnione"),
    ]:
        assert email in content
        assert label in content
    assert content.count("Unieważnij") == 1  # tylko wiersz „wysłane” da się jeszcze unieważnić


def test_przyciski_wiersza_wysylaja_ponownie_i_uniewazniaja(
    web_client, coordinator, django_capture_on_commit_callbacks
):
    invitation = InvitationCodeFactory(email="ala@example.test", sent_at=timezone.now())
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        resend = web_client.post(f"/coordinator/invitations/{invitation.pk}/resend/")

    invitation.refresh_from_db()
    assert resend.status_code == 302
    assert invitation.revoked_at is not None
    fresh = InvitationCode.objects.exclude(pk=invitation.pk).get()
    assert len(mail.outbox) == 1

    revoke = web_client.post(f"/coordinator/invitations/{fresh.pk}/revoke/")

    fresh.refresh_from_db()
    assert revoke.status_code == 302
    assert fresh.revoked_at is not None


def test_uniewaznienie_uzytego_kodu_konczy_sie_komunikatem_a_nie_zmiana(web_client, coordinator):
    invitation = InvitationCodeFactory(email="ala@example.test", sent_at=timezone.now(), used_count=1)
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/invitations/{invitation.pk}/revoke/", follow=True)

    invitation.refresh_from_db()
    assert invitation.revoked_at is None
    assert "zostało już wykorzystane" in response.content.decode()


@pytest.mark.parametrize(
    "url",
    [SEND_URL, "/coordinator/invitations/1/resend/", "/coordinator/invitations/1/revoke/"],
)
def test_inne_role_nie_maja_wstepu(web_client, participant, url):
    web_client.force_login(participant.user)

    response = web_client.post(url, form_data("ala@example.test"))

    assert response.status_code == 403
    assert InvitationCode.objects.count() == 0
