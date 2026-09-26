"""Adresy wewnętrzne platformy — montowane pod ``internal/`` w ``config/urls.py``.

Osobny moduł z tego samego powodu, co ``apps/tenancy/setup_urls.py``: ``config/urls.py`` ma jedną
linijkę na gałąź, a co w gałęzi stoi, wie aplikacja. Dziś są tu dwie gałęzie:

- ``tls-allowed`` – pytanie Caddy'ego o certyfikat konkursu w subdomenie (niżej),
- ``djcms/v1/…`` i ``djcms/v2/…`` – dane zawodów i ramy serwisu dla serwisu na django CMS
  (v1: jeden konkurs, ``docs/tasks/DJ-01.md`` § 3; v2: każdy konkurs, ``docs/tasks/DJ-02.md`` § 4).
  Tylko ``GET``, za bramką hosta **i** tokenu (``apps.cms.djcms_api.auth``); każda porażka bramki
  to pusta 404, tak samo jak tutaj.

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

from django.urls import include, path, re_path

from apps.cms.djcms_api.views import not_found as djcms_not_found
from apps.tenancy.internal_views import tls_allowed

urlpatterns = [
    path("tls-allowed", tls_allowed, name="tls-allowed"),
    path("djcms/v1/", include("apps.cms.djcms_api.urls")),
    # v2 – per konkurs (``competitions``, ``c/<slug>/…``; DJ-02 § 4). v1 zostaje do DJ-02k.
    path("djcms/v2/", include("apps.cms.djcms_api.urls_v2")),
    # Reszta gałęzi ``djcms`` (``/internal/djcms``, ``…/v1``/``…/v2`` bez ukośnika, ``…/v3/…``): ta
    # sama pusta 404, co pod ``v1/`` – inaczej 301 z ``APPEND_SLASH`` zdradzałby, że gałąź istnieje.
    re_path(r"^djcms(?:/.*)?$", djcms_not_found),
]
