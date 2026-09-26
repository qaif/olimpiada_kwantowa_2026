"""Grupy redakcji per konkurs i uprawnienia folderów filera (DJ-02 D5) – idempotentne, woła je deploy.

Od DJ-02g redaktorzy nie mają jednej wspólnej grupy: każdy konkurs ma ``redakcja:<slug>`` (z publikacją)
i ``redakcja:<slug>:bez-publikacji``, a konta bez ograniczeń – ``redakcja:platforma``. Członków
wyznacza wyłącznie logowanie z ``/cms/`` (SSO, ``apps.sites.sso``); ta komenda dba o to, żeby
grupy, ich ``GlobalPagePermission`` (z listą witryn) i uprawnienia do folderów istniały i były
takie, jak mówi kod (``apps.sites.permissions`` – tam uzasadnienie zestawu).

Grupa „Redaktorzy” z DJ-01 (jedna dla wszystkich, bez ``GlobalPagePermission``) jest usuwana: przy
``CMS_PERMISSION = True`` i tak nie daje żadnej strony, a zostawiona sugerowałaby, że daje.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.sites.permissions import PLATFORM_GROUP, ensure_all

#: Grupa z DJ-01 – usuwana przy pierwszym uruchomieniu po DJ-02g.
LEGACY_GROUP_NAME = "Redaktorzy"


class Command(BaseCommand):
    help = "Zakłada/aktualizuje grupy redakcji per konkurs (redakcja:<slug>) i uprawnienia folderów filera."

    @transaction.atomic
    def handle(self, *args, **options):
        removed, _ = Group.objects.filter(name=LEGACY_GROUP_NAME).delete()
        counts = ensure_all()
        self.stdout.write(
            self.style.SUCCESS(
                f"Grupy redakcji: {counts['competitions']} konkursów (po dwie grupy) + {PLATFORM_GROUP}."
            )
        )
        if removed:
            self.stdout.write(
                f"Usunięto grupę „{LEGACY_GROUP_NAME}” z DJ-01 (zastąpiona grupami redakcja:*)."
            )
