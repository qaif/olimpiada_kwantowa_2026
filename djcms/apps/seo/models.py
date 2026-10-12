"""Przekierowania starych adresów witryny konkursu (DJ-02 § 8) – ``dj_seo.Redirect``.

Odpowiednik ``wagtail.contrib.redirects.Redirect`` po przełączeniu na django CMS: django CMS 5.1.3
ma wyłącznie ``PageContent.redirect`` (strona-przekierowanie), a ``django.contrib.redirects`` nie
zna witryn konkursów pod prefiksem ścieżki (ustalenia § 5.1).

- ``old_path`` – adres **względem korzenia witryny konkursu** (bez prefiksu ścieżki), w postaci
  ``Redirect.normalise_path`` Wagtaila: bez ukośnika końcowego, parametry i zapytanie posortowane
  (``/regulamin``, ``/stary?a=1&b=2``). Ten sam kształt, w którym eksportuje je paczka v2.
- ``new_path`` – ścieżka względem korzenia witryny (``/dokumenty/regulamin/`` – pod prefiksem
  warstwa dokłada prefiks) albo adres bezwzględny ``http(s)://`` – na host platformy, a na inny
  host wyłącznie z konta platformy (``apps.seo.targets``, audyt 2026-10-10).
- ``source`` – ``import`` (paczka Wagtaila; ``--replace`` kasuje tylko te), ``auto`` (zmiana adresu
  opublikowanej strony – ``apps.seo.auto``), ``manual`` (redakcja w panelu). Wpis redakcji wygrywa
  z importem i z automatem.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import models

MAX_OLD_PATH = 255
MAX_NEW_PATH = 500


class RedirectSource(models.TextChoices):
    IMPORT = "import", "import z Wagtaila"
    AUTO = "auto", "zmiana adresu strony"
    MANUAL = "manual", "redakcja"


def normalise_path(url: str) -> str:
    """Port ``wagtail.contrib.redirects.models.Redirect.normalise_path`` (Wagtail 7).

    Ścieżka z ``/`` na początku i bez ``/`` na końcu, parametry (``;``) i składniki zapytania
    posortowane alfabetycznie – ``/a/?b=2&a=1`` i ``/a?a=1&b=2`` to jeden wpis.
    """
    parsed = urlsplit(url.strip())
    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    path, _, parameters = path.partition(";")
    if parameters:
        path = path + ";" + ";".join(sorted(parameters.split(";")))
    if parsed.query:
        path = path + "?" + "&".join(sorted(parsed.query.split("&")))
    return path


def is_safe_target(value: str) -> bool:
    """Cel: ścieżka witryny (``/…``, nie ``//``) albo ``http(s)://host…``; bez spacji i znaków sterujących."""
    if not value or len(value) > MAX_NEW_PATH or "\\" in value:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if value.startswith("/"):
        return not value.startswith("//")
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


class Redirect(models.Model):
    #: Czy ``clean`` przepuszcza adres bezwzględny na host spoza platformy – ustawia panel
    #: (``RedirectAdmin.get_form``) dla konta platformy. Nie jest polem: decyduje ustawiający, nie wpis.
    allow_foreign_host = False

    site = models.ForeignKey("sites.Site", on_delete=models.CASCADE, related_name="dj_redirects")
    old_path = models.CharField("stary adres", max_length=MAX_OLD_PATH, db_index=True)
    new_path = models.CharField("nowy adres", max_length=MAX_NEW_PATH)
    is_permanent = models.BooleanField("stałe (301)", default=True)
    source = models.CharField(
        "źródło", max_length=8, choices=RedirectSource.choices, default=RedirectSource.MANUAL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "przekierowanie"
        verbose_name_plural = "przekierowania"
        ordering = ("site", "old_path")
        constraints = [
            models.UniqueConstraint(fields=["site", "old_path"], name="dj_seo_unique_site_old_path")
        ]

    def __str__(self) -> str:
        return f"{self.old_path} → {self.new_path}"

    def clean(self) -> None:
        self.old_path = normalise_path(self.old_path or "")
        self.new_path = (self.new_path or "").strip()
        if "\0" in self.old_path or len(self.old_path) > MAX_OLD_PATH:
            raise ValidationError({"old_path": "Niepoprawny adres."})
        if not is_safe_target(self.new_path):
            raise ValidationError(
                {"new_path": "Podaj ścieżkę w witrynie (zaczynającą się od /) albo adres http(s)://."}
            )
        from .targets import MESSAGE, is_platform_target

        if not self.allow_foreign_host and not is_platform_target(self.new_path):
            raise ValidationError({"new_path": MESSAGE})
        if normalise_path(self.new_path) == self.old_path and self.new_path.startswith("/"):
            raise ValidationError({"new_path": "Przekierowanie nie może prowadzić pod ten sam adres."})
