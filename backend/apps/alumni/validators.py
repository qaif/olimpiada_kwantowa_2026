"""Walidacja odnośników profilu absolwenta: tylko ``https`` i tylko właściwy serwis.

Lista hostów jest **zamknięta**, bo profil z dowolnym adresem byłby tablicą ogłoszeń dla linków,
których nikt nie sprawdza – na stronie oglądanej przez niepełnoletnich uczestników. Dwa serwisy
z polecenia (LinkedIn, GitHub) pokrywają to, po co absolwent wpisuje odnośnik: „kim jestem
zawodowo” i „co zrobiłem”. Odnośnik i tak dostaje ``rel="nofollow noopener noreferrer ugc"``
(szablon), więc profil nie podbija nikomu pozycji w wyszukiwarce.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

LINKEDIN_HOSTS = frozenset({"linkedin.com", "www.linkedin.com", "pl.linkedin.com"})
GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def _clean(value: str, hosts: frozenset[str], *, label: str, path_prefixes: tuple[str, ...] = ()) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in hosts or parts.username or parts.password or parts.port:
        raise ValidationError(
            _("Podaj adres profilu %(service)s zaczynający się od https://.") % {"service": label}
        )
    path = parts.path or "/"
    if path_prefixes and not path.lower().startswith(path_prefixes):
        raise ValidationError(_("To nie wygląda na adres profilu %(service)s.") % {"service": label})
    if len(path.strip("/")) == 0:
        raise ValidationError(_("To nie wygląda na adres profilu %(service)s.") % {"service": label})
    # Bez zapytania i kotwicy: profil to ścieżka; parametry śledzące nie mają czego tu szukać.
    return f"https://{host}{path}"


def clean_linkedin(value: str) -> str:
    return _clean(value, LINKEDIN_HOSTS, label="LinkedIn", path_prefixes=("/in/", "/pub/"))


def clean_github(value: str) -> str:
    return _clean(value, GITHUB_HOSTS, label="GitHub")


def clean_event_url(value: str) -> str:
    """Adres wydarzenia w zaproszeniu: dowolny host, ale wyłącznie ``https`` (list idzie do ludzi)."""
    text = (value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValidationError(_("Adres wydarzenia musi zaczynać się od https://."))
    return text
