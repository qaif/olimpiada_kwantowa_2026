"""Wewnętrzne API dla serwisu publicznego na django CMS (``djcms``) – wersja 2, per konkurs.

Kontrakt: ``docs/tasks/DJ-02.md`` § 4 (``docs/API.md`` § 8). Zwykłe widoki Django (nie DRF – schemat
OpenAPI aplikacji ma się od tego nie zmienić), wyłącznie ``GET``, za bramką hosta i tokenu
(``auth``). Dane liczą te same funkcje, które karmią strony Wagtaila (``apps.cms.live_data``,
``apps.cms.timeline`` i reszta), a ``serializers`` zamienia je na JSON z gotowymi napisami – szablony
djcms nie formatują danych zawodów samodzielnie, więc termin nie może mieć tam innego brzmienia niż
na stronie głównej. Wersja 1 (DJ-01, jeden konkurs) usunięta w DJ-02k.
"""
