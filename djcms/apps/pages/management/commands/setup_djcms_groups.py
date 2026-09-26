"""Grupa „Redaktorzy” djcms z uprawnieniami do stron, wtyczek, plików i wersji (idempotentne).

Decyzja z 26.09.2026: jedna grupa dla całej redakcji porównania, bez uprawnień per strona
(``CMS_PERMISSION = False``) i per folder (``FILER_ENABLE_PERMISSIONS = False``). Uprawnienia
są zwykłymi uprawnieniami modeli Django, wyliczanymi **z etykiet aplikacji**, a nie z listy
modeli wpisanej tutaj – dzięki temu wtyczki i rozszerzenia stron dochodzące w DJ-01e/f
(``dj_blocks``, ``dj_live``) trafiają do grupy po ponownym uruchomieniu komendy, bez jej edycji.
Deploy woła ją przy każdym wdrożeniu (§ 8.10), więc zestaw jest zawsze aktualny.

Poza grupą zostaje **zarządzanie dostępem**: uprawnienia stron django CMS, użytkownicy i grupy
CMS-a, uprawnienia folderów filera. Redaktor edytuje treść; konta i grupy zakłada administrator
(superuser z ``bootstrap_djcms_admin``). Nowe konto redaktora wymaga w panelu ``is_staff``
(„W zespole”) i członkostwa w tej grupie – grupa nie może nadać ``is_staff`` sama.

``group.permissions.set(...)`` – zestaw jest **zastępowany**, nie dopisywany: ręcznie dodane
w panelu uprawnienie znika przy następnym wdrożeniu. To celowe: stan grupy ma wynikać z kodu.
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

GROUP_NAME = "Redaktorzy"

#: Aplikacje, których modele redaktor edytuje w całości.
EDITOR_APP_LABELS = (
    "cms",
    "djangocms_text",
    "djangocms_versioning",
    "filer",
    "dj_pages",
    "dj_blocks",
    "dj_live",
)

#: Modele zarządzania dostępem – wyłączone z grupy (patrz docstring modułu) – oraz jeden model
#: wewnętrzny django CMS (``UrlconfRevision``: znacznik przeładowania urlconfu apphooków), którego
#: nikt nie edytuje ręcznie.
EXCLUDED_MODELS = frozenset(
    {
        ("cms", "urlconfrevision"),
        ("cms", "globalpagepermission"),
        ("cms", "pagepermission"),
        ("cms", "pageuser"),
        ("cms", "pageusergroup"),
        ("filer", "folderpermission"),
    }
)


def editor_permissions():
    perms = Permission.objects.filter(content_type__app_label__in=EDITOR_APP_LABELS).select_related(
        "content_type"
    )
    return [
        perm
        for perm in perms
        if (perm.content_type.app_label, perm.content_type.model) not in EXCLUDED_MODELS
    ]


class Command(BaseCommand):
    help = "Zakłada/aktualizuje grupę „Redaktorzy” z uprawnieniami do treści djcms."

    @transaction.atomic
    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name=GROUP_NAME)
        perms = editor_permissions()
        group.permissions.set(perms)
        verb = "utworzono" if created else "zaktualizowano"
        self.stdout.write(self.style.SUCCESS(f"Grupa „{GROUP_NAME}” {verb}: {len(perms)} uprawnień."))
