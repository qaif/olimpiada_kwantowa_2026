"""Czynności na motywach: wgranie paczki, aktywacja w konkursie, usunięcie wersji.

Wszystkie reguły są tutaj, a nie w widokach: ten sam kod obsługuje panel superkoordynatora,
panel koordynatora i komendę ``manage.py theme_install`` (wdrożenia). Każda czynność zostawia wpis
audytu (``theme.uploaded``, ``theme.activated``, ``theme.version_deleted``).

Uprawnienia sprawdza wołający (widok: superkoordynator / koordynator konkursu; komenda: dostęp do
serwera). Funkcje przyjmują ``actor`` wyłącznie do audytu.
"""

from __future__ import annotations

import io
import logging
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from apps.core.models import audit

from .models import CLASSIC_SLUG, Theme, ThemeVersion
from .package import PackageResult, validate_package
from .runtime import clean_options, forget_runtime, runtime_for
from .tokens import build_tokens_css

logger = logging.getLogger(__name__)

AUDIT_UPLOADED = "theme.uploaded"
AUDIT_ACTIVATED = "theme.activated"
AUDIT_DELETED = "theme.version_deleted"


class ThemeError(Exception):
    """Czynność odrzucona z powodem do pokazania użytkownikowi."""


def app_version() -> str:
    return os.environ.get("APP_VERSION", "dev")


def scan_package(data: bytes) -> tuple[str, str]:
    """Skan ClamAV całej paczki (ten sam klient clamd, co przy zaświadczeniach i pracach).

    Niedostępny skaner to **błąd paczki**, a nie cichy przepust: paczka, której nikt nie
    przeskanował, nie publikuje plików w buckecie. Ponowne wgranie tej samej wersji zastępuje
    wersję odrzuconą.
    """
    from apps.submissions.antivirus import ClamAVError, scan_stream

    try:
        return scan_stream(io.BytesIO(data), size=len(data))
    except ClamAVError as exc:
        logger.warning("Skan paczki motywu nieudany: %s", exc)
        return "ERROR", f"skaner niedostępny – spróbuj ponownie ({exc.__class__.__name__})"


def _scanner():
    return scan_package if getattr(settings, "THEMES_AV_SCAN", True) else None


def _publish(prefix: str, path: str, data: bytes) -> None:
    name = prefix + path
    if default_storage.exists(name):
        default_storage.delete(name)
    saved = default_storage.save(name, ContentFile(data))
    if saved != name:  # pragma: no cover - storage zmienił nazwę mimo usunięcia
        raise ThemeError(f"Storage zapisał plik pod inną nazwą ({saved!r} zamiast {name!r}).")


def install_package(data: bytes, *, actor=None, request=None) -> tuple[ThemeVersion | None, PackageResult]:
    """Waliduje paczkę i zapisuje wersję motywu.

    - paczka bez czytelnego ``slug``/``version`` → nic nie powstaje, raport wraca do wołającego,
    - paczka z błędami → wersja ``invalid`` z raportem (bez publikacji plików),
    - paczka poprawna → wersja ``valid``: pliki publiczne pod ``themes/<slug>/<wersja>-<sha8>/``,
      ``tokens.css`` wygenerowany z ``tokens.json``, paczka w storage prywatnym.

    Wersja ``valid`` jest niezmienna: druga paczka z tym samym numerem jest odrzucana (trzeba
    podnieść numer). Wersję ``invalid`` kolejne wgranie zastępuje.
    """
    result = validate_package(data, app_version=app_version(), scan=_scanner())
    slug, version_label = result.slug, result.version
    if not slug or not version_label or slug == CLASSIC_SLUG:
        return None, result
    generated = None
    if result.ok:
        generated = build_tokens_css(result.tokens, result.manifest.get("color_scheme", "light"))
        result.warnings += generated.warnings

    with transaction.atomic():
        theme, _created = Theme.objects.get_or_create(
            slug=slug, defaults={"name": result.manifest.get("name") or slug}
        )
        existing = ThemeVersion.objects.select_for_update().filter(theme=theme, version=version_label).first()
        if existing is not None:
            if existing.is_valid:
                result.errors.append(
                    f"Wersja {version_label} motywu „{slug}” już istnieje i jest niezmienna – "
                    "podnieś numer wersji."
                )
                return None, result
            if existing.package:
                existing.package.delete(save=False)
            existing.delete()
        version = ThemeVersion(
            theme=theme,
            version=version_label,
            status=ThemeVersion.Status.VALID if result.ok else ThemeVersion.Status.INVALID,
            package_sha256=result.sha256,
            package_size=result.size,
            manifest=result.manifest,
            report=result.report(),
            uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        if result.ok:
            version.public_prefix = f"themes/{slug}/{version_label}-{result.sha256[:8]}/"
            version.tokens = {
                **result.tokens.as_json(),
                "generated": {"main": generated.main, "dark": generated.dark},
            }
            version.templates = result.templates
            version.has_screenshot = result.has_screenshot
            files = []
            for item in result.public_files:
                _publish(version.public_prefix, item.path, item.data)
                files.append({"path": item.path, "size": len(item.data), "content_type": item.content_type})
            _publish(version.public_prefix, "theme.css", result.theme_css.encode("utf-8"))
            _publish(version.public_prefix, "tokens.css", generated.css.encode("utf-8"))
            files += [
                {
                    "path": "theme.css",
                    "size": len(result.theme_css.encode("utf-8")),
                    "content_type": "text/css",
                },
                {
                    "path": "tokens.css",
                    "size": len(generated.css.encode("utf-8")),
                    "content_type": "text/css",
                },
            ]
            version.files = files
            if not theme.is_builtin:
                theme.name = result.manifest.get("name") or theme.name
                theme.author = result.manifest.get("author", "")
                theme.description = result.manifest.get("description", "")
                theme.save(update_fields=["name", "author", "description"])
        version.package.save(f"{slug}-{version_label}-{result.sha256[:8]}.zip", ContentFile(data), save=False)
        version.save()
        audit(
            actor,
            AUDIT_UPLOADED,
            version,
            {
                "slug": slug,
                "version": version_label,
                "status": version.status,
                "sha256": result.sha256,
                "errors": len(result.errors),
                "warnings": len(result.warnings),
            },
            request=request,
        )
    forget_runtime(version.pk)
    return version, result


def activate(
    competition, version: ThemeVersion | None, options: dict | None = None, *, actor=None, request=None
):
    """Ustawia konkursowi wersję motywu (``None`` = ``classic``) i opcje. Zapis + audyt."""
    from apps.web.page_cache import invalidate_competition

    if version is not None and not version.is_valid:
        raise ThemeError("Nie można aktywować odrzuconej wersji motywu.")
    previous = competition.theme_version_id
    cleaned: dict = {}
    if version is not None:
        runtime = runtime_for(version.pk)
        if runtime is None:
            raise ThemeError("Wersja motywu jest niedostępna.")
        cleaned = clean_options(runtime, options)
    competition.theme_version = version
    competition.theme_options = cleaned
    competition.save(update_fields=["theme_version", "theme_options"])
    audit(
        actor,
        AUDIT_ACTIVATED,
        competition,
        {
            "from_version": previous,
            "to_version": version.pk if version is not None else None,
            "theme": version.theme.slug if version is not None else CLASSIC_SLUG,
            "version": version.version if version is not None else "",
            "options": cleaned,
        },
        request=request,
    )
    # Pełnostronicowy cache gościa trzyma HTML z ``<link>`` do arkuszy motywu – po zmianie motywu
    # strony tego konkursu muszą się wyrenderować od nowa.
    invalidate_competition(competition.pk)
    return competition


def delete_version(version: ThemeVersion, *, actor=None, request=None) -> None:
    """Usuwa **nieużywaną** wersję (pliki publiczne, paczkę, wiersz)."""
    from apps.tenancy.models import Competition

    if Competition.objects.filter(theme_version=version).exists():
        raise ThemeError("Wersja jest używana przez konkurs – najpierw wybierz w nim inny motyw.")
    for item in version.files or []:
        name = version.public_prefix + item["path"]
        if version.public_prefix and default_storage.exists(name):
            default_storage.delete(name)
    if version.package:
        version.package.delete(save=False)
    audit(
        actor,
        AUDIT_DELETED,
        version,
        {"slug": version.theme.slug, "version": version.version},
        request=request,
    )
    pk = version.pk
    theme = version.theme
    version.delete()
    forget_runtime(pk)
    if not theme.is_builtin and not theme.versions.exists():
        theme.delete()
