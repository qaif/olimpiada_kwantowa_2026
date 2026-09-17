"""Kolejka zgłoszeń w panelu koordynatora: lista z filtrami i wątek z odpowiedzią.

Osobny moduł od ``support.py`` z powodu, który widać w każdym queryseciie: tam wszystko zaczyna
się od „moje”, tutaj od „wszystkie”. Wspólny widok z warunkiem roli w środku metody znaczyłby,
że jedyną rzeczą stojącą między uczestnikiem a cudzą sprawą jest ``if`` – a nie zestaw danych,
do którego jego konto ma dostęp.

Kolejność kolejki jest regułą, nie ustawieniem sortowania: **otwarte na górze**, a w obrębie stanu
od najstarszego. Najstarsza otwarta sprawa jest tą, która czeka najdłużej, i to ona ma stać
pierwsza – sortowanie „od najnowszej” spychałoby ją na dół dokładnie wtedy, gdy ruch rośnie.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.views.generic import View

from apps.core.api import DomainError
from apps.support.forms import CoordinatorReplyForm
from apps.support.models import SupportCategory, SupportTicket, TicketStatus
from apps.support.services import reply, set_status
from apps.web.mixins import CoordinatorRequiredMixin

QUEUE_TEMPLATE = "web/coordinator/support.html"
DETAIL_TEMPLATE = "web/coordinator/support_detail.html"

#: Ile zgłoszeń pokazujemy naraz. Bez stronicowania: kolejka ma być pusta, a nie przewijana –
#: gdy urośnie ponad tę liczbę, problemem jest zaległość, a nie brak kolejnej strony.
QUEUE_LIMIT = 200

#: Kolejność stanów w kolejce. Otwarte pierwsze, bo to one czekają na organizatora; zamknięte
#: na końcu, bo są już tylko historią sprawy.
STATUS_ORDER = (TicketStatus.OPEN, TicketStatus.ANSWERED, TicketStatus.CLOSED)


def queue(competition, *, status: str = "", category: str = ""):
    """Kolejka zgłoszeń **tego konkursu**, w kolejności „otwarte, najstarsze pierwsze”.

    Zakres idzie z managera (``SupportTicket.objects.for_competition``), a nie z warunku w widoku:
    sprawa trafia do organizatora **tego** konkursu, bo to on jest w niej administratorem danych
    i to on ma na nią odpowiedzieć (§ 3.2). ``None`` nie widzi niczego – tak samo jak wszędzie.

    Zgłoszenia do operatora platformy (``competition IS NULL``) też tu nie wchodzą: adresatem jest
    operator, a nie koordynator, i czyta je ``/admin/``.

    Porządek liczymy w SQL-u (``Case``/``When``), a nie w Pythonie: filtr zawęża zbiór, ale limit
    obcina go **po** sortowaniu, więc sortowanie w widoku pokazywałoby pierwsze dwieście spraw
    w kolejności przypadkowej i dopiero je układało.
    """
    tickets = (
        SupportTicket.objects.for_competition(competition)
        .select_related("user")
        .annotate(
            status_rank=Case(
                *[When(status=value, then=Value(index)) for index, value in enumerate(STATUS_ORDER)],
                default=Value(len(STATUS_ORDER)),
                output_field=IntegerField(),
            )
        )
    )
    if status in TicketStatus.values:
        tickets = tickets.filter(status=status)
    if category in SupportCategory.values:
        tickets = tickets.filter(category=category)
    return tickets.order_by("status_rank", "created_at", "id")


class CoordinatorSupportView(CoordinatorRequiredMixin, View):
    """``/coordinator/support/`` – kolejka zgłoszeń z filtrem stanu i kategorii.

    Filtry są zwykłymi parametrami adresu (``?status=``, ``?category=``), a nie formularzem POST:
    adres z filtrem ma dać się zapisać w zakładkach i przesłać drugiej osobie – tak samo jak przy
    zgłoszeniach problemów z pracami.
    """

    def get(self, request):
        status = request.GET.get("status") or ""
        category = request.GET.get("category") or ""
        tickets = list(queue(request.competition, status=status, category=category)[:QUEUE_LIMIT])
        filters = {name: value for name, value in (("status", status), ("category", category)) if value}
        context = {
            "tickets": tickets,
            "status": status,
            "category": category,
            "status_choices": TicketStatus.choices,
            "category_choices": SupportCategory.choices,
            "open_count": SupportTicket.objects.for_competition(request.competition)
            .filter(status=TicketStatus.OPEN)
            .count(),
            "filter_query": urlencode(filters),
        }
        return TemplateResponse(request, QUEUE_TEMPLATE, context)


class CoordinatorSupportDetailView(CoordinatorRequiredMixin, View):
    """``/coordinator/support/<id>/`` – wątek sprawy, odpowiedź i zmiana stanu.

    Odpowiedź i zamknięcie są jednym zapisem formularza, bo to jedna decyzja („odpisuję i uważam
    sprawę za załatwioną”). Samo zamknięcie – bez ani jednego zdania – też jest możliwe osobnym
    przyciskiem: zgłoszenie bywa duplikatem albo pomyłką i wymuszanie na to odpowiedzi byłoby
    wymuszaniem pustego zdania.
    """

    def get(self, request, pk: int):
        return self._render(request, self._ticket(request, pk), CoordinatorReplyForm())

    def post(self, request, pk: int):
        ticket = self._ticket(request, pk)
        # Samo zamknięcie (przycisk „Zamknij bez odpowiedzi”) nie przechodzi przez formularz –
        # nie ma w nim treści do sprawdzenia, a wymaganie jej byłoby wymaganiem pustego zdania.
        if request.POST.get("action") == "close":
            return self._close(request, ticket)
        form = CoordinatorReplyForm(request.POST)
        if not form.is_valid():
            return self._render(request, ticket, form, status=400)
        try:
            reply(
                ticket,
                form.cleaned_data["body"],
                author=request.user,
                from_coordinator=True,
                request=request,
            )
            if form.cleaned_data.get("close"):
                set_status(ticket, TicketStatus.CLOSED, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(
                request, self._ticket(request, pk), CoordinatorReplyForm(), status=exc.status_code
            )
        messages.success(request, f"Odpowiedź na zgłoszenie #{ticket.pk} została wysłana.")
        return redirect(reverse("web:coordinator-support-detail", args=[ticket.pk]))

    def _close(self, request, ticket: SupportTicket):
        try:
            set_status(ticket, TicketStatus.CLOSED, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(
                request, self._ticket(request, ticket.pk), CoordinatorReplyForm(), status=exc.status_code
            )
        messages.success(request, f"Zgłoszenie #{ticket.pk} zostało zamknięte.")
        return redirect(reverse("web:coordinator-support"))

    def _ticket(self, request, pk: int) -> SupportTicket:
        """Sprawa **tego konkursu** albo 404 – jedno wejście dla wątku, odpowiedzi i zamknięcia.

        404, a nie 403: istnienie zgłoszenia u sąsiada nie jest informacją tego koordynatora
        (§ 3.6), a odpowiedź na cudzą sprawę wyszłaby listem z jego podpisem. Zawężenie idzie
        z querysetu, więc żadna z trzech dróg tego ekranu nie może go pominąć.
        """
        return get_object_or_404(
            SupportTicket.objects.for_competition(request.competition).select_related("user"), pk=pk
        )

    def _render(self, request, ticket: SupportTicket, form, *, status: int = 200):
        context = {
            "ticket": ticket,
            "thread": list(ticket.messages.select_related("author")),
            "form": form,
            # Kontekst techniczny wypisujemy jako pary klucz–wartość, w kolejności zapisu:
            # to zrzut diagnostyczny, a nie dane, po których ekran ma cokolwiek rozstrzygać.
            "context_rows": list((ticket.context or {}).items()),
        }
        return TemplateResponse(request, DETAIL_TEMPLATE, context, status=status)
