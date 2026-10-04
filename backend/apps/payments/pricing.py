"""Naliczanie delegacji: skład, okres cenowy, pokrycie zamówieniami i zestawienie (PAY-01 § 2).

Jedno miejsce odpowiada na pytanie „ile ta delegacja jeszcze winna i za co” – pulpit koordynatora,
panel opiekuna i wystawienie zamówienia czytają ten sam :class:`Statement`, więc nie mogą się
rozjechać.

**Pokrycie zamiast salda.** Zamówienie jest niezmienne, więc zamiast przeliczać „ile kosztuje
drużyna” od nowa przy każdej zmianie, liczymy, które **ilości** są już objęte zamówieniami otwartymi
albo zapłaconymi. Nowe zamówienie obejmuje wyłącznie przyrost – wyceniony w okresie dnia
wystawienia. Uczeń dopisany po terminie „late” płaci cenę późną, a opłaceni wcześniej zostają
przy swojej cenie; zwrot całości (``REFUNDED``) przestaje pokrywać i pozycje wracają do zapłaty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Sum
from django.utils import timezone

from .models import (
    COVERING_STATUSES,
    DELEGATION_KINDS,
    ZERO,
    AdjustmentKind,
    FeeAdjustment,
    Order,
    OrderLine,
    OrderStatus,
    PriceItem,
    PriceKind,
    PriceList,
    PricePeriod,
)

#: Stany zestawienia delegacji – kolejność jest kolejnością pilności na pulpicie.
STATE_NO_PRICES = "no_prices"
STATE_WAIVED = "waived"
STATE_DUE = "due"
STATE_OPEN = "open"
STATE_SETTLED = "settled"


def competition_today(competition):
    """Dzisiejsza data w strefie konkursu – od niej zależy okres cenowy."""
    try:
        zone = ZoneInfo(competition.time_zone)
    except KeyError, ValueError, TypeError:
        zone = timezone.get_current_timezone()
    return timezone.localdate(timezone=zone)


def price_list_for(edition) -> PriceList | None:
    return PriceList.objects.filter(edition=edition, is_active=True).first()


def prices_of(price_list: PriceList) -> dict[tuple[str, str], Decimal]:
    return {(item.kind, item.period): item.amount for item in PriceItem.objects.filter(price_list=price_list)}


def unit_price(prices: dict, kind: str, period: str) -> Decimal | None:
    """Cena pozycji w okresie; brak ceny okresu = cena podstawowa; brak podstawowej = ``None``."""
    if (kind, period) in prices:
        return prices[(kind, period)]
    return prices.get((kind, PricePeriod.REGULAR))


def composition(delegation) -> dict[str, int]:
    """Skład delegacji do wyceny: 1 delegacja, uczniowie, opiekunowie i zadeklarowani obserwatorzy."""
    from apps.accounts.delegations import DelegationLeader
    from apps.accounts.models import Participant

    from .models import BillingProfile

    profile = BillingProfile.objects.filter(delegation=delegation).only("observers").first()
    return {
        PriceKind.DELEGATION: 1,
        PriceKind.STUDENT: Participant.objects.filter(delegation=delegation).count(),
        PriceKind.LEADER: DelegationLeader.objects.filter(delegation=delegation).count(),
        PriceKind.OBSERVER: profile.observers if profile else 0,
    }


def covered(delegation) -> dict[str, int]:
    rows = (
        OrderLine.objects.filter(order__delegation=delegation, order__status__in=COVERING_STATUSES)
        .exclude(kind=PriceKind.DISCOUNT)
        .values("kind")
        .annotate(total=Sum("quantity"))
    )
    return {row["kind"]: row["total"] or 0 for row in rows}


def discount_used(delegation) -> Decimal:
    used = OrderLine.objects.filter(
        order__delegation=delegation, order__status__in=COVERING_STATUSES, kind=PriceKind.DISCOUNT
    ).aggregate(total=Sum("amount"))["total"]
    return -(used or ZERO)


def active_adjustments(delegation):
    return FeeAdjustment.objects.filter(delegation=delegation, revoked_at__isnull=True)


@dataclass
class LineDraft:
    kind: str
    quantity: int
    unit_price: Decimal
    amount: Decimal
    period: str = ""

    @property
    def label(self) -> str:
        return str(dict(PriceKind.choices)[self.kind])

    @property
    def period_label(self) -> str:
        return str(dict(PricePeriod.choices)[self.period]) if self.period else ""


@dataclass
class Statement:
    """Zestawienie delegacji. ``new_lines``/``new_total`` – to, co wystawi kolejne zamówienie."""

    state: str
    currency: str = ""
    period: str = ""
    composition: dict = field(default_factory=dict)
    covered: dict = field(default_factory=dict)
    new_lines: list[LineDraft] = field(default_factory=list)
    discount_line: LineDraft | None = None
    new_total: Decimal = ZERO
    discount_total: Decimal = ZERO
    discount_remaining: Decimal = ZERO
    open_total: Decimal = ZERO
    paid_total: Decimal = ZERO
    refunded_total: Decimal = ZERO
    waived: bool = False
    price_list: PriceList | None = None
    open_orders: list = field(default_factory=list)

    @property
    def outstanding(self) -> Decimal:
        """Ile jeszcze wpłynie, jeśli delegacja zapłaci wszystko: otwarte zamówienia + niewystawione."""
        return self.open_total + self.new_total


def statement(delegation, *, today=None) -> Statement:
    """Zestawienie opłat delegacji na dziś (albo na ``today``)."""
    price_list = price_list_for(delegation.edition)
    orders = list(Order.objects.filter(delegation=delegation).order_by("created_at", "id"))
    open_orders = [order for order in orders if order.status == OrderStatus.OPEN]
    paid = [order for order in orders if order.status in (OrderStatus.PAID, OrderStatus.REFUNDED)]
    base = {
        "open_total": sum((o.total for o in open_orders), ZERO),
        "paid_total": sum((o.total for o in paid), ZERO),
        "refunded_total": sum((o.refunded_amount for o in orders), ZERO),
        "open_orders": open_orders,
        "price_list": price_list,
    }
    if price_list is None:
        currency = orders[0].currency if orders else ""
        return Statement(state=STATE_NO_PRICES, currency=currency, **base)
    adjustments = list(active_adjustments(delegation))
    waived = any(adj.kind == AdjustmentKind.WAIVER for adj in adjustments)
    discount_total = sum(
        (adj.amount or ZERO for adj in adjustments if adj.kind == AdjustmentKind.DISCOUNT), ZERO
    )
    today = today or competition_today(delegation.competition)
    period = price_list.period_on(today)
    prices = prices_of(price_list)
    have = composition(delegation)
    done = covered(delegation)
    lines: list[LineDraft] = []
    for kind in DELEGATION_KINDS:
        missing = max(0, have.get(kind, 0) - done.get(kind, 0))
        price = unit_price(prices, kind, period)
        if missing and price is not None and price > 0:
            lines.append(
                LineDraft(
                    kind=kind, quantity=missing, unit_price=price, amount=price * missing, period=period
                )
            )
    subtotal = sum((line.amount for line in lines), ZERO)
    remaining = max(ZERO, discount_total - discount_used(delegation))
    discount_line = None
    if subtotal > 0 and remaining > 0:
        applied = min(remaining, subtotal)
        discount_line = LineDraft(kind=PriceKind.DISCOUNT, quantity=1, unit_price=-applied, amount=-applied)
    new_total = ZERO if waived else subtotal + (discount_line.amount if discount_line else ZERO)
    if waived:
        state = STATE_WAIVED
    elif new_total > 0:
        state = STATE_DUE
    elif open_orders:
        state = STATE_OPEN
    else:
        state = STATE_SETTLED
    return Statement(
        state=state,
        currency=price_list.currency,
        period=period,
        composition=have,
        covered=done,
        new_lines=[] if waived else lines,
        discount_line=None if waived else discount_line,
        new_total=new_total,
        discount_total=discount_total,
        discount_remaining=remaining,
        waived=waived,
        **base,
    )
