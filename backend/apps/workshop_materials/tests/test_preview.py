"""Podgląd Markdownu, tekstu i kodu na stronie materiału (WM-FMT-01) – i serwowanie tekstu do pobrania.

Przedmiotem jest to, co mogłoby pójść źle, gdy plik od koordynatora pokazujemy innym na **naszej**
stronie (a nie w osobnym originie magazynu):

- Markdown: surowy HTML, ``onerror=`` i ``javascript:`` nie stają się znacznikami ani atrybutami,
- kod i tekst (także ``html``/``svg``): wyłącznie uciekany tekst w ``<pre><code>``,
- limit podglądu i budżet składania (plik złośliwie „drogi” nie zajmuje wątku na minuty),
- podgląd widzi dokładnie ten, kto może pobrać plik – i tylko gotowy, opublikowany materiał,
- pobranie tekstu, HTML-a i SVG to zawsze ``attachment`` z ``text/plain``, nigdy ``inline``.
"""

from __future__ import annotations

import time

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import ParticipantFactory
from apps.tenancy.tests.factories import grant_membership
from apps.workshop_materials import preview as preview_module
from apps.workshop_materials.models import MaterialKind, MaterialStatus, WorkshopMaterial

from .helpers import FakeMaterialStorage, enable, make_material, workshops_page_for

LIST = "/warsztaty/materialy/"

EVIL_MARKDOWN = (
    b"# Bramki\n\n"
    b"<script>alert('md')</script>\n\n"
    b'<img src="x" onerror="alert(1)">\n\n'
    b"[kliknij](javascript:alert(1)) i ![obraz](javascript:alert(2))\n\n"
    b"**Hadamard** to $H = \\frac{1}{\\sqrt{2}}$.\n"
)


def material(file_format: str, content: bytes, **fields) -> WorkshopMaterial:
    """Materiał w pamięci (bez bazy) z treścią w magazynie testowym."""
    key = f"workshop-materials/1/podglad.{file_format}"
    FakeMaterialStorage.objects[key] = content
    return WorkshopMaterial(
        pk=1,
        kind=MaterialKind.FILE,
        file_format=file_format,
        size_bytes=len(content),
        object_key=key,
        status=MaterialStatus.READY,
        **fields,
    )


def build(file_format: str, content: bytes):
    return preview_module.build_preview(material(file_format, content), FakeMaterialStorage())


# --- skład ------------------------------------------------------------------------------------------


def test_markdown_is_rendered_and_raw_html_stays_inert_text():
    result = build("md", EVIL_MARKDOWN)

    html = str(result.html)
    assert result.kind == preview_module.SHOWN_MARKDOWN
    assert "<h2>Bramki</h2>" in html
    assert "<strong>Hadamard</strong>" in html
    assert 'class="tr-math"' in html
    # Surowy HTML z pliku jest tekstem, nie znacznikiem – a odnośników konwerter nie tworzy wcale.
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "<img" not in html
    assert 'onerror="' not in html
    assert "href=" not in html


def test_code_is_kept_as_plain_text_for_the_escaping_template():
    result = build("html", b"<!doctype html><script>alert(1)</script>\n")

    assert result.kind == preview_module.SHOWN_TEXT
    assert result.text.startswith("<!doctype html>")
    assert result.html == ""


def test_bom_nul_and_invalid_utf8_beyond_the_probe_are_cleaned_up():
    result = build("txt", b"\xef\xbb\xbfpocz\xc4\x85tek\x00 i \xff koniec")

    assert result.text == "początek i � koniec"


def test_formats_without_a_preview_get_none():
    assert build("pdf", b"%PDF-1.4") is None
    assert build("docx", b"PK\x03\x04") is None
    assert build("ipynb", b"{}") is None


def test_file_over_the_limit_is_not_read(settings):
    settings.WORKSHOP_PREVIEW_MAX_KB = 1
    item = material("py", b"x = 1\n" * 400)
    del FakeMaterialStorage.objects[item.object_key]  # odczyt rzuciłby KeyError

    result = preview_module.build_preview(item, FakeMaterialStorage())

    assert result.kind == preview_module.TOO_LARGE
    assert result.limit_bytes == 1024
    assert not result.shown


def test_storage_failure_degrades_to_download_only():
    item = material("md", b"# x")
    del FakeMaterialStorage.objects[item.object_key]

    result = preview_module.build_preview(item, FakeMaterialStorage())

    assert result.kind == preview_module.UNAVAILABLE


def test_pathological_markdown_falls_back_to_plain_text_quickly():
    # Bez budżetu ten wiersz (50 000 niedomkniętych „**”) składał się ponad dwie minuty.
    evil = b"**a " * 50_000

    started = time.monotonic()
    result = build("md", evil)

    assert time.monotonic() - started < 5
    assert result.kind == preview_module.SHOWN_TEXT
    assert result.plain_fallback is True


def test_ordinary_markdown_stays_well_under_the_budget():
    chapter = "## Rozdział\n\nTekst **pogrubiony**, *kursywa*, `kod` i $x$.\n\n```python\nprint(1)\n```\n\n"
    text = chapter * 10_000  # ok. 750 KB

    assert preview_module.markdown_cost(text) < preview_module.MARKDOWN_COST_BUDGET // 5


# --- widok: dostęp, liczenie i nagłówki pobrania --------------------------------------------------------


@pytest.fixture
def ready(competition):
    enable(competition)
    workshops_page_for(competition)
    return competition


def detail(item) -> str:
    return f"{LIST}{item.pk}/"


def open_url(item) -> str:
    return f"{LIST}{item.pk}/pobierz/"


@pytest.mark.django_db
def test_participant_sees_the_markdown_preview_with_katex(participant_client, ready):
    item = make_material(ready, kind="file", file_format="md", content=EVIL_MARKDOWN, title="Notatki")

    response = participant_client.get(detail(item))

    content = response.content.decode()
    assert response.status_code == 200
    assert "<strong>Hadamard</strong>" in content
    assert "&lt;script&gt;alert(&#x27;md&#x27;)" in content
    assert "<script>alert" not in content
    assert 'href="javascript' not in content
    assert "problem_translations/vendor/katex/katex.min.js" in content
    assert "no-store" in response["Cache-Control"]
    item.refresh_from_db()
    assert item.view_count == 1


@pytest.mark.django_db
def test_code_preview_is_escaped(participant_client, ready):
    code = b'<svg onload="alert(1)"><script>alert(2)</script></svg>\n'
    item = make_material(ready, kind="file", file_format="svg", content=code, title="Rysunek")

    content = participant_client.get(detail(item)).content.decode()

    assert '<pre class="workshop-preview workshop-preview--code"><code>' in content
    assert "&lt;svg onload=&quot;alert(1)&quot;&gt;&lt;script&gt;" in content
    assert "<svg onload" not in content
    assert "katex.min.js" not in content


@pytest.mark.django_db
def test_too_large_file_has_only_the_download(participant_client, ready, settings):
    settings.WORKSHOP_PREVIEW_MAX_KB = 1
    item = make_material(ready, kind="file", file_format="txt", content=b"a\n" * 1024, title="Dane")

    response = participant_client.get(detail(item))

    content = response.content.decode()
    assert "za duży na podgląd" in content
    assert "workshop-preview--code" not in content
    item.refresh_from_db()
    assert item.view_count == 0


@pytest.mark.django_db
def test_preview_has_the_same_gates_as_the_download(client_for, participant_client, ready, other_competition):
    item = make_material(ready, kind="file", file_format="md", content=b"# Tajne", title="Notatki")
    draft = make_material(
        ready, kind="file", file_format="md", content=b"# Szkic", title="Szkic", published=False
    )
    scanning = make_material(
        ready, kind="file", file_format="md", content=b"# Skan", title="Skan", status=MaterialStatus.SCANNING
    )

    anonymous = client_for(ready).get(detail(item))
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith("/login/?next=")

    stranger = ParticipantFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)
    client = client_for(ready)
    client.force_login(stranger.user)
    assert client.get(detail(item)).status_code == 403

    for hidden in (draft, scanning):
        assert participant_client.get(detail(hidden)).status_code == 404
        assert participant_client.get(open_url(hidden)).status_code == 404
    assert "Tajne" in participant_client.get(detail(item)).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("file_format", ["html", "svg", "py", "md"])
def test_text_download_is_a_plain_text_attachment(participant_client, ready, file_format):
    item = make_material(
        ready, kind="file", file_format=file_format, content=b"<script>alert(1)</script>", title="Plik"
    )

    location = participant_client.get(open_url(item))["Location"]

    expected_type = "text/markdown" if file_format == "md" else "text/plain"
    assert f"response-content-type={expected_type}%3B%20charset%3Dutf-8" in location
    assert "response-content-disposition=attachment" in location
    assert "inline" not in location
    assert f"plik.{file_format}" in location


@pytest.mark.django_db
def test_list_offers_view_and_download_for_previewable_files(participant_client, ready):
    item = make_material(ready, kind="file", file_format="py", content=b"print(1)\n", title="Skrypt")

    content = participant_client.get(LIST).content.decode()

    assert f'href="{detail(item)}"' in content
    assert f'href="{open_url(item)}"' in content
    assert "Python (PY)" in content


@pytest.mark.django_db
def test_macro_formats_carry_a_neutral_note(participant_client, ready):
    item = make_material(ready, kind="file", file_format="docm", content=b"PK\x03\x04", title="Makra")

    content = participant_client.get(detail(item)).content.decode()

    assert "może zawierać makra" in content
    assert "Word z makrami (DOCM)" in content


@pytest.mark.django_db
def test_coordinator_previews_a_draft_without_counting_it(coordinator_client, ready):
    item = make_material(
        ready, kind="file", file_format="md", content=b"# Szkic **gotowy**", title="Szkic", published=False
    )

    response = coordinator_client.get(f"/coordinator/workshops/materials/{item.pk}/preview/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "<strong>gotowy</strong>" in content
    # Szkicu nie wyda ``workshop-material-open`` – przycisk prowadzi wprost na podpis.
    assert "https://s3.test/submissions/" in content
    assert "no-store" in response["Cache-Control"]
    item.refresh_from_db()
    assert item.view_count == 0


@pytest.mark.django_db
def test_coordinator_preview_of_a_pdf_still_redirects(coordinator_client, ready):
    item = make_material(ready, kind="file", title="Slajdy")

    response = coordinator_client.get(f"/coordinator/workshops/materials/{item.pk}/preview/")

    assert response.status_code == 302
    assert "response-content-type=application/pdf" in response["Location"]


# --- kodowanie CSV/TSV/TXT ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("charset", "raw"),
    [
        ("windows-1250", "miasto;liczba\nŁódź;3\n".encode("cp1250")),
        ("utf-16", "miasto;liczba\nŁódź;3\n".encode("utf-16")),  # z BOM-em
        ("", "﻿miasto;liczba\nŁódź;3\n".encode()),
    ],
)
def test_preview_decodes_the_charset_detected_at_upload(charset, raw):
    item = material("csv", raw, charset=charset)

    result = preview_module.build_preview(item, FakeMaterialStorage())

    assert result.text == "miasto;liczba\nŁódź;3\n"


@pytest.mark.django_db
@pytest.mark.parametrize("charset", ["windows-1250", "utf-16"])
def test_legacy_csv_download_names_its_charset(participant_client, ready, charset):
    item = make_material(
        ready,
        kind="file",
        file_format="csv",
        content="Łódź;3\n".encode("cp1250" if charset == "windows-1250" else "utf-16"),
        title="Wyniki",
        charset=charset,
    )

    location = participant_client.get(open_url(item))["Location"]
    content = participant_client.get(detail(item)).content.decode()

    assert f"response-content-type=text/plain%3B%20charset%3D{charset}" in location
    assert "response-content-disposition=attachment" in location
    assert "Łódź;3" in content
