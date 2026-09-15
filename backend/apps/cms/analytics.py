"""Czy serwis ma włączoną analitykę – odczyt na tyle tani, żeby wołać go z middleware'u CSP.

Nagłówek ``Content-Security-Policy`` powstaje dla **każdej** odpowiedzi, także dla plików
statycznych oddawanych przez WhiteNoise (patrz ``apps/web/middleware.py``). Zapytanie do bazy
w tym miejscu kosztowałoby jeden ``SELECT`` na każdy arkusz, skrypt i obrazek – dlatego wynik
mieszka w pamięci procesu z krótkim czasem życia.

Dlaczego pytamy o ``exists()``, a nie o samą wartość: w polityce nie ma identyfikatora, jest
wyłącznie host ``googletagmanager.com``. Rozstrzygnięcie jest więc binarne, a wartość dla szablonu
i tak przychodzi z ``settings.cms.SiteSettings`` (kontekst Wagtaila, jedno zapytanie na żądanie
i to samo, którym szablon składa stopkę).

Dlaczego pytanie nie jest zawężone do witryny z żądania: ``Site.find_for_request`` to kolejne
zapytanie, a dyrektywa CSP jest **pozwoleniem**, nie poleceniem – witryna bez identyfikatora
nie wczyta żadnego skryptu Google'a niezależnie od tego, czy host stoi na liście. Instalacja,
w której nikt nie wpisał identyfikatora, ma nagłówek co do bajtu taki, jak przed dodaniem GA.

Świeżość: zapis ustawienia w ``/cms/`` czyści pamięć od razu (sygnał ``post_save``), ale tylko
w tym procesie – pozostałe workery i tak odczytają wartość po upływie ``CACHE_TTL_SECONDS``.
Włączenie analityki „widać” więc najpóźniej po minucie, co dla jednorazowej decyzji organizatora
jest ceną bez znaczenia.
"""

from __future__ import annotations

import time

from django.db import DatabaseError
from django.db.models.signals import post_save
from django.dispatch import receiver

#: Czas życia pamięci podręcznej. Krótki, bo jedynym kosztem jest jedno zapytanie na pół minuty
#: na proces, a zyskiem – to, że redaktor nie musi czekać na restart aplikacji.
CACHE_TTL_SECONDS = 30

#: ``(monotoniczny znacznik czasu, wynik)`` albo ``None``. Moduł, a nie ``django.core.cache``:
#: pamięć współdzielona (Redis) byłaby tu drugim wywołaniem sieciowym zamiast zapytania do bazy.
_cache: tuple[float, bool] | None = None


def analytics_enabled() -> bool:
    """Czy którakolwiek witryna ma wpisany identyfikator GA4 (z pamięcią na ``CACHE_TTL_SECONDS``).

    Błąd bazy znaczy „nie” – tak samo, jak w ``site_chrome``. Nagłówek bezpieczeństwa nie może
    się wywrócić przez to, że tabela ustawień jeszcze nie istnieje (świeża baza przed migracjami),
    a domyślenie się „wyłączone” jest w polityce CSP stroną bezpieczną.
    """
    global _cache

    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]

    from apps.cms.models import SiteSettings

    try:
        enabled = SiteSettings.objects.exclude(ga_measurement_id="").exists()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli ustawień
        enabled = False
    _cache = (now, enabled)
    return enabled


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętaną odpowiedź. Woła to sygnał zapisu ustawień oraz testy."""
    global _cache

    _cache = None


@receiver(post_save, dispatch_uid="cms.analytics.reset_cache")
def _reset_on_settings_save(sender, **kwargs) -> None:
    """Zapis ``SiteSettings`` w ``/cms/`` ma być widoczny od razu, a nie po upływie TTL.

    Odbiornik jest podpięty pod **każdy** ``post_save`` i dopiero w środku sprawdza nadawcę:
    podpięcie go do konkretnego modelu wymagałoby zaimportowania ``apps.cms.models`` w chwili
    ładowania aplikacji, czyli zanim rejestr modeli jest gotowy.
    """
    if sender.__name__ == "SiteSettings" and sender._meta.app_label == "cms":
        reset_cache()
