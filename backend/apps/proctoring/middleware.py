"""Bramka treści etapu z nadzorem (``docs/tasks/PROC-01.md`` § 5).

Dlaczego warstwa ``process_view``, a nie wywołanie w każdym widoku: treść etapu wychodzi pięcioma
drogami (PDF treści zadania, wysyłka rozwiązania przez WWW i przez API, start testu i strona
podejścia) w trzech aplikacjach, które o nadzorze nic nie wiedzą. Jedna warstwa z zamkniętą listą
nazw adresów nie zmienia żadnej z nich, a test kontraktu (``tests/test_gate.py``) pilnuje, że każda
nazwa z listy istnieje – zmiana nazwy adresu bez poprawki tutaj wywróci test, a nie bramkę po cichu.

W oknie etapu z nadzorem treść widzą wyłącznie: uczniowie etapu z gotową sesją oraz personel
(koordynator, komisja). Niezalogowany – logowanie (przeglądarka) albo 403; zalogowany bez
zgłoszenia – 403. Konto liczy się w kolejności DRF (:func:`request_user`), a serwisy wysyłki i startu
testu powtarzają regułę ucznia (``services.assert_stage_access``) – druga linia obrony.

Koszt dla reszty serwisu: porównanie nazwy adresu ze zbiorem. Dla adresów z listy w konkursie bez
flagi ``proctoring`` – odczyt pola konkursu, zero zapytań. Autozapisu testu bramka nie dotyka nigdy:
odpowiedzi ucznia nie mogą zginąć przez zerwany strumień.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _

#: Nazwa adresu → jak dojść do etapu z argumentów adresu. Każdy adres, który oddaje **treść zadań**
#: etapu albo przyjmuje pracę, musi tu stać – pilnuje tego ``tests/test_gate.py`` (przegląd wszystkich
#: nazw adresów). Tłumaczenia zadań dla ucznia (TR-01, ``apps.problem_translations``) stoją tu zawczasu:
#: nazwa bez adresu w mapie nigdy nie trafi, a po scaleniu TR-01 bramka obejmie ją sama.
GATED_VIEWS = {
    "competitions:problem-statement": "problem",
    "web:problem-upload": "stage",
    "submissions:submission-create": "stage",
    "web:quiz-start": "stage",
    "web:quiz-attempt": "attempt",
    "web:student-translation": "problem",
    "web:student-translation-file": "problem",
    # Strona laboratorium notatnika przy zadaniu (QC-01) – ``pk`` to zadanie. Sam notatnik startowy
    # (``web:participant-notebook-starter``, adres z tokenem bez zadania w ścieżce) bramkuje widok.
    "web:participant-notebook": "problem",
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
            QuizAttempt.objects.select_related("quiz__stage__edition")
            .filter(pk=kwargs.get("attempt_id"))
            .first()
        )
        stage = attempt.quiz.stage if attempt is not None else None
        if stage is not None and stage.edition.competition_id != competition.pk:
            return None
        return stage
    return None


def request_user(request):
    """Konto, które **zobaczy widok** – w kolejności DRF, a nie Django.

    DRF uwierzytelnia klasami z ``DEFAULT_AUTHENTICATION_CLASSES`` po kolei: najpierw token, potem
    sesja. Warstwa Django widzi wyłącznie sesję – gdyby bramka pytała tylko ``request.user``,
    wystarczyłaby sesja dowolnego konta bez zgłoszenia (personelu, ucznia innego etapu) i własny
    token ucznia w nagłówku: bramka przepuściłaby sesję, a widok przyjąłby pracę od właściciela
    tokenu. Dlatego przy nagłówku ``Authorization`` pod ``/api/`` liczy się token: poprawny – to
    jego właściciel jest sprawdzany; zły – ``None`` (widok i tak odpowie 401); nieobsługiwany
    schemat – sesja, dokładnie jak w DRF.
    """
    user = getattr(request, "user", None)
    if not request.headers.get("Authorization") or "/api/" not in request.path:
        return user
    from django.conf import settings
    from django.utils.module_loading import import_string
    from rest_framework.exceptions import APIException

    for path in settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", []):
        if path.endswith("SessionAuthentication"):
            break  # sesja – ``request.user`` niżej
        try:
            result = import_string(path)().authenticate(request)
        except APIException:
            return None
        if result is not None:
            return result[0]
    return user


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
        user = request_user(request)
        stage = _stage(kind, view_kwargs, competition)
        if stage is None:
            return None  # widok sam odpowie 404
        decision = services.gate_decision(user, stage, competition)
        if decision is None:
            return None
        from django.urls import reverse

        accept = request.headers.get("Accept", "")
        if decision != services.GATE_CONSOLE:
            message = _("Treść tego etapu jest w trakcie zawodów dostępna wyłącznie dla jego uczestników.")
            if decision == services.GATE_LOGIN and request.method == "GET" and "text/html" in accept:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if "/api/" in request.path or "application/json" in accept:
                return JsonResponse({"code": "PROCTORING_STAGE_CLOSED", "detail": message}, status=403)
            return HttpResponseForbidden(message)
        target = reverse("web:proctoring-console", args=[stage.pk])
        message = _("Ten etap jest nadzorowany zdalnie. Najpierw włącz nadzór w konsoli nadzoru.")
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
