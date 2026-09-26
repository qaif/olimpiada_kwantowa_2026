"""Filer w zasięgu redaktora: wyszukiwarka bez cudzych plików „luzem” (DJ-02g, S12).

``FolderAdmin.directory_listing`` (filer 3.6.0) zawęża wyniki uprawnieniami folderów, ale dokłada do
nich **każdy** plik bez folderu (``Q(folder_id__isnull=True)``), bez względu na właściciela. Pliki
„luzem” to wgrania bez wybranego folderu – przy wyszukiwaniu w korzeniu redaktor widziałby nazwy
takich plików z innych konkursów (i mógł na nich wywołać akcje listy). Filer sam, w wirtualnym
folderze „Unsorted Uploads”, pokazuje nie-superużytkownikowi tylko jego własne – tu ta sama reguła
dla wyszukiwania. Lista „plików z brakującymi metadanymi” jest zamknięta dla redaktora w
``EditorAccessMiddleware`` (``_site_wide_view``).

Rejestracja zastępuje ``FolderAdmin`` filera – ``apps.sites`` stoi w ``INSTALLED_APPS`` za ``filer``,
więc ``autodiscover`` ładuje ten moduł po ``filer.admin``.
"""

from __future__ import annotations

from contextvars import ContextVar

from django.contrib import admin
from django.db.models import Q
from filer.admin.folderadmin import FolderAdmin
from filer.models import Folder

from .permissions import editable_site_ids

#: Redaktor z listą witryn, dla którego filtrujemy bieżącą listę folderu (``None`` – bez filtra).
_scoped_user: ContextVar = ContextVar("dj_filer_scoped_user", default=None)


class EditorFolderAdmin(FolderAdmin):
    def directory_listing(self, request, folder_id=None, viewtype=None):
        scoped = request.user.is_staff and editable_site_ids(request.user) is not None
        token = _scoped_user.set(request.user if scoped else None)
        try:
            return super().directory_listing(request, folder_id=folder_id, viewtype=viewtype)
        finally:
            _scoped_user.reset(token)

    def filter_file(self, qs, terms=()):
        # Woła je wyłącznie wyszukiwanie ``directory_listing`` – jedyna droga, którą do wyników trafiają
        # pliki bez folderu spoza „Unsorted Uploads”.
        qs = super().filter_file(qs, terms)
        user = _scoped_user.get()
        if user is not None:
            qs = qs.exclude(Q(folder__isnull=True) & ~Q(owner=user))
        return qs


admin.site.unregister(Folder)
admin.site.register(Folder, EditorFolderAdmin)
