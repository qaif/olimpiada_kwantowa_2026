"""``manage.py visa_letter_redirects <konkurs>`` – przekierowania starych adresów weryfikacji (VISA-01 M4).

Kod QR na liście niesie adres strony weryfikacji **z chwili wystawienia**
(``InvitationLetter.verification_base_url``). Gdy konkurs zmieni potem adresowanie – inny prefiks
ścieżki (``/stary/visa/verify/…`` → ``/nowy/visa/verify/…``) albo inną domenę – stary adres nie trafia
już do widoku weryfikacji tego konkursu, a list leży w konsulacie. Komenda zakłada dla każdego takiego
listu przekierowanie Wagtaila (``wagtail.contrib.redirects``, warstwa ``CompetitionRedirectMiddleware``
na końcu łańcucha): stara ścieżka z kodem → dzisiejszy adres weryfikacji listu.

Działa dla zmiany **prefiksu** (stary adres trafia na hosta platformy, nic go nie obsługuje – 404 –
i wtedy rusza przekierowanie). Przy zmianie **domeny** stara domena musi dalej prowadzić na serwer;
jeśli trafia do innego konkursu z listami, widok weryfikacji sam przekierowuje po zapamiętanym adresie
(``verification.moved_letter``) – przekierowanie z tej komendy jest wtedy siatką bezpieczeństwa.

Idempotentna: istniejące przekierowanie tej ścieżki jest aktualizowane, a nie dublowane. ``--dry-run``
wypisuje plan bez zapisu. Kodów nie wypisuje w całości (adres z kodem pokazuje nazwisko na stronie).
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Przekierowania starych adresów weryfikacji listów po zmianie domeny albo prefiksu konkursu."

    def add_arguments(self, parser) -> None:
        parser.add_argument("competition", help="identyfikator (slug) konkursu")
        parser.add_argument("--dry-run", action="store_true", help="tylko pokaż plan")

    def handle(self, *args, **options) -> None:
        from wagtail.contrib.redirects.models import Redirect

        from apps.delegation_logistics.letters import current_verification_url, verification_entry_url
        from apps.delegation_logistics.models import InvitationLetter
        from apps.tenancy.context import competition_context
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=options["competition"]).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu {options['competition']!r}.")
        with competition_context(competition):
            current_base = verification_entry_url(competition)
            letters = (
                InvitationLetter.objects.filter(competition=competition)
                .exclude(verification_base_url="")
                .exclude(verification_base_url=current_base)
                .exclude(verification_code=None)
            )
            planned = []
            for letter in letters:
                old_path = (
                    urlparse(letter.verification_base_url).path.rstrip("/") + f"/{letter.verification_code}/"
                )
                planned.append((Redirect.normalise_path(old_path), current_verification_url(letter), letter))
        self.stdout.write(f"Bieżący adres weryfikacji: {current_base}")
        self.stdout.write(f"Listy ze starym adresem: {len(planned)}")
        if options["dry_run"]:
            for old_path, _link, letter in planned:
                self.stdout.write(f"  {letter.number}: {old_path[: -len(letter.verification_code) - 1]}<kod>")
            return
        with transaction.atomic():
            for old_path, link, _letter in planned:
                Redirect.objects.update_or_create(
                    old_path=old_path,
                    site=None,
                    defaults={"redirect_link": link, "redirect_page": None, "is_permanent": False},
                )
        self.stdout.write(self.style.SUCCESS(f"Zapisano przekierowania: {len(planned)}."))
