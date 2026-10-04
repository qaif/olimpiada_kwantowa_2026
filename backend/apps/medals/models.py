"""Medale olimpiady międzynarodowej: schemat nagród etapu, ręczne zmiany i język dokumentu (MED-01).

Skąd to się wzięło. Olimpiada Kwantowa nagradza **tytułem** laureata albo finalisty i o tym, kto
go dostaje, rozstrzyga komitet (``results.CertificateKind``). Olimpiada międzynarodowa (``iqo``)
nagradza jak IPhO i IMO: złoto, srebro, brąz i wyróżnienie liczone **z rankingu** – najlepsze 8 %
pola, kolejne 17 %, kolejne 25 % – a ręczna zmiana jest wyjątkiem, który trzeba uzasadnić.

Trzy modele i ich granice:

- ``MedalScheme`` – progi procentowe, polityka remisu i kryteria wyróżnienia **jednego etapu**
  (tego, który koordynator uznaje za ranking ostateczny), a po ogłoszeniu także zamrożony wynik:
  nagrody per wpis, progi punktowe, publiczna tabela i tabela krajów. Zamrożenie jest w tym samym
  wierszu, a nie w osobnej „publikacji medali”, bo schemat bez wyniku i wynik bez schematu nie
  mają sensu – a zamrożony schemat to po prostu schemat, którego nie wolno już zmienić,
- ``MedalOverride`` – nagroda inna niż wyliczona dla jednego wpisu, z **obowiązkowym**
  uzasadnieniem. Osobny wiersz, a nie pole w zamrożonym JSON-ie: zmiana jest decyzją z autorem
  i datą, a nie stanem tabeli,
- ``CertificateLanguage`` – język, w którym wystawiono dokument. PDF powstaje przy każdym pobraniu
  (``apps.results.certificates``), więc bez tego wiersza dyplom zmieniałby język razem z ustawieniem
  konta ucznia – a dokument, który ktoś trzyma w ręku, ma wychodzić z drukarki tak samo.

Cała funkcja stoi za flagą konkursu ``medals`` (``tenancy.FEATURE_DEFAULTS``, domyślnie wyłączona).
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.competitions.scoping import competition_scoped_manager

#: Przełącznik konkursu, za którym stoi cała aplikacja. Nazwa jest jedna i jest tutaj, żeby
#: literówka wywracała się w jednym miejscu (``has_feature`` podnosi ``KeyError`` na nieznanej).
FLAG = "medals"


def enabled(competition) -> bool:
    """Czy ten konkurs nagradza medalami. **Jedyne** wejście do flagi – bez zapytania do bazy."""
    return competition is not None and competition.has_feature(FLAG)


class Award(models.TextChoices):
    """Nagroda. Kolejność wartości jest kolejnością ważności (złoto najwyżej)."""

    GOLD = "GOLD", _("złoty medal")
    SILVER = "SILVER", _("srebrny medal")
    BRONZE = "BRONZE", _("brązowy medal")
    HONOURABLE = "HM", _("wyróżnienie")
    NONE = "NONE", _("bez nagrody")


#: Nagrody, które są medalem (wyróżnienie nim nie jest – liczy się w osobnej kolumnie).
MEDALS = (Award.GOLD, Award.SILVER, Award.BRONZE)
#: Ranga nagrody do sortowania: niższa liczba = wyższa nagroda.
AWARD_RANK = {Award.GOLD: 0, Award.SILVER: 1, Award.BRONZE: 2, Award.HONOURABLE: 3, Award.NONE: 4}


class TiePolicy(models.TextChoices):
    """Co z grupą równych wyników na granicy puli. Remis nigdy nie jest dzielony – pytanie brzmi tylko,
    w którą stronę idzie cała grupa."""

    INCLUSIVE = "INCLUSIVE", "na korzyść uczestników (cała grupa dostaje wyższą nagrodę)"
    EXCLUSIVE = "EXCLUSIVE", "w granicach puli (grupa, która by ją przekroczyła, dostaje niższą)"


PERCENT_VALIDATORS = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))]


class MedalScheme(models.Model):
    """Schemat nagród jednego etapu – i, po ogłoszeniu, zamrożony wynik."""

    # PROTECT, jak przy ``ResultsPublication``: ogłoszone medale są dokumentem zawodów, a nie
    # szczegółem etapu – skasowanie etapu ma się zatrzymać i wymusić świadomą decyzję.
    stage = models.OneToOneField(
        "competitions.Stage", on_delete=models.PROTECT, related_name="medal_scheme", verbose_name="etap"
    )
    gold_percent = models.DecimalField(
        "złoto – % uczestników",
        max_digits=5,
        decimal_places=2,
        default=Decimal("8"),
        validators=PERCENT_VALIDATORS,
    )
    silver_percent = models.DecimalField(
        "srebro – kolejne %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("17"),
        validators=PERCENT_VALIDATORS,
    )
    bronze_percent = models.DecimalField(
        "brąz – kolejne %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("25"),
        validators=PERCENT_VALIDATORS,
    )
    tie_policy = models.CharField(
        "remis na granicy puli", max_length=16, choices=TiePolicy.choices, default=TiePolicy.INCLUSIVE
    )
    #: Puste = kryterium procentowe wyróżnienia wyłączone (zostaje ewentualnie pełne rozwiązanie).
    hm_percent_of_best = models.DecimalField(
        "wyróżnienie – % najlepszego wyniku",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        default=Decimal("50"),
        validators=PERCENT_VALIDATORS,
    )
    hm_full_solution = models.BooleanField("wyróżnienie za pełne rozwiązanie zadania", default=True)
    updated_at = models.DateTimeField("zmieniony", default=timezone.now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="zmienił",
    )
    # --- zamrożony wynik ------------------------------------------------------------------------
    frozen_at = models.DateTimeField("ogłoszone", null=True, blank=True)
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="ogłosił",
    )
    #: ``ResultsPublication.published_at`` z chwili zamrożenia – po nim panel poznaje, że wyniki
    #: ogłoszono ponownie, a medale zostały przy poprzedniej tabeli.
    publication_published_at = models.DateTimeField(
        "publikacja wyników z chwili ogłoszenia", null=True, blank=True
    )
    #: ``{str(entry_id): {"award", "computed", "total", "rank", "overridden"}}`` – bez danych osobowych.
    awards = models.JSONField("nagrody per wpis", default=dict, blank=True)
    #: Progi punktowe, liczności i pole ``N`` z chwili zamrożenia.
    thresholds = models.JSONField("progi", default=dict, blank=True)
    #: Publiczna tabela: podpisy wierszy z ``results.services.build_snapshot`` (te same zgody).
    public_rows = models.JSONField("tabela publiczna", default=list, blank=True)
    #: Ranking krajów – wyłącznie agregaty.
    country_table = models.JSONField("ranking krajów", default=list, blank=True)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "schemat medali"
        verbose_name_plural = "schematy medali"
        ordering = ("-updated_at", "-id")

    def __str__(self) -> str:
        return f"medale etapu {self.stage_id}"

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None

    @property
    def cumulative_percents(self) -> tuple[Decimal, Decimal, Decimal]:
        """Odsetki łączne: złoto, złoto+srebro, złoto+srebro+brąz."""
        gold = self.gold_percent
        silver = gold + self.silver_percent
        return gold, silver, silver + self.bronze_percent


class MedalOverride(models.Model):
    """Ręczna nagroda jednego wpisu z uzasadnieniem. Jedna na (schemat, wpis)."""

    scheme = models.ForeignKey(MedalScheme, on_delete=models.CASCADE, related_name="overrides")
    #: CASCADE: wpis znika wyłącznie razem z kontem bez śladu w zawodach (``accounts.profile``),
    #: a wtedy decyzja o jego nagrodzie nie ma już kogo dotyczyć.
    entry = models.ForeignKey(
        "competitions.StageEntry",
        on_delete=models.CASCADE,
        related_name="medal_overrides",
        verbose_name="wpis",
    )
    award = models.CharField("nagroda", max_length=8, choices=Award.choices)
    justification = models.TextField("uzasadnienie", max_length=2000)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="wpisał",
    )
    created_at = models.DateTimeField("wpisano", default=timezone.now)
    updated_at = models.DateTimeField("zmieniono", default=timezone.now)

    objects = competition_scoped_manager("scheme__stage__edition__competition")

    class Meta:
        verbose_name = "ręczna nagroda"
        verbose_name_plural = "ręczne nagrody"
        ordering = ("scheme", "entry_id")
        constraints = [
            models.UniqueConstraint(fields=["scheme", "entry"], name="medals_override_one_per_entry"),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}: {self.award}"


class CertificateLanguage(models.Model):
    """Język wystawionego dokumentu – zamrożony przy wystawieniu (patrz docstring modułu)."""

    certificate = models.OneToOneField(
        "results.Certificate",
        on_delete=models.CASCADE,
        related_name="medal_language",
        verbose_name="dokument",
    )
    language = models.CharField("język", max_length=10)
    created_at = models.DateTimeField("utworzono", default=timezone.now)

    objects = competition_scoped_manager("certificate__edition__competition")

    class Meta:
        verbose_name = "język dokumentu"
        verbose_name_plural = "języki dokumentów"

    def __str__(self) -> str:
        return f"{self.certificate_id}: {self.language}"
