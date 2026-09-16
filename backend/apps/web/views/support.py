"""Zgłoszenia od użytkownika: nowa sprawa, lista własnych spraw i wątek.

Podział na ten moduł i ``coordinator_support`` odpowiada podziałowi ról, a nie podziałowi kodu:
tutaj każdy queryset zaczyna się od „moje”, tam – od „wszystkie”. Gdyby oba ekrany dzieliły widok
z parametrem roli, jedyną rzeczą stojącą między uczestnikiem a cudzą sprawą byłby warunek w środku
metody. Tutaj cudzej sprawy po prostu nie ma w queryseciie i odpowiedzią jest 404.

``/support/new/`` działa **z kontem i bez**. Bez konta jest publicznym formularzem kontaktowym
z tym samym blokiem antyspamowym, co rejestracja – osoba, która nie może się zalogować, ma
najwięcej powodów, żeby napisać, a wymaganie konta zamykałoby drzwi dokładnie jej.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.core.api import DomainError
from apps.support.forms import AnonymousSupportTicketForm, SupportReplyForm, SupportTicketForm
from apps.support.models import SupportTicket
from apps.support.services import collect_context, open_ticket, reply
from apps.web.throttle import ThrottledFormMixin

NEW_TEMPLATE = "web/support/new.html"
LIST_TEMPLATE = "web/support/list.html"
DETAIL_TEMPLATE = "web/support/detail.html"

#: Ile zgłoszeń pokazujemy na liście własnych spraw. Bez stronicowania: uczestnik ma ich kilka,
#: a nie kilkaset – lista jest przypomnieniem „o co pytałem”, a nie archiwum do przeglądania.
OWN_TICKETS_LIMIT = 50


class SupportTicketCreateView(ThrottledFormMixin, View):
    """``/support/new/`` – zgłoszenie problemu. Dla zalogowanych i dla gości.

    Limit (scope ``support``) obowiązuje **oba** warianty: każde zgłoszenie wysyła list na adres
    organizatora, a konto zakłada się w minutę, więc samo logowanie nie jest tu ograniczeniem.

    ``next`` z parametru adresu **nie** jest używany do przekierowania – po zapisie wracamy na
    własny wątek (albo na stronę główną przy zgłoszeniu bez konta). Adres strony, z której przyszło
    zgłoszenie, jedzie ukrytym polem do kontekstu sprawy i zostaje tam napisem.
    """

    throttle_scope = "support"

    def get(self, request):
        return self._render(request, self._form(request))

    def post(self, request):
        form = self._form(request, data=request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            ticket = open_ticket(
                user=request.user,
                category=form.cleaned_data["category"],
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                email=form.cleaned_data.get("email", ""),
                context=collect_context(request, page_url=form.cleaned_data.get("page_url", "")),
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        messages.success(
            request,
            "Zgłoszenie zostało wysłane. Odpowiedź przyjdzie na Twój adres e-mail.",
        )
        if request.user.is_authenticated:
            return redirect(reverse("web:support-detail", args=[ticket.pk]))
        return redirect(reverse("web:support-sent"))

    def _form(self, request, data=None):
        """Formularz z blokiem antyspamowym albo bez – zależnie od tego, czy nadawca ma konto."""
        if request.user.is_authenticated:
            return SupportTicketForm(data)
        return AnonymousSupportTicketForm(data)

    def _render(self, request, form, *, status: int = 200):
        context = {
            "form": form,
            # Adres strony, z której użytkownik przyszedł, wpisany w ukryte pole. Bierzemy go
            # z nagłówka ``Referer`` tylko na potrzeby **kontekstu zgłoszenia** – nigdy do
            # przekierowania, więc nagłówek od klienta niczego tu nie otwiera.
            "page_url": (request.headers.get("Referer") or "")[:500],
        }
        return TemplateResponse(request, NEW_TEMPLATE, context, status=status)


class SupportTicketSentView(View):
    """Podziękowanie po zgłoszeniu bez konta – jedyna droga powrotu dla kogoś, kto nie ma panelu."""

    template_name = "web/support/sent.html"

    def get(self, request):
        return TemplateResponse(request, self.template_name, {})


def own_tickets(user):
    """Sprawy zgłoszone przez to konto, od najnowszej.

    Jedno miejsce, w którym powstaje „moje zgłoszenia” – używają go lista i wątek. Dzięki temu
    cudza sprawa jest 404 z tego samego queryseta w obu widokach, a nie z warunku powtórzonego
    w dwóch metodach.
    """
    return SupportTicket.objects.filter(user=user).order_by("-created_at", "-id")


class SupportTicketListView(LoginRequiredMixin, View):
    """``/support/`` – lista własnych zgłoszeń."""

    def get(self, request):
        context = {"tickets": list(own_tickets(request.user)[:OWN_TICKETS_LIMIT])}
        return TemplateResponse(request, LIST_TEMPLATE, context)


class SupportTicketDetailView(LoginRequiredMixin, View):
    """``/support/<id>/`` – wątek sprawy razem z formularzem dopisku.

    Cudza sprawa to 404, a nie 403: odpowiedź nie ma potwierdzać, że zgłoszenie o tym numerze
    w ogóle istnieje. Numery są kolejne, więc 403 byłoby licznikiem spraw w serwisie.
    """

    def get(self, request, pk: int):
        return self._render(request, self._ticket(request, pk), SupportReplyForm())

    def post(self, request, pk: int):
        ticket = self._ticket(request, pk)
        form = SupportReplyForm(request.POST)
        if not form.is_valid():
            return self._render(request, ticket, form, status=400)
        try:
            reply(
                ticket,
                form.cleaned_data["body"],
                author=request.user,
                from_coordinator=False,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(
                request, self._ticket(request, pk), SupportReplyForm(), status=exc.status_code
            )
        messages.success(request, "Dopisaliśmy Twoją wiadomość do zgłoszenia.")
        return redirect(reverse("web:support-detail", args=[ticket.pk]))

    def _ticket(self, request, pk: int) -> SupportTicket:
        ticket = own_tickets(request.user).filter(pk=pk).first()
        if ticket is None:
            raise Http404
        return ticket

    def _render(self, request, ticket: SupportTicket, form, *, status: int = 200):
        context = {
            "ticket": ticket,
            "thread": list(ticket.messages.select_related("author")),
            "form": form,
        }
        return TemplateResponse(request, DETAIL_TEMPLATE, context, status=status)
