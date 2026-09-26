"""Wewnętrzne API dla wersji porównawczej serwisu na django CMS (``dj.<SITE_DOMAIN>``).

Kontrakt: ``docs/tasks/DJ-01.md`` § 3. Zwykłe widoki Django (nie DRF – schemat OpenAPI aplikacji
ma się od tego nie zmienić), wyłącznie ``GET``, za bramką hosta i tokenu (``auth``). Dane liczą te
same funkcje, które karmią strony Wagtaila (``apps.cms.live_data``, ``apps.cms.timeline`` i reszta),
a ``serializers`` zamienia je na JSON z gotowymi napisami – szablony ``dj.`` nie formatują danych
zawodów samodzielnie, więc termin nie może mieć tam innego brzmienia niż na stronie głównej.
"""
