"""``manage.py check_domains`` — czy domeny konkursów są wpuszczone w **trzech** miejscach naraz.

Dodanie konkursu z własną domeną wymaga zgodnej zmiany w trzech niezależnych konfiguracjach
(``docs/UNIWERSALNY-ETAP-1.md`` § 2.5):

1. ``EXTRA_DOMAINS`` — Caddy musi mieć blok dla tego hosta, inaczej nie wystawi certyfikatu
   i odpowie „no such site” jeszcze przed aplikacją,
2. ``DJANGO_ALLOWED_HOSTS`` — bez wpisu Django odpowiada 400 na **każde** żądanie z tej domeny,
3. ``DJANGO_CSRF_TRUSTED_ORIGINS`` — bez wpisu strony się otwierają, ale **każdy** formularz
   kończy się odmową weryfikacji CSRF.

Objawy są więc trzy różne, a przyczyna jedna: linijka wpisana w dwóch plikach z trzech. Ta komenda
porównuje stan bazy ze stanem ustawień i mówi wprost, czego brakuje. Uruchamia ją ``scripts/deploy.sh``
na końcu wdrożenia — ostrzeżenie w logu wdrożenia jest jedynym momentem, w którym ktoś na to patrzy
**zanim** zgłosi się uczestnik.

Domyślnie komenda **nie przerywa** wdrożenia (kod wyjścia 0): rozjazd domen nie psuje konkursów,
które już działają, a zatrzymane wdrożenie psuje. ``--strict`` odwraca tę decyzję i jest dla
monitoringu oraz dla CI.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.http.request import validate_host

from apps.tenancy.models import Competition, RoutingMode


class Command(BaseCommand):
    help = "Porównuje domeny konkursów z ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS i EXTRA_DOMAINS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Zakończ błędem, gdy któraś domena nie jest wpuszczona (monitoring, CI).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Pokaż także konkursy skonfigurowane poprawnie (domyślnie widać same rozjazdy).",
        )

    def handle(self, *args, **options):
        allowed_hosts = list(settings.ALLOWED_HOSTS)
        csrf_origins = set(settings.CSRF_TRUSTED_ORIGINS)
        # ``SITE_DOMAIN`` jest obsługiwany przez własny blok Caddy'ego (``{$SITE_DOMAIN}``), więc
        # z punktu widzenia proxy liczy się tak samo, jak wpis w ``EXTRA_DOMAINS``.
        proxy_hosts = {settings.SITE_DOMAIN, f"www.{settings.SITE_DOMAIN}"}
        proxy_hosts.update(getattr(settings, "EXTRA_DOMAINS", []))

        problems = 0
        checked = 0
        for competition in Competition.objects.select_related("site").order_by("slug"):
            if competition.routing_mode == RoutingMode.PATH:
                # Konkurs w trybie prefiksu ścieżki nie ma własnego hosta – odpowiada pod domeną
                # platformy, która jest sprawdzona przy Konkursie #1.
                continue
            checked += 1
            host = competition.primary_domain or competition.site.hostname
            missing = []
            if host not in proxy_hosts:
                missing.append(f"EXTRA_DOMAINS (brak „{host}” – Caddy nie wystawi certyfikatu)")
            if not validate_host(host, allowed_hosts):
                missing.append(f"DJANGO_ALLOWED_HOSTS (brak „{host}” – każde żądanie to 400)")
            if f"https://{host}" not in csrf_origins:
                missing.append(f"DJANGO_CSRF_TRUSTED_ORIGINS (brak „https://{host}” – formularze odmówią)")
            # Rozjazd między witryną a konkursem jest osobnym błędem: witrynę zmienia redaktor
            # w ``/cms/``, a ``primary_domain`` buduje linki w listach wysyłanych spoza żądania.
            if competition.primary_domain and competition.primary_domain != competition.site.hostname:
                missing.append(
                    f"wiersz Competition mówi „{competition.primary_domain}”, "
                    f"a witryna Wagtaila „{competition.site.hostname}”"
                )

            if missing:
                problems += 1
                self.stdout.write(
                    self.style.WARNING(f"UWAGA  {competition.slug} ({host}): " + "; ".join(missing))
                )
            elif options["all"]:
                self.stdout.write(f"ok     {competition.slug} ({host})")

        summary = f"Sprawdzono konkursy z własną domeną: {checked}, rozjazdów: {problems}."
        if problems and options["strict"]:
            raise CommandError(summary)
        self.stdout.write(self.style.WARNING(summary) if problems else self.style.SUCCESS(summary))
