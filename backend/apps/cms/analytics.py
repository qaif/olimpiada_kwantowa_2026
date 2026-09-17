"""Czy serwis ma włączoną analitykę – odczyt na tyle tani, żeby wołać go z middleware'u CSP.

Nagłówek ``Content-Security-Policy`` powstaje dla **każdej** odpowiedzi, także dla plików
statycznych oddawanych przez WhiteNoise (patrz ``apps/web/middleware.py``). Zapytanie do bazy
w tym miejscu kosztowałoby jeden ``SELECT`` na każdy arkusz, skrypt i obrazek – dlatego wynik
mieszka w pamięci procesu z krótkim czasem życia.

Dlaczego pytamy o ``exists()``, a nie o samą wartość: w polityce nie ma identyfikatora, jest
wyłącznie host ``googletagmanager.com``. Rozstrzygnięcie jest więc binarne, a wartość dla szablonu
i tak przychodzi z ``settings.cms.SiteSettings`` (kontekst Wagtaila, jedno zapytanie na żądanie
i to samo, którym szablon składa stopkę).

**Pytanie jest per witryna** i to jest zmiana wobec stanu sprzed wielokonkursowości
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.7, pierwszy z czterech globalnych odczytów). Wcześniej
wystarczyło, że **którakolwiek** witryna instalacji ma wpisany identyfikator, żeby hosty Google'a
trafiły do polityki **wszystkich**. Dyrektywa CSP jest wprawdzie pozwoleniem, a nie poleceniem
(witryna bez identyfikatora nie wczyta skryptu tak czy inaczej), ale pozwolenie, z którego nigdy
nikt nie skorzysta, jest samym poszerzeniem powierzchni ataku – i to poszerzeniem, o którym
organizator drugiego konkursu nigdy się nie dowie, bo nie wynika z żadnego jego ustawienia.

``SiteSettings`` jest per witryna od migracji ``cms.0005`` (dziedziczy po ``BaseSiteSetting``,
który ma unikalne pole ``site``), więc zawężenie nie wymaga zmiany schematu – wystarczy filtr
i klucz pamięci podręcznej.

Odwrót ``site_id=None`` („nie wiadomo, która witryna”) zachowuje pytanie w dawnej, instalacyjnej
postaci. To jest świadome: warstwa CSP odpowiada także na żądania, dla których witryny nie da się
rozstrzygnąć (plik statyczny oddany przez WhiteNoise, żądanie z nieznanym hostem), a nagłówek
**węższy** od potrzeby zablokowałby analitykę tam, gdzie dotąd działała. Instalacja, w której nikt
nie wpisał identyfikatora, ma nagłówek co do bajtu taki, jak przed dodaniem GA – w obu wariantach.

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

#: ``{identyfikator witryny albo None: (monotoniczny znacznik czasu, wynik)}``. Moduł, a nie
#: ``django.core.cache``: pamięć współdzielona (Redis) byłaby tu drugim wywołaniem sieciowym
#: zamiast zapytania do bazy, a to jest kod odpalany przy **każdej** odpowiedzi.
#:
#: Słownik rośnie o jeden wpis na witrynę i nigdy więcej – witryn jest tyle, co konkursów, czyli
#: jednostki. Sprzątania nie ma i nie trzeba: skasowanie witryny zostawia wpis wart dwa wskaźniki,
#: a i tak wygasa on po ``CACHE_TTL_SECONDS``.
_cache: dict[int | None, tuple[float, bool]] = {}


def analytics_enabled(site_id: int | None = None) -> bool:
    """Czy ta witryna ma wpisany identyfikator GA4 (z pamięcią na ``CACHE_TTL_SECONDS``).

    ``site_id=None`` znaczy „nie wiadomo, która witryna” i pyta o **instalację** – patrz docstring
    modułu. To jest jedyny wariant, w którym odpowiedź może być szersza od potrzeby, i dlatego
    wołający, który witrynę zna, ma ją podać: robi to ``analytics_enabled_for_request``.

    Błąd bazy znaczy „nie” – tak samo, jak w ``site_chrome``. Nagłówek bezpieczeństwa nie może
    się wywrócić przez to, że tabela ustawień jeszcze nie istnieje (świeża baza przed migracjami),
    a domyślenie się „wyłączone” jest w polityce CSP stroną bezpieczną.
    """
    now = time.monotonic()
    remembered = _cache.get(site_id)
    if remembered is not None and now - remembered[0] < CACHE_TTL_SECONDS:
        return remembered[1]

    from apps.cms.models import SiteSettings

    try:
        rows = SiteSettings.objects.exclude(ga_measurement_id="")
        if site_id is not None:
            rows = rows.filter(site_id=site_id)
        enabled = rows.exists()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli ustawień
        enabled = False
    _cache[site_id] = (now, enabled)
    return enabled


def analytics_enabled_for_request(request) -> bool:
    """To samo pytanie, zadane o witrynę **tego** żądania.

    Wejście dla warstwy CSP (``apps.web.middleware``): nagłówek konkursu, który nie ma GA4, nie ma
    wymieniać hostów Google'a tylko dlatego, że wpisał je organizator obok.

    Witryna jest darmowa: ``Site.find_for_request`` zapamiętuje wynik na obiekcie żądania
    (``request._wagtail_site``), a do chwili składania nagłówka odpowiedzi rozstrzygnęły ją już
    ``CompetitionMiddleware`` i menu części informacyjnej. Żądanie, którego witryny nie da się
    ustalić – plik statyczny oddany przez WhiteNoise, nieznany host, ``RequestFactory`` w teście –
    schodzi na pytanie instalacyjne, czyli na zachowanie sprzed tej zmiany.
    """
    from wagtail.models import Site

    try:
        site = Site.find_for_request(request)
    except Exception:  # noqa: BLE001 - patrz niżej
        # Ta funkcja stoi w warstwie, która dokleja nagłówek do **każdej** odpowiedzi, łącznie
        # z odpowiedzią 400 na nieznany host (``DisallowedHost``) i ze stroną błędu. Wyjątek
        # rozstrzygania witryny nie może być nowym miejscem, w którym żądanie się kończy.
        site = None
    return analytics_enabled(site.pk if site is not None else None)


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętane odpowiedzi **wszystkich** witryn. Woła to sygnał zapisu oraz testy.

    Czyszczenie hurtem, mimo że sygnał zna zapisany wiersz: ustawienia da się przepiąć na inną
    witrynę, a wpis instalacyjny (klucz ``None``) zależy od nich wszystkich naraz. Zapis ustawień
    jest rzadki, a słownik ma tyle wpisów, co witryn – koszt jest żaden.
    """
    _cache.clear()


@receiver(post_save, dispatch_uid="cms.analytics.reset_cache")
def _reset_on_settings_save(sender, **kwargs) -> None:
    """Zapis ``SiteSettings`` w ``/cms/`` ma być widoczny od razu, a nie po upływie TTL.

    Odbiornik jest podpięty pod **każdy** ``post_save`` i dopiero w środku sprawdza nadawcę:
    podpięcie go do konkretnego modelu wymagałoby zaimportowania ``apps.cms.models`` w chwili
    ładowania aplikacji, czyli zanim rejestr modeli jest gotowy.
    """
    if sender.__name__ == "SiteSettings" and sender._meta.app_label == "cms":
        reset_cache()
