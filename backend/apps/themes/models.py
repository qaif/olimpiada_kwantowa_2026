"""Katalog motywów platformy (THEME-01 § 4).

``Theme`` jest wpisem katalogu (jeden na ``slug``), ``ThemeVersion`` – jedną wgraną paczką.
Wersja jest **niezmienna**: pliki publiczne leżą pod prefiksem ``themes/<slug>/<wersja>-<skrót>/``,
więc przeglądarka i CDN mogą trzymać je bez unieważniania, a cofnięcie motywu to wybór poprzedniej
wersji w konkursie (wersje zostają, dopóki operator ich nie usunie, a usunąć można tylko nieużywaną
– ``Competition.theme_version`` stoi na ``PROTECT``).

Motyw jest globalny (operator platformy wgrywa go raz), a **wybór** należy do konkursu:
``Competition.theme_version`` + ``Competition.theme_options``. ``null`` = wbudowany ``classic``,
czyli wygląd aplikacji bez żadnego arkusza motywu – Konkurs #1 nie płaci za motywy ani bajtem HTML,
ani zapytaniem.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.storage import private_media_storage

#: Slug wbudowanego motywu (wygląd aplikacji). Zarezerwowany – paczka o tym slugu jest odrzucana.
CLASSIC_SLUG = "classic"


class Theme(models.Model):
    slug = models.SlugField("identyfikator", max_length=50, unique=True)
    name = models.CharField("nazwa", max_length=80)
    author = models.CharField("autor", max_length=120, blank=True)
    description = models.TextField("opis", blank=True)
    #: ``classic`` – wpis istnieje po to, żeby galeria i audyt miały jeden model dla obu przypadków;
    #: nie ma żadnej wersji ani pliku (wygląd żyje w ``static/css`` i ``templates/theme``).
    is_builtin = models.BooleanField("wbudowany", default=False)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    class Meta:
        verbose_name = "motyw"
        verbose_name_plural = "motywy"
        ordering = ("-is_builtin", "name")

    def __str__(self) -> str:
        return self.name


class ThemeVersion(models.Model):
    class Status(models.TextChoices):
        VALID = "valid", "poprawna"
        INVALID = "invalid", "odrzucona"

    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="versions", verbose_name="motyw")
    version = models.CharField("wersja", max_length=32)
    status = models.CharField("stan", max_length=10, choices=Status.choices)
    #: Paczka w **prywatnym** storage (źródło prawdy do ponownej publikacji i do wglądu operatora).
    package = models.FileField("paczka", storage=private_media_storage, upload_to="themes/", blank=True)
    package_sha256 = models.CharField("SHA-256 paczki", max_length=64)
    package_size = models.PositiveIntegerField("rozmiar paczki", default=0)
    #: Prefiks plików publicznych w storage ``default`` (bucket ``public-media``), z ``/`` na końcu.
    #: Pusty u wersji odrzuconej – odrzucona paczka nie publikuje ani jednego pliku.
    public_prefix = models.CharField("prefiks publiczny", max_length=200, blank=True)
    manifest = models.JSONField("manifest", default=dict)
    #: Odczytane ``tokens.json`` (palety i pozostałe tokeny, ``apps.themes.tokens.TokenSet``).
    tokens = models.JSONField("tokeny", default=dict)
    #: Szablony slotów: nazwa → źródło. W bazie, nie w storage: są małe, potrzebne przy renderze
    #: i nie mogą być nigdy podane przeglądarce jako plik (bucket podałby je jako ``text/html``).
    templates = models.JSONField("szablony", default=dict)
    #: Opublikowane pliki: ``[{"path": "assets/…", "size": 123, "content_type": "…"}]``.
    files = models.JSONField("pliki", default=list)
    #: Raport walidacji: ``{"errors": [...], "warnings": [...]}``.
    report = models.JSONField("raport walidacji", default=dict)
    has_screenshot = models.BooleanField("zrzut ekranu", default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="wgrał",
    )
    uploaded_at = models.DateTimeField("wgrano", default=timezone.now)

    class Meta:
        verbose_name = "wersja motywu"
        verbose_name_plural = "wersje motywów"
        ordering = ("theme__name", "-uploaded_at")
        constraints = [models.UniqueConstraint(fields=("theme", "version"), name="themes_version_unique")]

    def __str__(self) -> str:
        return f"{self.theme.name} {self.version}"

    @property
    def is_valid(self) -> bool:
        return self.status == self.Status.VALID

    @property
    def color_scheme(self) -> str:
        return (self.manifest or {}).get("color_scheme", "light")

    def public_url(self, path: str) -> str:
        from django.core.files.storage import default_storage

        return default_storage.url(self.public_prefix + path)

    @property
    def screenshot_url(self) -> str | None:
        return self.public_url("screenshot.png") if self.has_screenshot and self.public_prefix else None
