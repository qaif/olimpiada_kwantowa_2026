"""Filer bez plików „prywatnych” (reguła 12 z § 7 docs/tasks/DJ-01.md).

Flaga ``is_public = False`` w filerze przenosi plik do osobnego magazynu i serwuje go przez widok
Django z kontrolą uprawnień. W djcms ta kontrola nie istnieje: ``/djcms/media/*`` serwuje Caddy
wprost z wolumenu. Plik „prywatny” byłby więc albo zepsutym odnośnikiem, albo – gdyby ktoś kiedyś
wystawił magazyn prywatny – plikiem publicznym z etykietą „prywatny”, która obiecuje coś, czego nikt
nie egzekwuje. Uprawnienia folderów filera są włączone (``FILER_ENABLE_PERMISSIONS = True`` – folder
per konkurs, DJ-02 D5), ale porządkują bibliotekę redakcji, a nie widoczność plików. Dlatego flagi nie ma:

- **ukryta w panelu** – przy włączonych uprawnieniach filer pokazuje pole ``is_public`` w formularzu
  pliku i akcje „ustaw publiczny/prywatny” w liście folderu; :func:`hide_private_toggle` zdejmuje
  jedno i drugie, a system check ``dj_blocks.E001`` pilnuje, żeby tak zostało,
- **zablokowana w kodzie** – sygnał niżej zawraca ``is_public = False`` przy każdym zapisie pliku,
  także z kodu (import, powłoka), nie tylko z formularza.
"""

from __future__ import annotations

import inspect
import logging

from django.db.models.signals import pre_save
from django.dispatch import receiver
from filer.models import File

logger = logging.getLogger(__name__)

#: Akcje listy folderu, które przestawiają flagę ``is_public`` (filer 3.6, ``FolderAdmin.get_actions``).
PRIVACY_ACTIONS = ("files_set_public", "files_set_private")
PUBLIC_FLAG_FIELD = "is_public"


def _without_public_flag(fieldsets):
    cleaned = []
    for name, options in fieldsets or ():
        fields = tuple(field for field in options.get("fields", ()) if field != PUBLIC_FLAG_FIELD)
        if fields:
            cleaned.append((name, {**options, "fields": fields}))
    return tuple(cleaned)


def hide_private_toggle(site=None) -> None:
    """Zdejmuje z panelu filera pole ``is_public`` i akcje prywatności (woła ``BlocksConfig.ready``).

    Panel filera rejestruje się w ``django.contrib.admin`` przy autodiscover – przed ``ready`` tej
    aplikacji – więc poprawiamy zarejestrowane instancje, a nie klasy przed rejestracją.
    """
    from django.contrib import admin
    from filer.models import File, Folder

    registry = (site or admin.site)._registry
    for model, model_admin in registry.items():
        if issubclass(model, File) and model_admin.fieldsets:
            model_admin.fieldsets = _without_public_flag(model_admin.fieldsets)
    folder_admin = registry.get(Folder)
    if folder_admin is not None and not getattr(folder_admin, "_dj_private_toggle_hidden", False):
        original = folder_admin.get_actions
        # Django 6.1 przekazuje ``action_location`` tylko metodom, które go deklarują (filer 3.6 – nie).
        takes_location = "action_location" in inspect.signature(original).parameters

        def get_actions(request, action_location=None):
            if takes_location and action_location is not None:
                actions = original(request, action_location=action_location)
            else:
                actions = original(request)
            for name in PRIVACY_ACTIONS:
                actions.pop(name, None)
            return actions

        folder_admin.get_actions = get_actions
        folder_admin._dj_private_toggle_hidden = True


def private_toggle_visible(site=None) -> list[str]:
    """Gdzie panel filera nadal pokazuje przełącznik prywatności – dla ``dj_blocks.E001``."""
    from django.contrib import admin
    from filer.models import File, Folder

    registry = (site or admin.site)._registry
    found = []
    for model, model_admin in registry.items():
        if not issubclass(model, File):
            continue
        for _name, options in model_admin.fieldsets or ():
            if PUBLIC_FLAG_FIELD in options.get("fields", ()):
                found.append(f"{model._meta.label}: pole {PUBLIC_FLAG_FIELD}")
    folder_admin = registry.get(Folder)
    if folder_admin is not None and not getattr(folder_admin, "_dj_private_toggle_hidden", False):
        found.append("filer.Folder: akcje " + ", ".join(PRIVACY_ACTIONS))
    return found


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
