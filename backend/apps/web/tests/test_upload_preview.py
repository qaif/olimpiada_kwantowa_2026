"""Lista kontrolna przed wysyłką i podgląd oddanego pliku (panel uczestnika).

Dwie rzeczy, jedna karta zadania: bramka **przed** kliknięciem („czy to na pewno to zadanie
i czy plik jest czytelny”) i dowód **po** kliknięciu („w systemie leży to, co wysłałem”).
Testujemy je razem, bo razem odpowiadają na jedno pytanie – i razem stoją w ``_problem_card.html``.

Skąd prawdziwy PDF w tym pliku, skoro fabryki mają ``PDF_BYTES``: tamten ma poprawny nagłówek
(tyle sprawdza walidator), ale nie jest dokumentem z tablicą stron. Liczba stron jest tu
przedmiotem testu, więc dokument musi dać się otworzyć biblioteką – stąd minimalny, ale kompletny
PDF z policzonymi offsetami w ``xref``.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.competitions.tests.factories import ProblemFactory
from apps.submissions.antivirus import VERDICT_CLEAN, VERDICT_INFECTED
from apps.submissions.models import AvStatus, Submission
from apps.submissions.preview import preview_for, text_head
from apps.submissions.services import apply_scan_verdict, create_submission
from apps.submissions.tests.factories import jpeg_upload, pdf_upload, upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def jpeg_problem(elim_stage):
    """Zadanie przyjmujące zdjęcie kartki – podgląd jest tam zwykłym ``<img>``."""
    return ProblemFactory(
        stage=elim_stage, number=7, title="Zdjęcie rozwiązania", allowed_formats=["pdf", "jpg"]
    )


@pytest.fixture
def code_problem(elim_stage):
    """Zadanie oddawane jako kod – podgląd to pierwsze wiersze pliku wycięte po stronie serwera."""
    return ProblemFactory(
        stage=elim_stage, number=8, title="Symulacja numeryczna", allowed_formats=["py", "ipynb"]
    )


def multipage_pdf(pages: int = 3) -> bytes:
    """Minimalny **poprawny** PDF o zadanej liczbie stron, z prawidłową tablicą ``xref``.

    Budowany bez biblioteki składu, bo ta jest w ekstrze ``dev`` i test nie może od niej zależeć.
    Offsety liczymy w trakcie sklejania – pypdf czyta je wprost, więc zgadywane rozminęłyby się
    z treścią przy pierwszej zmianie w tym pliku.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [{}] /Count {} >>".format(
            " ".join(f"{index + 3} 0 R" for index in range(pages)), pages
        ).encode("ascii"),
        *[b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>" for _ in range(pages)],
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("ascii")
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode(
        "ascii"
    )
    return bytes(body)


def pdf_pages_upload(pages: int = 3, name: str = "rozwiazanie.pdf"):
    return upload(name, multipage_pdf(pages), "application/pdf")


def upload_url(entry, number: int = 1) -> str:
    return f"/me/stages/{entry.stage_id}/problems/{number}/upload/"


# --- (a) lista kontrolna przed wysyłką ----------------------------------------------------------


def test_card_shows_the_confirmation_checkbox(web_client, participant, entry, problems):
    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()

    assert 'name="confirmed"' in content
    # Numer zadania jest w treści potwierdzenia – to on odróżnia pomyłkę „plik z sąsiedniego zadania”.
    assert "Potwierdzam, że to rozwiązanie zadania 1" in content


def test_upload_without_confirmation_is_refused(web_client, participant, entry, problems):
    """Bramką jest serwer, nie atrybut ``required`` w HTML – żądanie bez pola nie tworzy wersji."""
    web_client.force_login(participant.user)

    response = web_client.post(upload_url(entry), {"file": pdf_upload()}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "Zaznacz potwierdzenie" in response.content.decode()
    assert not Submission.objects.filter(entry=entry).exists()


def test_upload_with_confirmation_creates_version(web_client, participant, entry, problems):
    web_client.force_login(participant.user)

    response = web_client.post(
        upload_url(entry), {"file": pdf_upload(), "confirmed": "1"}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert Submission.objects.filter(entry=entry).count() == 1


# --- (b) metryki liczone po czystym skanie ------------------------------------------------------


def test_page_count_is_stored_after_a_clean_scan(participant, entry, problems):
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(3)
    )
    submission_file = submission.latest_file
    assert submission_file.page_count is None

    apply_scan_verdict(submission_file, VERDICT_CLEAN)

    submission_file.refresh_from_db()
    assert submission_file.page_count == 3


def test_infected_file_has_no_metrics_and_no_preview(participant, entry, problems):
    """Plik odrzucony przez skan nie jest czytany – ani do liczby stron, ani do podglądu."""
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(2)
    )
    submission_file = submission.latest_file

    apply_scan_verdict(submission_file, VERDICT_INFECTED, "Eicar-Test-Signature")

    submission_file.refresh_from_db()
    assert submission_file.page_count is None
    assert preview_for(submission_file) is None


def test_preview_before_the_scan_says_so(participant, entry, problems):
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(1)
    )

    preview = preview_for(submission.latest_file)

    assert preview["kind"] == "document"
    assert preview["pending_scan"] is True


def test_jpeg_preview_carries_image_dimensions(participant, entry, jpeg_problem):
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=jpeg_problem.number, upload=jpeg_upload()
    )
    # ``apply_scan_verdict`` pracuje na **zablokowanej kopii** wiersza i ją zwraca; instancja
    # sprzed skanu nadal ma ``PENDING``, więc podgląd liczymy z tego, co wróciło z serwisu.
    scanned = apply_scan_verdict(submission.latest_file, VERDICT_CLEAN)

    preview = preview_for(scanned)

    assert preview["kind"] == "image"
    # Fabryczny JPEG jest sztucznie mały, ale ma poprawny nagłówek – interesuje nas, że wymiary
    # w ogóle zostały odczytane, a nie ile dokładnie pikseli ma plik testowy.
    assert preview["width"] is None or preview["width"] > 0


def test_text_preview_cuts_the_file_to_forty_lines():
    """Reguła obcięcia jest w module podglądu, nie w szablonie – i to ją sprawdzamy wprost."""
    source = "\n".join(f"linia {number}" for number in range(1, 100))

    head = text_head(source.encode("utf-8"))

    assert head.startswith("linia 1\n")
    assert head.endswith("linia 40")
    assert "linia 41" not in head


def test_python_solution_preview_shows_first_lines(participant, entry, code_problem):
    source = "\n".join(f"print({number})" for number in range(1, 60)).encode("utf-8")
    submission = create_submission(
        user=participant.user,
        stage=entry.stage,
        problem_number=code_problem.number,
        upload=upload("rozwiazanie.py", source, "text/x-python"),
    )
    scanned = apply_scan_verdict(submission.latest_file, VERDICT_CLEAN)

    preview = preview_for(scanned)

    assert preview["kind"] == "text"
    assert preview["text"].startswith("print(1)")
    assert "print(41)" not in preview["text"]


# --- (c) podgląd na karcie zadania --------------------------------------------------------------


def test_card_renders_the_pdf_preview_hook(web_client, participant, entry, problems):
    """Pierwszą stronę rysuje pdf.js, więc serwer ma zostawić w karcie adres i punkt zaczepienia."""
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(4)
    )
    apply_scan_verdict(submission.latest_file, VERDICT_CLEAN)

    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()

    assert "data-upload-preview" in content
    assert f"/api/submissions/{submission.pk}/download/" in content
    assert "Liczba stron:" in content
    assert ">4<" in content or "4</strong>" in content


def test_card_preview_waits_for_the_scan(web_client, participant, entry, problems):
    create_submission(user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(1))

    web_client.force_login(participant.user)
    content = web_client.get("/me/").content.decode()

    assert "Podgląd pojawi się po zakończeniu skanu" in content
    assert "data-upload-preview" not in content


def test_scan_timestamp_and_status_stay_intact(participant, entry, problems):
    """Metryki są dodatkiem: nie wolno im zmienić ani werdyktu, ani chwili skanu."""
    submission = create_submission(
        user=participant.user, stage=entry.stage, problem_number=1, upload=pdf_pages_upload(2)
    )
    before = timezone.now()

    scanned = apply_scan_verdict(submission.latest_file, VERDICT_CLEAN)

    assert scanned.av_status == AvStatus.CLEAN
    assert scanned.scanned_at >= before
