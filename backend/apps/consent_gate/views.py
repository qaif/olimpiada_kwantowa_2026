"""Ekran „Uzupełnij zgody” uczestnika i eksport braków dla koordynatora (``docs/tasks/CONS-01.md`` § 3, § 5).

Reguły są w ``state`` (co brakuje) i ``services`` (zapis); widoki wołają je i wybierają odpowiedź.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.generic import View

from apps.accounts.consents import organizer_name
from apps.accounts.guardian import guardian_status, requires_guardian_consent
from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin, ParticipantRequiredMixin

from . import report, services
from .forms import VERSION_CHANGED, ConsentCompletionForm, version_field_name

TEMPLATE = "consent_gate/complete.html"


def next_url(request) -> str:
    """Adres powrotu po uzupełnieniu – wyłącznie wewnętrzny, inaczej panel uczestnika.

    ``next`` przychodzi z adresu (bramka dokleja go sama, ale można go podać ręcznie), więc bez
    sprawdzenia ekran zgód byłby otwartym przekierowaniem tuż po tym, jak człowiek zaufał formularzowi.
    """
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse("web:me")


@method_decorator(never_cache, name="dispatch")
class ConsentCompleteView(ParticipantRequiredMixin, View):
    """``/me/consents/complete/`` – brakujące zgody (GET) i ich zapis (POST).

    Bez limitu żądań, świadomie: zapis jest idempotentny (po pierwszym udanym nie ma już czego
    uzupełniać), nie wysyła listów i nie przyjmuje danych od nikogo poza właścicielem sesji.
    Prośba do opiekuna – jedyna czynność ekranu, która wysyła list – idzie istniejącym, limitowanym
    widokiem ``web:guardian-request``.
    """

    def get(self, request):
        participant = self.participant
        missing, records = services.participant_missing(participant)
        if not missing:
            return self._nothing_missing(request, participant)
        organizer = organizer_name(participant.competition)
        form = ConsentCompletionForm(consents=missing, organizer=organizer)
        return self._render(request, participant, form, missing, records)

    def post(self, request):
        participant = self.participant
        missing, records = services.participant_missing(participant)
        if not missing:
            return self._nothing_missing(request, participant)
        organizer = organizer_name(participant.competition)
        form = ConsentCompletionForm(request.POST, consents=missing, organizer=organizer)
        if not form.is_valid():
            if form.changed:
                # Zaznaczenie dotyczyło innej wersji – pole wraca puste, z bieżącą wersją w ukrytym polu.
                form = self._recheck(request, form, missing, organizer)
            return self._render(request, participant, form, missing, records, status=400)
        try:
            services.complete_consents(
                participant, form.given_kinds(), versions=form.versions(), request=request
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, participant, form, missing, records, status=400)
        messages.success(request, _("Dziękujemy – zgody zostały zapisane."))
        return redirect(next_url(request))

    def _nothing_missing(self, request, participant):
        """Komplet zgód w bazie – a bramka mogła tu odesłać ze stanem z cache'a sprzed zapisu.

        Bez unieważnienia (przegląd L4) nieświeży stan w cache'u odesłałby uczestnika z ``next``
        z powrotem tutaj, a stąd znów na ``next`` – pętla do końca TTL.
        """
        from . import state

        state.forget_state(participant.competition_id, participant.user_id)
        return redirect(next_url(request))

    def _recheck(self, request, form, missing, organizer):
        data = request.POST.copy()
        for consent in form.changed:
            data.pop(consent.field_name, None)
            data[version_field_name(consent)] = consent.version
        fresh = ConsentCompletionForm(data, consents=missing, organizer=organizer)
        fresh.is_valid()
        for consent in form.changed:
            fresh.errors[consent.field_name] = fresh.error_class([VERSION_CHANGED])
        return fresh

    def _render(self, request, participant, form, missing, records, *, status: int = 200):
        previous = {}
        for kind, version in records:
            previous.setdefault(kind, []).append(version)
        rows = [
            {
                "consent": consent,
                "field": form[consent.field_name],
                # Zgoda tego rodzaju złożona wcześniej pod inną wersją – „dokument się zmienił”.
                "previous": sorted(previous.get(consent.kind, [])),
            }
            for consent in missing
        ]
        minor = requires_guardian_consent(participant)
        context = {
            "form": form,
            "rows": rows,
            "participant": participant,
            "next": next_url(request),
            "any_changed": any(row["previous"] for row in rows),
            # Stan zgody opiekuna online – sam stan i „wyślij ponownie”, bez blokady (CONS-01 § 4).
            "guardian": guardian_status(participant) if minor else None,
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)


class ConsentGapExportView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/consents/missing.csv`` – uczestnicy z brakującymi zgodami w tym konkursie."""

    def get(self, request):
        dataset = report.dataset(request.competition)
        # Eksport listy ludzi jest zdarzeniem w audycie – kto i kiedy ją wyniósł.
        audit(
            request.user,
            "consent_gate.exported",
            request.competition,
            {"rows": dataset.count},
            request=request,
        )
        return csv_response(dataset)
