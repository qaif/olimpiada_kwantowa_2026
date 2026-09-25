"""Metryki i podgląd oddanego pliku – to, co uczestnik widzi **po** wysyłce.

Po co to w ogóle: między „wysłałem” a „oceniono” uczestnik nie ma dziś żadnego dowodu, że
w buckecie leży to, co chciał wysłać. Najczęstsze pomyłki nie są pomyłkami systemu – to skan
zrobiony tyłem, PDF z dwiema stronami zamiast pięciu i notatnik wygenerowany z pustej sesji.
Każda z nich jest widoczna w pierwszej sekundzie, jeśli tylko pokazać stronę tytułową i liczby.

Zasady modułu:

- **czytamy dopiero po czystym skanie**. Plik świeżo wgrany jest danymi od nieznanego nadawcy;
  parsowanie go tą samą biblioteką, którą potem pokaże się recenzentowi, byłoby wykonaniem
  roboty za napastnika. ``store_page_count`` woła ``apps.submissions.services.apply_scan_verdict``
  i tylko dla werdyktu ``CLEAN``,
- **liczba stron PDF-a jest kolumną w bazie** (``SubmissionFile.page_count``). To jedyna metryka,
  której policzenie wymaga przeczytania całego dokumentu (tablica stron leży na jego końcu),
  a czytają ją panel uczestnika i ekrany komitetu – liczenie jej przy każdym renderowaniu byłoby
  pobraniem pliku z S3 na każde wejście do panelu,
- **reszta metryk jedzie przez podręczną pamięć** (``django.core.cache``) pod kluczem z sumy
  sha256 pliku. Suma jest tu naturalnym kluczem: zmiana treści to nowy plik i nowy klucz, więc
  wpis nie ma jak się zdezaktualizować, a ta sama wersja pracy nie jest czytana ze storage
  dwa razy,
- **podgląd nie jest nigdy warunkiem niczego**. Każda funkcja tego modułu zwraca ``None`` zamiast
  podnosić wyjątek: brak biblioteki, uszkodzony nagłówek albo niedostępny storage mają skończyć
  się brakiem miniatury, a nie błędem 500 na panelu uczestnika, którego praca jest już przyjęta.

Czego tu **nie ma**: renderowania PDF-a po stronie serwera. Pierwszą stronę rysuje pdf.js
w przeglądarce uczestnika (``static/js/upload-preview.js``) – to jego własny plik, więc nie ma
powodu, żeby serwer składał z niego obrazek i trzymał go w kolejnym buckecie.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from .models import AvStatus, SubmissionFile
from .storage import get_submission_storage
from .validators import declared_extension

logger = logging.getLogger(__name__)

#: Ile wierszy kodu pokazujemy z notatnika i ze skryptu. Czterdzieści to mniej więcej jeden ekran:
#: tyle wystarcza, żeby rozpoznać własny plik, i za mało, żeby panel stał się czytnikiem pracy.
TEXT_PREVIEW_LINES = 40

#: Ile bajtów wolno przeczytać, żeby te czterdzieści wierszy wyciąć. Notatnik bywa megabajtowy
#: (base64 z wykresami), a nas interesuje wyłącznie jego początek.
TEXT_PROBE_BYTES = 256 * 1024

#: Ważność wpisu w pamięci podręcznej. Doba, bo klucz zawiera sumę sha256 pliku – wpis nie może
#: skłamać, a jedynie wygasnąć; krótszy czas znaczyłby po prostu częstsze czytanie z S3.
CACHE_TTL_SECONDS = 24 * 3600

CACHE_KEY_PREFIX = "submissions.preview"

#: Rodzaj podglądu na format pliku. ``document`` rysuje pdf.js, ``image`` jest zwykłym ``<img>``,
#: ``text`` to początek pliku wycięty po stronie serwera. Format spoza tej mapy podglądu nie ma –
#: i to jest poprawny stan, a nie brak funkcji.
PREVIEW_KINDS = {"pdf": "document", "jpg": "image", "ipynb": "text", "py": "text"}


def preview_kind(submission_file: SubmissionFile) -> str:
    """Rodzaj podglądu dla tego pliku (``document`` / ``image`` / ``text``) albo pusty napis.

    Rozstrzyga rozszerzenie **znormalizowane przez walidator** (``declared_extension``), a nie
    ``mime``: obie wartości zapisuje ten sam serwis z tego samego źródła, ale rozszerzenie jest
    tym, po czym rozpoznaje plik cała reszta systemu (formaty zadania, nazwa w paczce ZIP).
    """
    return PREVIEW_KINDS.get(declared_extension(submission_file.original_name), "")


def _cache_key(submission_file: SubmissionFile) -> str:
    return f"{CACHE_KEY_PREFIX}:{submission_file.sha256}"


def _read_bytes(submission_file: SubmissionFile, limit: int | None = None) -> bytes | None:
    """Treść pliku ze storage – całość albo pierwsze ``limit`` bajtów. ``None``, gdy się nie da.

    Wyjątek jest łapany szeroko z premedytacją: po drugiej stronie jest sieć i S3, a podgląd nie
    może być powodem, dla którego panel uczestnika przestaje się renderować.
    """
    try:
        with get_submission_storage().open(submission_file.object_key) as stream:
            return stream.read() if limit is None else stream.read(limit)
    except Exception:  # noqa: BLE001 - storage niedostępny = brak podglądu, nie błąd strony
        logger.warning("Nie udało się odczytać pliku %s do podglądu.", submission_file.object_key)
        return None


def count_pdf_pages(data: bytes) -> int | None:
    """Liczba stron dokumentu PDF albo ``None``, gdy pypdf nie umie go otworzyć.

    Import jest leniwy (wzorzec ``nbformat``/``reportlab`` w tym projekcie): biblioteka jest
    potrzebna wyłącznie w tej jednej ścieżce, a moduł ładuje się przy starcie aplikacji.
    """
    import io

    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        # ``strict=False``: dokument z drobną niezgodnością ze specyfikacją (typowy eksport
        # z edytora tekstu) ma podać liczbę stron, a nie wywrócić się na ostrzeżeniu.
        return len(PdfReader(io.BytesIO(data), strict=False).pages)
    except PyPdfError, ValueError, OSError, RecursionError:
        logger.info("Nie udało się policzyć stron PDF-a do podglądu.")
        return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """Wymiary zdjęcia w pikselach albo ``None``.

    Pillow jest w projekcie od zawsze (ciągnie go Wagtail i CAPTCHA), więc nie jest to nowa
    zależność. ``Image.open`` czyta wyłącznie nagłówek – danych obrazu nie dekodujemy, bo do
    podania wymiarów nie są potrzebne, a dekodowanie jest jedyną kosztowną i ryzykowną częścią.
    """
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except UnidentifiedImageError, OSError, ValueError:
        logger.info("Nie udało się odczytać wymiarów zdjęcia do podglądu.")
        return None


def text_head(data: bytes, lines: int = TEXT_PREVIEW_LINES) -> str:
    """Pierwsze ``lines`` wierszy pliku tekstowego. Bajty spoza UTF-8 zastępujemy, nie odrzucamy.

    Notatnik i skrypt przechodzą walidację UTF-8 przy uploadzie, więc ``errors="replace"`` jest tu
    wyłącznie bezpiecznikiem na plik ucięty w połowie znaku przez ``TEXT_PROBE_BYTES``.
    """
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[:lines])


def _build_metrics(submission_file: SubmissionFile) -> dict:
    """Metryki pliku policzone ze storage. Kształt jest stały – brakujące wartości są ``None``."""
    kind = preview_kind(submission_file)
    metrics: dict = {"page_count": None, "width": None, "height": None, "text": ""}
    if kind == "document":
        data = _read_bytes(submission_file)
        if data is not None:
            metrics["page_count"] = count_pdf_pages(data)
    elif kind == "image":
        data = _read_bytes(submission_file)
        if data is not None:
            size = image_size(data)
            if size is not None:
                metrics["width"], metrics["height"] = size
    elif kind == "text":
        data = _read_bytes(submission_file, TEXT_PROBE_BYTES)
        if data is not None:
            metrics["text"] = text_head(data)
    return metrics


def metrics_for(submission_file: SubmissionFile) -> dict:
    """Metryki pliku – z pamięci podręcznej, a przy pudle policzone i zapamiętane.

    Liczba stron ma pierwszeństwo z kolumny w bazie: została policzona raz, zaraz po skanie, i jest
    prawdą także wtedy, gdy podręczna pamięć była właśnie czyszczona.
    """
    key = _cache_key(submission_file)
    metrics = cache.get(key)
    if metrics is None:
        metrics = _build_metrics(submission_file)
        cache.set(key, metrics, CACHE_TTL_SECONDS)
    if submission_file.page_count is not None:
        metrics = {**metrics, "page_count": submission_file.page_count}
    return metrics


def preview_for(submission_file: SubmissionFile | None) -> dict | None:
    """Komplet danych podglądu dla szablonu albo ``None``, gdy nie ma czego pokazać.

    Plik zainfekowany nie ma podglądu i nie ma metryk: uczestnik ma o nim wiedzieć (kolumna „skan
    antywirusowy” w historii wersji to mówi), ale wciąganie jego treści do przeglądarki – choćby
    własnej – byłoby dokładnie tym, czego skan miał uniknąć. Plik jeszcze nieprzeskanowany
    pokazujemy jako „podgląd będzie po skanie”, bo to stan przejściowy, a nie odmowa.
    """
    if submission_file is None:
        return None
    kind = preview_kind(submission_file)
    if not kind:
        return None
    if submission_file.av_status == AvStatus.INFECTED:
        return None
    if submission_file.av_status != AvStatus.CLEAN:
        return {"kind": kind, "pending_scan": True, "page_count": None, "width": None, "height": None}
    return {"kind": kind, "pending_scan": False, **metrics_for(submission_file)}


def store_page_count(submission_file: SubmissionFile) -> SubmissionFile:
    """Zapisuje liczbę stron PDF-a po czystym skanie. Dla pozostałych formatów nie robi nic.

    Wołane z ``apply_scan_verdict``, czyli z workera – tam wolno przeczytać cały plik ze storage,
    bo nikt nie czeka na odpowiedź HTTP. Przy okazji zapisujemy komplet metryk do pamięci
    podręcznej, więc pierwsze wejście do panelu po skanie nie pobiera pliku po raz drugi.
    """
    if preview_kind(submission_file) == "":
        return submission_file
    metrics = _build_metrics(submission_file)
    cache.set(_cache_key(submission_file), metrics, CACHE_TTL_SECONDS)
    page_count = metrics.get("page_count")
    if page_count is not None and page_count != submission_file.page_count:
        submission_file.page_count = page_count
        submission_file.save(update_fields=["page_count"])
    return submission_file
