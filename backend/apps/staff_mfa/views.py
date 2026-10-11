"""Ekrany SEC-01: nowy komplet kodów zapasowych (konto) i polityka 2FA konkursu (panel koordynatora).

Oba za wyłącznikiem ``TWO_FACTOR_ENABLED`` – przy wyłączonej funkcji adresy odpowiadają 404, jak
pozostałe ekrany drugiego składnika (``apps.web.views.twofactor.TwoFactorFeatureMixin``).
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.accounts import twofactor
from apps.core.api import DomainError
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.twofactor import FRESH_CODES_SESSION_KEY, TwoFactorFeatureMixin

from . import policy
from .forms import PolicyForm
from .models import TwoFactorGrace, TwoFactorPolicy


class RegenerateCodesView(TwoFactorFeatureMixin, ThrottledFormMixin, LoginRequiredMixin, View):
    """``/account/2fa/codes/regenerate/`` – nowy komplet kodów zapasowych (hasło + bieżący kod).

    Wcześniej jedyną drogą do nowych kodów było wyłączenie i ponowne włączenie 2FA – czyli chwila,
    w której konto personelu nie miało drugiego składnika wcale. Nowy komplet unieważnia stary.
    Kody pokazuje ten sam ekran „raz”, co po włączeniu (``web:twofactor-codes``).
    """

    template_name = "staff_mfa/regenerate.html"
    throttle_scope = twofactor.THROTTLE_SCOPE
    throttle_on_request = False

    def get(self, request):
        if twofactor.confirmed_device(request.user) is None:
            return redirect(reverse("web:twofactor-setup"))
        return self._render(request)

    def post(self, request):
        if twofactor.confirmed_device(request.user) is None:
            return redirect(reverse("web:twofactor-setup"))
        try:
            twofactor.check_credentials(
                request.user, request.POST.get("password", ""), request.POST.get("code", ""), request=request
            )
        except DomainError as exc:
            self.consume_throttle()
            return self._render(request, error=str(exc.detail), status=exc.status_code)
        self.reset_throttle()
        request.session[FRESH_CODES_SESSION_KEY] = twofactor.regenerate_backup_codes(
            request.user, request=request
        )
        return redirect(reverse("web:twofactor-codes"))

    def _render(self, request, *, error: str = "", status: int = 200):
        return TemplateResponse(request, self.template_name, {"error": error}, status=status)


class ForgetDevicesView(TwoFactorFeatureMixin, LoginRequiredMixin, View):
    """``/account/2fa/forget-devices/`` (POST) – „zapomnij wszystkie urządzenia” (przegląd, L4).

    Unieważnia każde wydane wcześniej ciasteczko ``2fa_trust`` tego konta – także w przeglądarkach,
    do których właściciel nie ma już dostępu (zgubiony laptop, komputer w hotelu). Bez hasła: to
    czynność, która wyłącznie **dokłada** pytanie o kod, więc nie ma czego przed nią chronić.
    """

    def post(self, request):
        from apps.staff_mfa import security, trust

        security.revoke_trusted_devices(request.user)
        audit(request.user, "2fa.devices_forgotten", request.user, {}, request)
        messages.success(
            request,
            _("Zapomnieliśmy wszystkie zapamiętane urządzenia – przy następnym logowaniu podasz kod."),
        )
        response = redirect(reverse("web:twofactor-setup"))
        trust.forget(response)
        return response


# --- panel koordynatora -------------------------------------------------------------------------


def _staff_rows(competition) -> list[dict]:
    """Personel konkursu z ról polityki: konto, role, stan 2FA i termin okresu przejściowego.

    Kandydaci z jednego zapytania (grupy, członkostwa tego konkursu, ``is_staff``, przydziały
    logistyki), zawężeni do kont widocznych dla koordynatora tego konkursu (wykluczenie kont
    cudzych konkursów – ``users_for_competition``). Role liczy ta sama funkcja, co wymóg, więc
    ekran nie może pokazać innej odpowiedzi niż ta, którą dostaje warstwa wymuszająca.
    """
    from apps.accounts.models import GROUP_SUPER_COORDINATOR
    from apps.web.views.coordinator_accounts import users_for_competition

    staff_keys = [key for key in policy.COMPETITION_ROLE_KEYS if key != "logistics"]
    candidates = (
        users_for_competition(competition)
        .filter(
            Q(groups__name__in=[*staff_keys, GROUP_SUPER_COORDINATOR])
            | Q(memberships__competition=competition, memberships__role__in=staff_keys)
            | Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(logistics_access__competition=competition)
        )
        .filter(is_active=True)
        .distinct()
        .order_by("last_name", "email")
    )
    users = list(candidates.prefetch_related("groups")[:500])
    ids = [user.pk for user in users]
    # Role liczone hurtem (przegląd, L6): stała liczba zapytań niezależnie od liczby kont, ta sama
    # reguła, co ``roles_for`` (członkostwa przy ``memberships_enforced``, inaczej grupy Django;
    # superkoordynator jest koordynatorem każdego konkursu) i ``role_keys_of`` (logistyka, admin).
    from apps.accounts.models import Membership
    from apps.accounts.services import memberships_enforced
    from apps.delegation_logistics.models import LogisticsAccess

    enforced = memberships_enforced(competition)
    by_membership: dict[int, set[str]] = {}
    if enforced:
        for user_id, role in Membership.objects.filter(competition=competition, user_id__in=ids).values_list(
            "user_id", "role"
        ):
            by_membership.setdefault(user_id, set()).add(role)
    logistics = set(
        LogisticsAccess.objects.filter(competition=competition, user_id__in=ids).values_list(
            "user_id", flat=True
        )
    )
    devices = {
        row.user_id: row
        for row in twofactor.TwoFactorDevice.objects.filter(user_id__in=ids, confirmed_at__isnull=False)
    }
    graces = {row.user_id: row for row in TwoFactorGrace.objects.filter(user_id__in=ids)}
    required = policy.required_roles(competition)
    staff_role_keys = set(policy.COMPETITION_ROLE_KEYS) - {"logistics"}
    days_cache: dict[frozenset, int] = {}

    def _days(matched: frozenset) -> int:
        if matched not in days_cache:
            days_cache[matched] = policy.grace_days_for(matched, competition)
        return days_cache[matched]

    rows = []
    for user in users:
        groups = {group.name for group in user.groups.all()}
        keys: set[str] = set()
        if user.is_staff or user.is_superuser:
            keys.add("admin")
        if GROUP_SUPER_COORDINATOR in groups:
            keys |= {"superkoordynator", "coordinator"}
        keys |= (by_membership.get(user.pk, set()) if enforced else groups) & staff_role_keys
        if user.pk in logistics:
            keys.add("logistics")
        if not keys:
            continue
        grace = graces.get(user.pk)
        matched = keys & required
        rows.append(
            {
                "user": user,
                "roles": [policy.ROLE_KEYS[key] for key in policy.ROLE_KEYS if key in keys],
                "required": bool(matched),
                "device": devices.get(user.pk),
                "deadline": (
                    grace.required_since + timedelta(days=_days(frozenset(matched)))
                    if grace and matched
                    else None
                ),
            }
        )
    return rows


class PolicyView(CoordinatorRequiredMixin, View):
    """``/coordinator/security/2fa/`` – polityka 2FA konkursu i stan personelu.

    Koordynator widzi, superkoordynator zmienia (SEC-01 § 2). Rola w serwisie, nie w szablonie:
    POST od koordynatora to 403, nawet jeśli ktoś dopisze formularz w narzędziach przeglądarki.
    Poluzowanie wymogu przez zwykłego koordynatora zamieniałoby wymóg w zalecenie.
    """

    template_name = "staff_mfa/coordinator_policy.html"

    def dispatch(self, request, *args, **kwargs):
        if not twofactor.is_enabled():
            raise Http404("Logowanie dwuskładnikowe jest wyłączone na tej instalacji.")
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    def _may_edit(self, request) -> bool:
        from apps.accounts.super_coordinator import is_super_coordinator

        return is_super_coordinator(request.user)

    def get(self, request):
        row = policy.policy_for(self.competition)
        return self._render(
            request, PolicyForm(instance=row or TwoFactorPolicy(competition=self.competition))
        )

    def post(self, request):
        if not self._may_edit(request):
            raise PermissionDenied("Politykę 2FA konkursu zmienia wyłącznie superkoordynator.")
        row = policy.policy_for(self.competition) or TwoFactorPolicy(competition=self.competition)
        before = _snapshot(row) if row.pk else None
        form = PolicyForm(request.POST, instance=row)
        if not form.is_valid():
            return self._render(request, form, status=400)
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        policy.bump_version()
        audit(
            request.user, "2fa.policy_changed", saved, {"before": before, "after": _snapshot(saved)}, request
        )
        messages.success(request, "Zapisano politykę logowania dwuskładnikowego.")
        return redirect(reverse("web:coordinator-two-factor"))

    def _render(self, request, form, *, status: int = 200):
        competition = self.competition
        may_edit = self._may_edit(request)
        context = {
            "form": form,
            "may_edit": may_edit,
            # Lista personelu (kto nie ma 2FA = cel ataku) – wyłącznie dla superkoordynatora (L6).
            "rows": _staff_rows(competition) if may_edit else None,
            "platform_roles": [
                policy.ROLE_KEYS[key] for key in policy.ROLE_KEYS if key in policy.platform_roles()
            ],
            "competition_roles": [
                policy.ROLE_KEYS[key]
                for key in policy.ROLE_KEYS
                if key in policy.competition_roles(competition)
            ],
            "sensitive": policy.sensitive_features(competition),
            "grace_days": policy.grace_days(competition),
            "remember_days": policy.remember_days(competition),
        }
        return TemplateResponse(request, self.template_name, context, status=status)


def _snapshot(row: TwoFactorPolicy) -> dict:
    return {
        "mode": row.mode,
        "roles": sorted(row.roles or []),
        "grace_days": row.grace_days,
        "allow_remember": row.allow_remember,
    }
