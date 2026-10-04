"""Ekrany przeglądu tłumaczeń (L10N-01): panel tłumacza, zgłoszenie ze stopki, role w panelu koordynatora.

Panel tłumacza nie jest częścią panelu koordynatora ani uczestnika: tłumaczem bywa kierownik
delegacji, opiekun albo wolontariusz bez żadnej roli w konkursie, a napisy są wspólne dla całej
platformy. Dostęp rozstrzyga ``services`` (``can_translate``/``can_review``) – widok tylko pyta.

Wszystkie POST-y przechodzą przez ``ThrottledFormMixin`` ze scope'em ``translations`` liczonym
**per konto** (jak czat i forum): pracujący tłumacz klika szybko, ale nie sto razy na godzinę,
a delegacja siedząca za jednym adresem IP nie może dzielić jednego budżetu.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views import View

from apps.accounts.super_coordinator import is_super_coordinator
from apps.core.api import DomainError
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin, user_throttle_keys

from . import catalogs, services
from .forms import FilterForm, GrantForm, ReportForm, SuggestionForm
from .models import ReportStatus, SuggestionStatus, TranslationReport, TranslationSuggestion

PAGE_SIZE = 50


class TranslationThrottleMixin(ThrottledFormMixin):
    throttle_scope = "translations"

    def get_throttle_keys(self, request) -> list[str]:
        return user_throttle_keys(self.throttle_scope, request)


class LanguageMixin(LoginRequiredMixin):
    """Język z adresu: nieznany → 404, bez uprawnienia → 403."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.language = kwargs["language"]
        if self.language not in catalogs.review_languages():
            raise Http404("Nieznany język.")
        if not services.can_translate(request.user, self.language):
            raise PermissionDenied(_("Nie masz uprawnień do tłumaczeń tego języka."))
        self.can_review = services.can_review(request.user, self.language)
        return super().dispatch(request, *args, **kwargs)

    def base_context(self) -> dict:
        return {
            "language": self.language,
            "language_label": catalogs.language_label(self.language),
            "can_review": self.can_review,
            "show_pivot": self.language != "en",
        }


class HomeView(LoginRequiredMixin, View):
    """``/translations/`` – języki tej osoby z licznikami pracy."""

    def get(self, request):
        languages = services.languages_for(request.user)
        if not languages:
            raise PermissionDenied(_("Twoje konto nie ma roli tłumacza."))
        rows = []
        for code in languages:
            rows.append(
                {
                    "code": code,
                    "label": catalogs.language_label(code),
                    "can_review": services.can_review(request.user, code),
                    "pending": TranslationSuggestion.objects.filter(
                        language=code, status=SuggestionStatus.PENDING
                    ).count(),
                    "reports": TranslationReport.objects.filter(
                        language=code, status=ReportStatus.OPEN
                    ).count(),
                }
            )
        return TemplateResponse(request, "translation_review/home.html", {"languages": rows})


class StringListView(LanguageMixin, View):
    """``/translations/<język>/`` – lista napisów z filtrem i wyszukiwaniem."""

    def get(self, request, language):
        form = FilterForm(request.GET or None)
        status, query = "", ""
        if form.is_valid():
            status, query = form.cleaned_data["status"], form.cleaned_data["q"]
        items = services.strings(language, status=status, query=query)
        page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page"))
        params = {key: value for key, value in (("status", status), ("q", query)) if value}
        context = {
            **self.base_context(),
            "form": form,
            "page_obj": page,
            "total": len(items),
            "filter_query": urlencode(params),
        }
        return TemplateResponse(request, "translation_review/list.html", context)


class StringDetailView(LanguageMixin, TranslationThrottleMixin, View):
    """``/translations/<język>/<klucz>/`` – napis, kontekst, propozycje, głosy i decyzje."""

    def _context(self, key, form=None) -> dict:
        view = services.string_view(self.language, key)
        suggestions = list(services.suggestions_for(self.language, key))
        voted = set(
            self.request.user.translation_votes.filter(suggestion__in=suggestions).values_list(
                "suggestion_id", flat=True
            )
        )
        return {
            **self.base_context(),
            "item": view,
            "suggestions": suggestions,
            "voted": voted,
            "form": form or SuggestionForm(),
        }

    def get(self, request, language, key):
        return TemplateResponse(request, "translation_review/detail.html", self._context(key))

    def post(self, request, language, key):
        services.row_or_404(language, key)
        action = request.POST.get("action", "suggest")
        user = request.user
        try:
            if action == "suggest":
                form = SuggestionForm(request.POST)
                if not form.is_valid():
                    return TemplateResponse(
                        request, "translation_review/detail.html", self._context(key, form), status=400
                    )
                services.suggest(
                    user=user,
                    language=language,
                    key=key,
                    text=form.cleaned_data["text"],
                    approve_now=form.cleaned_data["approve"],
                    request=request,
                )
                messages.success(request, _("Propozycja została zapisana."))
            elif action in ("vote", "approve", "reject"):
                suggestion = get_object_or_404(
                    TranslationSuggestion, pk=_pk(request.POST.get("suggestion")), language=language, key=key
                )
                if action == "vote":
                    services.vote(user=user, suggestion=suggestion, request=request)
                elif action == "approve":
                    services.approve(user=user, suggestion=suggestion, request=request)
                    messages.success(
                        request, _("Tłumaczenie zatwierdzone – działa w serwisie w ciągu kilku sekund.")
                    )
                else:
                    services.reject(user=user, suggestion=suggestion, request=request)
                    messages.success(request, _("Propozycja została odrzucona."))
            elif action == "confirm":
                services.confirm(user=user, language=language, key=key, request=request)
                messages.success(request, _("Tłumaczenie oznaczone jako przejrzane."))
            elif action == "revert":
                services.revert(user=user, language=language, key=key, request=request)
                messages.success(request, _("Przywrócono tłumaczenie z katalogu."))
            else:
                raise Http404("Nieznana czynność.")
        except DomainError as exc:
            if action == "suggest":
                form = SuggestionForm(request.POST)
                form.is_valid()
                form.add_error("text", str(exc.detail))
                return TemplateResponse(
                    request,
                    "translation_review/detail.html",
                    self._context(key, form),
                    status=exc.status_code,
                )
            messages.error(request, str(exc.detail))
        return redirect(reverse("web:translation-string", args=[language, key]))


class ReportsView(LanguageMixin, TranslationThrottleMixin, View):
    """``/translations/<język>/reports/`` – zgłoszenia ze stopki; zamyka recenzent."""

    def get(self, request, language):
        if not self.can_review:
            raise PermissionDenied(_("Zgłoszenia przegląda recenzent tłumaczeń."))
        context = {**self.base_context(), "reports": services.reports_for(language)}
        return TemplateResponse(request, "translation_review/reports.html", context)

    def post(self, request, language):
        report = get_object_or_404(TranslationReport, pk=_pk(request.POST.get("report")), language=language)
        try:
            services.close_report(user=request.user, report_obj=report, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Zgłoszenie zamknięte."))
        return redirect(reverse("web:translation-reports", args=[language]))


class ReportView(LoginRequiredMixin, TranslationThrottleMixin, View):
    """``/translations/report/?page=<ścieżka>`` – „Zgłoś tłumaczenie” ze stopki."""

    def _languages(self, request):
        languages = services.languages_for(request.user)
        if not languages:
            raise PermissionDenied(_("Twoje konto nie ma roli tłumacza."))
        return languages

    def get(self, request):
        languages = self._languages(request)
        current = get_language()
        initial = {
            "language": current if current in languages else languages[0],
            "page": services.clean_page(request.GET.get("page", "")),
        }
        form = ReportForm(initial=initial, languages=languages)
        return TemplateResponse(
            request, "translation_review/report.html", {"form": form, "page": initial["page"]}
        )

    def post(self, request):
        languages = self._languages(request)
        form = ReportForm(request.POST, languages=languages)
        page = services.clean_page(request.POST.get("page", ""))
        if form.is_valid():
            try:
                services.report(
                    user=request.user,
                    language=form.cleaned_data["language"],
                    page=page,
                    phrase=form.cleaned_data["phrase"],
                    comment=form.cleaned_data["comment"],
                    request=request,
                )
            except DomainError as exc:
                form.add_error(None, str(exc.detail))
            else:
                messages.success(request, _("Dziękujemy – recenzent tłumaczeń zobaczy zgłoszenie."))
                # Powrót na stronę, z której przyszło zgłoszenie. ``clean_page`` przepuszcza wyłącznie
                # ścieżkę w serwisie; sprawdzenie Django jest drugim, niezależnym zamkiem.
                safe = url_has_allowed_host_and_scheme(page, allowed_hosts={request.get_host()})
                return redirect(page if page and safe else reverse("web:translations"))
        return TemplateResponse(
            request, "translation_review/report.html", {"form": form, "page": page}, status=400
        )


class CoordinatorTranslatorsView(CoordinatorRequiredMixin, TranslationThrottleMixin, View):
    """``/coordinator/translators/`` – nadawanie i odbieranie roli tłumacza (L10N-01 § 2)."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not services.can_manage_grants(
            request.user, getattr(request, "competition", None)
        ):
            # Konkurs z jednym językiem interfejsu nie ma czego tłumaczyć – ekranu tu nie ma.
            raise Http404("Ten konkurs ma jeden język interfejsu.")
        return super().dispatch(request, *args, **kwargs)

    def _render(self, request, form, status=200):
        superuser = is_super_coordinator(request.user)
        context = {
            "form": form,
            "grants": services.grants_visible_to(request.user, self.competition),
            "superuser": superuser,
        }
        return TemplateResponse(
            request, "translation_review/coordinator_translators.html", context, status=status
        )

    def get(self, request):
        return self._render(request, GrantForm(reviewer_allowed=is_super_coordinator(request.user)))

    def post(self, request):
        superuser = is_super_coordinator(request.user)
        if request.POST.get("action") == "revoke":
            grant = get_object_or_404(
                services.grants_visible_to(request.user, self.competition), pk=_pk(request.POST.get("grant"))
            )
            try:
                services.revoke(
                    actor=request.user, competition=self.competition, grant_obj=grant, request=request
                )
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, _("Rola tłumacza została odebrana."))
            return redirect(reverse("web:coordinator-translators"))
        form = GrantForm(request.POST, reviewer_allowed=superuser)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            services.grant(
                actor=request.user,
                competition=self.competition,
                email=form.cleaned_data["email"],
                language=form.cleaned_data["language"],
                level=form.cleaned_data["level"],
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        messages.success(request, _("Rola tłumacza została nadana."))
        return redirect(reverse("web:coordinator-translators"))


def _pk(raw) -> int:
    try:
        return int(raw)
    except TypeError, ValueError:
        raise Http404("Nieprawidłowy identyfikator.") from None
