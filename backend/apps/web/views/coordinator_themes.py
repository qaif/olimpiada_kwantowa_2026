"""Motywy wizualne w panelu (THEME-01 § 6).

Dwa ekrany o dwóch różnych właścicielach:

- ``/coordinator/platform/themes/`` – **katalog** motywów platformy: wgranie paczki, raport
  walidacji, wersje, usunięcie nieużywanej wersji. Wyłącznie superkoordynator (operator platformy):
  paczka publikuje pliki w buckecie wspólnym dla wszystkich konkursów, więc decyzja o niej nie
  należy do organizatora jednego konkursu. Zwykły koordynator dostaje 403 (rola jest, ale nie ta).
- ``/coordinator/competition/theme/`` – **wybór** motywu konkursu: galeria ze zrzutami, warianty
  układów, akcent marki, podgląd i „Aktywuj”. Koordynator konkursu żądania, za flagą ``themes``
  (wyłączona = 404 i menu bez zmian). Konkurs bierzemy z domeny żądania – nie ma tu adresu,
  pod którym dałoby się zmienić motyw cudzego konkursu.

Reguły (walidacja, publikacja, audyt) są w ``apps.themes.services`` – widoki tylko je wołają.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import get_script_prefix, reverse
from django.views.generic import View

from apps.accounts.models import CompetitionRole
from apps.accounts.services import has_role
from apps.accounts.super_coordinator import is_super_coordinator
from apps.themes import services
from apps.themes.models import CLASSIC_SLUG, Theme, ThemeVersion
from apps.themes.package import LAYOUT_VOCABULARY, MAX_PACKAGE_BYTES
from apps.themes.runtime import PREVIEW_PARAM, make_preview_token, runtime_for
from apps.themes.slots import SLOTS
from apps.themes.tokens import HEX, accent_override_css
from apps.web.mixins import CoordinatorRequiredMixin

FEATURE = "themes"

LAYOUT_LABELS = {
    "header": "Nagłówek",
    "home_hero": "Plansza strony głównej",
    "footer": "Stopka",
    "cards": "Karty",
}


class ThemeUploadForm(forms.Form):
    package = forms.FileField(
        label="Paczka motywu (.zip)",
        help_text=f"Najwyżej {MAX_PACKAGE_BYTES // 1024 // 1024} MB. Format: docs/tasks/THEME-01.md § 1.",
    )

    def clean_package(self):
        upload = self.cleaned_data["package"]
        if upload.size > MAX_PACKAGE_BYTES:
            raise forms.ValidationError(f"Paczka jest większa niż {MAX_PACKAGE_BYTES // 1024 // 1024} MB.")
        return upload


class SuperCoordinatorMixin(CoordinatorRequiredMixin):
    """Koordynator **i** superkoordynator – operator katalogu motywów platformy."""

    role_denied_message = "Katalog motywów platformy prowadzi wyłącznie superkoordynator."

    def has_role(self, user) -> bool:
        return super().has_role(user) and is_super_coordinator(user)


class PlatformThemesView(SuperCoordinatorMixin, View):
    """``GET|POST /coordinator/platform/themes/`` – katalog i wgrywanie paczek."""

    template_name = "web/coordinator/platform_themes.html"

    def get(self, request):
        return self._render(request, ThemeUploadForm())

    def post(self, request):
        form = ThemeUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, form, status=400)
        data = form.cleaned_data["package"].read()
        version, result = services.install_package(data, actor=request.user, request=request)
        if version is None:
            for error in result.errors[:20]:
                form.add_error("package", error)
            return self._render(request, form, status=400)
        if version.is_valid:
            messages.success(
                request,
                f"Wgrano motyw „{version.theme.name}” {version.version}"
                + (f" ({len(result.warnings)} ostrzeżeń w raporcie)." if result.warnings else "."),
            )
        else:
            messages.error(
                request,
                f"Paczka „{version.theme.slug}” {version.version} odrzucona: "
                f"{len(result.errors)} błędów (raport niżej).",
            )
        return redirect(reverse("web:coordinator-platform-theme", args=[version.pk]))

    def _render(self, request, form, *, status=200):
        themes = Theme.objects.prefetch_related("versions").order_by("-is_builtin", "name")
        used = _usage()
        return TemplateResponse(
            request,
            self.template_name,
            {"form": form, "themes": themes, "used": used},
            status=status,
        )


def _usage() -> dict[int, list[str]]:
    from apps.tenancy.models import Competition

    usage: dict[int, list[str]] = {}
    for version_id, name in Competition.objects.exclude(theme_version=None).values_list(
        "theme_version_id", "name"
    ):
        usage.setdefault(version_id, []).append(name)
    return usage


class PlatformThemeVersionView(SuperCoordinatorMixin, View):
    """``GET|POST /coordinator/platform/themes/<pk>/`` – raport walidacji, pliki, usunięcie."""

    template_name = "web/coordinator/platform_theme_version.html"

    def get(self, request, pk: int):
        version = get_object_or_404(ThemeVersion.objects.select_related("theme", "uploaded_by"), pk=pk)
        context = {
            "version": version,
            "used_by": _usage().get(version.pk, []),
            "slots": [
                (f"theme/{name}.html", label, f"theme/{name}.html" in version.templates)
                for name, label in SLOTS.items()
            ],
        }
        return TemplateResponse(request, self.template_name, context)

    def post(self, request, pk: int):
        version = get_object_or_404(ThemeVersion.objects.select_related("theme"), pk=pk)
        if request.POST.get("action") != "delete":
            raise Http404
        label = f"„{version.theme.name}” {version.version}"
        try:
            services.delete_version(version, actor=request.user, request=request)
        except services.ThemeError as exc:
            messages.error(request, str(exc))
            return redirect(reverse("web:coordinator-platform-theme", args=[pk]))
        messages.success(request, f"Usunięto wersję {label}.")
        return redirect(reverse("web:coordinator-platform-themes"))


class CompetitionThemeView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/competition/theme/`` – wybór, podgląd i aktywacja motywu konkursu."""

    template_name = "web/coordinator/competition_theme.html"

    def _competition(self, request):
        competition = request.competition
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ekran motywu jest w tym konkursie wyłączony.")
        return competition

    def get(self, request):
        competition = self._competition(request)
        context = {**self._context(competition), "is_super": is_super_coordinator(request.user)}
        return TemplateResponse(request, self.template_name, context)

    def post(self, request):
        competition = self._competition(request)
        choice = request.POST.get("version", "")
        version = None
        if choice != CLASSIC_SLUG:
            if not choice.isdigit():
                raise Http404("Nieznany motyw.")
            version = get_object_or_404(ThemeVersion, pk=int(choice), status=ThemeVersion.Status.VALID)
        options = {
            "layouts": {
                key: request.POST.get(f"layout_{key}", "")
                for key in LAYOUT_VOCABULARY
                if request.POST.get(f"layout_{key}")
            },
            "brand_accent": request.POST.get("brand_accent") == "on",
        }
        action = request.POST.get("action")
        if action == "preview":
            token = make_preview_token(competition, version.pk if version else None, options)
            # Zawsze strona główna **tego** konkursu – adres celu nie przychodzi z formularza,
            # więc przycisk podglądu nie jest przekierowaniem pod dowolny adres.
            return redirect(f"{get_script_prefix()}?{PREVIEW_PARAM}={token}")
        if action != "activate":
            raise Http404
        try:
            services.activate(competition, version, options, actor=request.user, request=request)
        except services.ThemeError as exc:
            messages.error(request, str(exc))
            return redirect(reverse("web:coordinator-theme"))
        name = f"„{version.theme.name}” {version.version}" if version else "„Klasyczny” (wbudowany)"
        messages.success(
            request, f"Aktywowano motyw {name}. Zmiana obowiązuje od razu na wszystkich stronach konkursu."
        )
        return redirect(reverse("web:coordinator-theme"))

    def _context(self, competition):
        versions = (
            ThemeVersion.objects.filter(status=ThemeVersion.Status.VALID)
            .select_related("theme")
            .order_by("theme__name", "-uploaded_at")
        )
        current_options = competition.theme_options or {}
        cards = []
        for version in versions:
            layouts = []
            for key, options in (version.manifest.get("layouts") or {}).items():
                selected = (
                    (current_options.get("layouts") or {}).get(key)
                    if competition.theme_version_id == version.pk
                    else None
                )
                layouts.append(
                    {
                        "key": key,
                        "label": LAYOUT_LABELS.get(key, key),
                        "options": options,
                        "selected": selected if selected in options else options[0],
                    }
                )
            cards.append(
                {"version": version, "layouts": layouts, "active": competition.theme_version_id == version.pk}
            )
        return {
            "competition": competition,
            "classic": Theme.objects.filter(slug=CLASSIC_SLUG).first(),
            "classic_active": competition.theme_version_id is None,
            "cards": cards,
            "brand_accent": bool(current_options.get("brand_accent")),
            "site_root": get_script_prefix(),
        }


def theme_overrides_css(request):
    """``GET /_theme/overrides.css?v=<wersja>-<kolor>`` – akcent marki konkursu nad motywem.

    Arkusz z adresu własnej domeny (``'self'`` w CSP), bez stylu inline w ``<head>``. Kolor bierzemy
    z konkursu żądania, a parametr ``v`` jest wyłącznie kluczem pamięci podręcznej – zgodność
    sprawdzamy, żeby adres nie stał się generatorem arkuszy z dowolnym kolorem.
    """
    competition = getattr(request, "competition", None)
    version_part, _, colour = request.GET.get("v", "").partition("-")
    accent = (getattr(competition, "accent_colour", "") or "").lower()
    if (
        competition is None
        or not version_part.isdigit()
        or not HEX.match(accent)
        or colour != accent.lstrip("#")
    ):
        raise Http404
    # Wyłącznie wersja aktywna w tym konkursie – albo, dla jego koordynatora, dowolna poprawna
    # (podgląd z akcentem marki). Gość nie wskaże tu żadnej innej wersji, więc adres nie jest
    # drogą do odpytywania katalogu motywów.
    version_id = int(version_part)
    if version_id != competition.theme_version_id and not has_role(
        request.user, competition, CompetitionRole.COORDINATOR
    ):
        raise Http404
    runtime = runtime_for(version_id)
    if runtime is None:
        raise Http404
    response = HttpResponse(
        accent_override_css(accent, runtime.palette), content_type="text/css; charset=utf-8"
    )
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
