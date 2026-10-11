"""Adresy zmiany hasła (AUTH-01b) – rozwijane na **końcu** ``apps/web/urls.py`` (przestrzeń ``web``).

Pod ``/account/…``, obok zmiany adresu e-mail i 2FA: to ta sama rodzina ustawień konta, wspólna
dla wszystkich ról. Prefiks ``/account/`` **nie** jest na liście page cache'u
(``apps.web.page_cache.ALLOWED_PREFIXES``) ani na liście adresów dostępnych sesji czekającej na
drugi składnik (``apps.accounts.twofactor._allowed_prefixes``) – i nie ma prawa na nich stanąć.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("account/password/", views.PasswordChangeView.as_view(), name="password-change"),
    path("account/password/set-link/", views.PasswordSetLinkView.as_view(), name="password-set-link"),
]
