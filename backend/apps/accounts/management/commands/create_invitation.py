"""``manage.py create_invitation --email <koordynator> [--pending] [--appeals] [--district X]``.

Kod jest wypisywany raz na stdout i nigdzie nie jest zapisywany ani logowany.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import (
    COORDINATOR_GROUPS,
    InvitationGrantsStatus,
    User,
    Voivodeship,
    normalize_voivodeship,
)
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
        parser.add_argument(
            "--district",
            default=None,
            help=(
                "Województwo narzucone rejestrowanemu recenzentowi. Nadpisuje deklarację z formularza "
                "i nadaje profilowi district_verified=True. Pole jest opcjonalne – kod bez "
                "województwa daje recenzenta oceniającego prace ze wszystkich województw. "
                f"Dopuszczalne wartości: {', '.join(Voivodeship.values)}."
            ),
        )
        parser.add_argument("--max-uses", type=int, default=1, help="Ile razy kod może zostać użyty.")
        parser.add_argument("--valid-days", type=int, default=14, help="Ważność kodu w dniach.")

    def handle(self, *args, **options):
        try:
            creator = User.objects.get(email=options["email"].strip().lower())
        except User.DoesNotExist as exc:
            raise CommandError(f"Nie znaleziono użytkownika {options['email']}.") from exc
        if not creator.groups.filter(name__in=COORDINATOR_GROUPS).exists():
            raise CommandError("Kody zaproszeń może tworzyć wyłącznie koordynator.")

        # Przyjmujemy też etykietę i formę przymiotnikową („woj. mazowieckie”, „mazowiecki”) –
        # komendę uruchamia człowiek z terminala, a lista wartości jest zamknięta, więc pomyłkę
        # w zapisie da się rozstrzygnąć bez zgadywania. Czego nie umiemy przypisać, odrzucamy.
        district = options["district"]
        if district is not None:
            normalized = normalize_voivodeship(district)
            if normalized is None:
                raise CommandError(
                    f"Nieznane województwo {district!r}. Dopuszczalne: {', '.join(Voivodeship.values)}."
                )
            district = normalized

        grants = InvitationGrantsStatus.PENDING if options["pending"] else InvitationGrantsStatus.ACTIVE
        invitation, plain_code = create_invitation(
            creator,
            valid_for=timedelta(days=options["valid_days"]),
            max_uses=options["max_uses"],
            grants_status=grants,
            is_appeals=options["appeals"],
            district=district,
        )
        self.stdout.write(
            self.style.SUCCESS(f"Kod zaproszenia (zapisz teraz, nie da się go odtworzyć): {plain_code}")
        )
        self.stdout.write(
            f"status={invitation.grants_status} appeals={invitation.is_appeals} "
            f"district={invitation.district or '-'} "
            f"max_uses={invitation.max_uses} expires_at={invitation.expires_at.isoformat()}"
        )
