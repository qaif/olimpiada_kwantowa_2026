"""Rozstrzyganie konkursu z żądania: host → ``wagtailcore.Site`` → ``Competition``.

Jedno miejsce, w którym ta reguła jest zapisana. ``Site.find_for_request`` jest już używane przez
``apps.cms.context_processors`` (menu) i ``apps.support.services`` (adres kontaktowy), więc
dopasowanie hosta jest tu **tym samym** dopasowaniem, którym Wagtail wybiera drzewo stron.
Drugiej reguły nie budujemy: rozjazd między „która witryna serwuje strony” a „który konkurs
jest właścicielem danych” byłby wyciekiem między konkursami.

Ścieżka zapasowa dewelopera wychodzi z tego za darmo: ``find_for_request`` przy braku trafienia po
hoście oddaje witrynę domyślną, więc ``localhost``, ``127.0.0.1`` i ``web`` dostają Konkurs #1
dokładnie tak, jak dotąd – bez wpisów w DNS i bez zmiennych środowiskowych.

**Koszt na żądanie: jedno zapytanie.** Witrynę Wagtail i tak odczytuje (i zapamiętuje na żądaniu),
a konkurs – z domeny, z prefiksu ścieżki albo z aliasu witryny (``apps.tenancy.aliases``,
wielojęzyczność treści, § 1.6.2) – bierze się z **jednego** zapytania z alternatywą, a nie z trzech.
To jest świadome odstępstwo od § 2.3 dokumentu, który proponował osobną, buforowaną
mapę prefiksów: bufor z czasem życia znaczy, że koszt żądania zależy od tego, kiedy ostatnio
wygasł, a tego nie da się ani przewidzieć w teście liczby zapytań, ani wytłumaczyć przy diagnozie
produkcji. Jedno zapytanie zawsze jest tańsze do zrozumienia niż zero albo dwa zależnie od zegara.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.db import DatabaseError
from django.db.models import Q
from wagtail.models import Site

from apps.tenancy.aliases import alias_match
from apps.tenancy.models import Competition, RoutingMode

logger = logging.getLogger(__name__)

#: Przedrostek adresów **wewnętrznych** – tych, które woła infrastruktura, a nie przeglądarka.
#: Dziś jest pod nim jeden adres (``/internal/tls-allowed``, pytanie Caddy'ego o certyfikat).
#: Stała stoi tutaj, a nie w ``apps/tenancy/internal_urls.py``, bo czyta ją także warstwa
#: (``CompetitionMiddleware``), a warstwa ładuje się przy starcie aplikacji – wcześniej niż
#: urlconf i jego widoki. ``internal`` jest zarazem w ``apps.cms.models.RESERVED_SLUGS``, więc
#: strony CMS o tym adresie nie ma i być nie może.
INTERNAL_URL_PREFIX = "/internal/"


@dataclass(frozen=True)
class Resolution:
    """Wynik rozstrzygania: konkurs oraz prefiks, który trzeba zdjąć z adresu.

    Prefiks jest tu, a nie wyliczany drugi raz w warstwie, bo to **to samo** rozstrzygnięcie:
    konkurs znaleziony po pierwszym segmencie ścieżki i segment, który wobec tego do adresu już
    nie należy. Dwa wyliczenia tej samej rzeczy to dwie okazje do rozjazdu.
    """

    competition: Competition | None
    #: Niepusty wyłącznie w trybie ``PATH`` i wyłącznie wtedy, gdy adres faktycznie go niósł.
    path_prefix: str = ""
    #: Witryna dopasowana po **hoście** – przy prefiksie ścieżki jest to witryna platformy, a nie
    #: witryna konkursu. Warstwa potrzebuje jej do adresów bezwzględnych stron tego konkursu
    #: (``apps.tenancy.page_urls``): konkurs pod prefiksem odpowiada pod hostem platformy, więc
    #: i jego ``full_url`` ma się zaczynać od adresu platformy, a nie od domeny, na którą czeka.
    host_site: Site | None = None


#: Przełącznik konkursu **platformy** (tego, którego witryna odpowiada pod danym hostem), który
#: otwiera ten host dla konkursów adresowanych prefiksem ścieżki (§ 2.3). Czytany wyłącznie tutaj.
PATH_PREFIX_FLAG = "path_prefix_routing"


def hosts_path_prefixes(host_competition: Competition | None) -> bool:
    """Czy pod hostem tego konkursu wolno rozstrzygać inne konkursy po prefiksie ścieżki.

    Bramka stoi po stronie **gospodarza**, a nie konkursu pod prefiksem, bo to gospodarz płaci
    cenę: w trybie ``PATH`` sesja i CSRF stoją na jego hoście, więc zalogowanie w konkursie pod
    prefiksem jest zalogowaniem u niego (§ 2.3, „ciasteczka są wspólne”). Konkurs pod prefiksem
    mówi o sobie ``routing_mode=PATH`` – druga flaga z tą samą treścią byłaby drugim źródłem tej
    samej prawdy. Bez bramki każdy host z konkursem (także cudza domena organizatora) serwowałby
    pod ``/<prefiks>/`` obcy konkurs pod swoją marką i ze swoimi ciasteczkami.
    """
    return host_competition is not None and host_competition.has_feature(PATH_PREFIX_FLAG)


def first_path_segment(path_info: str) -> str:
    """Pierwszy segment ścieżki: ``"/fizyczna/me/"`` → ``"fizyczna"``, ``"/"`` → ``""``."""
    return path_info.lstrip("/").split("/", 1)[0]


def resolve_for_request(request) -> Resolution:
    """Konkurs żądania razem z prefiksem do zdjęcia z adresu.

    Pierwszeństwo ma **prefiks ścieżki**, bo jest jawnym wskazaniem w adresie: konkurs czekający
    na własny DNS chodzi pod domeną platformy, więc dopasowanie po hoście oddałoby konkurs
    platformy, a nie ten, o który poprosił adres. Prefiks liczy się jednak **wyłącznie** pod hostem,
    którego konkurs ma włączone ``path_prefix_routing`` (:func:`hosts_path_prefixes`) – bramka nie
    kosztuje zapytania, bo konkurs hosta przychodzi tym samym zapytaniem, co konkurs prefiksu.

    Błąd bazy nie wywraca żądania: warstwa wyżej ma prawo nie znać konkursu (dokładnie tak, jak
    nie znała go przed tą zmianą), a stronę błędu i tak złoży ten sam mechanizm, co dla każdego
    innego zapytania. Milcząco pusta odpowiedź jest tu lepsza niż 500 na ``/healthz/``.

    ``DisallowedHost`` też kończy się tu pustym wynikiem, a nie wyjątkiem: odpowiedź 400 dla
    żądania z obcym nagłówkiem ``Host`` ma nadal złożyć Django tam, gdzie składała ją przed tą
    zmianą. Rozstrzyganie konkursu nie może być **nowym** miejscem, w którym żądanie się kończy.
    """
    try:
        # Po przejściu warstwy żądanie pod prefiksem ma podmienioną witrynę (drzewo stron konkursu);
        # pierwszeństwo reguły „prefiks pod hostem gospodarza” liczy się od witryny **hosta**.
        site = getattr(request, "competition_host_site", None) or Site.find_for_request(request)
    except DatabaseError, DisallowedHost:
        logger.warning("Nie udało się rozstrzygnąć witryny żądania.", exc_info=True)
        return Resolution(None)

    segment = first_path_segment(request.path_info)
    match = Q(site=site) if site is not None else Q(pk__in=())
    if segment:
        match |= Q(routing_mode=RoutingMode.PATH, path_prefix=segment)
    # Gałąź aliasów (§ 1.6.2): druga witryna tego samego konkursu w drugim języku treści. Puste
    # ``Q()`` przy wyłączonym ``WAGTAIL_I18N_ENABLED`` nie zostawia w zapytaniu żadnego śladu –
    # ani złączenia, ani warunku – więc instalacja jednojęzyczna pyta dokładnie o to samo, co
    # przed tą zmianą.
    aliases = alias_match(site)
    match |= aliases

    try:
        # Najwyżej dwa wiersze: konkurs witryny (albo jej aliasu – witryna jest albo jednym, albo
        # drugim) i konkurs prefiksu. Pobieramy oba jednym zapytaniem i dopiero tutaj rozstrzygamy
        # pierwszeństwo – inaczej byłyby dwa zapytania, z których drugie prawie zawsze nic nie
        # znajduje.
        found = Competition.objects.filter(match, is_active=True).select_related("site")
        # Opcja „angielska wersja interfejsu” witryny konkursu jedzie **tym samym zapytaniem**:
        # warstwa języka (``apps.accounts.preferences.english_enabled``) pyta o nią przy każdym
        # żądaniu, także API, gdzie szablon bazowy nie czyta ``SiteSettings`` i osobny odczyt
        # byłby dodatkowym zapytaniem na każde wywołanie. Brak wiersza ustawień daje ``NULL``,
        # czyli „wyłączone” – tak samo, jak wartość domyślna pola.
        found = found.annotate(site_english_interface=_english_interface_subquery())
        if aliases:
            # Złączenie z aliasami mnoży wiersze konkursu, który ma ich kilka, a limit poniżej
            # liczy wiersze, nie konkursy. ``distinct()`` wchodzi **wyłącznie** razem z tą gałęzią,
            # żeby zapytanie instalacji jednojęzycznej zostało nietknięte.
            found = found.distinct()
        found = list(found[:2])
    except DatabaseError:
        logger.warning("Nie udało się odczytać konkursu dla żądania.", exc_info=True)
        return Resolution(None)

    def by_prefix(competition) -> bool:
        return (
            bool(segment)
            and competition.routing_mode == RoutingMode.PATH
            and competition.path_prefix == segment
        )

    # Konkurs hosta: witryna główna albo – pod drugą domeną językową – alias (§ 1.6.2). Wiersz
    # dopasowany prefiksem nie może być gospodarzem aliasu tylko dlatego, że stoi w tym samym wyniku.
    host = next((c for c in found if site is not None and c.site_id == site.pk), None)
    if host is None and aliases:
        host = _alias_competition([c for c in found if not by_prefix(c)])
    if hosts_path_prefixes(host):
        for competition in found:
            if competition is not host and by_prefix(competition):
                return Resolution(competition, segment, host_site=site)
    return Resolution(host, host_site=site if host is not None else None)


def _english_interface_subquery():
    """Podzapytanie o ``cms.SiteSettings.english_interface_enabled`` witryny konkursu.

    Import w funkcji: ``apps.cms`` importuje ``apps.tenancy`` (rozstrzyganie konkursu po witrynie),
    więc import na poziomie modułu zamknąłby koło.
    """
    from django.db.models import OuterRef, Subquery

    from apps.cms.models import SiteSettings

    return Subquery(
        SiteSettings.objects.filter(site_id=OuterRef("site_id")).values("english_interface_enabled")[:1]
    )


def _alias_competition(found) -> Competition | None:
    """Konkurs dopasowany **aliasem** witryny – o ile ma włączone tłumaczenia treści.

    Rozstrzygnięcie po wykluczeniu: pętle wyżej odrzuciły dopasowanie po prefiksie i po witrynie
    głównej, więc jedyną alternatywą, która mogła dołożyć ten wiersz do wyniku, jest alias. Pytanie
    bazy drugi raz („czy to na pewno alias”) kosztowałoby zapytanie na każde żądanie pod drugą
    domeną i nie odpowiedziałoby na nic, czego nie wiadomo.

    Flaga konkursu jest tu **drugą** bramką obok ustawienia instalacji: ``WAGTAIL_I18N_ENABLED``
    mówi, że w tej instalacji w ogóle istnieją drzewa w kilku językach, a ``content_translations``
    – że **ten** konkurs je prowadzi. Alias założony przed włączeniem flagi jest konfiguracją
    w toku, a nie działającą drugą domeną; bez tej bramki kolejność dwóch kroków operatora
    decydowałaby o tym, czy strona już odpowiada.
    """
    for competition in found:
        if competition.has_feature("content_translations"):
            return competition
    return None


def resolve_competition(request) -> Competition | None:
    """Konkurs dla żądania albo ``None``, gdy pod tym adresem nie stoi żaden.

    Wejście dla wołających, których prefiks ścieżki nie interesuje (wszystkich poza warstwą).
    """
    return resolve_for_request(request).competition


# =================================================================================================
# Subdomeny platformy (``PLATFORM_SUBDOMAINS``)
# =================================================================================================
#
# Konkurs założony z panelu koordynatora stoi pod adresem ``<slug>.<SITE_DOMAIN>``. Żeby to
# w ogóle mogło działać, instalacja wpuszcza **całą** domenę (``.SITE_DOMAIN`` w ``ALLOWED_HOSTS``,
# ``https://*.SITE_DOMAIN`` w ``CSRF_TRUSTED_ORIGINS`` – ``config/settings/base.py``). Wildcard
# jest wygodny i dlatego niebezpieczny: od tej chwili **każdy** host pod domeną platformy
# przechodzi walidację Django, a ``Site.find_for_request`` przy braku trafienia po hoście oddaje
# witrynę domyślną. Bez reguły niżej ``cokolwiek.olimpiadakwantowa.pl`` serwowałoby Konkurs #1 pod
# adresem, którego nikt nie zakładał – czyli tę samą treść pod nieskończenie wieloma adresami
# (dublet dla wyszukiwarek) i gotowy adres do podszywania się w listach.
#
# Reguła brzmi więc: pod domeną platformy odpowiada **wyłącznie** host, który ma własną witrynę
# i aktywny konkurs. Każdy inny dostaje 404, zanim cokolwiek się wyrenderuje. Przy wyłączonym
# przełączniku ta gałąź nie istnieje: wildcardu nie ma, więc nieznany host kończy się – jak dotąd –
# odmową ``ALLOWED_HOSTS``, a hosty wpisane ręcznie zachowują się dokładnie tak, jak przedtem.


def platform_subdomains_enabled() -> bool:
    """Czy instalacja obsługuje konkursy pod ``*.SITE_DOMAIN``.

    Czytamy przez ``getattr``, bo ustawienie jest nowe: instalacja ze starszym ``settings``
    (albo test z podmienionym obiektem ustawień) ma zachować się jak instalacja bez subdomen,
    a nie wywrócić się na ``AttributeError``.
    """
    return bool(getattr(settings, "PLATFORM_SUBDOMAINS", False))


def platform_domain() -> str:
    """Domena platformy (``SITE_DOMAIN``) w postaci porównywalnej z nagłówkiem ``Host``."""
    return _normalise_host(getattr(settings, "SITE_DOMAIN", "") or "")


def platform_subdomain_label(host: str) -> str:
    """``"fizyczna.olimpiadakwantowa.pl"`` → ``"fizyczna"``; spoza domeny platformy → ``""``.

    Etykieta bywa **wieloczłonowa** (``"en.fizyczna"`` dla drugiego drzewa językowego): ta funkcja
    oddaje wszystko, co stoi przed domeną platformy, a o tym, czy człon ma być jeden, rozstrzyga
    :func:`is_platform_subdomain` – i rozstrzyga to wyłącznie dla certyfikatów.
    """
    host = _normalise_host(host)
    base = platform_domain()
    suffix = f".{base}"
    if not base or not host.endswith(suffix):
        return ""
    return host[: -len(suffix)]


def is_platform_subdomain(host: str) -> bool:
    """Czy host jest **jednoczłonową** subdomeną platformy (``fizyczna.olimpiadakwantowa.pl``).

    Jeden człon, bo tyle obejmuje certyfikat wieloznaczny (``*.example.org`` nie pokrywa
    ``a.b.example.org``) i tyle zakłada ekran „Nowy konkurs”: slug konkursu jest etykietą
    subdomeny, a nie ścieżką w drzewie nazw.
    """
    label = platform_subdomain_label(host)
    return bool(label) and "." not in label


def platform_subdomain_miss(request, competition) -> bool:
    """Czy to żądanie trafiło pod subdomenę platformy, pod którą **nie stoi** aktywny konkurs.

    Trzy warunki naraz, w kolejności od najtańszego:

    1. przełącznik instalacji jest włączony (inaczej wildcardu nie ma i nie ma o czym mówić),
    2. host jest subdomeną ``SITE_DOMAIN`` i **nie** jest hostem wpisanym ręcznie do konfiguracji
       (``DJANGO_ALLOWED_HOSTS``, ``EXTRA_DOMAINS``, ``www.``) – host, który ktoś wypisał z nazwy,
       jest hostem zamówionym i nie należy do wildcardu,
    3. witryna Wagtaila **nie** została dopasowana po tym haśle (czyli ``find_for_request`` zszedł
       na witrynę domyślną) albo konkurs tej witryny jest nieaktywny.

    Zapytania: **zero**. ``Site.find_for_request`` zapamiętuje wynik na żądaniu
    (``request._wagtail_site``), a warstwa wołała je chwilę wcześniej przy rozstrzyganiu konkursu.
    """
    if not platform_subdomains_enabled():
        return False
    host = request_host(request)
    if not host or not platform_subdomain_label(host) or _host_is_configured(host):
        return False
    try:
        # Pod prefiksem ścieżki warstwa podmienia witrynę żądania na witrynę konkursu (drzewo stron),
        # a tu pytamy o witrynę **hosta** – tę, którą warstwa zapamiętała przed podmianą.
        site = getattr(request, "competition_host_site", None) or Site.find_for_request(request)
    except DatabaseError, DisallowedHost:  # pragma: no cover - baza bez witryn
        # „Nie wiem” nie może tu znaczyć „404”: stronę błędu i tak złoży ten sam mechanizm,
        # co dla każdego innego zapytania, a 404 z powodu awarii bazy byłby diagnozą fałszywą.
        return False
    if site is None or _normalise_host(site.hostname) != host:
        return True
    return competition is None


def request_host(request) -> str:
    """Host żądania bez portu, małymi literami. Pusty napis = „nie da się go odczytać”."""
    try:
        host = request.get_host()
    except DisallowedHost:  # pragma: no cover - odmowę składa Django, nie my
        return ""
    return _normalise_host(host.partition(":")[0])


def _host_is_configured(host: str) -> bool:
    """Czy ten host stoi **wypisany z nazwy** w konfiguracji instalacji.

    Wildcard ``.SITE_DOMAIN`` obejmuje wszystko pod domeną platformy, ale hosty wpisane ręcznie
    mają starszeństwo: ``www.<SITE_DOMAIN>`` jest w ``DJANGO_ALLOWED_HOSTS`` od pierwszego
    wdrożenia (przekierowanie 301 składa Caddy, ale w instalacji bez proxy żądanie dochodzi do
    aplikacji), a ``EXTRA_DOMAINS`` wymienia domeny konkursów dołożonych ręcznie. Gdyby reguła
    o nich nie wiedziała, włączenie przełącznika zgasiłoby hosty, które działały przed nim.
    """
    configured = {
        _normalise_host(entry)
        for entry in getattr(settings, "ALLOWED_HOSTS", [])
        if entry and not entry.startswith(".") and entry != "*"
    }
    configured.update(_normalise_host(entry) for entry in getattr(settings, "EXTRA_DOMAINS", []))
    base = platform_domain()
    if base:
        configured.add(f"www.{base}")
    return host in configured


def _normalise_host(value: str) -> str:
    """Host do porównania: bez spacji, bez kropki końcowej, małymi literami."""
    return (value or "").strip().lower().rstrip(".")
