"""Przekazywanie przyjętych rozwiązań na skrzynkę organizatora (prośba organizatora z 20.09.2026).

Testy patrzą na ``django.core.mail.outbox``, tak samo jak testy powiadomień: w trybie testowym
``CELERY_TASK_ALWAYS_EAGER`` wykonuje zadanie natychmiast, a backend ``locmem`` zapisuje wiadomość
zamiast otwierać gniazdo.

Czego te testy pilnują – po kolei tego, co w tej funkcji może boleć:

- **nic nie wychodzi, dopóki organizator nie wpisze adresu.** To jest stan domyślny każdego
  konkursu i zarazem jedyna bramka tej funkcji,
- **nie wychodzi plik, którego nie obejrzał antywirus.** Werdykt ``INFECTED`` nie przekazuje nic,
  a wysyłka wisi na ``CLEAN``, nie na przyjęciu pracy,
- **jeden plik to jeden list.** Powtórzone zadanie (ponowienie, które w rzeczywistości się udało)
  nie może dołożyć drugiej kopii załącznika,
- **prace nie przeciekają między konkursami.** Adres wpisany w konkursie A nie dostaje pracy
  oddanej w konkursie B – to ten sam warunek, co przy każdym innym odczycie (§ 3.5),
- **awaria poczty nie rusza przyjętej pracy ani jej skanu.** Werdykt ma zostać zapisany, a praca
  wejść do oceniania niezależnie od tego, czy MTA odpowiedział.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    EditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.core.models import AuditLog
from apps.submissions.antivirus import VERDICT_CLEAN, VERDICT_INFECTED
from apps.submissions.forwarding import TYPE_SUBMISSION_FORWARDED, forward_file
from apps.submissions.models import AvStatus, SubmissionFile, SubmissionStatus
from apps.submissions.services import apply_scan_verdict, create_submission
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import (
    PDF_BYTES,
    SubmissionFactory,
    SubmissionFileFactory,
    pdf_upload,
)

pytestmark = pytest.mark.django_db

FORWARD_TO = "komitet@example.invalid"
SUBJECT_MARK = "Nowe rozwiązanie:"


def forwarded_letters() -> list:
    """Listy przekazujące rozwiązanie – odsiane od poczty do uczestnika, która leci tą samą drogą."""
    return [item for item in mail.outbox if SUBJECT_MARK in item.subject]


def configure(competition, *addresses: str) -> None:
    """Wpisuje adresy przekazywania tak, jak robi to ekran panelu: jeden adres na wiersz."""
    competition.submission_forward_emails = "\n".join(addresses)
    competition.save(update_fields=["submission_forward_emails"])


def store(submission_file: SubmissionFile, content: bytes = PDF_BYTES) -> SubmissionFile:
    """Kładzie treść pliku w storage pod kluczem, który wiersz już nosi.

    Fabryka zapisuje **metadane**, a treść żyje wyłącznie w storage (``apps.submissions.models``),
    więc test, którego przedmiotem jest załącznik, musi ją tam położyć sam. Robi to tą samą drogą,
    co upload (``SubmissionStorage.put``), a nie zapisem do pliku – inaczej sprawdzałby własną
    znajomość układu katalogów zamiast zachowania serwisu.
    """
    get_submission_storage().put(submission_file.object_key, BytesIO(content), submission_file.mime)
    return submission_file


@pytest.fixture
def stage(competition):
    """Otwarty etap eliminacyjny Konkursu #1 z jednym zadaniem."""
    stage = StageFactory(edition=EditionFactory(competition=competition), competition=competition)
    ProblemFactory(stage=stage, number=1, title="Splątanie dwóch kubitów", competition=competition)
    return stage


@pytest.fixture
def entry(stage, competition):
    return StageEntryFactory(
        stage=stage,
        participant=ParticipantFactory(
            competition=competition,
            school="LO nr 5 w Warszawie",
        ),
        competition=competition,
    )


@pytest.fixture
def scanned_file(entry, stage):
    """Plik czekający na skan, z treścią leżącą w storage – wejście większości testów niżej."""
    submission = SubmissionFactory(entry=entry, problem=stage.problems.get(), version=1)
    return store(SubmissionFileFactory(submission=submission, av_status=AvStatus.PENDING))


# --- kiedy list w ogóle wychodzi -----------------------------------------------------------------


def test_clean_scan_forwards_the_file_with_an_attachment(competition, scanned_file, entry, stage):
    configure(competition, FORWARD_TO)

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    letter = forwarded_letters()[0]
    assert letter.to == [FORWARD_TO]
    assert letter.subject == (
        f"{competition.email_subject_prefix}Nowe rozwiązanie: {stage.display_name} – "
        f"zadanie 1 – {entry.participant.public_code}"
    )
    assert letter.attachments, "plik mieści się w granicy – powinien być w załączniku"
    name, content, _mime = letter.attachments[0]
    assert name.startswith(entry.participant.public_code)
    assert content == PDF_BYTES
    scanned_file.refresh_from_db()
    assert scanned_file.forwarded_at is not None


def test_the_body_carries_the_metadata_the_committee_needs(competition, scanned_file, entry, stage):
    configure(competition, FORWARD_TO)

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    body = forwarded_letters()[0].body
    assert stage.display_name in body
    assert "Zadanie: 1. Splątanie dwóch kubitów" in body
    assert entry.participant.public_code in body
    assert entry.participant.user.get_full_name() in body
    assert "LO nr 5 w Warszawie" in body
    assert "Wersja: 1" in body
    assert scanned_file.sha256 in body
    assert scanned_file.original_name in body
    assert "/coordinator/participants/" in body
    # Punktów ani recenzji w liście nie ma – skrzynka nie jest kanałem, w którym trzyma się ocenę.
    assert "pkt" not in body


def test_the_audit_entry_names_the_kind_and_the_count_but_no_address(competition, scanned_file):
    configure(competition, FORWARD_TO, "drugi@example.invalid")

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    entry = AuditLog.objects.get(action="notification.sent", diff__type=TYPE_SUBMISSION_FORWARDED)
    assert entry.diff["recipients"] == 2
    assert FORWARD_TO not in str(entry.diff)


def test_every_configured_address_gets_the_letter(competition, scanned_file):
    configure(competition, FORWARD_TO, "drugi@example.invalid")

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    assert forwarded_letters()[0].to == [FORWARD_TO, "drugi@example.invalid"]


def test_a_training_stage_is_forwarded_too(competition, entry, stage):
    """Trening jest piaskownicą całej ścieżki – tam właśnie organizator sprawdza, czy to działa."""
    configure(competition, FORWARD_TO)
    training = StageFactory(edition=stage.edition, kind=StageKind.TRAINING, competition=competition)
    problem = ProblemFactory(stage=training, number=1, competition=competition)
    training_entry = StageEntryFactory(stage=training, participant=entry.participant, competition=competition)
    submission = SubmissionFactory(entry=training_entry, problem=problem)
    scanned = store(SubmissionFileFactory(submission=submission, av_status=AvStatus.PENDING))

    apply_scan_verdict(scanned, VERDICT_CLEAN)

    assert len(forwarded_letters()) == 1


def test_a_new_version_is_forwarded_as_a_separate_letter(competition, entry, stage, scanned_file):
    configure(competition, FORWARD_TO)
    apply_scan_verdict(scanned_file, VERDICT_CLEAN)
    second = SubmissionFactory(entry=entry, problem=stage.problems.get(), version=2)
    newer = store(SubmissionFileFactory(submission=second, av_status=AvStatus.PENDING))

    apply_scan_verdict(newer, VERDICT_CLEAN)

    letters = forwarded_letters()
    assert len(letters) == 2
    assert "Wersja: 2" in letters[1].body


def test_the_whole_upload_path_ends_with_one_forwarded_letter(
    competition, entry, stage, django_capture_on_commit_callbacks
):
    """Droga produkcyjna od początku do końca: upload → kolejka skanu → czysty werdykt → list."""
    configure(competition, FORWARD_TO)

    with django_capture_on_commit_callbacks(execute=True):
        create_submission(user=entry.participant.user, stage=stage, problem_number=1, upload=pdf_upload())

    assert len(forwarded_letters()) == 1
    assert forwarded_letters()[0].attachments[0][1] == PDF_BYTES


# --- kiedy list nie wychodzi ----------------------------------------------------------------------


def test_nothing_is_forwarded_when_no_address_is_configured(competition, scanned_file):
    """Stan domyślny każdego konkursu: puste pole znaczy „nie przekazujemy”."""
    assert competition.forward_emails == []

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    assert forwarded_letters() == []
    scanned_file.refresh_from_db()
    assert scanned_file.forwarded_at is None


def test_an_infected_file_is_never_forwarded(competition, scanned_file):
    configure(competition, FORWARD_TO)

    apply_scan_verdict(scanned_file, VERDICT_INFECTED, "Eicar-Test-Signature")

    assert forwarded_letters() == []
    scanned_file.refresh_from_db()
    assert scanned_file.av_status == AvStatus.INFECTED
    assert scanned_file.forwarded_at is None


def test_an_unscanned_file_is_refused_by_the_task_itself(competition, scanned_file):
    """Zadanie wywołane wprost (ponowienie, ręczne uruchomienie) też sprawdza status skanu."""
    configure(competition, FORWARD_TO)

    assert forward_file(scanned_file.pk) == "SKIPPED"
    assert forwarded_letters() == []


def test_the_same_file_is_not_forwarded_twice(competition, scanned_file):
    configure(competition, FORWARD_TO)
    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    assert forward_file(scanned_file.pk) == "SKIPPED"
    assert len(forwarded_letters()) == 1


def test_a_deleted_file_ends_the_task_without_a_letter(competition, scanned_file):
    configure(competition, FORWARD_TO)
    file_id = scanned_file.pk
    SubmissionFile.objects.filter(pk=file_id).delete()

    assert forward_file(file_id) == "MISSING"
    assert forwarded_letters() == []


# --- granica załącznika ---------------------------------------------------------------------------


def test_a_file_above_the_limit_goes_without_an_attachment(competition, scanned_file, settings):
    """Plik ponad granicę: list wychodzi, ale z wyjaśnieniem i odnośnikiem zamiast pliku."""
    configure(competition, FORWARD_TO)
    SubmissionFile.objects.filter(pk=scanned_file.pk).update(
        size_bytes=(settings.SUBMISSION_FORWARD_MAX_ATTACHMENT_MB + 1) * 1024 * 1024
    )
    scanned_file.refresh_from_db()

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    letter = forwarded_letters()[0]
    assert letter.attachments == []
    assert "za duży" in letter.body
    assert "/coordinator/participants/" in letter.body


def test_lowering_the_limit_drops_the_attachment(competition, scanned_file, settings):
    """Granica jest ustawieniem instalacji i czytana przy każdym liście, a nie przy imporcie."""
    settings.SUBMISSION_FORWARD_MAX_ATTACHMENT_MB = 0
    configure(competition, FORWARD_TO)

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    assert forwarded_letters()[0].attachments == []


# --- izolacja konkursów ---------------------------------------------------------------------------


def test_the_other_competition_does_not_receive_anything(competition, other_competition, entry, stage):
    """Adres wpisany w Konkursie #1 nie dostaje pracy oddanej w konkursie sąsiada – i odwrotnie."""
    configure(competition, FORWARD_TO)
    other_entry = StageEntryFactory(competition=other_competition)
    other_problem = ProblemFactory(stage=other_entry.stage, competition=other_competition)
    other_file = store(
        SubmissionFileFactory(
            submission=SubmissionFactory(
                entry=other_entry, problem=other_problem, competition=other_competition
            ),
            av_status=AvStatus.PENDING,
            competition=other_competition,
        )
    )

    apply_scan_verdict(other_file, VERDICT_CLEAN)

    assert forwarded_letters() == []


def test_each_competition_uses_its_own_addresses(competition, other_competition, scanned_file):
    configure(competition, FORWARD_TO)
    configure(other_competition, "sasiad@example.invalid")

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    assert forwarded_letters()[0].to == [FORWARD_TO]


# --- awaria poczty ---------------------------------------------------------------------------------


class _DeadRelay:
    """Koperta, której nie da się wysłać – zastępuje ``EmailMessage`` w module przekazywania.

    Podmieniamy **symbol w module**, a nie metodę klasy Django: ta sama klasa składa też
    potwierdzenie przyjęcia pracy dla uczestnika, a przedmiotem tego testu jest awaria wyłącznie
    na drodze do organizatora.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def attach(self, *args) -> None:
        return None

    def send(self, **kwargs):
        raise OSError("MTA nie odpowiada")


def test_a_failing_relay_does_not_break_the_scan(competition, scanned_file, monkeypatch):
    configure(competition, FORWARD_TO)
    monkeypatch.setattr("apps.submissions.forwarding.EmailMessage", _DeadRelay)

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    scanned_file.refresh_from_db()
    assert scanned_file.av_status == AvStatus.CLEAN
    assert scanned_file.scanned_at is not None
    # Znacznik zostaje pusty, więc list da się wysłać ponownie bez ryzyka duplikatu.
    assert scanned_file.forwarded_at is None
    assert scanned_file.submission.status == SubmissionStatus.SUBMITTED
    assert forwarded_letters() == []


def test_a_missing_object_in_storage_does_not_break_the_scan(competition, entry, stage, monkeypatch):
    """Plik, którego nie ma w storage: skan już zapisany, a przekazanie zostaje w logu workera."""
    submission = SubmissionFactory(entry=entry, problem=stage.problems.get())
    orphan = SubmissionFileFactory(submission=submission, av_status=AvStatus.PENDING)
    configure(competition, FORWARD_TO)

    apply_scan_verdict(orphan, VERDICT_CLEAN)

    orphan.refresh_from_db()
    assert orphan.av_status == AvStatus.CLEAN
    assert orphan.forwarded_at is None
    assert forwarded_letters() == []


def test_the_marker_survives_a_second_scan_verdict(competition, scanned_file):
    """Idempotencja od strony skanu: powtórzony werdykt nie dokłada listu ani nie zeruje znacznika."""
    configure(competition, FORWARD_TO)
    apply_scan_verdict(scanned_file, VERDICT_CLEAN)
    scanned_file.refresh_from_db()
    first = scanned_file.forwarded_at

    apply_scan_verdict(scanned_file, VERDICT_CLEAN)

    scanned_file.refresh_from_db()
    assert scanned_file.forwarded_at == first
    assert len(forwarded_letters()) == 1
    assert first < timezone.now()
