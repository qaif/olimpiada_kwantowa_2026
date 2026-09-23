"""Miniatury plakatów na kartę ``/plakaty/``.

Plakat bywa plikiem kilkudziesięciu megabajtów w rozdzielczości do druku. Pokazanie go na karcie
wprost znaczyłoby, że każdy czytelnik listy pobiera cały plik do wydruku, żeby zobaczyć obrazek
szerokości kciuka – a przy okazji omija licznik pobrań. Dlatego karta pokazuje **miniaturę**:

- dla JPG/PNG powstaje sama przy zapisie (Pillow, najdłuższy bok ``THUMBNAIL_SIZE``),
- dla PDF-a koordynator może ją wgrać; bez niej karta pokazuje ikonę dokumentu. PDF-a nie
  rasteryzujemy – wymagałoby to Ghostscriptu/Poppler w obrazie, czyli nowego, dużego i historycznie
  dziurawego parsera uruchamianego na plikach z formularza. Ikona z tytułem wystarcza.

Dlaczego nie rendition Wagtaila: rendition wymaga obrazu w **bibliotece** CMS-u (``wagtailimages``),
a plakat nie jest obrazem redakcyjnym – trafiłby do wyszukiwarki obrazów redaktora, do kolekcji
i do uprawnień, które go nie dotyczą. Jedna miniatura liczona raz przy zapisie jest tańsza niż
ta infrastruktura.

Awaria dekodera (uszkodzony plik, nieobsługiwany wariant PNG, bomba dekompresyjna) **nie blokuje**
zapisu plakatu: plik przeszedł sprawdzenie sygnatury, więc jest tym, za co się podaje, a brak
miniatury kończy się ikoną – nie błędem formularza, którego koordynator nie ma jak naprawić.
"""

from __future__ import annotations

import logging
from io import BytesIO

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

#: Najdłuższy bok miniatury w pikselach. Karta ma ok. 280 px szerokości; dwukrotność pokrywa
#: ekrany o gęstości 2x, a plik zostaje w dziesiątkach kilobajtów.
THUMBNAIL_SIZE = (640, 640)
THUMBNAIL_QUALITY = 82

#: Górna granica pikseli, jaką zgadzamy się dekodować. Plakat A1 w 300 dpi to ok. 70 Mpx; domyślny
#: próg Pillow (ok. 89 Mpx) jest tu w sam raz i zostaje – ta stała jest wyłącznie po to, żeby
#: przekroczenie było naszym komunikatem w logu, a nie ostrzeżeniem biblioteki.
MAX_PIXELS = 90_000_000


def _open(source):
    from PIL import Image

    source.seek(0)
    image = Image.open(source)
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise ValueError(f"obraz ma {width}×{height} px – powyżej limitu {MAX_PIXELS} px")
    return image


def verify_image(upload) -> bool:
    """Czy dekoder potrafi ten plik otworzyć i przejść w całości – dla wgrywanego podglądu."""
    from PIL import Image

    try:
        image = _open(upload)
        image.verify()
    except Exception:  # noqa: BLE001 - Pillow rzuca kilkanaście różnych klas wyjątków
        return False
    finally:
        upload.seek(0)
    # ``verify()`` zużywa obiekt; drugie otwarcie sprawdza, że da się go też **zdekodować**.
    try:
        Image.open(upload).load()
    except Exception:  # noqa: BLE001
        return False
    finally:
        upload.seek(0)
    return True


def make_thumbnail(source) -> ContentFile | None:
    """Miniatura JPEG z obrazu plakatu albo ``None``, gdy się nie da (patrz docstring modułu).

    Zawsze JPEG, także z PNG: miniatura ma być mała, a przezroczystość plakatu na karcie i tak
    nie ma czego odsłaniać – kładziemy ją na białe tło, tak jak wyglądałby wydruk.
    """
    from PIL import Image, ImageOps

    try:
        image = _open(source)
        # JPEG w trybie „draft” dekoduje od razu w mniejszej skali – przy plakacie 8000 px
        # oszczędza to kilkaset megabajtów pamięci na chwilę dekodowania.
        image.draft("RGB", THUMBNAIL_SIZE)
        image = ImageOps.exif_transpose(image)
        image.thumbnail(THUMBNAIL_SIZE)
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True, progressive=True)
    except Exception:  # noqa: BLE001 - patrz docstring modułu: brak miniatury to ikona, nie błąd
        logger.warning("Nie udało się zrobić miniatury plakatu – karta pokaże ikonę.", exc_info=True)
        return None
    finally:
        source.seek(0)
    return ContentFile(buffer.getvalue(), name="podglad.jpg")
