"""``manage.py superkoordynator`` — nadaje i odbiera rolę superkoordynatora (wszystkie konkursy).

Cztery czynności, dokładnie jedna na wywołanie::

    manage.py superkoordynator --grant adres@example.org
    manage.py superkoordynator --revoke adres@example.org
    manage.py superkoordynator --all-current-coordinators [--dry-run]
    manage.py superkoordynator --list

``--all-current-coordinators`` jest krokiem wdrożenia wydania „uprawnienia CMS per konkurs”
(``docs/OPERACJE.md`` § 6.7): uruchamia się go **raz, zaraz po wdrożeniu i przed**
``scope_cms_access``. Polecenie organizatora brzmi „obecny koordynator ma nim zostać” — więc
każde konto, które dziś ma rolę koordynatora (``apps.accounts.super_coordinator.current_coordinators``),
dostaje rolę platformy, zanim zawężenie ``/cms/`` zabierze globalnej grupie prawa do korzenia.
Kolejność odwrotna też niczego nie psuje na zawsze, ale zostawia okno, w którym koordynator widzi
tylko swój konkurs.

Każda zmiana idzie przez ``super_coordinator.grant`` / ``revoke``, czyli z wpisem audytu. Wywołanie
powtórzone jest bez skutku i bez wpisu — komenda jest idempotentna. ``--dry-run`` wykonuje całość
w transakcji i ją wycofuje: lista na ekranie jest listą tego, co zrobiłaby baza.

Konta **nieaktywne** są przy ``--all-current-coordinators`` pomijane i wypisane z powodem: dziś nie
mają żadnego dostępu (``has_role`` odpowiada im ``False``), więc nadanie roli platformy byłoby
rozszerzeniem uprawnień, a nie ich zachowaniem. Pojedyncze ``--grant`` takie konto przyjmuje —
tam decyzję podejmuje człowiek, wskazując adres.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts import super_coordinator
from apps.accounts.models import GROUP_SUPER_COORDINATOR, User


class Command(BaseCommand):
    help = (
        "Rola superkoordynatora: koordynator wszystkich konkursów i całego /cms/, bez /admin/. "
        "--grant/--revoke <e-mail>, --all-current-coordinators (krok wdrożenia), --list."
    )

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--grant", metavar="EMAIL", help="Nadaj rolę temu kontu.")
        action.add_argument("--revoke", metavar="EMAIL", help="Odbierz rolę temu kontu.")
        action.add_argument(
            "--all-current-coordinators",
            action="store_true",
            help="Nadaj rolę każdemu aktywnemu kontu, które dziś ma rolę koordynatora.",
        )
        action.add_argument("--list", action="store_true", help="Wypisz obecnych superkoordynatorów.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Wykonaj i wycofaj — pokaż, co by się zmieniło, niczego nie zapisując.",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._list()
            return

        dry_run = options["dry_run"]
        with transaction.atomic():
            if options["grant"]:
                changed = self._one(options["grant"], super_coordinator.grant, "nadaję", "już ma rolę")
            elif options["revoke"]:
                changed = self._one(
                    options["revoke"], super_coordinator.revoke, "odbieram", "nie miał(a) roli"
                )
            else:
                changed = self._all_current()
            if dry_run:
                transaction.set_rollback(True)

        summary = f"Zmienionych kont: {changed}."
        if dry_run:
            self.stdout.write(self.style.WARNING(summary + " Próba na sucho — nic nie zapisano."))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

    def _user(self, email: str) -> User:
        user = User.objects.filter(email__iexact=(email or "").strip()).first()
        if user is None:
            raise CommandError(f"Nie ma konta o adresie „{email}”.")
        return user

    def _one(self, email: str, change, verb: str, unchanged: str) -> int:
        user = self._user(email)
        if change(user, via="command"):
            self.stdout.write(f"{verb:<10}{user.email}")
            return 1
        self.stdout.write(f"{'bez zmian':<10}{user.email}: {unchanged}.")
        return 0

    def _all_current(self) -> int:
        changed = 0
        for user in super_coordinator.current_coordinators():
            if not user.is_active:
                self.stdout.write(
                    self.style.WARNING(
                        f"{'pomijam':<10}{user.email}: konto nieaktywne — dziś nie ma dostępu, "
                        "więc rola platformy byłaby rozszerzeniem, a nie zachowaniem uprawnień."
                    )
                )
                continue
            if super_coordinator.grant(user, via="command:all-current-coordinators"):
                changed += 1
                self.stdout.write(f"{'nadaję':<10}{user.email}")
            else:
                self.stdout.write(f"{'bez zmian':<10}{user.email}: już ma rolę.")
        return changed

    def _list(self) -> None:
        rows = User.objects.filter(groups__name=GROUP_SUPER_COORDINATOR).order_by("email")
        for user in rows:
            state = "" if user.is_active else " (konto nieaktywne)"
            self.stdout.write(f"{user.email}{state}")
        self.stdout.write(f"Superkoordynatorów: {rows.count()}.")
