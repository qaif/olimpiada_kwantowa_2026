"""Render slotów motywu: osobny silnik szablonów na wersję i loader szablonów z paczki.

Szablon slotu z paczki renderuje silnik zbudowany **dla tej wersji** (pamięć procesu, jak
``ThemeRuntime``), z łańcuchem loaderów: szablony paczki → alias ``classic/<slot>.html`` (domyślny
slot aplikacji) → loadery aplikacji. Dzięki temu ``{% include "theme/partials/x.html" %}`` w paczce
trafia do paczki, a ``{% include "web/_support_link.html" %}`` – do aplikacji, bez przepisywania
nazw przy wgraniu. Silnik ma te same biblioteki co aplikacja (fragmenty aplikacji dołączane przez
slot ładują własne biblioteki), a ograniczenie ``{% load %}`` dla **szablonów paczki** pilnuje lint,
wołany przy wgraniu i ponownie przez :class:`PackageLoader` przy każdym załadowaniu (obrona
w głąb: szablon, który ominąłby walidację wgrania, i tak się nie załaduje).

Błąd w szablonie motywu nie może położyć strony: slot wraca wtedy do treści domyślnej aplikacji,
a wyjątek idzie do logu.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.conf import settings
from django.template import Context, Engine, Origin, TemplateDoesNotExist, engines
from django.template.loaders.base import Loader

from . import slots
from .runtime import ActiveTheme, ThemeRuntime

logger = logging.getLogger(__name__)

_ENGINES: dict[int, Engine] = {}
_LOCK = threading.Lock()


class PackageLoader(Loader):
    """Szablony jednej wersji motywu (z ``ThemeVersion.templates``)."""

    def __init__(self, engine, templates: dict[str, str], version_pk: int):
        super().__init__(engine)
        self.templates = templates
        self.version_pk = version_pk

    def get_template_sources(self, template_name):
        if template_name in self.templates:
            yield Origin(
                name=f"theme:{self.version_pk}:{template_name}", template_name=template_name, loader=self
            )

    def get_contents(self, origin):
        source = self.templates.get(origin.template_name)
        if source is None:
            raise TemplateDoesNotExist(origin)
        problems = slots.lint_template(origin.template_name, source, package_templates=set(self.templates))
        if problems:
            logger.error("Szablon motywu odrzucony przy ładowaniu: %s", problems)
            raise TemplateDoesNotExist(origin)
        return source


class ClassicAliasLoader(Loader):
    """``classic/<slot>.html`` → domyślny slot aplikacji ``templates/theme/<slot>.html``."""

    prefix = "classic/"

    def get_template_sources(self, template_name):
        if not template_name.startswith(self.prefix):
            return
        rest = template_name[len(self.prefix) :]
        if "/" in rest.replace("partials/", "", 1) or ".." in rest:
            return
        path = Path(settings.BASE_DIR) / "templates" / "theme" / rest
        yield Origin(name=str(path), template_name=template_name, loader=self)

    def get_contents(self, origin):
        try:
            return Path(origin.name).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TemplateDoesNotExist(origin) from exc


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
    engine = Engine(
        dirs=main.dirs,
        app_dirs=False,
        context_processors=[],
        debug=False,
        loaders=[
            (
                "django.template.loaders.cached.Loader",
                [
                    ("apps.themes.rendering.PackageLoader", runtime.templates, runtime.pk),
                    "apps.themes.rendering.ClassicAliasLoader",
                    *_app_loaders(main),
                ],
            )
        ],
        string_if_invalid="",
        file_charset="utf-8",
        libraries=main.libraries,
        builtins=None,
        autoescape=True,
    )
    with _LOCK:
        _ENGINES[runtime.pk] = engine
    return engine


def forget_engines() -> None:
    with _LOCK:
        _ENGINES.clear()


def render_theme_template(theme: ActiveTheme, name: str, context, extra: dict | None = None) -> str | None:
    """Treść szablonu paczki albo ``None`` (brak nadpisania albo błąd – slot domyślny)."""
    if name not in theme.runtime.templates:
        return None
    try:
        template = engine_for(theme.runtime).get_template(name)
        # Świeży ``Context`` zamiast bieżącego: ``{% include %}`` szuka szablonów silnikiem
        # szablonu, z którym związany jest kontekst – z kontekstem strony szukałby w silniku
        # aplikacji, a tam nie ma szablonów paczki.
        values = context.flatten()
        values["theme"] = theme.context
        if extra:
            values.update(extra)
        fresh = Context(values, autoescape=True, use_l10n=context.use_l10n, use_tz=context.use_tz)
        return template.render(fresh)
    except Exception:  # noqa: BLE001 - motyw nie może położyć strony
        logger.exception(
            "Slot %s motywu %s (wersja %s) nie wyrenderował się.", name, theme.runtime.slug, theme.runtime.pk
        )
        return None
