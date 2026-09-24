"""Potwierdzenie umowy powierzenia (DPA) z dostawcą AI – komenda operatora.

Organizator potwierdza umowy zwykle sam, w panelu (``/coordinator/ai-grading/``, pole
„Potwierdzam zawarcie umowy powierzenia (DPA) z …”). Komenda istnieje dla sytuacji, w której
oświadczenie organizatora przychodzi **inną drogą** (rozmowa, e-mail) i operator ma je wpisać – np.
po wdrożeniu wersji z dostawcami, bo migracja celowo niczego nie „domniemywa”:

    python manage.py confirm_ai_provider_dpa --competition kwantowa --provider all \\
        --confirmed-by koordynator@example.com --note "potwierdzone przez organizatora w rozmowie 24.09.2026"

Zapis jest **dokładnie ten sam** co z panelu (``services.set_dpa_confirmation``): data, konto
potwierdzającego i wpis ``ai_grading.dpa_confirmed`` w dzienniku zdarzeń z uwagą i znacznikiem
``via: "command"``. Komenda jest idempotentna – umowa już potwierdzona zostaje z pierwotną datą
i osobą, bez nowego wpisu. Konto potwierdzającego musi istnieć i być aktywne: oświadczenie prawne
ma mieć autora, a nie „operatora”.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ai_grading.models import AiProvider


class Command(BaseCommand):
    help = "Zapisuje potwierdzenie umowy powierzenia (DPA) z dostawcą AI – tak jak pole w panelu."

    def add_arguments(self, parser):
        parser.add_argument("--competition", required=True, help="Identyfikator (slug) konkursu.")
        parser.add_argument(
            "--provider",
            required=True,
            choices=[*AiProvider.values, "all"],
            help="Dostawca albo „all” (wszyscy).",
        )
        parser.add_argument(
            "--confirmed-by", required=True, dest="confirmed_by", help="Adres e-mail konta potwierdzającego."
        )
        parser.add_argument("--note", default="", help="Uwaga do potwierdzenia (np. data i forma umowy).")

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.accounts.services import has_role
        from apps.ai_grading.services import provider_label, set_dpa_confirmation
        from apps.tenancy.context import competition_context
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=options["competition"]).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{options['competition']}”.")
        email = (options["confirmed_by"] or "").strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is None or not user.is_active:
            raise CommandError(f"Nie ma aktywnego konta o adresie „{email}”.")
        if not has_role(user, competition, "coordinator"):
            self.stderr.write(
                self.style.WARNING(
                    f"Uwaga: konto {email} nie ma roli koordynatora w konkursie {competition.slug} – "
                    "potwierdzenie zostanie zapisane na nie mimo to."
                )
            )
        names = list(AiProvider.values) if options["provider"] == "all" else [options["provider"]]
        with competition_context(competition):
            for name in names:
                account, changed = set_dpa_confirmation(
                    competition, name, True, actor=user, note=options["note"], via="command"
                )
                label = provider_label(name)
                if changed:
                    self.stdout.write(
                        self.style.SUCCESS(f"{label}: umowa powierzenia potwierdzona ({email}).")
                    )
                else:
                    who = account.dpa_confirmed_by.email if account.dpa_confirmed_by else "?"
                    when = f"{account.dpa_confirmed_at:%Y-%m-%d %H:%M}"
                    self.stdout.write(f"{label}: już potwierdzona {when} ({who}) – bez zmian.")
                if not account.has_key:
                    self.stdout.write(
                        f"  {label}: brak klucza API – dostawca zacznie działać po jego dodaniu."
                    )
