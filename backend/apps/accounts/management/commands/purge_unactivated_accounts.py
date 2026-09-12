"""Ręczne uruchomienie kosiarki kont nieaktywowanych (to samo, co robi ``beat`` co 15 minut).

Po co osobna komenda, gdy zadanie i tak chodzi cyklicznie: koordynator dostaje telefon „nie mogę
się zarejestrować, pisze że konto już istnieje” i musi zwolnić adres **teraz**, a nie w najbliższym
kwadransie. Komenda nie ma własnej logiki – woła dokładnie tę funkcję, którą woła zadanie Celery,
więc nie ma szansy rozjechać się z przebiegiem automatycznym.
"""

from django.core.management.base import BaseCommand

from apps.accounts.tasks import purge_unactivated_accounts


class Command(BaseCommand):
    help = "Kasuje konta, których adresu e-mail nikt nie potwierdził w oknie aktywacji (4 h)."

    def handle(self, *args, **options):
        result = purge_unactivated_accounts()
        self.stdout.write(
            self.style.SUCCESS(
                "Konta nieaktywowane: skasowano {deleted}, pominięto {skipped}.".format(**result)
            )
        )
