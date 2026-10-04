"""Czynności płatności: cennik, nabywca, zniżki, zamówienia, zapłata, webhooki, zwroty (PAY-01).

Widoki tylko orkiestrują; każda reguła ma tu jedno miejsce i każda czynność zmieniająca stan pisze
wpis audytu. Reguły, które warto znać, zanim zmieni się cokolwiek w tym pliku:

1. **Kwota zawsze z serwera.** ``Order.total`` liczy :mod:`apps.payments.pricing` (delegacja) albo
   kopiuje ``ParticipantFee.amount`` (uczestnik); ``Payment.amount`` jest jej kopią. Żaden formularz
   nie przesyła kwoty zapłaty, a webhook dostawcy jest z nią **porównywany**.
2. **„Zapłacone” ustawia wyłącznie webhook z poprawnym podpisem** (albo koordynator dla przelewu).
   Adres powrotu z Checkout niczego nie zapisuje – da się go otworzyć bez płacenia.
3. **Idempotencja doręczeń**: wiersz ``ProviderEvent`` powstaje w tej samej transakcji, co skutek
   zdarzenia. Duplikat kończy się na więzie unikalności, a błąd przetwarzania wycofuje oba – dostawca
   ponowi doręczenie i spróbujemy jeszcze raz.
4. **Wpłata jest zawsze zapisywana**, nawet gdy przyszła „nie w porę” (zamówienie anulowane albo już
   zapłacone inną próbą): pieniądze są faktem. Stan ``MISMATCH`` oznacza wtedy sprawę dla człowieka
   (zwrot), zamiast cichego odrzucenia.
5. **Sieć poza blokadą.** Rozmowa z dostawcą przy zakładaniu sesji i zwrocie idzie po zatwierdzeniu
   wiersza próby – blokada wiersza zamówienia nie trwa tyle, ile odpowiedź Stripe'a.
"""

from __future__ import annotations

import hashlib
import logging
import uuid as uuid_module
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy.fees import quantize_money

from . import documents, pricing
from .models import (
    DELEGATION_KINDS,
    ZERO,
    AdjustmentKind,
    BillingDocument,
    BillingProfile,
    DocumentKind,
    FeeAdjustment,
    Order,
    OrderLine,
    OrderStatus,
    Payment,
    PaymentSettings,
    PaymentStatus,
    PriceItem,
    PriceKind,
    PriceList,
    PricePeriod,
    Provider,
    ProviderEvent,
    Refund,
    RefundLine,
    RefundStatus,
    ScanStatus,
    enabled,
    new_reference,
)
from .providers import ProviderError, SignatureError, get_provider
from .providers.base import HTTP_TIMEOUT, CheckoutRequest, to_minor
from .providers.przelewy24 import TIME_LIMIT_MINUTES

logger = logging.getLogger(__name__)

#: Po ilu minutach od założenia transakcji P24 wolno uznać ją za porzuconą (limit u dostawcy + zapas).
P24_ABANDON_MINUTES = TIME_LIMIT_MINUTES + 5

#: Prefiks kluczy dowodów wpłaty w prywatnym storage (ten sam bucket, co zaświadczenia i prace).
PROOF_PREFIX = "payments"


def _error(message, code: str, status_code: int = http.HTTP_409_CONFLICT) -> DomainError:
    return DomainError(str(message), code, status_code)


def require_enabled(competition) -> None:
    """404, gdy konkurs nie pobiera opłat – ekranów i czynności wtedy **nie ma** (jak przy fladze)."""
    if not enabled(competition):
        raise Http404("Ten konkurs nie pobiera opłat.")


def _require_coordinator(actor, competition) -> None:
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    if not has_role(actor, competition, CompetitionRole.COORDINATOR):
        raise _error(
            "Ta czynność należy do koordynatora konkursu.", "COORDINATOR_REQUIRED", http.HTTP_403_FORBIDDEN
        )


def _actor(user):
    return user if user is not None and getattr(user, "is_authenticated", False) else None


# --- ustawienia i cennik --------------------------------------------------------------------------


def settings_for(competition) -> PaymentSettings:
    """Ustawienia konkursu albo **niezapisany** wiersz z wartościami domyślnymi."""
    return PaymentSettings.objects.filter(competition=competition).first() or PaymentSettings(
        competition=competition
    )


SETTINGS_FIELDS = (
    "seller_tax_id",
    "bank_account",
    "bank_swift",
    "bank_name",
    "document_prefix",
    "vat_note",
    "invoice_note",
    "proforma_due_days",
    "card_payments",
    "p24_payments",
    "bank_transfer",
)


def save_settings(competition, *, actor, request=None, **fields) -> PaymentSettings:
    _require_coordinator(actor, competition)
    row = settings_for(competition)
    changed = [name for name in SETTINGS_FIELDS if name in fields and getattr(row, name) != fields[name]]
    for name in SETTINGS_FIELDS:
        if name in fields:
            setattr(row, name, fields[name])
    row.full_clean()
    row.save()
    if changed:
        audit(actor, "payments.settings_updated", row, {"fields": changed}, request=request)
    return row


def price_key(kind: str, period: str) -> str:
    return f"{kind}:{period}"


def save_price_list(
    competition,
    edition,
    *,
    currency: str,
    early_until=None,
    late_from=None,
    is_active: bool = True,
    prices: dict[str, Decimal | None],
    actor,
    request=None,
) -> PriceList:
    """Zakłada albo zmienia cennik delegacji edycji. Wystawione zamówienia **nie zmieniają się**.

    Zmiana waluty po pierwszym zamówieniu jest odmową: pokrycie liczy ilości, a nie kwoty, więc
    zamówienia w dwóch walutach jednej delegacji dawałyby sumę, której nie da się wyrazić.
    """
    _require_coordinator(actor, competition)
    if edition.competition_id != competition.pk:
        raise Http404("Edycja należy do innego konkursu.")
    currency = (currency or "").strip().upper()
    with transaction.atomic():
        price_list = PriceList.objects.select_for_update(of=("self",)).filter(edition=edition).first()
        if price_list is not None and price_list.currency != currency:
            if (
                Order.objects.filter(edition=edition, delegation__isnull=False)
                .exclude(status=OrderStatus.CANCELLED)
                .exists()
            ):
                raise _error(
                    "Waluty cennika nie można zmienić – w tej edycji są już wystawione zamówienia.",
                    "CURRENCY_LOCKED",
                )
        price_list = price_list or PriceList(competition=competition, edition=edition)
        price_list.currency = currency
        price_list.early_until = early_until
        price_list.late_from = late_from
        price_list.is_active = is_active
        price_list.full_clean()
        price_list.save()
        for kind in DELEGATION_KINDS:
            for period in PricePeriod.values:
                value = prices.get(price_key(kind, period))
                if value is None:
                    PriceItem.objects.filter(price_list=price_list, kind=kind, period=period).delete()
                    continue
                amount = quantize_money(value)
                if amount < 0:
                    raise _error("Cena nie może być ujemna.", "NEGATIVE_PRICE", http.HTTP_400_BAD_REQUEST)
                PriceItem.objects.update_or_create(
                    price_list=price_list, kind=kind, period=period, defaults={"amount": amount}
                )
    audit(
        actor,
        "payments.price_list_saved",
        price_list,
        {
            "currency": currency,
            "early_until": str(early_until or ""),
            "late_from": str(late_from or ""),
            "is_active": is_active,
            "prices": {key: str(value) for key, value in prices.items() if value is not None},
        },
        request=request,
    )
    return price_list


# --- nabywca --------------------------------------------------------------------------------------

BUYER_FIELDS = ("buyer_type", "buyer_name", "buyer_address", "buyer_country", "buyer_vat_id", "buyer_email")


def billing_profile_of(*, delegation=None, participant=None) -> BillingProfile | None:
    if delegation is not None:
        return BillingProfile.objects.filter(delegation=delegation).first()
    return BillingProfile.objects.filter(participant=participant).first()


def save_billing_profile(
    *, delegation=None, participant=None, actor, request=None, **fields
) -> BillingProfile:
    """Dane nabywcy płacącego. Opiekun swojej delegacji, uczestnik – siebie (sprawdza wołający serwis)."""
    if delegation is not None and not is_leader(actor, delegation):
        raise Http404("Nie ma takiej delegacji.")
    if participant is not None and participant.user_id != getattr(actor, "pk", None):
        raise Http404("Nie ma takiego uczestnika.")
    profile = billing_profile_of(delegation=delegation, participant=participant) or BillingProfile(
        delegation=delegation, participant=participant
    )
    changed = []
    for name in (*BUYER_FIELDS, "observers"):
        if name in fields:
            value = fields[name].strip() if isinstance(fields[name], str) else fields[name]
            if getattr(profile, name) != value:
                changed.append(name)
            setattr(profile, name, value)
    profile.updated_by = _actor(actor)
    profile.full_clean(exclude=["updated_by"])
    profile.save()
    if changed:
        # Do audytu wyłącznie nazwy pól – wartości są danymi nabywcy, a dziennik czytają inni.
        audit(actor, "payments.billing_profile_saved", profile, {"fields": changed}, request=request)
    return profile


# --- zniżki i zwolnienia --------------------------------------------------------------------------


def add_adjustment(delegation, *, kind: str, amount=None, reason: str, actor, request=None) -> FeeAdjustment:
    competition = delegation.competition
    require_enabled(competition)
    _require_coordinator(actor, competition)
    reason = (reason or "").strip()
    if not reason:
        raise _error(
            "Zniżka i zwolnienie wymagają uzasadnienia.", "REASON_REQUIRED", http.HTTP_400_BAD_REQUEST
        )
    if kind not in AdjustmentKind.values:
        raise _error("Nieznany rodzaj decyzji.", "ADJUSTMENT_KIND", http.HTTP_400_BAD_REQUEST)
    value = None
    if kind == AdjustmentKind.DISCOUNT:
        try:
            value = quantize_money(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise _error("Podaj kwotę zniżki.", "AMOUNT_REQUIRED", http.HTTP_400_BAD_REQUEST) from exc
        if value <= 0:
            raise _error("Kwota zniżki musi być dodatnia.", "AMOUNT_REQUIRED", http.HTTP_400_BAD_REQUEST)
    adjustment = FeeAdjustment.objects.create(
        competition=competition,
        delegation=delegation,
        kind=kind,
        amount=value,
        reason=reason,
        created_by=actor,
    )
    audit(
        actor,
        "payments.adjustment_added",
        adjustment,
        {"delegation": delegation.pk, "kind": kind, "amount": str(value or ""), "reason": reason},
        request=request,
    )
    return adjustment


def revoke_adjustment(adjustment: FeeAdjustment, *, reason: str, actor, request=None) -> FeeAdjustment:
    _require_coordinator(actor, adjustment.competition)
    reason = (reason or "").strip()
    if not reason:
        raise _error("Cofnięcie wymaga uzasadnienia.", "REASON_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if adjustment.revoked_at is not None:
        return adjustment
    adjustment.revoked_at = timezone.now()
    adjustment.revoked_by = actor
    adjustment.revoke_reason = reason
    adjustment.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    audit(actor, "payments.adjustment_revoked", adjustment, {"reason": reason}, request=request)
    return adjustment


# --- dostęp płacącego -----------------------------------------------------------------------------


def is_leader(user, delegation) -> bool:
    from apps.accounts.delegations import DelegationLeader

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    # Wyłącznie opiekun **czynny** – odwołany (``removed_at``) traci wgląd w zamówienia i dokumenty (H1).
    return DelegationLeader.objects.active().filter(delegation=delegation, user=user).exists()


def can_pay(user, order: Order) -> bool:
    """Czy ta osoba jest płacącym zamówienia: opiekunem jego delegacji albo właścicielem należności."""
    if order.delegation_id:
        return is_leader(user, order.delegation)
    if not getattr(user, "is_authenticated", False):
        return False
    return order.participant_fee.participant.user_id == user.pk


def order_for_payer(user, competition, pk: int) -> Order:
    """Zamówienie **tego konkursu**, którego ta osoba jest płacącym – albo 404."""
    require_enabled(competition)
    order = (
        Order.objects.for_competition(competition)
        .select_related("delegation", "delegation__country", "participant_fee__participant", "edition")
        .filter(pk=pk)
        .first()
    )
    if order is None or not can_pay(user, order):
        raise Http404("Nie ma takiego zamówienia.")
    return order


def payer_language(user, competition) -> str:
    """Język płacącego (konto → konkurs → instalacja) – w nim powstają pozycje, dokumenty i listy."""
    from apps.accounts.preferences import language_for

    with language_for(user, competition):
        return translation.get_language() or "en"


# --- zamówienia -----------------------------------------------------------------------------------


def _unique_reference(competition) -> str:
    for _attempt in range(10):
        reference = new_reference(competition)
        if not Order.objects.filter(reference=reference).exists():
            return reference
    raise _error("Nie udało się nadać kodu zamówienia – spróbuj ponownie.", "REFERENCE_EXHAUSTED")


def _line_description(kind: str, period: str, price_list, delegation) -> str:
    labels = dict(PriceKind.choices)
    text = str(labels[kind])
    if kind == PriceKind.DELEGATION and delegation is not None:
        text = f"{text}: {delegation.country.name}"
    has_periods = price_list is not None and (price_list.early_until or price_list.late_from)
    if period and has_periods and kind != PriceKind.DISCOUNT:
        text = f"{text} ({dict(PricePeriod.choices)[period]})"
    return text


def _snapshot_buyer(order: Order, profile: BillingProfile) -> None:
    for name in BUYER_FIELDS:
        setattr(order, name, getattr(profile, name))


def prepare_delegation_order(delegation, *, actor, request=None) -> Order:
    """Wystawia zamówienie na **niepokryty** przyrost składu delegacji – z numerem pro formy."""
    from apps.accounts.delegations import Delegation

    competition = delegation.competition
    require_enabled(competition)
    if not is_leader(actor, delegation):
        _require_coordinator(actor, competition)
    language = payer_language(actor, competition)
    with transaction.atomic():
        # Blokada wiersza delegacji szereguje dwóch opiekunów klikających „Wystaw” naraz – inaczej
        # obaj policzyliby ten sam przyrost i powstałyby dwie pro formy na tych samych uczniów.
        Delegation.objects.select_for_update(of=("self",)).filter(pk=delegation.pk).values_list(
            "pk", flat=True
        ).first()
        profile = billing_profile_of(delegation=delegation)
        if profile is None:
            raise _error(_("Najpierw uzupełnij dane do faktury."), "BILLING_PROFILE_MISSING")
        statement = pricing.statement(delegation)
        if statement.state == pricing.STATE_NO_PRICES:
            raise _error(_("Organizator nie opublikował jeszcze cennika."), "NO_PRICE_LIST")
        if statement.waived:
            raise _error(_("Delegacja jest zwolniona z opłat."), "WAIVED")
        if statement.new_total <= 0:
            raise _error(_("Nie ma nic nowego do zapłaty."), "NOTHING_TO_PAY")
        settings_row = settings_for(competition)
        order = Order(
            competition=competition,
            edition=delegation.edition,
            delegation=delegation,
            currency=statement.currency,
            total=statement.new_total,
            period=statement.period,
            language=language,
            created_by=_actor(actor),
            due_on=pricing.competition_today(competition) + timedelta(days=settings_row.proforma_due_days),
            reference=_unique_reference(competition),
        )
        _snapshot_buyer(order, profile)
        order.full_clean(exclude=["created_by"])
        order.save()
        drafts = list(statement.new_lines)
        if statement.discount_line is not None:
            drafts.append(statement.discount_line)
        with translation.override(language):
            OrderLine.objects.bulk_create(
                OrderLine(
                    order=order,
                    position=index,
                    kind=draft.kind,
                    description=_line_description(draft.kind, draft.period, statement.price_list, delegation),
                    quantity=draft.quantity,
                    unit_price=draft.unit_price,
                    amount=draft.amount,
                )
                for index, draft in enumerate(drafts)
            )
        proforma = documents.issue_document(order, DocumentKind.PROFORMA)
    audit(
        actor,
        "payments.order_created",
        order,
        {
            "delegation": delegation.pk,
            "reference": order.reference,
            "total": str(order.total),
            "currency": order.currency,
            "period": order.period,
            "proforma": proforma.number,
        },
        request=request,
    )
    return order


def prepare_participant_order(fee, *, actor, request=None, **buyer) -> Order:
    """Zamówienie na należność uczestnika (``ParticipantFee``). Otwarte już – oddaje to samo."""
    from apps.tenancy.fees import ParticipantFee

    participant = fee.participant
    competition = participant.competition
    require_enabled(competition)
    if participant.user_id != getattr(actor, "pk", None):
        raise Http404("Nie ma takiej należności.")
    language = payer_language(actor, competition)
    with transaction.atomic():
        fee = (
            ParticipantFee.objects.select_for_update(of=("self",))
            .select_related("schedule__edition")
            .get(pk=fee.pk)
        )
        if fee.is_settled:
            raise _error(_("Ta opłata jest już rozliczona."), "FEE_SETTLED")
        if fee.amount <= 0:
            raise _error(_("Nie ma nic do zapłaty."), "NOTHING_TO_PAY")
        existing = Order.objects.filter(participant_fee=fee, status=OrderStatus.OPEN).first()
        if existing is not None:
            return existing
        profile = save_billing_profile(participant=participant, actor=actor, request=request, **buyer)
        settings_row = settings_for(competition)
        order = Order(
            competition=competition,
            edition=fee.schedule.edition,
            participant_fee=fee,
            currency=fee.currency,
            total=fee.amount,
            language=language,
            created_by=_actor(actor),
            due_on=fee.due_on
            or pricing.competition_today(competition) + timedelta(days=settings_row.proforma_due_days),
            reference=_unique_reference(competition),
        )
        _snapshot_buyer(order, profile)
        order.full_clean(exclude=["created_by"])
        order.save()
        with translation.override(language):
            label = _("Opłata za udział")
            OrderLine.objects.create(
                order=order,
                kind=PriceKind.PARTICIPANT,
                description=f"{label}: {fee.schedule.name} ({fee.schedule.edition.year_label})",
                quantity=1,
                unit_price=fee.amount,
                amount=fee.amount,
            )
        proforma = documents.issue_document(order, DocumentKind.PROFORMA)
    audit(
        actor,
        "payments.order_created",
        order,
        {
            "fee": fee.pk,
            "reference": order.reference,
            "total": str(order.total),
            "currency": order.currency,
            "proforma": proforma.number,
        },
        request=request,
    )
    return order


#: Ile sekund po założeniu próby Stripe bez identyfikatora sesji uznajemy ją za „w toku”: tyle trwa
#: najdłużej rozmowa z dostawcą (``HTTP_TIMEOUT``) plus zapas. Starsza bez identyfikatora – porzucona.
STRIPE_UNREFERENCED_GRACE_SECONDS = HTTP_TIMEOUT + 10


def _in_progress(message=None) -> DomainError:
    return _error(
        message or _("Płatność tego zamówienia jest właśnie przetwarzana. Spróbuj ponownie za kilka minut."),
        "PAYMENT_IN_PROGRESS",
    )


def _pending_verdict(payment: Payment, now) -> str:
    """Co zrobić z próbą ``PENDING`` przed nową próbą albo anulowaniem: ``closed`` albo ``in_progress``.

    Woła dostawcę (HTTP), więc **nigdy** pod blokadą wiersza (L1). Stripe: ``expire``; gdy odmówi –
    ``GET`` sesji: wygasła = zamknięta, otwarta albo zakończona = płatność w drodze (M2). Próba bez
    identyfikatora sesji młodsza niż czas rozmowy z dostawcą jest „w toku” – druga karta przeglądarki
    właśnie ją zakłada (M1). P24 nie ma wygaszania: blokuje do upływu limitu transakcji.
    """
    if payment.provider == Provider.STRIPE:
        if not payment.provider_ref:
            young = payment.created_at > now - timedelta(seconds=STRIPE_UNREFERENCED_GRACE_SECONDS)
            return "in_progress" if young else "closed"
        provider = get_provider(Provider.STRIPE)
        if provider.expire(payment):
            return "closed"
        state = provider.session_status(payment)
        if state is not None and state["status"] == "expired":
            return "closed"
        return "in_progress"
    if payment.created_at > now - timedelta(minutes=P24_ABANDON_MINUTES):
        return "p24_wait"
    return "closed"


def _close_pending_unlocked(order: Order) -> None:
    """Faza 1 (bez blokad): zamyka u dostawców otwarte próby zamówienia albo odmawia."""
    now = timezone.now()
    pending = Payment.objects.filter(order=order, status=PaymentStatus.PENDING).exclude(
        provider=Provider.BANK_TRANSFER
    )
    for payment in pending:
        verdict = _pending_verdict(payment, now)
        if verdict == "in_progress":
            raise _in_progress()
        if verdict == "p24_wait":
            raise _in_progress(
                _("Płatność Przelewy24 tego zamówienia jest w toku. Spróbuj ponownie za %(minutes)s minut.")
                % {"minutes": P24_ABANDON_MINUTES}
            )
        _close(payment, PaymentStatus.CANCELLED)


def _require_no_pending_locked(order: Order) -> None:
    """Faza 2 (pod blokadą zamówienia): żadna próba nie może już czekać – inaczej doszła równolegle."""
    if (
        Payment.objects.select_for_update(of=("self",))
        .filter(order=order, status=PaymentStatus.PENDING)
        .exclude(provider=Provider.BANK_TRANSFER)
        .exists()
    ):
        raise _in_progress()


def cancel_order(order: Order, *, actor, reason: str = "", request=None) -> Order:
    """Anuluje otwarte zamówienie (opiekun albo koordynator). Pro forma zostaje w rejestrze z numerem."""
    competition = order.competition
    require_enabled(competition)
    if not can_pay(actor, order):
        _require_coordinator(actor, competition)
    if order.status != OrderStatus.OPEN:
        raise _error(_("Anulować można wyłącznie zamówienie czekające na wpłatę."), "ORDER_NOT_OPEN")
    _close_pending_unlocked(order)
    with transaction.atomic():
        order = Order.objects.select_for_update(of=("self",)).get(pk=order.pk)
        if order.status != OrderStatus.OPEN:
            raise _error(_("Anulować można wyłącznie zamówienie czekające na wpłatę."), "ORDER_NOT_OPEN")
        _require_no_pending_locked(order)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancelled_by = _actor(actor)
        order.cancel_reason = (reason or "").strip()[:300]
        order.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason"])
    audit(actor, "payments.order_cancelled", order, {"reason": order.cancel_reason}, request=request)
    return order


# --- zapłata --------------------------------------------------------------------------------------


def methods_for(competition, currency: str) -> list[str]:
    """Metody płatności konkursu w tej walucie: skonfigurowane w środowisku i włączone w ustawieniach."""
    row = settings_for(competition)
    methods = []
    stripe = get_provider(Provider.STRIPE)
    p24 = get_provider(Provider.P24)
    if row.card_payments and stripe.is_configured() and stripe.supports_currency(currency):
        methods.append(Provider.STRIPE)
    if row.p24_payments and p24.is_configured() and p24.supports_currency(currency):
        methods.append(Provider.P24)
    if row.bank_transfer and row.bank_account:
        methods.append(Provider.BANK_TRANSFER)
    return methods


def available_methods(order: Order) -> list[str]:
    """Metody, którymi da się zapłacić **to** zamówienie."""
    return methods_for(order.competition, order.currency)


def delegation_card(delegation) -> dict:
    """Kontekst sekcji „Opłaty” w panelu drużyny – pusty, gdy konkurs nie pobiera opłat (zero zapytań)."""
    if not enabled(delegation.competition):
        return {}
    return {"payments_statement": pricing.statement(delegation)}


def _require_payable(order: Order) -> None:
    if order.status != OrderStatus.OPEN:
        raise _error(_("To zamówienie nie czeka na wpłatę."), "ORDER_NOT_OPEN")
    if (
        order.delegation_id
        and pricing.active_adjustments(order.delegation).filter(kind=AdjustmentKind.WAIVER).exists()
    ):
        raise _error(_("Delegacja jest zwolniona z opłat."), "WAIVED")
    if order.participant_fee_id:
        order.participant_fee.refresh_from_db()
        if order.participant_fee.is_settled:
            raise _error(_("Ta opłata jest już rozliczona."), "FEE_SETTLED")


def _description(order: Order) -> str:
    who = (
        order.delegation.country.name
        if order.delegation_id
        else order.participant_fee.participant.public_code
    )
    return f"{order.competition.name} {order.edition.year_label} – {who} – {order.reference}"


def start_checkout(order: Order, provider_code: str, *, actor, request=None) -> str:
    """Zakłada próbę zapłaty u dostawcy i oddaje adres, na który przekierować płacącego.

    Trzy fazy, żeby żadna rozmowa z dostawcą nie trwała pod blokadą wiersza (L1): (1) bez blokad
    zamykamy poprzednie próby u dostawcy, (2) pod blokadą zamówienia sprawdzamy, że nic nie czeka,
    i zakładamy wiersz próby, (3) bez blokad zakładamy sesję u dostawcy. Identyfikator sesji zapisuje
    się **warunkowo** (``status=PENDING``): jeżeli równoległe żądanie zdążyło tę próbę zamknąć, nowa
    sesja jest od razu wygaszana, a płacący dostaje odmowę – inaczej istniałyby dwie otwarte sesje
    na jedno zamówienie (M1).
    """
    from apps.accounts.activation import absolute_url

    competition = order.competition
    require_enabled(competition)
    if not can_pay(actor, order):
        raise Http404("Nie ma takiego zamówienia.")
    if provider_code == Provider.BANK_TRANSFER or provider_code not in available_methods(order):
        raise _error(
            _("Ta metoda płatności nie jest dostępna."), "METHOD_UNAVAILABLE", http.HTTP_400_BAD_REQUEST
        )
    provider = get_provider(provider_code)
    _require_payable(order)
    _close_pending_unlocked(order)
    with transaction.atomic():
        order = (
            Order.objects.select_for_update(of=("self",))
            .select_related("delegation__country", "participant_fee__participant", "edition", "competition")
            .get(pk=order.pk)
        )
        _require_payable(order)
        _require_no_pending_locked(order)
        payment = Payment(
            order=order,
            provider=provider_code,
            amount=order.total,
            currency=order.currency,
            created_by=_actor(actor),
        )
        if provider_code == Provider.P24:
            # Identyfikatorem sesji P24 jest nasz UUID – znany od razu, więc nie ma okna bez niego.
            payment.provider_ref = str(payment.uuid)
        payment.save()
    checkout = CheckoutRequest(
        payment_uuid=str(payment.uuid),
        amount=payment.amount,
        currency=payment.currency,
        description=_description(order),
        order_reference=order.reference,
        competition_slug=competition.slug,
        customer_email=order.buyer_email,
        language=order.language,
        return_url=absolute_url(reverse("web:payment-order", args=[order.pk]), request, competition),
        notify_url=absolute_url(reverse("web:payments-webhook-p24"), request, competition),
    )
    try:
        result = provider.create_checkout(checkout)
    except ProviderError as exc:
        Payment.objects.filter(pk=payment.pk, status=PaymentStatus.PENDING).update(
            status=PaymentStatus.FAILED, closed_at=timezone.now(), detail=str(exc)[:300]
        )
        audit(
            actor,
            "payments.checkout_failed",
            payment,
            {"provider": provider_code, "error": str(exc)[:120]},
            request=request,
        )
        raise _error(
            _("Dostawca płatności jest chwilowo niedostępny. Spróbuj ponownie za chwilę."),
            "PROVIDER_UNAVAILABLE",
            http.HTTP_502_BAD_GATEWAY,
        ) from exc
    from urllib.parse import urlsplit

    if (
        urlsplit(result.redirect_url).scheme != "https"
        or urlsplit(result.redirect_url).hostname not in provider.redirect_hosts
    ):
        raise _error("Nieoczekiwany adres dostawcy.", "PROVIDER_REDIRECT", http.HTTP_502_BAD_GATEWAY)
    stored = Payment.objects.filter(pk=payment.pk, status=PaymentStatus.PENDING).update(
        provider_ref=result.provider_ref
    )
    if not stored:
        # Równoległe żądanie zamknęło tę próbę, zanim dostawca oddał sesję – wygaszamy ją od razu.
        payment.provider_ref = result.provider_ref
        provider.expire(payment)
        audit(actor, "payments.checkout_superseded", payment, {"provider": provider_code}, request=request)
        raise _in_progress()
    audit(
        actor,
        "payments.checkout_started",
        payment,
        {
            "provider": provider_code,
            "order": order.reference,
            "amount": str(payment.amount),
            "currency": payment.currency,
        },
        request=request,
    )
    return result.redirect_url


def _fee_paid(order: Order, payment: Payment, actor) -> None:
    """Należność uczestnika w rejestrze wpisowego – ta sama wpłata, widziana z ``apps.tenancy.fees``."""
    from apps.tenancy.fees import record_payment

    try:
        record_payment(
            order.participant_fee,
            external_reference=order.reference,
            paid_at=payment.succeeded_at,
            amount=order.total,
            actor=actor,
        )
    except DomainError as exc:
        # Rejestr ma już inną wpłatę albo zwolnienie – wpłata online jest wtedy nadpłatą do zwrotu.
        logger.warning(
            "Wpłata %s nie zapisała się w rejestrze wpisowego: %s", order.reference, exc.machine_code
        )
        audit(None, "payments.fee_register_conflict", order, {"code": exc.machine_code})


def _lock_order_and_payment(payment_pk: int) -> tuple[Order, Payment]:
    """Blokady zawsze w tej samej kolejności: **zamówienie → wpłata** (L1) – bez zakleszczeń."""
    order_id = Payment.objects.filter(pk=payment_pk).values_list("order_id", flat=True).get()
    order = (
        Order.objects.select_for_update(of=("self",))
        .select_related("competition", "edition")
        .get(pk=order_id)
    )
    payment = Payment.objects.select_for_update(of=("self",)).get(pk=payment_pk)
    return order, payment


def apply_success(
    payment: Payment, *, provider_payment_id: str = "", paid_at=None, actor=None, request=None
) -> Payment:
    """Zapisuje udaną wpłatę: zamówienie zapłacone, faktura z numerem, list do płacącego. Idempotentne.

    Wpłata na zamówienie **niezapłacone i nieanulowane** zamyka je. Na zapłacone (druga karta
    przeglądarki) albo anulowane (pozycje mogło już objąć nowsze zamówienie – L5) zostaje zapisana
    jako ``MISMATCH`` do zwrotu: pieniądze są faktem, ale nie wolno nimi pokryć składu drugi raz.
    """
    from . import notifications

    with transaction.atomic():
        order, payment = _lock_order_and_payment(payment.pk)
        if payment.status == PaymentStatus.SUCCEEDED:
            return payment
        now = timezone.now()
        payment.provider_payment_id = provider_payment_id or payment.provider_payment_id
        payment.succeeded_at = paid_at or now
        payment.closed_at = now
        if order.status != OrderStatus.OPEN:
            payment.status = PaymentStatus.MISMATCH
            payment.detail = (
                "Zamówienie było anulowane – wpłata do zwrotu."
                if order.status == OrderStatus.CANCELLED
                else "Zamówienie było już zapłacone – podwójna wpłata do zwrotu."
            )
            payment.save()
            audit(
                actor,
                "payments.duplicate_payment",
                payment,
                {"order": order.reference, "order_status": order.status},
                request=request,
            )
            return payment
        payment.status = PaymentStatus.SUCCEEDED
        payment.save()
        order.status = OrderStatus.PAID
        order.paid_at = payment.succeeded_at
        order.save(update_fields=["status", "paid_at"])
        if order.participant_fee_id:
            _fee_paid(order, payment, actor)
        invoice = documents.issue_document(order, DocumentKind.INVOICE)
        audit(
            actor,
            "payments.order_paid",
            order,
            {
                "payment": str(payment.uuid),
                "provider": payment.provider,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "invoice": invoice.number,
            },
            request=request,
        )
        notifications.send_receipt(order, invoice)
    return payment


def _mark_mismatch(payment: Payment, detail: str, *, provider_payment_id: str = "") -> None:
    with transaction.atomic():
        _order, payment = _lock_order_and_payment(payment.pk)
        if payment.status == PaymentStatus.SUCCEEDED:
            return
        payment.status = PaymentStatus.MISMATCH
        payment.detail = detail[:300]
        payment.provider_payment_id = provider_payment_id or payment.provider_payment_id
        payment.succeeded_at = timezone.now()
        payment.save()
    audit(None, "payments.amount_mismatch", payment, {"detail": detail[:200]})


def _close(payment: Payment, new_status: str) -> str:
    updated = Payment.objects.filter(pk=payment.pk, status=PaymentStatus.PENDING).update(
        status=new_status, closed_at=timezone.now()
    )
    return new_status.lower() if updated else "noop"


# --- webhooki -------------------------------------------------------------------------------------


class WebhookRejected(Exception):
    """Podpis nie zgadza się – widok odpowiada 400 i niczego nie zapisuje."""


def _find_payment(provider_code: str, result) -> Payment | None:
    queryset = Payment.objects.select_related("order", "order__competition").filter(provider=provider_code)
    if result.provider_ref:
        payment = queryset.filter(provider_ref=result.provider_ref).first()
        if payment is not None:
            return payment
    if result.payment_uuid:
        try:
            return queryset.filter(uuid=uuid_module.UUID(result.payment_uuid)).first()
        except ValueError:
            return None
    return None


def _amount_matches(payment: Payment, result) -> bool:
    return (
        result.amount_minor is not None
        and int(result.amount_minor) == to_minor(payment.amount)
        and result.currency == payment.currency
    )


def handle_webhook(provider_code: str, body: bytes, headers, *, refund: bool = False) -> str:
    """Przetwarza doręczenie od dostawcy. Zwraca opis wyniku (do dziennika i odpowiedzi).

    ``Http404`` – dostawca nieskonfigurowany (bez sekretu podpisu nie ma czego sprawdzać, więc adresu
    nie ma); :class:`WebhookRejected` – zły podpis; ``ProviderError`` – nie udało się potwierdzić
    transakcji u dostawcy (widok odpowiada 503, dostawca ponowi).

    Potwierdzenie u dostawcy (P24 ``verify``) idzie **przed** transakcją zapisu – rozmowa z dostawcą
    nie trzyma żadnej blokady (L1). Powtórka po nieudanym ``verify`` zapyta go ponownie, co jest
    u P24 bezpieczne (``verify`` jest idempotentny).
    """
    from apps.tenancy.context import competition_context

    provider = get_provider(provider_code)
    if provider is None or not provider.is_configured():
        raise Http404("Dostawca nieskonfigurowany.")
    try:
        result = provider.parse_refund_webhook(body) if refund else provider.parse_webhook(body, headers)
    except SignatureError as exc:
        logger.warning("Odrzucono webhook %s: zły podpis.", provider_code)
        raise WebhookRejected(provider_code) from exc
    event_id = result.event_id[:255]
    if ProviderEvent.objects.filter(provider=provider_code, event_id=event_id).exists():
        return "duplicate"
    payment = None
    if not result.mode_mismatch and result.outcome != "refund":
        payment = _find_payment(provider_code, result)
        if payment is not None and result.outcome == "succeeded" and _amount_matches(payment, result):
            if not provider.confirm(payment, result):
                raise ProviderError("verification-failed", transient=True)
    with transaction.atomic():
        try:
            with transaction.atomic():
                event = ProviderEvent.objects.create(
                    provider=provider_code,
                    event_id=event_id,
                    event_type=result.event_type[:80],
                    payload_hash=hashlib.sha256(body).hexdigest(),
                    payment=payment,
                )
        except IntegrityError:
            logger.info("Powtórzone doręczenie %s %s.", provider_code, result.event_id)
            return "duplicate"
        if result.mode_mismatch:
            logger.warning("Webhook %s z innego trybu (test/live) niż klucz – pominięty.", provider_code)
            outcome = "mode_mismatch"
        elif result.outcome == "refund":
            outcome = _webhook_refund(provider_code, result)
        elif payment is None:
            outcome = "ignored" if result.outcome == "ignored" else "unknown_payment"
        else:
            with competition_context(payment.order.competition):
                outcome = _webhook_payment(payment, result)
        event.outcome = outcome
        event.save(update_fields=["outcome"])
    return outcome


def _webhook_payment(payment: Payment, result) -> str:
    """Skutek zdarzenia płatności. Potwierdzenie u dostawcy wykonał już wołający (poza blokadami)."""
    if result.outcome == "succeeded":
        if not _amount_matches(payment, result):
            _mark_mismatch(
                payment,
                f"Dostawca potwierdził {result.amount_minor} {result.currency}, "
                f"oczekiwano {to_minor(payment.amount)} {payment.currency}.",
                provider_payment_id=result.provider_payment_id,
            )
            return "mismatch"
        apply_success(payment, provider_payment_id=result.provider_payment_id)
        return "succeeded"
    if result.outcome == "failed":
        return _close(payment, PaymentStatus.FAILED)
    if result.outcome == "expired":
        return _close(payment, PaymentStatus.CANCELLED)
    return "ignored"


def _find_refund(provider_code: str, result) -> Refund | None:
    queryset = Refund.objects.select_related("payment__order__competition").filter(
        payment__provider=provider_code
    )
    if result.refund_id:
        refund = queryset.filter(provider_refund_id=result.refund_id).first()
        if refund is not None:
            return refund
    if result.refund_uuid:
        # Odwrót (M5): odpowiedź API zwrotu zgubiła się, więc identyfikatora dostawcy nie znamy –
        # ale zdarzenie niesie nasz UUID z metadanych.
        try:
            refund = queryset.filter(uuid=uuid_module.UUID(result.refund_uuid)).first()
        except ValueError:
            return None
        if refund is not None and not refund.provider_refund_id and result.refund_id:
            Refund.objects.filter(pk=refund.pk).update(provider_refund_id=result.refund_id)
            refund.provider_refund_id = result.refund_id
        return refund
    return None


def _webhook_refund(provider_code: str, result) -> str:
    refund = _find_refund(provider_code, result)
    if refund is None:
        return "unknown_refund"
    from apps.tenancy.context import competition_context

    with competition_context(refund.payment.order.competition):
        apply_refund_status(refund, result.refund_status)
    return f"refund_{result.refund_status}"


# --- przelew tradycyjny ---------------------------------------------------------------------------


def record_bank_transfer(
    order: Order, *, received_on, note: str = "", proof=None, actor, request=None
) -> Payment:
    """Koordynator zapisuje wpływ przelewu z kodem zamówienia (opcjonalnie z dowodem wpłaty).

    Stan zamówienia sprawdzany **pod blokadą** (M4): dwa kliknięcia „Zapisz wpłatę” szeregują się na
    wierszu zamówienia, a drugie widzi już „zapłacone” i odmawia – zamiast zapisać podwójną wpłatę.
    Zamówienie anulowane jest odmową (L5): jego pozycje mogło objąć nowsze zamówienie.
    """
    competition = order.competition
    require_enabled(competition)
    _require_coordinator(actor, competition)
    paid_at = timezone.make_aware(datetime.combine(received_on, time(12, 0)))
    with transaction.atomic():
        order = Order.objects.select_for_update(of=("self",)).get(pk=order.pk)
        if order.status != OrderStatus.OPEN:
            raise _error(
                "Wpłatę zapisuje się wyłącznie na zamówienie czekające na wpłatę "
                "(anulowane albo już zapłacone – zwróć przelew albo wystaw nowe zamówienie).",
                "ORDER_NOT_OPEN",
            )
        payment = Payment(
            order=order,
            provider=Provider.BANK_TRANSFER,
            amount=order.total,
            currency=order.currency,
            created_by=_actor(actor),
            received_on=received_on,
            note=(note or "").strip()[:300],
        )
        if proof is not None:
            _store_proof(payment, proof, competition)
        payment.save()
        if payment.proof_key:
            _enqueue_proof_scan(payment.pk)
        audit(
            actor,
            "payments.bank_transfer_recorded",
            payment,
            {
                "order": order.reference,
                "received_on": received_on.isoformat(),
                "proof": bool(payment.proof_key),
            },
            request=request,
        )
        apply_success(payment, paid_at=paid_at, actor=actor, request=request)
    return payment


def _store_proof(payment: Payment, upload, competition) -> None:
    from apps.student_status.validators import validate_scan
    from apps.submissions.storage import get_submission_storage, sanitize_segment

    ext, mime = validate_scan(upload)
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in iter(lambda: upload.read(64 * 1024), b""):
        digest.update(chunk)
    upload.seek(0)
    payment.proof_sha256 = digest.hexdigest()
    payment.proof_mime = mime
    payment.proof_size = int(getattr(upload, "size", 0) or 0)
    payment.proof_scan_status = ScanStatus.PENDING
    payment.proof_key = (
        "/".join(
            [
                PROOF_PREFIX,
                sanitize_segment(competition.pk),
                sanitize_segment(payment.order_id),
                sanitize_segment(payment.uuid),
            ]
        )
        + f"/{payment.proof_sha256}.{ext}"
    )
    get_submission_storage().put(payment.proof_key, upload, mime)


def _enqueue_proof_scan(pk: int) -> None:
    def _enqueue() -> None:
        from .tasks import scan_payment_proof

        scan_payment_proof.delay(pk)

    transaction.on_commit(_enqueue)


def apply_proof_verdict(pk: int, verdict: str) -> None:
    """Wynik skanu dowodu. Plik zainfekowany jest usuwany ze storage – wpłata zostaje (to fakt z banku)."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).filter(pk=pk).first()
        if payment is None or payment.proof_scan_status != ScanStatus.PENDING:
            return
        payment.proof_scan_status = (
            ScanStatus.INFECTED if verdict == ScanStatus.INFECTED else ScanStatus.CLEAN
        )
        if verdict == ScanStatus.INFECTED:
            _drop_proof(payment)
            audit(None, "payments.proof_infected", payment, {})
        payment.save(update_fields=["proof_scan_status", "proof_key"])


def apply_proof_error(pk: int) -> None:
    with transaction.atomic():
        payment = Payment.objects.select_for_update(of=("self",)).filter(pk=pk).first()
        if payment is None or payment.proof_scan_status != ScanStatus.PENDING:
            return
        payment.proof_scan_status = ScanStatus.ERROR
        _drop_proof(payment)
        payment.save(update_fields=["proof_scan_status", "proof_key"])


def _drop_proof(payment: Payment) -> None:
    key = payment.proof_key
    payment.proof_key = ""
    if not key:
        return

    def _delete() -> None:
        from apps.submissions.storage import get_submission_storage

        try:
            get_submission_storage().delete(key)
        except Exception:  # noqa: BLE001 - osierocony obiekt jest lepszy niż wywrócona transakcja
            logger.exception("Nie udało się usunąć dowodu wpłaty %s.", key)

    transaction.on_commit(_delete)


def open_proof(payment: Payment):
    """Strumień **czystego** dowodu wpłaty albo ``None``."""
    if payment.proof_scan_status != ScanStatus.CLEAN or not payment.proof_key:
        return None
    from apps.submissions.storage import get_submission_storage
    from apps.submissions.tasks import MissingStorageObject, _open_object

    try:
        return _open_object(get_submission_storage(), payment.proof_key)
    except MissingStorageObject:
        return None


# --- zwroty ---------------------------------------------------------------------------------------


REFUNDABLE_STATUSES = (PaymentStatus.SUCCEEDED, PaymentStatus.MISMATCH)

#: Po ilu minutach zwrot w toku bez identyfikatora dostawcy (odpowiedź API zginęła) jest ponawiany.
REFUND_RETRY_MINUTES = 2


def refundable_lines(order: Order) -> list[dict]:
    """Pozycje zamówienia z ilością, którą jeszcze da się zwrócić (bez zniżki – jej się nie zwraca)."""
    from django.db.models import Sum

    taken = dict(
        RefundLine.objects.filter(
            line__order=order, refund__status__in=(RefundStatus.PENDING, RefundStatus.SUCCEEDED)
        )
        .values_list("line_id")
        .annotate(total=Sum("quantity"))
    )
    rows = []
    for line in order.lines.exclude(kind=PriceKind.DISCOUNT):
        left = line.quantity - (taken.get(line.pk) or 0)
        rows.append({"line": line, "left": max(0, left)})
    return rows


def refund_payment(
    payment: Payment, *, lines: dict | None = None, reason: str, actor, request=None
) -> Refund:
    """Zwrot zlecony przez koordynatora: **pozycjami** (M3), u dostawcy przez API albo zapis przelewu.

    Wpłata, która zapłaciła zamówienie (``SUCCEEDED``), wraca wyłącznie za wskazane pozycje
    i ilości – kwota jest ich sumą (przycięta do tego, co z wpłaty zostało, gdy zamówienie miało
    zniżkę). Dzięki temu zwrócony uczeń przestaje „pokrywać” miejsce i jego zastępca płaci. Wpłata
    ``MISMATCH`` (podwójna, rozbieżna, po anulowaniu) wraca w całości i bez pozycji.
    """
    competition = payment.order.competition
    require_enabled(competition)
    _require_coordinator(actor, competition)
    reason = (reason or "").strip()
    if not reason:
        raise _error("Zwrot wymaga podania powodu.", "REASON_REQUIRED", http.HTTP_400_BAD_REQUEST)
    requested = {int(key): int(value) for key, value in (lines or {}).items() if int(value or 0)}
    with transaction.atomic():
        order, payment = _lock_order_and_payment(payment.pk)
        if payment.status not in REFUNDABLE_STATUSES:
            raise _error("Zwrócić można wyłącznie przyjętą wpłatę.", "PAYMENT_NOT_REFUNDABLE")
        pending = sum((r.amount for r in payment.refunds.filter(status=RefundStatus.PENDING)), ZERO)
        left = payment.amount - payment.refunded_amount - pending
        if left <= 0:
            raise _error("Z tej wpłaty nie ma już czego zwrócić.", "REFUND_AMOUNT", http.HTTP_400_BAD_REQUEST)
        drafts: list[tuple[OrderLine, int]] = []
        if payment.status == PaymentStatus.SUCCEEDED:
            available = {row["line"].pk: row for row in refundable_lines(order)}
            if not requested:
                raise _error(
                    "Wskaż pozycje i ilości do zwrotu.", "REFUND_LINES_REQUIRED", http.HTTP_400_BAD_REQUEST
                )
            for line_pk, quantity in requested.items():
                row = available.get(line_pk)
                if row is None or quantity < 0 or quantity > row["left"]:
                    raise _error(
                        "Pozycja albo ilość zwrotu spoza zamówienia.",
                        "REFUND_QUANTITY",
                        http.HTTP_400_BAD_REQUEST,
                    )
                drafts.append((row["line"], quantity))
            amount = min(left, sum((line.unit_price * quantity for line, quantity in drafts), ZERO))
        else:
            if requested:
                raise _error(
                    "Wpłata do wyjaśnienia nie pokrywa pozycji – wraca w całości.",
                    "REFUND_LINES_NOT_ALLOWED",
                    http.HTTP_400_BAD_REQUEST,
                )
            amount = left
        refund = Refund.objects.create(
            payment=payment, amount=amount, reason=reason, created_by=_actor(actor)
        )
        RefundLine.objects.bulk_create(
            RefundLine(refund=refund, line=line, quantity=quantity, amount=line.unit_price * quantity)
            for line, quantity in drafts
        )
    audit(
        actor,
        "payments.refund_requested",
        refund,
        {
            "payment": str(payment.uuid),
            "provider": payment.provider,
            "amount": str(amount),
            "reason": reason,
            "lines": {str(line.pk): quantity for line, quantity in drafts},
        },
        request=request,
    )
    if payment.provider == Provider.BANK_TRANSFER:
        Refund.objects.filter(pk=refund.pk).update(detail="Zwrot przelewem wykonany poza systemem.")
        apply_refund_status(refund, "succeeded", actor=actor)
        refund.refresh_from_db()
        return refund
    _submit_refund(refund, actor=actor, request=request)
    refund.refresh_from_db()
    if refund.status == RefundStatus.FAILED:
        raise _error(
            "Dostawca odrzucił zwrot. Szczegóły w historii zamówienia.",
            "REFUND_FAILED",
            http.HTTP_502_BAD_GATEWAY,
        )
    return refund


def _submit_refund(refund: Refund, *, actor=None, request=None) -> None:
    """Zleca zwrot u dostawcy – także ponownie (M5), zawsze z tym samym kluczem idempotencji.

    Brak odpowiedzi, przekroczony czas albo 5xx znaczą „wynik nieznany”: zwrot zostaje ``PENDING``
    i sprzątanie (``sweep_payments``) zleca go jeszcze raz z tym samym UUID – Stripe po kluczu
    idempotencji, P24 po ``requestId`` oddadzą ten sam zwrot zamiast drugiego. Odmowa (4xx) zamyka
    zwrot jako ``FAILED``.
    """
    from apps.accounts.activation import absolute_url

    payment = refund.payment
    competition = payment.order.competition
    provider = get_provider(payment.provider)
    try:
        result = provider.refund(
            payment,
            refund.amount,
            refund_uuid=refund.uuid.hex,
            reason=refund.reason,
            notify_url=absolute_url(reverse("web:payments-webhook-p24-refund"), request, competition),
        )
    except ProviderError as exc:
        if exc.transient:
            Refund.objects.filter(pk=refund.pk, status=RefundStatus.PENDING).update(
                detail="Operator nie odpowiedział – zwrot zostanie zlecony ponownie."
            )
            audit(
                actor, "payments.refund_retry_scheduled", refund, {"error": str(exc)[:120]}, request=request
            )
            return
        Refund.objects.filter(pk=refund.pk, status=RefundStatus.PENDING).update(
            status=RefundStatus.FAILED, completed_at=timezone.now(), detail=str(exc)[:300]
        )
        audit(actor, "payments.refund_failed", refund, {"error": str(exc)[:120]}, request=request)
        return
    Refund.objects.filter(pk=refund.pk).update(provider_refund_id=result.provider_refund_id, detail="")
    refund.provider_refund_id = result.provider_refund_id
    apply_refund_status(refund, result.status, actor=actor)


def apply_refund_status(refund: Refund, status: str, *, actor=None) -> Refund:
    """Wynik zwrotu (od razu z API albo później z webhooka). Idempotentne – zwrot rozstrzyga się raz.

    Blokady w kolejności zamówienie → wpłata → zwrot (L1).
    """
    from . import notifications

    if status == "pending":
        return refund
    with transaction.atomic():
        order, payment = _lock_order_and_payment(refund.payment_id)
        refund = Refund.objects.select_for_update(of=("self",)).get(pk=refund.pk)
        if refund.status != RefundStatus.PENDING:
            return refund
        refund.completed_at = timezone.now()
        if status != "succeeded":
            refund.status = RefundStatus.FAILED
            refund.save(update_fields=["status", "completed_at"])
            audit(actor, "payments.refund_failed", refund, {"status": status})
            return refund
        refund.status = RefundStatus.SUCCEEDED
        refund.save(update_fields=["status", "completed_at"])
        payment.refunded_amount = min(payment.amount, payment.refunded_amount + refund.amount)
        payment.save(update_fields=["refunded_amount"])
        if payment.status == PaymentStatus.SUCCEEDED:
            # Zwrot podwójnej albo rozbieżnej wpłaty (``MISMATCH``) nie rusza zamówienia – ono jest
            # opłacone inną wpłatą albo dalej czeka na właściwą.
            order.refunded_amount = min(order.total, order.refunded_amount + refund.amount)
            if order.refunded_amount >= order.total and order.status == OrderStatus.PAID:
                order.status = OrderStatus.REFUNDED
            order.save(update_fields=["refunded_amount", "status"])
            if order.status == OrderStatus.REFUNDED and order.participant_fee_id:
                from apps.tenancy.fees import record_refund

                try:
                    record_refund(order.participant_fee, reason=refund.reason, actor=actor)
                except DomainError as exc:
                    logger.warning(
                        "Zwrot %s nie zapisał się w rejestrze wpisowego: %s",
                        order.reference,
                        exc.machine_code,
                    )
        audit(actor, "payments.refunded", refund, {"amount": str(refund.amount), "order": order.reference})
        notifications.send_refund_notice(refund)
    return refund


# --- sprzątanie zaległych prób i zwrotów (beat) ----------------------------------------------------


def _sweep_stripe_payment(payment: Payment) -> str:
    provider = get_provider(Provider.STRIPE)
    if not payment.provider_ref:
        return _close(payment, PaymentStatus.CANCELLED)
    state = provider.session_status(payment)
    if state is None:
        return "unknown"
    if state["status"] == "expired":
        return _close(payment, PaymentStatus.CANCELLED)
    if state["status"] == "complete" and state["payment_status"] == "paid":
        # Webhook zginął (albo endpoint był źle skonfigurowany), a sesja jest zapłacona: stan pobrany
        # **naszym kluczem** z API dostawcy jest tak samo wiarygodny jak podpisane zdarzenie.
        from .providers.base import WebhookResult

        result = WebhookResult(
            event_id=f"sweep:{payment.provider_ref}",
            event_type="sweep",
            outcome="succeeded",
            provider_ref=payment.provider_ref,
            provider_payment_id=state["payment_intent"],
            amount_minor=state["amount_minor"],
            currency=state["currency"],
        )
        return _webhook_payment(payment, result)
    return "open"


def sweep_payments(now=None) -> dict[str, int]:
    """Zamyka porzucone próby zapłaty i ponawia zwroty o nieznanym wyniku (M2, M5). Woła beat.

    - Stripe ``PENDING`` starsze niż czas życia sesji (albo bez identyfikatora sesji starsze niż czas
      rozmowy z dostawcą): ``GET`` sesji – wygasła → przerwana, zapłacona → wpłata (jak z webhooka),
    - P24 ``PENDING`` starsze niż limit transakcji z zapasem → przerwana (zapłacona P24 doręczyłaby
      powiadomienie, a bez ``verify`` sam zwraca pieniądze),
    - zwroty ``PENDING`` bez identyfikatora dostawcy starsze niż kilka minut → zlecone ponownie.
    """
    from apps.tenancy.context import competition_context

    from .providers.stripe import SESSION_TTL_SECONDS

    now = now or timezone.now()
    stats = {"payments": 0, "refunds": 0}
    stripe_configured = get_provider(Provider.STRIPE).is_configured()
    stale = Payment.objects.select_related("order__competition").filter(status=PaymentStatus.PENDING)
    for payment in stale.filter(provider=Provider.STRIPE):
        limit = (
            timedelta(seconds=SESSION_TTL_SECONDS + 600)
            if payment.provider_ref
            else timedelta(seconds=STRIPE_UNREFERENCED_GRACE_SECONDS)
        )
        if payment.created_at > now - limit or not stripe_configured:
            continue
        with competition_context(payment.order.competition):
            _sweep_stripe_payment(payment)
        stats["payments"] += 1
    for payment in stale.filter(
        provider=Provider.P24, created_at__lt=now - timedelta(minutes=P24_ABANDON_MINUTES + 60)
    ):
        _close(payment, PaymentStatus.CANCELLED)
        stats["payments"] += 1
    for refund in (
        Refund.objects.select_related("payment__order__competition")
        .filter(
            status=RefundStatus.PENDING,
            provider_refund_id="",
            created_at__lt=now - timedelta(minutes=REFUND_RETRY_MINUTES),
        )
        .exclude(payment__provider=Provider.BANK_TRANSFER)
    ):
        if not get_provider(refund.payment.provider).is_configured():
            continue
        with competition_context(refund.payment.order.competition):
            _submit_refund(refund)
        stats["refunds"] += 1
    return stats


# --- zestawienia ----------------------------------------------------------------------------------


def orders_of_edition(competition, edition):
    return (
        Order.objects.for_competition(competition)
        .filter(edition=edition)
        .select_related("delegation__country", "participant_fee__participant")
        .prefetch_related("documents", "payments")
        .order_by("-created_at", "-id")
    )


def delegation_rows(competition, edition) -> list[dict]:
    """Wiersz pulpitu na delegację: skład, zapłacone, otwarte, niewystawione, stan."""
    if not competition.uses_delegations:
        return []
    from apps.accounts.delegations import Delegation

    rows = []
    for delegation in (
        Delegation.objects.for_competition(competition)
        .filter(edition=edition)
        .select_related("country", "edition", "competition")
    ):
        rows.append({"delegation": delegation, "statement": pricing.statement(delegation)})
    return rows


def totals_by_currency(competition, edition, rows=None) -> dict[str, dict[str, Decimal]]:
    """Sumy per waluta: wystawione, zapłacone, zwrócone, otwarte i jeszcze niewystawione (delegacje)."""
    totals: dict[str, dict[str, Decimal]] = {}

    def bucket(currency):
        return totals.setdefault(
            currency, {"issued": ZERO, "paid": ZERO, "refunded": ZERO, "open": ZERO, "not_issued": ZERO}
        )

    for order in (
        Order.objects.for_competition(competition)
        .filter(edition=edition)
        .exclude(status=OrderStatus.CANCELLED)
    ):
        entry = bucket(order.currency)
        entry["issued"] += order.total
        entry["refunded"] += order.refunded_amount
        if order.status in (OrderStatus.PAID, OrderStatus.REFUNDED):
            entry["paid"] += order.total
        else:
            entry["open"] += order.total
    for row in rows or []:
        statement = row["statement"]
        if statement.new_total and statement.currency:
            bucket(statement.currency)["not_issued"] += statement.new_total
    return totals


def attention_payments(competition):
    """Wpłaty do wyjaśnienia (rozbieżna kwota, podwójna wpłata) i zwroty nieudane albo w toku."""
    payments = list(
        Payment.objects.for_competition(competition)
        .filter(status=PaymentStatus.MISMATCH, refunded_amount__lt=F("amount"))
        .select_related("order")
    )
    refunds = list(
        Refund.objects.for_competition(competition)
        .filter(status__in=(RefundStatus.PENDING, RefundStatus.FAILED))
        .select_related("payment__order")
    )
    return payments, refunds


def accounting_dataset(competition, edition):
    """Eksport CSV dla księgowości: jeden wiersz na zamówienie, z numerami dokumentów i wpłatą."""
    from apps.core.exports import Dataset

    orders = list(orders_of_edition(competition, edition))
    header = [
        "kod",
        "wystawiono",
        "płacący",
        "nabywca",
        "kraj nabywcy",
        "NIP / VAT ID",
        "e-mail",
        "waluta",
        "suma",
        "stan",
        "zapłacono",
        "metoda",
        "identyfikator transakcji",
        "zwrócono",
        "pro forma",
        "faktura",
    ]
    labels = dict(OrderStatus.choices)

    def rows():
        for order in orders:
            docs = {doc.kind: doc.number for doc in order.documents.all()}
            paid = next((p for p in order.payments.all() if p.status == PaymentStatus.SUCCEEDED), None)
            payer = (
                order.delegation.country.name
                if order.delegation_id
                else order.participant_fee.participant.public_code
            )
            yield [
                order.reference,
                timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M"),
                payer,
                order.buyer_name,
                order.buyer_country,
                order.buyer_vat_id,
                order.buyer_email,
                order.currency,
                f"{order.total:.2f}",
                str(labels[order.status]),
                timezone.localtime(order.paid_at).strftime("%Y-%m-%d") if order.paid_at else "",
                paid.provider if paid else "",
                paid.provider_payment_id if paid else "",
                f"{order.refunded_amount:.2f}",
                docs.get(DocumentKind.PROFORMA, ""),
                docs.get(DocumentKind.INVOICE, ""),
            ]

    return Dataset(
        header=header,
        rows=rows(),
        count=len(orders),
        title="Płatności",
        filename=f"platnosci-{competition.slug}",
    )


def document_for(competition, pk: int, *, user, coordinator: bool) -> BillingDocument:
    """Dokument tego konkursu – dla koordynatora albo płacącego zamówienia. Inaczej 404."""
    require_enabled(competition)
    document = (
        BillingDocument.objects.for_competition(competition)
        .select_related("order__delegation", "order__participant_fee__participant")
        .filter(pk=pk)
        .first()
    )
    if document is None or not (coordinator or can_pay(user, document.order)):
        raise Http404("Nie ma takiego dokumentu.")
    return document


# --- RODO -----------------------------------------------------------------------------------------


def export_section(user) -> list[dict]:
    """Zamówienia wystawione przez to konto i jego dane nabywcy – do eksportu danych konta."""
    rows = [
        {
            "rodzaj": "zamówienie",
            "kod": order.reference,
            "konkurs": order.competition.slug,
            "kwota": f"{order.total:.2f} {order.currency}",
            "stan": order.status,
            "wystawiono": order.created_at.isoformat(),
            "nabywca": order.buyer_name,
            "adres": order.buyer_address,
            "kraj": order.buyer_country,
            "vat_id": order.buyer_vat_id,
            "email": order.buyer_email,
        }
        for order in Order.objects.filter(created_by=user).select_related("competition")
    ]
    profiles = BillingProfile.objects.filter(participant__user=user) | BillingProfile.objects.filter(
        delegation__isnull=False, updated_by=user
    )
    for profile in profiles.distinct():
        rows.append(
            {
                "rodzaj": "dane nabywcy"
                if profile.participant_id
                else "dane nabywcy delegacji (wpisane przez to konto)",
                "nabywca": profile.buyer_name,
                "adres": profile.buyer_address,
                "kraj": profile.buyer_country,
                "vat_id": profile.buyer_vat_id,
                "email": profile.buyer_email,
            }
        )
    return rows


def erase_for_user(user) -> None:
    """Anonimizacja i usunięcie konta (L6): profil nabywcy **uczestnika** znika, dokumenty zostają.

    Zamówienia i faktury niosą migawkę nabywcy i są dokumentacją księgową (art. 6 ust. 1 lit. c RODO,
    5 lat) – tego nie kasujemy. Profil to tylko wzór do kolejnych zamówień, więc po koncie nie zostaje.
    Profil **delegacji** należy do delegacji (dane instytucji), a nie do osoby – odpinamy tylko autora.
    """
    BillingProfile.objects.filter(participant__user=user).delete()
    BillingProfile.objects.filter(updated_by=user).update(updated_by=None)
