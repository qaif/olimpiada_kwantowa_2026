"""Ekrany logistyki finału: opiekun drużyny, koordynator / oficer logistyki i obsługa rejestracji.

Trzy wejścia, trzy bramki (``access`` – LOG-01 § 2), a przed każdą z nich ta sama kolejność, co
w pozostałych ekranach za przełącznikiem: anonim → logowanie, zła rola → 403, a dopiero osoba z rolą
dowiaduje się, czy ekran w tym konkursie istnieje (404 bez flagi ``onsite_logistics`` albo poza
trybem delegacji).

Każdy obiekt jest wybierany z querysetu zawężonego do **konkursu żądania** (oficer, obsługa) albo do
**delegacji opiekuna** (opiekun) – cudzy członek, pokój albo list daje 404 z zawężenia, a nie
z warunku w widoku. Odpowiedzi z danymi osób mają ``Cache-Control: private, no-store``: paszport
w pamięci podręcznej przeglądarki na wspólnym komputerze biura delegacji to wyciek bez włamania.

Ekrany opiekuna i obsługi są tłumaczone (gettext), ekrany koordynatora – po polsku (I18N-01 § 0).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.accounts.delegations import Delegation
from apps.core.api import DomainError
from apps.core.exports import csv_response
from apps.core.models import AuditLog, audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.delegation import TeamLeaderRequiredMixin

from . import access, badges, letters, reports, rooming, services
from .forms import (
    AccessForm,
    CheckpointForm,
    EventForm,
    GuestForm,
    MemberForm,
    PhotoForm,
    RoomForm,
    submitted,
)
from .letter_texts import DEFAULT_LANGUAGE, letter_languages
from .models import FieldGroup, LogisticsAccess, MemberKind

THROTTLE_SCOPE = "onsite_logistics"


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


def _photo_response(member):
    handle = services.open_photo(member)
    if handle is None:
        raise Http404("Brak zdjęcia albo zdjęcie czeka na skan antywirusowy.")
    response = FileResponse(handle, content_type=member.photo_mime)
    response["X-Content-Type-Options"] = "nosniff"
    # Obraz otwarty wprost jest dokumentem pod domeną panelu – ``sandbox`` odbiera mu pochodzenie.
    response["Content-Security-Policy"] = "sandbox"
    return _no_store(response)


def _pdf(data: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return _no_store(response)


# --- opiekun drużyny ----------------------------------------------------------------------------------


class LeaderMixin(TeamLeaderRequiredMixin):
    """Opiekun z delegacją w bieżącej edycji **i** konkurs z logistyką finału."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            services.require_enabled(self.competition)
        response = super().dispatch(request, *args, **kwargs)
        return _no_store(response)

    def member(self, pk):
        return services.member_for_leader(self.leader, pk)


class LeaderDashboardView(LeaderMixin, View):
    """``/delegation/logistics/`` – członkowie delegacji ze stanem grup, goście, listy wizowe."""

    def get(self, request):
        delegation = self.delegation
        competition = self.competition
        groups = services.groups_for(competition)
        event = services.event_for(delegation.edition)
        members = services.members_of(delegation)
        rows = [
            {
                "member": member,
                "missing": [FieldGroup(group).label for group in services.missing_groups(member, groups)],
            }
            for member in members
        ]
        context = {
            "delegation": delegation,
            "event": event,
            "rows": rows,
            "deadlines": [
                {
                    "label": FieldGroup(group).label,
                    "deadline": services.group_deadline(event, group),
                    "locked": services.group_locked(event, group),
                }
                for group in groups
            ],
            "guest_form": GuestForm(),
            "letters": letters.letters_of(competition, delegation.edition, delegation),
            "purged": event is not None and event.purged_at is not None,
            "guest_count": sum(1 for member in members if member.kind == MemberKind.GUEST),
            "max_guests": services.MAX_GUESTS,
        }
        return TemplateResponse(request, "delegation_logistics/leader_dashboard.html", context)


class LeaderGuestAddView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request):
        form = GuestForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Podaj imię, nazwisko i rolę gościa."))
            return redirect("web:delegation-logistics")
        try:
            member = services.add_guest(
                self.delegation, **form.cleaned_data, actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect("web:delegation-logistics")
        messages.success(request, _("Dodano osobę do delegacji – uzupełnij jej dane."))
        return redirect("web:delegation-logistics-member", pk=member.pk)


class MemberFormMixin:
    """Wspólny formularz członka – opiekun (z terminami) i oficer (bez blokady terminów)."""

    as_officer = False
    template_name = ""

    def _context(self, request, member, form, photo_form, **extra):
        return {
            "member": member,
            "form": form,
            "photo_form": photo_form,
            "guest_form": GuestForm(
                initial={
                    "first_name": member.guest.first_name,
                    "last_name": member.guest.last_name,
                    "email": member.guest.email,
                    "role": member.guest.role,
                }
            )
            if member.guest_id
            else None,
            "photo_locked": not self.as_officer
            and services.group_locked(services.event_for(member.delegation.edition), FieldGroup.PERSONAL),
            **extra,
        }

    def _form(self, member, data=None):
        competition = member.delegation.competition
        return MemberForm(
            data,
            member=member,
            groups=services.groups_for(competition),
            event=services.event_for(member.delegation.edition),
            as_officer=self.as_officer,
        )

    def _save(self, request, member):
        form = self._form(member, request.POST)
        if not form.is_valid():
            return None, form
        data = submitted(form)
        consent = bool(data.pop("health_consent", False))
        try:
            services.save_member(
                member,
                data,
                actor=request.user,
                as_officer=self.as_officer,
                health_consent=consent,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return None, form
        return member, form


class LeaderMemberView(LeaderMixin, ThrottledFormMixin, MemberFormMixin, View):
    """``/delegation/logistics/members/<pk>/`` – dane pobytu jednej osoby delegacji."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request, pk: int):
        member = self.member(pk)
        return self._render(request, member, self._form(member))

    def post(self, request, pk: int):
        member = self.member(pk)
        saved, form = self._save(request, member)
        if saved is None:
            return self._render(request, member, form, status=400)
        messages.success(request, _("Zapisano dane: %(name)s.") % {"name": member.full_name})
        return redirect("web:delegation-logistics")

    def _render(self, request, member, form, *, status: int = 200):
        context = self._context(request, member, form, PhotoForm(), delegation=self.delegation)
        return TemplateResponse(request, "delegation_logistics/leader_member.html", context, status=status)


class LeaderPhotoView(LeaderMixin, ThrottledFormMixin, View):
    """GET – zdjęcie (po czystym skanie); POST – wgranie nowego zdjęcia."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request, pk: int):
        return _photo_response(self.member(pk))

    def post(self, request, pk: int):
        member = self.member(pk)
        form = PhotoForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, _("Wybierz plik ze zdjęciem."))
        else:
            try:
                services.upload_photo(member, form.cleaned_data["photo"], actor=request.user, request=request)
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(
                    request, _("Zdjęcie przesłane – pojawi się po automatycznym sprawdzeniu pliku.")
                )
        return redirect("web:delegation-logistics-member", pk=member.pk)


class LeaderGuestEditView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        member = self.member(pk)
        form = GuestForm(request.POST)
        if form.is_valid():
            try:
                services.update_guest(member, **form.cleaned_data, actor=request.user, request=request)
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, _("Zapisano."))
        else:
            messages.error(request, _("Podaj imię, nazwisko i rolę gościa."))
        return redirect("web:delegation-logistics-member", pk=member.pk)


class LeaderGuestRemoveView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        member = self.member(pk)
        services.remove_guest(member, actor=request.user, request=request)
        messages.success(request, _("Usunięto osobę z delegacji razem z jej danymi."))
        return redirect("web:delegation-logistics")


class LeaderHealthWithdrawView(LeaderMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        member = self.member(pk)
        services.withdraw_health_consent(member, actor=request.user, request=request)
        messages.success(
            request, _("Zgoda wycofana – dane o wyżywieniu i zdrowiu tej osoby zostały usunięte.")
        )
        return redirect("web:delegation-logistics-member", pk=member.pk)


class LeaderLetterView(LeaderMixin, View):
    """Pobranie listu zapraszającego **swojej** delegacji."""

    def get(self, request, pk: int):
        letter = letters.letter_for(self.competition, pk, delegation=self.delegation)
        try:
            data = letters.letter_pdf(letter)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect("web:delegation-logistics")
        audit(request.user, "logistics.letter_downloaded", letter, {"number": letter.number}, request=request)
        return _pdf(data, letters.pdf_filename(letter))


# --- koordynator i oficer logistyki ---------------------------------------------------------------------


class CoordinatorMixin(CoordinatorRequiredMixin):
    """Koordynator konkursu z logistyką finału. Ekrany bez danych osób."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            services.require_enabled(self.competition)
            self.check_access(request)
        response = super().dispatch(request, *args, **kwargs)
        return _no_store(response)

    def check_access(self, request) -> None:
        """Koordynator wystarcza. Ekrany z danymi osób podnoszą poprzeczkę (:class:`OfficerMixin`)."""

    @property
    def edition(self):
        return services.current_edition(self.competition)

    def base_context(self, **extra):
        return {
            "competition": self.competition,
            "is_officer": access.is_officer(self.request.user, self.competition),
            "can_check_in": access.can_check_in(self.request.user, self.competition),
            # Języki listu zapraszającego (VISA-01) – wybór przy wystawianiu z rejestru i z karty osoby.
            "letter_languages": letter_languages(self.competition),
            **extra,
        }

    def render(self, request, template: str, context: dict, *, status: int = 200):
        return TemplateResponse(
            request, f"delegation_logistics/{template}", self.base_context(**context), status=status
        )


class OfficerMixin(CoordinatorMixin):
    """Oficer logistyki – wyłącznie on widzi dane osób (LOG-01 § 2)."""

    def check_access(self, request) -> None:
        access.require_officer(request.user, self.competition)


class OverviewView(CoordinatorMixin, View):
    """``/coordinator/logistics/`` – kompletność per kraj (liczby), terminy, przypomnienia."""

    def get(self, request):
        competition = self.competition
        edition = self.edition
        members = services.edition_members(edition)
        groups = services.groups_for(competition)
        event = services.event_for(edition)
        latest = reports.last_reminders(edition)
        rows = reports.completeness(competition, edition, members)
        for row in rows:
            row["last_reminder"] = latest.get(row["delegation"].pk)
        return self.render(
            request,
            "overview.html",
            {
                "event": event,
                "rows": rows,
                "groups": [FieldGroup(group).label for group in groups],
                "deadlines": [
                    {"label": FieldGroup(group).label, "deadline": services.group_deadline(event, group)}
                    for group in groups
                ],
                "member_count": len(members),
                "health": FieldGroup.HEALTH in groups,
                "attendance": badges.attendance(edition, members),
            },
        )


class SettingsView(CoordinatorMixin, ThrottledFormMixin, View):
    """``/coordinator/logistics/settings/`` – finał, terminy, przydziały dostępu, punkty kontroli."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request):
        return self._render(request, EventForm(instance=services.event_for(self.edition)))

    def post(self, request):
        form = EventForm(request.POST, instance=services.event_for(self.edition))
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            services.save_event(
                self.competition, self.edition, actor=request.user, request=request, **form.cleaned_data
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=400)
        messages.success(request, "Zapisano ustawienia finału.")
        return redirect("web:coordinator-onsite-settings")

    def _render(self, request, form, *, status: int = 200):
        return self.render(
            request,
            "settings.html",
            {
                "form": form,
                "access_form": AccessForm(),
                "grants": access.grants_of(self.competition),
                "checkpoint_form": CheckpointForm(),
                "checkpoints": badges.checkpoints_of(self.edition),
            },
            status=status,
        )


class AccessGrantView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request):
        form = AccessForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Podaj adres e-mail konta i przydział.")
        else:
            try:
                access.grant(self.competition, **form.cleaned_data, actor=request.user, request=request)
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, f"Nadano przydział: {form.cleaned_data['email']}.")
        return redirect("web:coordinator-onsite-settings")


class AccessRevokeView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        grant = LogisticsAccess.objects.for_competition(self.competition).filter(pk=pk).first()
        if grant is None:
            raise Http404("Nie ma takiego przydziału.")
        try:
            access.revoke(grant, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Odebrano przydział.")
        return redirect("web:coordinator-onsite-settings")


class CheckpointAddView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request):
        form = CheckpointForm(request.POST)
        if form.is_valid():
            try:
                badges.create_checkpoint(
                    self.competition,
                    self.edition,
                    name=form.cleaned_data["name"],
                    actor=request.user,
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
        return redirect("web:coordinator-onsite-settings")


class CheckpointDeleteView(CoordinatorMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        checkpoint = badges.checkpoint_for(self.competition, pk)
        if checkpoint is None:
            raise Http404("Nie ma takiego punktu kontroli.")
        badges.delete_checkpoint(checkpoint, actor=request.user, request=request)
        messages.success(request, "Usunięto punkt kontroli razem z jego odhaczeniami.")
        return redirect("web:coordinator-onsite-settings")


class RemindersView(CoordinatorMixin, ThrottledFormMixin, View):
    """POST – przypomnienia o brakach do opiekunów (wszystkich delegacji albo jednej)."""

    throttle_scope = THROTTLE_SCOPE

    def post(self, request):
        delegations = None
        raw = request.POST.get("delegation")
        if raw:
            delegation = Delegation.objects.for_competition(self.competition).filter(pk=raw).first()
            if delegation is None:
                raise Http404("Nie ma takiej delegacji.")
            delegations = [delegation]
        sent = reports.send_reminders(
            self.competition, self.edition, delegations=delegations, actor=request.user, request=request
        )
        messages.success(request, f"Wysłano przypomnień: {sent}.")
        return redirect("web:coordinator-onsite")


class MembersView(OfficerMixin, View):
    """``/coordinator/logistics/members/`` – wszystkie osoby z brakami; filtr kraju."""

    def get(self, request):
        competition = self.competition
        members = services.edition_members(self.edition)
        groups = services.groups_for(competition)
        delegation_id = request.GET.get("delegation", "")
        if delegation_id.isdigit():
            members = [m for m in members if m.delegation_id == int(delegation_id)]
        rows = [
            {"member": m, "missing": [FieldGroup(g).label for g in services.missing_groups(m, groups)]}
            for m in members
        ]
        delegations = (
            Delegation.objects.for_competition(competition)
            .filter(edition=self.edition)
            .select_related("country")
        )
        return self.render(
            request,
            "members.html",
            {"rows": rows, "delegations": delegations, "selected": delegation_id, "guest_form": GuestForm()},
        )


class OfficerGuestAddView(OfficerMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        delegation = Delegation.objects.for_competition(self.competition).filter(pk=pk).first()
        if delegation is None:
            raise Http404("Nie ma takiej delegacji.")
        form = GuestForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Podaj imię, nazwisko i rolę gościa.")
            return redirect("web:coordinator-onsite-members")
        try:
            member = services.add_guest(delegation, **form.cleaned_data, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect("web:coordinator-onsite-members")
        return redirect("web:coordinator-onsite-member", pk=member.pk)


class OfficerMemberView(OfficerMixin, ThrottledFormMixin, MemberFormMixin, View):
    """Karta osoby: pełne dane (także po terminie), zdjęcie, pokój, identyfikator, historia zmian."""

    as_officer = True
    throttle_scope = THROTTLE_SCOPE

    def get(self, request, pk: int):
        member = services.member_for_competition(self.competition, pk)
        # Otwarcie karty z paszportem i zdrowiem jest zdarzeniem – „kto oglądał” ma mieć odpowiedź.
        audit(request.user, "logistics.member_viewed", member, {}, request=request)
        return self._render(request, member, self._form(member))

    def post(self, request, pk: int):
        member = services.member_for_competition(self.competition, pk)
        saved, form = self._save(request, member)
        if saved is None:
            return self._render(request, member, form, status=400)
        messages.success(request, "Zapisano dane osoby.")
        return redirect("web:coordinator-onsite-member", pk=member.pk)

    def _render(self, request, member, form, *, status: int = 200):
        history = AuditLog.objects.filter(
            target_type="delegation_logistics.delegationmember", target_id=str(member.pk)
        ).select_related("actor")[:50]
        context = self._context(
            request,
            member,
            form,
            PhotoForm(),
            history=history,
            rooms=rooming.rooms_of(member.delegation.edition),
            letters=member.letters.order_by("-issued_at"),
            check_ins=badges.status_of(member),
            checkpoints=badges.checkpoints_of(member.delegation.edition),
            minor=member.is_minor_on(rooming.reference_day(member.delegation.edition)),
        )
        return self.render(request, "member.html", context, status=status)


class OfficerPhotoView(OfficerMixin, ThrottledFormMixin, View):
    """GET – zdjęcie osoby; POST – wgranie przez oficera (także po terminie)."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request, pk: int):
        return _photo_response(services.member_for_competition(self.competition, pk))

    def post(self, request, pk: int):
        member = services.member_for_competition(self.competition, pk)
        form = PhotoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                services.upload_photo(
                    member, form.cleaned_data["photo"], actor=request.user, as_officer=True, request=request
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, "Zdjęcie przesłane – pojawi się po skanie antywirusowym.")
        return redirect("web:coordinator-onsite-member", pk=member.pk)


class OfficerMemberActionView(OfficerMixin, ThrottledFormMixin, View):
    """Czynności na karcie osoby: pokój, nowy identyfikator, usunięcie gościa, wycofanie zgody."""

    throttle_scope = THROTTLE_SCOPE
    action = ""

    def post(self, request, pk: int):
        member = services.member_for_competition(self.competition, pk)
        target = redirect("web:coordinator-onsite-member", pk=member.pk)
        try:
            if self.action == "room":
                raw = request.POST.get("room", "")
                room = rooming.room_for(self.competition, int(raw)) if raw.isdigit() else None
                rooming.assign(member, room, actor=request.user, request=request)
                messages.success(request, "Zmieniono przydział pokoju.")
                if request.POST.get("next") == "rooming":
                    target = redirect("web:coordinator-onsite-rooming")
            elif self.action == "badge":
                badges.reissue_badge(member, actor=request.user, request=request)
                messages.success(request, "Wydano nowy identyfikator – poprzedni przestał działać.")
            elif self.action == "remove-guest":
                services.remove_guest(member, actor=request.user, request=request)
                messages.success(request, "Usunięto gościa razem z jego danymi.")
                return redirect("web:coordinator-onsite-members")
            elif self.action == "health-withdraw":
                services.withdraw_health_consent(member, actor=request.user, request=request)
                messages.success(request, "Zgoda wycofana, dane o zdrowiu usunięte.")
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            if request.POST.get("next") == "rooming":
                target = redirect("web:coordinator-onsite-rooming")
        return target


class TravelView(OfficerMixin, View):
    """Tablica przyjazdów / wyjazdów – planowanie odbiorów z lotniska i dworca."""

    def get(self, request):
        direction = "departure" if request.GET.get("direction") == "departure" else "arrival"
        members = services.edition_members(self.edition)
        return self.render(
            request,
            "travel.html",
            {"board": reports.travel_board(members, direction=direction), "direction": direction},
        )


class RoomingView(OfficerMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def get(self, request):
        return self._render(request, RoomForm())

    def post(self, request):
        form = RoomForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            rooming.create_room(
                self.competition, self.edition, **form.cleaned_data, actor=request.user, request=request
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=400)
        messages.success(request, "Dodano pokój.")
        return redirect("web:coordinator-onsite-rooming")

    def _render(self, request, form, *, status: int = 200):
        edition = self.edition
        members = services.edition_members(edition)
        data = rooming.rooming_rows(edition, members)
        return self.render(
            request,
            "rooming.html",
            {**data, "form": form, "rooms": rooming.rooms_of(edition)},
            status=status,
        )


class RoomDeleteView(OfficerMixin, ThrottledFormMixin, View):
    throttle_scope = THROTTLE_SCOPE

    def post(self, request, pk: int):
        rooming.delete_room(rooming.room_for(self.competition, pk), actor=request.user, request=request)
        messages.success(request, "Usunięto pokój – jego mieszkańcy wrócili na listę nieprzydzielonych.")
        return redirect("web:coordinator-onsite-rooming")


class DietaryView(OfficerMixin, View):
    def get(self, request):
        members = services.edition_members(self.edition)
        return self.render(
            request,
            "dietary.html",
            {
                "summary": reports.dietary_summary(members),
                "health": FieldGroup.HEALTH in services.groups_for(self.competition),
            },
        )


class TshirtsView(OfficerMixin, View):
    def get(self, request):
        members = services.edition_members(self.edition)
        return self.render(request, "tshirts.html", {"summary": reports.tshirt_summary(members)})


class ExportView(OfficerMixin, View):
    """``/coordinator/logistics/export/<kind>.csv`` – każde wyniesienie listy ludzi w audycie."""

    def get(self, request, kind: str):
        if kind not in reports.EXPORT_KINDS:
            raise Http404("Nie ma takiego zestawienia.")
        members = services.edition_members(self.edition)
        dataset = reports.dataset(kind, self.competition, self.edition, members)
        audit(
            request.user,
            "logistics.exported",
            self.competition,
            {"kind": kind, "rows": dataset.count},
            request=request,
        )
        return _no_store(csv_response(dataset))


class LettersView(OfficerMixin, ThrottledFormMixin, View):
    """Rejestr listów zapraszających i wystawienie nowego (dla delegacji albo imienny)."""

    throttle_scope = THROTTLE_SCOPE

    def get(self, request):
        edition = self.edition
        delegations = list(
            Delegation.objects.for_competition(self.competition)
            .filter(edition=edition)
            .select_related("country")
        )
        return self.render(
            request,
            "letters.html",
            {"letters": letters.letters_of(self.competition, edition), "delegations": delegations},
        )

    def post(self, request):
        raw = request.POST.get("delegation", "")
        delegation = (
            Delegation.objects.for_competition(self.competition).filter(pk=raw).first()
            if raw.isdigit()
            else None
        )
        if delegation is None:
            raise Http404("Nie ma takiej delegacji.")
        member = None
        raw_member = request.POST.get("member", "")
        if raw_member.isdigit():
            member = services.member_for_competition(self.competition, int(raw_member))
        try:
            letter = letters.issue_letter(
                self.competition,
                delegation,
                member=member,
                actor=request.user,
                request=request,
                language=request.POST.get("language") or DEFAULT_LANGUAGE,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Wystawiono list {letter.number} ({letter.people_count} os.).")
        if member is not None:
            return redirect("web:coordinator-onsite-member", pk=member.pk)
        return redirect("web:coordinator-onsite-letters")


class LetterPdfView(OfficerMixin, View):
    def get(self, request, pk: int):
        letter = letters.letter_for(self.competition, pk)
        try:
            data = letters.letter_pdf(letter)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect("web:coordinator-onsite-letters")
        audit(request.user, "logistics.letter_downloaded", letter, {"number": letter.number}, request=request)
        return _pdf(data, letters.pdf_filename(letter))


class BadgesView(OfficerMixin, View):
    """PDF identyfikatorów – wszystkich albo jednej delegacji (``?delegation=``) / osoby (``?member=``)."""

    def get(self, request):
        members = services.edition_members(self.edition)
        raw = request.GET.get("delegation", "")
        if raw.isdigit():
            members = [m for m in members if m.delegation_id == int(raw)]
        raw_member = request.GET.get("member", "")
        if raw_member.isdigit():
            members = [m for m in members if m.pk == int(raw_member)]
        data = badges.badges_pdf(
            members, competition=self.competition, event=services.event_for(self.edition), request=request
        )
        audit(
            request.user,
            "logistics.badges_printed",
            self.competition,
            {"count": len(members)},
            request=request,
        )
        return _pdf(data, "identyfikatory.pdf")


# --- obsługa rejestracji (skanowanie) -------------------------------------------------------------------


class CheckinMixin(LoginRequiredMixin, ThrottledFormMixin):
    """Obsługa rejestracji: przydział ``CHECKIN`` albo oficer. Rola w konkursie niepotrzebna."""

    throttle_scope = "onsite_checkin"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            services.require_enabled(getattr(request, "competition", None))
            if not access.can_check_in(request.user, request.competition):
                raise PermissionDenied(_("Ten ekran jest dostępny wyłącznie dla obsługi rejestracji finału."))
        response = super().dispatch(request, *args, **kwargs)
        return _no_store(response)

    @property
    def competition(self):
        return self.request.competition

    def checkpoint(self, request):
        edition = services.current_edition(self.competition)
        checkpoints = list(badges.checkpoints_of(edition))
        selected = badges.checkpoint_for(self.competition, request.GET.get("cp") or request.POST.get("cp"))
        if selected is None and checkpoints:
            selected = checkpoints[0]
        return edition, checkpoints, selected


class CheckinView(CheckinMixin, View):
    """``/coordinator/logistics/checkin/`` – wybór punktu, wyszukiwarka, skaner QR."""

    def get(self, request):
        edition, checkpoints, selected = self.checkpoint(request)
        query = request.GET.get("q", "")
        found = badges.search(services.edition_members(edition, sync=False), query) if query else []
        return TemplateResponse(
            request,
            "delegation_logistics/checkin.html",
            {"checkpoints": checkpoints, "selected": selected, "query": query, "found": found},
        )


class CheckinPhotoView(CheckinMixin, View):
    """Zdjęcie osoby ze skanu – dla obsługi, która nie jest koordynatorem (wybór po tokenie)."""

    def get(self, request, token: str):
        member = badges.member_by_token(self.competition, token)
        if member is None:
            raise Http404("Nieznany identyfikator.")
        return _photo_response(member)


class CheckinMemberView(CheckinMixin, View):
    """Strona osoby ze skanu: imię, kraj, rola, zdjęcie, stan i „Odhacz” / „Cofnij”."""

    def get(self, request, token: str):
        edition, checkpoints, selected = self.checkpoint(request)
        member = badges.member_by_token(self.competition, token)
        if member is None:
            return TemplateResponse(
                request,
                "delegation_logistics/checkin_unknown.html",
                {"selected": selected},
                status=404,
            )
        return TemplateResponse(
            request,
            "delegation_logistics/checkin_member.html",
            {
                "member": member,
                "checkpoints": checkpoints,
                "selected": selected,
                "status": badges.status_of(member),
                "minor": member.is_minor_on(rooming.reference_day(edition)),
            },
        )

    def post(self, request, token: str):
        edition, checkpoints, selected = self.checkpoint(request)
        member = badges.member_by_token(self.competition, token)
        if member is None or selected is None:
            raise Http404("Nieznany identyfikator albo brak punktu kontroli.")
        if request.POST.get("undo") == "1":
            badges.undo_check_in(member, selected, actor=request.user, request=request)
            messages.info(request, _("Cofnięto odhaczenie."))
        else:
            badges.check_in(member, selected, actor=request.user, request=request)
            messages.success(request, _("Odhaczono: %(name)s.") % {"name": member.full_name})
        return redirect(f"{reverse('web:onsite-checkin-member', args=[token])}?cp={selected.pk}")
