"""Ukrycie roli opiekuna szkolnego: przełącznik ``SiteSettings.supervisor_registration_enabled``.

Zgłoszenie organizatora brzmiało „rejestracja nauczycieli ma być ukryta”. Ukrycie ma tu znaczyć
**trzy rzeczy naraz**, bo każda z osobna byłaby pozorna: adres rejestracji nie istnieje, nigdzie
nie ma do niego odnośnika, a uczestnik nie widzi w profilu pola, które pyta o adres nauczyciela
niemogącego założyć konta. Przełącznik jest domyślnie wyłączony, więc **stan domyślny** tych
testów to stan ukryty — włączenie jest tym, co trzeba w teście zrobić jawnie.

Czego przełącznik nie rusza i co też jest tu sprawdzone: opiekunowie, którzy konto już mają,
zachowują swój panel. Ukrycie drogi wejścia nie jest tym samym, co odebranie komuś dostępu do
danych, które już ogląda.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor, User
from apps.accounts.supervisors import registration_enabled
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.web.tests.conftest import captcha_fields, password_fields

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/supervisor/"

#: Strony, na których organizator nie chce widzieć drogi do rejestracji nauczyciela. Lista jest
#: jawna, a nie „co komu przyjdzie do głowy”: to ona jest kontraktem, którego pilnuje test.
PUBLIC_PAGES = ("/login/", "/register/", "/register/committee/")


def register_payload(email: str = "nowy.opiekun@szkola.test") -> dict:
    return {
        "email": email,
        "first_name": "Jan",
        "last_name": "Nauczyciel",
        "school": "Zespół Szkół nr 2",
        **password_fields(),
        **captcha_fields(),
    }


# --- stan domyślny: rola ukryta ----------------------------------------------------------------


def test_domyslnie_rola_opiekuna_jest_wylaczona():
    """Domyślną odpowiedzią przełącznika, który **ukrywa** funkcję, musi być jej ukrycie."""
    assert registration_enabled() is False


def test_adres_rejestracji_daje_404_na_get_i_na_post(web_client):
    """404, a nie 403 ani strona „funkcja wyłączona”: z zewnątrz ten adres ma nie istnieć.

    POST jest sprawdzony osobno, bo bramka postawiona tylko na widoku ``GET`` zostawiłaby
    zakładaniu kont własną, nieogrodzoną drogę.
    """
    assert web_client.get(REGISTER_URL).status_code == 404
    assert web_client.post(REGISTER_URL, register_payload()).status_code == 404
    assert User.objects.filter(email="nowy.opiekun@szkola.test").exists() is False


@pytest.mark.parametrize("url", PUBLIC_PAGES)
def test_zadna_strona_publiczna_nie_prowadzi_do_rejestracji_opiekuna(web_client, url):
    response = web_client.get(url)
    assert response.status_code == 200
    assert REGISTER_URL not in response.content.decode()


def test_uczestnik_nie_widzi_w_profilu_pola_adresu_opiekuna(web_client, participant):
    """Pole jest **zdjęte z formularza**, a nie ukryte w szablonie – ukrycie w HTML-u byłoby pozorne.

    Sprawdzamy oba skutki: czego nie widać na ekranie i czego nie da się wysłać POST-em.
    """
    web_client.force_login(participant.user)
    body = web_client.get(reverse("web:profile")).content.decode()
    assert "supervisor_email" not in body
    assert "opiekuna szkolnego" not in body

    response = web_client.post(
        reverse("web:profile"),
        {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "phone": "600 100 200",
            "district": participant.district,
            "grade": participant.grade,
            "birth_year": participant.birth_year,
            "school": participant.school,
            "school_custom": "on",
            "supervisor_email": "nauczyciel@szkola.test",
        },
    )
    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.supervisor_email == ""


def test_wylaczenie_przelacznika_nie_kasuje_adresu_wpisanego_wcześniej(web_client):
    """Decyzja ucznia sprzed ukrycia roli zostaje – formularz danych nie cofa jej bez jego wiedzy."""
    participant = ParticipantFactory(
        user=UserFactory(email="uczen@example.test", groups=["participant"]),
        supervisor_email="nauczyciel@szkola.test",
    )
    web_client.force_login(participant.user)
    web_client.post(
        reverse("web:profile"),
        {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "phone": "600 100 200",
            "district": participant.district,
            "grade": participant.grade,
            "birth_year": participant.birth_year,
            "school": participant.school,
            "school_custom": "on",
        },
    )
    participant.refresh_from_db()
    assert participant.supervisor_email == "nauczyciel@szkola.test"


def test_istniejacy_opiekun_zachowuje_swoj_panel(web_client):
    """Konto powstało świadomie; ukrycie drogi wejścia nie odbiera dostępu do własnych danych."""
    user = UserFactory(email="nauczyciel@szkola.test")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    SchoolSupervisor.objects.create(user=user, school="XIV LO")
    web_client.force_login(user)

    assert web_client.get("/supervisor/").status_code == 200
    assert web_client.get("/supervisor/students/").status_code == 200
    assert web_client.get("/supervisor/import/").status_code == 200


def test_import_koordynatora_dziala_niezaleznie_od_przelacznika(web_client, coordinator):
    """Hurtowe zapraszanie uczniów jest narzędziem organizatora, a nie częścią roli nauczyciela."""
    web_client.force_login(coordinator)
    assert web_client.get("/coordinator/accounts/import/").status_code == 200


# --- stan włączony: wszystko jak dotąd ----------------------------------------------------------


def test_po_wlaczeniu_rejestracja_dziala_jak_dotad(web_client, supervisor_registration_on):
    assert registration_enabled() is True
    assert web_client.get(REGISTER_URL).status_code == 200

    response = web_client.post(REGISTER_URL, register_payload())
    assert response.status_code == 302
    user = User.objects.get(email="nowy.opiekun@szkola.test")
    assert user.groups.filter(name=GROUP_SUPERVISOR).exists()
    assert user.is_active is False


def test_po_wlaczeniu_uczestnik_znow_widzi_pole_adresu_opiekuna(
    web_client, participant, supervisor_registration_on
):
    web_client.force_login(participant.user)
    body = web_client.get(reverse("web:profile")).content.decode()
    assert "supervisor_email" in body
