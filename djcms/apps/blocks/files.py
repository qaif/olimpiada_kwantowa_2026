"""Filer bez plików „prywatnych” (reguła 12 z § 7 docs/tasks/DJ-01.md).

Flaga ``is_public = False`` w filerze przenosi plik do osobnego magazynu i serwuje go przez widok
Django z kontrolą uprawnień. Na ``dj.`` ta kontrola nie istnieje: uprawnienia filera są wyłączone
(``FILER_ENABLE_PERMISSIONS = False`` – jedna grupa redaktorów), a ``/media/*`` serwuje Caddy
wprost z wolumenu. Plik „prywatny” byłby więc albo zepsutym odnośnikiem, albo – gdyby ktoś kiedyś
wystawił magazyn prywatny – plikiem publicznym z etykietą „prywatny”, która obiecuje coś, czego nikt
nie egzekwuje. Dlatego flagi nie ma:

- **ukryta w panelu** – filer sam chowa pole ``is_public`` i akcje „ustaw publiczny/prywatny”, gdy
  uprawnienia są wyłączone; system check ``dj_blocks.E001`` pilnuje, żeby tak zostało,
- **zablokowana w kodzie** – sygnał niżej zawraca ``is_public = False`` przy każdym zapisie pliku,
  także z kodu (import, powłoka), nie tylko z formularza.
"""

from __future__ import annotations

import logging

from django.db.models.signals import pre_save
from django.dispatch import receiver
from filer.models import File

logger = logging.getLogger(__name__)


@receiver(pre_save, dispatch_uid="dj_blocks.force_public_filer_files")
def force_public_filer_files(sender, instance, **kwargs):
    """Każdy plik filera zapisuje się jako publiczny.

    ``pre_save`` przychodzi **po** ``File.save`` przeniesieniu pliku do magazynu prywatnego (filer
    robi to, zanim zawoła ``Model.save``). Samo odwrócenie flagi zostawiłoby więc wiersz „publiczny”
    wskazujący plik w magazynie prywatnym – stąd powrotne przeniesienie tą samą metodą filera.
    """
    if not isinstance(instance, File) or instance.is_public:
        return
    logger.warning("Plik filera #%s miał być prywatny – na dj. wszystkie pliki są publiczne.", instance.pk)
    instance.is_public = True
    if instance.pk and instance.file:
        # ``File.save`` zdążył przenieść plik do magazynu prywatnego i ustawić ``_old_is_public``
        # na ``False`` – ``_move_file`` przy ``is_public = True`` przenosi go z powrotem.
        instance._move_file()
    instance._old_is_public = True
