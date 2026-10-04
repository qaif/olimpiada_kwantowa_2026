"""Storage S3 (django-storages), w którym ``Content-Type`` obiektu wynika z rozszerzenia, a nie od klienta.

**Problem (audyt bezpieczeństwa z 1.10.2026).** ``storages.backends.s3.S3Storage`` zapisuje w MinIO
``ContentType`` wzięty z ``content.content_type`` – czyli z nagłówka części formularza
multipart, który podaje przeglądarka (``UploadedFile.content_type``). Redaktor mógł więc wgrać
``x.txt`` z ``Content-Type: text/html`` (albo ``logo.png`` jako ``image/svg+xml``), a bucket
``public-media`` – anonimowo czytelny, pod tą samą domeną co serwis (produkcja: ``<domena>:9000``,
ciasteczka nie rozróżniają portów) – podawał go potem przeglądarce jako HTML ze skryptem.

**Zasada.** Typ i sposób podania pliku ustala serwer, z rozszerzenia nazwy obiektu:

- ``ContentType`` – z tablicy typów Pythona (``mimetypes.MimeTypes()``: wbudowana tablica, bez
  rejestru Windows i bez ``/etc/mime.types`` obrazu, więc wynik jest ten sam na laptopie, w CI
  i w kontenerze). Rozszerzenie nieznane albo z kodowaniem (``.gz``, ``.svgz``) –
  ``application/octet-stream``; ``Content-Encoding`` nie ustawiamy nigdy (przeglądarka
  rozpakowałaby plik w locie, a ``ContentType`` opisywałby co innego niż bajty).
- ``ContentDisposition: attachment`` – dla wszystkiego, co nie jest obrazem, filmem, dźwiękiem ani
  PDF-em: otwarte wprost z adresu pliku pobiera się, a nie renderuje w karcie. SVG też ``attachment``
  (to dokument XML, w którym może siedzieć skrypt) – osadzony przez ``<img>`` wyświetla się tak samo,
  bo ``Content-Disposition`` dotyczy wyłącznie nawigacji.

Druga warstwa jest w proxy: blok S3 w ``deploy/Caddyfile`` dokłada ``nosniff`` i CSP ``sandbox``
(poza PDF-em). Ta klasa zamyka przyczynę, nagłówki – skutek dla obiektów sprzed tej zmiany.

Dotyczy wyłącznie aliasów ``STORAGES`` budowanych na django-storages (``default`` – media Wagtaila
w ``public-media``, ``private_media`` – treści zadań w ``submissions``; ``config/settings/production.py``).
Rozwiązania uczestników i zaświadczenia (``apps.submissions.storage``) oraz materiały z warsztatów
(``apps.workshop_materials``) mają typ wyliczany po stronie serwera od dawna (``MIME_BY_FORMAT``,
``formats.ALL_FORMATS``) i tej klasy nie potrzebują.
"""

from __future__ import annotations

import mimetypes
import posixpath

from storages.backends.s3 import S3Storage

#: Prefiks plików motywów w buckecie publicznym (``apps.themes.services``).
THEMES_PREFIX = "themes/"

#: Typ zapisywany, gdy rozszerzenie nic nie mówi – przeglądarka niczego z nim nie renderuje.
FALLBACK_CONTENT_TYPE = "application/octet-stream"

#: Prefiksy typów, które wolno podać „inline” (bez wymuszonego pobrania).
INLINE_PREFIXES = ("image/", "video/", "audio/")

#: Pojedyncze typy „inline” spoza prefiksów wyżej.
INLINE_TYPES = frozenset({"application/pdf"})

#: Wyjątki od prefiksów: typy obrazów, które są dokumentami ze skryptami.
ALWAYS_ATTACHMENT_TYPES = frozenset({"image/svg+xml"})

# Własna instancja, a nie moduł ``mimetypes``: ``mimetypes.guess_type`` czyta przy pierwszym użyciu
# rejestr Windows i pliki ``/etc/mime.types`` – wynik zależałby od maszyny (np. ``.zip`` to w Windows
# ``application/x-zip-compressed``). ``MimeTypes()`` bierze wyłącznie tablicę wbudowaną w Pythona.
_MIME = mimetypes.MimeTypes()


def content_type_for(name: str) -> str:
    """Typ MIME obiektu wyłącznie z rozszerzenia nazwy (bez znaczenia wielkość liter)."""
    content_type, encoding = _MIME.guess_type(posixpath.basename(name or "").lower(), strict=True)
    if not content_type or encoding:
        return FALLBACK_CONTENT_TYPE
    return content_type


def is_inline_type(content_type: str) -> bool:
    """Czy plik tego typu wolno podać do wyświetlenia w karcie (obraz, film, dźwięk, PDF)."""
    if content_type in ALWAYS_ATTACHMENT_TYPES:
        return False
    return content_type in INLINE_TYPES or content_type.startswith(INLINE_PREFIXES)


def object_parameters_for(name: str) -> dict[str, str]:
    """Parametry zapisu obiektu (``ExtraArgs`` boto3) wynikające z samej nazwy pliku."""
    content_type = content_type_for(name)
    params = {"ContentType": content_type}
    if not is_inline_type(content_type):
        params["ContentDisposition"] = "attachment"
    return params


class ExtensionContentTypeS3Storage(S3Storage):
    """``S3Storage``, w którym ``ContentType``/``ContentDisposition`` ustala nazwa obiektu, nie klient.

    django-storages buduje parametry zapisu w ``_get_write_parameters``: najpierw
    ``get_object_parameters(name)``, a ``content.content_type`` od klienta bierze **tylko wtedy**,
    gdy ``ContentType`` jeszcze w nich nie ma. Nadpisujemy więc to jedno, udokumentowane miejsce
    („Override this method to adjust this on a per-object basis”) – zawsze z ``ContentType`` – i tym
    samym klientowi nie zostaje nic do podania. Ta sama metoda zasila zapis przez ``File.open('w')``
    (wgrywanie wieloczęściowe ``S3File``) i odczyt (django-storages filtruje z niej wtedy wyłącznie
    parametry SSE-C, więc nasze klucze odczytu nie zmieniają).
    """

    def get_object_parameters(self, name):
        params = super().get_object_parameters(name)
        # Wartości z ``object_parameters`` (AWS_S3_OBJECT_PARAMETERS) nie mogą przywrócić typu od
        # kogoś innego niż ta klasa – stąd nadpisanie, a nie ``setdefault``.
        params.pop("ContentEncoding", None)
        params.pop("ContentDisposition", None)
        params.update(object_parameters_for(name))
        # Pliki motywów (``apps.themes``) leżą pod niezmiennym prefiksem ``themes/<slug>/<wersja>-<sha>/``
        # – nowa wersja to nowy prefiks, więc przeglądarka może je trzymać bez pytania serwera.
        if (name or "").startswith(THEMES_PREFIX):
            params["CacheControl"] = "public, max-age=31536000, immutable"
        return params
