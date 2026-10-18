"""Scalanie zdublowanych kont uczestnika w panelu koordynatora (ACC-MERGE-01): podgląd i „Scal”.

Reguły (co przechodzi, co jest konfliktem, kiedy odmowa) mieszkają w ``apps.accounts.merge``; widok
wyłącznie orkiestruje, tak jak sąsiedni ``coordinator_duplicates``:

- ``GET`` – podgląd planu (``plan_merge``) bez żadnego zapisu: co zostanie przepięte, co odpadnie,
  które pola profilu się uzupełnią, który adres e-mail zostaje, odmowy i konflikty,
- ``POST`` – scalenie (``merge_participants``); wartość potwierdzenia niesie sam przycisk
  „Tak, scal konto …” (``confirm=<id kopii>``), jak „Usuń” w wierszu (ACC-DUP-02 § 2).

Para musi być **teraz** w jednej grupie duplikatów: serwis sam wymaga tylko tego samego konkursu,
a ekran nie ma być drogą do sklejenia dwóch różnych osób spreparowanym adresem.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.account_cleanup import collect_facts
from apps.accounts.duplicates import find_duplicate_groups
from apps.accounts.merge import merge_participants, plan_merge
from apps.accounts.models import Participant
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.views.coordinator_accounts import account_status
from apps.web.views.coordinator_duplicates import participant_here_or_404

TEMPLATE = "web/coordinator/accounts_duplicates_merge.html"


def merge_url(target_id: int, source_id: int) -> str:
    """Adres podglądu – identyfikatory kont, nic więcej (żadnych danych osobowych w adresie)."""
    params = urlencode({"target": target_id, "source": source_id})
    return f"{reverse('web:coordinator-duplicates-merge')}?{params}"


def same_group(competition, *user_ids: int) -> int | None:
    """Numer grupy duplikatów, w której są **wszystkie** wskazane konta, albo ``None``."""
    wanted = set(user_ids)
    for group in find_duplicate_groups(competition, with_email_suspects=False).groups:
        if wanted <= {account.user.pk for account in group.accounts}:
            return group.number
    return None


def prepare_group_merge(group, actor) -> None:
    """Dokleja do grupy ``merge_options``, ``merge_target`` i ``merge_source`` – formularz „Scal…”.

    Konto docelowe domyślnie: „do zachowania”, inaczej ostatnio logowane, inaczej najstarsze. Kont
    chronionych i własnego konta wykonawcy nie ma na liście – serwis i tak by ich nie przepuścił.
    """
    options = [account for account in group.accounts if not account.protected and account.user.pk != actor.pk]
    group.merge_options = options if len(options) > 1 else []
    if not group.merge_options:
        return
    keeper = next((account for account in options if account.suggestion == "keep"), None)
    if keeper is None:
        logged = [account for account in options if account.user.last_login is not None]
        keeper = max(logged, key=lambda account: account.user.last_login) if logged else options[0]
    group.merge_target = keeper.user.pk
    group.merge_source = next(account.user.pk for account in options if account is not keeper)


class CoordinatorDuplicatesMergeView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/duplicates/merge/?target=<id>&source=<id>`` – podgląd (GET) i „Scal” (POST)."""

    def _pair(self, request, data) -> tuple[Participant, Participant] | None:
        try:
            target_id, source_id = int(data.get("target", "")), int(data.get("source", ""))
        except TypeError, ValueError:
            return None
        if target_id == source_id:
            return None
        # 404 dla konta spoza profili uczestnika **tego** konkursu – kod nie zdradza cudzych kont.
        keep = participant_here_or_404(request.competition, target_id)
        drop = participant_here_or_404(request.competition, source_id)
        related = Participant.objects.select_related("user", "competition")
        return related.get(pk=keep.pk), related.get(pk=drop.pk)

    def get(self, request):
        pair = self._pair(request, request.GET)
        if pair is None:
            return HttpResponseBadRequest("Wskaż dwa różne konta.")
        keep, drop = pair
        back = redirect(reverse("web:coordinator-duplicates"))
        group = same_group(request.competition, keep.user_id, drop.user_id)
        if group is None:
            messages.warning(request, "Te konta nie są (już) w jednej grupie zdublowanych kont.")
            return back
        try:
            plan = plan_merge(keep, drop, actor=request.user)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return back
        facts = collect_facts(request.competition, [keep.pk, drop.pk])
        context = {
            "plan": plan,
            "keep": facts.get(keep.pk),
            "drop": facts.get(drop.pk),
            "keep_status": account_status(keep.user),
            "drop_status": account_status(drop.user),
            "group_number": group,
            "swap_url": merge_url(drop.user_id, keep.user_id),
        }
        return TemplateResponse(request, TEMPLATE, context)

    def post(self, request):
        pair = self._pair(request, request.POST)
        if pair is None:
            return HttpResponseBadRequest("Wskaż dwa różne konta.")
        keep, drop = pair
        preview = redirect(merge_url(keep.user_id, drop.user_id))
        if request.POST.get("confirm") != str(drop.user_id):
            messages.error(request, "Scalenie wymaga potwierdzenia przyciskiem „Tak, scal konto…”.")
            return preview
        if same_group(request.competition, keep.user_id, drop.user_id) is None:
            messages.warning(request, "Te konta nie są (już) w jednej grupie zdublowanych kont.")
            return redirect(reverse("web:coordinator-duplicates"))
        kept_code, merged_code = keep.public_code, drop.public_code
        try:
            result = merge_participants(
                keep,
                drop,
                actor=request.user,
                request=request,
                take_email=request.POST.get("take_email") == "1",
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return preview
        moved = (
            ", ".join(f"{line.label}: {line.count}" for line in result.plan.moved) or "nic poza samym kontem"
        )
        text = f"Scalono konto {merged_code} w {kept_code}. Przepięto – {moved}."
        if result.email_taken:
            keep.user.refresh_from_db(fields=["email"])
            text += f" Konto {kept_code} ma teraz adres {keep.user.email}."
        if result.outcome == "anonymised":
            text += " Kopia miała jeszcze ślad w zawodach, więc została zanonimizowana, a nie usunięta."
        messages.success(request, text)
        return redirect(reverse("web:coordinator-duplicates"))
