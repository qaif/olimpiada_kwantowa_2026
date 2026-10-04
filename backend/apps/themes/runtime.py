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
        )
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

    @property
    def context(self) -> dict:
        """Zmienna ``theme`` w szablonach slotów motywu."""
        rt = self.runtime
        return {
            "slug": rt.slug,
            "name": rt.name,
            "version": rt.version,
            "assets": rt.assets_url,
            "color_scheme": rt.color_scheme,
            "layouts": dict(self.layouts),
            "preview": self.preview,
        }


def clean_options(runtime: ThemeRuntime, options: dict | None) -> dict:
    """Opcje konkursu przycięte do tego, co wersja deklaruje (``layouts`` + ``brand_accent``)."""
    options = options or {}
    chosen = options.get("layouts") or {}
    layouts = {}
    for key, allowed in runtime.layouts.items():
        value = chosen.get(key) if isinstance(chosen, dict) else None
        layouts[key] = value if value in allowed else allowed[0]
    return {"layouts": layouts, "brand_accent": bool(options.get("brand_accent"))}


def _build(runtime: ThemeRuntime, options: dict | None, competition, *, preview: bool) -> ActiveTheme:
    cleaned = clean_options(runtime, options)
    accent = ""
    if cleaned["brand_accent"] and competition is not None:
        accent = competition.accent_colour or ""
    return ActiveTheme(runtime=runtime, layouts=cleaned["layouts"], brand_accent=accent, preview=preview)


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


def active_theme(request) -> ActiveTheme | None:
    """Motyw dla tego żądania (pamiętany na obiekcie żądania)."""
    if request is None:
        return None
    cached = getattr(request, "_active_theme", _MISSING)
    if cached is not _MISSING:
        return cached
    competition = getattr(request, "competition", None)
    result = None
    decided = False
    token = request.GET.get(PREVIEW_PARAM) if request.method in ("GET", "HEAD") else None
    if token:
        decided, result = _preview(request, competition, token)
    if not decided and competition is not None and getattr(competition, "theme_version_id", None):
        runtime = runtime_for(competition.theme_version_id)
        if runtime is not None:
            result = _build(runtime, competition.theme_options, competition, preview=False)
    request._active_theme = result
    request._theme_preview = bool(token and decided)
    return result


def preview_active(request) -> bool:
    """Czy to żądanie jest honorowanym podglądem koordynatora (także podglądem ``classic``)."""
    active_theme(request)
    return bool(getattr(request, "_theme_preview", False))
