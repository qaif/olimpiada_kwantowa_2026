"""Adresy wewnętrzne platformy — montowane pod ``internal/`` w ``config/urls.py``.

Osobny moduł z tego samego powodu, co ``apps/tenancy/setup_urls.py``: ``config/urls.py`` ma jedną
linijkę na gałąź, a co w gałęzi stoi, wie aplikacja. Dziś są tu dwie gałęzie:

- ``tls-allowed`` – pytanie Caddy'ego o certyfikat konkursu w subdomenie (niżej),
- ``djcms/v1/…`` – dane zawodów i ramy serwisu dla wersji porównawczej na django CMS
  (``dj.<SITE_DOMAIN>``, ``docs/tasks/DJ-01.md`` § 3). Tylko ``GET``, za bramką hosta **i** tokenu
  (``apps.cms.djcms_api.auth``); każda porażka bramki to pusta 404, tak samo jak tutaj.

**Adres ``tls-allowed`` jest bez ukośnika na końcu i to jest kontrakt**, a nie przeoczenie: pyta
pod niego Caddy (``on_demand_tls { ask http://web:8000/internal/tls-allowed }``), a dyrektywa
``ask`` dokleja do podanego adresu ``?domain=<host>`` i nie podąża za przekierowaniami. Adres
z ukośnikiem nie pasuje do żadnego wzorca i kończy się 404 na drzewie stron — tak samo, jak każdy
inny nieistniejący adres.

Gałąź jest wyjęta z rozstrzygania konkursu (``apps/tenancy/middleware.py``,
``INTERNAL_URL_PREFIX``), a segment ``internal`` należy do ``apps.cms.models.RESERVED_SLUGS``, więc
strona CMS o takim adresie nie powstanie i nie przykryje tego wzorca.
"""

from __future__ import annotations

from django.urls import include, path

from apps.tenancy.internal_views import tls_allowed

urlpatterns = [
    path("tls-allowed", tls_allowed, name="tls-allowed"),
    path("djcms/v1/", include("apps.cms.djcms_api.urls")),
]
