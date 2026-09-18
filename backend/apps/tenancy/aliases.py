"""Druga witryna tego samego konkursu: ``CompetitionSiteAlias`` (wielojęzyczność treści, § 1.6).

Wagtail trzyma tłumaczenie strony jako **osobny węzeł drzewa** we własnym ``Locale``, a jedna
``wagtailcore.Site`` ma dokładnie jeden ``root_page``. Drugie drzewo językowe znaczy więc drugą
witrynę – np. ``en.olimpiadafizyczna.pl`` obok ``olimpiadafizyczna.pl``. ``Competition.site``
zostaje ``OneToOne`` (druga witryna w tamtym polu znaczyłaby drugi konkurs, patrz
``models.Competition``), a przynależność drugiej witryny do tego samego konkursu opisuje wiersz
tutaj: **konkurs + witryna + język**.

Dlaczego nie ``i18n_patterns``: prefiks języka zrobiłby z każdego adresu dwa
(``/en/dokumenty/regulamin/`` obok ``/dokumenty/regulamin/``), a adresy serwisu są wklejone
w listy, regulamin i pisma. Pełny dowód: ``docs/UNIWERSALNY-ETAP-2.md`` § 1.6.2, decyzja D17.

Dlaczego osobny moduł, a nie ``models.py``: podział własności plików etapu 2 (§ 4.3) – katalog
przełączników w ``models.py`` ma jednego właściciela i dokłada do niego wszystkie flagi naraz.
Model rejestruje się przez import w ``TenancyConfig.ready`` (``apps/tenancy/apps.py``); etykieta
aplikacji jest podana wprost, żeby nie zależała od tego, skąd moduł zostanie zaimportowany.

**Koszt na żądanie dla Konkursu #1: zero.** Gałąź aliasów w ``resolve_for_request`` istnieje
wyłącznie przy ``WAGTAIL_I18N_ENABLED`` (domyślnie wyłączone), a jej włączenie nie dokłada
zapytania – dopisuje alternatywę do **tego samego** zapytania o konkurs. Konkurs bez ani jednego
aliasu nie wchodzi w nią ani razu, dokładnie tak, jak w gałąź prefiksu ścieżki z etapu 1 § 2.3.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.tenancy.models import Competition


def content_translations_enabled() -> bool:
    """Czy instalacja w ogóle prowadzi drzewa stron w kilku językach.

    Czytamy ``WAGTAIL_I18N_ENABLED`` przez ``getattr``, bo to ustawienie Wagtaila, nie nasze:
    instalacja, która go nie ma (starsza konfiguracja, test z ``override_settings``), ma się
    zachować jak instalacja jednojęzyczna, a nie wywrócić na ``AttributeError``.
    """
    return bool(getattr(settings, "WAGTAIL_I18N_ENABLED", False))


def alias_match(site) -> Q:
    """Alternatywa „konkurs tej witryny **albo** konkurs, dla którego ta witryna jest aliasem”.

    Puste ``Q()`` znaczy „nie dokładaj niczego”: Django łączy je z alternatywą bez śladu
    w zapytaniu (``Q._combine`` oddaje drugi człon), więc zapytanie instalacji jednojęzycznej
    zostaje **tym samym** zapytaniem, co przed tą zmianą – z tym samym planem i tą samą liczbą
    złączeń, a nie tylko z tą samą liczbą zapytań.
    """
    if site is None or not content_translations_enabled():
        return Q()
    return Q(site_aliases__site=site)


class CompetitionSiteAlias(models.Model):
    """Druga (trzecia, …) witryna konkursu: ta sama olimpiada, inny język treści.

    Wiersz nie niesie żadnych danych zawodów – jest wyłącznie odpowiedzią na pytanie „czyj jest
    ten host”. Dlatego kasowanie jest tu ``CASCADE`` z obu stron, a nie ``PROTECT`` jak przy
    ``Competition.site``: zdjęcie angielskiej witryny w ``/cms/`` ma zdjąć alias, a nie zablokować
    redaktorowi kasowanie witryny, której drzewo i tak już usunął.
    """

    #: Konkurs, którego dotyczy alias. Bez ``related_name="+"``, bo pytamy w obie strony:
    #: rozstrzyganie żądania idzie od witryny, a panel operatora – od konkursu.
    competition = models.ForeignKey(
        Competition,
        on_delete=models.CASCADE,
        related_name="site_aliases",
        verbose_name="konkurs",
    )
    #: ``OneToOne``: jedna witryna serwuje jeden konkurs. Gdyby dwa konkursy mogły wskazać ten sam
    #: host, rozstrzyganie żądania miałoby dwie odpowiedzi i jedna z nich byłaby wyciekiem.
    site = models.OneToOneField(
        "wagtailcore.Site",
        on_delete=models.CASCADE,
        related_name="competition_alias",
        verbose_name="witryna",
    )
    #: Język **treści**, czyli ``Locale`` drzewa stron tej witryny – nie język interfejsu.
    #: ``PROTECT``: skasowanie ``Locale`` używanego przez witrynę osierociłoby całe drzewo stron,
    #: a Wagtail kasuje wtedy strony razem z nim.
    locale = models.ForeignKey(
        "wagtailcore.Locale",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="język treści",
    )
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    class Meta:
        # Etykieta aplikacji wprost: model stoi poza ``models.py`` (patrz docstring modułu),
        # a domysł Django opiera się na ścieżce modułu, którą łatwiej przypadkiem zmienić niż
        # jedną linijkę deklaracji.
        app_label = "tenancy"
        verbose_name = "alias witryny konkursu"
        verbose_name_plural = "aliasy witryn konkursów"
        ordering = ("competition", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("competition", "locale"),
                name="tenancy_one_site_per_competition_language",
            )
        ]

    def __str__(self) -> str:
        return f"{self.site.hostname} → {self.competition} ({self.locale.language_code})"

    def clean(self) -> None:
        """Trzy reguły, których złamanie widać dopiero jako cudzą stronę pod własnym adresem.

        Walidacja jest w modelu, a nie w formularzu ``/admin/``, bo alias zakłada tak samo dobrze
        komenda i migracja – formularz jest tylko jednym z wołających (ta sama reguła, co
        w ``Competition.clean``).
        """
        super().clean()
        errors: dict[str, str] = {}

        if self.site_id:
            owner = Competition.objects.filter(site_id=self.site_id).first()
            if owner is not None:
                # Także wtedy, gdy właścicielem jest **ten sam** konkurs: witryna główna jest już
                # jego witryną i drugi wiersz mówiący to samo dawałby dwie odpowiedzi na pytanie
                # „w jakim języku jest ta domena”.
                errors["site"] = f"Ta witryna jest witryną główną konkursu „{owner}” i nie może być aliasem."
            elif self.locale_id and self.site.root_page.locale_id != self.locale_id:
                # Alias mówi „ta domena serwuje ten konkurs w tym języku”. Korzeń drzewa w innym
                # języku znaczy, że serwuje w innym – i nikt by się o tym nie dowiedział, bo
                # strona otwierałaby się poprawnie, tylko nie w tym języku, co deklaracja.
                errors["locale"] = "Korzeń drzewa stron tej witryny jest w innym języku niż wskazany."

        if errors:
            raise ValidationError(errors)
