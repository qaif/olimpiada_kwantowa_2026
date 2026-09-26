"""Manifest adresów aplikacji – jedno źródło listy „co należy do ``web``, a co do drzewa stron”.

Po co to jest (``docs/tasks/DJ-02.md`` § 1.2 D3, § 6): serwis publiczny przejmie django CMS, więc
Caddy musi wiedzieć, które ścieżki dalej obsługuje aplikacja (logowanie, panele, API…), a djcms –
których stron **nie wolno** mu założyć, bo byłyby martwe. Dziś tę wiedzę ma wyłącznie urlconf
``web``: wszystko przed catch-allem ``wagtail_serve`` jest adresem aplikacji, reszta – stroną.
Ręczna lista w trzech miejscach (Caddyfile, djcms, ``RESERVED_SLUGS``) rozjechałaby się przy
pierwszym nowym ekranie; ręczna ``RESERVED_SLUGS`` już się rozjechała (brakowało kilkunastu
segmentów). Dlatego manifest **wyprowadzamy z urlconfu**, zapisujemy do plików kontraktu
(``backend/djcms_contract/``, ``manage.py djcms_routes --write``), a CI sprawdza, że pliki są
aktualne (``--check``).

Zasady przejścia:

- idziemy po ``get_resolver().url_patterns`` i **rozwijamy wyłącznie** ``include`` z pustym
  prefiksem (``apps.web.urls``, ``apps.web.social_urls``, ``wagtail.urls``); ``include`` z
  niepustym prefiksem (``api/``, ``cms/``) zajmuje cały swój pierwszy segment i nie ma po co
  zaglądać głębiej,
- kończymy na ``wagtail_serve``: to catch-all, wszystko za nim byłoby i tak nieosiągalne,
  a wszystko przed nim (w tym ``_util/…`` z ``wagtail.urls``) jest adresem aplikacji,
- wzorzec, którego pierwszego segmentu nie umiemy zaklasyfikować (konwerter albo wyrażenie
  regularne w pierwszym segmencie), kończy się ``ImproperlyConfigured`` z nazwą wzorca. Nowy adres
  w korzeniu **musi** zostać zaklasyfikowany świadomie – inaczej Caddy wysłałby go do djcms,
- wzorce zależne od ``DEBUG`` (``static(MEDIA_URL)``) pomijamy jawnie (``SKIPPED_CALLBACKS``):
  manifest musi być ten sam na laptopie, w CI i na produkcji.

Wynik jest czystą funkcją urlconfu – bez bazy i bez ustawień środowiska – więc ``--check`` działa
w CI bez Postgresa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import RegexPattern, RoutePattern

#: Nazwa catch-allu Wagtaila (``wagtail.urls``). Na nim przejście się kończy.
CATCH_ALL_NAME = "wagtail_serve"

#: Widoki wzorców istniejących wyłącznie przy ``DEBUG`` (``django.conf.urls.static.static``).
#: W produkcji media stoją na S3, a statyki podaje Caddy – te adresy nie są częścią kontraktu.
#: Samo ``media`` i ``static`` zostają zarezerwowane w ``RESERVED_SLUGS`` (patrz test manifestu).
SKIPPED_CALLBACKS = frozenset({"django.views.static.serve"})

#: Wzorce z konwerterem w **jedynym** segmencie, które znamy i świadomie przepuszczamy jako
#: „plik w korzeniu serwisu”. Klucz – ``RoutePattern._route``, wartość – wyrażenie ścieżki bez
#: wiodącego ``/``. Pliki weryfikacyjne Google Search Console (``/google….html``).
KNOWN_ROOT_ROUTES = {"<str:token>.html": r"[^/]+\.html"}

#: Prefiksy do ``Disallow`` w ``robots.txt`` djcms (§ 8): panele, API i logowanie Wagtaila.
#: ``djcms`` to adresy aplikacyjne samego djcms (admin, SSO) – nie ma go w urlconfie ``web``.
#: Test pilnuje, że każdy wpis poza ``djcms`` jest w ``first_segments``.
PRIVATE_PREFIXES = (
    "_util",
    "account",
    "accounts",
    "admin",
    "api",
    "cms",
    "coordinator",
    "djcms",
    "me",
    "review",
    "supervisor",
)

#: Pierwszy segment prefiksu konkursu w ``APP_RE_PREFIXED``. Dowolny segment, a nie
#: ``[a-z0-9-]+``: ``Competition.path_prefix`` jest ``SlugField``, czyli dopuszcza też ``_``
#: i wielkie litery, a prefiksy są danymi w bazie – Caddyfile nie może ich wyliczać (§ 1.2 D3).
PREFIX_SEGMENT_RE = "[^/]+"

#: Kształt segmentu, który wolno wkleić do wyrażenia bez ucieczek: te znaki znaczą w wyrażeniu
#: regularnym (Python ``re`` i RE2 Caddy'ego) to samo, co w adresie.
_SEGMENT_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")
#: Literał „pliku w korzeniu” (``status.json``): segment z opcjonalnym rozszerzeniem.
_ROOT_FILE_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*(?:\.[a-z0-9]+)?$")
#: Znaki, których wynikowe wyrażenie nie może zawierać, bo zepsułyby jego konsumentów:
#: odstęp i klamry (tokeny i placeholdery Caddyfile'a), apostrof i cudzysłów (wartość w ``.env``
#: jest w apostrofach), ``\n`` (jedna zmienna = jedna linia).
_FORBIDDEN_IN_REGEX = frozenset(" \t\n{}'\"")


@dataclass(frozen=True)
class AppRouteManifest:
    """Klasyfikacja adresów aplikacji (wszystko posortowane – plik kontraktu ma być stabilny)."""

    #: Pierwsze segmenty należące w całości do aplikacji: ``admin``, ``api``, ``login``, ``_util``…
    first_segments: tuple[str, ...]
    #: Adresy aplikacji pod ścieżką strony: ``warsztaty/materialy`` (dwa segmenty, bez ``/``).
    nested_paths: tuple[str, ...]
    #: Wyrażenia pełnej jednosegmentowej ścieżki pliku w korzeniu serwisu konkursu (bez ``/``):
    #: ``[^/]+\.html``, ``status\.json``.
    root_regexes: tuple[str, ...]
    #: Prefiksy prywatne do ``robots.txt`` djcms.
    private_prefixes: tuple[str, ...]

    @property
    def prefixable(self) -> tuple[str, ...]:
        """Co działa także pod ``/<prefiks>/`` – **wszystko**.

        ``CompetitionMiddleware`` zdejmuje prefiks przed rozwiązaniem adresu, więc
        ``/druga/status.json`` i ``/druga/google….html`` obsługuje dziś ``web`` tak samo jak adresy
        w korzeniu. Wysłanie ich do djcms złamałoby regułę S5 (odchylenie od § 6 zapisane w DJ-02).
        """
        return (*self.first_segments, *self.nested_paths, *self.root_regexes)


def _page_parent_segments() -> frozenset[str]:
    """Pierwsze segmenty, które są **ścieżką strony** CMS-u, a aplikacja wiesza pod nimi adresy.

    ``/warsztaty/`` to strona redakcyjna, ``/warsztaty/materialy/`` – ekran aplikacji
    (``apps/web/urls_workshop_materials.py``). Takich wzorców nie wolno potraktować jak zwykłych:
    ``warsztaty`` w ``first_segments`` zarezerwowałoby slug istniejącej strony i wysłało ją do
    ``web``. Lista jest jawna, a nie zgadywana z kształtu wzorca: po samym urlconfie nie da się
    odróżnić ``dyplomy/<code>/`` (segment aplikacji bez strony-rodzica) od
    ``warsztaty/materialy/`` (podstrona aplikacji pod stroną redakcyjną) – różni je tylko drzewo
    stron w bazie, a manifest ma być czystą funkcją kodu.

    Slug bierzemy z ``apps.cms.workshops`` – tej samej stałej, po której aplikacja znajduje stronę
    warsztatów – żeby zmiana sluga w jednym miejscu nie rozjechała manifestu. Import lokalny, bo
    ``apps.cms`` jako pakiet ładuje Wagtaila.
    """
    from apps.cms.workshops import WORKSHOPS_SLUG

    return frozenset({WORKSHOPS_SLUG})


def _is_empty_prefix(pattern) -> bool:
    if isinstance(pattern, RoutePattern):
        return pattern._route == ""
    if isinstance(pattern, RegexPattern):
        return pattern._regex in ("", "^")
    return False


def _describe(entry) -> str:
    """Nazwa wzorca do komunikatu błędu – ma wskazać, **który** wpis urlconfu poprawić."""
    pattern = entry.pattern
    text = pattern._route if isinstance(pattern, RoutePattern) else str(pattern)
    if isinstance(entry, URLPattern):
        return f"„{text}” (name={entry.name!r})"
    return f"„{text}” (include {entry.urlconf_name!r})"


def _callback_path(entry: URLPattern) -> str:
    callback = entry.callback
    return f"{getattr(callback, '__module__', '')}.{getattr(callback, '__name__', '')}"


def _top_level_entries(patterns, out: list) -> bool:
    """Spłaszcza ``include`` z pustym prefiksem; zwraca ``True`` po trafieniu na catch-all."""
    for entry in patterns:
        if isinstance(entry, URLResolver):
            if _is_empty_prefix(entry.pattern):
                if _top_level_entries(entry.url_patterns, out):
                    return True
                continue
            out.append(entry)
            continue
        if entry.name == CATCH_ALL_NAME:
            return True
        if _callback_path(entry) in SKIPPED_CALLBACKS:
            continue
        out.append(entry)
    return False


def _route_text(entry) -> str:
    """Tekst wzorca względem korzenia: ``_route`` albo wyrażenie bez kotwicy ``^``."""
    pattern = entry.pattern
    if isinstance(pattern, RoutePattern):
        return pattern._route
    if isinstance(pattern, RegexPattern):
        return pattern._regex.removeprefix("^")
    raise ImproperlyConfigured(
        f"djcms_routes: nieobsługiwany rodzaj wzorca {type(pattern).__name__} w {_describe(entry)} – "
        "dopisz jego klasyfikację w apps/core/app_routes.py."
    )


def _classify(entry, page_parents: frozenset[str]) -> tuple[str, str]:
    """``("segment" | "nested" | "root", wartość)`` dla jednego wpisu najwyższego poziomu."""
    text = _route_text(entry)
    is_regex = isinstance(entry.pattern, RegexPattern)
    head, sep, rest = text.partition("/")

    if not sep:
        # Jeden segment bez ukośnika: adres pliku w korzeniu (``status.json``, ``<token>.html``).
        if not is_regex and text in KNOWN_ROOT_ROUTES:
            return "root", KNOWN_ROOT_ROUTES[text]
        if not is_regex and _ROOT_FILE_RE.match(text):
            return "root", text.replace(".", r"\.")
        raise ImproperlyConfigured(
            f"djcms_routes: nie umiem zaklasyfikować wzorca {_describe(entry)} w korzeniu. "
            "Adres aplikacji musi zaczynać się stałym segmentem (``nazwa/…``) albo zostać dopisany "
            "świadomie do KNOWN_ROOT_ROUTES w apps/core/app_routes.py."
        )

    if not _SEGMENT_RE.match(head):
        raise ImproperlyConfigured(
            f"djcms_routes: pierwszy segment wzorca {_describe(entry)} nie jest stałym napisem "
            "[a-z0-9_-]. Adres aplikacji o zmiennym pierwszym segmencie przesłoniłby strony CMS – "
            "nadaj mu stały prefiks albo zaklasyfikuj go jawnie w apps/core/app_routes.py."
        )

    if head not in page_parents:
        return "segment", head

    # Adres aplikacji pod stroną redakcyjną: rezerwujemy dwa segmenty, nie pierwszy.
    second = rest.partition("/")[0]
    if is_regex or not _SEGMENT_RE.match(second):
        raise ImproperlyConfigured(
            f"djcms_routes: wzorzec {_describe(entry)} zajmuje stronę „/{head}/” albo jej podstrony "
            "o zmiennym drugim segmencie – nie da się go oddzielić od treści redakcyjnej. Adres "
            "aplikacji pod stroną musi mieć stały drugi segment (jak ``warsztaty/materialy/``)."
        )
    return "nested", f"{head}/{second}"


def build_manifest(resolver=None) -> AppRouteManifest:
    """Manifest z urlconfu (domyślnie głównego). Rzuca ``ImproperlyConfigured`` przy nieznanym kształcie."""
    resolver = resolver or get_resolver()
    entries: list = []
    _top_level_entries(resolver.url_patterns, entries)
    page_parents = _page_parent_segments()

    segments: set[str] = set()
    nested: set[str] = set()
    roots: set[str] = set()
    for entry in entries:
        kind, value = _classify(entry, page_parents)
        {"segment": segments, "nested": nested, "root": roots}[kind].add(value)

    return AppRouteManifest(
        first_segments=tuple(sorted(segments)),
        nested_paths=tuple(sorted(nested)),
        root_regexes=tuple(sorted(roots)),
        private_prefixes=PRIVATE_PREFIXES,
    )


def _checked(regex: str) -> str:
    """Wyrażenie gotowe dla Caddy'ego i powłoki – albo wyjątek, gdyby coś je zepsuło."""
    bad = sorted(set(regex) & _FORBIDDEN_IN_REGEX)
    if bad:  # pragma: no cover - segmenty są walidowane wcześniej; bezpiecznik na przyszłe zmiany
        raise ImproperlyConfigured(f"djcms_routes: niedozwolone znaki {bad!r} w wyrażeniu {regex!r}.")
    re.compile(regex)
    return regex


def caddy_regexes(manifest: AppRouteManifest) -> tuple[str, str]:
    """``(APP_RE, APP_RE_PREFIXED)`` – wyrażenia na **całą** ścieżkę żądania (z wiodącym ``/``).

    Składnia jest wspólnym podzbiorem Pythona i RE2 (Go, ``path_regexp`` Caddy'ego): grupy
    ``(?:…)``, alternatywa, ``.*``, klasy znaków; bez lookaround i odwołań wstecznych.
    ``(?:/.*)?$`` sprawia, że ``/login`` (bez ukośnika – ``APPEND_SLASH`` w ``web``) też jest
    adresem aplikacji. Drugie wyrażenie przyjmuje dowolny pierwszy segment (prefiks konkursu).
    """
    directories = "|".join(sorted((*manifest.first_segments, *manifest.nested_paths)))
    tails = [f"(?:{directories})(?:/.*)?", *manifest.root_regexes]
    app_re = "|".join(f"^/{tail}$" for tail in tails)
    app_re_prefixed = "|".join(f"^/{PREFIX_SEGMENT_RE}/{tail}$" for tail in tails)
    return _checked(app_re), _checked(app_re_prefixed)


#: Wersja formatu plików kontraktu. Podbijana przy każdej zmianie znaczenia pól – djcms odrzuca
#: plik w wersji, której nie zna, zamiast po cichu źle rozstrzygać trasy.
CONTRACT_VERSION = 1

#: Nagłówek pliku ``.env`` (komentarze ``#`` rozumie i ``sh``, i ``docker compose``).
ENV_HEADER = (
    "# Wygenerowane przez `manage.py djcms_routes --write` – nie edytuj ręcznie.\n"
    "# Źródło: urlconf aplikacji `web` (apps/core/app_routes.py, docs/tasks/DJ-02.md § 6).\n"
    "# Format: poprawny fragment POSIX sh – `. ./app_routes.env` – jedna zmienna w linii,\n"
    "# wartość w apostrofach, bez apostrofów, odstępów i klamer w środku (bezpieczne\n"
    "# wklejenie do matchera `path_regexp` Caddyfile'a). Wyrażenia dopasowują całą ścieżkę;\n"
    "# składnia RE2/PCRE (`(?:…)`), nie POSIX ERE – w testach powłoki `grep -P`, nie `grep -E`.\n"
)


def contract_json(manifest: AppRouteManifest) -> dict:
    """Treść ``app_routes.json`` (dla djcms) – klucze w stałej kolejności."""
    app_re, app_re_prefixed = caddy_regexes(manifest)
    return {
        "version": CONTRACT_VERSION,
        "generated_by": "manage.py djcms_routes --write",
        "first_segments": list(manifest.first_segments),
        "nested_paths": list(manifest.nested_paths),
        "root_regexes": list(manifest.root_regexes),
        "private_prefixes": list(manifest.private_prefixes),
        "app_re": app_re,
        "app_re_prefixed": app_re_prefixed,
    }


def contract_env(manifest: AppRouteManifest) -> str:
    """Treść ``app_routes.env`` (dla ``render_caddyfile.sh``)."""
    app_re, app_re_prefixed = caddy_regexes(manifest)
    return f"{ENV_HEADER}APP_RE='{app_re}'\nAPP_RE_PREFIXED='{app_re_prefixed}'\n"
