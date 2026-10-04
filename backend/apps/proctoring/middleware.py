"""Bramka treści etapu z nadzorem (``docs/tasks/PROC-01.md`` § 5).

Dlaczego warstwa ``process_view``, a nie wywołanie w każdym widoku: treść etapu wychodzi pięcioma
drogami (PDF treści zadania, wysyłka rozwiązania przez WWW i przez API, start testu i strona
podejścia) w trzech aplikacjach, które o nadzorze nic nie wiedzą. Jedna warstwa z zamkniętą listą
nazw adresów nie zmienia żadnej z nich, a test kontraktu (``tests/test_gate.py``) pilnuje, że każda
nazwa z listy istnieje – zmiana nazwy adresu bez poprawki tutaj wywróci test, a nie bramkę po cichu.

Koszt dla reszty serwisu: porównanie nazwy adresu ze zbiorem. Dla adresów z listy w konkursie bez
flagi ``proctoring`` – odczyt pola konkursu, zero zapytań. Autozapisu testu bramka nie dotyka nigdy:
odpowiedzi ucznia nie mogą zginąć przez zerwany strumień.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _

#: Nazwa adresu → jak dojść do etapu z argumentów adresu.
GATED_VIEWS = {
    "competitions:problem-statement": "problem",
    "web:problem-upload": "stage",
    "submissions:submission-create": "stage",
    "web:quiz-start": "stage",
    "web:quiz-attempt": "attempt",
}


def _stage(kind: str, kwargs: dict, competition):
    from apps.competitions.models import Problem, Stage

    if kind == "stage":
        return Stage.objects.for_competition(competition).filter(pk=kwargs.get("stage_id")).first()
    if kind == "problem":
        problem = (
            Problem.objects.for_competition(competition)
            .select_related("stage")
            .filter(pk=kwargs.get("pk"))
            .first()
        )
        return problem.stage if problem is not None else None
    if kind == "attempt":
        from apps.quiz.models import QuizAttempt

        attempt = (
            QuizAttempt.objects.select_related("quiz__stage").filter(pk=kwargs.get("attempt_id")).first()
        )
        stage = attempt.quiz.stage if attempt is not None else None
        if stage is not None and stage.edition.competition_id != competition.pk:
            return None
        return stage
    return None


def _token_user(request):
    """Konto z tokenu API (DRF) – warstwa Django widzi wyłącznie sesję, a wysyłka przez API idzie
    tokenem. Bez tego klient API omijałby bramkę. Zły token = brak konta: odmowę wyda sam widok."""
    from django.utils.module_loading import import_string
    from rest_framework.exceptions import APIException

    if not request.headers.get("Authorization"):
        return None
    from django.conf import settings

    for path in settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", []):
        if path.endswith("SessionAuthentication"):
            continue
        try:
            result = import_string(path)().authenticate(request)
        except APIException:
            return None
        if result is not None:
            return result[0]
    return None


class ProctoringGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        kind = GATED_VIEWS.get(getattr(match, "view_name", "") or "")
        if kind is None:
            return None
        competition = getattr(request, "competition", None)
        from . import services

        if not services.enabled(competition):
            return None
        user = getattr(request, "user", None)
        if (user is None or not user.is_authenticated) and "/api/" in request.path:
            user = _token_user(request)
        if user is None or not user.is_authenticated:
            return None
        stage = _stage(kind, view_kwargs, competition)
        if stage is None:
            return None  # widok sam odpowie 404
        if not services.gate_blocks(user, stage, competition):
            return None
        from django.urls import reverse

        target = reverse("web:proctoring-console", args=[stage.pk])
        message = _("Ten etap jest nadzorowany zdalnie. Najpierw włącz nadzór w konsoli nadzoru.")
        accept = request.headers.get("Accept", "")
        # Przeglądarka otwierająca PDF albo stronę testu dostaje przekierowanie do konsoli (adres PDF
        # leży pod ``/api/``, ale klika go człowiek); klient API i HTMX – odmowę, którą umie pokazać.
        if request.method == "GET" and not request.headers.get("HX-Request") and "text/html" in accept:
            messages.info(request, message)
            return redirect(target)
        if "/api/" in request.path or "application/json" in accept:
            return JsonResponse(
                {"code": "PROCTORING_REQUIRED", "detail": message, "console": target}, status=403
            )
        return HttpResponseForbidden(message)
