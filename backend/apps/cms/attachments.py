"""Wgrywanie plików organizatora do biblioteki Wagtaila i przypinanie ich do stron.

Moduł jest wspólny dla ``seed_regulamin`` i ``seed_legacy_content``: obie komendy dokładają
załączniki do tych samych stron (regulamin ma PDF z jednej i .docx z drugiej), więc reguła
„czym jest ten sam plik” musi być jedna.

Trzy decyzje warte uzasadnienia:

- **tożsamość pliku to jego tytuł w bibliotece.** Nie ścieżka źródłowa (ta zmienia się przy
  reorganizacji repozytorium) i nie nazwa pliku (Wagtail dokleja do niej sufiks przy kolizji).
  Tytuł jest tym, co redaktor widzi w ``/cms/`` i po czym szuka,
- **ta sama treść nie jest wgrywana drugi raz, inna – jest.** Porównujemy SHA-256 zawartości.
  Bez tego powtórzony przebieg albo mnożyłby kopie w buckecie (gdyby zawsze wgrywać), albo
  zostawiałby nieaktualny plik pod aktualnym tytułem (gdyby nigdy nie wgrywać). Podmieniamy
  zawartość istniejącego rekordu, więc identyfikator dokumentu – a z nim adres ``/documents/<id>/…``
  z linków i pism – zostaje ten sam,
- **lista załączników strony jest ustawiana w całości**, a nie doklejana. Komenda importująca
  opisuje docelowy stan strony; doklejanie dawałoby przy drugim przebiegu dwa te same wiersze
  albo kolejność zależną od tego, którą komendę uruchomiono wcześniej.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from django.core.files import File
from wagtail.documents import get_document_model

from apps.cms.permissions import upload_collection

#: ``…/apps/cms/`` → ``…/apps/cms/fixtures/legacy/pdf/``. Oficjalne PDF-y organizatora.
PDF_DIR = Path(__file__).resolve().parent / "fixtures" / "legacy" / "pdf"

#: Pliki **złożone w repozytorium** z treści, którą sami piszemy – dziś jeden: formularz zgody
#: opiekuna składany komendą ``build_guardian_consent_pdf`` z ``fixtures/legacy/zgoda-opiekuna.md``.
#: Katalog jest osobny od ``PDF_DIR`` celowo: tamte pliki przyszły podpisane z zewnątrz i nie wolno
#: ich odtworzyć, ten powstaje z pliku źródłowego jedną komendą i przy zmianie treści trzeba go
#: przebudować. Pomylenie tych dwóch rzeczy kończy się albo nadpisaniem dokumentu organizatora,
#: albo formularzem, który rozjechał się ze stroną.
GENERATED_PDF_DIR = Path(__file__).resolve().parent / "fixtures" / "documents"

#: Etykiety ról pliku – ta sama treść w obu komendach i w migracji danych ``cms.0007``.
LABEL_PDF = "PDF do druku"
LABEL_SOURCE_DOCX = "Wersja źródłowa (DOCX)"

#: Rozmiar porcji przy liczeniu skrótu. Największy plik to 13-stronicowy regulamin (~230 kB),
#: ale czytanie strumieniem nie kosztuje nic, a nie zakłada niczego o rozmiarze przyszłych plików.
CHUNK = 64 * 1024


def _digest(handle) -> str:
    sha = hashlib.sha256()
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        sha.update(chunk)
    return sha.hexdigest()


def _refresh_metadata(document) -> None:
    """Uzupełnia ``file_size`` i ``file_hash`` dokumentu.

    Wagtail liczy oba w formularzu biblioteki (``_set_document_file_metadata``), a nie przy
    ``file.save()`` – dokument wgrany komendą ma je puste. Rozmiar widać wtedy na karcie
    „Do pobrania” jako „0 bajtów”, a pusty skrót trafia do nagłówka ``ETag`` widoku
    serwującego (``wagtail.documents.views.serve.document_etag``), czyli do tego samego
    nagłówka, na którym stoi buforowanie z ``apps.cms.views``. Zerowanie przed odczytem jest
    konieczne, bo obie metody liczą wartość dopiero wtedy, gdy pola są puste – a po podmianie
    treści pliku stara wartość jest nieprawdziwa.
    """
    document.file_size = None
    document.file_hash = ""
    document.get_file_size()
    document.get_file_hash()


def ensure_document(title: str, source: Path, *, competition=None):
    """Dokument o zadanym tytule w kolekcji konkursu, z zawartością pliku ``source``.

    Zwraca ``(document, action)``, gdzie ``action`` to ``"created"``, ``"updated"`` albo
    ``"unchanged"`` – komendy raportują to na stdout, żeby przebieg dało się przeczytać.

    **Kolekcja dotyczy wyłącznie dokumentu zakładanego.** ``apps.cms.permissions.upload_collection``
    oddaje kolekcję konkursu przy włączonej fladze ``scoped_cms_permissions``, a korzeń wtedy, gdy
    flaga jest wyłączona (Konkurs #1, stan dzisiejszy) albo konkursu nie da się rozstrzygnąć.
    Dokument **już wgrany zostaje w swojej kolekcji**: podmieniamy zawartość rekordu, a nie jego
    miejsce w bibliotece (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5).
    """
    Document = get_document_model()
    document = Document.objects.filter(title=title).first()
    with source.open("rb") as handle:
        wanted = _digest(handle)

    if document is None:
        document = Document(title=title, collection=upload_collection(competition))
        with source.open("rb") as handle:
            document.file.save(source.name, File(handle), save=True)
        _refresh_metadata(document)
        return document, "created"

    try:
        with document.file.open("rb") as handle:
            current = _digest(handle)
    except FileNotFoundError, OSError:
        # Rekord bez pliku w storage (przeniesiona baza, wyczyszczony bucket) – wgrywamy na nowo.
        current = None
    if current == wanted:
        if not document.file_hash or not document.file_size:
            _refresh_metadata(document)
        return document, "unchanged"

    with source.open("rb") as handle:
        document.file.save(source.name, File(handle), save=True)
    _refresh_metadata(document)
    return document, "updated"


def set_attachments(page, model, specs: list[tuple[object, str]]) -> None:
    """Ustawia listę plików strony dokładnie na ``specs`` – kolejność z listy jest kolejnością na stronie.

    ``specs`` to pary ``(document, label)``. Wiersze spoza listy znikają, istniejące dostają nowy
    ``sort_order`` – dzięki temu druga komenda nie musi wiedzieć, co dołożyła pierwsza, wystarczy,
    że obie opisują ten sam stan docelowy.
    """
    wanted = {document.pk: (index, label) for index, (document, label) in enumerate(specs)}
    existing = {row.document_id: row for row in model.objects.filter(page=page)}

    for document_id, row in existing.items():
        if document_id not in wanted:
            row.delete()

    for document, label in specs:
        index, _ = wanted[document.pk]
        row = existing.get(document.pk)
        if row is None:
            model.objects.create(page=page, document=document, label=label, sort_order=index)
            continue
        row.label = label
        row.sort_order = index
        row.save(update_fields=["label", "sort_order"])


def retire_documents(page, model, titles: Iterable[str]) -> int:
    """Odpina od ``page`` i kasuje z biblioteki (wraz z plikiem) dokumenty o wskazanych tytułach.

    Wspólna dla ``seed_legacy_content`` (``_retire_attachments``) i komendy ``retire_legacy_files``:
    obie sprzątają plik, który organizator kazał zdjąć ze strony, nie ruszając samej strony ani
    dokumentów o innych tytułach – nawet dołożonych do tej samej strony ręcznie w ``/cms/``.
    Bezpieczna, gdy nie ma czego sprzątać: zwraca wtedy ``0`` i nic nie zmienia, więc wywołanie
    tej funkcji drugi raz (albo na instalacji, która pliku nigdy nie miała) jest równie tanim
    „nic się nie stało" co przeliczenie.

    Kolejność ewaluacji ma znaczenie: dokumenty czytamy **do listy** ``documents`` najpierw, zanim
    skasujemy jakikolwiek wiersz – dopiero ta lista (a nie leniwy queryset przeliczany drugi raz
    po tym, jak stan bazy już się zmienił) jest podstawą i filtra po wierszach załączników,
    i pętli kasującej same dokumenty.

    Plik ze storage nie jest kasowany tu wprost. ``document.delete()`` wystarcza: Wagtail podpina
    pod sygnał ``post_delete`` modelu dokumentu zadanie ``delete_file_from_storage_task``
    (``wagtail/documents/signal_handlers.py``), zaplanowane na ``transaction.on_commit`` – wołanie
    tu dodatkowo ``document.file.delete()`` kasowałoby ten sam plik drugi raz. W testach zadanie
    trzeba jawnie wykonać (``django_capture_on_commit_callbacks(execute=True)``), bo w transakcji
    testu ``on_commit`` się nie odpala.

    **Tytuł jest tożsamością globalną w całej bibliotece** – ``ensure_document`` szuka dokumentu
    po tytule bez względu na stronę czy konkurs (patrz jego docstring), więc dokument o tym samym
    tytule podpięty także do **innej** strony jest wciąż tym samym wierszem ``Document``. Wiersz
    załącznika odpinamy tylko dla ``page`` przekazanej tutaj, ale ``document.delete()`` działa na
    całej bibliotece: FK ``PageAttachment.document`` ma ``on_delete=PROTECT`` właśnie po to, żeby
    to nie uszło płazem – jeśli inna strona nadal ma załącznik z tym samym dokumentem, Django
    podniesie ``ProtectedError`` zamiast po cichu urwać jej plik spod nóg. To zamierzone: dwie
    strony z plikiem o identycznym tytule w bibliotece to kolizja nazw do rozwiązania ręcznie
    (zmiana tytułu jednego z nich), a nie coś, co ta funkcja ma cicho przemilczeć.
    """
    if not titles:
        return 0
    Document = get_document_model()
    documents = list(Document.objects.filter(title__in=titles))
    if not documents:
        return 0
    document_ids = [document.pk for document in documents]
    model.objects.filter(page=page, document_id__in=document_ids).delete()
    for document in documents:
        document.delete()
    return len(documents)
