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
import secrets

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from apps.competitions.storage import private_media_storage
from apps.core.models import audit

from .models import CLASSIC_SLUG, Theme, ThemeVersion
from .package import PackageResult, validate_package
from .runtime import clean_options, forget_runtime, runtime_for
from .tokens import build_tokens_css

logger = logging.getLogger(__name__)

AUDIT_UPLOADED = "theme.uploaded"
AUDIT_ACTIVATED = "theme.activated"
AUDIT_DELETED = "theme.version_deleted"
# THEME-02: dostosowanie wersji w konkursie i menu serwisu.
AUDIT_CUSTOMIZED = "theme.customized"
AUDIT_CUSTOMIZATION_RESET = "theme.customization_reset"
AUDIT_MENU_SAVED = "theme.menu_saved"
AUDIT_MENU_RESET = "theme.menu_reset"

#: Znacznik „bez zmiany wersji” w ``_store``.
_UNSET = object()


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
    if _valid_exists(slug, version_label):
        result.errors.append(_immutable_message(slug, version_label))
        return None, result

    # Pliki trafiają do storage **przed** transakcją, a nie w niej: zapis do S3 nie cofa się razem
    # z bazą. Gdy transakcja się nie uda, sprzątamy to, co wgraliśmy (``_cleanup``); gdy się uda,
    # wiersz wskazuje na komplet plików od pierwszej chwili, w której ktokolwiek może go zobaczyć.
    prefix = f"themes/{slug}/{version_label}-{result.sha256[:8]}/" if result.ok else ""
    published: list[str] = []
    files: list[dict] = []
    package_name = ""
    try:
        if result.ok:
            for path, payload, content_type in [
                *((item.path, item.data, item.content_type) for item in result.public_files),
                ("theme.css", result.theme_css.encode("utf-8"), "text/css"),
                ("tokens.css", generated.css.encode("utf-8"), "text/css"),
            ]:
                _publish(prefix, path, payload)
                published.append(prefix + path)
                files.append({"path": path, "size": len(payload), "content_type": content_type})
        package_name = private_media_storage().save(
            f"themes/{slug}-{version_label}-{result.sha256[:8]}.zip", ContentFile(data)
        )
        version = _save_version(result, generated, prefix, files, package_name, actor=actor, request=request)
    except Exception:
        _cleanup(published, package_name)
        raise
    if version is None:
        # Wyścig: ktoś w międzyczasie zapisał poprawną wersję o tym numerze. Jej pliki leżą pod
        # innym prefiksem (inny skrót treści) albo pod tym samym (ta sama paczka) – tych nie ruszamy.
        existing_prefixes = set(
            ThemeVersion.objects.filter(theme__slug=slug, version=version_label).values_list(
                "public_prefix", flat=True
            )
        )
        _cleanup([] if prefix in existing_prefixes else published, package_name)
        result.errors.append(_immutable_message(slug, version_label))
        return None, result
    forget_runtime(version.pk)
    return version, result


def _immutable_message(slug: str, version_label: str) -> str:
    return f"Wersja {version_label} motywu „{slug}” już istnieje i jest niezmienna – podnieś numer wersji."


def _valid_exists(slug: str, version_label: str) -> bool:
    return ThemeVersion.objects.filter(
        theme__slug=slug, version=version_label, status=ThemeVersion.Status.VALID
    ).exists()


def _cleanup(names: list[str], package_name: str) -> None:
    for name in names:
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001 - sprzątanie nie może przykryć pierwotnego błędu
            logger.warning("Nie udało się usunąć osieroconego pliku motywu %s.", name)
    if package_name:
        try:
            private_media_storage().delete(package_name)
        except Exception:  # noqa: BLE001 - j.w.
            logger.warning("Nie udało się usunąć osieroconej paczki motywu %s.", package_name)


def _save_version(result, generated, prefix, files, package_name, *, actor, request) -> ThemeVersion | None:
    slug, version_label = result.slug, result.version
    with transaction.atomic():
        theme, _created = Theme.objects.get_or_create(
            slug=slug, defaults={"name": result.manifest.get("name") or slug}
        )
        existing = ThemeVersion.objects.select_for_update().filter(theme=theme, version=version_label).first()
        if existing is not None:
            if existing.is_valid:
                return None
            old_package = existing.package.name
            existing.delete()
            if old_package:
                transaction.on_commit(lambda name=old_package: _cleanup([], name))
        version = ThemeVersion(
            theme=theme,
            version=version_label,
            status=ThemeVersion.Status.VALID if result.ok else ThemeVersion.Status.INVALID,
            package_sha256=result.sha256,
            package_size=result.size,
            manifest=result.manifest,
            report=result.report(),
            uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
            public_prefix=prefix,
            files=files,
        )
        version.package.name = package_name
        if result.ok:
            version.tokens = {
                **result.tokens.as_json(),
                "generated": {"main": generated.main, "dark": generated.dark},
            }
            version.templates = result.templates
            version.has_screenshot = result.has_screenshot
            if not theme.is_builtin:
                theme.name = result.manifest.get("name") or theme.name
                theme.author = result.manifest.get("author", "")
                theme.description = result.manifest.get("description", "")
                theme.save(update_fields=["name", "author", "description"])
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
    return version


def _lock(competition):
    """Wiersz konkursu pod blokadą (``select_for_update``) – świeże ``theme_options`` do scalenia.

    Menu, dostosowanie i aktywacja zapisują tę samą kolumnę JSON. Bez blokady dwa równoległe zapisy
    (koordynator zapisuje menu, drugi – kolory) czytałyby stary słownik z obiektu żądania i jeden
    nadpisałby zmianę drugiego (zgubiony znacznik menu albo kolory). Wołać wewnątrz transakcji.
    """
    from apps.tenancy.models import Competition

    return Competition.objects.select_for_update().get(pk=competition.pk)


def _store(competition, locked, options: dict, *, version=_UNSET) -> None:
    """Zapis ``theme_options`` (i ewentualnie wersji) w zablokowanym wierszu + kopia w obiekcie wołającego."""
    fields = ["theme_options"]
    locked.theme_options = options
    if version is not _UNSET:
        locked.theme_version = version
        competition.theme_version = version
        fields.append("theme_version")
    locked.save(update_fields=fields)
    competition.theme_options = options


def activate(
    competition, version: ThemeVersion | None, options: dict | None = None, *, actor=None, request=None
):
    """Ustawia konkursowi wersję motywu (``None`` = ``classic``) i opcje. Zapis + audyt.

    Kolory dostosowania (zapisane dla tej wersji albo przeniesione z poprzedniej) przechodzą tę samą
    kontrolę kontrastu co zapis z panelu. Para poniżej WCAG AA = kolory **odrzucone** (motyw wchodzi
    z kolorami paczki), komunikaty w ``competition.theme_activation_warnings`` i w audycie
    (``dropped_colors``) – aktywacja nie może po cichu opublikować nieczytelnej palety.
    """
    from apps.web.page_cache import invalidate_competition

    if version is not None and not version.is_valid:
        raise ThemeError("Nie można aktywować odrzuconej wersji motywu.")
    cleaned: dict = {}
    dropped: dict = {}
    warnings: list[str] = []
    if version is not None:
        runtime = runtime_for(version.pk)
        if runtime is None:
            raise ThemeError("Wersja motywu jest niedostępna.")
        # Dostosowanie zapisane wcześniej dla tej wersji (THEME-02 § 2.4) wraca razem z nią;
        # opcje z formularza galerii (układy, akcent marki) mają pierwszeństwo.
        cleaned = clean_options(runtime, with_customization(competition, version, options))
        errors, _ = customization_report(runtime, cleaned)
        if errors and cleaned.get("colors"):
            dropped = cleaned.pop("colors")
            warnings = errors
    with transaction.atomic():
        locked = _lock(competition)
        previous = locked.theme_version_id
        _store(competition, locked, _keep_menu(locked, cleaned), version=version)
        if version is not None:
            # Opcje, z którymi wersja weszła (także przeniesione z poprzedniej przez ``theme_install``),
            # są od teraz jej zapisanym dostosowaniem – kolejne „Zapisz opcje” w galerii (układy
            # i akcent) scala się z nimi zamiast je gubić (przegląd THEME-02, druga runda).
            from .models import ThemeCustomization

            ThemeCustomization.objects.update_or_create(
                competition=competition,
                theme_version=version,
                defaults={
                    "options": cleaned,
                    "updated_by": actor if getattr(actor, "is_authenticated", False) else None,
                },
            )
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
                **({"dropped_colors": dropped, "contrast_errors": warnings} if dropped else {}),
            },
            request=request,
        )
    competition.theme_activation_warnings = warnings
    # Pełnostronicowy cache gościa trzyma HTML z ``<link>`` do arkuszy motywu – po zmianie motywu
    # strony tego konkursu muszą się wyrenderować od nowa.
    invalidate_competition(competition.pk)
    return competition


def _keep_menu(competition, options: dict) -> dict:
    """``theme_options`` z zachowaną rewizją menu – menu nie zależy od wybranego motywu."""
    from .menu import OPTIONS_KEY

    revision = (competition.theme_options or {}).get(OPTIONS_KEY)
    return {**options, OPTIONS_KEY: revision} if revision else options


def with_customization(competition, version: ThemeVersion | None, options: dict | None) -> dict:
    """Opcje ``options`` uzupełnione o dostosowanie zapisane dla (konkurs, wersja)."""
    from .models import ThemeCustomization

    options = dict(options or {})
    if version is None:
        return options
    stored = (
        ThemeCustomization.objects.filter(competition=competition, theme_version=version)
        .values_list("options", flat=True)
        .first()
    )
    return {**(stored or {}), **options}


def customization_report(runtime, options: dict) -> tuple[list[str], list[str]]:
    """Kontrola kontrastu oczyszczonych opcji (``customize.contrast_report``) dla widoku i zapisu."""
    from . import customize

    scheme = options.get("scheme") or runtime.color_scheme
    return customize.contrast_report(runtime, scheme, options.get("colors") or {})


def save_customization(
    competition, version: ThemeVersion, options: dict, *, actor=None, request=None
) -> dict:
    """Zapisuje dostosowanie wersji w konkursie (THEME-02 § 2). Zwraca oczyszczone opcje.

    Kontrast poniżej WCAG AA w parze, którą zmienił koordynator, **blokuje** zapis
    (``customize.CustomizationError`` z listą komunikatów). Wersja aktywna dostaje opcje od razu
    (kopia w ``Competition.theme_options``) i pełnostronicowy cache gościa jest unieważniany; wersja
    nieaktywna – tylko wiersz ``ThemeCustomization``, użyty przy jej aktywacji.
    """
    from apps.web.page_cache import invalidate_competition

    from .customize import CustomizationError
    from .models import ThemeCustomization

    if not version.is_valid:
        raise ThemeError("Nie można dostosować odrzuconej wersji motywu.")
    runtime = runtime_for(version.pk)
    if runtime is None:
        raise ThemeError("Wersja motywu jest niedostępna.")
    cleaned = clean_options(runtime, options)
    errors, _warnings = customization_report(runtime, cleaned)
    if errors:
        raise CustomizationError(errors)
    with transaction.atomic():
        locked = _lock(competition)
        row, _created = ThemeCustomization.objects.select_for_update().get_or_create(
            competition=competition, theme_version=version
        )
        before = dict(row.options or {})
        row.options = cleaned
        row.updated_by = actor if getattr(actor, "is_authenticated", False) else None
        row.save()
        active = locked.theme_version_id == version.pk
        if active:
            _store(competition, locked, _keep_menu(locked, cleaned))
        audit(
            actor,
            AUDIT_CUSTOMIZED,
            competition,
            {
                "theme": version.theme.slug,
                "version": version.version,
                "version_id": version.pk,
                "active": active,
                "before": before,
                "after": cleaned,
            },
            request=request,
        )
    if active:
        invalidate_competition(competition.pk)
    return cleaned


def reset_customization(competition, version: ThemeVersion, *, actor=None, request=None) -> None:
    """„Przywróć domyślne”: kolory, schemat, logo, kroje i promienie wersji wracają do paczki.

    Układy i akcent marki zostają (to wybory z galerii, nie dostosowanie kolorów).
    """
    from apps.web.page_cache import invalidate_competition

    from .models import ThemeCustomization
    from .runtime import CUSTOM_KEYS

    with transaction.atomic():
        locked = _lock(competition)
        row = (
            ThemeCustomization.objects.select_for_update()
            .filter(competition=competition, theme_version=version)
            .first()
        )
        before = dict(row.options or {}) if row is not None else {}
        if row is not None:
            row.options = {key: value for key, value in before.items() if key not in CUSTOM_KEYS}
            row.updated_by = actor if getattr(actor, "is_authenticated", False) else None
            row.save()
        active = locked.theme_version_id == version.pk
        if active:
            options = {k: v for k, v in (locked.theme_options or {}).items() if k not in CUSTOM_KEYS}
            _store(competition, locked, options)
        audit(
            actor,
            AUDIT_CUSTOMIZATION_RESET,
            competition,
            {
                "theme": version.theme.slug,
                "version": version.version,
                "version_id": version.pk,
                "before": before,
            },
            request=request,
        )
    if active:
        invalidate_competition(competition.pk)


def save_menu(
    competition, raw_items: list, *, auto_keys: dict, default_keys: list[str], actor=None, request=None
):
    """Zapisuje nadpisania menu (THEME-02 § 1). ``menu.MenuError`` = odrzucone z komunikatem.

    Lista niczego niezmieniająca (kolejność domyślna, bez etykiet, ukryć i własnych pozycji) usuwa
    nadpisania w ogóle – konkurs wraca do menu bez zapytania (``theme_options`` bez klucza ``menu``).

    Rewizja to **losowy** znacznik (``secrets.token_hex``), nie licznik: po „przywróć domyślne”
    wiersz znika, a licznik zacząłby od 1 – proces, który pamięta menu sprzed resetu pod rewizją 1,
    podawałby je dalej po kolejnym zapisie z rewizją 1 (przegląd THEME-02, H1).
    """
    from apps.web.page_cache import invalidate_competition

    from . import menu as menu_mod
    from .models import SiteMenu

    stored = SiteMenu.objects.filter(competition=competition).values_list("items", flat=True).first() or []
    previous_pages = {entry["page"] for entry in stored if isinstance(entry, dict) and entry.get("page")}
    items = menu_mod.clean_items(competition, raw_items, auto_keys=auto_keys, previous_pages=previous_pages)
    if menu_mod.is_default(items, default_keys):
        reset_menu(competition, actor=actor, request=request)
        return None
    with transaction.atomic():
        locked = _lock(competition)
        row = SiteMenu.objects.select_for_update().filter(competition=competition).first()
        before = list(row.items) if row is not None else []
        if row is None:
            row = SiteMenu(competition=competition)
        row.revision = secrets.token_hex(8)
        row.items = items
        row.updated_by = actor if getattr(actor, "is_authenticated", False) else None
        row.save()
        _store(competition, locked, {**(locked.theme_options or {}), menu_mod.OPTIONS_KEY: row.revision})
        audit(
            actor,
            AUDIT_MENU_SAVED,
            competition,
            {"revision": row.revision, "before": before, "after": items},
            request=request,
        )
    menu_mod.forget(competition.pk)
    invalidate_competition(competition.pk)
    return row


def reset_menu(competition, *, actor=None, request=None) -> None:
    """Usuwa nadpisania menu – menu wraca do drzewa stron (bez zapytania na stronach)."""
    from apps.web.page_cache import invalidate_competition

    from . import menu as menu_mod
    from .models import SiteMenu

    with transaction.atomic():
        locked = _lock(competition)
        row = SiteMenu.objects.select_for_update().filter(competition=competition).first()
        before = list(row.items) if row is not None else []
        if row is not None:
            row.delete()
        options = dict(locked.theme_options or {})
        had_marker = options.pop(menu_mod.OPTIONS_KEY, None) is not None
        if had_marker:
            _store(competition, locked, options)
        if row is not None or had_marker:
            audit(actor, AUDIT_MENU_RESET, competition, {"before": before}, request=request)
    menu_mod.forget(competition.pk)
    invalidate_competition(competition.pk)


def delete_version(version: ThemeVersion, *, actor=None, request=None) -> None:
    """Usuwa **nieużywaną** wersję: najpierw wiersz (w transakcji, z blokadą), pliki po zatwierdzeniu.

    Kolejność odwrotna (pliki, potem wiersz) przy wycofanej transakcji zostawiłaby wersję
    wskazującą na skasowane pliki – a ta mogłaby zostać aktywowana. Blokada wiersza zamyka wyścig
    z równoczesną aktywacją tej wersji w konkursie (``PROTECT`` i tak nie pozwoli jej usunąć).
    """
    from apps.tenancy.models import Competition

    with transaction.atomic():
        locked = ThemeVersion.objects.select_for_update().select_related("theme").get(pk=version.pk)
        if Competition.objects.filter(theme_version=locked).exists():
            raise ThemeError("Wersja jest używana przez konkurs – najpierw wybierz w nim inny motyw.")
        names = (
            [locked.public_prefix + item["path"] for item in (locked.files or [])]
            if locked.public_prefix
            else []
        )
        package_name = locked.package.name
        audit(
            actor,
            AUDIT_DELETED,
            locked,
            {"slug": locked.theme.slug, "version": locked.version},
            request=request,
        )
        pk = locked.pk
        theme = locked.theme
        locked.delete()
        if not theme.is_builtin and not theme.versions.exists():
            theme.delete()
        transaction.on_commit(lambda: _cleanup(names, package_name))
    forget_runtime(pk)
