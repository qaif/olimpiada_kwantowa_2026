"""Modele wtyczek redakcyjnych djcms – odpowiedniki bloków ``backend/apps/cms/blocks.py`` (§ 6.2 speca).

Zasady wspólne dla wszystkich:

- **tekst formatowany wyłącznie przez djangocms-text.** Pola z HTML-em to ``HTMLField``: sanityzacja
  ``nh3`` przy zapisie (``get_prep_value``) i ponownie przy walidacji formularza. Szablony nie mają
  ``|safe`` – ``HTMLField`` oddaje z bazy napis już oznaczony jako bezpieczny, bo wyczyszczony
  (reguła 4 z § 7). Reszta pól to zwykły tekst, autoescapowany w szablonie,
- **adresy tylko ``http(s)``** – walidator formularza i ``safe_http_url`` przy renderze (wiersz mógł
  powstać z pominięciem formularza: import, powłoka),
- **żadnych danych zawodów.** Terminy, zadania i wyniki rysują wtyczki żywe (``apps.live``); tu nie
  ma ani jednego pola z datą etapu czy liczbą punktów. Jedyna data (``ScheduleRow.date_value``) to
  data warsztatu z harmonogramu redakcyjnego – jak w ``ScheduleRowBlock`` Wagtaila,
- **nazwy pól takie jak w blokach Wagtaila** – importer (DJ-01g) przepisuje ``value`` bloku na pola
  modelu jeden do jednego.
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import unquote, urlsplit

from cms.models import CMSPlugin
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_slug
from django.db import models
from djangocms_text.fields import HTMLField
from filer.fields.file import FilerFileField
from filer.fields.image import FilerImageField

from .embeds import embed_src

#: Adresy wpisywane przez redaktora: wyłącznie ``http(s)`` (``URLField`` domyślnie wpuszcza też ``ftp``).
HTTP_URL_VALIDATOR = URLValidator(schemes=["http", "https"])

#: Kopia ``PARTNER_LEVELS`` z ``backend/apps/cms/blocks.py`` (kolejność = kolejność grup na stronie).
#: Kopia, a nie import: djcms nie ma kodu aplikacji głównej. Importer (DJ-01g) porównuje ją ze
#: słownikiem z paczki (``vocabularies.partner_levels``) i odrzuca poziom spoza listy.
PARTNER_LEVELS = [
    ("patron-honorowy", "patron honorowy"),
    ("partner-instytucjonalny", "partner instytucjonalny"),
    ("partner-naukowy", "partner naukowy"),
    ("sponsor-diamentowy", "sponsor diamentowy"),
    ("sponsor-platynowy", "sponsor platynowy"),
    ("sponsor-zloty", "sponsor złoty"),
    ("partner-medialny", "partner medialny"),
]

#: Inicjały i próg „logotypu-pasa” – te same wartości co ``INITIALS_LENGTH``/``WIDE_LOGO_RATIO``
#: w blokach Wagtaila (uzasadnienia tam: proporcja kadru karty na ``/partnerzy/``).
INITIALS_LENGTH = 2
WIDE_LOGO_RATIO = 3.0


def safe_http_url(value: str | None) -> str:
    """Adres ``http(s)`` bez zmian, cokolwiek innego – pusty napis (nie trafi do ``href``/``src``)."""
    text = (value or "").strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return ""
    return text if scheme in {"http", "https"} else ""


# --- plik: z biblioteki albo adres na domenie głównej ---------------------------------------------


class LinkedFile(CMSPlugin):
    """Plik do pobrania: **albo** z biblioteki filera, **albo** adres (§ 1.2 p. 5 speca).

    Adres to zwykle dokument na domenie głównej (``https://<SITE_DOMAIN>/documents/<id>/<plik>``):
    regulamin, RODO, ZOZ nie są kopiowane na ``dj.``, bo druga kopia dokumentu prawnego rozjechałaby
    się z oryginałem przy pierwszej podmianie pliku w Wagtailu. Filer zostaje dla plików, które
    redaktor ``dj.`` wgrywa sam.

    ``extension`` i ``size_bytes`` są polami, a nie odczytem pliku: przy adresie nie ma pliku do
    odczytania (importer przepisuje je z paczki), a przy pliku z biblioteki ``clean`` kopiuje je
    z filera, więc szablon zawsze czyta to samo miejsce.
    """

    file = FilerFileField(
        verbose_name="plik z biblioteki",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Plik wgrany do biblioteki dj. Wypełnij to pole albo adres – nie oba.",
    )
    url = models.URLField(
        "adres pliku",
        max_length=500,
        blank=True,
        validators=[HTTP_URL_VALIDATOR],
        help_text="Np. dokument na stronie głównej olimpiady (https://…/documents/5/regulamin.pdf).",
    )
    extension = models.CharField(
        "rozszerzenie",
        max_length=10,
        blank=True,
        help_text="Np. „pdf”. Przy pliku z biblioteki uzupełnia się samo; przy adresie – z końcówki adresu.",
    )
    size_bytes = models.PositiveBigIntegerField(
        "rozmiar (bajty)",
        null=True,
        blank=True,
        help_text="Przy pliku z biblioteki uzupełnia się samo. Puste = rozmiar nie jest pokazywany.",
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        has_file, has_url = self.file_id is not None, bool((self.url or "").strip())
        if has_file == has_url:
            raise ValidationError("Wskaż plik z biblioteki albo podaj adres – dokładnie jedno z dwóch.")
        if has_file:
            self.extension = (self.file.extension or "")[:10]
            self.size_bytes = self.file.size or None
        elif not self.extension:
            self.extension = _url_extension(self.url)

    @property
    def href(self) -> str:
        if self.file_id is not None and self.file is not None:
            return self.file.url or ""
        return safe_http_url(self.url)

    @property
    def filename(self) -> str:
        """Nazwa pliku do podpisu (``doc-files__hint``): oryginalna z filera albo ostatni człon adresu."""
        if self.file_id is not None and self.file is not None:
            return self.file.original_filename or self.file.label
        return posixpath.basename(unquote(urlsplit(self.url or "").path))

    @property
    def file_extension(self) -> str:
        return (self.extension or "").lower()

    @property
    def is_pdf(self) -> bool:
        """PDF dostaje przycisk główny – wersja, którą organizator podpisał i drukuje."""
        return self.file_extension == "pdf"

    @property
    def file_title(self) -> str:
        """Tytuł pliku (odpowiednik ``document.title`` Wagtaila) – pole ``title`` albo nazwa w bibliotece."""
        title = getattr(self, "title", "") or ""
        if title:
            return title
        if self.file_id is not None and self.file is not None:
            return self.file.label
        return self.filename


def _url_extension(url: str | None) -> str:
    suffix = posixpath.splitext(urlsplit(url or "").path)[1]
    return suffix.lstrip(".").lower()[:10]


class DocumentLink(LinkedFile):
    """Blok ``document`` (``DocumentBlock``): przycisk „Pobierz: …” w treści."""

    label = models.CharField("etykieta linku", max_length=250, blank=True, help_text="Puste = tytuł pliku.")
    title = models.CharField("tytuł pliku", max_length=250, blank=True)

    def __str__(self) -> str:
        return self.label or self.file_title


class Attachment(LinkedFile):
    """Plik strony w karcie „Do pobrania” (``PageAttachment`` Wagtaila: Content/DocumentPage).

    ``label`` to rola pliku („PDF do druku”), ``title`` – co to za plik (tytuł dokumentu
    w bibliotece Wagtaila). Karta pokazuje etykietę, a bez niej tytuł – jak w oryginale.
    """

    label = models.CharField(
        "etykieta",
        max_length=100,
        blank=True,
        help_text="Rola pliku, np. „PDF do druku”. Puste = tytuł pliku.",
    )
    title = models.CharField("tytuł pliku", max_length=250, blank=True)

    def __str__(self) -> str:
        return self.label or self.file_title


class ArchiveDocument(LinkedFile):
    """Materiał edycji archiwalnej: treść zadań, rozwiązania albo inny dokument."""

    class Kind(models.TextChoices):
        PROBLEMS = "PROBLEMS", "zadania"
        SOLUTIONS = "SOLUTIONS", "rozwiązania"
        OTHER = "OTHER", "inne"

    kind = models.CharField("rodzaj", max_length=16, choices=Kind.choices, default=Kind.PROBLEMS)
    title = models.CharField("etykieta", max_length=200)

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.title}"


# --- treść artykułu i dokumentu (ART / DOC) -------------------------------------------------------


class ImageWithCaption(CMSPlugin):
    """Blok ``image``. Tekst alternatywny bierze się z opisu obrazu w bibliotece (``default_alt_text``)."""

    image = FilerImageField(verbose_name="obraz", on_delete=models.PROTECT, related_name="+")
    caption = models.CharField("podpis", max_length=250, blank=True)

    def __str__(self) -> str:
        return self.caption or (self.image.label if self.image_id and self.image else "obraz")


class Embed(CMSPlugin):
    """Blok ``embed``: film z YouTube albo Vimeo – ramka z wzorca (``embeds.py``), nie z oEmbed."""

    url = models.URLField(
        "adres filmu",
        max_length=500,
        validators=[HTTP_URL_VALIDATOR],
        help_text="Adres filmu na YouTube albo Vimeo, np. https://www.youtube.com/watch?v=…",
    )
    title = models.CharField(
        "tytuł filmu",
        max_length=200,
        help_text="Czytany przez czytniki ekranu zamiast zawartości ramki.",
    )

    def clean(self):
        super().clean()
        if self.url and embed_src(self.url) is None:
            raise ValidationError({"url": "Dozwolone są wyłącznie filmy z YouTube i Vimeo."})

    @property
    def src(self) -> str | None:
        return embed_src(self.url)

    def __str__(self) -> str:
        return self.title


class Heading(CMSPlugin):
    """Blok ``heading``: śródtytuł z jawną kotwicą – źródło spisu treści.

    Kotwica jest osobnym polem, a nie slugiem tekstu: adres ``#rozdzial-3`` ma przeżyć poprawkę
    tytułu rozdziału. ``in_toc`` oddziela rozdziały od sekcji dodatkowych (uzasadnienie
    w ``HeadingBlock``).
    """

    class Level(models.TextChoices):
        CHAPTER = "2", "rozdział (H2)"
        PARAGRAPH = "3", "paragraf (H3)"

    text = models.CharField("tekst", max_length=250)
    level = models.CharField("poziom", max_length=1, choices=Level.choices, default=Level.CHAPTER)
    anchor = models.CharField(
        "kotwica",
        max_length=100,
        validators=[validate_slug],
        help_text="Fragment adresu po „#”, np. „rozdzial-3”. Zmiana psuje istniejące odnośniki.",
    )
    in_toc = models.BooleanField("pokaż w spisie rozdziałów", default=True)

    def __str__(self) -> str:
        return self.text


class Notice(CMSPlugin):
    """Blok ``notice``: wyróżniona ramka (zastrzeżenie prawne, komunikat o statusie dokumentu)."""

    class Tone(models.TextChoices):
        INFO = "info", "informacja"
        WARNING = "warning", "ostrzeżenie"

    tone = models.CharField("ton", max_length=10, choices=Tone.choices, default=Tone.INFO)
    text = HTMLField("treść")

    def __str__(self) -> str:
        return self.get_tone_display()


class DefinitionList(CMSPlugin):
    """Blok ``definitions``: tabela dwukolumnowa dokumentu jako ``<dl>``; pary to wtyczki-dzieci.

    Dlaczego lista definicji, a nie tabela – ``DefinitionListBlock`` w blokach Wagtaila.
    """

    term_label = models.CharField("nagłówek etykiet", max_length=100, blank=True)
    description_label = models.CharField("nagłówek wartości", max_length=100, blank=True)

    def __str__(self) -> str:
        return " | ".join(filter(None, [self.term_label, self.description_label])) or "tabela dwukolumnowa"


class DefinitionItem(CMSPlugin):
    """Jedna para etykieta–wartość (``DefinitionItemBlock``)."""

    term = models.CharField("etykieta", max_length=500)
    description = models.CharField("wartość", max_length=1000)

    def __str__(self) -> str:
        return self.term


class Schedule(CMSPlugin):
    """Blok ``schedule``: harmonogram jako **prawdziwa** tabela; wiersze to wtyczki-dzieci.

    ``has_time``/``has_lecturer`` – port ``ScheduleValue``: nagłówek ``<th>`` powstaje przed pętlą
    po wierszach, więc odpowiedź „czy kolumna ma co pokazać” musi być gotowa wcześniej.
    """

    caption = models.CharField("podpis tabeli", max_length=250, blank=True)
    topic_label = models.CharField("nagłówek kolumny „temat”", max_length=100, blank=True)
    date_label = models.CharField("nagłówek kolumny „termin”", max_length=100, blank=True)
    time_label = models.CharField("nagłówek kolumny „godziny”", max_length=100, blank=True)
    lecturer_label = models.CharField("nagłówek kolumny „prowadzący”", max_length=100, blank=True)

    def __str__(self) -> str:
        return self.caption or "harmonogram"

    def rows(self) -> list:
        """Wiersze w kolejności – z drzewa wtyczek renderu, a poza renderem z bazy."""
        children = getattr(self, "child_plugin_instances", None)
        if children is None:
            children = list(ScheduleRow.objects.filter(parent_id=self.pk).order_by("position"))
        return [child for child in children if isinstance(child, ScheduleRow)]

    @property
    def has_time(self) -> bool:
        return any((row.time or "").strip() for row in self.rows())

    @property
    def has_lecturer(self) -> bool:
        return any((row.lecturer or "").strip() for row in self.rows())


class ScheduleRow(CMSPlugin):
    """Wiersz harmonogramu (``ScheduleRowBlock``).

    ``date`` to **brzmienie** terminu („9 stycznia 2027”, „do potwierdzenia”), ``date_value`` – ta
    sama data do porównań. Na ``dj.`` nikt jej jeszcze nie czyta (zapowiedź warsztatów na stronie
    głównej bierze tabelę Wagtaila przez API – ryzyko 4 speca), ale pole zostaje, żeby tabela
    przeniesiona z importu nie traciła informacji.
    """

    topic = models.CharField("temat", max_length=250)
    date = models.CharField("termin", max_length=100)
    date_value = models.DateField("termin (data)", null=True, blank=True)
    time = models.CharField("godziny", max_length=100, blank=True)
    lecturer = models.CharField("prowadzący", max_length=200, blank=True)

    def __str__(self) -> str:
        return self.topic


# --- strona główna --------------------------------------------------------------------------------


class Hero(CMSPlugin):
    """Hasło strony głównej (``HomePage.hero_title``/``hero_text``). Puste hasło = tytuł strony."""

    title = models.CharField("hasło", max_length=200, blank=True, help_text="Puste = tytuł strony.")
    text = HTMLField("tekst pod hasłem", blank=True)

    def __str__(self) -> str:
        return self.title or "hasło strony głównej"


class StepsSection(CMSPlugin):
    """Sekcja „Jak zacząć” (``HomePage.steps_title`` + ``steps``); kroki to wtyczki-dzieci."""

    title = models.CharField("nagłówek sekcji", max_length=200)

    def __str__(self) -> str:
        return self.title


class Step(CMSPlugin):
    """Jeden krok (``StepBlock``): tytuł i jedno zdanie wyjaśnienia."""

    title = models.CharField("tytuł kroku", max_length=120)
    text = models.TextField("opis", max_length=300)

    def __str__(self) -> str:
        return self.title


class AboutSection(CMSPlugin):
    """Sekcja „O Olimpiadzie” (``HomePage.about_title`` + ``about_body``); treść to wtyczki-dzieci."""

    title = models.CharField("nagłówek sekcji", max_length=200)

    def __str__(self) -> str:
        return self.title


# --- partnerzy ------------------------------------------------------------------------------------


class Partner(CMSPlugin):
    """Blok ``partner`` (``PartnerBlock``) z właściwościami ``PartnerValue``: inicjały i logotyp-pas."""

    #: Wyrazy pomijane przy inicjałach – ta sama lista co ``PartnerValue.SKIPPED_WORDS``.
    SKIPPED_WORDS = frozenset({"im", "i", "w", "na", "z", "the", "of", "and"})

    name = models.CharField("nazwa", max_length=200)
    level = models.CharField(
        "poziom współpracy",
        max_length=40,
        choices=PARTNER_LEVELS,
        default=PARTNER_LEVELS[1][0],
        help_text="Decyduje o grupie, w której partner stoi na stronie.",
    )
    logo = FilerImageField(
        verbose_name="logotyp", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    url = models.URLField("strona partnera", max_length=300, blank=True, validators=[HTTP_URL_VALIDATOR])
    description = models.CharField(
        "opis", max_length=300, blank=True, help_text="Jedno zdanie: na czym polega współpraca."
    )

    def __str__(self) -> str:
        return self.name

    @property
    def href(self) -> str:
        return safe_http_url(self.url)

    @property
    def initials(self) -> str:
        words = [word for word in re.split(r"[\s\-–—/,.]+", self.name or "") if word]
        meaningful = [word for word in words if word.lower() not in self.SKIPPED_WORDS]
        return "".join(word[0] for word in (meaningful or words)[:INITIALS_LENGTH]).upper()

    @property
    def is_wide(self) -> bool:
        """Czy logotyp jest pasem na dwie kolumny siatki – wymiary z bazy filera, bez odczytu pliku."""
        logo = self.logo if self.logo_id else None
        if logo is None or not getattr(logo, "height", 0) or not getattr(logo, "width", 0):
            return False
        return logo.width / logo.height >= WIDE_LOGO_RATIO


class BecomePartner(CMSPlugin):
    """Sekcja „Zostań partnerem” (``PartnersPage.become_partner_*`` + ``contact_email``)."""

    title = models.CharField("nagłówek sekcji", max_length=200, default="Zostań partnerem")
    body = HTMLField("zaproszenie do współpracy")
    contact_email = models.EmailField(
        "e-mail w sprawie współpracy",
        blank=True,
        help_text="Adres przycisku „Napisz do nas”. Puste = bez przycisku.",
    )

    def __str__(self) -> str:
        return self.title


# --- najczęstsze pytania --------------------------------------------------------------------------


class FAQEntry(CMSPlugin):
    """Jedno pytanie z odpowiedzią (``FAQEntry`` Wagtaila).

    Kotwica jest **zapisana**, a nie liczona przy renderze: każda wersja robocza djangocms-versioning
    kopiuje wtyczki z nowymi kluczami, więc kotwica „``pytanie-dj-<pk>``” liczona w locie zmieniałaby
    się przy każdej publikacji. Puste pole dostaje ją raz, przy pierwszym zapisie, i kopia wersji
    przenosi ją dalej. Import zachowuje kotwice Wagtaila (``pytanie-<id>``) – odnośniki wysłane
    w odpowiedziach na zgłoszenia działają także na ``dj.``.
    """

    section = models.CharField(
        "sekcja",
        max_length=100,
        blank=True,
        help_text="Nagłówek grupy, np. „Konto i rejestracja”. Puste = pytanie bez sekcji.",
    )
    question = models.CharField("pytanie", max_length=250)
    answer = HTMLField("odpowiedź")
    anchor = models.CharField(
        "kotwica",
        max_length=100,
        blank=True,
        validators=[validate_slug],
        help_text="Fragment adresu po „#”. Puste = nadawana automatycznie i potem stała.",
    )

    def __str__(self) -> str:
        return self.question

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.anchor:
            self.anchor = f"pytanie-dj-{self.pk}"
            type(self).objects.filter(pk=self.pk).update(anchor=self.anchor)
