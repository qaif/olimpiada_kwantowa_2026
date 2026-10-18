"""Status wiersza importu uczniów i licznik „już na liście” (REG-UX-01 § 2).

Zgłoszenie z produkcji: uczeń z kontem, który już wskazał nauczyciela, był w podglądzie i w komunikacie
„adresem z istniejącym kontem (uczniowie dostali prośbę o zgodę)”, choć prośba nie szła – nauczyciel
szukał ucznia, który od dawna był na jego liście. Testy pilnują dwóch rzeczy naraz: statusy mówią,
co się stanie, a nauczyciel nadal nie odróżnia ucznia z innym opiekunem od konta kogoś innego.
"""

from __future__ import annotations

import pytest

from apps.accounts.bulk_registration import (
    EXISTING_NOT_ON_LIST_NOTE,
    EXISTING_NOTE,
    STATUS_ERROR,
    STATUS_EXISTING,
    STATUS_INVITE,
    STATUS_NO_SUPERVISOR,
    STATUS_NOT_PARTICIPANT,
    STATUS_ON_LIST,
    STATUS_OTHER_SUPERVISOR,
    STATUS_UNCHANGED,
    import_students,
    preview_upload,
    unpack_rows,
)
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.accounts.tests.test_bulk_registration import HEADER, SUPERVISOR_EMAIL, csv_upload, row

pytestmark = pytest.mark.django_db

COORDINATOR_HEADER = HEADER + ";e-mail opiekuna szkolnego"
OTHER_TEACHER = "inny.nauczyciel@szkola.test"


def teacher_preview(*rows: str):
    return preview_upload(csv_upload(*rows), with_supervisor=False, default_supervisor_email=SUPERVISOR_EMAIL)


def coordinator_preview(*rows: str):
    return preview_upload(csv_upload(*rows, header=COORDINATOR_HEADER), with_supervisor=True)


def statuses(preview) -> list[str]:
    return [item.status for item in preview.rows]


def test_nauczyciel_widzi_ucznia_z_wlasnej_listy_i_neutralne_zdanie_dla_reszty():
    # Uczeń wpisał adres nauczyciela inną wielkością liter – lista dopasowuje po znormalizowanym adresie.
    ParticipantFactory(user=UserFactory(email="moj@example.test"), supervisor_email=SUPERVISOR_EMAIL.upper())
    ParticipantFactory(user=UserFactory(email="cudzy@example.test"), supervisor_email=OTHER_TEACHER)
    ParticipantFactory(user=UserFactory(email="bez@example.test"), supervisor_email="")
    UserFactory(email="recenzent@example.test")

    preview = teacher_preview(
        row("nowy@example.test"),
        row("moj@example.test"),
        row("cudzy@example.test"),
        row("bez@example.test"),
        row("recenzent@example.test"),
        row("zly-adres"),
    )

    assert statuses(preview) == [
        STATUS_INVITE,
        STATUS_ON_LIST,
        STATUS_EXISTING,
        STATUS_EXISTING,
        STATUS_EXISTING,
        STATUS_ERROR,
    ]
    on_list = preview.rows[1]
    assert str(EXISTING_NOTE) not in on_list.notes
    assert str(EXISTING_NOT_ON_LIST_NOTE) not in on_list.notes
    assert on_list.user_id is None  # odnośnik nauczyciela prowadzi do jego listy, nie do konta
    # Wyrocznia ról zostaje zamknięta: uczeń z innym opiekunem, bez opiekuna i recenzent – ten sam wiersz.
    others = [(item.action, item.status, item.errors, item.notes) for item in preview.rows[2:5]]
    assert others == [others[0]] * 3
    assert preview.rows[2].notes == [str(EXISTING_NOT_ON_LIST_NOTE)]
    assert (preview.to_create, preview.on_list, preview.other_existing, preview.skipped) == (1, 1, 3, 1)


def test_uczen_innego_konkursu_jest_u_nauczyciela_zwyklym_zajetym_adresem(other_competition):
    ParticipantFactory(
        user=UserFactory(email="obcy@example.test"),
        competition=other_competition,
        supervisor_email=SUPERVISOR_EMAIL,
    )

    preview = teacher_preview(row("obcy@example.test"))

    # Wskazał tego nauczyciela – ale w innej olimpiadzie, więc na liście tego konkursu go nie ma.
    assert statuses(preview) == [STATUS_EXISTING]


def test_koordynator_widzi_pelny_podzial_zajetych_adresow(other_competition):
    ParticipantFactory(user=UserFactory(email="na-liscie@example.test"), supervisor_email=OTHER_TEACHER)
    ParticipantFactory(user=UserFactory(email="inny@example.test"), supervisor_email="ktos@szkola.test")
    ParticipantFactory(user=UserFactory(email="bez@example.test"), supervisor_email="")
    on_list_user = UserFactory(email="bez-kolumny@example.test")
    ParticipantFactory(user=on_list_user, supervisor_email=OTHER_TEACHER)
    ParticipantFactory(user=UserFactory(email="obcy@example.test"), competition=other_competition)
    UserFactory(email="recenzent@example.test")

    preview = coordinator_preview(
        row("na-liscie@example.test") + f";{OTHER_TEACHER.upper()}",
        row("inny@example.test") + f";{OTHER_TEACHER}",
        row("bez@example.test") + f";{OTHER_TEACHER}",
        row("bez-kolumny@example.test") + ";",
        row("obcy@example.test") + f";{OTHER_TEACHER}",
        row("recenzent@example.test") + f";{OTHER_TEACHER}",
    )

    assert statuses(preview) == [
        STATUS_ON_LIST,
        STATUS_OTHER_SUPERVISOR,
        STATUS_NO_SUPERVISOR,
        STATUS_UNCHANGED,
        # Konto w innym konkursie i konto bez profilu ucznia – jeden status (izolacja konkursów).
        STATUS_NOT_PARTICIPANT,
        STATUS_NOT_PARTICIPANT,
    ]
    assert preview.rows[0].user_id is not None
    assert preview.rows[4].user_id is None
    assert all(str(EXISTING_NOTE) not in item.notes for item in preview.rows)


def test_zapis_liczy_uczniow_juz_na_liscie_i_nie_wysyla_im_prosby(
    mailoutbox, django_capture_on_commit_callbacks
):
    ParticipantFactory(user=UserFactory(email="moj@example.test"), supervisor_email=SUPERVISOR_EMAIL)
    ParticipantFactory(user=UserFactory(email="cudzy@example.test"), supervisor_email=OTHER_TEACHER)
    preview = teacher_preview(row("moj@example.test"), row("cudzy@example.test"), row("nowy@example.test"))

    with django_capture_on_commit_callbacks(execute=True):
        summary = import_students(
            unpack_rows(preview.token),
            school_name="XIV LO",
            default_supervisor_email=SUPERVISOR_EMAIL,
        )

    assert summary == {"created": 1, "existing": 2, "consent_requested": 1, "on_list": 1, "skipped": 0}
    recipients = sorted(address for message in mailoutbox for address in message.to)
    # Zaproszenie dla nowego i prośba o zgodę dla ucznia z innym opiekunem – „mój” uczeń nie dostaje nic.
    assert recipients == ["cudzy@example.test", "nowy@example.test"]
