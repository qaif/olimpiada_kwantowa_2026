"""Render slotów motywu: osobny silnik szablonów na wersję i jeden loader „paczka → aplikacja”.

Szablon slotu z paczki renderuje silnik zbudowany **dla tej wersji** (pamięć procesu, jak
``ThemeRuntime``). Loader :class:`ThemeLoader` rozstrzyga nazwę w kolejności: szablony paczki →
alias ``classic/<slot>.html`` (domyślny slot aplikacji) → loadery aplikacji. Dzięki temu
``{% include "theme/partials/x.html" %}`` trafia do paczki, a ``{% include "web/_support_link.html" %}``
– do aplikacji, bez przepisywania nazw przy wgraniu.

**Dwa rodzaje szablonów, dwa konteksty** (przegląd 4.10.2026, H1):

- szablon **paczki** (:class:`PackageTemplate`) widzi wyłącznie kontekst z listy dozwolonej
  (``apps.themes.safe_context``) – kopie danych, bez obiektów modeli i bez obiektu żądania,
- szablon **aplikacji** dołączony przez slot (:class:`AppTemplate`) – fragment ``web/_*``,
  ``cms/_*``, ``classic/*`` – renderuje się z pełnym kontekstem strony (to kod aplikacji; menu
  języków czy slider sponsorów potrzebują żądania i ustawień), plus wartościami, które slot
  przekazał przez ``{% include … with … %}``.

Biblioteki: te same co aplikacja (fragmenty aplikacji ładują własne), z ``wagtailcore_tags``
podmienionym na ``apps.themes.safe_tags`` (``{% pageurl %}`` przyjmuje pośrednika strony).
Ograniczenie ``{% load %}`` dla szablonów paczki egzekwują lint i kompilacja ograniczonym silnikiem
przy wgraniu, a lint jeszcze raz przy każdym załadowaniu (obrona w głąb).

**Druga linia za lintem:** wynik renderu slotu paczki jest sprawdzany (``slots.check_rendered``) –
``<script>``, ``on*=``, ``javascript:`` i pokrewne w gotowym HTML-u (np. złożone z kawałków, których
lint nie skleił) dają slot domyślny. Granicą bezpieczeństwa pozostaje CSP (bez ``unsafe-inline``
dla skryptów); lint i kontrola wyniku zamykają to, co CSP przepuszcza (style, formularze, ramki).

Błąd w szablonie motywu nie może położyć strony: slot wraca wtedy do treści domyślnej aplikacji,
a wyjątek idzie do logu – **raz** na wersję i slot w procesie, żeby zepsuty motyw nie zalał logu
wpisem na każde żądanie.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.conf import settings
from django.template import Context, Engine, Origin, Template, TemplateDoesNotExist, engines
from django.template.loaders.base import Loader

from . import slots
from .runtime import ActiveTheme, ThemeRuntime
from .safe_context import curated_context

logger = logging.getLogger(__name__)

_ENGINES: dict[int, Engine] = {}
_LOCK = threading.Lock()
_LOGGED: set[tuple[int, str]] = set()

FULL_CONTEXT_KEY = "_full_context"
#: Liczba słowników kontekstu, które tworzy ``Context(values)`` (wbudowane + nasze wartości).
#: Wszystko powyżej dołożył szablon paczki (``with``, pętla, ``include … with``).
_BASE_DICTS = 2


class PackageTemplate(Template):
    """Szablon z paczki motywu – renderowany w kontekście z listy dozwolonej."""


class AppTemplate(Template):
    """Szablon aplikacji dołączony przez slot paczki – renderowany z pełnym kontekstem strony."""

    def render(self, context):
        full = context.get(FULL_CONTEXT_KEY)
        if full is None:
            return super().render(context)
        values = dict(full)
        for layer in context.dicts[_BASE_DICTS:]:
            values.update({key: value for key, value in layer.items() if not key.startswith("_")})
        fresh = Context(values, autoescape=True, use_l10n=context.use_l10n, use_tz=context.use_tz)
        return super().render(fresh)


class ThemeLoader(Loader):
    """Paczka jednej wersji, alias ``classic/…`` i loadery aplikacji – w tej kolejności."""

    def __init__(self, engine, templates: dict[str, str], version_pk: int, app_loaders: list):
        super().__init__(engine)
        self.templates = templates
        self.version_pk = version_pk
        self.app_loaders = engine.get_template_loaders(app_loaders)
        self.cache: dict[str, Template] = {}

    def get_template(self, template_name, skip=None):
        cached = self.cache.get(template_name) if skip is None else None
        if cached is not None:
            return cached
        if template_name in self.templates:
            template = self._package_template(template_name)
        else:
            template = self._app_template(template_name, skip)
        if skip is None:
            self.cache[template_name] = template
        return template

    def _package_template(self, name: str) -> Template:
        source = self.templates[name]
        origin = Origin(name=f"theme:{self.version_pk}:{name}", template_name=name, loader=self)
        problems = slots.lint_template(name, source, package_templates=set(self.templates))
        if problems:
            logger.error("Szablon motywu odrzucony przy ładowaniu: %s", problems)
            raise TemplateDoesNotExist(name)
        return PackageTemplate(source, origin, name, self.engine)

    def _app_template(self, name: str, skip) -> Template:
        lookup = name
        if name.startswith("classic/"):
            rest = name[len("classic/") :]
            if ".." in rest or "\\" in rest:
                raise TemplateDoesNotExist(name)
            path = Path(settings.BASE_DIR) / "templates" / "theme" / rest
            if not path.is_file():
                raise TemplateDoesNotExist(name)
            origin = Origin(name=str(path), template_name=name, loader=self)
            return AppTemplate(path.read_text(encoding="utf-8"), origin, name, self.engine)
        for loader in self.app_loaders:
            try:
                found = loader.get_template(lookup, skip=skip)
            except TemplateDoesNotExist:
                continue
            return AppTemplate(found.source, found.origin, name, self.engine)
        raise TemplateDoesNotExist(name)

    def get_template_sources(self, template_name):  # pragma: no cover - get_template nadpisane
        return iter(())

    def reset(self):
        self.cache.clear()


def _app_loaders(main: Engine) -> list:
    loaders = list(main.loaders)
    if (
        len(loaders) == 1
        and isinstance(loaders[0], (tuple, list))
        and loaders[0][0].endswith("cached.Loader")
    ):
        return list(loaders[0][1])
    return loaders


def engine_for(runtime: ThemeRuntime) -> Engine:
    engine = _ENGINES.get(runtime.pk)
    if engine is not None:
        return engine
    main = engines["django"].engine
    libraries = {**main.libraries, "wagtailcore_tags": "apps.themes.safe_tags"}
    engine = Engine(
        dirs=main.dirs,
        app_dirs=False,
        context_processors=[],
        debug=False,
        loaders=[("apps.themes.rendering.ThemeLoader", runtime.templates, runtime.pk, _app_loaders(main))],
        string_if_invalid="",
        file_charset="utf-8",
        libraries=libraries,
        builtins=None,
        autoescape=True,
    )
    with _LOCK:
        _ENGINES[runtime.pk] = engine
    return engine


def forget_engines() -> None:
    with _LOCK:
        _ENGINES.clear()
        _LOGGED.clear()


def _log_once(theme: ActiveTheme, name: str, message: str, *, exc_info: bool) -> None:
    key = (theme.runtime.pk, name)
    if key in _LOGGED:
        return
    with _LOCK:
        _LOGGED.add(key)
    logger.error(
        "Slot %s motywu %s (wersja %s): %s – do restartu procesu bez kolejnych wpisów.",
        name,
        theme.runtime.slug,
        theme.runtime.pk,
        message,
        exc_info=exc_info,
    )


def render_theme_template(theme: ActiveTheme, name: str, context, extra: dict | None = None) -> str | None:
    """Treść szablonu paczki albo ``None`` (brak nadpisania albo błąd – slot domyślny)."""
    if name not in theme.runtime.templates:
        return None
    request = context.get("request")
    try:
        template = engine_for(theme.runtime).get_template(name)
        full = context.flatten()
        if extra:
            full.update(extra)
        values = curated_context(full, request)
        values["theme"] = theme.context
        if extra:
            values.update(extra)
        values[FULL_CONTEXT_KEY] = full
        # Świeży ``Context`` zamiast bieżącego: ``{% include %}`` szuka szablonów silnikiem
        # szablonu, z którym związany jest kontekst – z kontekstem strony szukałby w silniku
        # aplikacji, a tam nie ma szablonów paczki.
        fresh = Context(values, autoescape=True, use_l10n=context.use_l10n, use_tz=context.use_tz)
        rendered = template.render(fresh)
    except Exception:  # noqa: BLE001 - motyw nie może położyć strony
        _log_once(theme, name, "błąd renderu", exc_info=True)
        return None
    # Treść strony podana jako ``slot_content`` należy do aplikacji (np. osadzony film w treści
    # CMS) – sprawdzamy wyłącznie to, co dołożył szablon paczki.
    checked = rendered
    slot_content = (extra or {}).get("slot_content")
    if slot_content:
        checked = checked.replace(str(slot_content), "")
    problem = slots.check_rendered(checked)
    if problem:
        _log_once(theme, name, f"wynik zawiera niedozwolony fragment {problem!r}", exc_info=False)
        return None
    return rendered
