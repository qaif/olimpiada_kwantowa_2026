"""Ekrany VISA-01: wnioski opiekuna o listy zapraszające, decyzje oficera i publiczna weryfikacja.

Osobny moduł obok ``views`` (LOG-01), bo to przyrost na istniejącej aplikacji: wspólne są bramki
i mixiny (``LeaderMixin``, ``OfficerMixin`` – rola, przydział, zakres delegacji, ``no-store``),
a nie kod widoków. Reguły są w ``letter_requests`` i ``verification``; widok orkiestruje.

Trzy wejścia:

- **opiekun drużyny** (``/delegation/logistics/letters/``) – wnioski dla osób swojej delegacji,
  wycofanie oczekującego, pobranie ważnego listu (pobranie – widok LOG-01). Tłumaczone (gettext),
- **oficer logistyki** (``/coordinator/logistics/letter-requests/``) – lista z filtrami, decyzje
  pojedyncze i hurtowe (jeden formularz z zaznaczeniem), CSV, unieważnienie listu. Po polsku,
- **każdy** (``/visa/verify/…``) – strona weryfikacji; bez logowania, z limitem żądań na adres IP.

Każda decyzja ma własny adres POST (repozytoryjna reguła ekranów koordynatora); przyciski jednego
formularza wybierają adres atrybutem ``formaction``, a nie nazwą przycisku.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.web.throttle import ThrottledFormMixin

from . import letter_requests as service
from . import letters, services, verification
from .letter_texts import DEFAULT_LANGUAGE, letter_languages
from .models import LetterRequestStatus
from .views import THROTTLE_SCOPE, LeaderMixin, OfficerMixin, _no_store

#: Limit żądań strony weryfikacji (``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]``) – per adres IP.
VERIFY_THROTTLE_SCOPE = "visa_verify"


# --- opiekun drużyny ---------------------------------------------------------------------------------


class LeaderLettersView(LeaderMixin, ThrottledFormMixin, View):
    """``/delegation/logistics/letters/`` – osoby delegacji, stan wniosków, „Poproś o list”."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request):
        return self._render(request)

    def post(self, request):
        language = request.POST.get("language") or DEFAULT_LANGUAGE
        ids = [value for value in request.POST.getlist("member") if value.isdigit()]
        try:
            result = service.request_letters(self.leader, ids, language=language, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, status=400)
        created, skipped = len(result["created"]), len(result["skipped"])
        if created:
            messages.success(
                request, _("Złożone wnioski o listy zapraszające: %(count)d.") % {"count": created}
            )
        if skipped:
            messages.info(
                request,
                _("Pominięto osoby, które mają już wniosek oczekujący na decyzję: %(count)d.")
                % {"count": skipped},
            )
        return redirect("web:delegation-logistics-letters")

    def _render(self, request, *, status: int = 200):
        delegation = self.delegation
        members = services.members_of(delegation)
        event = services.event_for(delegation.edition)
        context = {
            "delegation": delegation,
            "rows": service.leader_rows(delegation, members),
            "languages": letter_languages(self.competition),
            "default_language": DEFAULT_LANGUAGE,
            "letters": letters.letters_of(self.competition, delegation.edition, delegation),
            "purged": event is not None and event.purged_at is not None,
            "pending": LetterRequestStatus.PENDING,
            "rejected": LetterRequestStatus.REJECTED,
        }
        return TemplateResponse(request, "delegation_logistics/leader_letters.html", context, status=status)


class LeaderRequestWithdrawView(LeaderMixin, ThrottledFormMixin, View):
    """``POST /delegation/logistics/letter-requests/<pk>/withdraw/`` – wycofanie oczekującego wniosku."""

    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        row = service.request_for_leader(self.leader, pk)
        try:
            service.withdraw(self.leader, row, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Wniosek został wycofany."))
        return redirect("web:delegation-logistics-letters")


# --- oficer logistyki -----------------------------------------------------------------------------------


def _filters(request) -> dict:
    return {
        "country": (request.GET.get("country") or "").strip().lower()[:10],
        "state": (request.GET.get("state") or "").strip().upper()[:16],
    }


class LetterRequestsView(OfficerMixin, View):
    """``/coordinator/logistics/letter-requests/`` – wnioski edycji z filtrami kraju i stanu."""

    def get(self, request):
        from apps.accounts.delegations import Delegation

        filters = _filters(request)
        edition = self.edition
        rows = list(service.requests_of(self.competition, edition, **filters))
        countries = (
            Delegation.objects.for_competition(self.competition)
            .filter(edition=edition)
            .select_related("country")
            .order_by("country__name")
        )
        return self.render(
            request,
            "letter_requests.html",
            {
                "rows": rows,
                "filters": filters,
                "countries": [(d.country.code, d.country.name) for d in countries],
                "states": LetterRequestStatus.choices,
                "pending_count": service.pending_count(self.competition, edition),
                # Listy, które zatwierdzenie unieważni (M1) – oficer widzi to przed kliknięciem.
                "would_revoke": service.would_revoke(rows),
                "query": request.GET.urlencode(),
            },
        )


class LetterRequestsExportView(OfficerMixin, View):
    """``/coordinator/logistics/letter-requests.csv`` – te same filtry, co lista; bez paszportów."""

    def get(self, request):
        from apps.core.models import audit

        rows = list(service.requests_of(self.competition, self.edition, **_filters(request)))
        audit(
            request.user,
            "logistics.letter_requests_exported",
            self.competition,
            {"rows": len(rows)},
            request=request,
        )
        return _no_store(csv_response(service.export_dataset(rows)))


class _DecisionView(OfficerMixin, ThrottledFormMixin, View):
    """Wspólny kształt decyzji: zaznaczone wnioski z formularza listy, powrót z tymi samymi filtrami."""

    throttle_scope = THROTTLE_SCOPE

    def back(self, request):
        query = request.POST.get("query", "")
        url = reverse("web:coordinator-onsite-letter-requests")
        return redirect(f"{url}?{query}" if query else url)

    def selected(self, request):
        return service.requests_by_ids(self.competition, request.POST.getlist("request"))

    def post(self, request):
        rows = self.selected(request)
        if not rows:
            messages.error(request, "Zaznacz co najmniej jeden wniosek.")
            return self.back(request)
        try:
            self.decide(request, rows)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        return self.back(request)


class LetterRequestsApproveView(_DecisionView):
    """``POST …/letter-requests/approve/`` – zatwierdzenie zaznaczonych (każdy wniosek = list imienny)."""

    def decide(self, request, rows):
        result = service.approve(self.competition, rows, actor=request.user, request=request)
        if result["approved"]:
            numbers = ", ".join(row.letter.number for row in result["approved"])
            messages.success(
                request, f"Zatwierdzono {len(result['approved'])} – wystawione listy: {numbers}."
            )
        for row, reason in result["failed"]:
            messages.error(request, f"{row.member.full_name}: {reason} Wniosek czeka dalej.")


class LetterRequestsRejectView(_DecisionView):
    """``POST …/letter-requests/reject/`` – odrzucenie zaznaczonych z jednym powodem."""

    def decide(self, request, rows):
        rejected = service.reject(
            self.competition, rows, reason=request.POST.get("reason", ""), actor=request.user, request=request
        )
        if rejected:
            messages.success(request, f"Odrzucono {len(rejected)} – opiekunowie dostali powód e-mailem.")
        else:
            messages.info(request, "Żaden z zaznaczonych wniosków nie czekał już na decyzję.")


class LetterRevokeView(OfficerMixin, ThrottledFormMixin, View):
    """``POST /coordinator/logistics/letters/<pk>/revoke/`` – unieważnienie listu z powodem."""

    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        letter = letters.letter_for(self.competition, pk)
        try:
            service.revoke(
                self.competition,
                letter,
                reason=request.POST.get("reason", ""),
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"List {letter.number} został unieważniony.")
        return redirect("web:coordinator-onsite-letters")


# --- weryfikacja publiczna ---------------------------------------------------------------------------------


class _VerifyMixin(ThrottledFormMixin):
    """Limit żądań per adres IP, bramka „konkurs wystawił listy”, ``no-store``, bez analityki.

    - **bramka** (M3): strona istnieje w konkursie, który wystawił choć jeden list – niezależnie od
      flagi logistyki i trybu rejestracji (list leży w konsulacie dłużej niż trwa logistyka finału).
      Sprawdzana **po** limicie żądań: odpowiedź 404 albo przekierowanie mówi o istnieniu kodu,
      więc ma kosztować miejsce w kubełku tak samo, jak odpowiedź z wynikiem,
    - **limit** (L1): GET i HEAD – HEAD jest tym samym zapytaniem bez treści; oficer logistyki tego
      konkursu, który klika kody w rejestrze listów, limitu nie zużywa (L2),
    - **bez analityki** (M5): ``no_analytics`` wyłącza tag Google w ``base.html`` – kod z listu
      w adresie strony nie ma prawa trafić do statystyk odwiedzin.
    """

    throttle_scope = VERIFY_THROTTLE_SCOPE
    throttle_methods = ("GET", "HEAD")

    def dispatch(self, request, *args, **kwargs):
        from . import access

        competition = getattr(request, "competition", None)
        if request.user.is_authenticated and access.is_officer(request.user, competition):
            self.throttle_scope = ""
        response = super().dispatch(request, *args, **kwargs)
        # Wynik weryfikacji nie ma prawa zostać w pamięci podręcznej przeglądarki ani pośrednika: list
        # unieważniony godzinę temu ma się pokazać jako unieważniony, a nazwisko nie ma czego szukać
        # w cache'u wspólnego komputera w konsulacie.
        response["X-Robots-Tag"] = "noindex, nofollow"
        return _no_store(response)

    def check(self, request, code: str):
        """Wynik dla kodu: przekierowanie (list przeniesionego konkursu), strona z wynikiem albo 404."""
        competition = getattr(request, "competition", None)
        cleaned = verification.normalise(code)
        result = verification.verify(competition, cleaned) if cleaned else None
        if result is None and cleaned:
            moved = verification.moved_letter(request, cleaned)
            if moved is not None and moved.competition_id != getattr(competition, "pk", None):
                return redirect(letters.current_verification_url(moved))
        if not verification.has_letters(competition):
            raise Http404("Ten konkurs nie wystawia listów zapraszających.")
        context = {"code": cleaned, "result": result, "form": result is None, "no_analytics": True}
        return TemplateResponse(request, "delegation_logistics/verify.html", context)


class VisaVerifyFormView(_VerifyMixin, View):
    """``/visa/verify/`` – pole na kod; ``?code=`` pokazuje wynik od razu (jedno miejsce w limicie, L2)."""

    def get(self, request):
        return self.check(request, request.GET.get("code", ""))


class VisaVerifyView(_VerifyMixin, View):
    """``/visa/verify/<kod>/`` – wynik. Nieznany kod to ta sama strona z komunikatem, nie 404.

    Rozróżnienie po kodzie odpowiedzi zamieniłoby ten adres w narzędzie do maszynowego sprawdzania
    kodów (ta sama decyzja, co ``/dyplomy/<kod>/``).
    """

    def get(self, request, code: str):
        return self.check(request, code)
