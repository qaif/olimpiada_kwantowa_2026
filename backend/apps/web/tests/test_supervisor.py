"""Panel opiekuna szkolnego: skąd bierze się uprawnienie i czego opiekun nie zobaczy.

Uprawnienie opiekuna pochodzi **od ucznia** – z adresu wpisanego w jego profilu – a nie od
organizatora. Testy pilnują obu stron tej reguły: że lista pokazuje dokładnie tych uczniów,
którzy wskazali ten adres, i że nie da się przez nią dojść do punktów przed publikacją ani do
kogokolwiek innego.
"""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.models import GROUP_SUPERVISOR, SchoolParticipation, SchoolSupervisor, User
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.results.models import Anonymization
from apps.results.services import publish_results
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import current_or_default_competition
from apps.web.tests.conftest import (
    WEB_TEST_PASSWORD,
    captcha_fields,
    close_stage_timeline,
    password_fields,
)

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"


@pytest.fixture
def supervisor():
    """Konto opiekuna gotowe do logowania – tak wygląda profil po aktywacji adresu."""
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


@pytest.fixture
def logged_supervisor(web_client, supervisor):
    web_client.force_login(supervisor.user)
    return supervisor


def student(email: str = SUPERVISOR_EMAIL, **kwargs):
    return ParticipantFactory(supervisor_email=email, **kwargs)


def test_rejestracja_zaklada_konto_nieaktywne_w_grupie_opiekunow(web_client, supervisor_registration_on):
    """Konto czeka na link aktywacyjny – tak samo jak uczestnik i komitet.

    Fixture włącza przełącznik ``SiteSettings.supervisor_registration_enabled``: od zgłoszenia
    organizatora („rejestracja nauczycieli ma być ukryta”) adres istnieje wyłącznie wtedy, gdy
    ta rola jest w serwisie oferowana. Ukrycia pilnuje ``test_supervisor_registration_flag``.
    """
    response = web_client.post(
        reverse("web:register-supervisor"),
        {
            "email": "nowy.opiekun@szkola.test",
            "first_name": "Jan",
            "last_name": "Nauczyciel",
            "school": "Zespół Szkół nr 2",
            **password_fields(),
            **captcha_fields(),
        },
    )

    assert response.status_code == 302
    user = User.objects.get(email="nowy.opiekun@szkola.test")
    assert user.is_active is False
    assert user.email_verified_at is None
    assert user.groups.filter(name=GROUP_SUPERVISOR).exists()
    assert user.school_supervisor.school == "Zespół Szkół nr 2"


def test_panel_pokazuje_tylko_uczniow_ktorzy_wskazali_ten_adres(web_client, logged_supervisor, elim_stage):
    """Lista jest skutkiem decyzji ucznia – nikt inny na nią nie wchodzi."""
    mine = student()
    StageEntryFactory(stage=elim_stage, participant=mine)
    someone_else = student(email="ktos.inny@szkola.test")
    StageEntryFactory(stage=elim_stage, participant=someone_else)
    without_supervisor = student(email="")
    StageEntryFactory(stage=elim_stage, participant=without_supervisor)

    response = web_client.get(reverse("web:supervisor"))

    body = response.content.decode()
    assert response.status_code == 200
    assert mine.public_code in body
    assert someone_else.public_code not in body
    assert without_supervisor.public_code not in body


def test_adres_dopasowuje_sie_niezaleznie_od_wielkosci_liter(web_client, logged_supervisor, elim_stage):
    """Uczeń przepisuje adres ze słuchu albo z tablicy – to ta sama skrzynka."""
    mine = student(email=SUPERVISOR_EMAIL.upper())
    StageEntryFactory(stage=elim_stage, participant=mine)

    response = web_client.get(reverse("web:supervisor"))

    assert mine.public_code in response.content.decode()


def test_panel_nie_pokazuje_punktow_przed_publikacja(web_client, logged_supervisor, elim_stage, problems):
    """Ścieżka statusu tak, liczba punktów dopiero wtedy, gdy jest publiczna."""
    mine = student()
    entry = StageEntryFactory(stage=elim_stage, participant=mine)
    entry.total_points = 42
    entry.save(update_fields=["total_points"])
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.IN_REVIEW)

    body = web_client.get(reverse("web:supervisor")).content.decode()

    assert "w ocenie" in body
    # Nie szukamy samej liczby (trafiłaby w losowy token CSRF), tylko wiersza, który ją podpisuje.
    assert "Suma punktów" not in body
    assert "Wyniki ogłoszone" not in body


def test_po_publikacji_panel_pokazuje_ta_sama_liczbe_co_tabela(
    web_client, logged_supervisor, elim_stage, problems
):
    """Po ogłoszeniu wyników liczba jest publiczna – ukrywanie jej przed opiekunem nic nie chroni."""
    mine = student()
    entry = StageEntryFactory(stage=elim_stage, participant=mine)
    close_stage_timeline(elim_stage)
    publish_results(elim_stage, None, Anonymization.CODE)
    entry.refresh_from_db()

    body = web_client.get(reverse("web:supervisor")).content.decode()

    assert "Wyniki ogłoszone" in body
    assert str(entry.total_points) in body


def test_potwierdzenie_udzialu_szkoly_przelacza_sie(web_client, logged_supervisor, edition):
    """Jedno kliknięcie ustawia oświadczenie, drugie je wycofuje – oba zostawiają ślad w audycie."""
    url = reverse("web:supervisor-participation")

    web_client.post(url)
    assert SchoolParticipation.objects.filter(supervisor=logged_supervisor, edition=edition).exists()

    web_client.post(url)
    assert not SchoolParticipation.objects.filter(supervisor=logged_supervisor, edition=edition).exists()


def test_panel_jest_zamkniety_dla_innych_rol(web_client, participant):
    """Domyślnie zamknięte: konto bez profilu opiekuna dostaje 403, a nie pustą listę."""
    web_client.force_login(participant.user)

    assert web_client.get(reverse("web:supervisor")).status_code == 403


def test_panel_wymaga_zalogowania(web_client):
    """Niezalogowany trafia na formularz logowania z parametrem ``next``."""
    response = web_client.get(reverse("web:supervisor"))

    assert response.status_code == 302
    assert reverse("web:login") in response.headers["Location"]


def test_uczestnik_zapisuje_adres_opiekuna_w_profilu(web_client, participant, supervisor_registration_on):
    """Pole jest w formularzu profilu, opcjonalne i zapisuje się po normalizacji.

    Warunkiem jest włączona rola opiekuna: przy wyłączonym przełączniku pola w formularzu
    nie ma wcale (``test_supervisor_registration_flag``), bo byłoby pytaniem o adres, pod
    którym nikt nie może założyć konta.
    """
    web_client.force_login(participant.user)

    response = web_client.post(
        reverse("web:profile"),
        {
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "phone": "600 100 200",
            "district": participant.district,
            "grade": participant.grade,
            "birth_date": participant.birth_date,
            "school": participant.school,
            "school_custom": "on",
            "supervisor_email": "  Nauczyciel@Szkola.Test  ",
        },
    )

    assert response.status_code == 302
    participant.refresh_from_db()
    assert participant.supervisor_email == SUPERVISOR_EMAIL


def test_logowanie_opiekuna_prowadzi_do_jego_panelu(web_client, supervisor):
    """Po zalogowaniu opiekun ma trafić do swojego panelu, a nie na stronę główną."""
    supervisor.user.set_password(WEB_TEST_PASSWORD)
    supervisor.user.save(update_fields=["password"])

    response = web_client.post(
        reverse("web:login"), {"username": supervisor.user.email, "password": WEB_TEST_PASSWORD}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("web:supervisor")
