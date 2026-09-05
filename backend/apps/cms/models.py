"""Modele stron części informacyjnej (Wagtail).

Zasady, które te modele mają egzekwować:

- **CMS nie jest źródłem prawdy o zawodach.** Terminy, zadania i wyniki czytamy w ``get_context``
  z ``apps.competitions`` i ``apps.results`` – redaktor opisuje je słowem, ale nie przepisuje.
  Dzięki temu strona nie może pokazać innego deadline'u niż ten, który egzekwuje serwer.
- **Treść zadań jest jawna dopiero po ``Stage.opens_at``.** ``ProblemsPage`` przed otwarciem etapu
  pokazuje wyłącznie komunikat: żadnego tytułu zadania, żadnego linku do PDF (T-09, kryterium 3).
- **Tabela wyników pochodzi wyłącznie ze snapshotu.** ``ResultsPage`` czyta
  ``ResultsPublication.snapshot`` i nie dotyka ``FinalGrade`` ani danych uczestników
  (PROJEKT.md 2.4).
- **Treści redakcyjne renderują się przez filtr ``|richtext`` / ``{% include_block %}``** (Wagtail
  sanityzuje je whitelistą). W szablonach ``cms/`` nie ma ani jednego ``|safe``.
- **Redaktor nie może przesłonić adresu aplikacji.** Wagtail jest catch-allem w korzeniu, więc
  strona o slugu ``login`` na drugim poziomie drzewa miałaby adres ``/login/`` – ten sam, co widok
  logowania. Kolejność z ``config/urls.py`` sprawia, że wygrywa aplikacja, czyli strona byłaby
  po prostu nieosiągalna: redaktor widziałby „opublikowano”, a czytelnik formularz logowania.
  ``CMSPage.clean`` odrzuca takie slugi (patrz ``RESERVED_SLUGS``).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
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

from .blocks import RICH_TEXT_FEATURES, ArticleStreamBlock, DocumentStreamBlock

#: Adresy pierwszego segmentu, które należą do aplikacji (``config/urls.py`` + ``apps/web/urls.py``).
#: Strona CMS z takim slugiem na drugim poziomie drzewa byłaby martwa – patrz docstring modułu.
#: Lista jest jawna, a nie wyprowadzana z urlconfa: ``reverse()`` nie zna adresów, które dopiero
#: powstaną, a slug raz opublikowany zostaje w linkach i w wyszukiwarkach.
RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "appeals",
        "cms",
        "coordinator",
        "documents",
        "healthz",
        "login",
        "logout",
        "me",
        "media",
        "register",
        "results",
        "review",
        "static",
    }
)

#: Głębokość strony głównej w drzewie treebearda: ``Root`` ma 1, ``HomePage`` 2. Strony o adresie
#: jednosegmentowym (``/aktualnosci/``) są jej dziećmi, czyli mają ``depth == 3``.
HOME_PAGE_DEPTH = 2


class CMSPage(Page):
    """Wspólna baza stron części informacyjnej. Bez własnych pól – nie generuje migracji.

    Jedyne, co dokłada, to walidacja zarezerwowanych slugów. Sprawdzenie „czy to drugi poziom”
    ma dwie drogi, bo Wagtail woła ``full_clean`` w dwóch różnych momentach:

    - strona już w drzewie (edycja, przeniesienie) ma ``depth`` i porównujemy je wprost,
    - strona dopiero tworzona w ``/cms/`` **nie ma** jeszcze ani ``path``, ani ``depth`` (nadaje je
      ``add_child`` już po walidacji formularza). Zostaje wtedy deklaracja ``parent_page_types``:
      typy montowane pod stroną główną to dokładnie te, które trafią na drugi poziom.
    """

    class Meta:
        abstract = True

    def is_second_level(self) -> bool:
        """Czy strona jest (albo dopiero będzie) bezpośrednim dzieckiem strony głównej."""
        if self.depth:
            return self.depth == HOME_PAGE_DEPTH + 1
        return "cms.HomePage" in list(self.parent_page_types or ())

    def clean(self):
        super().clean()
        if self.slug in RESERVED_SLUGS and self.is_second_level():
            raise ValidationError(
                {
                    "slug": (
                        f"Adres „/{self.slug}/” należy do aplikacji (logowanie, panel, API). "
                        "Strona pod tym slugiem nigdy by się nie otworzyła – wybierz inny."
                    )
                }
            )


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


class HomePage(CMSPage):
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
    subpage_types = [
        "cms.NewsIndexPage",
        "cms.ProblemsPage",
        "cms.DocumentPage",
        "cms.ArchiveIndexPage",
        "cms.ResultsPage",
    ]
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


class NewsIndexPage(CMSPage):
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


class NewsPage(CMSPage):
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


class ProblemsPage(CMSPage):
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


class DocumentPage(CMSPage):
    """Dokument urzędowy (regulamin, ZOZ) w wersji do czytania w przeglądarce.

    Strona istnieje obok pliku, a nie zamiast niego: ``attachment`` wskazuje oryginał
    w bibliotece Wagtaila, więc czytelnik ma zarówno tekst z linkowalnymi kotwicami
    (``#par-16`` w piśmie do komisji odsyła w konkretne miejsce), jak i dokument, który
    da się wydrukować i podpisać. Treść to **dane**: struktura HTML pochodzi z konwersji
    pliku źródłowego, brzmienie zapisów – wyłącznie z niego.

    Metadane wersji (``version_label``/``document_date``/``status_label``) są osobnymi polami,
    a nie akapitem treści: przy dokumencie prawnym pierwsze pytanie czytelnika brzmi „czy to
    obowiązująca wersja”, więc odpowiedź nie może zależeć od tego, czy redaktor pamiętał
    o poprawieniu zdania w środku tekstu.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    body = StreamField(DocumentStreamBlock(), verbose_name="treść", blank=True)
    attachment = models.ForeignKey(
        "wagtaildocs.Document",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="plik źródłowy",
        help_text="Oryginał do pobrania (DOCX/PDF). Usunięcie pliku nie kasuje strony.",
    )
    version_label = models.CharField("wersja", max_length=50, blank=True)
    document_date = models.DateField("data dokumentu", null=True, blank=True)
    status_label = models.CharField(
        "status",
        max_length=200,
        blank=True,
        help_text="Np. „Projekt do zatwierdzenia uchwałą Zarządu”.",
    )

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [FieldPanel("version_label"), FieldPanel("document_date"), FieldPanel("status_label")],
            heading="Metryka dokumentu",
        ),
        FieldPanel("intro"),
        FieldPanel("attachment"),
        FieldPanel("body"),
    ]
    search_fields = Page.search_fields + [
        index.SearchField("intro"),
        index.SearchField("body"),
        index.FilterField("document_date"),
    ]

    template = "cms/document_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = []

    class Meta:
        verbose_name = "dokument"
        verbose_name_plural = "dokumenty"

    def chapters(self) -> list[dict]:
        """Spis rozdziałów: śródtytuły poziomu 2 oznaczone „pokaż w spisie”.

        Liczymy z ``body``, a nie z wyrenderowanego HTML-a – parsowanie własnego wyjścia
        po to, by znaleźć w nim ``<h2 id=…>``, robiłoby ze spisu treści funkcję szablonu.
        """
        return [
            {"anchor": block.value["anchor"], "text": block.value["text"]}
            for block in self.body
            if block.block_type == "heading" and block.value.get("level") == "2" and block.value.get("in_toc")
        ]


class ArchiveIndexPage(CMSPage):
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


class ArchiveEditionPage(CMSPage):
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

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # ``select_related`` zamiast ``page.documents.all`` w szablonie: każdy wiersz sięga po
        # ``item.document.url`` i ``file_extension``, więc bez tego archiwum z dwudziestoma
        # materiałami robi dwadzieścia jeden zapytań zamiast jednego.
        context["documents"] = self.documents.select_related("document")
        return context

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


class ResultsPage(CMSPage):
    """Publiczna tabela wyników: etapy z ogłoszoną publikacją plus wbudowany snapshot.

    Strona nie czyta ani ``StageEntry``, ani ``Participant`` – wyłącznie zamrożony
    ``ResultsPublication.snapshot`` (T-09, kryterium 6).

    Dwie decyzje o kształcie tej strony:

    - **jedno zapytanie na całość.** Wchodzimy od strony publikacji (``ResultsPublication``),
      a nie od etapów, i dociągamy ``stage__edition`` przez ``select_related``. Wariant „lista
      etapów, a potem publikacja per etap” rósł liniowo z liczbą ogłoszonych etapów, a rośnie ona
      z każdą edycją i nigdy nie maleje,
    - **pełne tabele tylko dla bieżącej edycji.** Archiwalne edycje zostają linkiem do
      ``/results/<id>/``. Snapshot finału to tysiące wierszy; sklejenie wszystkich roczników
      w jeden dokument HTML dawałoby stronę rosnącą bez końca, którą i tak nikt nie przewinie.
      Stare tabele nie znikają – mają własny adres i archiwum.
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
        edition = current_edition()
        # Filtr po ``results_published_at`` zostaje: znacznik na etapie jest tym, co koordynator
        # zdejmuje, żeby wycofać ogłoszenie, a sam rekord publikacji ma zostać jako ślad.
        publications = (
            ResultsPublication.objects.filter(stage__results_published_at__isnull=False)
            .select_related("stage", "stage__edition")
            .order_by("-stage__results_published_at", "-stage_id")
        )
        tables: list[dict] = []
        archive: list[dict] = []
        for publication in publications:
            stage = publication.stage
            if edition is not None and stage.edition_id == edition.pk:
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
            else:
                archive.append({"stage": stage, "publication": publication})
        context.update({"edition": edition, "tables": tables, "archive": archive})
        return context
