"""Sieć absolwentów – ekrany uczestnika i absolwenta oraz publiczna ściana (ALUM-01).

Widoki wyłącznie orkiestrują: każda reguła (rola, flaga, kwalifikowalność, limity mentoringu) stoi
w ``apps.alumni.services``/``apps.alumni.mentoring`` i tam rzuca ``DomainError``. Kolejność bramek
taka sama, jak w pozostałych funkcjach za flagą (``participant_student_status``): rola
(``ParticipantRequiredMixin`` – anonim 302, obcy 403), potem flaga (404).

Szablony dostają gotowe karty (``services.Card``) – podpis, osiągnięcia, etykiety – a nie konto ani
profil uczestnika, więc nie ma jak wypisać e-maila, kodu publicznego czy szkoły przez pomyłkę.
"""

from __future__ import annotations

from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from apps.alumni import invitations, mentoring, services
from apps.alumni.forms import JoinForm, ProfileForm, ReasonForm, RequestForm
from apps.alumni.models import Interest, MentorshipStatus, NoteStatus, enabled
from apps.core.api import DomainError
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin

PAGE_SIZE = 20
WALL_PAGE_SIZE = 30


class _AlumniMixin(ParticipantRequiredMixin):
    #: Widok dostępny także przy wyłączonej fladze – wyłącznie dla osoby, która ma jeszcze profil
    #: (M4: wycofanie zgody musi działać zawsze, bo dane po wyłączeniu sieci zostają w bazie).
    reachable_when_disabled = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user) and not enabled(self.competition):
            has_profile = services.profile_of(self.participant) is not None
            if not (self.reachable_when_disabled and has_profile):
                raise Http404("Sieć absolwentów jest w tym konkursie wyłączona.")
        return super().dispatch(request, *args, **kwargs)


def _profile_initial(profile) -> dict:
    return {name: getattr(profile, name) for name in services.EDITABLE_FIELDS}


class AlumniHomeView(_AlumniMixin, View):
    """``GET /me/alumni/`` – dołącz albo edytuj profil, moje relacje mentorskie.

    Przy wyłączonej fladze strona istnieje wyłącznie dla osoby z profilem i pokazuje jedno: wycofanie
    zgody (M4).
    """

    template_name = "alumni/home.html"
    reachable_when_disabled = True

    def get(self, request):
        return self.render(request)

    def render(self, request, *, profile_form=None, join_form=None, status: int = 200):
        participant = self.participant
        profile = services.profile_of(participant)
        if not enabled(self.competition):
            return TemplateResponse(request, "alumni/disabled.html", {"profile": profile}, status=status)
        row = services.settings_for(self.competition)
        status_info = services.eligibility(participant, row=row) if profile is None else None
        relations = []
        items = list(mentoring.my_mentorships(participant))
        channels = mentoring.channels(items, self.competition)
        for item in items:
            as_mentor = item.mentor_id == participant.pk
            other = item.mentee if as_mentor else item.mentor
            relations.append(
                {
                    "row": item,
                    "as_mentor": as_mentor,
                    "other": services_display(other),
                    "channel": channels.get(item.pk),
                    # Notatkę małoletniego mentor czyta dopiero po akceptacji organizatora (H1).
                    "note": item.note if (not as_mentor or mentoring.note_visible_to_mentor(item)) else "",
                    "note_pending": as_mentor and item.note_status == NoteStatus.PENDING,
                }
            )
        context = {
            "profile": profile,
            "needs_reconsent": services.needs_reconsent(profile),
            "card": services.cards([profile], finished_only=False)[0] if profile is not None else None,
            "eligibility": status_info,
            "achievements": status_info.achievements if status_info else [],
            "consent_text": services.CONSENT_TEXT,
            "join_form": join_form or JoinForm(),
            "profile_form": profile_form
            or (ProfileForm(initial=_profile_initial(profile)) if profile else None),
            "settings": row,
            "relations": relations,
            "statuses": MentorshipStatus,
            "mentoring_enabled": row.mentoring_enabled,
        }
        return TemplateResponse(request, self.template_name, context, status=status)


def services_display(participant) -> str:
    """Podpis drugiej strony relacji – zawsze „Imię N.” (pełne nazwisko widzi tylko koordynator)."""
    from apps.forum.models import display_author

    return display_author(participant.user)


class AlumniJoinView(_AlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request):
        form = JoinForm(request.POST)
        if not form.is_valid():
            view = AlumniHomeView()
            view.setup(request)
            return view.render(request, join_form=form, status=400)
        try:
            services.join(user=request.user, competition=self.competition, consent=True, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(
                request, _("Witaj w sieci absolwentów! Uzupełnij profil – każde pole jest opcjonalne.")
            )
        return redirect(reverse("web:alumni"))


class AlumniProfileView(_AlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request):
        form = ProfileForm(request.POST)
        if not form.is_valid():
            view = AlumniHomeView()
            view.setup(request)
            return view.render(request, profile_form=form, status=400)
        try:
            services.update_profile(user=request.user, competition=self.competition, data=form.cleaned_data)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Profil zapisany."))
        return redirect(reverse("web:alumni"))


class AlumniWithdrawView(_AlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"
    reachable_when_disabled = True

    def post(self, request):
        services.withdraw(user=request.user, competition=self.competition, request=request)
        messages.success(request, _("Zgoda wycofana – Twój profil absolwenta został usunięty."))
        return redirect(reverse("web:alumni"))


class AlumniRenewView(_AlumniMixin, ThrottledFormMixin, View):
    """``POST /me/alumni/renew/`` – potwierdzenie nowej wersji treści zgody (L7)."""

    throttle_scope = "alumni"

    def post(self, request):
        form = JoinForm(request.POST)
        try:
            services.renew_consent(
                user=request.user, competition=self.competition, consent=form.is_valid(), request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Dziękujemy – Twój profil znów jest widoczny."))
        return redirect(reverse("web:alumni"))


class AlumniDirectoryView(_AlumniMixin, View):
    """``GET /me/alumni/directory/`` – katalog dla zalogowanych uczestników tego konkursu."""

    def get(self, request):
        query = (request.GET.get("q") or "").strip()[:60]
        interest = request.GET.get("interest") or ""
        interest = interest if interest in Interest.values else ""
        mentors_only = request.GET.get("mentors") == "1"
        rows = services.directory(self.participant, query=query, interest=interest, mentors_only=mentors_only)
        page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
        row = services.settings_for(self.competition)
        context = {
            "cards": services.cards(page.object_list, viewer=self.participant),
            "page_obj": page,
            "query": query,
            "interest": interest,
            "mentors_only": mentors_only,
            "interests": Interest.choices,
            "mentoring_enabled": row.mentoring_enabled,
            "can_request": row.mentoring_enabled and mentoring.is_current_participant(self.participant),
        }
        return TemplateResponse(request, "alumni/directory.html", context)


class AlumniRequestView(_AlumniMixin, ThrottledFormMixin, View):
    """``GET|POST /me/alumni/mentor/<token>/`` – prośba o mentoring do jednej osoby z katalogu."""

    throttle_scope = "alumni"

    def card(self, token):
        try:
            mentoring.ensure_mentoring(self.competition)
            profile = mentoring.mentor_profile(self.competition, token)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        return services.cards(
            services.annotate_load(type(profile).objects.filter(pk=profile.pk)), viewer=self.participant
        )[0]

    def get(self, request, token):
        return TemplateResponse(
            request, "alumni/request.html", {"card": self.card(token), "form": RequestForm()}
        )

    def post(self, request, token):
        card = self.card(token)
        form = RequestForm(request.POST)
        if not form.is_valid():
            return TemplateResponse(request, "alumni/request.html", {"card": card, "form": form}, status=400)
        try:
            mentoring.request_mentor(
                user=request.user,
                competition=self.competition,
                token=token,
                topic=form.cleaned_data["topic"],
                note=form.cleaned_data["note"],
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return TemplateResponse(request, "alumni/request.html", {"card": card, "form": form}, status=400)
        messages.success(request, _("Prośba wysłana. Dostaniesz e-mail, gdy mentor odpowie."))
        return redirect(reverse("web:alumni"))


#: Akcje na relacji mentorskiej wykonywane jednym przyciskiem – nazwa w adresie → serwis i komunikat.
ACTIONS = {
    "accept": (mentoring.accept, gettext_lazy("Prośba przyjęta – rozmowa czeka w Wiadomościach.")),
    "decline": (mentoring.decline, gettext_lazy("Prośba odrzucona.")),
    "cancel": (mentoring.cancel, gettext_lazy("Prośba wycofana.")),
    "end": (mentoring.end, gettext_lazy("Relacja mentorska zakończona.")),
}


class AlumniMentoringActionView(_AlumniMixin, ThrottledFormMixin, View):
    throttle_scope = "alumni"

    def post(self, request, pk, action):
        if action not in ACTIONS:
            raise Http404("Nieznana akcja.")
        function, message = ACTIONS[action]
        try:
            row = function(user=request.user, competition=self.competition, pk=pk, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:alumni"))
        messages.success(request, str(message))
        if action == "accept" and row.conversation_id:
            return redirect(reverse("web:chat-thread", args=[row.conversation_id]))
        return redirect(reverse("web:alumni"))


class AlumniFlagView(_AlumniMixin, ThrottledFormMixin, View):
    """``GET|POST /me/alumni/mentoring/<pk>/flag/`` – zgłoszenie problemu z relacją do organizatora."""

    throttle_scope = "alumni"

    def get(self, request, pk):
        return TemplateResponse(request, "alumni/flag.html", {"form": ReasonForm(), "pk": pk})

    def post(self, request, pk):
        form = ReasonForm(request.POST)
        if not form.is_valid():
            return TemplateResponse(request, "alumni/flag.html", {"form": form, "pk": pk}, status=400)
        try:
            mentoring.flag(
                user=request.user,
                competition=self.competition,
                pk=pk,
                reason=form.cleaned_data["reason"],
                request=request,
            )
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            form.add_error(None, str(exc.detail))
            return TemplateResponse(request, "alumni/flag.html", {"form": form, "pk": pk}, status=400)
        messages.success(request, _("Zgłoszenie wysłane do organizatora."))
        return redirect(reverse("web:alumni"))


# --- publiczne ------------------------------------------------------------------------------------------


class AlumniWallView(View):
    """``GET /alumni/`` – publiczna ściana. 404, gdy flaga albo ściana jest wyłączona."""

    def get(self, request):
        competition = getattr(request, "competition", None)
        if not enabled(competition) or not services.settings_for(competition).public_wall:
            raise Http404("Ściana absolwentów nie jest publiczna.")
        page = Paginator(services.wall(competition), WALL_PAGE_SIZE).get_page(request.GET.get("page"))
        return TemplateResponse(
            request, "alumni/wall.html", {"cards": services.cards(page.object_list), "page_obj": page}
        )


@method_decorator(csrf_exempt, name="dispatch")
class AlumniUnsubscribeView(View):
    """``GET|POST /alumni/unsubscribe/<token>/`` – wypis z zaproszeń bez logowania (RFC 8058).

    ``GET`` pokazuje stronę z przyciskiem, a wypisuje wyłącznie ``POST`` – skaner odnośników
    w skrzynce firmowej otwiera każdy link w liście i nie może przy tym nikogo wypisać. Klient poczty
    wysyła ``POST`` z ``List-Unsubscribe=One-Click`` bez tokenu CSRF, więc widok go nie wymaga:
    jedynym poświadczeniem jest podpisany token, który i tak niczego poza wypisem nie umożliwia.
    """

    def dispatch(self, request, *args, **kwargs):
        competition = getattr(request, "competition", None)
        if not enabled(competition):
            raise Http404("Sieć absolwentów jest w tym konkursie wyłączona.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, token):
        valid = self._valid(token)
        return TemplateResponse(
            request, "alumni/unsubscribe.html", {"valid": valid, "done": False}, status=200 if valid else 400
        )

    def post(self, request, token):
        if not self._valid(token):
            return TemplateResponse(
                request, "alumni/unsubscribe.html", {"valid": False, "done": False}, status=400
            )
        invitations.apply_unsubscribe(request.competition, token)
        return TemplateResponse(request, "alumni/unsubscribe.html", {"valid": True, "done": True})

    @staticmethod
    def _valid(token) -> bool:
        try:
            invitations.read_token(token)
        except signing.BadSignature:
            return False
        return True
