"""Rejestr konkursów w djcms (docs/tasks/DJ-02.md § 5.2).

Źródłem prawdy o konkursach jest aplikacja główna – ``GET /internal/djcms/v2/competitions``.
Tu leży jej **kopia** potrzebna do rozstrzygania żądań bez pytania API przy każdej odsłonie:

- ``CompetitionSite`` – konkurs i jego witryna django CMS (``django.contrib.sites.Site``: drzewo
  stron, menu, bufory placeholderów). Jedna witryna na konkurs, na zawsze: skasowanie konkursu
  w aplikacji głównej **nie** kasuje tu niczego (``is_active=False`` – hosty odpowiadają 404,
  treść zostaje; ``sync_competitions --prune`` robi operator po kopii),
- ``CompetitionHost`` – znormalizowane hosty konkursu. Konkurs pod prefiksem ścieżki (``PATH``)
  odpowiada pod hostem gospodarza (``hosts_path_prefixes``), a pod własnym hostem (domeną, na którą
  czeka) – bez prefiksu, jak w aplikacji głównej,
- ``SsoNonce`` – jednorazowe identyfikatory tokenów logowania redaktorów (D6, DJ-02g).

``Site.domain`` to wyłącznie unikalna etykieta dla ``django.contrib.sites`` – adresy kanoniczne,
mapa witryny i odnośniki budujemy z ``public_origin`` + ``public_path_prefix``, nigdy z ``domain``.
"""

from __future__ import annotations

from django.db import models


class RoutingMode(models.TextChoices):
    DOMAIN = "DOMAIN", "własny host"
    PATH = "PATH", "prefiks ścieżki"


class CompetitionSite(models.Model):
    site = models.OneToOneField("sites.Site", on_delete=models.PROTECT, related_name="competition")
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    #: Konkurs witryny domyślnej aplikacji głównej – odpowiada także pod ``localhost``/``127.0.0.1``/
    #: ``djcms`` (dev i healthcheck). Co najwyżej jeden (ograniczenie niżej).
    is_default = models.BooleanField(default=False)
    routing_mode = models.CharField(max_length=8, choices=RoutingMode.choices, default=RoutingMode.DOMAIN)
    path_prefix = models.SlugField(max_length=40, blank=True)
    #: Czy pod hostami tego konkursu wolno rozstrzygać konkursy ``PATH`` po pierwszym segmencie
    #: (``apps.tenancy.resolution.hosts_path_prefixes`` aplikacji głównej).
    hosts_path_prefixes = models.BooleanField(default=False)
    #: Publiczny adres konkursu (``public_base`` z API): origin i prefiks ścieżki. Pusty origin =
    #: konkurs nie ma dziś adresu publicznego (bramka aplikacji głównej zamknięta).
    public_origin = models.CharField(max_length=255, blank=True)
    public_path_prefix = models.CharField(max_length=41, blank=True)
    #: Ścieżki stron, do których linkuje aplikacja (zgody, warsztaty) – kontrola S16 (DJ-02e).
    linked_paths = models.JSONField(default=list, blank=True)
    #: Odcisk wpisu z API; ten sam odcisk = zero zapisów przy uzgadnianiu.
    fingerprint = models.CharField(max_length=64, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    #: Ostatni import treści (``--replace``/``--if-empty``/drzewo startowe) – DJ-02e.
    content_imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "witryna konkursu"
        verbose_name_plural = "witryny konkursów"
        ordering = ("slug",)
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="dj_sites_single_default_competition",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    @property
    def public_base(self) -> str:
        """``https://host`` + ``/prefiks`` – baza adresów bezwzględnych konkursu (pusta = brak)."""
        if not self.public_origin:
            return ""
        return f"{self.public_origin.rstrip('/')}{self.public_path_prefix}"


class CompetitionHost(models.Model):
    #: Znormalizowany host: małe litery, bez portu i bez kropki końcowej (``resolution.normalise_host``).
    host = models.CharField(max_length=255, unique=True)
    competition = models.ForeignKey(CompetitionSite, on_delete=models.CASCADE, related_name="hosts")

    class Meta:
        verbose_name = "host konkursu"
        verbose_name_plural = "hosty konkursów"
        ordering = ("host",)

    def __str__(self) -> str:
        return self.host


class SsoNonce(models.Model):
    """Zużyty identyfikator tokenu SSO (D6) – drugi raz ten sam token nie loguje (DJ-02g)."""

    nonce = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "zużyty token SSO"
        verbose_name_plural = "zużyte tokeny SSO"

    def __str__(self) -> str:
        return self.nonce
