"""Zgoda ucznia na opiekuna szkolnego dopisującego się importem listy (v0.38.7).

Decyzja właściciela platformy: opiekun szkolny jest dowiązywany do **istniejącego** ucznia wyłącznie
za jego zgodą. Do v0.38.5 import listy nadpisywał ``supervisor_email`` ucznia adresem osoby, która
wgrała plik, a rejestracja opiekunów jest otwarta – więc każdy mógł dopisać się do dowolnego ucznia
znanego z adresu e-mail. Te testy idą przez HTTP, bo przedmiotem jest cała droga: import → list do
ucznia → strona zgody → decyzja; reguły tokenu sprawdza też ``apps/accounts/tests``.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.models import GROUP_SUPERVISOR, Participant, SchoolSupervisor
from apps.accounts.supervisor_consent import (
    SUPERVISOR_CONSENT_DAYS,
    VIA_SUPERVISOR,
    make_token,
    read_token,
)
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import current_or_default_competition

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"
OLD_SUPERVISOR = "stary.opiekun@szkola.test"
STUDENT_EMAIL = "ola@example.test"
ADULT_YEAR = timezone.localdate().year - 25

HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"
IMPORT_URL = "/supervisor/import/"
COORDINATOR_IMPORT_URL = "/coordinator/accounts/import/"
LINK = re.compile(r"/opiekun/zgoda/([^/\s\"<]+)/")


def upload(*rows: str, header: str = HEADER) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "klasa.csv", "\n".join([header, *rows]).encode("utf-8"), content_type="text/csv"
    )


def line(email: str) -> str:
    return f"Ola;Nowak;{email};{ADULT_YEAR};2;;"


@pytest.fixture
def supervisor():
    user = UserFactory(email=SUPERVISOR_EMAIL, first_name="Anna", last_name="Nauczycielska")
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


@pytest.fixture
def student():
    """Uczeń, który zarejestrował się sam i ma już **innego** opiekuna szkolnego."""
    return ParticipantFactory(
        user=UserFactory(email=STUDENT_EMAIL, first_name="Ola"), supervisor_email=OLD_SUPERVISOR
    )


def supervisor_import(client, *rows: str, capture) -> tuple:
    """Podgląd i zatwierdzenie importu nauczyciela – dokładnie tak, jak robi to przeglądarka."""
    preview = client.post(IMPORT_URL, {"file": upload(*rows)})
    assert preview.status_code == 200
    data = {"rows": preview.context["preview"].token, "school": preview.context["school_name"]}
    with capture(execute=True):
        confirm = client.post(IMPORT_URL, data)
    return preview, confirm


def consent_url_from(message) -> str:
    match = LINK.search(message.body)
    assert match, message.body
    return reverse("web:supervisor-consent", args=[match.group(1)])


def consent_url(participant, supervisor_email: str = SUPERVISOR_EMAIL) -> str:
    return reverse(
        "web:supervisor-consent", args=[make_token(participant, supervisor_email, via=VIA_SUPERVISOR)]
    )


# --- import: zamiast zapisu – list do ucznia ---------------------------------------------------


def test_import_istniejacego_ucznia_nie_zmienia_opiekuna_i_wysyla_mu_jeden_list(
    web_client, supervisor, student, mailoutbox, django_capture_on_commit_callbacks
):
    """Sedno decyzji: profil ucznia zostaje nietknięty, a on sam dostaje jeden list z prośbą."""
    web_client.force_login(supervisor.user)
    _, confirm = supervisor_import(
        web_client, line(STUDENT_EMAIL), capture=django_capture_on_commit_callbacks
    )
    assert confirm.status_code == 302

    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [STUDENT_EMAIL]
    # Kto prosi – imię, nazwisko i adres z **konta** opiekuna – i o co.
    assert "Anna Nauczycielska" in message.body
    assert SUPERVISOR_EMAIL in message.body
    assert f"{SUPERVISOR_CONSENT_DAYS} dni" in message.body
    # Wersja HTML bez zasobów zdalnych: jedyny adres w liście to link do strony zgody.
    html = message.alternatives[0][0]
    assert "<img" not in html and "<link" not in html and "src=" not in html
    assert LINK.search(html)
    assert AuditLog.objects.filter(
        action="participant.supervisor_consent_requested", target_id=str(student.pk)
    ).exists()


def test_drugi_import_w_ciagu_doby_nie_wysyla_drugiego_listu(
    web_client, supervisor, student, mailoutbox, django_capture_on_commit_callbacks
):
    """Jedna prośba na parę (uczeń, opiekun) na dobę – poprawiony i wgrany ponownie plik nie spamuje."""
    web_client.force_login(supervisor.user)
    supervisor_import(web_client, line(STUDENT_EMAIL), capture=django_capture_on_commit_callbacks)
    supervisor_import(web_client, line(STUDENT_EMAIL), capture=django_capture_on_commit_callbacks)
    assert len(mailoutbox) == 1


def test_uczen_ktory_juz_wskazal_tego_opiekuna_nie_dostaje_listu(
    web_client, supervisor, mailoutbox, django_capture_on_commit_callbacks
):
    """Uczeń, który sam wpisał adres nauczyciela, nie ma o co być pytany."""
    ParticipantFactory(user=UserFactory(email=STUDENT_EMAIL), supervisor_email=SUPERVISOR_EMAIL.upper())
    web_client.force_login(supervisor.user)
    supervisor_import(web_client, line(STUDENT_EMAIL), capture=django_capture_on_commit_callbacks)
    assert mailoutbox == []


def test_podglad_ma_jedna_etykiete_dla_ucznia_i_dla_konta_bez_profilu_uczestnika(
    web_client, supervisor, student
):
    """Podgląd nie jest wyszukiwarką ról: adres ucznia i adres recenzenta wyglądają w nim tak samo."""
    UserFactory(email="recenzent@example.test")
    web_client.force_login(supervisor.user)
    for_student = web_client.post(IMPORT_URL, {"file": upload(line(STUDENT_EMAIL))})
    for_reviewer = web_client.post(IMPORT_URL, {"file": upload(line("recenzent@example.test"))})
    rows = [response.context["preview"].rows[0] for response in (for_student, for_reviewer)]
    assert [(row.action, row.errors, row.notes) for row in rows] == [
        (rows[0].action, rows[0].errors, rows[0].notes)
    ] * 2
    summaries = [
        (p.to_create, p.existing, p.skipped)
        for p in (for_student.context["preview"], for_reviewer.context["preview"])
    ]
    assert summaries[0] == summaries[1] == (0, 1, 0)
    assert "nie jest kontem uczestnika" not in for_reviewer.content.decode()


def test_podsumowanie_importu_nie_mowi_ile_prosb_wyszlo(
    web_client, supervisor, django_capture_on_commit_callbacks
):
    """Komunikat po zapisie jest ten sam dla konta ucznia i konta bez profilu uczestnika."""
    UserFactory(email="recenzent@example.test")
    web_client.force_login(supervisor.user)
    ParticipantFactory(user=UserFactory(email=STUDENT_EMAIL))
    texts = []
    for email in (STUDENT_EMAIL, "recenzent@example.test"):
        _, confirm = supervisor_import(web_client, line(email), capture=django_capture_on_commit_callbacks)
        page = web_client.get(confirm.headers["Location"])
        texts.append([str(message) for message in page.context["messages"]])
    assert texts[0] == texts[1]


def test_import_opiekuna_ma_limit_zadan(web_client, supervisor):
    """Każdy POST importu (podgląd i zatwierdzenie) zużywa limit scope'u ``upload``."""
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], "upload": "1/hour"}
    web_client.force_login(supervisor.user)
    with override_settings(REST_FRAMEWORK=config):
        assert web_client.post(IMPORT_URL, {"file": upload(line("a@example.test"))}).status_code == 200
        assert web_client.post(IMPORT_URL, {"file": upload(line("a@example.test"))}).status_code == 429


# --- import koordynatora: zgoda obowiązuje i tutaj ---------------------------------------------


def coordinator_import(client, *rows: str, header: str, capture):
    preview = client.post(
        COORDINATOR_IMPORT_URL,
        {"file": upload(*rows, header=header), "school_custom": "on", "school": "Zespół Szkół nr 2"},
    )
    assert preview.status_code == 200
    data = {"rows": preview.context["preview"].token, "school": preview.context["school_name"]}
    with capture(execute=True):
        return client.post(COORDINATOR_IMPORT_URL, data)


def test_import_koordynatora_tez_prosi_ucznia_o_zgode(
    web_client, coordinator, supervisor, student, mailoutbox, django_capture_on_commit_callbacks
):
    """Kolumna „e-mail opiekuna szkolnego” nie przypisuje nauczyciela istniejącemu uczniowi od razu."""
    web_client.force_login(coordinator)
    coordinator_import(
        web_client,
        line(STUDENT_EMAIL) + f";{SUPERVISOR_EMAIL}",
        header=HEADER + ";e-mail opiekuna szkolnego",
        capture=django_capture_on_commit_callbacks,
    )
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR
    assert len(mailoutbox) == 1
    assert "Organizator konkursu" in mailoutbox[0].body
    assert "Anna Nauczycielska" in mailoutbox[0].body


def test_import_koordynatora_bez_kolumny_opiekuna_nie_odpina_ucznia(
    web_client, coordinator, student, mailoutbox, django_capture_on_commit_callbacks
):
    """Do v0.38.5 pusty adres w pliku koordynatora **czyścił** opiekuna, którego uczeń wpisał sam."""
    web_client.force_login(coordinator)
    coordinator_import(
        web_client, line(STUDENT_EMAIL), header=HEADER, capture=django_capture_on_commit_callbacks
    )
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR
    assert mailoutbox == []


# --- strona zgody -------------------------------------------------------------------------------


def test_niezalogowany_trafia_na_logowanie_z_powrotem_na_strone_zgody(web_client, supervisor, student):
    url = consent_url(student)
    response = web_client.get(url)
    assert response.status_code == 302
    assert "/login/" in response.headers["Location"]
    assert "opiekun" in response.headers["Location"]


def test_strona_mowi_kto_prosi_co_zobaczy_i_ze_zastapi_obecnego_opiekuna(web_client, supervisor, student):
    web_client.force_login(student.user)
    response = web_client.get(consent_url(student))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Anna Nauczycielska" in body
    assert SUPERVISOR_EMAIL in body
    assert OLD_SUPERVISOR in body
    assert "zastąpi" in body
    assert "Zgadzam się" in body and "Nie zgadzam się" in body
    # GET niczego nie zmienia – klienty pocztowe i skanery otwierają linki same.
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR


def test_zgoda_ucznia_zapisuje_opiekuna_i_zostawia_slad_bez_adresow(
    web_client, supervisor, student, mailoutbox, django_capture_on_commit_callbacks
):
    """Pełna droga: import → list → strona → „Zgadzam się” → nauczyciel widzi ucznia."""
    web_client.force_login(supervisor.user)
    supervisor_import(web_client, line(STUDENT_EMAIL), capture=django_capture_on_commit_callbacks)
    url = consent_url_from(mailoutbox[0])

    web_client.force_login(student.user)
    response = web_client.post(url, {"decision": "accept"})
    assert response.status_code == 302
    student.refresh_from_db()
    assert student.supervisor_email == SUPERVISOR_EMAIL

    entry = AuditLog.objects.get(action="participant.supervisor_consented", target_id=str(student.pk))
    assert entry.actor_id == student.user_id
    assert entry.diff == {"via": VIA_SUPERVISOR, "replaced": True}
    assert SUPERVISOR_EMAIL not in str(entry.diff) and OLD_SUPERVISOR not in str(entry.diff)

    web_client.force_login(supervisor.user)
    dashboard = web_client.get("/supervisor/")
    assert [row["participant"].pk for row in dashboard.context["rows"]] == [student.pk]


def test_odmowa_niczego_nie_zmienia_i_zostaje_w_audycie(web_client, supervisor, student):
    web_client.force_login(student.user)
    response = web_client.post(consent_url(student), {"decision": "refuse"})
    assert response.status_code == 302
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR
    assert AuditLog.objects.filter(
        action="participant.supervisor_consent_refused", target_id=str(student.pk)
    ).exists()


def test_decyzja_bez_tokenu_csrf_jest_odrzucana(supervisor, student):
    """Zgoda jest zwykłym formularzem serwisu – bez tokenu CSRF obca strona nie kliknie jej za ucznia."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(student.user)
    assert client.post(consent_url(student), {"decision": "accept"}).status_code == 403
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR


def test_post_bez_decyzji_niczego_nie_zapisuje(web_client, supervisor, student):
    web_client.force_login(student.user)
    response = web_client.post(consent_url(student), {})
    assert response.status_code == 400
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR


def test_inny_zalogowany_uzytkownik_nie_zgodzi_sie_za_ucznia(web_client, supervisor, student):
    """Najważniejszy przypadek: nauczyciel z kopią linku nie może kliknąć „Zgadzam się” sam."""
    web_client.force_login(supervisor.user)
    url = consent_url(student)
    assert web_client.get(url).status_code == 403
    assert web_client.post(url, {"decision": "accept"}).status_code == 403
    other = ParticipantFactory(user=UserFactory(email="inny.uczen@example.test"))
    web_client.force_login(other.user)
    assert web_client.post(url, {"decision": "accept"}).status_code == 403
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR


def test_link_nieaktualny_po_zmianie_opiekuna_przez_ucznia(web_client, supervisor, student):
    """Uczeń zmienił opiekuna po wysłaniu prośby – stary link nie może odwrócić jego decyzji."""
    url = consent_url(student)
    Participant.objects.filter(pk=student.pk).update(supervisor_email="ktos.trzeci@szkola.test")
    web_client.force_login(student.user)
    response = web_client.post(url, {"decision": "accept"})
    assert response.status_code == 400
    student.refresh_from_db()
    assert student.supervisor_email == "ktos.trzeci@szkola.test"


def test_link_po_zgodzie_nie_dziala_drugi_raz(web_client, supervisor, student):
    url = consent_url(student)
    web_client.force_login(student.user)
    assert web_client.post(url, {"decision": "accept"}).status_code == 302
    assert web_client.post(url, {"decision": "refuse"}).status_code == 400


def test_link_podrobiony_nie_dziala(web_client, supervisor, student):
    web_client.force_login(student.user)
    forged = consent_url(student)[:-3] + "xx/"
    assert web_client.post(forged, {"decision": "accept"}).status_code == 400
    student.refresh_from_db()
    assert student.supervisor_email == OLD_SUPERVISOR


def test_link_wygasly_nie_dziala(supervisor, student):
    """Wygaśnięcie sprawdzamy na serwisie, a nie przez HTTP: przesunięty zegar unieważniłby też
    sesję logowania i test mierzyłby przekierowanie na logowanie, a nie odmowę tokenu."""
    token = make_token(student, SUPERVISOR_EMAIL, via=VIA_SUPERVISOR)
    assert read_token(token).supervisor_email == SUPERVISOR_EMAIL
    with (
        freeze_time(timezone.now() + timedelta(days=SUPERVISOR_CONSENT_DAYS + 1)),
        pytest.raises(DomainError),
    ):
        read_token(token)
