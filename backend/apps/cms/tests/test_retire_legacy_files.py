"""``apps.cms.attachments.retire_documents`` i ``manage.py retire_legacy_files``.

Organizator kazał 21.09.2026 zdjąć PDF ze składem komitetów, ale strona ``/dokumenty/komitety/``
ma zostać **bez zmian** – ani nowej rewizji, ani przebudowanej treści. Te testy pilnują trzech
różnych dróg, którymi plik znika z instalacji:

- ``seed_legacy_content`` sprząta go jako efekt uboczny (pełnego albo ``--only``) przebiegu –
  dla instalacji, które i tak importują treść (``_retire_attachments``),
- ``retire_legacy_files`` sprząta go **bez** dotykania strony w ogóle – dla produkcji, na której
  pełny import nie chodzi po każdym wdrożeniu,
- ``build_guardian_consent_pdf --document komitety`` nadal umie złożyć wydruk na żądanie – plik
  po prostu nie wraca do repozytorium ani nie jest nigdzie przypinany.

Instalacja „sprzed 21.09” jest symulowana ręcznie: dokument o tytule wycofanym przez organizatora,
wgrany i przypięty do strony tak, jak zrobiłby to ``ensure_document``/``set_attachments`` na
starszej wersji ``seed_legacy_content``.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import ProtectedError
from wagtail.documents import get_document_model

from apps.cms.attachments import LABEL_PDF, ensure_document, retire_documents
from apps.cms.management.commands.build_guardian_consent_pdf import DOCUMENTS, render_pdf
from apps.cms.models import ContentPage, ContentPageAttachment, DocumentPage, DocumentPageAttachment

pytestmark = pytest.mark.django_db

#: Tytuł wycofany decyzją organizatora z 21.09.2026 – patrz ``LegacyPage.retired_pdf_titles``
#: w ``seed_legacy_content``. Ten sam napis, tożsamość dokumentu w bibliotece Wagtaila.
RETIRED_TITLE = "Skład komitetów Olimpiady Kwantowej (PDF)"


@pytest.fixture
def legacy_content():
    call_command("seed_legacy_content", verbosity=0)


def _attach_pdf(tmp_path, page, model, *, title: str):
    """Wgrywa mały plik pod danym tytułem i przypina go do ``page`` – bez ruszania innych wierszy.

    Osobno od ``apps.cms.attachments.set_attachments`` celowo: tamta funkcja ustawia listę
    załączników **w całości**, więc drugie wywołanie zdjęłoby to, co dołożył pierwszy. Tu chodzi
    o dołożenie kolejnego wiersza – dokładnie to, co robi redaktor, który ręcznie podpina drugi
    plik do tej samej strony w ``/cms/``.

    Nazwa pliku na dysku **nie** jest tytułem – ``ensure_document`` rozróżnia je tak samo (patrz
    jego docstring: tożsamością jest tytuł, nie nazwa pliku). Jeden z tytułów testowych niesie
    ukośnik („dołożony ręcznie w /cms/”), a `Path` czyta go jako separator katalogów, więc plik
    dostaje nazwę pochodną od licznika wywołań, nie od samego tytułu.
    """
    index = len(list(tmp_path.glob("*.pdf")))
    source = tmp_path / f"attachment-{index}.pdf"
    source.write_bytes(b"%PDF-1.4 zawartosc testowa\n")
    document, _ = ensure_document(title, source)
    sort_order = model.objects.filter(page=page).count()
    model.objects.create(page=page, document=document, label=LABEL_PDF, sort_order=sort_order)
    return document


def _editor():
    editor, _ = get_user_model().objects.get_or_create(
        email="redakcja-retire@example.org", defaults={"is_staff": True, "is_superuser": True}
    )
    return editor


# --- seed_legacy_content: sprzątanie jako efekt uboczny importu ---------------------------------


def test_seed_retires_the_committee_pdf_on_an_installation_that_still_has_it(
    tmp_path, legacy_content, django_capture_on_commit_callbacks
):
    """Instalacja sprzed 21.09.2026: dokument jeszcze wisi przy stronie – seed go sprząta."""
    page = DocumentPage.objects.get(slug="komitety")
    document = _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    file_name = document.file.name
    storage = document.file.storage

    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_legacy_content", "--only", "komitety", "--force", verbosity=0)

    page.refresh_from_db()
    assert not page.attachments.exists()
    assert not get_document_model().objects.filter(pk=document.pk).exists()
    assert not storage.exists(file_name)


def test_seed_second_run_is_a_no_op(tmp_path, legacy_content, django_capture_on_commit_callbacks):
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_legacy_content", "--only", "komitety", "--force", verbosity=0)

    # Drugi przebieg nie znajduje już nic do sprzątania – ma przejść bez błędu i bez zmian.
    call_command("seed_legacy_content", "--only", "komitety", "--force", verbosity=0)

    assert not page.attachments.exists()
    assert not get_document_model().objects.filter(title=RETIRED_TITLE).exists()


def test_a_manually_attached_document_with_a_different_title_survives(
    tmp_path, legacy_content, django_capture_on_commit_callbacks
):
    """Sprzątamy **po tytule**: plik dołożony ręcznie w /cms/ pod innym tytułem zostaje."""
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    other = _attach_pdf(
        tmp_path, page, DocumentPageAttachment, title="Skład komitetów – dołożony ręcznie w /cms/"
    )

    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_legacy_content", "--only", "komitety", "--force", verbosity=0)

    remaining = page.attachments.get()
    assert remaining.document.pk == other.pk
    assert get_document_model().objects.filter(pk=other.pk).exists()


def test_other_pages_pdfs_are_untouched(tmp_path, legacy_content, django_capture_on_commit_callbacks):
    """Sprzątanie „komitetów” nie rusza plików innych stron (tu: RODO)."""
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    rodo_attachment = DocumentPage.objects.get(slug="rodo").attachments.get()

    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_legacy_content", "--only", "komitety", "--force", verbosity=0)

    assert DocumentPageAttachment.objects.filter(pk=rodo_attachment.pk).exists()
    assert get_document_model().objects.filter(pk=rodo_attachment.document_id).exists()


# --- retire_legacy_files: sprzątanie bez seedu ---------------------------------------------------


def test_retire_legacy_files_removes_the_file_without_touching_the_page(
    tmp_path, legacy_content, django_capture_on_commit_callbacks
):
    """Plik znika, strona zostaje bajt w bajt – nawet gdy redakcja zdążyła ją zmienić w /cms/."""
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)

    edited_intro = "<p>Poprawka redakcji wprowadzona w /cms/ po imporcie.</p>"
    page.intro = edited_intro
    page.save_revision(user=_editor()).publish()
    page.refresh_from_db()
    revisions_before = page.revisions.count()
    revision_id_before = page.latest_revision_id
    published_before = page.last_published_at
    body_before = str(page.body)

    with django_capture_on_commit_callbacks(execute=True):
        call_command("retire_legacy_files", verbosity=0)

    page.refresh_from_db()
    assert not page.attachments.exists()
    assert not get_document_model().objects.filter(title=RETIRED_TITLE).exists()
    # Ani jednej nowej rewizji, ani zmiany treści – komenda nie woła ani ``save()``,
    # ani ``save_revision()`` na stronie.
    assert page.revisions.count() == revisions_before
    assert page.latest_revision_id == revision_id_before
    assert page.last_published_at == published_before
    assert str(page.body) == body_before
    assert edited_intro in str(page.intro)


def test_dry_run_removes_nothing(tmp_path, legacy_content):
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)

    call_command("retire_legacy_files", "--dry-run", verbosity=0)

    assert page.attachments.exists()
    assert get_document_model().objects.filter(title=RETIRED_TITLE).exists()


def test_second_run_reports_nothing_to_remove(
    tmp_path, legacy_content, django_capture_on_commit_callbacks, capsys
):
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)

    with django_capture_on_commit_callbacks(execute=True):
        call_command("retire_legacy_files", verbosity=1)
    capsys.readouterr()  # odrzucamy wydruk pierwszego przebiegu

    call_command("retire_legacy_files", verbosity=1)

    assert "nic do usunięcia" in capsys.readouterr().out


def test_a_page_missing_from_the_tree_is_skipped_not_an_error(legacy_content, capsys):
    """Instalacja, która nigdy nie miała tej strony (albo ją skasowano w /cms/) – bez wyjątku."""
    DocumentPage.objects.get(slug="komitety").delete()

    call_command("retire_legacy_files", verbosity=1)

    assert "strony nie ma w drzewie" in capsys.readouterr().out


# --- izolacja: tytuł jest tożsamością globalną w bibliotece --------------------------------------


def test_retire_documents_refuses_to_delete_a_document_shared_with_another_page(tmp_path, legacy_content):
    """Tytuł jest tożsamością **globalną** (patrz ``ensure_document``), nie per-strona ani per-konkurs.

    Ten sam tytuł podpięty także do innej strony (choćby innego konkursu, innej witryny Wagtaila)
    jest wciąż **tym samym** wierszem ``Document``. ``PageAttachment.document`` ma
    ``on_delete=PROTECT`` właśnie po to, żeby taka kolizja nazw nie ucichła: Django podnosi
    ``ProtectedError``, zamiast po cichu urwać plik spod nóg innej strony. Wiersz naszego
    załącznika i tak znika – to udokumentowana cena wywołania funkcji **bez** transakcji dookoła
    (patrz test niżej: komenda ``retire_legacy_files`` to samo wywołanie owija w
    ``@transaction.atomic``, więc na tym poziomie taki częściowy skutek nie zostaje).
    """
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    other_page = ContentPage.objects.get(slug="dla-nauczycieli")
    shared_document = get_document_model().objects.get(title=RETIRED_TITLE)
    ContentPageAttachment.objects.create(
        page=other_page, document=shared_document, label=LABEL_PDF, sort_order=0
    )

    with pytest.raises(ProtectedError):
        retire_documents(page, DocumentPageAttachment, [RETIRED_TITLE])

    assert not DocumentPageAttachment.objects.filter(page=page).exists()
    assert get_document_model().objects.filter(pk=shared_document.pk).exists()
    assert ContentPageAttachment.objects.filter(page=other_page, document=shared_document).exists()


def test_retire_legacy_files_rolls_back_entirely_when_a_document_is_shared(
    tmp_path, legacy_content, other_competition, django_capture_on_commit_callbacks
):
    """Na poziomie komendy ta sama kolizja nie zostawia stanu „pół sprzątnięte”.

    ``other_competition`` daje **inną** witrynę Wagtaila (inny konkurs) z własnym korzeniem drzewa
    – dokładnie scenariusz „strona innego konkursu” z docstringu ``retire_documents``. Cała komenda
    stoi w jednej transakcji, więc podniesiony ``ProtectedError`` cofa też odpięcie zdążone dla
    naszej strony.
    """
    page = DocumentPage.objects.get(slug="komitety")
    _attach_pdf(tmp_path, page, DocumentPageAttachment, title=RETIRED_TITLE)
    other_page = other_competition.site.root_page.add_child(
        instance=ContentPage(title="Inna strona innego konkursu", slug="inna-strona-innego-konkursu")
    )
    shared_document = get_document_model().objects.get(title=RETIRED_TITLE)
    ContentPageAttachment.objects.create(
        page=other_page, document=shared_document, label=LABEL_PDF, sort_order=0
    )

    with pytest.raises(ProtectedError), django_capture_on_commit_callbacks(execute=True):
        call_command("retire_legacy_files", verbosity=0)

    page.refresh_from_db()
    assert page.attachments.exists()
    assert get_document_model().objects.filter(pk=shared_document.pk).exists()
    assert ContentPageAttachment.objects.filter(page=other_page, document=shared_document).exists()


# --- wydruk na żądanie (build_guardian_consent_pdf) ----------------------------------------------


def test_committee_pdf_can_still_be_built_on_demand():
    """Wynik nie leży już w repozytorium, ale komenda nadal umie go złożyć z ``komitety.md``."""
    spec = DOCUMENTS["komitety"]

    data = render_pdf(
        spec["source"].read_text(encoding="utf-8"),
        title=spec["title"],
        subject=spec["subject"],
        footer_left=spec["footer_left"],
    )

    assert data.startswith(b"%PDF")
    from pypdf import PdfReader

    text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    assert "Jakub Mielczarek" in text
    assert "Karol Życzkowski" in text


def test_build_command_writes_to_output_and_not_into_the_repository(tmp_path):
    """``--output`` decyduje, gdzie ląduje plik – repozytorium go już nie przechowuje."""
    spec = DOCUMENTS["komitety"]
    repository_output = spec["output"]
    target = tmp_path / "sklad-komitetow.pdf"
    assert not repository_output.exists(), "plik wycofano z repozytorium – ma tak zostać"

    call_command("build_guardian_consent_pdf", "--document", "komitety", "--output", str(target), verbosity=0)

    assert target.exists()
    assert target.read_bytes().startswith(b"%PDF")
    assert not repository_output.exists()
