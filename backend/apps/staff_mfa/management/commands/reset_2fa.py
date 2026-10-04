"""``manage.py reset_2fa <e-mail> --note "…"`` – reset 2FA z powłoki serwera (SEC-01, przegląd M1).

Droga ostatnia: dla konta ``admin``/superkoordynatora albo personelu innego konkursu, gdy na
platformie nie ma aktywnego superkoordynatora (panel wtedy odmawia). Audyt ``2fa.reset`` bez
wykonawcy z panelu i z notatką, list do właściciela, zamknięte sesje. Notatka jest obowiązkowa:
„kto, kiedy i dlaczego” ma zostać w aktach, a nie w pamięci dyżurnego.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import twofactor
from apps.accounts.models import User


class Command(BaseCommand):
    help = "Zdejmuje drugi składnik logowania z konta (droga ostatnia; audyt i list do właściciela)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--note", required=True, help="kto zgłosił i jak potwierdzono tożsamość")

    def handle(self, *args, **options):
        user = User.objects.filter(email__iexact=options["email"].strip()).first()
        if user is None:
            raise CommandError("Nie ma konta o tym adresie.")
        if not twofactor.reset_by_operator(user, note=options["note"]):
            self.stdout.write("Konto nie miało drugiego składnika – nic nie zmieniono.")
            return
        self.stdout.write(self.style.SUCCESS(f"Zdjęto drugi składnik z konta {user.email}."))
