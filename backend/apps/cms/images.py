"""Wgrywanie logotypów do biblioteki obrazów Wagtaila.

Moduł jest odpowiednikiem ``apps.cms.attachments`` dla obrazów i powtarza jego zasadę tożsamości:
**tym samym obrazem jest obraz o tym samym tytule w bibliotece**, a nie plik o tej samej nazwie.
Tytuł widzi redaktor w ``/cms/``, po nim szuka i to on zostaje, kiedy organizator przyśle logotyp
w innym formacie.

Trzy decyzje warte uzasadnienia:

- **plik jest normalizowany przed wgraniem, a nie po.** Logotypy przychodzą od instytucji
  w postaci, w jakiej trafiają do druku: ``fuw.jpg`` jest w przestrzeni **CMYK** (przeglądarki
  renderują taki JPEG z przekłamanymi barwami albo wcale, a Pillow odmawia zapisania go jako PNG),
  ``pcss.png`` ma 8082 px szerokości przy kaflu, który pokazuje 400 px. Konwersja przy wgrywaniu
  oznacza, że w bibliotece leży jeden, sensowny oryginał; Wagtail dorabia z niego renditions,
  ale nie naprawi ani przestrzeni barw, ani ośmiu tysięcy pikseli,
- **format wyjściowy wynika z kanału alfa, nie z rozszerzenia źródła.** Logotypy z przezroczystym
  tłem zostają PNG-iem (JPEG wypełniłby przezroczystość czernią), reszta – JPEG-iem (PNG z fotografii
  albo gradientu bywa dziesięć razy cięższy). Rozszerzenie pliku źródłowego nie jest wyznacznikiem:
  ``fuw.jpg`` jest JPEG-iem bez alfy i JPEG-iem zostaje, ``cft-pan.png`` ma alfę i zostaje PNG-iem,
- **ta sama treść nie jest wgrywana drugi raz, inna – jest.** Porównujemy SHA-256 **znormalizowanych**
  bajtów z zawartością pliku w storage. Dzięki temu drugi przebieg komendy nie mnoży plików
  w buckecie ani nie zostawia nieaktualnego logotypu pod aktualnym tytułem. Przy podmianie kasujemy
  renditions: to zbuforowane przeskalowania **poprzedniego** pliku, a klucz bufora nie zawiera
  skrótu treści, więc bez tego strona pokazywałaby stary logotyp pod nowym adresem.

Identyfikator obrazu (a więc adresy renditions w już wysłanych stronach) przy podmianie zostaje ten
sam – zmienia się zawartość rekordu, nie rekord.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image as PILImage
from PIL import ImageChops
from wagtail.images import get_image_model
from wagtail.models import Collection

#: ``…/apps/cms/`` → ``…/apps/cms/fixtures/partners/``. Logotypy przekazane przez organizatora.
PARTNERS_DIR = Path(__file__).resolve().parent / "fixtures" / "partners"

#: Największa szerokość oryginału w bibliotece. Najszerszy kadr, w jakim pokazujemy logotyp, ma
#: 400 px (siatka na ``/partnerzy/``); 1600 px zostawia zapas na ekran o gęstości 4× i na przyszłe
#: użycia (nagłówek strony partnera, materiał do druku ze strony), a jednocześnie ścina plik
#: PCSS-u z ośmiu tysięcy pikseli do rozmiaru, który da się wysłać przeglądarce.
MAX_WIDTH = 1600

#: Jakość zapisu JPEG. 88 to próg, powyżej którego na logotypie (płaskie plamy barwne i tekst)
#: nie widać już różnicy, a plik rośnie.
JPEG_QUALITY = 88

#: Tryby PIL-a niosące kanał alfa – decydują o formacie wyjściowym.
ALPHA_MODES = frozenset({"RGBA", "LA", "PA"})

CHUNK = 64 * 1024


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(handle) -> str:
    sha = hashlib.sha256()
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        sha.update(chunk)
    return sha.hexdigest()


def _has_alpha(image: PILImage.Image) -> bool:
    return image.mode in ALPHA_MODES or "transparency" in image.info


def trim(image: PILImage.Image) -> PILImage.Image:
    """Obcina jednolity margines (biel albo przezroczystość) dookoła znaku.

    Pliki przychodzą z różnych działów promocji i mają wbudowane pole ochronne w bardzo różnych
    proporcjach: ``aiqlab.png`` to znak zajmujący 70 % szerokości pliku i 41 % wysokości,
    ``fuw.jpg`` – znak dociśnięty do krawędzi. W kaflu o stałej wysokości ten margines jest
    widoczny wprost: znak z dużym polem rysuje się o połowę mniejszy od sąsiada, choć oba obrazy
    mają tę samą wysokość. Obcięcie sprowadza wszystkie do jednej zasady „kafel pokazuje znak”,
    a odstęp dokłada CSS – jednakowy dla każdego.

    Znak jest **wpisany w prostokąt**, nie przeskalowany: obcinamy tylko puste pole, proporcji
    samego znaku nie ruszamy. Obraz bez marginesu (albo w całości pusty) wraca nietknięty.
    """
    ground = PILImage.new("RGBA", image.size, (255, 255, 255, 255))
    flat = PILImage.alpha_composite(ground, image.convert("RGBA")).convert("RGB")
    box = ImageChops.difference(flat, PILImage.new("RGB", flat.size, (255, 255, 255))).getbbox()
    if box is None or box == (0, 0, image.width, image.height):
        return image
    return image.crop(box)


def normalize(source: Path) -> tuple[bytes, str, int, int]:
    """Logotyp gotowy do wgrania: ``(bajty, nazwa pliku, szerokość, wysokość)``.

    Reguły – i tylko one: przestrzeń barw na RGB(A), obcięcie pustego marginesu (``trim``),
    szerokość ograniczona do ``MAX_WIDTH``, format wyjściowy z kanału alfa. Nic tu nie zmienia
    proporcji samego znaku – kadrowaniem w kaflu zajmuje się szablon przez ``object-fit``.
    """
    with PILImage.open(source) as opened:
        image = trim(opened.convert("RGBA" if _has_alpha(opened) else "RGB"))

    if image.width > MAX_WIDTH:
        height = max(1, round(image.height * MAX_WIDTH / image.width))
        image = image.resize((MAX_WIDTH, height), PILImage.LANCZOS)

    buffer = BytesIO()
    if image.mode == "RGBA":
        image.save(buffer, format="PNG", optimize=True)
        suffix = ".png"
    else:
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        suffix = ".jpg"
    return buffer.getvalue(), f"{source.stem}{suffix}", image.width, image.height


def _refresh_metadata(image) -> None:
    """Uzupełnia ``file_size`` i ``file_hash`` obrazu – tak samo jak ``attachments._refresh_metadata``.

    Wagtail liczy oba w formularzu biblioteki, a nie przy ``file.save()``: obraz wgrany komendą ma
    je puste, a ``file_hash`` wchodzi do klucza bufora renditions. Skrótu **nie** liczymy sami:
    kolumna ma 40 znaków, bo Wagtail trzyma tam SHA-1, a nie SHA-256 z tego modułu (ten służy
    wyłącznie do porównania „czy to ta sama treść”). Zerowanie przed odczytem jest konieczne, bo
    obie metody liczą wartość dopiero wtedy, gdy pole jest puste.
    """
    image.file_size = None
    image.file_hash = ""
    image.get_file_size()
    image.get_file_hash()


def _apply(image, data: bytes, filename: str, width: int, height: int) -> None:
    """Zapisuje zawartość pliku i metadane obrazu; kasuje renditions poprzedniej wersji."""
    image.file.save(filename, ContentFile(data), save=False)
    image.width = width
    image.height = height
    # Zapis przed policzeniem metadanych: ``get_file_size``/``get_file_hash`` same wołają
    # ``save(update_fields=…)``, a to wymaga istniejącego klucza głównego.
    image.save()
    _refresh_metadata(image)
    image.renditions.all().delete()


def ensure_image(title: str, source: Path, *, description: str = ""):
    """Obraz o zadanym tytule w kolekcji Root, z **znormalizowaną** zawartością ``source``.

    Zwraca ``(image, action)``, gdzie ``action`` to ``"created"``, ``"updated"`` albo
    ``"unchanged"`` – komendy raportują to na stdout, żeby przebieg dało się przeczytać.
    """
    Image = get_image_model()
    data, filename, width, height = normalize(source)

    image = Image.objects.filter(title=title).first()
    if image is None:
        image = Image(
            title=title,
            description=description,
            collection=Collection.get_first_root_node(),
        )
        _apply(image, data, filename, width, height)
        return image, "created"

    try:
        with image.file.open("rb") as handle:
            current = _digest_file(handle)
    except (FileNotFoundError, OSError):
        # Rekord bez pliku w storage (przeniesiona baza, wyczyszczony bucket) – wgrywamy na nowo.
        current = None

    if current == _digest_bytes(data):
        changed = []
        if description and image.description != description:
            image.description = description
            changed.append("description")
        if not image.file_hash or not image.file_size:
            _refresh_metadata(image)
            changed += ["file_size", "file_hash"]
        if changed:
            image.save(update_fields=changed)
        return image, "unchanged"

    if description:
        image.description = description
    _apply(image, data, filename, width, height)
    return image, "updated"
