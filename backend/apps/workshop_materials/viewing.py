"""Podpisane adresy dla widza: odtwarzacz filmu i pobranie pliku.

**Co ten adres chroni, a czego nie.** Adres jest podpisany kluczem prywatnego konta serwisowego,
ważny krótko (film: ``VIDEO_URL_TTL_SECONDS``, plik: ``FILE_URL_TTL_SECONDS``) i powstaje wyłącznie
w widoku, który sprawdził, że konto należy do konkursu. Bez niego bucket odpowiada 403. To znaczy:

- nie ma stałego adresu nagrania, który dałoby się wkleić na forum albo w komunikator – link
  przestaje działać po czasie życia podpisu,
- strona z odtwarzaczem nie trafia do żadnej pamięci podręcznej (``no-store``; pamięć stron
  publicznych nie obsługuje zalogowanych ani tego adresu – ``apps.web.page_cache``), a adres
  nie wycieka w nagłówku ``Referer`` (``Referrer-Policy: same-origin``),
- **nie** da się zabronić zapisania filmu komuś, kto go ogląda. ``controlsList="nodownload"``
  zdejmuje przycisk „Pobierz” z odtwarzacza, ale adres z narzędzi przeglądarki (albo nagranie
  ekranu) zadziała przez czas życia podpisu. Tak mówi o tym dokumentacja
  (``docs/PODRECZNIK-ORGANIZATORA.md``): to jest ochrona przed rozpowszechnianiem linku, a nie DRM.

Nagłówki odpowiedzi (``Content-Type`` z formatu rozpoznanego po treści, ``Content-Disposition``
z tytułu) wchodzą do podpisu – widz nie może ich podmienić w adresie.
"""

from __future__ import annotations

from django.utils.http import content_disposition_header

from .models import MaterialKind, WorkshopMaterial
from .storage import FILE_URL_TTL_SECONDS, VIDEO_URL_TTL_SECONDS, MaterialStorage


def signed_url(material: WorkshopMaterial, storage: MaterialStorage, *, ttl: int | None = None) -> str:
    """Krótkotrwały adres treści materiału. Film i PDF/obraz – do otwarcia, reszta – do zapisu."""
    fmt = material.format
    inline = bool(fmt and fmt.inline)
    if ttl is None:
        ttl = VIDEO_URL_TTL_SECONDS if material.kind == MaterialKind.VIDEO else FILE_URL_TTL_SECONDS
    return storage.presigned_get(
        material.object_key,
        ttl=ttl,
        content_type=material.content_type,
        content_disposition=content_disposition_header(not inline, material.download_name),
    )
