"""Motyw aktywny dla żądania: rozstrzyganie, pamięć podręczna per wersja, podgląd koordynatora.

**Koszt dla konkursu bez motywu: zero zapytań.** ``active_theme`` patrzy najpierw na
``competition.theme_version_id`` (kolumna wiersza, który ``CompetitionMiddleware`` i tak już
pobrał) i na obecność parametru ``theme_preview`` w adresie – bez żadnego z nich odpowiedź
„brak motywu” zapada bez bazy i bez sesji. Konkurs #1 renderuje się więc co do bajtu i co do
liczby zapytań tak, jak przed motywami (testy złote/budżety zapytań w ``apps/tenancy/tests``).

**Koszt dla konkursu z motywem: jedno zapytanie na proces na wersję.** Wersja motywu jest niezmienna
(``ThemeVersion`` – pliki pod niezmiennym prefiksem, szablony w bazie), więc obiekt
:class:`ThemeRuntime` może żyć w pamięci procesu bez unieważniania. Usunąć da się wyłącznie wersję
nieużywaną, a takiej nikt już nie pyta.

**Podgląd** (``?theme_preview=<token>``): podpisany token z identyfikatorem wersji, konkursem
i wybranymi opcjami. Działa wyłącznie dla zalogowanego koordynatora **tego** konkursu – każdy inny
(gość, uczestnik, koordynator innego konkursu, token z innego konkursu, token przeterminowany)
dostaje stronę tak, jakby parametru nie było. Podgląd nie zapisuje niczego.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from django.core import signing

logger = logging.getLogger(__name__)

PREVIEW_PARAM = "theme_preview"
PREVIEW_SALT = "apps.themes.preview"
#: Token podglądu żyje dobę – tyle wystarczy na obejrzenie motywu i pokazanie go komuś obok.
PREVIEW_MAX_AGE = 24 * 3600
#: Identyfikator „wersji” motywu wbudowanego w tokenie podglądu (podgląd powrotu do ``classic``).
CLASSIC_PREVIEW = 0

_MISSING = object()


@dataclass(frozen=True)
class ThemeRuntime:
    """Wszystko, czego render potrzebuje o wersji motywu – bez dalszych zapytań."""

    pk: int
    slug: str
    name: str
    version: str
    color_scheme: str
    supports: tuple[str, ...]
    layouts: dict[str, tuple[str, ...]]
    templates: dict[str, str]
    css_url: str
    tokens_url: str
    assets_url: str
    meta_color: str
    palette: dict[str, str] = field(default_factory=dict)
    #: Palety i pozostałe tokeny z ``tokens.json`` (``light``/``dark``/``other``/``dark_other``) –
    #: źródło dla dostosowania kolorów i schematu (THEME-02 § 2, ``apps.themes.customize``).
    tokens: dict = field(default_factory=dict)
    #: Warianty logo i pary krojów z manifestu (THEME-02 § 2.1); pierwszy wpis = domyślny.
    logos: tuple[dict, ...] = ()
    fonts: tuple[dict, ...] = ()


TOKEN_GROUPS = ("light", "dark", "other", "dark_other")

_RUNTIMES: dict[int, ThemeRuntime | None] = {}
_LOCK = threading.Lock()


def runtime_for(version_id: int | None) -> ThemeRuntime | None:
    """``ThemeRuntime`` poprawnej wersji albo ``None`` (wersja usunięta/odrzucona)."""
    if not version_id:
        return None
    cached = _RUNTIMES.get(version_id, _MISSING)
    if cached is not _MISSING:
        return cached
    from django.core.files.storage import default_storage

    from .models import ThemeVersion

    version = (
        ThemeVersion.objects.select_related("theme")
        .filter(pk=version_id, status=ThemeVersion.Status.VALID)
        .exclude(public_prefix="")
        .first()
    )
    runtime = None
    if version is not None:
        manifest = version.manifest or {}
        generated = (version.tokens or {}).get("generated") or {}
        palette = generated.get("main") or {}
        base = default_storage.url(version.public_prefix + "__base__")
        runtime = ThemeRuntime(
            pk=version.pk,
            slug=version.theme.slug,
            name=version.theme.name,
            version=version.version,
            color_scheme=manifest.get("color_scheme", "light"),
            supports=tuple(manifest.get("supports") or ("public", "panels")),
            layouts={key: tuple(values) for key, values in (manifest.get("layouts") or {}).items()},
            templates=dict(version.templates or {}),
            css_url=version.public_url("theme.css"),
            tokens_url=version.public_url("tokens.css"),
            assets_url=base[: -len("__base__")],
            meta_color=palette.get("primary", ""),
            palette=palette,
            tokens={key: dict((version.tokens or {}).get(key) or {}) for key in TOKEN_GROUPS},
            logos=tuple(dict(entry) for entry in manifest.get("logos") or ()),
            fonts=tuple(dict(entry) for entry in manifest.get("fonts") or ()),
        )
    # Pamiętamy **wyłącznie** trafienia. Brak (wersja nieistniejąca, odrzucona) nie trafia do pamięci:
    # identyfikator przychodzi także z adresu (``/_theme/overrides.css?v=…``), więc pamiętanie
    # chybień pozwalałoby gościowi „zatruć” numer przyszłej wersji i rozdmuchać słownik bez końca.
    if runtime is not None:
        with _LOCK:
            _RUNTIMES[version_id] = runtime
    return runtime


def forget_runtime(version_id: int | None = None) -> None:
    """Czyści pamięć procesu (testy; usunięcie wersji)."""
    with _LOCK:
        if version_id is None:
            _RUNTIMES.clear()
        else:
            _RUNTIMES.pop(version_id, None)


@dataclass(frozen=True)
class ActiveTheme:
    runtime: ThemeRuntime
    layouts: dict[str, str]
    brand_accent: str = ""
    preview: bool = False
    #: Oczyszczone opcje dostosowania (THEME-02 § 2): ``scheme``, ``logo``, ``font``, ``colors``.
    options: dict = field(default_factory=dict)
    competition_id: int | None = None

    @property
    def scheme(self) -> str:
        """Schemat kolorów **efektywny** (wybór koordynatora albo ``color_scheme`` manifestu)."""
        return self.options.get("scheme") or self.runtime.color_scheme

    @property
    def logo(self) -> dict | None:
        """Wybrany wariant logo z manifestu: ``{"id", "label", "light", "dark"}`` z pełnymi adresami."""
        logos = {entry["id"]: entry for entry in self.runtime.logos}
        entry = logos.get(self.options.get("logo") or "") or (
            self.runtime.logos[0] if self.runtime.logos else None
        )
        if entry is None:
            return None
        base = self.runtime.assets_url
        return {
            "id": entry["id"],
            "label": entry.get("label", ""),
            "light": base + entry["light"] if entry.get("light") else "",
            "dark": base + entry["dark"] if entry.get("dark") else "",
        }

    @property
    def custom_css_token(self) -> str:
        """Podpisany zestaw opcji dla ``/_theme/custom.css`` – pusty, gdy opcje niczego nie zmieniają."""
        from . import customize

        if self.competition_id is None or not customize.needs_css(self.runtime, self.options):
            return ""
        return customize.sign(self.competition_id, self.runtime, self.options)

    @property
    def meta_color(self) -> str:
        from . import customize

        return customize.meta_color(self.runtime, self.options) if self.options else self.runtime.meta_color

    @property
    def context(self) -> dict:
        """Zmienna ``theme`` w szablonach slotów motywu."""
        rt = self.runtime
        return {
            "slug": rt.slug,
            "name": rt.name,
            "version": rt.version,
            "assets": rt.assets_url,
            "color_scheme": self.scheme,
            "layouts": dict(self.layouts),
            "preview": self.preview,
            "logo": self.logo,
        }


#: Opcje dostosowania (THEME-02) – zapisywane przy wersji i w ``Competition.theme_options``.
CUSTOM_KEYS = ("scheme", "logo", "font", "colors")


def clean_options(runtime: ThemeRuntime, options: dict | None) -> dict:
    """Opcje konkursu przycięte do tego, co wersja deklaruje.

    ``layouts`` i ``brand_accent`` (THEME-01) zawsze; ``scheme``/``logo``/``font``/``colors``
    (THEME-02) – tylko gdy wybór odbiega od wartości domyślnej wersji, żeby konkurs bez dostosowania
    miał w ``theme_options`` dokładnie to, co przed THEME-02.
    """
    from . import customize

    options = options or {}
    chosen = options.get("layouts") or {}
    layouts = {}
    for key, allowed in runtime.layouts.items():
        value = chosen.get(key) if isinstance(chosen, dict) else None
        layouts[key] = value if value in allowed else allowed[0]
    cleaned: dict = {"layouts": layouts, "brand_accent": bool(options.get("brand_accent"))}
    scheme = options.get("scheme")
    if scheme in customize.schemes_for(runtime) and scheme != runtime.color_scheme:
        cleaned["scheme"] = scheme
    for key, entries in (("logo", runtime.logos), ("font", runtime.fonts)):
        ids = customize.option_ids(entries)
        value = options.get(key)
        if value in ids and value != ids[0]:
            cleaned[key] = value
    colors = customize.clean_colors(runtime, options.get("colors"))
    if colors:
        cleaned["colors"] = colors
    return cleaned


def _build(runtime: ThemeRuntime, options: dict | None, competition, *, preview: bool) -> ActiveTheme:
    cleaned = clean_options(runtime, options)
    accent = ""
    if cleaned["brand_accent"] and competition is not None:
        accent = competition.accent_colour or ""
    return ActiveTheme(
        runtime=runtime,
        layouts=cleaned["layouts"],
        brand_accent=accent,
        preview=preview,
        options={key: cleaned[key] for key in CUSTOM_KEYS if key in cleaned},
        competition_id=getattr(competition, "pk", None),
    )


def make_preview_token(competition, version_id: int | None, options: dict | None) -> str:
    payload = {"c": competition.pk, "v": version_id or CLASSIC_PREVIEW, "o": options or {}}
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def _preview(request, competition, token: str):
    """``(True, ActiveTheme|None)`` gdy podgląd obowiązuje, ``(False, None)`` gdy go ignorujemy."""
    user = getattr(request, "user", None)
    if competition is None or user is None or not user.is_authenticated:
        return False, None
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.BadSignature:
        return False, None
    if not isinstance(payload, dict) or payload.get("c") != competition.pk:
        return False, None
    from apps.accounts.models import CompetitionRole
    from apps.accounts.services import has_role

    if not has_role(user, competition, CompetitionRole.COORDINATOR):
        return False, None
    version_id = payload.get("v")
    if version_id == CLASSIC_PREVIEW:
        return True, None
    runtime = runtime_for(version_id if isinstance(version_id, int) else None)
    if runtime is None:
        return False, None
    return True, _build(runtime, payload.get("o"), competition, preview=True)


#: Strony **publiczne** – jedyne, na których działają szablony slotów z paczki (przegląd 4.10.2026,
#: H2; § 0: „panele dziedziczą tokeny, ale nie szablony”). Lista dozwolona, a nie zakazana: nowy
#: ekran aplikacji jest panelem, dopóki ktoś świadomie nie dopisze go tutaj. Strony CMS to
#: ``wagtail_serve``; reszta to publiczne ekrany aplikacji bez formularzy.
PUBLIC_VIEWS = frozenset(
    {"wagtail_serve", "statistics", "web:posters", "web:results", "web:certificate-verify"}
)

#: Ekrany zarządzania motywem renderują się **zawsze** bez motywu (ani arkusza, ani tokenów):
#: zepsuty albo złośliwy motyw nie może ukryć przycisku, którym się go wyłącza.
THEME_FREE_VIEWS = frozenset(
    {
        "web:coordinator-theme",
        "web:coordinator-theme-menu",
        "web:coordinator-theme-customize",
        "web:coordinator-platform-themes",
        "web:coordinator-platform-theme",
    }
)

#: ``?theme=off`` – awaryjne wyłączenie motywu na jedno żądanie, wyłącznie dla superkoordynatora.
EMERGENCY_PARAM = "theme"


def _view_name(request) -> str:
    match = getattr(request, "resolver_match", None)
    return match.view_name if match is not None else ""


def is_public_page(request) -> bool:
    return _view_name(request) in PUBLIC_VIEWS


def _emergency_off(request) -> bool:
    if request.GET.get(EMERGENCY_PARAM) != "off":
        return False
    from apps.accounts.super_coordinator import is_super_coordinator

    return is_super_coordinator(getattr(request, "user", None))


def active_theme(request) -> ActiveTheme | None:
    """Motyw dla tego żądania (pamiętany na obiekcie żądania).

    Uwzględnia zakres z manifestu (``supports``): strona publiczna dostaje motyw, gdy wersja
    deklaruje ``public``, panel – gdy deklaruje ``panels``. Szablony slotów paczki obowiązują
    wyłącznie na stronach publicznych (:func:`package_slots_allowed`).
    """
    if request is None:
        return None
    cached = getattr(request, "_active_theme", _MISSING)
    if cached is not _MISSING:
        return cached
    competition = getattr(request, "competition", None)
    result = None
    decided = False
    token = request.GET.get(PREVIEW_PARAM) if request.method in ("GET", "HEAD") else None
    if _view_name(request) in THEME_FREE_VIEWS or (
        competition is not None
        and (getattr(competition, "theme_version_id", None) or token)
        and EMERGENCY_PARAM in request.GET
        and _emergency_off(request)
    ):
        request._active_theme = None
        request._theme_preview = False
        return None
    if token:
        decided, result = _preview(request, competition, token)
    if not decided and competition is not None and getattr(competition, "theme_version_id", None):
        runtime = runtime_for(competition.theme_version_id)
        if runtime is not None:
            result = _build(runtime, competition.theme_options, competition, preview=False)
    if result is not None:
        scope = "public" if is_public_page(request) else "panels"
        if scope not in result.runtime.supports:
            result = None
    request._active_theme = result
    request._theme_preview = bool(token and decided)
    return result


def package_slots_allowed(request) -> bool:
    """Szablony slotów z paczki – tylko na stronie publicznej (panele mają szablony aplikacji)."""
    return is_public_page(request)


def preview_active(request) -> bool:
    """Czy to żądanie jest honorowanym podglądem koordynatora (także podglądem ``classic``)."""
    active_theme(request)
    return bool(getattr(request, "_theme_preview", False))
