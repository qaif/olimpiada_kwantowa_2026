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

from html import unescape

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page
from wagtail.search import index

from apps.competitions.models import Edition, Stage
from apps.competitions.services import current_edition, current_stage
from apps.results.models import ResultsPublication

from .blocks import RICH_TEXT_FEATURES, ArticleStreamBlock, DocumentStreamBlock, StepsStreamBlock

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


@register_setting(icon="site")
class SiteSettings(BaseSiteSetting):
    """Nazwa serwisu, hasło i dane organizatora – jedno miejsce dla nagłówka i stopki.

    Te wartości zmieniają się poza rytmem wydań (zmiana adresu fundacji, numeru KRS, telefonu),
    więc nie mogą mieszkać w szablonie ani w ``settings.py``: każda taka poprawka byłaby wtedy
    deployem. Ustawienie jest per-witryna (``BaseSiteSetting``), bo domena publiczna i domena
    stagingu to w Wagtailu dwa obiekty ``Site``.

    Wartości początkowe ustawia migracja danych ``cms.0006`` – dzięki temu świeża baza ma pełne
    dane organizatora jeszcze przed pierwszym wejściem redaktora do ``/cms/``.
    """

    site_name = models.CharField("nazwa serwisu", max_length=100, default="Olimpiada Kwantowa")
    tagline = models.CharField(
        "hasło",
        max_length=200,
        blank=True,
        default="Przyszłość ma naturę kwantową.",
        help_text="Zdanie pod logotypem i w nagłówku strony głównej.",
    )
    organizer_name = models.CharField("organizator", max_length=200, default="Fundacja Quantum AI")
    organizer_address = models.CharField(
        "adres organizatora", max_length=200, blank=True, default="ul. Sanocka 9/103, 02-110 Warszawa"
    )
    organizer_registry = models.CharField(
        "dane rejestrowe",
        max_length=200,
        blank=True,
        default="KRS 0000808359 · NIP 7010955891 · REGON 384899425",
    )
    contact_email = models.EmailField("e-mail kontaktowy", blank=True, default="contact@qaif.org")
    contact_phone = models.CharField("telefon", max_length=40, blank=True, default="+48 507 982 292")
    contact_url = models.URLField("strona organizatora", blank=True, default="https://www.qaif.org/")

    panels = [
        MultiFieldPanel([FieldPanel("site_name"), FieldPanel("tagline")], heading="Serwis"),
        MultiFieldPanel(
            [
                FieldPanel("organizer_name"),
                FieldPanel("organizer_address"),
                FieldPanel("organizer_registry"),
            ],
            heading="Organizator",
        ),
        MultiFieldPanel(
            [FieldPanel("contact_email"), FieldPanel("contact_phone"), FieldPanel("contact_url")],
            heading="Kontakt",
        ),
    ]

    class Meta:
        verbose_name = "dane serwisu"
        verbose_name_plural = "dane serwisu"


def body_chapters(body) -> list[dict]:
    """Spis sekcji: śródtytuły poziomu 2 oznaczone „pokaż w spisie”.

    Liczymy z ``body``, a nie z wyrenderowanego HTML-a – parsowanie własnego wyjścia po to, by
    znaleźć w nim ``<h2 id=…>``, robiłoby ze spisu treści funkcję szablonu.
    """
    return [
        {"anchor": block.value["anchor"], "text": block.value["text"]}
        for block in body
        if block.block_type == "heading" and block.value.get("level") == "2" and block.value.get("in_toc")
    ]


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


def _download_rows(home) -> list[dict]:
    """Opublikowane strony, przy których wisi PDF – materiał sekcji „Dokumenty do pobrania”.

    Sekcja nie zna ani jednego sluga: pokazuje to, co redakcja faktycznie przypięła do stron,
    więc dołożenie kolejnego dokumentu w ``/cms/`` nie wymaga wydania aplikacji. Filtr po PDF
    jest świadomy – na stronie głównej ma stać lista dokumentów urzędowych do wydruku, a nie
    każdy plik pomocniczy (arkusz, plik źródłowy .docx), który redakcja gdzieś przypięła.

    Zapytań jest tyle, ile typów stron z załącznikami (dwa), plus jedno wspólne dla plików
    każdego z nich – ``prefetch_related`` zdejmuje N+1 niezależnie od liczby dokumentów.
    Kolejność bierzemy z drzewa (``path``), czyli tę samą, co w menu serwisu.
    """
    pages = [
        *DocumentPage.objects.live().descendant_of(home).prefetch_related("attachments__document"),
        *ContentPage.objects.live().descendant_of(home).prefetch_related("attachments__document"),
    ]
    rows = [
        {"page": page, "attachment": pdf}
        for page in pages
        # ``attachments.all()`` czyta bufor ``prefetch_related``; ``.filter()`` puściłby zapytanie.
        if (pdf := next((item for item in page.attachments.all() if item.is_pdf), None)) is not None
    ]
    rows.sort(key=lambda row: row["page"].path)
    return rows


class HomePage(CMSPage):
    """Strona główna serwisu (korzeń witryny). Przejmuje ``/`` po widoku ``web:home`` z T-08."""

    hero_title = models.CharField("nagłówek", max_length=200, blank=True)
    hero_text = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    show_timeline = models.BooleanField(
        "pokaż oś czasu bieżącej edycji",
        default=True,
        help_text="Tabela etapów z terminami i linkami do ogłoszonych wyników.",
    )
    steps_title = models.CharField(
        "nagłówek sekcji „jak zacząć”",
        max_length=200,
        blank=True,
        help_text="Puste = sekcja kroków się nie pokazuje.",
    )
    steps = StreamField(StepsStreamBlock(), verbose_name="kroki", blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_text"),
        FieldPanel("show_timeline"),
        MultiFieldPanel([FieldPanel("steps_title"), FieldPanel("steps")], heading="Jak zacząć"),
    ]
    search_fields = Page.search_fields + [index.SearchField("hero_title")]

    template = "cms/home_page.html"
    # Strona główna jest korzeniem witryny – nie wolno jej zagnieżdżać pod inną stroną treści.
    parent_page_types = ["wagtailcore.Page"]
    # ``DocumentPage`` nie stoi już bezpośrednio pod stroną główną: dokumenty organizatora mieszkają
    # w sekcji ``/dokumenty/`` (``DocumentIndexPage``), żeby menu miało jedną pozycję zamiast czterech,
    # a czytelnik – jedno miejsce, w którym leży komplet.
    subpage_types = [
        "cms.NewsIndexPage",
        "cms.ProblemsPage",
        "cms.DocumentIndexPage",
        "cms.ContentPage",
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
                "downloads": _download_rows(self),
                # Sekcja „Dokumenty do pobrania” prowadzi do pełnej listy; strona-indeks bywa
                # nieopublikowana (świeża baza przed seedem), więc szablon pyta o ``None``.
                "documents_index": DocumentIndexPage.objects.live().child_of(self).first(),
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


class ContentPage(CMSPage):
    """Zwykła strona redakcyjna: „O Olimpiadzie”, „Kontakt”, „Jak zacząć?”.

    Powstała przy imporcie starej strony (``docs/import/stara-strona-inwentarz.md``, punkt 2a):
    osiem podstron WordPressa to był tekst z nagłówkami, listami i okazjonalną tabelą, czyli coś,
    czego żaden istniejący typ nie obsługiwał. ``NewsPage`` ma datę i lead (strona „Kontakt” nie
    jest datowana), a ``DocumentPage`` metrykę wersji i załącznik (strona „Jak zacząć?” nie jest
    dokumentem, którego wersję ktoś cytuje w piśmie).

    Zestaw bloków jest ten sam, co w dokumencie (``DocumentStreamBlock``) – śródtytuł z jawną
    kotwicą i ramka informacyjna przydają się tak samo w treści redakcyjnej, a jeden wspólny
    zestaw oznacza, że przeniesienie akapitu między stroną a dokumentem nie gubi bloku.

    ``show_in_menu`` jest osobnym polem obok wagtailowego ``show_in_menus``: to drugie steruje
    całym menu Wagtaila, a redaktor pyta wprost „czy ta strona ma być w pasku u góry”. Wartość
    przepisujemy na ``show_in_menus`` przy zapisie, żeby istniało jedno źródło prawdy dla
    ``context_processors.cms_menu``.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    body = StreamField(DocumentStreamBlock(), verbose_name="treść", blank=True)
    show_in_menu = models.BooleanField(
        "pokaż w menu głównym",
        default=False,
        help_text="Pozycja w pasku nawigacji na górze serwisu.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("show_in_menu"),
        FieldPanel("intro"),
        InlinePanel("attachments", label="pliki do pobrania"),
        FieldPanel("body"),
    ]
    search_fields = Page.search_fields + [index.SearchField("intro"), index.SearchField("body")]

    template = "cms/content_page.html"
    parent_page_types = ["cms.HomePage", "cms.ContentPage"]
    subpage_types = ["cms.ContentPage"]

    class Meta:
        verbose_name = "strona treści"
        verbose_name_plural = "strony treści"

    #: Poniżej tej liczby śródtytułów spis sekcji jest dłuższy od tego, co spisuje.
    MIN_CHAPTERS_FOR_TOC = 3

    def save(self, *args, **kwargs):
        self.show_in_menus = self.show_in_menu
        super().save(*args, **kwargs)

    def chapters(self) -> list[dict]:
        """Spis sekcji – pusty, dopóki nagłówków jest mniej niż trzy."""
        chapters = body_chapters(self.body)
        return chapters if len(chapters) >= self.MIN_CHAPTERS_FOR_TOC else []

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["attachments"] = self.attachments.select_related("document")
        return context


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


class DocumentIndexPage(CMSPage):
    """Sekcja ``/dokumenty/``: jedno miejsce na komplet dokumentów organizatora.

    Powstała, bo dokumenty rozeszły się po pasku nawigacji: regulamin stał między „Zadaniami”
    a „Archiwum”, skład komitetów – zaraz za „O Olimpiadzie”, a polityka RODO i standardy ochrony
    małoletnich nie były w menu w ogóle (prowadziła do nich tylko strona główna). Czytelnik, który
    szuka „dokumentów olimpiady”, nie ma wtedy jednego adresu do zapamiętania ani jednej strony
    do podania w piśmie.

    Strona jest wyłącznie spisem: ``get_context`` czyta opublikowane dzieci wraz z ich plikami
    (``prefetch_related`` – karta każdego dokumentu pokazuje rozszerzenie i rozmiar załącznika),
    a kolejność bierze z drzewa, czyli tę samą, którą redaktor widzi w ``/cms/`` i którą pokazuje
    rozwijana pozycja menu.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [FieldPanel("intro")]
    search_fields = Page.search_fields + [index.SearchField("intro")]

    template = "cms/document_index_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = ["cms.DocumentPage"]
    max_count = 1

    class Meta:
        verbose_name = "dokumenty"
        verbose_name_plural = "dokumenty"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["documents"] = (
            DocumentPage.objects.live()
            .child_of(self)
            .order_by("path")
            .prefetch_related("attachments__document")
        )
        return context


class DocumentPage(CMSPage):
    """Dokument urzędowy (regulamin, ZOZ) w wersji do czytania w przeglądarce.

    Strona istnieje obok pliku, a nie zamiast niego: ``attachments`` wskazują oryginały
    w bibliotece Wagtaila, więc czytelnik ma zarówno tekst z linkowalnymi kotwicami
    (``#par-16`` w piśmie do komisji odsyła w konkretne miejsce), jak i dokument, który
    da się wydrukować i podpisać. Treść to **dane**: struktura HTML pochodzi z konwersji
    pliku źródłowego, brzmienie zapisów – wyłącznie z niego.

    Załączników jest wiele i mają kolejność (``DocumentPageAttachment``), bo jeden dokument
    bywa opublikowany w dwóch postaciach: PDF podpisany przez organizatora (to on jest wersją
    do druku i do cytowania) oraz plik źródłowy .docx. Pojedyncze pole ``attachment`` zmuszało
    do wyboru, którą z nich pokazać – a czytelnik szukający „regulaminu do wydruku” i redakcja
    szukająca źródła to dwie różne potrzeby.

    Metadane wersji (``version_label``/``document_date``/``status_label``) są osobnymi polami,
    a nie akapitem treści: przy dokumencie prawnym pierwsze pytanie czytelnika brzmi „czy to
    obowiązująca wersja”, więc odpowiedź nie może zależeć od tego, czy redaktor pamiętał
    o poprawieniu zdania w środku tekstu.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    body = StreamField(DocumentStreamBlock(), verbose_name="treść", blank=True)
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
        InlinePanel("attachments", label="pliki do pobrania"),
        FieldPanel("body"),
    ]
    search_fields = Page.search_fields + [
        index.SearchField("intro"),
        index.SearchField("body"),
        index.FilterField("document_date"),
    ]

    template = "cms/document_page.html"
    parent_page_types = ["cms.DocumentIndexPage"]
    subpage_types = []

    #: Długość zajawki na karcie w spisie ``/dokumenty/``. Trzy wiersze przy szerokości karty –
    #: dłuższy fragment zamieniłby spis w kopię wprowadzeń, krótszy nie odróżniłby dokumentów.
    SUMMARY_WORDS = 28

    class Meta:
        verbose_name = "dokument"
        verbose_name_plural = "dokumenty"

    def chapters(self) -> list[dict]:
        """Spis rozdziałów – patrz ``body_chapters``. Dokument pokazuje go od pierwszego rozdziału."""
        return body_chapters(self.body)

    def summary(self) -> str:
        """Zajawka na kartę spisu: pierwsze zdania wprowadzenia jako czysty tekst.

        Liczymy ją tu, a nie filtrem w szablonie: ``intro`` jest polem RichText, więc zawiera
        znaczniki i encje, a spis ma pokazać zdanie, nie kod. Kiedy redaktor nie napisał
        wprowadzenia, zostaje opis wyszukiwarkowy – jeśli i on jest pusty, karta pokazuje
        samą metrykę zamiast wymyślonego opisu.
        """
        # Spacja przed każdym znacznikiem: ``</p><p>`` bez niej sklejałoby ostatnie słowo akapitu
        # z pierwszym słowem następnego („…Quantum AIRegulamin…”). Nadmiarowe odstępy zbieramy
        # niżej, więc do zajawki trafia zwykły tekst z pojedynczymi spacjami.
        text = " ".join(unescape(strip_tags((self.intro or "").replace("<", " <"))).split())
        if not text:
            return self.search_description.strip()
        return Truncator(text).words(self.SUMMARY_WORDS, truncate="…")

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # ``select_related`` – karta „Do pobrania” sięga po ``document.url``, ``filename``
        # i ``get_file_size`` w każdym wierszu; bez tego dwa pliki to trzy zapytania.
        context["attachments"] = self.attachments.select_related("document")
        return context


class PageAttachment(Orderable):
    """Wspólna baza plików do pobrania przy stronie. Abstrakcyjna – nie ma własnej tabeli.

    ``PROTECT`` zamiast ``CASCADE``: usunięcie pliku w bibliotece Wagtaila nie może po cichu
    zdjąć odnośnika ze strony regulaminu. Redaktor dostaje wtedy komunikat o powiązaniu i musi
    najpierw odpiąć plik od strony – czyli podjąć tę decyzję świadomie.

    ``label`` jest opisem roli pliku („PDF do druku”, „Wersja źródłowa (DOCX)”), a nie jego
    nazwą: tytuł dokumentu w bibliotece odpowiada na pytanie „co to za plik”, etykieta –
    „po co miałbym go pobrać”.
    """

    document = models.ForeignKey(
        "wagtaildocs.Document",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="plik",
    )
    label = models.CharField(
        "etykieta",
        max_length=100,
        blank=True,
        help_text="Rola pliku, np. „PDF do druku”. Puste = tytuł pliku z biblioteki.",
    )

    panels = [FieldPanel("document"), FieldPanel("label")]

    class Meta(Orderable.Meta):
        abstract = True

    def __str__(self) -> str:
        return self.label or self.document.title

    @property
    def is_pdf(self) -> bool:
        """PDF dostaje przycisk główny – to wersja, którą organizator podpisał i drukuje."""
        return self.document.file_extension.lower() == "pdf"


class DocumentPageAttachment(PageAttachment):
    page = ParentalKey(DocumentPage, on_delete=models.CASCADE, related_name="attachments")

    class Meta(PageAttachment.Meta):
        verbose_name = "plik dokumentu"
        verbose_name_plural = "pliki dokumentu"


class ContentPageAttachment(PageAttachment):
    page = ParentalKey(ContentPage, on_delete=models.CASCADE, related_name="attachments")

    class Meta(PageAttachment.Meta):
        verbose_name = "plik strony"
        verbose_name_plural = "pliki strony"


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
