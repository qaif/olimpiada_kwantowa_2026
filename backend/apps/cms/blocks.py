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


class ScheduleRowBlock(blocks.StructBlock):
    """Jeden wiersz harmonogramu: co, kiedy i w jakich godzinach.

    ``date`` zostaje tekstem, a data maszynowa stoi obok w ``date_value``. Wygląda to na
    dwukrotny zapis tej samej informacji, ale te pola odpowiadają na dwa różne pytania:

    - ``date`` jest **brzmieniem terminu** tak, jak podał go organizator („9 stycznia 2027”,
      ale w razie potrzeby też „przełom lutego i marca” albo „do potwierdzenia”). To ono stoi
      w tabeli i nie wolno go generować z daty, bo nie każdy termin jest jedną datą,
    - ``date_value`` jest tą samą datą w postaci, którą da się **porównać z zegarem**. Bez niej
      zapowiedź „najbliższe warsztaty” na stronie głównej musiałaby parsować polski tekst przy
      każdym żądaniu i milczeć przy pierwszej literówce redaktora.

    Pole jest opcjonalne właśnie dlatego, że termin bywa nieostry: wiersz bez daty zostaje
    w tabeli, tylko nie pojawia się w zapowiedzi (nie ma czego uszeregować).
    """

    topic = blocks.CharBlock(max_length=250, label="temat")
    date = blocks.CharBlock(max_length=100, label="termin")
    date_value = blocks.DateBlock(
        required=False,
        label="termin (data)",
        help_text=(
            "Ta sama data w postaci maszynowej. Decyduje o kolejności i o tym, czy wiersz trafi "
            "do zapowiedzi na stronie głównej. Puste = wiersz zostaje tylko w tabeli."
        ),
    )
    time = blocks.CharBlock(required=False, max_length=100, label="godziny")
    # Prowadzący. Pole jest opcjonalne, bo harmonogram bywa ogłaszany, zanim wykładowca jest
    # potwierdzony – ale wpisane nazwisko trafia **na zaświadczenie** z warsztatów, a tam jest
    # treścią dokumentu: „u kogo” to połowa odpowiedzi na pytanie, czego uczeń się nauczył.
    lecturer = blocks.CharBlock(required=False, max_length=200, label="prowadzący")

    class Meta:
        icon = "time"
        label = "wiersz harmonogramu"


class ScheduleValue(blocks.StructValue):
    """Wartość bloku ``schedule`` z informacją, czy kolumna godzin ma cokolwiek do pokazania.

    Liczymy to w Pythonie, bo szablon musi znać odpowiedź **przed** pętlą po wierszach: nagłówek
    ``<th>`` powstaje raz, a ``{% if %}`` po wierszach nie da się z niego wyprowadzić.
    """

    @property
    def has_time(self) -> bool:
        return any((row.get("time") or "").strip() for row in self.get("rows", []))

    @property
    def has_lecturer(self) -> bool:
        """To samo pytanie o kolumnę „prowadzący” – pusta kolumna znika z tabeli w całości."""
        return any((row.get("lecturer") or "").strip() for row in self.get("rows", []))


class ScheduleBlock(blocks.StructBlock):
    """Harmonogram jako **prawdziwa** tabela (``<table>``) – w odróżnieniu od ``DefinitionListBlock``.

    Rozróżnienie nie jest kosmetyczne. Lista definicji obsługuje tabele **dwukolumnowe, w których
    komórka jest zdaniem** (RODO: „Cel | Podstawa”): tam wiersz czyta się jak akapit, a nagłówki
    kolumn trzeba powtórzyć przy każdej parze, żeby wiadomo było, co jest czym. Harmonogram
    warsztatów jest odwrotnością tego przypadku: szesnaście wierszy po trzy krótkie komórki,
    z których dwie to daty i godziny. Jako ``<dl>`` powstaje z tego pięćdziesiąt bloków tekstu
    z etykietą „Termin” powtórzoną szesnaście razy — dokument, przez który nie da się przebiec
    wzrokiem po dacie. Tabela jest tu właściwą semantyką, bo dane **są** tabelaryczne:
    czytelnik porównuje wiersze między sobą, a czytnik ekranu ma nagłówki kolumn (``<th scope>``)
    do zapowiedzenia przy każdej komórce.

    Nagłówki kolumn są polami, a nie literałami szablonu: ten sam blok obsłuży harmonogram
    warsztatów („Temat”) i harmonogram zjazdów („Wydarzenie”). Kolumna godzin bywa pusta – wtedy
    znika z tabeli w całości, żeby nie zostawiać szesnastu pustych komórek.

    Treść wiersza jest tekstem (poza opcjonalną datą maszynową – patrz ``ScheduleRowBlock``):
    to harmonogram redakcyjny, a nie oś czasu zawodów. Terminy, które egzekwuje serwer, mieszkają
    w ``competitions.Stage`` i CMS ich nie przepisuje.
    """

    caption = blocks.CharBlock(required=False, max_length=250, label="podpis tabeli")
    topic_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek kolumny „temat”")
    date_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek kolumny „termin”")
    time_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek kolumny „godziny”")
    lecturer_label = blocks.CharBlock(required=False, max_length=100, label="nagłówek kolumny „prowadzący”")
    rows = blocks.ListBlock(ScheduleRowBlock(), label="wiersze")

    class Meta:
        icon = "date"
        label = "harmonogram"
        template = "cms/blocks/schedule.html"
        value_class = ScheduleValue


class StageTimelineBlock(blocks.StructBlock):
    """Terminy etapów **bieżącej edycji** czytane wprost z bazy zawodów.

    To jest odpowiedź na to, co robiła stara strona: harmonogram był tabelą wpisaną ręcznie
    w treści, więc każda zmiana terminu wymagała poprawki w dwóch miejscach – w systemie, który
    egzekwuje deadline, i w akapicie, który go ogłasza. Rozjazd między nimi jest tylko kwestią
    czasu, a kosztuje uczestnika pracę oddaną „na czas” według strony i po terminie według serwera.
    Blok nie ma **ani jednego** pola z datą: redaktor wstawia go tam, gdzie terminy mają stanąć,
    a treść bierze się z ``competitions.Stage`` – tego samego obiektu, który zamyka upload.

    Jedyne pole to nagłówek sekcji, i to opcjonalny: blok bywa wstawiany pod własnym śródtytułem
    (``heading``), a dwa nagłówki nad jedną tabelą to szum. ``StaticBlock`` byłby tu wygodniejszy
    w edytorze, ale nie miałby gdzie trzymać tej jednej wartości.

    Stan etapu (nadchodzący / otwarty / zamknięty / wyniki ogłoszone) liczy ``apps.cms.timeline``
    z zegara serwera przy każdym żądaniu. Strona nie jest cache'owana, więc zmiana terminu
    w panelu koordynatora jest widoczna od następnego odświeżenia.
    """

    heading = blocks.CharBlock(
        required=False,
        max_length=200,
        label="nagłówek sekcji",
        help_text="Puste = sama tabela terminów, bez nagłówka.",
    )

    class Meta:
        icon = "time"
        label = "terminy etapów (z systemu)"
        template = "cms/blocks/stage_timeline.html"

    def get_context(self, value, parent_context=None):
        """Terminy **tego** konkursu, a nie „bieżącej edycji w bazie”.

        Blok stoi w treści redakcyjnej, więc sam nie wie, czyją stronę składa – ale wie to
        kontekst, w którym jest renderowany: Wagtail wstawia tam stronę i żądanie. Stąd bierzemy
        konkurs (``apps.cms.tenancy``), bo bez niego blok wstawiony w treść jednej olimpiady
        wyliczałby terminy drugiej – i to w akapicie, który redaktor wstawił właśnie po to, żeby
        nie przepisywać dat ręcznie.

        Importy są w środku metody: ``apps.cms.models`` importuje ten moduł, więc na poziomie
        pliku byłby to cykl.
        """
        from .tenancy import competition_for_page
        from .timeline import stage_rows

        context = super().get_context(value, parent_context=parent_context)
        parent = parent_context or {}
        competition = competition_for_page(parent.get("page"), parent.get("request"))
        context["rows"] = stage_rows(competition=competition)
        return context


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

#: Od tej proporcji (szerokość ÷ wysokość) logotyp jest **pasem**: napisem rozciągniętym na całą
#: szerokość kafla i wysokim na kilkanaście pikseli.
#:
#: Próg nie jest gustem, tylko **proporcją kadru**. Kadr ma stałą wysokość i szerokość kolumny
#: (ok. 273 × 128 px na ``/partnerzy/``, 313 × 96 px w pasie na stronie głównej), a
#: ``object-fit: contain`` mieści w nim obraz w całości. Dopóki proporcja znaku jest mniejsza niż
#: proporcja kadru (2,1 i 3,3), to **wysokość** jest ogranicznikiem – znak wypełnia kadr w pionie
#: i dołożenie szerokości nie zmienia dla niego nic. Dopiero powyżej ogranicznikiem staje się
#: szerokość, a wtedy druga kolumna siatki jest jedyną drogą do powiększenia napisu bez
#: przycinania go i bez rozciągania.
#:
#: Stąd 3 – wartość między obiema proporcjami kadru. Na rzeczywistych plikach organizatora:
#: godła instytutów 0,8–1,1, logotyp Wydziału Informatyki i Telekomunikacji PWr 1,1 (prawie
#: kwadrat), Wydział Fizyki UW 2,5, IQM 2,8 – wszystkie wypełniają kadr w pionie. Znak AIQLAB-u
#: ma 3,3, a pas PCSS-u 7,7: te dwa w jednej kolumnie rysują się wysokie odpowiednio na 82 i 35 px,
#: choć kadr ma 128.
#:
#: Czego ten próg **nie** rozwiązuje: znaku prawie kwadratowego z nazwą instytucji wpisaną drobnym
#: krojem w sam plik (właśnie logotyp PWr, na który skarżył się organizator). Tam ogranicza
#: wysokość kadru, więc lekarstwem jest wyższy kadr w arkuszu, a nie druga kolumna.
#:
#: Wartość jest stałą modułu, a nie liczbą w arkuszu, bo decyzję podejmuje serwer: przeglądarka
#: nie ma jak zapytać o proporcje pliku przed jego pobraniem, a przy ``loading="lazy"`` układ musi
#: być gotowy wcześniej.
WIDE_LOGO_RATIO = 3.0


class PartnerValue(blocks.StructValue):
    """Wartość bloku ``partner`` z inicjałami i kwalifikacją logotypu liczonymi po stronie Pythona.

    Inicjały zastępują logotyp, którego dla większości partnerów po prostu nie ma (patrz
    ``docs/import/assets.md``): pusta karta wyglądałaby na błąd wczytywania obrazu. Liczymy je
    tutaj, a nie filtrem w szablonie, bo „pierwsza litera wyrazu” w nazwie typu „Uniwersytet
    im. Adama Mickiewicza” wymaga pominięcia skrótów – to reguła, nie formatowanie.

    ``is_wide`` odpowiada na drugie pytanie układu: czy ten znak zmieści się w jednej kolumnie
    siatki, czy potrzebuje dwóch, żeby dało się go przeczytać. Odpowiedź bierzemy z **wymiarów
    zapisanych przy obrazie** (``Image.width``/``Image.height`` są kolumnami w bazie, nie odczytem
    pliku), więc nie kosztuje ani jednego wejścia na dysk.
    """

    #: Wyrazy pomijane przy inicjałach: skróty i spójniki nie identyfikują instytucji.
    SKIPPED_WORDS = frozenset({"im", "i", "w", "na", "z", "the", "of", "and"})

    @property
    def initials(self) -> str:
        words = [word for word in re.split(r"[\s\-–—/,.]+", self.get("name", "")) if word]
        meaningful = [word for word in words if word.lower() not in self.SKIPPED_WORDS]
        return "".join(word[0] for word in (meaningful or words)[:INITIALS_LENGTH]).upper()

    @property
    def is_wide(self) -> bool:
        """Czy logotyp jest pasem, któremu trzeba dać dwie kolumny siatki (patrz ``WIDE_LOGO_RATIO``).

        Brak logotypu i wysokość zero to ``False``: karta pokazuje wtedy inicjały, a te są
        kwadratem i nie mają czego rozciągać. Zero w mianowniku zdarza się przy obrazie wgranym
        przed uzupełnieniem wymiarów – pytanie o proporcje nie ma wtedy odpowiedzi, a wywrócony
        szablon strony partnerów byłby gorszy od nierozpoznanego pasa.
        """
        logo = self.get("logo")
        if logo is None or not getattr(logo, "height", 0) or not getattr(logo, "width", 0):
            return False
        return logo.width / logo.height >= WIDE_LOGO_RATIO


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
    schedule = ScheduleBlock()
    stage_timeline = StageTimelineBlock()

    class Meta:
        required = False
