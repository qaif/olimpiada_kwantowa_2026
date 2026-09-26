"""Manifest adresów aplikacji i pliki kontraktu dla Caddy'ego/djcms (DJ-02a, ``docs/tasks/DJ-02.md`` § 6).

Co jest tu umową:

- manifest powstaje z **prawdziwego** urlconfu i pliki w ``backend/djcms_contract/`` są z nim
  zgodne (``djcms_routes --check`` – to samo, co w CI),
- ``APP_RE`` dopasowuje ścieżkę **wtedy i tylko wtedy**, gdy ``resolve()`` oddaje ją aplikacji,
  a nie catch-allowi Wagtaila – dla każdego wzorca urlconfu z przykładowymi argumentami i dla
  ścieżek stron z seedów. Tak samo ``APP_RE_PREFIXED`` pod prefiksem konkursu,
- nieznany kształt pierwszego segmentu to ``ImproperlyConfigured``, a nie cicha klasyfikacja,
- ``RESERVED_SLUGS`` pokrywa wszystkie pierwsze segmenty (+ ``djcms``, ``static``, ``media``).
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import CommandError, call_command
from django.http import HttpResponse
from django.urls import Resolver404, URLPattern, URLResolver, get_resolver, include, path, re_path, resolve
from django.urls.resolvers import RegexPattern, RoutePattern

from apps.cms.checks import check_pages_under_app_routes, check_reserved_slugs_cover_app_routes
from apps.cms.models import RESERVED_SLUGS, ContentPage, HomePage, NewsIndexPage
from apps.core.app_routes import (
    CATCH_ALL_NAME,
    PRIVATE_PREFIXES,
    build_manifest,
    caddy_regexes,
    contract_env,
    contract_json,
)
from apps.core.management.commands.djcms_routes import (
    ENV_NAME,
    JSON_NAME,
    default_contract_dir,
    rendered_files,
)

#: Pierwsze segmenty, których ręczna ``RESERVED_SLUGS`` nie znała przed DJ-02a (§ 0.1) – lista
#: z przeglądu urlconfu. Test pilnuje, że manifest je widzi: gdyby któryś zniknął, to znaczy, że
#: przejście przestało schodzić do jakiegoś ``include``.
PREVIOUSLY_MISSING = {
    "_util",
    "account",
    "accounts",
    "activate",
    "captcha",
    "dyplomy",
    "forum",
    "i18n",
    "konto",
    "password-reset",
    "plakaty",
    "rejestracja",
    "reset",
    "statystyki",
    "supervisor",
    "zaproszenie",
    "zgoda",
}

#: Ścieżki stron (catch-all Wagtaila) – w tym te, które **wyglądają** jak adresy aplikacji, a nimi
#: nie są: wspólny początek napisu (``konto-bankowe``, ``zgoda-opiekuna``), strona-rodzic adresu
#: aplikacji (``/warsztaty/``) i jej inne podstrony, adres aplikacji głębiej niż na drugim poziomie.
PAGE_PATHS = [
    "/",
    "/aktualnosci/",
    "/aktualnosci/ruszyla-rejestracja/",
    "/archiwum/edycja-1/",
    "/dokumenty/",
    "/dokumenty/regulamin/",
    "/dokumenty/zgoda-opiekuna/",
    "/faq/",
    "/harmonogram/",
    "/kontakt/",
    "/konto-bankowe/",
    "/login-pomoc/",
    "/partnerzy/",
    "/statusy/",
    "/warsztaty/",
    "/warsztaty/harmonogram/",
    "/warsztaty/materialy-dodatkowe/",
    "/wyniki/",
    "/zadania/",
    "/zgoda-opiekuna/",
    "/a/b/login/",
]

#: Próbki dla wzorców z wyrażeniem regularnym (nie da się ich wygenerować z ``_route``).
REGEX_SAMPLES = [
    "/admin/auth/",
    "/admin/cokolwiek/dalej/",
    "/captcha/image/abc123/",
    "/captcha/refresh/",
    "/documents/1/regulamin.pdf",
    "/cms/pages/1/edit/",
]

_CONVERTER_SAMPLES = {
    "int": "1",
    "str": "abc",
    "slug": "abc-1",
    "path": "a/b",
    "uuid": "0" * 8 + "-0000-0000-0000-" + "0" * 12,
}
_CONVERTER_RE = re.compile(r"<(?:(?P<conv>\w+):)?\w+>")


def _view(request):  # pragma: no cover - wzorce testowe nie są wołane
    return HttpResponse()


def _resolver(patterns) -> URLResolver:
    return URLResolver(RegexPattern(r"^/"), patterns)


def _sample_route(route: str) -> str:
    return _CONVERTER_RE.sub(lambda m: _CONVERTER_SAMPLES.get(m.group("conv") or "str", "abc"), route)


def _generated_samples(patterns, prefix: str = "") -> list[str]:
    """Po jednej ścieżce na każdy wzorzec z ``_route`` (także w zagnieżdżonych ``include``)."""
    samples: list[str] = []
    for entry in patterns:
        if not isinstance(entry.pattern, RoutePattern):
            continue  # wyrażenia regularne – ``REGEX_SAMPLES``
        here = prefix + _sample_route(entry.pattern._route)
        if isinstance(entry, URLResolver):
            samples.extend(_generated_samples(entry.url_patterns, here))
        elif isinstance(entry, URLPattern) and entry.name != CATCH_ALL_NAME:
            samples.append("/" + here)
    return samples


def _is_app(path: str) -> bool | None:
    """``True`` – aplikacja, ``False`` – drzewo stron, ``None`` – urlconf nie zna tej ścieżki."""
    try:
        match = resolve(path)
    except Resolver404:
        return None
    return match.url_name != CATCH_ALL_NAME


@pytest.fixture(scope="module")
def manifest():
    return build_manifest()


@pytest.fixture(scope="module")
def regexes(manifest):
    app_re, app_re_prefixed = caddy_regexes(manifest)
    return re.compile(app_re), re.compile(app_re_prefixed)


# --- manifest z prawdziwego urlconfu ---------------------------------------------------------------


def test_manifest_sees_every_first_segment_of_the_urlconf(manifest):
    segments = set(manifest.first_segments)

    assert PREVIOUSLY_MISSING <= segments
    assert {"admin", "api", "cms", "coordinator", "documents", "login", "me", "internal"} <= segments
    assert manifest.nested_paths == ("warsztaty/materialy",)
    assert manifest.root_regexes == (r"[^/]+\.html", r"status\.json")
    assert list(manifest.first_segments) == sorted(manifest.first_segments)


def test_page_parent_is_not_reserved_as_a_whole(manifest):
    """``warsztaty`` to strona redakcyjna – zarezerwowane jest tylko ``warsztaty/materialy``."""
    assert "warsztaty" not in manifest.first_segments
    assert "warsztaty" not in RESERVED_SLUGS


def test_debug_only_media_pattern_is_not_part_of_the_contract(manifest):
    assert "media" not in manifest.first_segments


def test_reserved_slugs_cover_every_application_first_segment(manifest):
    assert set(manifest.first_segments) | {"djcms", "static", "media"} <= RESERVED_SLUGS


def test_private_prefixes_are_application_segments(manifest):
    assert set(PRIVATE_PREFIXES) <= set(manifest.first_segments) | {"djcms"}


# --- wyrażenia wobec resolve() ----------------------------------------------------------------------


def _all_samples() -> list[str]:
    return sorted({*_generated_samples(get_resolver().url_patterns), *REGEX_SAMPLES, *PAGE_PATHS})


def test_there_are_enough_samples_to_mean_something():
    samples = _all_samples()
    assert len(samples) >= 60
    assert sum(1 for p in samples if _is_app(p) is False) >= len(PAGE_PATHS) - 1


def test_app_re_matches_exactly_what_the_application_resolves(regexes):
    """Jedna asercja na całą listę (kilkaset ścieżek) – czytelny komunikat zamiast setek przypadków.

    Ścieżki, których urlconf nie zna (``Resolver404`` – np. konwerter własny z próbką spoza
    wzorca), pomijamy: nie da się o nich powiedzieć, do kogo należą.
    """
    app_re, _ = regexes
    wrong = [
        (sample, owner)
        for sample in _all_samples()
        if (owner := _is_app(sample)) is not None and bool(app_re.match(sample)) is not owner
    ]

    assert wrong == []


@pytest.mark.parametrize("prefix", ["/druga", "/Konkurs_2"])
def test_app_re_prefixed_is_app_re_under_any_competition_prefix(regexes, prefix):
    """``CompetitionMiddleware`` zdejmuje prefiks przed ``resolve()`` – pod prefiksem adres aplikacji
    to ten sam adres co w korzeniu. ``SlugField`` prefiksu dopuszcza ``_`` i wielkie litery."""
    app_re, app_re_prefixed = regexes
    for sample in _all_samples():
        assert bool(app_re_prefixed.match(prefix + sample)) is bool(app_re.match(sample)), prefix + sample


def test_paths_without_trailing_slash_and_file_like_paths(regexes):
    app_re, app_re_prefixed = regexes
    assert app_re.match("/login")
    assert app_re.match("/status.json")
    assert app_re.match("/google0123.html")
    assert not app_re.match("/status.jsonx")
    assert not app_re.match("/loginx/")
    assert app_re_prefixed.match("/druga/status.json")
    assert not app_re_prefixed.match("/druga/")
    assert not app_re_prefixed.match("/druga/zadania/")


# --- klasyfikacja na urlconfach testowych -----------------------------------------------------------


def test_walk_flattens_empty_includes_and_stops_at_the_catch_all():
    resolver = _resolver(
        [
            path("admin/", include([path("", _view), path("<int:pk>/", _view)])),
            path("", include([path("forum/<int:pk>/", _view), path("forum/", _view)])),
            re_path(r"^foo/(?P<x>\d+)/$", _view),
            path("warsztaty/materialy/<int:pk>/", _view),
            path("status.json", _view),
            path("<str:token>.html", _view),
            path("", include([path("_util/login/", _view), re_path(r"^(.*)$", _view, name=CATCH_ALL_NAME)])),
            # Za catch-allem – nieosiągalne, więc nie należy do manifestu.
            path("<slug:po>/", _view),
            path("za-catch-allem/", _view),
        ]
    )

    manifest = build_manifest(resolver)

    assert manifest.first_segments == ("_util", "admin", "foo", "forum")
    assert manifest.nested_paths == ("warsztaty/materialy",)
    assert manifest.root_regexes == (r"[^/]+\.html", r"status\.json")


def test_debug_static_patterns_are_skipped():
    from django.views.static import serve

    manifest = build_manifest(_resolver([re_path(r"^media/(?P<path>.*)$", serve), path("login/", _view)]))

    assert manifest.first_segments == ("login",)


@pytest.mark.parametrize(
    "pattern",
    [
        path("<slug:competition>/", _view, name="po-slugu"),
        path("<int:pk>/edit/", _view, name="po-numerze"),
        re_path(r"^(?P<x>\w+)/$", _view, name="wyrazenie"),
        path("<int:pk>.txt", _view, name="plik-z-konwerterem"),
        path("warsztaty/", _view, name="sama-strona"),
        path("warsztaty/<int:pk>/", _view, name="zmienny-drugi-segment"),
        re_path(r"^warsztaty/materialy/$", _view, name="wyrazenie-pod-strona"),
        path("Wielkie/", _view, name="wielkie-litery"),
    ],
)
def test_unclassifiable_pattern_is_refused_by_name(pattern):
    with pytest.raises(ImproperlyConfigured) as excinfo:
        build_manifest(_resolver([pattern]))

    assert pattern.name in str(excinfo.value)


def test_regexes_are_safe_for_caddyfile_and_shell(manifest):
    for regex in caddy_regexes(manifest):
        assert not set(regex) & set(" \t\n{}'\"")
        assert regex.startswith("^/")


# --- pliki kontraktu i komenda ----------------------------------------------------------------------


def test_committed_contract_files_are_current():
    """To samo, co krok CI: nowy adres aplikacji bez ``djcms_routes --write`` = czerwony test."""
    out = io.StringIO()

    call_command("djcms_routes", "--check", stdout=out)

    assert "aktualny" in out.getvalue()


def test_json_and_env_carry_the_same_regexes(manifest):
    data = contract_json(manifest)
    env = dict(
        line.split("=", 1)
        for line in contract_env(manifest).splitlines()
        if line and not line.startswith("#")
    )

    assert data["version"] == 1
    assert env == {"APP_RE": f"'{data['app_re']}'", "APP_RE_PREFIXED": f"'{data['app_re_prefixed']}'"}
    assert set(data) == {
        "version",
        "generated_by",
        "first_segments",
        "nested_paths",
        "root_regexes",
        "private_prefixes",
        "app_re",
        "app_re_prefixed",
    }


@pytest.mark.skipif(shutil.which("sh") is None, reason="brak POSIX sh")
def test_env_file_is_a_posix_shell_fragment(manifest):
    """``render_caddyfile.sh`` ma móc zrobić ``. app_routes.env`` i dostać wyrażenie bajt w bajt."""
    contract = default_contract_dir() / ENV_NAME
    result = subprocess.run(
        ["sh", "-c", '. "$1" && printf "%s\\n%s" "$APP_RE" "$APP_RE_PREFIXED"', "sh", str(contract)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.split("\n") == list(caddy_regexes(manifest))


def test_write_then_check_round_trip(tmp_path):
    call_command("djcms_routes", "--write", "--dir", str(tmp_path), stdout=io.StringIO())

    assert (tmp_path / JSON_NAME).read_text(encoding="utf-8") == rendered_files()[JSON_NAME]
    assert json.loads((tmp_path / JSON_NAME).read_text(encoding="utf-8"))["version"] == 1
    assert b"\r\n" not in (tmp_path / ENV_NAME).read_bytes()
    call_command("djcms_routes", "--check", "--dir", str(tmp_path), stdout=io.StringIO())


def test_check_fails_with_a_diff_when_a_file_is_stale(tmp_path):
    call_command("djcms_routes", "--write", "--dir", str(tmp_path), stdout=io.StringIO())
    env = tmp_path / ENV_NAME
    env.write_text(env.read_text(encoding="utf-8").replace("|forum", ""), encoding="utf-8")
    out = io.StringIO()

    with pytest.raises(CommandError) as excinfo:
        call_command("djcms_routes", "--check", "--dir", str(tmp_path), stdout=out)

    assert excinfo.value.returncode == 1
    assert ENV_NAME in str(excinfo.value)
    assert "+APP_RE=" in out.getvalue()


def test_check_fails_when_the_files_are_missing(tmp_path):
    with pytest.raises(CommandError) as excinfo:
        call_command("djcms_routes", "--check", "--dir", str(tmp_path / "brak"), stdout=io.StringIO())

    assert JSON_NAME in str(excinfo.value)
    assert ENV_NAME in str(excinfo.value)


@pytest.mark.parametrize("fmt", ["text", "json", "env"])
def test_preview_formats(fmt, manifest):
    out = io.StringIO()

    call_command("djcms_routes", "--format", fmt, stdout=out)

    app_re, _ = caddy_regexes(manifest)
    if fmt == "json":
        assert json.loads(out.getvalue())["app_re"] == app_re
    else:
        assert app_re in out.getvalue()


def test_contract_dir_is_next_to_manage_py():
    assert (default_contract_dir().parent / "manage.py").exists()
    assert isinstance(default_contract_dir(), Path)


# --- rezerwacja djcms i kontrole systemowe ----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["djcms", "forum", "konto", "_util", "password-reset"])
def test_second_level_page_cannot_take_a_newly_reserved_slug(slug):
    with pytest.raises(ValidationError) as excinfo:
        NewsIndexPage(title="Podszywacz", slug=slug).clean()

    assert "slug" in excinfo.value.message_dict


def test_w011_is_silent_for_the_real_lists():
    assert check_reserved_slugs_cover_app_routes() == []


def test_w011_names_the_missing_segments(monkeypatch):
    monkeypatch.setattr("apps.cms.models.RESERVED_SLUGS", RESERVED_SLUGS - {"forum", "zgoda"})

    messages = check_reserved_slugs_cover_app_routes()

    assert [m.id for m in messages] == ["cms.W011"]
    assert "forum, zgoda" in messages[0].msg


def test_w011_reports_an_unclassifiable_urlconf(monkeypatch):
    def broken(resolver=None):
        raise ImproperlyConfigured("djcms_routes: nie umiem zaklasyfikować wzorca")

    monkeypatch.setattr("apps.core.app_routes.build_manifest", broken)

    assert [m.id for m in check_reserved_slugs_cover_app_routes()] == ["cms.W011"]


def _page_with_forced_slug(parent, slug: str, *, live: bool = True) -> ContentPage:
    """Strona o slugu, którego ``CMSPage.clean`` dziś nie przepuści – stan sprzed DJ-02a."""
    page = ContentPage(title=f"Stara {slug}", slug=f"tymczasowa-{slug.strip('_')}", live=live)
    parent.add_child(instance=page)
    ContentPage.objects.filter(pk=page.pk).update(slug=slug, url_path=f"{parent.url_path}{slug}/")
    page.refresh_from_db()
    return page


@pytest.mark.django_db
def test_w012_needs_the_database_flag(competition):
    home = HomePage.objects.get(pk=competition.site.root_page_id)
    _page_with_forced_slug(home, "forum")

    assert check_pages_under_app_routes() == []


@pytest.mark.django_db
def test_w012_reports_published_pages_under_application_addresses(competition):
    from wagtail.models import Page

    home = HomePage.objects.get(pk=competition.site.root_page_id)
    _page_with_forced_slug(home, "forum")
    _page_with_forced_slug(home, "zgoda", live=False)
    warsztaty = Page.objects.child_of(home).filter(slug="warsztaty").first()
    if warsztaty is None:
        warsztaty = ContentPage(title="Warsztaty", slug="warsztaty")
        home.add_child(instance=warsztaty)
    warsztaty.add_child(instance=ContentPage(title="Materiały", slug="materialy"))
    about = ContentPage(title="O nas", slug="o-nas")
    home.add_child(instance=about)
    # Trzeci poziom o slugu aplikacji: reguła drugiego segmentu (DJ-02 § 1.2 D3).
    about.add_child(instance=ContentPage(title="Jak się zalogować", slug="login"))
    about.add_child(instance=ContentPage(title="Zespół", slug="zespol"))

    messages = check_pages_under_app_routes(databases=["default"])

    assert {m.id for m in messages} == {"cms.W012"}
    reported = sorted(re.search(r"„([^”]+)”", m.msg).group(1) for m in messages)
    assert reported == ["/forum/", "/o-nas/login/", "/warsztaty/materialy/"]


@pytest.mark.django_db
def test_w012_reports_pages_under_djcms_addresses(competition, other_competition):
    """``/djcms/`` pod każdym hostem, ``/<segment>/djcms/`` wyłącznie pod ``SITE_DOMAIN`` (DJ-02 § 3, S6)."""
    from wagtail.models import Page

    home = HomePage.objects.get(pk=competition.site.root_page_id)
    _page_with_forced_slug(home, "djcms")
    about = ContentPage(title="O nas", slug="o-nas")
    home.add_child(instance=about)
    about.add_child(instance=ContentPage(title="Nasz CMS", slug="djcms"))
    other_root = Page.objects.get(pk=other_competition.site.root_page_id)
    _page_with_forced_slug(other_root, "djcms")
    other_about = ContentPage(title="O nas", slug="o-nas")
    other_root.add_child(instance=other_about)
    # Pod hostem konkursu z własną domeną Caddy nie zabiera ``/<segment>/djcms/`` – strona zostaje.
    other_about.add_child(instance=ContentPage(title="Nasz CMS", slug="djcms"))

    messages = check_pages_under_app_routes(databases=["default"])

    reported = sorted(
        (re.search(r"witryny (\S+) ", m.msg).group(1), re.search(r"„([^”]+)”", m.msg).group(1))
        for m in messages
    )
    assert reported == [
        (other_competition.site.hostname, "/djcms/"),
        (competition.site.hostname, "/djcms/"),
        (competition.site.hostname, "/o-nas/djcms/"),
    ]


@pytest.mark.django_db
def test_seeded_content_has_no_page_under_an_application_address(competition, regexes):
    """Seedy produkcyjne (``seed_cms``, ``seed_regulamin``, ``seed_legacy_content``, ``seed_partners``)
    nie zakładają strony, którą nowa lista zarezerwowanych slugów by unieważniła."""
    from wagtail.models import Page

    call_command("seed_cms", verbosity=0)
    call_command("seed_regulamin", verbosity=0)
    call_command("seed_legacy_content", verbosity=0)
    call_command("seed_partners", verbosity=0)

    assert check_pages_under_app_routes(databases=["default"]) == []
    root = HomePage.objects.get(pk=competition.site.root_page_id)
    paths = ["/" + p.url_path[len(root.url_path) :] for p in Page.objects.descendant_of(root, inclusive=True)]
    assert len(paths) > 10
    app_re, _ = regexes
    for page_path in paths:
        assert not app_re.match(page_path), page_path
        assert _is_app(page_path) is False, page_path
