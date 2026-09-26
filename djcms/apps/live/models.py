"""Modele wtyczek żywych (§ 6.2 docs/tasks/DJ-01.md, wiersze „żywe”).

Reguła 1 z § 7 („CMS nie jest źródłem prawdy o zawodach”) jest tu zapisana w kształcie modeli:
żadna wtyczka żywa **nie ma pola z datą ani liczbą punktów**. Redaktor wybiera, *gdzie* terminy,
zadania i wyniki stoją na stronie, i może dopisać nagłówek albo komunikat – ale nie może wpisać
terminu, który rozjechałby się z tym, co egzekwuje serwer aplikacji głównej. Dane przychodzą przy
każdym renderowaniu z API (``apps.live.client``), a wtyczki mają ``cache = False``.

``ResultsPlugin`` i ``ArchiveResultsPlugin`` nie mają pól wcale (model ``CMSPlugin``): edycję
archiwalną wskazuje rozszerzenie strony ``ArchiveMeta`` (DJ-01e), a nie wtyczka – tak jak
``ArchiveEditionPage.edition`` w Wagtailu jest polem strony.
"""

from __future__ import annotations

from cms.models import CMSPlugin
from django.db import models


class StageTimeline(CMSPlugin):
    """Terminy etapów bieżącej edycji – port ``StageTimelineBlock`` i sekcji „Przebieg zawodów”.

    Dwa warianty tego samego źródła (``GET stages``), bo Wagtail ma dwa znaczniki tej samej osi:
    blok w treści (``cms/blocks/stage_timeline.html`` – karty z rubryką „Wyniki do”) i sekcję
    strony głównej (``home_page.html`` – nagłówek z edycją i przycisk „Tabela wyników”).
    Wariant jest wyborem redaktora, a nie typem strony: importer wstawia ``home`` do slotu
    ``timeline`` strony głównej, a ``block`` wszędzie tam, gdzie w StreamFieldzie stał blok.
    """

    class Variant(models.TextChoices):
        BLOCK = "block", "w treści strony (karty etapów)"
        HOME = "home", "strona główna („Przebieg zawodów”)"

    heading = models.CharField(
        "nagłówek sekcji",
        max_length=200,
        blank=True,
        help_text="Puste = same karty etapów, bez nagłówka. "
        "Wariant „strona główna” ma własny nagłówek z edycją.",
    )
    variant = models.CharField("wariant", max_length=8, choices=Variant.choices, default=Variant.BLOCK)

    class Meta:
        verbose_name = "terminy etapów (z systemu)"
        verbose_name_plural = "terminy etapów (z systemu)"

    def __str__(self) -> str:
        return self.heading or self.get_variant_display()


class Problems(CMSPlugin):
    """Zadania bieżącego etapu i arkusz treningowy – port części danych ``ProblemsPage``.

    Jedyne pole to komunikat pokazywany **przed** otwarciem etapu (``ProblemsPage.closed_notice``).
    Zadań, ich tytułów ani adresów PDF-ów wtyczka nie przechowuje – bierze je z ``GET problems``,
    które oddaje pustą listę, dopóki etap się nie otworzy (reguła 2 z § 7).
    """

    closed_notice = models.TextField(
        "komunikat przed otwarciem etapu",
        max_length=500,
        blank=True,
        help_text="Wyświetlany, dopóki etap się nie rozpocznie. Puste = komunikat systemowy.",
    )

    class Meta:
        verbose_name = "zadania etapu (z systemu)"
        verbose_name_plural = "zadania etapu (z systemu)"

    def __str__(self) -> str:
        return "zadania etapu"
