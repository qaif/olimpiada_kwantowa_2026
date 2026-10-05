"""Bramka „Uzupełnij zgody” (``docs/tasks/CONS-01.md`` § 2).

Dlaczego warstwa ``process_view``, a nie domieszka w widokach: obszar uczestnika to kilkadziesiąt
widoków w kilkunastu modułach (panel, upload, test, czat, nadzór, forum, notebooki, webinary,
płatności) i każdy nowy ekran pod ``/me/`` powinien wejść do bramki **sam**, a nie dlatego, że ktoś
pamiętał o domieszce. ``process_view``, bo potrzebna jest rozstrzygnięta trasa – po niej (a nie po
``request.path``) poznajemy obszar, więc prefiks ścieżki konkursu (``/druga/me/…``) niczego nie zmienia.

Obszar jest **listą członów trasy**, a wyjątki w nim – **listą dozwolonych nazw** (test kontraktu
w ``tests/test_gate.py`` pilnuje, że każda istnieje). Wszystko poza obszarem przechodzi: wylogowanie,
zmiana hasła i całe ``/account/…`` (eksport danych, usunięcie konta – prawa z RODO nie mogą zależeć
od zaakceptowania nowego regulaminu), strony publiczne z dokumentami, których zgody dotyczą, zgoda
opiekuna pod ``/zgoda/<token>/``, ekrany personelu.

Koszt: anonim, personel (``is_staff``/``is_superuser``) i adres spoza obszaru – zero zapytań. Uczestnik
– odczyt cache'a (``state.py``), przy chybieniu jedno zapytanie.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _

#: Pierwszy człon trasy → obszar uczestnika. ``me`` to panel ze wszystkim, co w nim jest (upload,
#: test, czat ``me/messages``, konsola nadzoru ``me/proctoring``, dyplomy, kalendarz, absolwenci);
#: pozostałe to ekrany uczestnika z własnym adresem w korzeniu.
GATED_SEGMENTS = frozenset({"me", "forum", "notebook-starter", "webinars", "warsztaty", "payments"})

#: Przestrzenie API, przez które uczestnik oddaje pracę, czyta treść zadań albo składa reklamację.
#: Pozostałe (``accounts`` – logowanie i opis zgód, ``schools``, ``results``, ``grading``,
#: ``integrations``) nie są czynnościami uczestnika w zawodach.
GATED_API_NAMESPACES = frozenset({"submissions", "competitions", "appeals"})

#: Wyjątki **w** obszarze. Każdy ma powód, którego nie da się obejść:
CONSENT_VIEW = "web:consent-complete"
ALLOWED_VIEWS = frozenset(
    {
        CONSENT_VIEW,
        # sprostowanie danych (art. 16 RODO); zmiana daty urodzenia zmienia też wymagane zgody,
        "web:profile",
        # istniejąca prośba do opiekuna („wyślij ponownie”) – formularz stoi na ekranie zgód,
        "web:guardian-request",
        # autozapis testu – odpowiedzi ucznia nie mogą zginąć przez zmianę wersji dokumentu w trakcie,
        "web:quiz-autosave",
        # klient nadzoru zdalnego – trwająca sesja nie może się zerwać z tego samego powodu,
        "web:proctoring-student-token",
        "web:proctoring-student-messages",
        "web:proctoring-student-action",
        # wypis z listów forum – link z listu, działa także bez logowania,
        "web:forum-unsubscribe",
    }
)


def enabled() -> bool:
    return bool(getattr(settings, "CONSENT_GATE_ENABLED", True))


def in_gated_area(match) -> bool:
    """Czy rozstrzygnięta trasa należy do obszaru uczestnika – bez zapytań, po samej trasie."""
    view_name = match.view_name or ""
    if view_name in ALLOWED_VIEWS:
        return False
    segment = (match.route or "").lstrip("^").split("/", 1)[0]
    if segment == "api":
        return match.namespace in GATED_API_NAMESPACES
    return segment in GATED_SEGMENTS


def _user_for(request, match):
    """Konto, które zobaczy widok. Pod API z tokenem – w kolejności DRF, jak w bramce nadzoru.

    Sama sesja nie wystarczy: uczestnik z tokenem API (``/api/auth/login/``) wysłałby rozwiązanie
    bez zgód, bo dla warstwy Django jest anonimem. Odczyt tokenu to jedno zapytanie – wyłącznie na
    adresach API z bramką i wyłącznie z nagłówkiem ``Authorization``.
    """
    if (match.route or "").startswith("api/") and request.headers.get("Authorization"):
        from apps.proctoring.middleware import request_user

        return request_user(request)
    return getattr(request, "user", None)


def consent_url(request) -> str:
    """Adres ekranu zgód z powrotem na bieżący adres (``next``) – tylko dla GET-a."""
    target = reverse(CONSENT_VIEW)
    if request.method == "GET":
        return f"{target}?{urlencode({'next': request.get_full_path()})}"
    return target


class ConsentGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not enabled():
            return None
        match = getattr(request, "resolver_match", None)
        if match is None or not in_gated_area(match):
            return None
        competition = getattr(request, "competition", None)
        if competition is None:
            return None
        user = _user_for(request, match)
        if user is None or not user.is_authenticated or user.is_staff or user.is_superuser:
            return None
        from .state import missing_consents

        missing = missing_consents(user, competition)
        if not missing:
            return None
        return self._stop(request)

    def _stop(self, request):
        """Każdy klient dostaje odpowiedź, którą rozumie: przeglądarka – ekran, HTMX i API – 403."""
        url = consent_url(request)
        detail = _("Zanim przejdziesz dalej, uzupełnij wymagane zgody.")
        if request.headers.get("HX-Request"):
            response = JsonResponse({"code": "CONSENTS_REQUIRED", "detail": detail, "url": url}, status=403)
            # HTMX przechodzi pod ``HX-Redirect`` całą stroną – zamiast wkleić odmowę w fragment.
            response["HX-Redirect"] = url
            return response
        accept = request.headers.get("Accept", "")
        if (request.resolver_match.route or "").startswith("api/") or "application/json" in accept:
            return JsonResponse({"code": "CONSENTS_REQUIRED", "detail": detail, "url": url}, status=403)
        return HttpResponseRedirect(url)
