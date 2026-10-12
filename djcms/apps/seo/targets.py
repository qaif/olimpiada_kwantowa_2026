"""Cele przekierowań tylko na hosty platformy (audyt 2026-10-10, pozycja niska djcms).

Redaktor jednego konkursu ustawiał przekierowanie (wpis ``dj_seo.Redirect`` albo pole
„przekierowanie” strony django CMS – ``PageContent.redirect``) na **dowolny** ``https://host``:
adres konkursu prowadził wtedy pod domenę napastnika z wiarygodnym odnośnikiem w ręku (phishing
logowania, które leży w tym samym originie). Reguła:

- ścieżka witryny (``/…``) – zawsze wolno,
- adres bezwzględny (``http(s)://host…``, a w polu strony także ``//host…``, które django CMS
  przepuszcza) – tylko na host platformy (:func:`apps.sites.resolution.is_platform_host`:
  ``ALLOWED_HOSTS`` djcms bez ``*`` i hosty/adresy publiczne aktywnych konkursów z rejestru),
- konto platformy (:func:`apps.sites.permissions.is_platform_editor`: superużytkownik albo grupa
  ``redakcja:platforma`` z SSO) – dowolny host, jak dotąd (świadomy wybór, np. strona partnera).

Egzekwujemy przy **zapisie** (formularz panelu i ``Redirect.clean``), bo tylko tam wiadomo, kto
ustawia cel; warstwa przekierowań (``apps.seo.middleware``) i widok django CMS oddają zapisany cel.
Wpisy importu (paczka eksportu platformy) i automatu (ścieżki) nie przechodzą przez tę regułę.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

MESSAGE = (
    "Adres bezwzględny może prowadzić wyłącznie na host platformy (adres któregoś z konkursów). "
    "Przekierowanie na inną domenę ustawia redakcja platformy."
)
PATCH_MARKER = "_dj_seo_redirect_targets"


def is_platform_target(value: str) -> bool:
    """Czy cel zostaje na platformie: ścieżka witryny albo adres bezwzględny na host platformy.

    Ukośnik wsteczny, spacje i znaki sterujące – ``False``: przeglądarki zamieniają ``\\`` na ``/``
    (``/\\evil.example`` to ``//evil.example``), więc kształtu, którego nie da się jednoznacznie
    przeczytać, nie oceniamy jako bezpiecznego.
    """
    from apps.sites.resolution import is_platform_host

    value = (value or "").strip()
    if not value:
        return True
    if "\\" in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
    except ValueError:  # np. niedomknięty adres IPv6 – ``https://[::1/``
        return False
    if not parts.scheme and not parts.netloc:
        return True
    if parts.scheme not in ("", "http", "https"):
        return False
    return bool(host) and is_platform_host(host)


def validate_target(value: str, user) -> None:
    """``ValidationError`` dla celu spoza platformy, chyba że ustawia go konto platformy."""
    from apps.sites.permissions import is_platform_editor

    if is_platform_target(value) or is_platform_editor(user):
        return
    raise ValidationError(MESSAGE, code="foreign-host")


def install_page_redirect_validation() -> None:
    """``ChangePageForm.clean_redirect`` django CMS 5.1.3 (``AppConfig.ready``, idempotentnie).

    Pole „przekierowanie” (``PageSmartLinkField`` z ``cms.forms.validators.validate_url``) przyjmuje
    każdy adres ``http(s)://`` i ``//host``. Formularz nie ma własnego ``clean_redirect``, a Django
    woła ``clean_<pole>`` po walidatorach pola – dokładamy więc metodę klasie, bez kopiowania
    formularza. Konto bierzemy z ``form._request`` (ustawia je ``PageContentAdmin.get_form``);
    formularz bez żądania traktujemy jak konto bez uprawnień platformy.
    """
    from cms.admin import forms

    if getattr(forms.ChangePageForm, PATCH_MARKER, False):
        return
    if "clean_redirect" in vars(forms.ChangePageForm):
        # Nowe wydanie django CMS dodało własną walidację – nie nadpisujemy jej po cichu.
        raise RuntimeError("ChangePageForm.clean_redirect już istnieje – sprawdź apps.seo.targets")

    def clean_redirect(self):
        value = self.cleaned_data.get("redirect") or ""
        request = getattr(self, "_request", None)
        validate_target(value, getattr(request, "user", None))
        return value

    forms.ChangePageForm.clean_redirect = clean_redirect
    setattr(forms.ChangePageForm, PATCH_MARKER, True)
