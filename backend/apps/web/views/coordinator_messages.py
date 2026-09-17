"""Wysyłka komunikatów organizatora – ekran ``/coordinator/messages/``.

Ekran jest dwustopniowy i to jest jego jedyna nietrywialna decyzja projektowa: wypełniony
formularz najpierw pokazuje **podgląd** (liczba odbiorców i treść tak, jak pójdzie w liście),
a dopiero drugie kliknięcie wysyła. Powód jest prosty: wysyłki nie da się cofnąć. Grupa
„uczestnicy bieżącej edycji” to kilka tysięcy osób, a różnica między nią a „zapisani do etapu”
bywa w interfejsie jednym kliknięciem myszy – liczba odbiorców pokazana przed wysyłką jest
jedynym momentem, w którym pomyłka jest jeszcze odwracalna.

Podgląd i wysyłka to **to samo żądanie POST**, rozróżniane polem ``action``. Nie ma tu stanu
w sesji ani tokenu podglądu: formularz jedzie drugi raz w komplecie, więc nie da się wysłać
czegoś innego, niż się obejrzało, a odświeżenie podglądu niczego nie psuje.

Reguły domenowe – kto należy do grupy, jak dzieli się wysyłkę na porcje, co trafia do audytu –
stoją w ``apps.accounts.messaging``. Widok wyłącznie orkiestruje, tak jak reszta panelu.
"""

from __future__ import annotations

from django.contrib import messages as django_messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.messaging import BROADCAST_HISTORY_LIMIT, resolve_recipients, send_broadcast
from apps.accounts.models import MessageBroadcast
from apps.competitions.models import Stage
from apps.competitions.services import current_edition
from apps.web.coordinator_forms import BroadcastForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/messages.html"

#: Wartość pola ``action``, która znaczy „wyślij naprawdę”. Każda inna (także brak) kończy się
#: podglądem – domyślnie zamknięte, bo pomyłka w tę stronę kosztuje jedno kliknięcie więcej,
#: a w drugą kilka tysięcy listów.
ACTION_SEND = "send"


class CoordinatorMessagesView(CoordinatorRequiredMixin, View):
    """``GET`` pokazuje formularz i historię, ``POST`` – podgląd albo wysyłkę."""

    def get(self, request):
        return self._render(request, BroadcastForm(stages=self._stages(request.competition)))

    def post(self, request):
        form = BroadcastForm(request.POST, stages=self._stages(request.competition))
        if not form.is_valid():
            return self._render(request, form)
        recipients = resolve_recipients(
            form.cleaned_data["group"],
            edition=current_edition(request.competition),
            stage=form.cleaned_data.get("stage"),
            district=form.cleaned_data.get("district"),
            addresses=form.cleaned_data.get("addresses", ""),
        )
        if request.POST.get("action") != ACTION_SEND:
            return self._render(request, form, preview=recipients)
        if not recipients:
            # Wysyłka do pustej grupy nie jest błędem użytkownika, tylko informacją: grupa może
            # być pusta, bo nikt się jeszcze nie zapisał. Zapis pustego komunikatu w rejestrze
            # zaśmiecałby historię wpisem, po którym nie poszedł ani jeden list.
            django_messages.error(request, "Ta grupa nie ma ani jednego odbiorcy – nic nie wysłano.")
            return self._render(request, form, preview=recipients)
        broadcast = send_broadcast(
            group=form.cleaned_data["group"],
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            recipients=recipients,
            actor=request.user,
            request=request,
        )
        django_messages.success(
            request,
            f"Komunikat przekazany do wysyłki: {broadcast.recipient_count} odbiorców.",
        )
        return redirect(reverse("web:coordinator-messages"))

    @staticmethod
    def _stages(competition):
        """Etapy bieżącej edycji – jedyne, do których wolno adresować komunikat.

        Bieżąca edycja, a nie wszystkie: komunikat o terminie dotyczy tegorocznych zawodów,
        a lista z etapami sprzed dwóch lat byłaby wyłącznie zaproszeniem do pomyłki.
        """
        edition = current_edition(competition)
        if edition is None:
            return Stage.objects.none()
        return Stage.objects.filter(edition=edition).order_by("opens_at", "id")

    def _render(self, request, form, preview: list[str] | None = None):
        """Strona z formularzem, ewentualnym podglądem i historią wysyłek.

        ``preview`` niesie **listę adresów**, ale do szablonu idzie z niej wyłącznie długość:
        ekran ma powiedzieć „ile”, a nie „komu”. Wyświetlenie kilku tysięcy adresów na stronie
        byłoby wyciągiem z bazy kontaktów pokazanym bez żadnej potrzeby – koordynator i tak nie
        weryfikuje ich po jednym, tylko sprawdza rząd wielkości.
        """
        context = {
            "form": form,
            "preview": None if preview is None else {"count": len(preview)},
            # Rejestr wysyłek **tego** konkursu: historia komunikatów sąsiada nie jest historią
            # tego organizatora, a ``recent_broadcasts`` oddaje listę, więc zakres dokładamy
            # do zapytania, zanim limit obetnie wiersze.
            "broadcasts": list(
                MessageBroadcast.objects.for_competition(request.competition)
                .select_related("created_by")
                .order_by("-created_at", "-id")[:BROADCAST_HISTORY_LIMIT]
            ),
            "edition": current_edition(request.competition),
        }
        return TemplateResponse(request, TEMPLATE, context)
