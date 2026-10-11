"""Zapis wyniku sprawdzenia DNS i ostrzeżenia dla koordynatora (MAIL-01 § 4.1–4.3)."""

from __future__ import annotations

from email.utils import parseaddr

from django.conf import settings
from django.utils import timezone

from apps.mail_domains.checks import DomainReport
from apps.mail_domains.models import SenderDomain

#: Nazwy sprawdzeń w zdaniach dla koordynatora.
CHECK_LABELS = {"spf": "SPF", "dkim": "DKIM", "dmarc": "DMARC"}


def email_domain(address: str) -> str:
    """Domena adresu (także w postaci „Nazwa <adres>”), małymi literami; pusty napis bez ``@``."""
    _name, email = parseaddr(address or "")
    if "@" not in email:
        return ""
    return email.rpartition("@")[2].strip().rstrip(".").lower()


def installation_domain() -> str:
    """Domena ``DEFAULT_FROM_EMAIL`` – jej rekordy wypisuje krok 7/8 wdrożenia (``mail-dns.txt``)."""
    return email_domain(settings.DEFAULT_FROM_EMAIL)


def record_check(report: DomainReport, *, now=None) -> SenderDomain:
    """Zapisuje wynik ``check_mail_dns``. ``verified_at`` przesuwa tylko udane sprawdzenie."""
    now = now or timezone.now()
    row, _created = SenderDomain.objects.get_or_create(
        domain=report.domain.lower(), defaults={"checked_at": now}
    )
    row.verified = report.verified
    row.checked_at = now
    if report.verified:
        row.verified_at = now
    row.report = report.as_dict()
    row.save()
    return row


def sender_warnings(competition) -> list[str]:
    """Zdania o nadawcy listów konkursu, które koordynator ma zobaczyć. Pusta lista = wszystko w porządku.

    Bez zapytań DNS (pulpit nie czeka na resolwer) i bez zapytań do bazy, gdy nie ma o czym mówić:
    pusty ``from_email`` i domena instalacji kończą funkcję przed bazą. Wariant B
    (``ALLOWED_SENDER_DOMAINS=*``, zewnętrzny dostawca) też – rekordy autoryzują wtedy dostawcę,
    a ``check_mail_dns`` sprawdza adres naszego relaya, więc jego wynik nic by tam nie znaczył.
    """
    from apps.core.tasks import sender_domain_allowed

    sender = (getattr(competition, "from_email", "") or "").strip()
    domain = email_domain(sender)
    if not domain or domain == installation_domain():
        return []
    if getattr(settings, "MAIL_ALLOWED_SENDER_DOMAINS", None) is None:
        return []
    fallback = settings.DEFAULT_FROM_EMAIL
    if not sender_domain_allowed(sender):
        return [
            f"Nadawca listów {sender} nie jest obsługiwany przez serwer poczty platformy (domena "
            f"{domain} nie została dodana) – listy konkursu wychodzą od {fallback}. Poproś operatora "
            f"platformy o dodanie domeny (docs/OPERACJE.md § 49) albo wyczyść pole „Nadawca listów”."
        ]
    row = SenderDomain.objects.filter(domain=domain).first()
    if row is None:
        return [
            f"Rekordy DNS poczty domeny {domain} (SPF, DKIM, DMARC) nie zostały jeszcze sprawdzone – "
            f"listy od {sender} mogą trafiać do spamu albo być odrzucane. Poproś operatora platformy "
            f"o weryfikację (docs/OPERACJE.md § 49) albo wyczyść pole „Nadawca listów”, a listy pójdą "
            f"od {fallback}."
        ]
    if not row.verified:
        failed = [
            CHECK_LABELS.get(item.get("name"), item.get("name"))
            for item in (row.report or {}).get("checks", [])
            if item.get("status") not in ("ok", "warn")
        ]
        what = ", ".join(failed) or "rekordy poczty"
        checked = timezone.localtime(row.checked_at).strftime("%d.%m.%Y %H:%M")
        return [
            f"Ostatnie sprawdzenie DNS domeny {domain} ({checked}) nie przeszło: {what}. Listy od "
            f"{sender} mogą trafiać do spamu albo być odrzucane. Poproś operatora platformy o poprawkę "
            f"(docs/OPERACJE.md § 49) albo wyczyść pole „Nadawca listów”, a listy pójdą od {fallback}."
        ]
    return []
