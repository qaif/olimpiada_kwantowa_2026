"""``POST /djcms/sso/`` – logowanie redaktora tokenem z ``/cms/`` aplikacji głównej (DJ-02 D6, S11).

Kolejność sprawdzeń (każda porażka = 403 z jednym zdaniem i **bez** logowania; powód – w logu,
bez tokenu):

1. metoda ``POST`` (``GET`` → 405) – token nie może przyjść w adresie,
2. ``Origin`` równy ``<schemat>://<host>`` żądania – formularz wysłała strona tego samego hosta
   (``/cms/`` aplikacji głównej), a nie obca strona. To jest ochrona przed CSRF logowania: widok
   jest ``csrf_exempt``, bo formularz wystawia aplikacja główna, która nie zna tokenu CSRF djcms,
3. :func:`apps.sites.sso.verify_token` – podpis, wersja, odbiorca, host, czas,
4. jednorazowość (``SsoNonce``),
5. konto i grupy z tokenu (:func:`apps.sites.sso.provision_user`) – **także** gdy dalej odmówimy:
   utrata uprawnień w aplikacji głównej ma tu zadziałać przy pierwszej okazji,
6. konto zablokowane w djcms albo token bez witryny tego żądania → 403, bez logowania.

Po sukcesie: nowa sesja (``login`` zmienia klucz sesji) z terminem :func:`apps.sites.sso.session_seconds`
i przekierowanie na drzewo stron witryny żądania (pod prefiksem konkursu, jeśli jest).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import sso
from .resolution import normalise_host

logger = logging.getLogger(__name__)

REFUSED_TEMPLATE = "dj_sites/sso_refused.html"

MESSAGES = {
    "invalid": "Link logowania jest nieważny albo został już użyty. Wróć do panelu /cms/ i spróbuj ponownie.",
    "forbidden": "Twoje konto nie redaguje tej witryny. Uprawnienia nadaje się w panelu /cms/.",
    "blocked": "Konto w django CMS jest zablokowane. Skontaktuj się z administratorem serwisu.",
}


def _refuse(request, kind: str, reason: str):
    logger.warning("SSO djcms odrzucone (%s): %s, host %s", kind, reason, normalise_host(request.get_host()))
    return render(request, REFUSED_TEMPLATE, {"message": MESSAGES[kind]}, status=403)


def _origin_matches(request) -> bool:
    origin = request.META.get("HTTP_ORIGIN", "")
    return bool(origin) and origin.lower() == f"{request.scheme}://{request.get_host()}".lower()


@csrf_exempt
@never_cache
@require_POST
def sso_login(request):
    if not _origin_matches(request):
        return _refuse(request, "invalid", "nagłówek Origin spoza hosta")
    competition = getattr(request, "competition_site", None)
    if competition is None:  # pragma: no cover - warstwa witryny odpowiada 404 wcześniej
        return _refuse(request, "invalid", "brak konkursu żądania")
    try:
        claims = sso.verify_token(request.POST.get("token", ""), host=normalise_host(request.get_host()))
        with transaction.atomic():
            sso.consume_nonce(claims)
            user = sso.provision_user(claims)
    except sso.SsoError as exc:
        return _refuse(request, "invalid", exc.reason)
    if not user.is_active:
        return _refuse(request, "blocked", f"konto {user.username} zablokowane")
    if not claims.allows(competition.slug):
        return _refuse(request, "forbidden", f"konto {user.username} bez konkursu {competition.slug}")
    sso.start_session(request, user)
    logger.info("SSO djcms: %s zalogowany (konkurs %s)", user.username, competition.slug)
    return HttpResponseRedirect(reverse("admin:cms_pagecontent_changelist"))
