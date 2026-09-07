"""Bloki StreamField dla treści redakcyjnych.

Redaktor nie wpisuje HTML-a: ``RichTextBlock`` przepuszcza treść przez whitelistę Wagtaila
(``features`` niżej), a obrazy, dokumenty i osadzenia są wyborem z biblioteki, nie surowym
znacznikiem. W szablonach renderujemy je przez ``{% include_block %}`` albo ``|richtext``, nigdy
przez ``|safe`` – patrz docstring ``apps/cms/models.py``.
"""

import re

from wagtail import blocks
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.images.blocks import ImageChooserBlock

#: Dozwolone formatowanie w akapicie. Świadomie bez ``image``/``embed`` (są osobnymi blokami)
#: i bez surowego HTML – whitelist Wagtaila jest jedyną drogą treści do szablonu.
RICH_TEXT_FEATURES = [
    "h2",
    "h3",
    "h4",
    "bold",
    "italic",
    "ol",
    "ul",
    "hr",
    "link",
    "document-link",
    "superscript",
    "subscript",
    "blockquote",
]


class ImageWithCaptionBlock(blocks.StructBlock):
    """Obraz z podpisem. Tekst alternatywny bierze się z opisu obrazu w bibliotece."""

    image = ImageChooserBlock(label="obraz")
    caption = blocks.CharBlock(required=False, max_length=250, label="podpis")

    class Meta:
        icon = "image"
        label = "obraz"
        template = "cms/blocks/image.html"


class DocumentBlock(blocks.StructBlock):
    """Załącznik do pobrania (np. treść zadań w PDF). Link idzie przez widok ``/documents/``."""

    document = DocumentChooserBlock(label="dokument")
    label = blocks.CharBlock(required=False, max_length=250, label="etykieta linku")

    class Meta:
        icon = "doc-full"
        label = "dokument"
        template = "cms/blocks/document.html"


class HeadingBlock(blocks.StructBlock):
    """Śródtytuł z jawną kotwicą – budulec spisu treści długich dokumentów.

    Kotwica jest osobnym polem, a nie wyprowadzana ze slugifikacji tekstu: adres ``#rozdzial-3``
    ma przeżyć redakcyjną poprawkę tytułu rozdziału. Ktoś zdążył go już skopiować do pisma.

    ``in_toc`` oddziela rozdziały od sekcji dodatkowych (źródła, miejsce na uchwałę): obie są
    śródtytułami tego samego poziomu, ale spis rozdziałów ma wymieniać wyłącznie rozdziały.
    """

    text = blocks.CharBlock(max_length=250, label="tekst")
    level = blocks.ChoiceBlock(
        choices=[("2", "rozdział (H2)"), ("3", "paragraf (H3)")],
        default="2",
        label="poziom",
    )
    anchor = blocks.CharBlock(
        max_length=100,
        label="kotwica",
        help_text="Fragment adresu po „#”, np. „rozdzial-3”. Zmiana psuje istniejące odnośniki.",
    )
    in_toc = blocks.BooleanBlock(required=False, default=True, label="pokaż w spisie rozdziałów")

    class Meta:
        icon = "title"
        label = "śródtytuł"
        template = "cms/blocks/heading.html"


class NoticeBlock(blocks.StructBlock):
    """Wyróżniona ramka: zastrzeżenie prawne, komunikat o statusie dokumentu."""

    tone = blocks.ChoiceBlock(
        choices=[("info", "informacja"), ("warning", "ostrzeżenie")],
        default="info",
        label="ton",
    )
    text = blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label="treść")

    class Meta:
        icon = "warning"
        label = "ramka"
        template = "cms/blocks/notice.html"


class DefinitionItemBlock(blocks.StructBlock):
    """Jedna para etykieta–wartość, czyli jeden wiersz tabeli dwukolumnowej z dokumentu."""

    term = blocks.CharBlock(max_length=500, label="etykieta")
    description = blocks.CharBlock(max_length=1000, label="wartość")

    class Meta:
        icon = "list-ul"
        label = "para"


class DefinitionListBlock(blocks.StructBlock):
    """Tabela dwukolumnowa dokumentu („Cel | Podstawa”) w postaci listy definicji ``<dl>``.

    Dlaczego nie ``<table>``: te tabele są układem, a nie danymi – nie ma czego sortować ani
    sumować, a wiersz to jedno zdanie w każdej kolumnie. Prawdziwa tabela z takimi komórkami
    na telefonie zwęża obie kolumny do słupków po dwa słowa; ``<dl>`` układa etykietę nad
    wartością i czyta się tak samo na każdej szerokości.

    Dlaczego nie akapit „**etykieta** — wartość” (tak robił import wcześniej): przy zdaniach
    po 150 znaków w obu kolumnach powstaje jedno zlepione zdanie bez widocznej granicy między
    celem a podstawą prawną. W dokumencie RODO to właśnie ta granica jest treścią.

    ``term_label``/``description_label`` to nagłówki kolumn z dokumentu. Powtarzamy je przy
    każdej parze jako drobną etykietę: czytelnik ekranu słyszy „Cel … Podstawa …”, a wzrokowo
    są na tyle małe, że nie konkurują z treścią. Bez nich sama kolejność nie mówi, co jest czym.

    Pola są tekstowe (``CharBlock``): treść tabeli to zdania, a nie formatowany akapit. Szablon
    autoescapuje – żadnego ``|safe``.
    """

    term_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek etykiet")
    description_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek wartości")
    rows = blocks.ListBlock(DefinitionItemBlock(), label="pary")

    class Meta:
        icon = "list-ul"
        label = "lista definicji"
        template = "cms/blocks/definitions.html"


class StepBlock(blocks.StructBlock):
    """Jeden krok sekcji „Jak zacząć” na stronie głównej: tytuł i jedno zdanie wyjaśnienia."""

    title = blocks.CharBlock(max_length=120, label="tytuł kroku")
    text = blocks.TextBlock(max_length=300, label="opis")

    class Meta:
        icon = "list-ol"
        label = "krok"


class StepsStreamBlock(blocks.StreamBlock):
    """Lista kroków. Bez innych bloków – to sekcja o stałym układzie, nie dowolna treść."""

    step = StepBlock()

    class Meta:
        required = False


#: Poziomy współpracy w kolejności, w jakiej mają stać na stronie: najpierw patronat i partnerzy
#: (wkład merytoryczny i instytucjonalny), potem sponsorzy od najwyższego progu, na końcu patronat
#: medialny. Klucz jest zapisywany w bazie, więc zmiana etykiety nie unieważnia istniejących wpisów.
PARTNER_LEVELS = [
    ("patron-honorowy", "patron honorowy"),
    ("partner-instytucjonalny", "partner instytucjonalny"),
    ("partner-naukowy", "partner naukowy"),
    ("sponsor-diamentowy", "sponsor diamentowy"),
    ("sponsor-platynowy", "sponsor platynowy"),
    ("sponsor-zloty", "sponsor złoty"),
    ("partner-medialny", "partner medialny"),
]

#: Ile liter inicjału pokazać, gdy partner nie ma jeszcze logotypu.
INITIALS_LENGTH = 2


class PartnerValue(blocks.StructValue):
    """Wartość bloku ``partner`` z inicjałami liczonymi po stronie Pythona.

    Inicjały zastępują logotyp, którego dla większości partnerów po prostu nie ma (patrz
    ``docs/import/assets.md``): pusta karta wyglądałaby na błąd wczytywania obrazu. Liczymy je
    tutaj, a nie filtrem w szablonie, bo „pierwsza litera wyrazu” w nazwie typu „Uniwersytet
    im. Adama Mickiewicza” wymaga pominięcia skrótów – to reguła, nie formatowanie.
    """

    #: Wyrazy pomijane przy inicjałach: skróty i spójniki nie identyfikują instytucji.
    SKIPPED_WORDS = frozenset({"im", "i", "w", "na", "z", "the", "of", "and"})

    @property
    def initials(self) -> str:
        words = [word for word in re.split(r"[\s\-–—/,.]+", self.get("name", "")) if word]
        meaningful = [word for word in words if word.lower() not in self.SKIPPED_WORDS]
        return "".join(word[0] for word in (meaningful or words)[:INITIALS_LENGTH]).upper()


class PartnerBlock(blocks.StructBlock):
    """Jeden partner albo sponsor Olimpiady.

    ``logo`` i ``url`` są opcjonalne, bo w chwili pisania tej strony organizator nie ma ani
    jednego logotypu partnera; karta bez nich pokazuje inicjały i samą nazwę. ``description``
    jest tekstem, nie RichTextem: karta w siatce mieści jedno zdanie, a pole formatowane
    zachęcałoby do wklejenia noty prasowej.
    """

    name = blocks.CharBlock(max_length=200, label="nazwa")
    level = blocks.ChoiceBlock(
        choices=PARTNER_LEVELS,
        default=PARTNER_LEVELS[1][0],
        label="poziom współpracy",
        help_text="Decyduje o grupie, w której partner stoi na stronie.",
    )
    logo = ImageChooserBlock(required=False, label="logotyp")
    url = blocks.URLBlock(required=False, max_length=300, label="strona partnera")
    description = blocks.CharBlock(
        required=False,
        max_length=300,
        label="opis",
        help_text="Jedno zdanie: na czym polega współpraca.",
    )

    class Meta:
        icon = "group"
        label = "partner"
        value_class = PartnerValue


class PartnersStreamBlock(blocks.StreamBlock):
    """Lista partnerów. Bez innych bloków – to sekcja o stałym układzie, nie dowolna treść."""

    partner = PartnerBlock()

    class Meta:
        required = False


class ArticleStreamBlock(blocks.StreamBlock):
    """Treść artykułu: akapit, obraz, dokument, osadzenie."""

    paragraph = blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label="akapit")
    image = ImageWithCaptionBlock()
    document = DocumentBlock()
    embed = EmbedBlock(label="osadzenie (film, prezentacja)")

    class Meta:
        required = False


class DocumentStreamBlock(ArticleStreamBlock):
    """Treść dokumentu urzędowego: bloki artykułu plus śródtytuł z kotwicą i ramka.

    Osobna klasa zamiast dopisania obu bloków do ``ArticleStreamBlock``: aktualność ma być
    krótka i nie potrzebuje własnego spisu treści, a rozszerzenie wspólnego bloku zmieniłoby
    definicję pola ``body`` także w ``NewsPage`` i ``ProblemsPage`` (migracja bez powodu).
    """

    heading = HeadingBlock()
    notice = NoticeBlock()
    definitions = DefinitionListBlock()

    class Meta:
        required = False
