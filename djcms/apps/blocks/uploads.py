"""Walidator wgrywanych obrazów filera: limit pikseli (bomba dekompresyjna).

Filer woła walidatory z ``FILER_ADD_FILE_VALIDATORS`` (``config/settings/base.py``) przy każdym
wgraniu pliku przez redaktora – w panelu i z paska narzędzi – **przed** zapisem do magazynu. Obraz
powyżej ``DJ_MAX_IMAGE_PIXELS`` (szerokość × wysokość) jest odrzucany z komunikatem: plik
12000×12000 px potrafi ważyć kilkadziesiąt kilobajtów, a pierwsza jego miniatura (easy-thumbnails,
strona publiczna) zabija proces djcms przez OOM. Wymiary czytamy z nagłówka (``Image.open`` nie
dekoduje pikseli), więc sam walidator nie jest tym, co zjada pamięć.

Plik, którego Pillow nie otwiera, przepuszczamy – o tym, czy to obraz, decyduje filer (typ MIME,
własna walidacja SVG); tu pilnujemy wyłącznie wielkości tego, co obrazem **jest**.
"""

from __future__ import annotations

from django.conf import settings
from filer.validation import FileValidationError


def validate_image_pixels(file_name: str, file, owner, mime_type: str) -> None:
    from PIL import Image as PILImage

    limit = int(getattr(settings, "DJ_MAX_IMAGE_PIXELS", 40_000_000))
    position = file.tell() if hasattr(file, "tell") else 0
    try:
        with PILImage.open(file) as image:
            width, height = image.size
    except PILImage.DecompressionBombError, PILImage.DecompressionBombWarning:
        width = height = None
    except Exception:  # noqa: BLE001 - nie obraz albo uszkodzony: rozstrzyga filer, nie ten walidator
        return
    finally:
        if hasattr(file, "seek"):
            file.seek(position)
    if width is None or height is None or width * height > limit:
        raise FileValidationError(
            f"Plik „{file_name}”: obraz większy niż {limit // 1_000_000} Mpx (szerokość × wysokość) – "
            "zmniejsz go przed wgraniem."
        )
