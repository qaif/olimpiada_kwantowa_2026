"""Kolizje ścieżek stron z adresami aplikacji (S5 docs/tasks/DJ-02.md, reguła 5 z § 7 DJ-01).

Caddy kieruje do ``web`` każdą ścieżkę pasującą do ``APP_RE`` (a w bloku domeny platformy – także
``APP_RE_PREFIXED``, czyli adres aplikacji w **drugim** segmencie, ``/druga/login/``). Strona djcms
pod takim adresem byłaby niewidoczna, a gdyby trasy kiedyś się rozjechały – przesłaniałaby
aplikację. Dlatego djcms odrzuca stronę, której ścieżka:

- zaczyna się od segmentu zarezerwowanego (``settings.DJ_RESERVED_SLUGS``: własne adresy djcms
  i pierwsze segmenty urlconfu aplikacji głównej),
- albo pasuje do ``app_re``/``app_re_prefixed`` z kontraktu (``app_routes.json``) – to obejmuje
  adresy zagnieżdżone (``warsztaty/materialy``) i regułę drugiego segmentu (``o-nas/login``).

Trzy miejsca używają tej reguły: formularze stron django CMS (dodanie, zmiana sluga/adresu,
przeniesienie – ``install_form_validation``), system check ``dj_pages.W001`` (strony już
opublikowane) i – w DJ-02e – importer.
"""

from __future__ import annotations

import re
from functools import cache

from django.conf import settings
from django.core.exceptions import ValidationError

PATCH_MARKER = "_dj_pages_app_paths"


@cache
def _regexes(app_re: str, app_re_prefixed: str) -> tuple[re.Pattern, re.Pattern]:
    return re.compile(app_re), re.compile(app_re_prefixed)


def path_collides_with_app(path: str | None) -> str | None:
    """Powód kolizji ścieżki strony (``PageUrl.path``, bez ukośników brzegowych) albo ``None``."""
    path = (path or "").strip("/")
    if not path:
        return None  # strona główna – korzeń witryny należy do djcms
    first = path.split("/", 1)[0]
    if first in settings.DJ_RESERVED_SLUGS:
        return f"segment „{first}” jest zarezerwowany dla adresów aplikacji"
    routes = settings.DJ_APP_ROUTES
    app_re, app_re_prefixed = _regexes(routes["app_re"], routes["app_re_prefixed"])
    for url in (f"/{path}/", f"/{path}"):
        if app_re.match(url) or app_re_prefixed.match(url):
            return f"adres /{path}/ należy do aplikacji głównej (kontrakt tras app_routes.json)"
    return None


def validate_page_path(path: str | None) -> None:
    reason = path_collides_with_app(path)
    if reason:
        raise ValidationError(
            f"Nie można użyć tego adresu: {reason}. Zmień slug strony (albo jej rodzica).",
            code="app-path",
        )


def install_form_validation() -> None:
    """Podpina regułę pod formularze stron django CMS 5.1.3 (``AppConfig.ready``, idempotentnie).

    ``AddPageForm.clean``, ``ChangePageForm.clean`` i ``MovePageForm._validate_url_uniqueness``
    (``cms/admin/forms.py``) wołają moduł-globalną ``validate_url_uniqueness(site, path, …)``
    z pełną ścieżką nowej strony i dokładają jej ``ValidationError`` do pola ``slug``/``overwrite_url``.
    Opakowanie tej jednej nazwy obejmuje więc wszystkie trzy drogi zmiany adresu, bez kopiowania
    formularzy.
    """
    from cms.admin import forms

    current = forms.validate_url_uniqueness
    if getattr(current, PATCH_MARKER, False):
        return

    def validate_url_uniqueness(site, path, language, user_language=None, exclude_page=None):
        validate_page_path(path)
        return current(site, path, language, user_language=user_language, exclude_page=exclude_page)

    setattr(validate_url_uniqueness, PATCH_MARKER, True)
    forms.validate_url_uniqueness = validate_url_uniqueness
