"""Listy do płacącego: potwierdzenie wpłaty z numerem faktury i potwierdzenie zwrotu (PAY-01 § 3, § 5).

Język **płacącego** (``language_for`` jego konta, z językiem konkursu jako odwrotem), a nie osoby,
która akurat wywołała zapis – wpłatę przelewem oznacza koordynator po polsku, a list ma dostać
opiekun z Brazylii po portugalsku. Listy idą przez ``queue_mail`` (po commicie): zapis wpłaty nie może
zależeć od działania serwera poczty.

PDF-a nie dołączamy: list zawiera wszystkie dane wpłaty (kwota, kod, numer faktury), a dokument leży
w panelu za logowaniem – załącznik z danymi nabywcy krążyłby po skrzynkach bez kontroli dostępu.
"""

from __future__ import annotations

from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.activation import absolute_url, queue_mail
from apps.accounts.preferences import language_for
from apps.tenancy import branding

RECEIPT_SUBJECT = "payments/mail/receipt_subject.txt"
RECEIPT_BODY = "payments/mail/receipt_body.txt"
REFUND_SUBJECT = "payments/mail/refund_subject.txt"
REFUND_BODY = "payments/mail/refund_body.txt"


def _recipients(order) -> list[str]:
    emails = [order.buyer_email]
    if order.created_by_id and order.created_by.email:
        emails.append(order.created_by.email)
    seen: list[str] = []
    for email in emails:
        if email and email.lower() not in (item.lower() for item in seen):
            seen.append(email)
    return seen


def _send(order, subject_template: str, body_template: str, context: dict) -> None:
    competition = order.competition
    link = absolute_url(reverse("web:payment-order", args=[order.pk]), None, competition)
    context = {**context, "order": order, "link": link}
    with language_for(order.created_by, competition):
        context["brand"] = branding.brand_names(competition)
        subject = render_to_string(subject_template, context).strip().replace("\n", " ")
        body = render_to_string(body_template, context)
    for email in _recipients(order):
        queue_mail(subject, body, email, competition=competition)


def send_receipt(order, invoice) -> None:
    _send(order, RECEIPT_SUBJECT, RECEIPT_BODY, {"invoice": invoice})


def send_refund_notice(refund) -> None:
    order = refund.payment.order
    _send(order, REFUND_SUBJECT, REFUND_BODY, {"refund": refund})
