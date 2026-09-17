"""Panel koordynatora: szablony dokumentów, zaświadczenia opiekunów i obecność na warsztatach.

Testy warstwy WWW pilnują tego, czego nie widać w serwisie domenowym: że ekran w ogóle się
renderuje, że POST robi to, co obiecuje przycisk, i — przede wszystkim — że **nikt poza
koordynatorem tam nie wejdzie**. Reguły dopasowania szablonu, numeracji i idempotencji sprawdzają
testy ``apps/results/tests/`` i nie powtarzamy ich tutaj.
"""

import zipfile
from datetime import date
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import SchoolSupervisor
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.cms.models import ContentPage, HomePage, WorkshopAttendance
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_rows
from apps.competitions.tests.factories import StageEntryFactory
from apps.results.certificate_layout import DEFAULT_LAYOUT
from apps.results.models import Certificate, CertificateKind, CertificateTemplate

pytestmark = pytest.mark.django_db

TOPIC = "Kubity i bramki kwantowe"
WORKSHOP_DATE = date(2026, 11, 12)


@pytest.fixture
def logged_coordinator(web_client, coordinator):
    web_client.force_login(coordinator)
    return coordinator


@pytest.fixture
def workshops_page() -> ContentPage:
    """Strona „Warsztaty” z jednym wierszem harmonogramu – kolumna tabeli obecności."""
    home = HomePage.objects.get()
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "",
                    "lecturer_label": "Prowadzący",
                    "rows": [
                        {
                            "topic": TOPIC,
                            "date": "12.11.2026",
                            "date_value": WORKSHOP_DATE,
                            "time": "",
                            "lecturer": "dr Anna Kowalska",
                        }
                    ],
                },
            )
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def workshop_key_of(page) -> str:
    return workshop_rows(page)[0]["key"]


def supervised_participant(stage, supervisor_email: str, **kwargs):
    """Uczestnik z wpisem do etapu, który wskazał podany adres opiekuna."""
    participant = ParticipantFactory(supervisor_email=supervisor_email, **kwargs)
    StageEntryFactory(stage=stage, participant=participant)
    return participant


def supervisor_account(email="opiekun@example.test") -> SchoolSupervisor:
    return SchoolSupervisor.objects.create(
        user=UserFactory(email=email, first_name="Anna", last_name="Nauczycielska"),
        school="XIV LO",
    )


# --- szablony ------------------------------------------------------------------------------------


def test_lista_szablonow_mowi_wprost_ze_pustka_jest_poprawna(web_client, logged_coordinator):
    """Brak szablonu to stan produkcji przez większość roku, a nie usterka do naprawienia."""
    response = web_client.get(reverse("web:coordinator-certificate-templates"))

    assert response.status_code == 200
    assert "układem wbudowanym" in response.content.decode()


def test_szablon_da_sie_dodac_zmienic_i_usunac(web_client, logged_coordinator, edition):
    """Pełny obieg z panelu: dodanie z plikiem tła, zmiana nazwy, usunięcie."""
    from apps.results.tests.test_certificate_templates import png_bytes

    created = web_client.post(
        reverse("web:coordinator-certificate-template-new"),
        {
            "name": "Winieta jubileuszowa",
            "kind": CertificateKind.LAUREAT,
            "edition": edition.pk,
            "layout": "{}",
            "is_active": "on",
            "background": SimpleUploadedFile("tlo.png", png_bytes(), content_type="image/png"),
        },
    )

    assert created.status_code == 302
    template = CertificateTemplate.objects.get()
    assert template.created_by_id == logged_coordinator.pk
    assert template.background

    web_client.post(
        reverse("web:coordinator-certificate-template-edit", args=[template.pk]),
        {"name": "Winieta 2027", "kind": CertificateKind.LAUREAT, "edition": edition.pk, "layout": "{}"},
    )
    template.refresh_from_db()
    assert template.name == "Winieta 2027"
    # Puste „aktywny” w POST-cie znaczy odznaczoną kratkę – szablon ma się wtedy wyłączyć.
    assert template.is_active is False

    web_client.post(reverse("web:coordinator-certificate-template-delete", args=[template.pk]))
    assert CertificateTemplate.objects.count() == 0


def test_bledny_uklad_wraca_z_formularzem_a_nie_z_bledem_500(web_client, logged_coordinator):
    """Układ wpisuje człowiek, więc formularz musi umieć odmówić – i powiedzieć dlaczego."""
    response = web_client.post(
        reverse("web:coordinator-certificate-template-new"),
        {"name": "Zły układ", "kind": "", "layout": "[1, 2, 3]"},
    )

    assert response.status_code == 400
    assert CertificateTemplate.objects.count() == 0


def test_nowy_szablon_dostaje_uklad_domyslny_w_formularzu(web_client, logged_coordinator):
    """Redaktor przesuwa napisy względem czegoś, co działa, a nie układa strony od pustej kartki."""
    body = web_client.get(reverse("web:coordinator-certificate-template-new")).content.decode()

    assert str(DEFAULT_LAYOUT["recipient"]["y"]) in body


def test_podglad_oddaje_pdf_i_nie_wystawia_dokumentu(web_client, logged_coordinator):
    """Obejrzenie układu nie może zużyć numeru z puli ani zostawić dyplomu w rejestrze."""
    template = CertificateTemplate.objects.create(name="Podgląd", kind=CertificateKind.LAUREAT)

    response = web_client.get(reverse("web:coordinator-certificate-template-preview", args=[template.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF-")
    assert Certificate.objects.count() == 0


def test_ustaw_jako_domyslny_wylacza_poprzedni(web_client, logged_coordinator, edition):
    """Jedno kliknięcie zamiast odznaczania „aktywny” w każdym z pozostałych szablonów."""
    old = CertificateTemplate.objects.create(name="Stary", kind="", edition=edition)
    new = CertificateTemplate.objects.create(name="Nowy", kind="", edition=edition, is_active=False)

    web_client.post(reverse("web:coordinator-certificate-template-default", args=[new.pk]))

    old.refresh_from_db()
    new.refresh_from_db()
    assert (new.is_active, old.is_active) == (True, False)


def test_szablony_zamkniete_dla_uczestnika(web_client, participant):
    """Wygląd dokumentów olimpiady nie jest sprawą, do której dopuszcza się kogokolwiek innego."""
    web_client.force_login(participant.user)

    assert web_client.get(reverse("web:coordinator-certificate-templates")).status_code == 403
    assert web_client.post(reverse("web:coordinator-certificate-template-new"), {}).status_code == 403


# --- opiekunowie ---------------------------------------------------------------------------------


def test_lista_opiekunow_pokazuje_liczbe_uczniow(web_client, logged_coordinator, elim_stage):
    """Bez liczby uczniów przycisk „Wystaw” byłby decyzją na ślepo."""
    supervisor = supervisor_account()
    supervised_participant(elim_stage, supervisor.user.email)
    supervised_participant(elim_stage, supervisor.user.email.upper())

    body = web_client.get(reverse("web:coordinator-supervisors")).content.decode()

    assert "Nauczycielska" in body
    # Adres wpisany wielkimi literami to ten sam adres – inaczej nauczyciel gubi połowę uczniów.
    assert ">2<" in body


def test_wystaw_wszystkim_opiekunom_oddaje_paczke_zip(web_client, logged_coordinator, elim_stage):
    """Zaświadczenia dla opiekunów wychodzą hurtem, a nie klikaniem po wierszu."""
    first = supervisor_account("pierwszy@example.test")
    second = supervisor_account("drugi@example.test")
    supervised_participant(elim_stage, first.user.email)
    supervised_participant(elim_stage, second.user.email)

    response = web_client.post(reverse("web:coordinator-supervisor-certificates"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        assert len(archive.namelist()) == 2
    assert Certificate.objects.filter(kind=CertificateKind.OPIEKUN).count() == 2


def test_wystaw_jednemu_opiekunowi_jest_idempotentne(web_client, logged_coordinator, elim_stage):
    """Drugie kliknięcie ma oddać ten sam numer, a nie wypisać drugie zaświadczenie."""
    supervisor = supervisor_account()
    supervised_participant(elim_stage, supervisor.user.email)
    url = reverse("web:coordinator-supervisor-certificate", args=[supervisor.pk])

    web_client.post(url)
    web_client.post(url)

    assert Certificate.objects.filter(supervisor=supervisor).count() == 1


def test_lista_opiekunow_zamknieta_dla_uczestnika(web_client, participant):
    """Lista niesie dane osobowe nauczycieli – widzi ją wyłącznie koordynator."""
    web_client.force_login(participant.user)

    assert web_client.get(reverse("web:coordinator-supervisors")).status_code == 403
    assert web_client.post(reverse("web:coordinator-supervisor-certificates")).status_code == 403


# --- warsztaty -----------------------------------------------------------------------------------


def test_tabela_obecnosci_pokazuje_kolumny_z_harmonogramu(
    web_client, logged_coordinator, elim_stage, workshops_page
):
    """Kolumny biorą się z treści redakcyjnej – panel nie prowadzi drugiego harmonogramu."""
    supervised_participant(elim_stage, "")

    body = web_client.get(reverse("web:coordinator-workshop-attendance")).content.decode()

    assert TOPIC in body
    assert workshop_key_of(workshops_page) in body


def test_zapis_tabeli_dodaje_i_zdejmuje_obecnosc(web_client, logged_coordinator, elim_stage, workshops_page):
    """Kratka odznaczona ma znaczyć „nie było”, ale **tylko** dla uczestników z tej strony."""
    participant = supervised_participant(elim_stage, "")
    key = workshop_key_of(workshops_page)
    url = reverse("web:coordinator-workshop-attendance")

    web_client.post(url, {"participant": [participant.pk], "attend": [f"{participant.pk}:{key}"]})
    assert WorkshopAttendance.objects.filter(participant=participant, workshop_key=key).exists()

    web_client.post(url, {"participant": [participant.pk]})
    assert not WorkshopAttendance.objects.filter(participant=participant).exists()


def test_zapis_jednej_strony_nie_kasuje_obecnosci_spoza_niej(
    web_client, logged_coordinator, elim_stage, workshops_page
):
    """Przejście na drugą stronę listy nie może po cichu wyczyścić pierwszej."""
    shown = supervised_participant(elim_stage, "")
    hidden = supervised_participant(elim_stage, "")
    key = workshop_key_of(workshops_page)
    WorkshopAttendance.objects.create(participant=hidden, workshop_key=key)

    web_client.post(
        reverse("web:coordinator-workshop-attendance"),
        {"participant": [shown.pk], "attend": [f"{shown.pk}:{key}"]},
    )

    assert WorkshopAttendance.objects.filter(participant=hidden).exists()


def test_import_csv_doklada_obecnosci(web_client, logged_coordinator, elim_stage, workshops_page):
    """Lista obecności powstaje na platformie wideo – przepisywanie jej ręcznie nie ma sensu."""
    participant = supervised_participant(elim_stage, "")
    key = workshop_key_of(workshops_page)
    plik = SimpleUploadedFile(
        "obecnosc.csv",
        f"kod,warsztat\n{participant.public_code},{key}\nNIEZNANY,{key}\n".encode(),
        content_type="text/csv",
    )

    web_client.post(
        reverse("web:coordinator-workshop-attendance"),
        {"action": "import", "file": plik},
    )

    assert WorkshopAttendance.objects.filter(participant=participant, workshop_key=key).count() == 1
    # Nieznany kod jest pomijany, a nie wywraca całego importu: plik z platformy bywa pełen
    # gości bez konta w serwisie olimpiady.
    assert WorkshopAttendance.objects.count() == 1


def test_wystaw_zaswiadczenia_z_warsztatow_oddaje_paczke(
    web_client, logged_coordinator, elim_stage, workshops_page
):
    """Odbiorcą jest każdy z co najmniej jedną obecnością i wpisem do etapu w tej edycji."""
    participant = supervised_participant(elim_stage, "")
    supervised_participant(elim_stage, "")  # bez obecności – nie dostaje dokumentu
    WorkshopAttendance.objects.create(participant=participant, workshop_key=workshop_key_of(workshops_page))

    response = web_client.post(reverse("web:coordinator-workshop-certificates"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        assert len(archive.namelist()) == 1
    assert Certificate.objects.filter(kind=CertificateKind.WARSZTATY).count() == 1


def test_obecnosc_na_warsztatach_zamknieta_dla_uczestnika(web_client, participant):
    """Obecność jest oświadczeniem organizatora – uczestnik nie odhacza jej sobie sam."""
    web_client.force_login(participant.user)

    assert web_client.get(reverse("web:coordinator-workshop-attendance")).status_code == 403
    assert web_client.post(reverse("web:coordinator-workshop-certificates")).status_code == 403


# --- weryfikacja publiczna nowych rodzajów --------------------------------------------------------


def test_weryfikacja_zaswiadczenia_z_warsztatow_dziala_bez_logowania(web_client, elim_stage, workshops_page):
    """Nowy rodzaj dokumentu ma być sprawdzalny tą samą drogą, co dyplom laureata."""
    from apps.results.certificates import issue_workshop_certificates

    participant = ParticipantFactory()
    StageEntryFactory(stage=elim_stage, participant=participant)
    WorkshopAttendance.objects.create(participant=participant, workshop_key=workshop_key_of(workshops_page))
    certificate = issue_workshop_certificates(elim_stage.edition)[0]

    body = web_client.get(reverse("web:certificate-verify", args=[certificate.code])).content.decode()

    assert certificate.number in body
    assert "Zaświadczenie o udziale w warsztatach" in body
    # Bez pieczęci strona mówi to wprost – pusta rubryka wyglądałaby na wadę dokumentu.
    assert "bez pieczęci elektronicznej" in body


def test_weryfikacja_pokazuje_pieczec_gdy_dokument_jest_podpisany(web_client, elim_stage):
    """Nazwa pieczętującego jest odpowiedzią na „czyja to pieczęć”, a nie ozdobą."""
    from django.utils import timezone

    from apps.results.certificates import issue_certificate

    entry = StageEntryFactory(stage=elim_stage)
    certificate, _ = issue_certificate(edition=elim_stage.edition, kind=CertificateKind.LAUREAT, entry=entry)
    Certificate.objects.filter(pk=certificate.pk).update(
        signed=True, signed_at=timezone.now(), signer_name="Olimpiada Kwantowa"
    )

    body = web_client.get(reverse("web:certificate-verify", args=[certificate.code])).content.decode()

    assert "podpisany elektronicznie" in body
    assert "Olimpiada Kwantowa" in body
