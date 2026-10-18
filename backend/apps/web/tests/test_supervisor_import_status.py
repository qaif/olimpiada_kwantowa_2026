"""Czytelny wynik importu uczniów (REG-UX-01 § 2): statusy w podglądzie i komunikat po zatwierdzeniu."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.tenancy.tests.factories import current_or_default_competition
from apps.web.tests.test_supervisor_import import (
    COORDINATOR_IMPORT_URL,
    HEADER,
    IMPORT_URL,
    STUDENTS_URL,
    SUPERVISOR_EMAIL,
    confirm_from,
    line,
    upload,
)

pytestmark = pytest.mark.django_db

OTHER_TEACHER = "inny.nauczyciel@szkola.test"


@pytest.fixture
def logged_supervisor(web_client):
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    supervisor = SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )
    web_client.force_login(user)
    return supervisor


def messages_after(client, response) -> list[str]:
    page = client.get(response.headers["Location"])
    return [str(message) for message in page.context["messages"]]


def test_uczen_juz_na_liscie_ma_wlasny_status_z_odnosnikiem(web_client, logged_supervisor):
    # Przypadek z produkcji: uczeń wskazał tego nauczyciela sam – wiersz nie może „zniknąć”.
    ParticipantFactory(user=UserFactory(email="kasia@example.test"), supervisor_email=SUPERVISOR_EMAIL)

    response = web_client.post(
        IMPORT_URL, {"file": upload(line("kasia@example.test"), line("ola@example.test"))}
    )

    body = response.content.decode()
    assert "ma już konto i jest na Twojej liście" in body
    assert f'href="{STUDENTS_URL}"' in body
    assert "zaproszenie zostanie wysłane" in body
    assert "Nowe zaproszenia: 1 · już na liście: 1 · inne istniejące konta: 0 · pominięte (błędy): 0." in body


def test_nauczyciel_nie_odroznia_ucznia_z_innym_opiekunem_od_konta_recenzenta(web_client, logged_supervisor):
    ParticipantFactory(user=UserFactory(email="cudzy@example.test"), supervisor_email=OTHER_TEACHER)
    UserFactory(email="recenzent@example.test")

    pages = [
        web_client.post(IMPORT_URL, {"file": upload(line(email))}).content.decode()
        for email in ("cudzy@example.test", "recenzent@example.test")
    ]

    for body in pages:
        assert "ma już konto, ale nie ma go na Twojej liście" in body
        assert "Jeśli startuje w innym konkursie albo to konto innej osoby" in body
        assert "wskazał innego opiekuna</span>" not in body  # status koordynatora
    rows = [page[page.index("<tbody>") : page.index("</tbody>")] for page in pages]
    assert rows[0].replace("cudzy@", "X@") == rows[1].replace("recenzent@", "X@")


def test_bledny_wiersz_ma_status_bledu(web_client, logged_supervisor):
    response = web_client.post(IMPORT_URL, {"file": upload(line("to-nie-adres"))})

    assert "błąd: adres e-mail jest nieprawidłowy" in response.content.decode()


def test_komunikat_po_imporcie_nauczyciela(web_client, logged_supervisor, django_capture_on_commit_callbacks):
    ParticipantFactory(user=UserFactory(email="moj@example.test"), supervisor_email=SUPERVISOR_EMAIL)
    ParticipantFactory(user=UserFactory(email="cudzy@example.test"), supervisor_email=OTHER_TEACHER)
    preview = web_client.post(
        IMPORT_URL,
        {
            "file": upload(
                line("nowy@example.test"),
                line("moj@example.test"),
                line("cudzy@example.test"),
                line("zly-adres"),
            )
        },
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview, skipped=preview.context["preview"].skipped)

    assert messages_after(web_client, response) == [
        "Import zakończony. Wysłano zaproszeń: 1, uczniów już było na Twojej liście: 1, pominięto: 1. "
        "Adresów z kontem spoza Twojej listy: 1 – jeśli to uczniowie tego konkursu, dostają prośbę o zgodę "
        "na dopisanie Cię jako opiekuna i pojawią się na liście po jej przyjęciu."
    ]


def test_komunikat_bez_zajetych_adresow_spoza_listy_jest_krotki(
    web_client, logged_supervisor, django_capture_on_commit_callbacks
):
    preview = web_client.post(IMPORT_URL, {"file": upload(line("nowy@example.test"))})

    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview)

    assert messages_after(web_client, response) == [
        "Import zakończony. Wysłano zaproszeń: 1, uczniów już było na Twojej liście: 0, pominięto: 0."
    ]


def test_koordynator_widzi_pelny_podzial_i_odnosnik_do_konta(
    web_client, coordinator, other_competition, django_capture_on_commit_callbacks
):
    on_list = ParticipantFactory(
        user=UserFactory(email="na-liscie@example.test"), supervisor_email=OTHER_TEACHER
    )
    ParticipantFactory(user=UserFactory(email="inny@example.test"), supervisor_email="ktos@szkola.test")
    ParticipantFactory(user=UserFactory(email="bez@example.test"), supervisor_email="")
    ParticipantFactory(user=UserFactory(email="obcy@example.test"), competition=other_competition)
    web_client.force_login(coordinator)
    header = HEADER + ";e-mail opiekuna szkolnego"
    preview = web_client.post(
        COORDINATOR_IMPORT_URL,
        {
            "file": upload(
                line("na-liscie@example.test") + f";{OTHER_TEACHER}",
                line("inny@example.test") + f";{OTHER_TEACHER}",
                line("bez@example.test") + f";{OTHER_TEACHER}",
                line("obcy@example.test") + f";{OTHER_TEACHER}",
                header=header,
            ),
            "school_custom": "on",
            "school": "Zespół Szkół nr 2",
        },
    )

    body = preview.content.decode()
    assert "ma już konto i jest na liście tego opiekuna" in body
    assert f'href="/coordinator/accounts/{on_list.user_id}/"' in body
    assert "ma już konto, ale wskazał innego opiekuna" in body
    assert "ma już konto, ale nie wskazał opiekuna" in body
    assert "ma konto, ale nie jest uczestnikiem tego konkursu" in body

    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview, url=COORDINATOR_IMPORT_URL)

    assert messages_after(web_client, response) == [
        "Import zakończony. Wysłano zaproszeń: 0, uczniów już było na liście wskazanego opiekuna: 1, "
        "próśb o zgodę na opiekuna: 2, pozostałych kont bez zmian: 1, pominięto: 0."
    ]
