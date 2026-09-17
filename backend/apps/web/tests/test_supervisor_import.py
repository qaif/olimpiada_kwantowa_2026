"""Ekrany importu listy uczniów: podgląd, zatwierdzenie, zaproszenie i jego przyjęcie.

Testy jednostkowe serwisu stoją w ``apps/accounts/tests/test_bulk_registration.py``; tutaj
przedmiotem jest droga przez HTTP – kto ma dostęp do których adresów, czy podgląd naprawdę nic
nie zapisuje i czy uczeń jest w stanie uruchomić konto samym linkiem z listu.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.bulk_registration import (
    import_students,
    make_invite_token,
    preview_upload,
    unpack_rows,
)
from apps.accounts.models import GROUP_SUPERVISOR, Participant, SchoolSupervisor, User, Voivodeship
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.web.tests.conftest import WEB_TEST_PASSWORD

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"
ADULT_YEAR = timezone.localdate().year - 25
MINOR_YEAR = timezone.localdate().year - 16

HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"

IMPORT_URL = "/supervisor/import/"
STUDENTS_URL = "/supervisor/students/"
COORDINATOR_IMPORT_URL = "/coordinator/accounts/import/"


class _MemoryUpload:
    """Namiastka wgranego pliku dla wywołań serwisowych: ``name``, ``size`` i ``read``."""

    name = "klasa.csv"

    def __init__(self, data: bytes):
        self._data = data
        self.size = len(data)

    def read(self) -> bytes:
        return self._data


def upload(*rows: str, header: str = HEADER, name: str = "klasa.csv") -> SimpleUploadedFile:
    body = "\n".join([header, *rows]).encode("utf-8")
    return SimpleUploadedFile(name, body, content_type="text/csv")


def line(email: str, *, first: str = "Kasia", last: str = "Nowak", year: int = ADULT_YEAR, grade: int = 2):
    return f"{first};{last};{email};{year};{grade};;"


@pytest.fixture
def supervisor():
    """Konto opiekuna gotowe do pracy – tak wygląda profil po aktywacji adresu."""
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(user=user, school="XIV LO")


@pytest.fixture
def logged_supervisor(web_client, supervisor):
    web_client.force_login(supervisor.user)
    return supervisor


def do_preview(client, url: str = IMPORT_URL, **extra):
    return client.post(url, {"file": upload(line("kasia@example.test")), **extra})


def confirm_from(client, preview_response, url: str = IMPORT_URL, **overrides):
    """Zatwierdza dokładnie ten koszyk, który przyszedł z podglądu – tak jak robi to przeglądarka."""
    context = preview_response.context
    data = {
        "rows": context["preview"].token,
        "school": context["school_name"],
        **overrides,
    }
    if context.get("school_id"):
        data.setdefault("school_id", context["school_id"])
    return client.post(url, data)


# --- dostęp ------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [IMPORT_URL, STUDENTS_URL])
def test_ekrany_opiekuna_sa_zamkniete_dla_niezalogowanych(web_client, url):
    response = web_client.get(url)
    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]


@pytest.mark.parametrize("url", [IMPORT_URL, STUDENTS_URL])
def test_uczestnik_nie_wejdzie_na_ekrany_opiekuna(web_client, participant, url):
    web_client.force_login(participant.user)
    assert web_client.get(url).status_code == 403


def test_import_koordynatora_jest_zamkniety_dla_opiekuna(web_client, logged_supervisor):
    """Dwa importy to dwie role. Opiekun nie dostaje kolumny „e-mail opiekuna szkolnego”,
    bo przypisywałby uczniów komuś innemu."""
    assert web_client.get(COORDINATOR_IMPORT_URL).status_code == 403


def test_import_koordynatora_dziala_dla_koordynatora(web_client, coordinator):
    web_client.force_login(coordinator)
    response = web_client.get(COORDINATOR_IMPORT_URL)
    assert response.status_code == 200
    labels = [column.label for column in response.context["columns"]]
    assert "e-mail opiekuna szkolnego" in labels


# --- podgląd i zatwierdzenie -------------------------------------------------------------------


def test_podglad_niczego_nie_zapisuje_i_nie_wysyla(web_client, logged_supervisor, mailoutbox):
    """Cały sens tego ekranu: pokazać skutki, zanim staną się nieodwracalne.

    Listu nie da się cofnąć, a plik z arkusza szkolnego zawiera literówki – dlatego między
    wgraniem a zapisem stoi strona, której zamknięcie nie zostawia w bazie ani jednego wiersza.
    """
    response = do_preview(web_client)
    assert response.status_code == 200
    assert response.context["preview"].to_create == 1
    assert User.objects.filter(email="kasia@example.test").exists() is False
    assert mailoutbox == []


def test_zatwierdzenie_zaklada_konta_i_wysyla_zaproszenia(
    web_client, logged_supervisor, mailoutbox, django_capture_on_commit_callbacks
):
    preview_response = do_preview(web_client)
    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview_response)
    assert response.status_code == 302
    assert response.headers["Location"] == STUDENTS_URL
    participant = Participant.objects.select_related("user").get(user__email="kasia@example.test")
    # Szkoła wchodzi z profilu opiekuna: nauczyciel importuje własną klasę i nie ma go po co
    # pytać drugi raz o to, co podał przy rejestracji.
    assert participant.school == "XIV LO"
    assert participant.supervisor_email == SUPERVISOR_EMAIL
    assert participant.user.is_active is False
    assert len(mailoutbox) == 1


def test_podmieniony_koszyk_nie_przechodzi(web_client, logged_supervisor):
    """Podpis pilnuje, że zatwierdzamy tę listę, którą pokazał podgląd."""
    preview_response = do_preview(web_client)
    response = confirm_from(web_client, preview_response, rows="podrobiony-koszyk")
    assert response.status_code == 302
    assert User.objects.filter(email="kasia@example.test").exists() is False


def test_plik_bez_wymaganej_kolumny_wraca_z_bledem_a_nie_z_bledem_500(web_client, logged_supervisor):
    response = web_client.post(IMPORT_URL, {"file": upload(header="imię;nazwisko")})
    assert response.status_code == 400
    assert "e-mail" in response.content.decode()


def test_koordynator_rozdziela_uczniow_miedzy_opiekunow_kolumna_z_pliku(
    web_client, coordinator, django_capture_on_commit_callbacks
):
    """Kolumna „e-mail opiekuna szkolnego” istnieje po to, żeby jedno wgranie obsłużyło całą szkołę."""
    web_client.force_login(coordinator)
    header = HEADER + ";e-mail opiekuna szkolnego"
    rows = [
        line("uczen1@example.test") + ";a.nauczyciel@szkola.test",
        line("uczen2@example.test") + ";b.nauczyciel@szkola.test",
    ]
    preview_response = web_client.post(
        COORDINATOR_IMPORT_URL,
        {
            "file": upload(*rows, header=header),
            "school_custom": "on",
            "school": "Zespół Szkół nr 2",
        },
    )
    assert preview_response.status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = confirm_from(web_client, preview_response, url=COORDINATOR_IMPORT_URL)
    assert response.status_code == 302
    assert (
        Participant.objects.get(user__email="uczen1@example.test").supervisor_email
        == "a.nauczyciel@szkola.test"
    )
    assert (
        Participant.objects.get(user__email="uczen2@example.test").supervisor_email
        == "b.nauczyciel@szkola.test"
    )


def test_koordynator_musi_wskazac_szkole(web_client, coordinator):
    """Organizator nie ma „własnej” szkoły, więc domyślenie się jej byłoby zgadywaniem."""
    web_client.force_login(coordinator)
    response = web_client.post(COORDINATOR_IMPORT_URL, {"file": upload(line("a@example.test"))})
    assert response.status_code == 400
    assert User.objects.filter(email="a@example.test").exists() is False


# --- lista uczniów i ponowne zaproszenie -------------------------------------------------------


def invited(supervisor, email: str = "kasia@example.test", *, year: int = ADULT_YEAR) -> Participant:
    """Uczeń zaproszony importem – drogą serwisową, bo przedmiotem testu jest to, co **po** niej.

    Przez formularz przechodzimy w testach podglądu i zatwierdzenia wyżej; tutaj interesuje nas
    ekran ucznia, a nie kolejne wgranie pliku.
    """
    result = preview_upload(
        _MemoryUpload("\n".join([HEADER, line(email, year=year)]).encode("utf-8")),
        with_supervisor=False,
        default_supervisor_email=SUPERVISOR_EMAIL,
    )
    import_students(
        unpack_rows(result.token),
        school_name="XIV LO",
        default_supervisor_email=SUPERVISOR_EMAIL,
        actor=supervisor.user,
    )
    return Participant.objects.select_related("user").get(user__email=email)


def test_lista_kont_rozroznia_zaproszonych_aktywnych_i_zapisanych_samodzielnie(web_client, logged_supervisor):
    """Trzy stany, a nie dwa: „zapisał się sam” nie jest „aktywnym zaproszeniem”."""
    invited(logged_supervisor)
    ParticipantFactory(user=UserFactory(email="sam@example.test"), supervisor_email=SUPERVISOR_EMAIL)
    response = web_client.get(STUDENTS_URL)
    assert response.status_code == 200
    states = {row["participant"].user.email: row["state"] for row in response.context["rows"]}
    assert states["kasia@example.test"] == "invited"
    assert states["sam@example.test"] == "self"
    assert response.context["invited_count"] == 1


def test_opiekun_wysyla_zaproszenie_ponownie_tylko_swojemu_uczniowi(
    web_client, logged_supervisor, mailoutbox, django_capture_on_commit_callbacks
):
    """Uprawnienie pochodzi z adresu w profilu ucznia – nie ze szkoły i nie z tego, kto wgrał plik."""
    mine = invited(logged_supervisor)
    stranger = ParticipantFactory(
        user=UserFactory(email="obcy@example.test"), supervisor_email="inny@szkola.test"
    )
    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(reverse("web:supervisor-resend-invitation", args=[mine.pk]))
    assert response.status_code == 302
    assert len(mailoutbox) == 1
    assert web_client.post(reverse("web:supervisor-resend-invitation", args=[stranger.pk])).status_code == 404


# --- przyjęcie zaproszenia przez ucznia ---------------------------------------------------------


def accept_url(participant: Participant) -> str:
    return reverse("web:student-invite", args=[make_invite_token(participant)])


def accept_payload(**overrides) -> dict:
    data = {
        "password": WEB_TEST_PASSWORD,
        "password2": WEB_TEST_PASSWORD,
        "phone": "600 100 200",
        "district": Voivodeship.MAZOWIECKIE,
        "terms_consent": "on",
        "gdpr_consent": "on",
    }
    data.update(overrides)
    return data


def test_uczen_otwiera_zaproszenie_bez_logowania_i_widzi_swoje_dane(web_client, supervisor):
    participant = invited(supervisor)
    response = web_client.get(accept_url(participant))
    assert response.status_code == 200
    body = response.content.decode()
    assert "kasia@example.test" in body
    assert "XIV LO" in body


def test_przyjecie_zaproszenia_uruchamia_konto_i_pozwala_sie_zalogowac(web_client, supervisor):
    """Koniec przepływu sprawdzony skutkiem, a nie stanem pola: uczeń faktycznie się loguje."""
    participant = invited(supervisor)
    response = web_client.post(accept_url(participant), accept_payload())
    assert response.status_code == 302
    assert response.headers["Location"] == "/zaproszenie/dziekujemy/"
    assert web_client.login(username="kasia@example.test", password=WEB_TEST_PASSWORD) is True


def test_bez_zgod_konto_zostaje_nieaktywne_a_formularz_wraca_z_bledem(web_client, supervisor):
    participant = invited(supervisor)
    response = web_client.post(accept_url(participant), accept_payload(terms_consent=""))
    assert response.status_code == 400
    participant.refresh_from_db()
    assert participant.user.is_active is False


def test_zuzyty_link_nie_otwiera_konta_drugi_raz(web_client, supervisor):
    """Po uruchomieniu konta link przestaje działać – i mówi to tym samym zdaniem, co link wygasły."""
    participant = invited(supervisor)
    url = accept_url(participant)
    web_client.post(url, accept_payload())
    response = web_client.get(url)
    assert response.status_code == 400
    assert "nie działa" in response.content.decode()


def test_niepelnoletni_bez_zgody_opiekuna_nie_uruchomi_konta(web_client, supervisor):
    """Reguła wieku jest ta sama, co w rejestracji – rocznik przyszedł z listy klasowej."""
    participant = invited(supervisor, "mlody@example.test", year=MINOR_YEAR)
    response = web_client.post(accept_url(participant), accept_payload())
    assert response.status_code == 400
    participant.refresh_from_db()
    assert participant.user.is_active is False


def test_koordynator_widzi_odznake_z_importu_na_liscie_kont(web_client, coordinator, supervisor):
    """Odznaka odpowiada na pytanie „skąd dwadzieścia kont z jednej szkoły w jednej minucie”."""
    invited(supervisor)
    web_client.force_login(coordinator)
    response = web_client.get("/coordinator/accounts/?q=kasia@example.test")
    assert response.status_code == 200
    assert "z importu" in response.content.decode()
