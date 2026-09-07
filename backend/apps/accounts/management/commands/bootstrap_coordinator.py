"""Pierwsze konto koordynatora na świeżej instalacji (idempotentne).

Dane przychodzą wyłącznie ze zmiennych środowiskowych ``COORDINATOR_EMAIL`` i ``COORDINATOR_PASSWORD``
(nie z argumentów – te trafiłyby do historii powłoki i listy procesów). Konto jest superuserem
(dostęp do /admin/ i /cms/) i należy do grupy ``coordinator``. Jeśli konto istnieje, hasło nie jest
zmieniane, chyba że podano ``--reset-password``.
"""

import os

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from apps.accounts.models import User
from apps.accounts.services import make_coordinator


class Command(BaseCommand):
    help = "Tworzy (lub uzupełnia) pierwsze konto koordynatora z COORDINATOR_EMAIL/COORDINATOR_PASSWORD."

    def add_arguments(self, parser):
        parser.add_argument("--reset-password", action="store_true", help="Nadpisz hasło istniejącego konta.")

    @sensitive_variables()
    @transaction.atomic
    def handle(self, *args, **options):
        email = (os.environ.get("COORDINATOR_EMAIL") or "").strip().lower()
        password = os.environ.get("COORDINATOR_PASSWORD") or ""
        if not email or not password:
            raise CommandError("Ustaw COORDINATOR_EMAIL i COORDINATOR_PASSWORD w środowisku procesu.")

        user = User.objects.filter(email=email).first()
        created = user is None
        if created:
            user = User(email=email, is_staff=True, is_superuser=True, is_active=True)
            validate_password(password, user)
            user.set_password(password)
            user.save()
        else:
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            if options["reset_password"]:
                validate_password(password, user)
                user.set_password(password)
            user.save()
        make_coordinator(user)
        verb = "utworzono" if created else "zaktualizowano"
        self.stdout.write(self.style.SUCCESS(f"Koordynator {verb}: {email} (superuser, grupa coordinator)"))
