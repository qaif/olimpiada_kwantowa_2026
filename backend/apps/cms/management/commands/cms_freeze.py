"""Zamrożenie edycji stron Wagtaila: ``cms_freeze on [--message …] | off | status``.

Wołają ją skrypty przełączenia na django CMS (``scripts/djcms_cutover.sh`` – krok 3,
``scripts/djcms_switch.sh status``) i operator przy wycofaniu (DJ-02 § 10). Stan jest wierszem
w bazie (``cms.EditingFreeze``), więc zmiana nie wymaga restartu ``web`` – pozostałe procesy
widzą ją najpóźniej po ``apps.cms.freeze.CACHE_TTL_SECONDS`` sekundach.

Kody wyjścia:

- ``on``, ``off`` – 0 (idempotentne: ponowne ``on`` podmienia komunikat),
- ``status`` – **0, gdy zamrożone**, 1, gdy edycja jest otwarta (``if cms_freeze status; then …``).
"""

from __future__ import annotations

import getpass
import sys

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.cms import freeze


def _operator() -> str:
    try:
        return f"manage.py cms_freeze ({getpass.getuser()})"
    except KeyError, OSError:  # pragma: no cover - kontener bez wpisu w /etc/passwd
        return "manage.py cms_freeze"


class Command(BaseCommand):
    help = (
        "Zamraża (on) albo odmraża (off) edycję stron w /cms/ Wagtaila po przełączeniu serwisu "
        "na django CMS; status: kod 0 = zamrożone, 1 = edycja otwarta."
    )

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("on", "off", "status"))
        parser.add_argument(
            "--message",
            default="",
            help=f"Pierwsze zdanie banera w /cms/ (domyślnie: „{freeze.DEFAULT_MESSAGE}”).",
        )
        parser.add_argument("--by", default="", help="Kto zmienia stan (zapisywane w bazie).")

    def handle(self, *args, action, message, by, **options):
        if action == "status":
            freeze.reset_cache()
            state = freeze.freeze_state()
            self.stdout.write(self._describe(state))
            if not state.active:
                sys.exit(1)
            return

        state = freeze.set_frozen(action == "on", message=message.strip(), changed_by=by or _operator())
        self.stdout.write(self.style.SUCCESS(self._describe(state)))
        self.stdout.write(
            f"Pozostałe procesy web zobaczą zmianę najpóźniej po {freeze.CACHE_TTL_SECONDS} s (bez restartu)."
        )
        if not state.active:
            self.stdout.write(
                "Uwaga: Wagtail pokazuje treść z chwili zamrożenia – zmiany wprowadzone w django CMS "
                "do Wagtaila nie wracają."
            )

    @staticmethod
    def _describe(state) -> str:
        if not state.active:
            head = "Zamrożenie edycji stron: WYŁĄCZONE (strony w /cms/ edytowalne)."
        else:
            head = f"Zamrożenie edycji stron: WŁĄCZONE – {state.banner_message}"
        if state.changed_at is not None:
            when = timezone.localtime(state.changed_at).strftime("%Y-%m-%d %H:%M:%S")
            head += f" [zmieniono {when}, {state.changed_by or 'nieznany'}]"
        return head
