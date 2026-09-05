"""``manage.py create_invitation --email <koordynator> [--pending] [--appeals]``.

Kod jest wypisywany raz na stdout i nigdzie nie jest zapisywany ani logowany.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import GROUP_COORDINATOR, InvitationGrantsStatus, User
from apps.accounts.services import create_invitation


class Command(BaseCommand):
    help = "Tworzy kod zaproszenia do komitetu i wypisuje go (jednorazowo) na stdout."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="E-mail koordynatora tworzącego kod.")
        parser.add_argument(
            "--pending",
            action="store_true",
            help="Kod nadaje status PENDING (konto wymaga zatwierdzenia). Domyślnie ACTIVE.",
        )
        parser.add_argument("--appeals", action="store_true", help="Kod uprawnia do komisji odwoławczej.")
        parser.add_argument("--max-uses", type=int, default=1, help="Ile razy kod może zostać użyty.")
        parser.add_argument("--valid-days", type=int, default=14, help="Ważność kodu w dniach.")

    def handle(self, *args, **options):
        try:
            creator = User.objects.get(email=options["email"].strip().lower())
        except User.DoesNotExist as exc:
            raise CommandError(f"Nie znaleziono użytkownika {options['email']}.") from exc
        if not creator.groups.filter(name=GROUP_COORDINATOR).exists():
            raise CommandError("Kody zaproszeń może tworzyć wyłącznie koordynator.")

        grants = InvitationGrantsStatus.PENDING if options["pending"] else InvitationGrantsStatus.ACTIVE
        invitation, plain_code = create_invitation(
            creator,
            valid_for=timedelta(days=options["valid_days"]),
            max_uses=options["max_uses"],
            grants_status=grants,
            is_appeals=options["appeals"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"Kod zaproszenia (zapisz teraz, nie da się go odtworzyć): {plain_code}")
        )
        self.stdout.write(
            f"status={invitation.grants_status} appeals={invitation.is_appeals} "
            f"max_uses={invitation.max_uses} expires_at={invitation.expires_at.isoformat()}"
        )
