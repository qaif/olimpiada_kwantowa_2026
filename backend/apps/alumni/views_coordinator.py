"""Panel koordynatora: „Absolwenci” – ustawienia i lista, nadzór mentoringu, zaproszenia, statystyki.

Bramki: rola (``CoordinatorRequiredMixin`` – 403), potem flaga (404). Każdy obiekt szukany przez
``for_competition`` – identyfikator z sąsiedniej olimpiady daje 404 (serwis: ``not_found``).

Koordynator widzi tu **pełne** dane stron relacji mentorskiej (imię, nazwisko, kod) – to jego rola,
tak samo jak na karcie uczestnika. Nie widzi natomiast treści rozmów: ta dociera do niego wyłącznie
przez kolejkę moderacji Wiadomości (obietnica CZ-01 § 5) – stąd link do kolejki, a nie do wątku.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.competitions.models import Edition
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from . import invitations, mentoring, services, stats
from .forms import EndForm, InvitationForm, SettingsForm
from .models import Level, MentorshipStatus, enabled

PAGE_SIZE = 50


class _CoordinatorAlumniMixin(CoordinatorRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user) and not enabled(self.competition):
            raise Http404("Sieć absolwentów jest w tym konkursie wyłączona.")
        return super().dispatch(request, *args, **kwargs)


def _raise_404(exc: DomainError):
    if exc.status_code == 404:
        raise Http404(str(exc.detail)) from exc


class CoordinatorAlumniView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    """``GET|POST /coordinator/alumni/`` – ustawienia sieci i lista absolwentów."""

    throttle_scope = "alumni"
    template_name = "alumni/coordinator/index.html"

    def get(self, request):
        row = services.settings_for(self.competition)
        form = SettingsForm(
            initial={
                "eligibility": row.eligibility,
                "mentoring_enabled": row.mentoring_enabled,
                "public_wall": row.public_wall,
            }
        )
        return self.render(request, form)

    def post(self, request):
        form = SettingsForm(request.POST)
        if not form.is_valid():
            return self.render(request, form, status=400)
        services.save_settings(
            competition=self.competition, actor=request.user, request=request, **form.cleaned_data
        )
        messages.success(request, _("Ustawienia zapisane."))
        return redirect(reverse("web:coordinator-alumni"))

    def render(self, request, form, *, status: int = 200):
        from apps.chat.services import is_enabled as chat_enabled

        query = (request.GET.get("q") or "").strip()[:60]
        page = Paginator(services.coordinator_list(self.competition, query=query), PAGE_SIZE).get_page(
            request.GET.get("page")
        )
        rows = list(page.object_list)
        cards = services.cards(rows)
        context = {
            "form": form,
            "settings": services.settings_for(self.competition),
            "chat_enabled": chat_enabled(self.competition),
            "page_obj": page,
            "rows": [
                {
                    "card": card,
                    "full_name": card.profile.participant.user.get_full_name(),
                    "row": card.profile,
                }
                for card in cards
            ],
            "query": query,
            "eligible_not_joined": services.eligible_not_joined_count(self.competition),
            "review_queue": services.content_review_queue(self.competition),
            "levels": Level,
        }
        return TemplateResponse(request, self.template_name, context, status=status)


class CoordinatorAlumniHideView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request, pk):
        hidden = request.POST.get("hidden") == "1"
        try:
            services.set_hidden(
                competition=self.competition, actor=request.user, pk=pk, hidden=hidden, request=request
            )
        except DomainError as exc:
            _raise_404(exc)
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Profil ukryty.") if hidden else _("Profil znów jest widoczny."))
        return redirect(reverse("web:coordinator-alumni"))


class CoordinatorApproveContentView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    """``POST /coordinator/alumni/<pk>/approve-content/`` – opis mentora widoczny dla małoletnich (H1)."""

    throttle_scope = "alumni"

    def post(self, request, pk):
        try:
            services.approve_content(competition=self.competition, actor=request.user, pk=pk, request=request)
        except DomainError as exc:
            _raise_404(exc)
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Opis zaakceptowany – małoletni uczestnicy go zobaczą."))
        return redirect(reverse("web:coordinator-alumni"))


class CoordinatorNoteDecisionView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    """``POST /coordinator/alumni/notes/<pk>/<approve|reject>/`` – notatka prośby małoletniego (H1)."""

    throttle_scope = "alumni"

    def post(self, request, pk, decision):
        if decision not in ("approve", "reject"):
            raise Http404("Nieznana decyzja.")
        try:
            mentoring.decide_note(
                competition=self.competition,
                actor=request.user,
                pk=pk,
                approve=decision == "approve",
                request=request,
            )
        except DomainError as exc:
            _raise_404(exc)
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Decyzja zapisana."))
        return redirect(reverse("web:coordinator-alumni-mentoring"))


class CoordinatorMentoringView(_CoordinatorAlumniMixin, View):
    """``GET /coordinator/alumni/mentoring/`` – relacje, kanały, zgłoszenia."""

    def get(self, request):
        status = request.GET.get("status") or ""
        status = status if status in MentorshipStatus.values else ""
        page = Paginator(mentoring.oversight(self.competition, status=status), PAGE_SIZE).get_page(
            request.GET.get("page")
        )
        from .safety import mentee_is_minor

        items = list(page.object_list)
        # Kanały jednym odczytem ustawień czatu, a nie zapytaniem na wiersz (L4).
        channels = mentoring.channels(items, self.competition)
        rows = [
            {"row": row, "channel": channels.get(row.pk), "minor_mentee": mentee_is_minor(row)}
            for row in items
        ]
        context = {
            "rows": rows,
            "page_obj": page,
            "status": status,
            "statuses": MentorshipStatus.choices,
            "flags": list(mentoring.open_flags(self.competition)),
            "pending_notes": mentoring.pending_notes(self.competition),
            "end_form": EndForm(),
            "settings": services.settings_for(self.competition),
        }
        return TemplateResponse(request, "alumni/coordinator/mentoring.html", context)


class CoordinatorMentoringEndView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request, pk):
        form = EndForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Podaj powód zakończenia relacji."))
            return redirect(reverse("web:coordinator-alumni-mentoring"))
        try:
            mentoring.coordinator_end(
                competition=self.competition,
                actor=request.user,
                pk=pk,
                note=form.cleaned_data["note"],
                request=request,
            )
        except DomainError as exc:
            _raise_404(exc)
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Relacja zakończona. Obie strony dostały list."))
        return redirect(reverse("web:coordinator-alumni-mentoring"))


class CoordinatorFlagResolveView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request, pk):
        try:
            mentoring.resolve_flag(competition=self.competition, actor=request.user, pk=pk, request=request)
        except DomainError as exc:
            _raise_404(exc)
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Zgłoszenie zamknięte."))
        return redirect(reverse("web:coordinator-alumni-mentoring"))


class CoordinatorInvitationsView(_CoordinatorAlumniMixin, ThrottledFormMixin, View):
    """``GET|POST /coordinator/alumni/invitations/`` – podgląd liczby odbiorców, potem wysyłka.

    Dwa przyciski w jednym formularzu: „Policz odbiorców” (``action=preview``) niczego nie wysyła,
    „Wyślij” (``action=send``) wysyła. Liczba przed wysyłką jest po to, żeby zaproszenie do jury nie
    poszło przez pomyłkę do całej sieci.
    """

    throttle_scope = "alumni"
    template_name = "alumni/coordinator/invitations.html"

    def editions(self):
        return list(Edition.objects.for_competition(self.competition).order_by("-created_at", "-id"))

    def get(self, request):
        return self.render(request, InvitationForm(editions=self.editions()))

    def post(self, request):
        form = InvitationForm(request.POST, editions=self.editions())
        if not form.is_valid():
            return self.render(request, form, status=400)
        if request.POST.get("action") != "send":
            count = len(invitations.audience(self.competition, form.filters()))
            return self.render(request, form, preview=count)
        data = form.cleaned_data
        try:
            invitation = invitations.send_invitation(
                competition=self.competition,
                actor=request.user,
                kind=data["kind"],
                title=data["title"],
                body=data["body"],
                url=data["url"],
                filters=form.filters(),
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self.render(request, form, status=400)
        messages.success(
            request, _("Zaproszenie wysłane. Liczba odbiorców: %(count)s.") % {"count": invitation.recipients}
        )
        return redirect(reverse("web:coordinator-alumni-invitations"))

    def render(self, request, form, *, preview=None, status: int = 200):
        context = {
            "form": form,
            "preview": preview,
            "history": list(invitations.history(self.competition)[:20]),
        }
        return TemplateResponse(request, self.template_name, context, status=status)


class CoordinatorStatsView(_CoordinatorAlumniMixin, View):
    """``GET /coordinator/alumni/stats/`` – „gdzie są teraz” z progiem k-anonimowości."""

    def get(self, request):
        return TemplateResponse(
            request, "alumni/coordinator/stats.html", stats.where_are_they_now(self.competition)
        )
