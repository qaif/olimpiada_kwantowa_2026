"""Zaświadczenia z warsztatów: klucz wiersza harmonogramu, lista na papierze i wystawianie hurtem.

Warsztaty są jedyną częścią olimpiady, o której baza nie wie nic sama z siebie: harmonogram jest
treścią redakcyjną, a obecność wpisuje organizator. Testy pilnują trzech rzeczy, które z tego
wynikają i które łatwo zepsuć:

- **klucz obecności przeżywa redakcję strony**. Redaktor dopisze wiersz na początku tabeli albo
  poprawi godziny – odhaczone obecności mają zostać przy swoich zajęciach,
- **dokument wylicza tematy**, bo zaświadczenie „był na czymś” jest w praktyce bezużyteczne,
- **odbiorcą jest uczestnik z wpisem do etapu w tej edycji** – dokument musi mieć się do czego
  przypiąć, a warsztat nie jest etapem.
"""

from datetime import date

import pytest

from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.cms.models import ContentPage, HomePage, WorkshopAttendance
from apps.cms.workshops import WORKSHOPS_SLUG, attended_workshops, workshop_key, workshop_rows
from apps.competitions.tests.factories import StageEntryFactory
from apps.results.certificates import (
    certificate_content,
    issue_workshop_certificates,
    participants_with_workshops,
    render_pdf,
)
from apps.results.models import Certificate, CertificateKind

from .conftest import make_stage

pytestmark = pytest.mark.django_db

#: Dwa wiersze harmonogramu, w których jest wszystko, co trafia na zaświadczenie.
ROWS = [
    ("Kubity i bramki kwantowe", date(2026, 11, 12), "dr Anna Kowalska"),
    ("Splątanie i nierówności Bella", date(2026, 12, 10), "prof. Jan Nowak"),
]


def schedule_block(rows) -> tuple:
    """Blok ``schedule`` w postaci, w jakiej zapisuje go edytor treści."""
    return (
        "schedule",
        {
            "caption": "",
            "topic_label": "Temat",
            "date_label": "Termin",
            "time_label": "",
            "lecturer_label": "Prowadzący",
            "rows": [
                {
                    "topic": topic,
                    "date": value.strftime("%d.%m.%Y"),
                    "date_value": value,
                    "time": "",
                    "lecturer": lecturer,
                }
                for topic, value, lecturer in rows
            ],
        },
    )


def workshops_page(rows=ROWS) -> ContentPage:
    """Strona „Warsztaty” z jednym blokiem ``schedule`` – jedyne źródło harmonogramu."""
    home = HomePage.objects.get()
    page = ContentPage(title="Warsztaty", slug=WORKSHOPS_SLUG, live=True, body=[schedule_block(rows)])
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def rewrite_rows(page: ContentPage, rows) -> ContentPage:
    """Redakcja tej samej strony – tak, jak robi ją człowiek: ta sama strona, inna tabela."""
    page.body = [schedule_block(rows)]
    page.save()
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


def participant_with_entry(stage, first_name="Łucja", last_name="Śniadecka"):
    participant = ParticipantFactory(user=UserFactory(first_name=first_name, last_name=last_name))
    StageEntryFactory(stage=stage, participant=participant)
    return participant


# --- klucz wiersza -------------------------------------------------------------------------------


def test_klucz_wiersza_nie_zalezy_od_kolejnosci_w_tabeli():
    """Dopisanie wcześniejszego terminu na początku tabeli nie może przesunąć obecności."""
    page = workshops_page()
    before = [row["key"] for row in workshop_rows(page)]

    reordered = rewrite_rows(page, [ROWS[1], ROWS[0]])
    after = {row["key"] for row in workshop_rows(reordered)}

    assert set(before) == after
    assert before == sorted(before)  # kolejność wyniku jest kolejnością kalendarza


def test_klucz_niesie_date_i_temat():
    """Klucz ma być czytelny w pliku CSV, który organizator składa ręcznie z eksportu platformy."""
    assert workshop_key("Kubity i bramki kwantowe", date(2026, 11, 12)) == (
        "2026-11-12-kubity-i-bramki-kwantowe"
    )


# --- obecność ------------------------------------------------------------------------------------


def test_obecnosc_bez_wiersza_w_harmonogramie_nie_trafia_na_dokument():
    """Zajęcia odwołane albo przemianowane znikają z dokumentu – został sam klucz, nie fakt."""
    page = workshops_page()
    participant = ParticipantFactory()
    WorkshopAttendance.objects.create(participant=participant, workshop_key=workshop_rows(page)[0]["key"])
    WorkshopAttendance.objects.create(participant=participant, workshop_key="2026-01-01-zajecia-odwolane")

    attended = attended_workshops(participant)

    assert [row["topic"] for row in attended] == ["Kubity i bramki kwantowe"]


def test_bez_odhaczonej_obecnosci_lista_jest_pusta():
    """Brak obecności to normalny stan uczestnika, a nie brak harmonogramu."""
    workshops_page()

    assert attended_workshops(ParticipantFactory()) == []


# --- wystawianie ---------------------------------------------------------------------------------


def test_zaswiadczenie_wylicza_tematy_terminy_i_prowadzacych():
    """Szkoła i komisja stypendialna pytają „na czym był”, a nie „czy był”."""
    page = workshops_page()
    stage = make_stage(problems=1)
    participant = participant_with_entry(stage)
    for row in workshop_rows(page):
        WorkshopAttendance.objects.create(participant=participant, workshop_key=row["key"])

    certificates = issue_workshop_certificates(stage.edition)

    content = certificate_content(certificates[0])
    assert certificates[0].kind == CertificateKind.WARSZTATY
    assert content.title == "Zaświadczenie o udziale w warsztatach"
    assert content.workshops == (
        "12.11.2026 – Kubity i bramki kwantowe, dr Anna Kowalska",
        "10.12.2026 – Splątanie i nierówności Bella, prof. Jan Nowak",
    )
    assert render_pdf(certificates[0]).startswith(b"%PDF-")


def test_wystawianie_hurtem_pomija_uczestnikow_bez_obecnosci_i_jest_idempotentne():
    """Powtórne kliknięcie ma oddać te same numery – lista bywa klikana w trakcie gali."""
    page = workshops_page()
    stage = make_stage(problems=1)
    present = participant_with_entry(stage)
    participant_with_entry(stage, first_name="Jan", last_name="Nieobecny")
    WorkshopAttendance.objects.create(participant=present, workshop_key=workshop_rows(page)[0]["key"])

    first = issue_workshop_certificates(stage.edition)
    second = issue_workshop_certificates(stage.edition)

    assert len(first) == 1
    assert [item.pk for item in first] == [item.pk for item in second]
    assert Certificate.objects.count() == 1


def test_uczestnik_bez_wpisu_w_edycji_nie_dostaje_zaswiadczenia():
    """Dokument przypina się do wpisu w edycji – bez niego nie wiadomo, której edycji dotyczy."""
    page = workshops_page()
    stage = make_stage(problems=1)
    outsider = ParticipantFactory()
    WorkshopAttendance.objects.create(participant=outsider, workshop_key=workshop_rows(page)[0]["key"])

    assert participants_with_workshops(stage.edition) == []
    assert issue_workshop_certificates(stage.edition) == []
