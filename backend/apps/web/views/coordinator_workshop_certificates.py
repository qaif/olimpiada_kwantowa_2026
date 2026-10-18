"""Panel koordynatora: reguła automatycznych zaświadczeń z warsztatów (WS-CERT-02).

Osobny ekran, a nie karta na ekranie obecności: obecność to tabela, którą się wypełnia po każdych
zajęciach (i którą rozwija import z platformy wideo), a reguła to ustawienie, które zmienia się raz
na edycję. Wspólny ekran kazałby przewijać trzysta wierszy kratek, żeby zmienić próg.

Ekran pracuje na **edycji bieżącej** konkursu z żądania – tak jak ekran obecności i ręczne
„Wystaw (ZIP)”. Reguła domenowa mieszka w ``apps.workshop_certificates.rules``; widok orkiestruje,
a uprawnienie rozstrzyga ``CoordinatorRequiredMixin``.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.competitions.services import current_edition
from apps.core.models import audit
from apps.results.models import Certificate, CertificateKind
from apps.web.mixins import CoordinatorRequiredMixin
from apps.workshop_certificates.forms import AUDITED_FIELDS, AutoIssuePolicyForm
from apps.workshop_certificates.models import AutoIssuedCertificate, AutoIssuePolicy
from apps.workshop_certificates.rules import PENDING_LIMIT, evaluate

TEMPLATE = "web/coordinator/workshop_auto_certificates.html"

#: Ile sekund po „Przelicz teraz” kolejne kliknięcie dla tej samej edycji nic nie zleca. Zadanie
#: i tak jest idempotentne – limit chroni kolejkę przed serią kliknięć w trakcie długiego przebiegu.
RECALCULATE_COOLDOWN_SECONDS = 30


def _snapshot(policy: AutoIssuePolicy) -> dict:
    return {name: getattr(policy, name) for name in AUDITED_FIELDS}


class WorkshopAutoCertificatesView(CoordinatorRequiredMixin, View):
    """``/coordinator/workshops/auto-certificates/`` – reguła, liczby, „Przelicz teraz”, poniżej progu."""

    def get(self, request):
        edition = current_edition(request.competition)
        policy = self._policy(edition)
        form = AutoIssuePolicyForm(instance=policy, competition=request.competition, edition=edition)
        return self._render(request, edition, policy, form)

    def post(self, request):
        edition = current_edition(request.competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – reguła nie ma do czego się odnosić.")
            return redirect(reverse("web:coordinator-workshop-auto-certificates"))
        if request.POST.get("action") == "recalculate":
            return self._recalculate(request, edition)
        policy = self._policy(edition)
        before = _snapshot(policy)
        form = AutoIssuePolicyForm(
            request.POST, instance=policy, competition=request.competition, edition=edition
        )
        if not form.is_valid():
            # Liczby liczymy z reguły **zapisanej**, a nie z tej, którą formularz właśnie odrzucił.
            return self._render(request, edition, self._policy(edition), form, status=400)
        policy = form.save(commit=False)
        policy.edition = edition
        policy.updated_at = timezone.now()
        policy.updated_by = request.user
        policy.save()
        after = _snapshot(policy)
        changed = {name: after[name] for name in AUDITED_FIELDS if before[name] != after[name]}
        if changed:
            audit(request.user, "workshop_certificates.policy_updated", policy, changed, request=request)
        messages.success(
            request,
            "Zapisano regułę."
            + (
                " Osoby, które już spełniają próg, dostaną zaświadczenie po kliknięciu „Przelicz teraz”"
                " albo przy najbliższym zapisie ich obecności."
                if policy.enabled
                else ""
            ),
        )
        return redirect(reverse("web:coordinator-workshop-auto-certificates"))

    def _recalculate(self, request, edition):
        from apps.workshop_certificates.tasks import recalculate_edition

        policy = AutoIssuePolicy.objects.filter(edition=edition).first()
        if policy is None or not policy.enabled:
            messages.error(request, "Reguła jest wyłączona – włącz ją i zapisz, zanim przeliczysz.")
            return redirect(reverse("web:coordinator-workshop-auto-certificates"))
        # ``cache.add`` jest atomowe: z dwóch równoczesnych kliknięć zleca tylko pierwsze.
        if not cache.add(f"workshop-certificates:recalculate:{edition.pk}", 1, RECALCULATE_COOLDOWN_SECONDS):
            messages.info(
                request,
                "Przeliczanie tej edycji zlecono przed chwilą – odśwież stronę za kilkanaście sekund.",
            )
            return redirect(reverse("web:coordinator-workshop-auto-certificates"))
        audit(request.user, "workshop_certificates.recalculate_requested", edition, {}, request=request)
        actor_id = request.user.pk
        transaction.on_commit(lambda: recalculate_edition.delay(edition.pk, actor_id), robust=True)
        messages.success(
            request,
            "Zlecono przeliczenie. Zaświadczenia pojawią się w panelach uczestników w ciągu kilku chwil"
            " – odśwież stronę.",
        )
        return redirect(reverse("web:coordinator-workshop-auto-certificates"))

    @staticmethod
    def _policy(edition) -> AutoIssuePolicy:
        """Reguła edycji albo niezapisany wiersz z wartościami domyślnymi (brak wiersza = wyłączone)."""
        if edition is None:
            return AutoIssuePolicy()
        return AutoIssuePolicy.objects.filter(edition=edition).first() or AutoIssuePolicy(edition=edition)

    def _render(self, request, edition, policy, form, *, status: int = 200):
        context = {"edition": edition, "policy": policy, "form": form}
        if edition is not None:
            threshold = policy.min_workshops or 1
            evaluation = evaluate(edition, threshold)
            context.update(
                {
                    "evaluation": evaluation,
                    "threshold": threshold,
                    "auto_issued": AutoIssuedCertificate.objects.filter(edition=edition).count(),
                    "issued_total": Certificate.objects.filter(
                        edition=edition, kind=CertificateKind.WARSZTATY
                    ).count(),
                    "due_count": len(evaluation.due),
                    "below": evaluation.below[:PENDING_LIMIT],
                    "below_count": len(evaluation.below),
                    "below_limit": PENDING_LIMIT,
                    "threshold_unreachable": evaluation.workshops and threshold > evaluation.workshops,
                }
            )
        return TemplateResponse(request, TEMPLATE, context, status=status)
