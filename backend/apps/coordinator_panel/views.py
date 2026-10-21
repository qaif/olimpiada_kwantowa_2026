"""Widoki panelu koordynatora (UX-PANEL-04): przypinanie pozycji menu.

Start nie ma własnego widoku – to dotychczasowy pulpit ``/coordinator/`` z kontekstem z ``start.py``
(UX-PANEL-04 D4). Tu jest wyłącznie czynność przypinania: POST z formularza w nagłówku ekranu albo z kafla
„Przypięte” na Starcie, bez JavaScriptu.

Przypinanie jest **ustawieniem konta**, a nie czynnością w zawodach – nie trafia do audytu (``AuditLog``
opisuje decyzje o danych i zawodach; dziennik „kto przypiął sobie Lejek” byłby szumem w przeglądarce audytu).
Limit żądań jest per konto (kubełek ``anon``, 60/min), jak oznaczanie powiadomień w panelach uczestnika
i komisji: zapis dotyczy wyłącznie własnych wierszy.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import get_script_prefix, reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import PerAccountThrottleMixin

from . import services


def safe_next(request) -> str:
    """Powrót po zapisie: ścieżka panelu koordynatora **tego** konkursu albo Start.

    ``next`` przychodzi z formularza, więc jest daną wejściową: wolno wyłącznie ścieżkę względną zaczynającą
    się prefiksem konkursu i ``coordinator/`` – bez schematu, hosta i „//”, żeby formularz nie stał się
    przekierowaniem na cudzy adres.
    """
    raw = (request.POST.get("next") or "").strip()
    panel = f"{get_script_prefix()}coordinator/"
    if raw.startswith(panel) and "//" not in raw and "\\" not in raw and "\n" not in raw and "\r" not in raw:
        return raw
    return reverse("web:coordinator")


class PinToggleView(CoordinatorRequiredMixin, PerAccountThrottleMixin, View):
    """``POST /coordinator/pins/`` – przypnij albo odepnij pozycję menu (pole ``path``)."""

    throttle_scope = "anon"

    def post(self, request):
        try:
            pinned = services.toggle_pin(request, request.POST.get("path", ""))
        except services.PinLimitReached:
            messages.error(
                request,
                _("Masz już %(limit)s przypiętych pozycji – odepnij którąś, żeby dodać nową.")
                % {"limit": services.shell.MAX_PINS},
            )
        except services.NotAMenuItem:
            messages.error(request, _("Przypiąć można wyłącznie pozycję menu panelu."))
        else:
            if pinned:
                messages.success(request, _("Przypięto. Pozycja stoi teraz nad sekcjami menu i na Starcie."))
            else:
                messages.success(request, _("Odpięto pozycję."))
        return HttpResponseRedirect(safe_next(request))
