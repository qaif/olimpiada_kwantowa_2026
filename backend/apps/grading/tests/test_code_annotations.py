"""Podgląd kodu i uwagi przypięte do linii: listing, walidacja kształtu adnotacji, jawność."""

import json

import pytest

from apps.core.api import DomainError
from apps.grading.code_view import (
    MAX_SOURCE_BYTES,
    TRUNCATION_NOTICE,
    add_line_note,
    code_listing,
    line_notes,
    notebook_source,
    public_line_notes,
    source_kind,
)
from apps.grading.models import ReviewStatus
from apps.grading.services import validate_annotations
from apps.grading.tests.conftest import locked_submission
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFileFactory

pytestmark = pytest.mark.django_db


def _open_review(stage, **kwargs):
    """Recenzja, którą wolno jeszcze zapisać: praca musi być **w ocenie**, nie tylko zablokowana."""
    submission = locked_submission(stage)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return ReviewFactory(submission=submission, **kwargs)


def _store(submission, payload: bytes, *, extension: str, mime: str):
    """Plik rozwiązania faktycznie zapisany na storage – podgląd czyta treść, nie metadane."""
    from io import BytesIO

    key = f"1/1/OLM-TEST/{submission.pk}-{extension}/{'b' * 64}.{extension}"
    get_submission_storage().put(key, BytesIO(payload), mime)
    return SubmissionFileFactory(
        submission=submission,
        object_key=key,
        mime=mime,
        av_status=AvStatus.CLEAN,
        size_bytes=len(payload),
    )


# --- rozpoznanie pliku --------------------------------------------------------------------------


def test_pdf_nie_jest_kodem(stage):
    submission = locked_submission(stage)
    file = SubmissionFileFactory(submission=submission, mime="application/pdf")

    assert source_kind(file) is None
    assert code_listing(file) is None


def test_rozpoznanie_po_typie_mime(stage):
    file = SubmissionFileFactory(submission=locked_submission(stage), mime="text/x-python")

    assert source_kind(file) == "py"


def test_rozpoznanie_po_rozszerzeniu_klucza_gdy_mime_jest_ogolny(stage):
    """Klucz obiektu buduje aplikacja, więc jego końcówka jest danymi z systemu, nie od uczestnika."""
    file = SubmissionFileFactory(
        submission=locked_submission(stage),
        mime="application/octet-stream",
        object_key=f"1/1/OLM-TEST/x/{'c' * 64}.ipynb",
    )

    assert source_kind(file) == "ipynb"


# --- listing ------------------------------------------------------------------------------------


def test_listing_numeruje_wiersze(stage):
    submission = locked_submission(stage)
    file = _store(submission, b"import math\nprint(math.pi)\n", extension="py", mime="text/x-python")

    listing = code_listing(file)

    assert [line.number for line in listing.lines] == [1, 2]
    assert listing.lines[0].text == "import math"


def test_linie_z_uwagami_sa_oznaczone(stage):
    submission = locked_submission(stage)
    file = _store(submission, b"a = 1\nb = 2\n", extension="py", mime="text/x-python")

    listing = code_listing(file, [{"line": 2, "text": "tu", "public": False}])

    assert [line.noted for line in listing.lines] == [False, True]


def test_plik_w_obcym_kodowaniu_nie_wywraca_podgladu(stage):
    """Dekodowanie z podmianą: rozwiązanie w cp1250 ma być czytelne, a nie zamienione w pustą stronę."""
    submission = locked_submission(stage)
    file = _store(submission, "x = 'zażółć'".encode("cp1250"), extension="py", mime="text/x-python")

    listing = code_listing(file)

    assert listing.error is None
    assert listing.lines[0].text.startswith("x = ")


def test_wielki_plik_jest_obcinany_z_widoczna_adnotacja(stage):
    submission = locked_submission(stage)
    payload = b"x = 1\n" * (MAX_SOURCE_BYTES // 6 + 100)
    file = _store(submission, payload, extension="py", mime="text/x-python")

    listing = code_listing(file)

    assert listing.truncated is True
    assert listing.lines[-1].text == TRUNCATION_NOTICE


def test_brak_pliku_na_storage_daje_listing_z_komunikatem(stage):
    """Podgląd jest wygodą – strona oceny musi stanąć także wtedy, gdy storage milczy."""
    file = SubmissionFileFactory(
        submission=locked_submission(stage),
        mime="text/x-python",
        object_key=f"1/1/OLM-TEST/nieistnieje/{'d' * 64}.py",
    )

    listing = code_listing(file)

    assert listing.lines == []
    assert "Nie udało się wczytać" in listing.error


# --- notatnik -----------------------------------------------------------------------------------


def test_notatnik_sklejamy_z_komorek_kodu_z_naglowkami():
    payload = json.dumps(
        {
            "cells": [
                {"cell_type": "markdown", "source": ["# Wstęp\n"]},
                {"cell_type": "code", "source": ["a = 1\n", "b = 2\n"]},
                {"cell_type": "code", "source": "print(a + b)\n"},
            ]
        }
    )

    text = notebook_source(payload)

    assert "# --- komórka 1 ---" in text
    assert "# --- komórka 2 ---" in text
    assert "# Wstęp" not in text
    assert "print(a + b)" in text


def test_notatnik_nie_do_sparsowania_wraca_jako_zwykly_tekst():
    """Uczestnik oddał plik i recenzent ma prawo zobaczyć, co w nim jest."""
    assert notebook_source("to nie jest JSON") == "to nie jest JSON"


# --- walidacja kształtu adnotacji ---------------------------------------------------------------


def test_stary_ksztalt_adnotacji_nadal_jest_poprawny():
    cleaned = validate_annotations([{"page": 2, "rect": [0, 0, 1, 1], "text": "t", "public": True}])

    assert cleaned == [{"page": 2, "rect": [0.0, 0.0, 1.0, 1.0], "text": "t", "public": True}]


def test_uwaga_do_linii_przechodzi_walidacje():
    cleaned = validate_annotations([{"line": 42, "text": "tu jest błąd", "public": True}])

    assert cleaned == [{"line": 42, "text": "tu jest błąd", "public": True}]


def test_numer_linii_musi_byc_dodatni():
    with pytest.raises(DomainError) as error:
        validate_annotations([{"line": 0, "text": "t"}])

    assert error.value.machine_code == "INVALID_ANNOTATIONS"


def test_oba_ksztalty_moga_stac_obok_siebie():
    cleaned = validate_annotations([{"line": 1, "text": "a"}, {"page": 1, "rect": [0, 0, 1, 1], "text": "b"}])

    assert len(cleaned) == 2


# --- dopisywanie i jawność ----------------------------------------------------------------------


def test_dopisanie_uwagi_zapisuje_ja_w_recenzji(stage):
    review = _open_review(stage)

    add_line_note(review, "7", "brak przypadku brzegowego", public=True)

    review.refresh_from_db()
    assert line_notes(review) == [{"line": 7, "text": "brak przypadku brzegowego", "public": True}]


def test_dopisanie_uwagi_nie_kasuje_prostokatow(stage):
    review = _open_review(
        stage,
        annotations=[{"page": 1, "rect": [0.0, 0.0, 0.5, 0.5], "text": "p", "public": False}],
    )

    add_line_note(review, 3, "uwaga")

    review.refresh_from_db()
    assert len(review.annotations) == 2


def test_uwaga_do_wystawionej_recenzji_to_odmowa(stage):
    """Bramka jest ta sama, co przy szkicu – ``_assert_review_open``."""
    review = ReviewFactory(submission=locked_submission(stage), status=ReviewStatus.SUBMITTED, score=6)

    with pytest.raises(DomainError) as error:
        add_line_note(review, 1, "za późno")

    assert error.value.machine_code == "REVIEW_ALREADY_SUBMITTED"


def test_pusta_tresc_uwagi_to_odmowa(stage):
    review = _open_review(stage)

    with pytest.raises(DomainError) as error:
        add_line_note(review, 1, "   ")

    assert error.value.machine_code == "ANNOTATION_TEXT_REQUIRED"


def test_numer_linii_musi_byc_liczba(stage):
    review = _open_review(stage)

    with pytest.raises(DomainError):
        add_line_note(review, "siódma", "uwaga")


def test_do_uczestnika_ida_wylacznie_uwagi_publiczne(stage):
    review = _open_review(stage)
    add_line_note(review, 1, "robocza", public=False)
    review.refresh_from_db()
    add_line_note(review, 2, "dla uczestnika", public=True)
    review.refresh_from_db()

    assert [item["line"] for item in public_line_notes(review)] == [2]


def test_uwagi_do_linii_nie_wchodza_do_adnotacji_prostokatnych(stage):
    """``public_annotations`` wypisuje się jako „str. N” – uwaga do linii wpadłaby tam jako „str. ”."""
    review = ReviewFactory(
        submission=locked_submission(stage),
        annotations=[
            {"line": 5, "text": "linia", "public": True},
            {"page": 1, "rect": [0.0, 0.0, 1.0, 1.0], "text": "prostokąt", "public": True},
        ],
    )

    assert [item["text"] for item in review.public_annotations()] == ["prostokąt"]
