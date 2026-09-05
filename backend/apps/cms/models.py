"""Modele stron części informacyjnej (Wagtail).

Zasady, które te modele mają egzekwować:

- **CMS nie jest źródłem prawdy o zawodach.** Terminy, zadania i wyniki czytamy w ``get_context``
  z ``apps.competitions`` i ``apps.results`` – redaktor opisuje je słowem, ale nie przepisuje.
  Dzięki temu strona nie może pokazać innego deadline'u niż ten, który egzekwuje serwer.
- **Treść zadań jest jawna dopiero po ``Stage.opens_at``.** ``ProblemsPage`` przed otwarciem etapu
  pokazuje wyłącznie komunikat: żadnego tytułu zadania, żadnego linku do PDF (T-09, kryterium 3).
- **Tabela wyników pochodzi wyłącznie ze snapshotu.** ``ResultsPage`` czyta
  ``ResultsPublication.snapshot`` przez ``apps.results.services.published_results`` i nie dotyka
  ``FinalGrade`` ani danych uczestników (PROJEKT.md 2.4).
- **Treści redakcyjne renderują się przez filtr ``|richtext`` / ``{% include_block %}``** (Wagtail
  sanityzuje je whitelistą). W szablonach ``cms/`` nie ma ani jednego ``|safe``.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page
from wagtail.search import index

from apps.competitions.models import Edition, Stage
from apps.competitions.services import current_edition, current_stage
from apps.results.models import ResultsPublication
from apps.results.services import published_results

from .blocks import RICH_TEXT_FEATURES, ArticleStreamBlock


def _stage_rows(edition: Edition | None, now=None) -> list[dict]:
    """Oś czasu etapów edycji wraz z informacją, czy wyniki są już ogłoszone.

    Jedno zapytanie o etapy i jedno o publikacje – bez N+1 niezależnie od liczby etapów.
    """
    if edition is None:
        return []
    now = now or timezone.now()
    stages = list(edition.stages.order_by("opens_at", "id"))
    published = set(ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True))
    return [
        {
            "stage": stage,
            "is_open": stage.is_open_for_submissions(now),
            "has_opened": stage.has_opened(now),
            "has_results": stage.pk in published,
        }
        for stage in stages
    ]


class HomePage(Page):
    """Strona główna serwisu (korzeń witryny). Przejmuje ``/`` po widoku ``web:home`` z T-08."""

    hero_title = models.CharField("nagłówek", max_length=200, blank=True)
    hero_text = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    show_timeline = models.BooleanField(
        "pokaż oś czasu bieżącej edycji",
        default=True,
        help_text="Tabela etapów z terminami i linkami do ogłoszonych wyników.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_text"),
        FieldPanel("show_timeline"),
    ]
    search_fields = Page.search_fields + [index.SearchField("hero_title")]

    template = "cms/home_page.html"
    # Strona główna jest korzeniem witryny – nie wolno jej zagnieżdżać pod inną stroną treści.
    parent_page_types = ["wagtailcore.Page"]
    subpage_types = ["cms.NewsIndexPage", "cms.ProblemsPage", "cms.ArchiveIndexPage", "cms.ResultsPage"]
    max_count = 1

    class Meta:
        verbose_name = "strona główna"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        now = timezone.now()
        edition = current_edition()
        context.update(
            {
                "now": now,
                "edition": edition,
                "current_stage": current_stage(edition, now) if edition else None,
                "stage_rows": _stage_rows(edition, now),
                "latest_news": NewsPage.objects.live().descendant_of(self).order_by("-date", "-pk")[:3],
            }
        )
        return context


class NewsIndexPage(Page):
    """Newsroom: lista aktualności. Sama nie ma treści poza wprowadzeniem."""

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [FieldPanel("intro")]

    template = "cms/news_index_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = ["cms.NewsPage"]

    class Meta:
        verbose_name = "aktualności"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # ``live()`` – wersje robocze nie mogą wyciec na listę publiczną.
        context["news"] = NewsPage.objects.live().child_of(self).order_by("-date", "-pk")
        return context


class NewsPage(Page):
    """Pojedyncza aktualność: data, lead i treść w StreamField (akapit/obraz/dokument/embed)."""

    date = models.DateField("data publikacji", default=timezone.localdate)
    lead = models.TextField("lead", max_length=500, blank=True)
    body = StreamField(ArticleStreamBlock(), verbose_name="treść", blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("date"),
        FieldPanel("lead"),
        FieldPanel("body"),
    ]
    search_fields = Page.search_fields + [
        index.SearchField("lead"),
        index.SearchField("body"),
        index.FilterField("date"),
    ]

    template = "cms/news_page.html"
    parent_page_types = ["cms.NewsIndexPage"]
    subpage_types = []

    class Meta:
        verbose_name = "aktualność"
        verbose_name_plural = "aktualności"


class ProblemsPage(Page):
    """Zadania bieżącego etapu. Treści PDF pokazujemy dopiero po ``Stage.opens_at``."""

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    closed_notice = models.TextField(
        "komunikat przed otwarciem etapu",
        max_length=500,
        blank=True,
        help_text="Wyświetlany, dopóki etap się nie rozpocznie. Domyślnie komunikat systemowy.",
    )
    body = StreamField(ArticleStreamBlock(), verbose_name="treści dodatkowe", blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("closed_notice"),
        FieldPanel("body"),
    ]

    template = "cms/problems_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = []

    class Meta:
        verbose_name = "zadania"
        verbose_name_plural = "zadania"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        now = timezone.now()
        edition = current_edition()
        stage = current_stage(edition, now) if edition else None
        # Jedyne miejsce decydujące o jawności treści. ``problems`` zostaje puste, dopóki etap
        # się nie otworzy – szablon nie ma z czego zrenderować ani tytułu, ani linku do PDF.
        has_opened = bool(stage and stage.has_opened(now))
        context.update(
            {
                "now": now,
                "edition": edition,
                "stage": stage,
                "stage_has_opened": has_opened,
                "problems": list(stage.problems.order_by("number")) if has_opened else [],
                "notice": self.closed_notice
                or "Treści zadań tego etapu zostaną opublikowane w chwili jego otwarcia.",
            }
        )
        return context


class ArchiveIndexPage(Page):
    """Archiwum edycji: lista stron ``ArchiveEditionPage``."""

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [FieldPanel("intro")]

    template = "cms/archive_index_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = ["cms.ArchiveEditionPage"]

    class Meta:
        verbose_name = "archiwum"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["editions"] = (
            ArchiveEditionPage.objects.live().child_of(self).select_related("edition").order_by("-pk")
        )
        return context


class ArchiveEditionPage(Page):
    """Jedna edycja w archiwum: opis, dokumenty (zadania/rozwiązania), linki do wyników etapów."""

    edition = models.ForeignKey(
        Edition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archive_pages",
        verbose_name="edycja",
        help_text="Powiązanie z edycją w bazie: stąd biorą się etapy i linki do wyników.",
    )
    summary = RichTextField("podsumowanie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [
        MultiFieldPanel([FieldPanel("edition"), FieldPanel("summary")], heading="Edycja"),
        InlinePanel("documents", label="dokumenty"),
    ]
    search_fields = Page.search_fields + [index.SearchField("summary")]

    template = "cms/archive_edition_page.html"
    parent_page_types = ["cms.ArchiveIndexPage"]
    subpage_types = []

    class Meta:
        verbose_name = "edycja w archiwum"
        verbose_name_plural = "edycje w archiwum"

    def result_links(self) -> list[dict]:
        """Etapy edycji, dla których istnieje **ogłoszona** tabela wyników.

        Bez publikacji nie ma linku: publiczny widok ``/results/<id>/`` i tak odpowiada 404,
        a martwy odnośnik sugerowałby, że wyniki są, tylko schowane.
        """
        if self.edition_id is None:
            return []
        stages = list(Stage.objects.filter(edition_id=self.edition_id).order_by("opens_at", "id"))
        published = set(
            ResultsPublication.objects.filter(stage__in=stages).values_list("stage_id", flat=True)
        )
        return [{"stage": stage} for stage in stages if stage.pk in published]


class ArchiveDocument(Orderable):
    """Dokument przypięty do edycji archiwalnej: treść zadań albo rozwiązania."""

    class Kind(models.TextChoices):
        PROBLEMS = "PROBLEMS", "zadania"
        SOLUTIONS = "SOLUTIONS", "rozwiązania"
        OTHER = "OTHER", "inne"

    page = ParentalKey(ArchiveEditionPage, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField("rodzaj", max_length=16, choices=Kind.choices, default=Kind.PROBLEMS)
    title = models.CharField("etykieta", max_length=200)
    document = models.ForeignKey(
        "wagtaildocs.Document",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="plik",
    )

    panels = [FieldPanel("kind"), FieldPanel("title"), FieldPanel("document")]

    class Meta(Orderable.Meta):
        verbose_name = "dokument archiwum"
        verbose_name_plural = "dokumenty archiwum"

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.title}"


class ResultsPage(Page):
    """Publiczna tabela wyników: etapy z ogłoszoną publikacją plus wbudowany snapshot.

    Strona nie czyta ani ``StageEntry``, ani ``Participant`` – wyłącznie zamrożony
    ``ResultsPublication.snapshot`` (T-09, kryterium 6).
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [FieldPanel("intro")]

    template = "cms/results_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = []

    class Meta:
        verbose_name = "wyniki"
        verbose_name_plural = "wyniki"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        stages = Stage.objects.filter(results_published_at__isnull=False).select_related("edition")
        tables = []
        for stage in stages.order_by("-results_published_at", "-id"):
            publication = published_results(stage.pk)
            if publication is None:
                # Znacznik na etapie bez publikacji: tabela nie istnieje, więc nic nie pokazujemy.
                continue
            rows = publication.rows
            tables.append(
                {
                    "stage": stage,
                    "publication": publication,
                    "rows": rows,
                    "problem_numbers": sorted(
                        {key for row in rows for key in (row.get("points") or {})},
                        key=lambda value: (len(value), value),
                    ),
                }
            )
        context["tables"] = tables
        return context
