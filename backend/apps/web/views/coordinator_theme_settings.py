"""Zarządzanie motywem bez paczki (THEME-02): menu serwisu i dostosowanie wersji motywu.

Dwa ekrany pod „Motywem serwisu” (``/coordinator/competition/theme/``), z tymi samymi regułami:
koordynator konkursu wskazanego **domeną** żądania (nie ma adresu z identyfikatorem cudzego
konkursu), za flagą ``themes`` (wyłączona = 404), bez motywu na samym ekranie
(``runtime.THEME_FREE_VIEWS`` – zepsute kolory nie mogą ukryć przycisku „Przywróć domyślne”),
POST-y z limitem ``theme_settings`` (per konto). Reguły – walidacja, kontrast, zapis, audyt,
unieważnienie cache gościa – są w ``apps.themes.services`` / ``menu`` / ``customize``.

Oraz arkusz ``/_theme/custom.css`` – kolory/schemat/kroje z podpisanego zestawu opcji.
"""

from __future__ import annotations

import copy

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import get_script_prefix, reverse
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.themes import customize, services
from apps.themes import menu as menu_mod
from apps.themes.models import SiteMenu, ThemeVersion
from apps.themes.runtime import PREVIEW_PARAM, clean_options, make_preview_token, runtime_for
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

from .coordinator_themes import FEATURE, LAYOUT_LABELS

LANGUAGE_NAMES = dict(settings.LANGUAGES)


class ThemeSettingsMixin(CoordinatorRequiredMixin, ThrottledFormMixin):
    """Koordynator konkursu żądania, flaga ``themes``, limit POST-ów."""

    throttle_scope = "theme_settings"

    def competition_or_404(self, request):
        competition = request.competition
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ekran motywu jest w tym konkursie wyłączony.")
        return competition


# --- menu serwisu ---------------------------------------------------------------------------------


def default_menu(request) -> list[dict]:
    """Menu **bez** nadpisań, tak jak widzi je gość (z pozycją „Dla szkół/nauczycieli”).

    Kopia żądania z gościem zamiast koordynatora: pozycja dla nauczycieli istnieje tylko dla
    niezalogowanych, a koordynator ma móc ją ukryć albo przemianować.
    """
    from apps.cms.context_processors import cms_menu

    proxy = copy.copy(request)
    proxy.user = AnonymousUser()
    proxy._skip_menu_overrides = True
    return cms_menu(proxy)["cms_menu"]


def _auto_info(menu: list[dict]) -> dict[str, dict]:
    return {
        item["key"]: {
            "title": item["title"],
            "url": item["url"],
            "home": bool(item.get("home")),
            "has_children": bool(item.get("children")),
        }
        for item in menu
    }


def _rows(stored: list[dict], auto: dict[str, dict], default_keys: list[str]) -> list[dict]:
    """Wiersze edytora: zapisane wpisy w ich kolejności, potem pozycje domyślne spoza zapisu."""
    rows = []
    seen = set()
    for entry in stored:
        if entry.get("type", menu_mod.TYPE_AUTO) == menu_mod.TYPE_AUTO and entry["key"] not in auto:
            continue
        rows.append({**entry, **({"info": auto[entry["key"]]} if entry["key"] in auto else {})})
        seen.add(entry["key"])
    for key in default_keys:
        if key not in seen:
            rows.append(
                {
                    "key": key,
                    "type": menu_mod.TYPE_AUTO,
                    "hidden": False,
                    "labels": {},
                    "parent": "",
                    "info": auto[key],
                }
            )
    return rows


class CompetitionMenuView(ThemeSettingsMixin, View):
    """``GET|POST /coordinator/competition/theme/menu/`` – kolejność, ukrycie, etykiety, własne pozycje."""

    template_name = "web/coordinator/competition_theme_menu.html"

    def get(self, request):
        competition = self.competition_or_404(request)
        menu, auto, default_keys = self._defaults(request)
        stored = list(getattr(SiteMenu.objects.filter(competition=competition).first(), "items", []) or [])
        return self._render(request, competition, _rows(stored, auto, default_keys))

    def post(self, request):
        competition = self.competition_or_404(request)
        _menu, auto, default_keys = self._defaults(request)
        action = request.POST.get("action", "")
        if action == "reset":
            services.reset_menu(competition, actor=request.user, request=request)
            messages.success(request, _("Przywrócono menu domyślne."))
            return redirect(reverse("web:coordinator-theme-menu"))
        stored = list(getattr(SiteMenu.objects.filter(competition=competition).first(), "items", []) or [])
        rows = _rows(stored, auto, default_keys)
        try:
            if action == "add_link":
                rows = [*rows, self._new_link(request, competition)]
            elif action == "add_group":
                rows = [*rows, self._new_group(request, competition)]
            elif action == "save" or request.POST.get("move"):
                rows = self._posted_rows(request, rows)
            else:
                raise Http404
            services.save_menu(
                competition,
                [self._entry(row) for row in rows],
                auto_keys=auto,
                default_keys=default_keys,
                actor=request.user,
                request=request,
            )
        except menu_mod.MenuError as exc:
            messages.error(request, _("Menu nie zostało zapisane: %(error)s") % {"error": exc})
            # Wiersze z formularza wracają na ekran – poprawka nie wymaga wpisywania wszystkiego od nowa.
            return self._render(request, competition, rows, status=400)
        messages.success(
            request, _("Zapisano menu serwisu. Zmiana obowiązuje od razu na wszystkich stronach.")
        )
        return redirect(reverse("web:coordinator-theme-menu"))

    # -- pomocnicze --

    @staticmethod
    def _defaults(request):
        menu = default_menu(request)
        auto = _auto_info(menu)
        return menu, auto, [item["key"] for item in menu]

    @staticmethod
    def _entry(row: dict) -> dict:
        return {
            key: row.get(key)
            for key in ("key", "type", "hidden", "labels", "parent", "url", "page", "new_tab")
        }

    @staticmethod
    def _labels(request, competition, prefix: str) -> dict[str, str]:
        return {
            code: request.POST.get(f"{prefix}{code}", "")
            for code in competition.ui_languages
            if request.POST.get(f"{prefix}{code}", "").strip()
        }

    def _new_link(self, request, competition) -> dict:
        page = request.POST.get("new_page", "")
        return {
            "key": menu_mod.new_key("link"),
            "type": menu_mod.TYPE_LINK,
            "hidden": False,
            "labels": self._labels(request, competition, "new_label_"),
            "parent": "",
            "page": page or None,
            "url": "" if page else request.POST.get("new_url", ""),
            "new_tab": request.POST.get("new_tab") == "on",
        }

    def _new_group(self, request, competition) -> dict:
        return {
            "key": menu_mod.new_key("group"),
            "type": menu_mod.TYPE_GROUP,
            "hidden": False,
            "labels": self._labels(request, competition, "group_label_"),
            "parent": "",
        }

    def _posted_rows(self, request, current: list[dict]) -> list[dict]:
        """Wiersze z formularza: kolejność z pól „pozycja” (i przycisków w górę/w dół), usunięcia.

        Wartości z POST-u dotyczą **wyłącznie** wierszy wymienionych w ukrytych polach ``row_keys``
        (przegląd THEME-02, M1). Formularz otwarty przed dodaniem strony w /cms/ albo przed zapisem
        w drugiej karcie nie ma pól nowego wiersza – brak pola „Widoczna” nie może go ukryć, a brak
        etykiet nie może skasować etykiet zapisanych w międzyczasie. Takie wiersze zostają bez zmian.
        """
        competition = request.competition
        in_form = set(request.POST.getlist("row_keys"))
        rows = []
        for index, row in enumerate(current):
            key = row["key"]
            if key not in in_form:
                rows.append({**row, "_order": (index + 1, index)})
                continue
            if row["type"] != menu_mod.TYPE_AUTO and request.POST.get(f"delete_{key}") == "on":
                continue
            order = menu_mod.ascii_int(request.POST.get(f"order_{key}"))
            if order is None:
                order = index + 1
            updated = {
                **row,
                "hidden": request.POST.get(f"visible_{key}") != "on",
                "labels": self._labels(request, competition, f"label_{key}_"),
                "parent": request.POST.get(f"parent_{key}", ""),
                "_order": (order, index),
            }
            if row["type"] == menu_mod.TYPE_LINK:
                page = request.POST.get(f"page_{key}", "")
                updated["page"] = page or None
                updated["url"] = "" if page else request.POST.get(f"url_{key}", "")
                updated["new_tab"] = request.POST.get(f"newtab_{key}") == "on"
            rows.append(updated)
        rows.sort(key=lambda row: row.pop("_order"))
        move = request.POST.get("move", "")
        key, _sep, direction = move.rpartition(":")
        positions = [row["key"] for row in rows]
        if key in positions and direction in ("up", "down"):
            i = positions.index(key)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(rows):
                rows[i], rows[j] = rows[j], rows[i]
        # Usunięta grupa nie może zostawić osieroconych członków – wracają na najwyższy poziom.
        groups = {row["key"] for row in rows if row["type"] == menu_mod.TYPE_GROUP}
        for row in rows:
            if row.get("parent") and row["parent"] not in groups:
                row["parent"] = ""
        return rows

    def _render(self, request, competition, rows: list[dict], *, status: int = 200):
        languages = [(code, LANGUAGE_NAMES.get(code, code)) for code in competition.ui_languages]
        groups = [
            {
                "key": row["key"],
                "label": menu_mod._pick_label(row.get("labels") or {}, "", competition.ui_languages),
            }
            for row in rows
            if row["type"] == menu_mod.TYPE_GROUP
        ]
        pages = [
            {"pk": page.pk, "title": page.title, "indent": "· " * max(0, page.depth - 2)}
            for page in menu_mod.site_pages(competition)[:300]
        ]
        page_ids = {page["pk"] for page in pages}
        for index, row in enumerate(rows):
            row["position"] = index + 1
            if isinstance(row.get("page"), str):
                row["page"] = menu_mod.ascii_int(row["page"])
            # Zapisana strona, której nie ma już na liście (wycofana z publikacji): zostaje wybrana
            # w formularzu jako „(niedostępna strona)”, żeby zapis tabeli jej po cichu nie zgubił.
            row["page_missing"] = bool(row.get("page")) and row["page"] not in page_ids
            row["label_fields"] = [
                {"code": code, "name": name, "value": (row.get("labels") or {}).get(code, "")}
                for code, name in languages
            ]
            info = row.get("info") or {}
            row["groupable"] = (
                row["type"] != menu_mod.TYPE_GROUP and not info.get("home") and not info.get("has_children")
            )
        context = {
            "competition": competition,
            "rows": rows,
            "languages": languages,
            "groups": groups,
            "pages": pages,
            "has_overrides": SiteMenu.objects.filter(competition=competition).exists(),
            "limits": {
                "entries": menu_mod.MAX_ENTRIES,
                "links": menu_mod.MAX_LINKS,
                "groups": menu_mod.MAX_GROUPS,
            },
        }
        return TemplateResponse(request, self.template_name, context, status=status)


# --- dostosowanie motywu --------------------------------------------------------------------------


class CompetitionThemeCustomizeView(ThemeSettingsMixin, View):
    """``GET|POST /coordinator/competition/theme/customize/[?version=<pk>]`` – kolory i opcje wersji."""

    template_name = "web/coordinator/competition_theme_customize.html"

    def _version(self, request, competition) -> ThemeVersion | None:
        raw = request.GET.get("version") or request.POST.get("version") or ""
        if raw:
            pk = menu_mod.ascii_int(raw)
            if pk is None:
                raise Http404
            return get_object_or_404(ThemeVersion, pk=pk, status=ThemeVersion.Status.VALID)
        if competition.theme_version_id is None:
            return None
        return ThemeVersion.objects.filter(
            pk=competition.theme_version_id, status=ThemeVersion.Status.VALID
        ).first()

    def get(self, request):
        competition = self.competition_or_404(request)
        version = self._version(request, competition)
        if version is None:
            return self._render(request, competition, None, None, {})
        runtime = runtime_for(version.pk)
        if runtime is None:
            raise Http404
        options = clean_options(runtime, self._saved(competition, version))
        return self._render(request, competition, version, runtime, options)

    def post(self, request):
        competition = self.competition_or_404(request)
        version = self._version(request, competition)
        if version is None:
            raise Http404
        runtime = runtime_for(version.pk)
        if runtime is None:
            raise Http404
        action = request.POST.get("action", "")
        url = reverse("web:coordinator-theme-customize") + f"?version={version.pk}"
        if action == "reset":
            services.reset_customization(competition, version, actor=request.user, request=request)
            messages.success(request, _("Przywrócono kolory i opcje domyślne motywu."))
            return redirect(url)
        if action not in ("save", "preview"):
            raise Http404
        saved = self._saved(competition, version)
        posted = self._posted(request, runtime)
        # Formularz pokazuje palety **wybranego** schematu – paleta, której pól nie było, zachowuje
        # zapisane nadpisania (zapis w schemacie ciemnym nie kasuje kolorów jasnych).
        posted["colors"] = {**(saved.get("colors") or {}), **posted["colors"]}
        options = clean_options(runtime, {**saved, **posted})
        errors, _warnings = services.customization_report(runtime, options)
        if errors:
            for error in errors:
                messages.error(request, error)
            return self._render(request, competition, version, runtime, options, status=400)
        if action == "preview":
            token = make_preview_token(competition, version.pk, options)
            # Cel podglądu nie przychodzi z formularza – zawsze strona główna tego konkursu.
            return redirect(f"{get_script_prefix()}?{PREVIEW_PARAM}={token}")
        try:
            services.save_customization(competition, version, options, actor=request.user, request=request)
        except customize.CustomizationError as exc:  # pragma: no cover - wyścig: raport wyżej był czysty
            for error in exc.messages:
                messages.error(request, error)
            return self._render(request, competition, version, runtime, options, status=400)
        if competition.theme_version_id == version.pk:
            messages.success(
                request, _("Zapisano wygląd motywu. Zmiana obowiązuje od razu na wszystkich stronach.")
            )
        else:
            messages.success(
                request,
                _("Zapisano wygląd tej wersji motywu – zacznie obowiązywać po jej aktywacji w galerii."),
            )
        return redirect(url)

    # -- pomocnicze --

    @staticmethod
    def _saved(competition, version) -> dict:
        """Opcje zapisane dla wersji: aktywnej – z konkursu, nieaktywnej – z ``ThemeCustomization``."""
        if competition.theme_version_id == version.pk:
            return dict(competition.theme_options or {})
        return services.with_customization(competition, version, {})

    @staticmethod
    def _posted(request, runtime) -> dict:
        options: dict = {
            "layouts": {
                key: request.POST.get(f"layout_{key}", "")
                for key in runtime.layouts
                if request.POST.get(f"layout_{key}")
            },
        }
        for key in ("scheme", "logo", "font"):
            if request.POST.get(key):
                options[key] = request.POST[key]
        colors: dict[str, dict[str, str]] = {}
        for mode in ("light", "dark"):
            fields = {
                token: request.POST.get(f"color_{mode}_{token}")
                for token in customize.editable_colors(runtime, mode)
                if f"color_{mode}_{token}" in request.POST
            }
            if fields:
                # Paleta obecna w formularzu – zastępuje zapisaną w całości (także „wróć do domyślnego”).
                colors[mode] = {token: value for token, value in fields.items() if value}
        options["colors"] = colors
        options["radius"] = {
            name: request.POST.get(f"radius_{name}", "")
            for name in customize.editable_radii(runtime)
            if request.POST.get(f"radius_{name}")
        }
        return options

    def _render(self, request, competition, version, runtime, options, *, status: int = 200):
        context = {"competition": competition, "version": version}
        if runtime is not None:
            scheme = options.get("scheme") or runtime.color_scheme
            errors, warnings = services.customization_report(runtime, options)
            palettes = []
            for mode in customize.palette_modes(runtime, scheme):
                overrides = (options.get("colors") or {}).get(mode) or {}
                palettes.append(
                    {
                        "mode": mode,
                        "label": _("Paleta ciemna") if mode == "dark" else _("Paleta jasna"),
                        "colors": [
                            {
                                "token": token,
                                "default": customize.default_hex(value),
                                "value": overrides.get(token) or customize.default_hex(value),
                                "changed": token in overrides,
                            }
                            for token, value in customize.editable_colors(runtime, mode).items()
                        ],
                    }
                )
            layouts = [
                {
                    "key": key,
                    "label": LAYOUT_LABELS.get(key, key),
                    "options": allowed,
                    "selected": (options.get("layouts") or {}).get(key) or allowed[0],
                }
                for key, allowed in runtime.layouts.items()
            ]
            context.update(
                {
                    "runtime": runtime,
                    "active": competition.theme_version_id == version.pk,
                    "schemes": customize.schemes_for(runtime),
                    "scheme": scheme,
                    "logos": runtime.logos,
                    "logo": options.get("logo") or (runtime.logos[0]["id"] if runtime.logos else ""),
                    "logo_base": runtime.assets_url,
                    "fonts": runtime.fonts,
                    "font": options.get("font") or (runtime.fonts[0]["id"] if runtime.fonts else ""),
                    "layouts": layouts,
                    "palettes": palettes,
                    "radii": [
                        {
                            "name": name,
                            "default": value,
                            "value": (options.get("radius") or {}).get(name, value),
                        }
                        for name, value in customize.editable_radii(runtime).items()
                    ],
                    "contrast_errors": errors,
                    "contrast_warnings": warnings,
                }
            )
        return TemplateResponse(request, self.template_name, context, status=status)


def theme_custom_css(request):
    """``GET /_theme/custom.css?s=<podpis>`` – arkusz dostosowania motywu (THEME-02 § 2.3).

    Podpis obejmuje konkurs, wersję i opcje; widok sprawdza go, porównuje konkurs z konkursem
    żądania (arkusz konkursu A nie odpowiada pod domeną konkursu B), oczyszcza opcje jeszcze raz
    i generuje arkusz. Ten sam adres = te same opcje, więc odpowiedź jest niezmienna.
    """
    competition = getattr(request, "competition", None)
    payload = customize.unsign(request.GET.get("s", ""))
    if competition is None or payload is None or payload.get("c") != competition.pk:
        raise Http404
    version_id = payload.get("v")
    runtime = runtime_for(version_id if isinstance(version_id, int) else None)
    if runtime is None:
        raise Http404
    options = clean_options(
        runtime,
        {
            "scheme": payload.get("s"),
            "font": payload.get("f"),
            "colors": payload.get("k"),
            "radius": payload.get("r"),
        },
    )
    response = HttpResponse(customize.css_for(runtime, options), content_type="text/css; charset=utf-8")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
