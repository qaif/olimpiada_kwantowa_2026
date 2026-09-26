"""Pierwsze konto administratora djcms (idempotentne) – wzorzec ``bootstrap_coordinator`` backendu.

Dane wyłącznie ze zmiennych środowiskowych ``DJCMS_ADMIN_EMAIL`` i ``DJCMS_ADMIN_PASSWORD``, nie
z argumentów – argumenty trafiłyby do historii powłoki i do listy procesów (``ps``). Konto jest
superuserem **tylko djcms**: djcms ma własną bazę użytkowników i nie dzieli logowania z aplikacją
główną (§ 1.2 p. 1), więc ten sam adres e-mail w obu miejscach to dwa niezależne konta.

Login (``username``) = adres e-mail małymi literami – domyślny model użytkownika Django loguje po
``username``, a redakcja zna swoje adresy, nie wymyślone loginy.

Istniejące konto: uprawnienia są uzupełniane (``is_staff``, ``is_superuser``, ``is_active``),
hasło **nie** jest zmieniane, chyba że podano ``--reset-password``. Dzięki temu ``deploy.sh``
może wołać komendę przy każdym wdrożeniu bez nadpisywania hasła zmienionego w panelu.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.views.decorators.debug import sensitive_variables


class Command(BaseCommand):
    help = "Tworzy (lub uzupełnia) konto superusera djcms z DJCMS_ADMIN_EMAIL/DJCMS_ADMIN_PASSWORD."

    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true", help="Nadpisz hasło istniejącego konta.")

    @sensitive_variables()
    @transaction.atomic
    def handle(self, *args, **options):
        email = (os.environ.get("DJCMS_ADMIN_EMAIL") or "").strip().lower()
        password = os.environ.get("DJCMS_ADMIN_PASSWORD") or ""
        if not email or not password:
            raise CommandError("Ustaw DJCMS_ADMIN_EMAIL i DJCMS_ADMIN_PASSWORD w środowisku procesu.")
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError(f"DJCMS_ADMIN_EMAIL nie jest poprawnym adresem: {email!r}") from exc

        User = get_user_model()
        user = User.objects.filter(username=email).first()
        created = user is None
        if created:
            user = User(username=email, email=email)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        if created or options["reset_password"]:
            try:
                validate_password(password, user)
            except ValidationError as exc:
                raise CommandError("Hasło odrzucone: " + " ".join(exc.messages)) from exc
            user.set_password(password)
        user.save()
        verb = "utworzono" if created else "zaktualizowano"
        self.stdout.write(self.style.SUCCESS(f"Administrator djcms {verb}: {email} (superuser)"))
