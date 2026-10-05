"""``manage.py check_mail_dns <domena> [...]`` – czy domena nadawcy ma SPF, DKIM i DMARC dla relaya.

Uruchamiana w kontenerze ``web`` (``docker compose exec web python manage.py check_mail_dns …``):
``web`` ma wyjście na świat przez sieć ``edge``, a ``worker`` i ``beat`` siedzą wyłącznie w sieci
``internal: true`` i zapytania DNS o cudze domeny tam nie wychodzą.

Wynik trafia do ``mail_domains.SenderDomain`` (``--no-save`` pomija zapis) – z niego pulpit
koordynatora bierze ostrzeżenie o niezweryfikowanym nadawcy. Kod wyjścia 1, gdy którakolwiek domena
nie przeszła: ``scripts/mail_add_domain.sh --check`` i operator w terminalu widzą to tak samo.

``--suggest`` wypisuje w liniach ``klucz=wartość`` sugerowany rekord SPF (scalony z istniejącym),
zdanie wyjaśnienia, opublikowany DMARC i MX – czyta je ``scripts/mail_add_domain.sh``, żeby nie
powtarzać logiki scalania w powłoce.
"""

from __future__ import annotations

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.mail_domains import checks
from apps.mail_domains.dnsquery import DnsError, Resolver
from apps.mail_domains.services import record_check

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)

LABELS = {
    checks.STATUS_OK: "OK",
    checks.STATUS_WARN: "UWAGA",
    checks.STATUS_FAIL: "BŁĄD",
    checks.STATUS_ERROR: "DNS?",
}


class Command(BaseCommand):
    help = "Sprawdza SPF/DKIM/DMARC domeny nadawcy dla relaya platformy (docs/OPERACJE.md § 49)."

    #: Atrapa resolwera w testach (``Command.resolver_factory = lambda ns: FakeResolver(...)``).
    resolver_factory = staticmethod(lambda nameservers: Resolver(nameservers or None))

    def add_arguments(self, parser) -> None:
        parser.add_argument("domains", nargs="+", help="domena nadawcy, np. iqo-official.org")
        parser.add_argument(
            "--ip", default="", help="IPv4 relaya (domyślnie MAIL_PUBLIC_IP albo A mail.<SITE_DOMAIN>)"
        )
        parser.add_argument(
            "--selector", default="", help="selektor DKIM (domyślnie DKIM_SELECTOR, 'olimpiada')"
        )
        parser.add_argument(
            "--dkim-public-key", default="", help="klucz p= z kontenera mail – porównanie z DNS"
        )
        parser.add_argument("--dmarc-rua", default=None, help="adres raportów DMARC w sugestii")
        parser.add_argument("--nameserver", action="append", default=[], help="serwer DNS (można kilka)")
        parser.add_argument("--no-save", action="store_true", help="bez zapisu wyniku w bazie")
        parser.add_argument(
            "--suggest", action="store_true", help="sugerowany SPF, opublikowany DMARC i MX (klucz=wartość)"
        )
        parser.add_argument("--json", action="store_true", help="raport jako JSON")

    def handle(self, *args, **options) -> None:
        domains = [d.strip().rstrip(".").lower() for d in options["domains"]]
        for domain in domains:
            if not DOMAIN_RE.match(domain):
                raise CommandError(f"To nie jest nazwa domeny: {domain!r}")
        if options["dkim_public_key"] and len(domains) > 1:
            raise CommandError("--dkim-public-key dotyczy jednej domeny – podaj jedną.")
        resolver = self.resolver_factory(options["nameserver"] or list(settings.MAIL_DNS_NAMESERVERS))
        ip = self._relay_ip(resolver, options["ip"])
        selector = options["selector"] or settings.MAIL_DKIM_SELECTOR
        rua = settings.MAIL_DMARC_RUA if options["dmarc_rua"] is None else options["dmarc_rua"]

        reports = [
            checks.check_domain(
                resolver,
                domain,
                ip=ip,
                selector=selector,
                expected_dkim=options["dkim_public_key"],
                dmarc_rua=rua,
            )
            for domain in domains
        ]
        if options["suggest"]:
            # Format dla powłoki: ``klucz=wartość``, jedna linia na klucz, bez cudzysłowów.
            for report in reports:
                self.stdout.write(f"spf={report.suggested_spf}")
                self.stdout.write(f"spf_note={report.spf_note}")
                self.stdout.write(f"dmarc={report.existing_dmarc}")
                self.stdout.write(f"mx={','.join(report.mx_hosts)}")
            return
        if not options["no_save"]:
            for report in reports:
                record_check(report)
        if options["json"]:
            self.stdout.write(json.dumps([r.as_dict() for r in reports], ensure_ascii=False, indent=2))
        else:
            for report in reports:
                self._print(report)
        failed = [r.domain for r in reports if not r.verified]
        if failed:
            raise CommandError("Niezweryfikowane: " + ", ".join(failed) + " (docs/OPERACJE.md § 49).")

    def _relay_ip(self, resolver, explicit: str) -> str:
        """Adres, który SPF ma autoryzować. Rekord A ``mail.<SITE_DOMAIN>`` to nazwa z HELO relaya."""
        candidate = explicit or settings.MAIL_PUBLIC_IP
        if not candidate:
            try:
                found = resolver.a(f"mail.{settings.SITE_DOMAIN}")
            except DnsError:
                found = []
            candidate = found[0] if found else ""
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", candidate or ""):
            raise CommandError(
                "Nie znam adresu IPv4 relaya – podaj --ip (albo MAIL_PUBLIC_IP w .env, albo rekord A "
                f"mail.{settings.SITE_DOMAIN})."
            )
        return candidate

    def _print(self, report: checks.DomainReport) -> None:
        self.stdout.write(f"{report.domain}  (selektor DKIM {report.selector}, IP relaya {report.ip})")
        for item in report.checks:
            self.stdout.write(f"  {LABELS[item.status]:6} {item.name.upper():6} {item.detail}")
            if item.record:
                self.stdout.write(f"         rekord: {item.record}")
            if item.hint and item.status not in (checks.STATUS_OK, checks.STATUS_WARN):
                self.stdout.write(f"         → {item.hint}")
        verdict = "ZWERYFIKOWANA" if report.verified else "NIE zweryfikowana"
        style = self.style.SUCCESS if report.verified else self.style.ERROR
        self.stdout.write(style(f"  wynik: {verdict}"))
        self.stdout.write("")
