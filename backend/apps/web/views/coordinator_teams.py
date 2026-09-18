"""Ekran „Drużyny” ``/coordinator/teams/`` i karta jednej drużyny (``docs/UNIWERSALNY-ETAP-2.md``
§ 1.2.3, § 2.2).

Jedyne miejsce, w którym organizator zakłada skład, dopisuje do niego uczestników, wskazuje
kapitana i wpisuje drużynę do etapu.

**Dlaczego ekran jest za flagą ``team_entries``.** Drużyna jest **właścicielem wpisu do etapu**,
a nie nowym bytem obok uczestnika: wszystko, co dzieje się dalej (zgłoszenie, recenzje, suma,
próg, dyplom), chodzi po wpisach i dlatego nie zmienia się wcale. Dopóki organizator drużyn nie
prowadzi, w bazie nie ma ani jednej – i ekran, który pozwalałby je zakładać, obiecywałby zawody,
których regulamin nie zna. Adres przy wyłączonej fladze daje **404**, a nie 403 (§ 2.1): adresu,
którego w tej instalacji nie ma, nie ma tak samo jak adresu cudzego konkursu. Rolę sprawdza
wcześniej i osobno ``CoordinatorRequiredMixin``, więc uczestnik dostaje 403 niezależnie od flagi
i nie dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Reguły są w serwisie, nie tutaj.** Kod publiczny z prefiksem konkursu, ponawianie przy kolizji,
jeden kapitan na drużynę, skład z tego samego konkursu, zgodność edycji przy wpisie do etapu
i **każdy** wpis audytowy stoją w ``apps.competitions.services``; widok wyłącznie orkiestruje
i tłumaczy ``DomainError`` na komunikat ze statusem. Bramka flagi jest w serwisie **także** –
i to jest celowe podwojenie: ekran jej nie ma jak ominąć, a import i komenda nie mają jak obejść
ekranu.

**Uczestnika wskazuje się kodem publicznym**, nie nazwiskiem. Ekran składu miałby inaczej własną
wyszukiwarkę po danych osobowych, czyli drugą listę uczestników – a kod jest tym, co koordynator
ma w protokole i co uczestnik widzi na swoim pulpicie.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import Participant
from apps.competitions.models import Stage, Team
from apps.competitions.services import (
    add_team_member,
    create_team,
    current_edition,
    register_team_for_stage,
    remove_team_member,
    set_team_captain,
)
from apps.core.api import DomainError
from apps.web.coordinator_forms import TeamForm, TeamMemberForm, TeamStageForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/teams.html"
DETAIL_TEMPLATE = "web/coordinator/teams_detail.html"

#: Przełącznik, który włącza te ekrany – ta sama nazwa, którą czyta bramka serwisu
#: (``_assert_team_entries_enabled``). Napis w dwóch miejscach dałby ekran włączony flagą, której
#: nie czyta czynność, do której ten ekran prowadzi.
FEATURE = "team_entries"


class TeamScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów drużyn: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile prowadzi zgłoszenia drużynowe. Inaczej 404.

        Konkurs bierzemy z ``request.competition`` (ustawia go ``CompetitionMiddleware``), a nie
        z adresu – dzięki temu nie ma tu drogi, którą dałoby się wskazać cudzą drużynę.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie prowadzi zgłoszeń drużynowych – ekran jest wyłączony.")
        return competition

    def team_or_404(self, competition, pk: int) -> Team:
        """Drużyna **tego** konkursu albo 404 – zakres wychodzi z querysetu, nie z widoku."""
        return get_object_or_404(Team.objects.for_competition(competition).select_related("edition"), pk=pk)

    def participant_or_none(self, competition, public_code: str):
        """Uczestnik **tego** konkursu o podanym kodzie publicznym albo ``None``.

        ``None``, a nie 404: pomyłka w przepisanym z protokołu kodzie jest błędem formularza,
        a nie nieistniejącym adresem – koordynator ma dostać stronę z komunikatem pod polem.
        """
        return Participant.objects.for_competition(competition).filter(public_code=public_code).first()


def _teams_of_edition(competition, edition):
    """Drużyny edycji razem z licznością składu – jedno zapytanie na całą listę, nie jedno na wiersz."""
    if edition is None:
        return []
    return list(
        Team.objects.for_competition(competition)
        .filter(edition=edition)
        .annotate(member_count=Count("members"))
        .order_by("name", "id")
    )


def _render_list(request, competition, form: TeamForm, *, status: int = 200):
    """Strona listy drużyn. Wspólna dla wejścia i dla nieudanego zakładania."""
    edition = current_edition(competition)
    context = {
        "edition": edition,
        "teams": _teams_of_edition(competition, edition),
        "form": form,
    }
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


def _render_detail(
    request, team: Team, member_form: TeamMemberForm, stage_form: TeamStageForm, *, status: int = 200
):
    """Karta drużyny: skład, wpisy do etapów i dwa formularze. Wspólna dla każdej nieudanej akcji."""
    context = {
        "team": team,
        "members": list(
            team.members.select_related("participant").order_by("-is_captain", "participant__public_code")
        ),
        "entries": list(team.stage_entries.select_related("stage").order_by("stage__opens_at", "id")),
        "member_form": member_form,
        "stage_form": stage_form,
    }
    return TemplateResponse(request, DETAIL_TEMPLATE, context, status=status)


def _stage_choices(team: Team):
    """Etapy, do których wolno wpisać tę drużynę: etapy **jej** edycji, bez tych już wpisanych."""
    return (
        Stage.objects.filter(edition_id=team.edition_id)
        .exclude(entries__team=team)
        .order_by("opens_at", "id")
    )


class TeamListView(TeamScreenMixin, View):
    """``GET|POST /coordinator/teams/`` – drużyny bieżącej edycji i formularz założenia nowej."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return _render_list(request, competition, TeamForm())

    def post(self, request):
        competition = self.competition_or_404(request)
        edition = current_edition(competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – drużyna nie ma do czego należeć.")
            return _render_list(request, competition, TeamForm(request.POST), status=409)
        form = TeamForm(request.POST)
        if not form.is_valid():
            return _render_list(request, competition, form, status=400)
        try:
            team = create_team(edition=edition, actor=request.user, request=request, **form.cleaned_data)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_list(request, competition, form, status=exc.status_code)
        messages.success(request, f"Drużyna {team.name} ({team.public_code}) została założona.")
        return redirect(reverse("web:coordinator-team", args=[team.pk]))


class TeamDetailView(TeamScreenMixin, View):
    """``GET|POST /coordinator/teams/<id>/`` – skład drużyny; POST dopisuje uczestnika."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        team = self.team_or_404(competition, pk)
        return _render_detail(request, team, TeamMemberForm(), TeamStageForm(stages=_stage_choices(team)))

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        team = self.team_or_404(competition, pk)
        form = TeamMemberForm(request.POST)
        stage_form = TeamStageForm(stages=_stage_choices(team))
        if not form.is_valid():
            return _render_detail(request, team, form, stage_form, status=400)
        participant = self.participant_or_none(competition, form.cleaned_data["public_code"])
        if participant is None:
            form.add_error("public_code", "Nie ma uczestnika o tym kodzie w tym konkursie.")
            return _render_detail(request, team, form, stage_form, status=400)
        try:
            add_team_member(
                team,
                participant,
                is_captain=form.cleaned_data["is_captain"],
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_detail(request, team, form, stage_form, status=exc.status_code)
        messages.success(request, f"Uczestnik {participant.public_code} został dopisany do składu.")
        return redirect(reverse("web:coordinator-team", args=[team.pk]))


class TeamMemberActionView(TeamScreenMixin, View):
    """Baza dwóch czynności na składzie: usunięcia i przekazania funkcji kapitana.

    Wspólne jest wszystko poza jednym wywołaniem serwisu: bramka flagi, zakres drużyny, odczyt
    uczestnika po kodzie i tłumaczenie odmowy. Osobne adresy, a nie jeden z polem „akcja”:
    rozróżnianie po nazwie przycisku zależy od tego, czy przeglądarka go przyśle, a przy wysyłce
    klawiaturą nie zawsze przysyła.
    """

    #: Komunikat sukcesu – jedno zdanie na czynność, z kodem uczestnika.
    success_message = ""

    def perform(self, team: Team, participant, request) -> None:  # pragma: no cover - klasa bazowa
        raise NotImplementedError

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        team = self.team_or_404(competition, pk)
        code = (request.POST.get("public_code") or "").strip().upper()
        participant = self.participant_or_none(competition, code)
        if participant is None:
            messages.error(request, "Nie ma uczestnika o tym kodzie w tym konkursie.")
            return _render_detail(
                request, team, TeamMemberForm(), TeamStageForm(stages=_stage_choices(team)), status=404
            )
        try:
            self.perform(team, participant, request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_detail(
                request,
                team,
                TeamMemberForm(),
                TeamStageForm(stages=_stage_choices(team)),
                status=exc.status_code,
            )
        messages.success(request, self.success_message.format(code=participant.public_code))
        return redirect(reverse("web:coordinator-team", args=[team.pk]))


class TeamMemberRemoveView(TeamMemberActionView):
    """``POST /coordinator/teams/<id>/members/remove/`` – usunięcie uczestnika ze składu."""

    success_message = "Uczestnik {code} został usunięty ze składu."

    def perform(self, team: Team, participant, request) -> None:
        remove_team_member(team, participant, actor=request.user, request=request)


class TeamCaptainView(TeamMemberActionView):
    """``POST /coordinator/teams/<id>/captain/`` – przekazanie funkcji kapitana."""

    success_message = "Kapitanem drużyny jest teraz uczestnik {code}."

    def perform(self, team: Team, participant, request) -> None:
        set_team_captain(team, participant, actor=request.user, request=request)


class TeamStageEntryView(TeamScreenMixin, View):
    """``POST /coordinator/teams/<id>/stage/`` – wpis drużyny do etapu.

    Wpis zakłada koordynator, a nie drużyna, i dlatego nie ma tu okna rejestracji: regulamin
    konkursu drużynowego nie zna samodzielnego zgłoszenia składu (``register_team_for_stage``).
    """

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        team = self.team_or_404(competition, pk)
        form = TeamStageForm(request.POST, stages=_stage_choices(team))
        if not form.is_valid():
            return _render_detail(request, team, TeamMemberForm(), form, status=400)
        stage = form.cleaned_data["stage"]
        try:
            register_team_for_stage(team, stage, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return _render_detail(request, team, TeamMemberForm(), form, status=exc.status_code)
        messages.success(request, f"Drużyna została wpisana do etapu {stage.display_name}.")
        return redirect(reverse("web:coordinator-team", args=[team.pk]))
