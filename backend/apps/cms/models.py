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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import DatabaseError, models
from django.db.models import F, Q
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
from apps.competitions.scoping import scope_to_competition
from apps.competitions.scoring import problem_maxima_by_number
from apps.competitions.services import current_edition, current_stage, training_stage
from apps.results.models import ResultsPublication
from apps.tenancy.managers import CompetitionScopedManager

from .blocks import (
    PARTNER_LEVELS,
    RICH_TEXT_FEATURES,
    ArticleStreamBlock,
    DocumentStreamBlock,
    PartnersStreamBlock,
    StepsStreamBlock,
)
from .tenancy import competition_for_page, resolve_competition
from .timeline import stage_rows
from .workshops import WORKSHOP_KEY_LENGTH, WORKSHOPS_SLUG, upcoming_workshops

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
        # Gałąź adresów wewnętrznych platformy (``/internal/tls-allowed`` – pytanie Caddy'ego
        # o certyfikat konkursu w subdomenie). Woła ją infrastruktura, nie człowiek, więc strona
        # CMS o tym slugu byłaby martwa **i** przykryłaby adres, od którego zależy TLS.
        "internal",
        "login",
        "logout",
        "me",
        "media",
        "register",
        "results",
        "review",
        "static",
        # Strona statusu serwisu i zgłoszenia do organizatora – oba adresy obsługuje aplikacja
        # (``config/urls.py`` i ``apps/web/urls.py``), więc strona CMS o takim slugu byłaby martwa.
        "setup",
        "status",
        "support",
    }
)

#: Głębokość strony głównej w drzewie treebearda: ``Root`` ma 1, ``HomePage`` 2. Strony o adresie
#: jednosegmentowym (``/aktualnosci/``) są jej dziećmi, czyli mają ``depth == 3``.
HOME_PAGE_DEPTH = 2


def competition_path_prefixes() -> frozenset[str]:
    """Pierwsze segmenty adresu zajęte przez konkursy adresowane prefiksem ścieżki (§ 2.3).

    ``RESERVED_SLUGS`` wyżej jest listą **stałą**, bo adresy aplikacji zna urlconf i one się nie
    zmieniają bez wydania. Prefiksy konkursów są za to danymi: operator platformy zakłada konkurs
    ``fizyczna`` pod adresem ``/fizyczna/…`` i od tej chwili strona CMS-u o slugu ``fizyczna``
    przechwyciłaby **cały** drugi konkurs – redaktor konkursu A widziałby „opublikowano”,
    a czytelnik konkursu B stronę A zamiast swojej. To jest ta sama klasa błędu, co slug ``login``,
    tylko szkoda jest większa, bo dotyczy nie jednego adresu, a całej witryny.

    Zapytanie wykonuje się przy **zapisie strony**, a nie przy jej wyświetleniu, więc nie ma tu
    czego buforować: redaktor zapisuje stronę rzadko, a pamięć podręczna z czasem życia znaczyłaby,
    że tuż po założeniu konkursu walidacja jeszcze o nim nie wie.

    Baza bez tabeli konkursów (świeża instalacja przed migracjami ``tenancy``) oddaje pusty zbiór:
    walidacja slugów ma wtedy działać dokładnie tak, jak działała przed wielokonkursowością.
    """
    from apps.tenancy.models import Competition, RoutingMode

    try:
        prefixes = Competition.objects.filter(routing_mode=RoutingMode.PATH).exclude(path_prefix="")
        return frozenset(prefixes.values_list("path_prefix", flat=True))
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli konkursów
        return frozenset()


def taken_first_segments(site=None) -> frozenset[str]:
    """Pierwsze segmenty adresu zajęte **w drzewie stron** danej witryny (domyślnie domyślnej).

    Odwrotna strona tej samej monety, co ``competition_path_prefixes``: tam pytamy „czy slug
    strony nie przechwyci konkursu”, tu – „czy prefiks konkursu nie przechwyci strony”. Reguła
    z § 2.3 wymaga obu sprawdzeń, bo kolizję da się zrobić z każdej strony: zakładając stronę pod
    istniejącym prefiksem **albo** zakładając konkurs pod istniejącym slugiem.

    Pierwsze sprawdzenie robi ``CMSPage.clean`` (niżej). Drugie należy do ``Competition.clean``
    (``apps/tenancy/models.py``) – i ta funkcja jest dla niego, tak samo jak ``RESERVED_SLUGS``,
    które ta sama metoda już stąd czyta. Wiedza „jakie adresy są zajęte w drzewie stron” mieszka
    w aplikacji, która to drzewo prowadzi, a nie w warstwie platformy.

    Zwracamy komplet slugów drugiego poziomu razem z ``RESERVED_SLUGS``: wołający ma jedną listę
    „tego nie wolno”, a nie dwa zbiory do złożenia u siebie.
    """
    from wagtail.models import Page, Site

    try:
        if site is None:
            site = Site.objects.filter(is_default_site=True).select_related("root_page").first()
        if site is None:
            return RESERVED_SLUGS
        # Dzieci korzenia witryny, czyli strony o adresie jednosegmentowym – te same, które
        # ``CMSPage.is_second_level`` uznaje za „drugi poziom”.
        slugs = Page.objects.child_of(site.root_page).values_list("slug", flat=True)
        return RESERVED_SLUGS | frozenset(slugs)
    except DatabaseError:  # pragma: no cover - baza bez drzewa stron
        return RESERVED_SLUGS


#: Format identyfikatora strumienia danych GA4. Walidator stoi tu, a nie w podpowiedzi pola:
#: literówka w identyfikatorze nie objawia się niczym widocznym (skrypt Google'a wczytuje się
#: i milczy), więc jedynym momentem, w którym da się ją złapać, jest zapis ustawienia.
#: ``UA-…`` (Universal Analytics) celowo **nie** przechodzi – ta usługa nie zbiera już danych,
#: a wpisanie starego identyfikatora dawałoby banner zgody bez żadnej analityki za nim.
GA_MEASUREMENT_ID_PATTERN = r"^G-[A-Z0-9]{6,}$"
validate_ga_measurement_id = RegexValidator(
    regex=GA_MEASUREMENT_ID_PATTERN,
    message=(
        "Identyfikator Google Analytics 4 ma postać „G-” i co najmniej sześciu wielkich liter "
        "lub cyfr, na przykład G-ABC1234DEF. Znajdziesz go w GA4 w sekcji "
        "Administracja → Strumienie danych."
    ),
)


#: Granice pola „sekundy” slidera sponsorów – tu i w formularzu ekranu koordynatora
#: (``apps.web.competition_forms.SponsorSliderForm``), żeby jedna liczba nie rozjechała się
#: z drugą. Dolna granica jest **jedynką**, nie zerem: o tym, czy pasek w ogóle się pokazuje,
#: rozstrzyga osobny przełącznik (``sponsor_slider_enabled``) – „zero sekund” przestało być
#: sposobem na wyłączenie slidera (uwaga organizatora z 21.09.2026).
SPONSOR_SLIDER_MIN_SECONDS = 1
SPONSOR_SLIDER_MAX_SECONDS = 120


def validate_sponsor_slider_levels(value):
    """Każdy klucz musi być znanym poziomem współpracy (patrz ``PARTNER_LEVELS`` w ``.blocks``).

    Ekran koordynatora (``/coordinator/sponsor-slider/``) odrzuca nieznany klucz już
    w formularzu (``forms.MultipleChoiceField`` z tymi samymi ``choices``) – ten walidator broni
    drugiej drogi zapisu tego pola, czyli JSON-a wklejonego wprost w ``/cms/``.
    """
    if not isinstance(value, list):
        raise ValidationError("Lista poziomów musi być tablicą kluczy.")
    known = {key for key, _ in PARTNER_LEVELS}
    unknown = [item for item in value if item not in known]
    if unknown:
        raise ValidationError(f"Nieznane poziomy współpracy: {', '.join(unknown)}.")


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
    #: Znak fundacji w stopce. Pole, a nie plik w ``static/``: logotyp organizatora zmienia się poza
    #: rytmem wydań tak samo, jak jego adres i numer KRS, a stopka ma jedno źródło tych danych.
    #: ``SET_NULL`` – skasowanie obrazu w bibliotece ma zdjąć logotyp ze stopki, a nie wywrócić
    #: ustawienia serwisu; ``related_name="+"``, bo od obrazu nikt nie pyta o ustawienia.
    organizer_logo = models.ForeignKey(
        "wagtailimages.Image",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="logotyp organizatora",
        help_text="Znak fundacji w stopce serwisu. Wgrywa go `manage.py seed_partners`.",
    )
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

    #: Adresy profili w mediach społecznościowych. Stoją tu, a nie w szablonie, z tego samego
    #: powodu, co reszta danych organizatora: te same cztery adresy wiszą w dwóch miejscach
    #: (stopka i strona „Kontakt”), a zmiana nazwy profilu albo wycofanie serwisu zdarza się poza
    #: rytmem wydań – przy adresie wpisanym w szablonie każda taka poprawka byłaby deployem.
    #: Puste pole znaczy „nie pokazuj”: wycofanie serwisu jest skasowaniem adresu w ``/cms/``,
    #: a nie usunięciem znacznika z dwóch plików.
    facebook_url = models.URLField(
        "Facebook", blank=True, default="https://www.facebook.com/olimpiadakwantowa"
    )
    linkedin_url = models.URLField(
        "LinkedIn", blank=True, default="https://linkedin.com/showcase/olimpiada-kwantowa"
    )
    instagram_url = models.URLField(
        "Instagram", blank=True, default="https://www.instagram.com/olimpiadakwantowa"
    )
    x_url = models.URLField("X (dawniej Twitter)", blank=True, default="https://x.com/olimpiadakwant")
    tiktok_url = models.URLField(
        "X (dawniej Twitter)", blank=True, default="https://www.tiktok.com/@olimpiadakwantowa"
    )
    youtube_url = models.URLField(
        "X (dawniej Twitter)", blank=True, default="https://www.youtube.com/@OlimpiadaKwantowa"
    )

    #: Zdanie o oficjalnym starcie rejestracji. **Nie** rozstrzyga o niczym: o tym, czy formularz
    #: przyjmuje zgłoszenia, decyduje ``Edition.registration_*`` w panelu koordynatora. Pole istnieje
    #: dlatego, że organizator trzyma formularz otwarty do testów, ogłaszając jednocześnie datę
    #: startu – bez tego rozdziału zapowiedź daty wymagałaby zamknięcia rejestracji albo kłamstwa
    #: w treści. Puste = komunikatu nie ma.
    registration_note = models.CharField(
        "komunikat o rejestracji",
        max_length=200,
        blank=True,
        default="Zakończenie rejestracji: 28.02.2027",
    )

    #: Czy serwis w ogóle **oferuje** konto opiekuna szkolnego (nauczyciela). Domyślnie ``False``,
    #: i to jest decyzja organizatora, a nie ostrożność: w pierwszej edycji rola opiekuna nie jest
    #: ogłaszana publicznie („rejestracja nauczycieli ma być ukryta”), a konta zakłada się na
    #: prośbę – przez włączenie tego przełącznika na czas zapisów albo ręcznie przez organizatora.
    #:
    #: Wyłączony przełącznik znaczy trzy rzeczy naraz, bo inaczej ukrycie byłoby pozorne:
    #: adres ``/register/supervisor/`` odpowiada **404**, nigdzie nie ma do niego odnośnika,
    #: a uczestnik nie widzi w profilu pola „adres e-mail opiekuna szkolnego” – bez nauczycieli
    #: z kontem byłoby ono pytaniem o adres, którego nikt nie użyje.
    #:
    #: Czego przełącznik **nie** robi: nie odbiera panelu opiekunom, którzy konto już mają.
    #: Te konta powstały świadomie (rejestracją albo ręką organizatora), a ukrycie drogi wejścia
    #: nie jest tym samym, co odebranie komuś dostępu do danych, które już ogląda.
    #:
    #: Pole, a nie zmienna środowiskowa – z tego samego powodu, co identyfikator GA4 niżej:
    #: to jest decyzja organizatora podejmowana w trakcie edycji, a przy zmiennej każde jej
    #: odwrócenie byłoby wdrożeniem.
    #: Tekst nad formularzem rejestracji opiekuna szkolnego. Domyślnie **pusty** – organizator
    #: 22.09.2026 kazał zdjąć wstęp z tej strony („usuń tylko ten tekst nad formularzem”) i zapytał,
    #: czy da się go redagować z /cms/. Pole zamiast twardego usunięcia: pusty = brak akapitu,
    #: wpisany = akapit nad formularzem, bez wdrożenia przy każdej zmianie brzmienia. Formaty
    #: ograniczone do pogrubienia, kursywy i odnośnika – to jeden akapit wstępu, nie strona.
    supervisor_registration_intro = RichTextField(
        "wstęp nad formularzem rejestracji opiekunów",
        blank=True,
        default="",
        features=["bold", "italic", "link"],
        help_text="Puste = nad formularzem /register/supervisor/ nie ma żadnego tekstu.",
    )

    supervisor_registration_enabled = models.BooleanField(
        "rejestracja opiekunów szkolnych",
        default=False,
        help_text=(
            "Wyłączone: adres /register/supervisor/ zwraca 404, nigdzie nie ma do niego "
            "odnośnika, a uczestnicy nie widzą w profilu pola z adresem opiekuna szkolnego. "
            "Opiekunowie, którzy mają już konto, zachowują swój panel."
        ),
    )

    #: Czy ten serwis **oferuje** angielską wersję interfejsu. Domyślnie ``False`` i to jest
    #: decyzja organizatora, a nie ostrożność: „strona tylko w wersji polskiej (sam CMS może dawać
    #: opcję zrobienia strony w wersji angielskiej, ale do polskiej olimpiady niech będzie wersja
    #: tylko w języku polskim na razie)”. Sama umiejętność zostaje w systemie – katalog ``locale/en``
    #: jest skompilowany, ``settings.LANGUAGES`` wymienia oba języki, a organizator drugiego
    #: konkursu włącza angielski **temu** serwisowi jednym kliknięciem tutaj.
    #:
    #: Wyłączony przełącznik znaczy, że polski obowiązuje **niezależnie od tego, o co prosi
    #: przeglądarka** – inaczej ukrycie byłoby pozorne. Nie ma flagi w pasku konta, nagłówek
    #: ``Accept-Language: en`` niczego nie zmienia, ciasteczko ``django_language`` jest pomijane,
    #: a ``<html lang>`` i nagłówek ``Content-Language`` mówią ``pl``.
    #:
    #: Czego przełącznik **nie** robi: nie kasuje zapisanego wyboru. ``UserPreference.language``
    #: zostaje w bazie nietknięty i wraca do użytku w dniu, w którym organizator angielski włączy –
    #: skasowanie cudzego ustawienia przy zmianie konfiguracji serwisu byłoby odpowiedzią na
    #: pytanie, którego nikt nie zadał.
    #:
    #: Nie dotyczy paneli redakcyjnych: język ``/cms/`` wybiera redaktor w swoim profilu Wagtaila
    #: (``UserProfile.preferred_language``) i to ustawienie żyje dalej własnym życiem.
    #:
    #: Pole, a nie zmienna środowiskowa – z tego samego powodu, co przełącznik wyżej: to jest
    #: decyzja organizatora, a przy zmiennej jej odwrócenie byłoby wdrożeniem.
    english_interface_enabled = models.BooleanField(
        "angielska wersja interfejsu",
        default=False,
        help_text=(
            "Wyłączone: serwis jest po polsku niezależnie od ustawień przeglądarki, a w pasku "
            "konta nie ma przełącznika języka. Włączenie dokłada flagę „EN” i pozwala każdemu "
            "wybrać angielski; zapisane wcześniej wybory wracają wtedy same."
        ),
    )

    #: Identyfikator strumienia danych Google Analytics 4. **Puste pole wyłącza analitykę
    #: całkowicie**: serwis nie wczytuje wtedy żadnego skryptu Google'a, nie pyta o zgodę
    #: (pasek cookie zostaje informacyjny, bo nie ma czego wstrzymywać do kliknięcia), a nagłówek
    #: CSP nie wymienia ani jednego hosta Google'a. To jest jedyny przełącznik tej funkcji –
    #: instalacja bez identyfikatora zachowuje się dokładnie tak, jak przed jej dodaniem.
    #:
    #: Pole, a nie zmienna środowiskowa: założenie usługi GA4 i wklejenie identyfikatora należy do
    #: organizatora, a nie do wdrożenia – przy zmiennej każde takie wklejenie byłoby deployem.
    ga_measurement_id = models.CharField(
        "identyfikator Google Analytics (G-…)",
        max_length=32,
        blank=True,
        validators=[validate_ga_measurement_id],
        help_text=(
            "Puste pole = brak analityki: serwis nie wczytuje skryptów Google'a i nie pyta "
            "o zgodę. Po wpisaniu identyfikatora pasek cookie zamienia się w pytanie o zgodę, "
            "a statystyki zbierają się dopiero po jej udzieleniu."
        ),
    )

    #: Pasek rotujących logotypów partnerów i organizatora w menu serwisu (uwaga organizatora
    #: z 21.09.2026, „jak na Olimpiadzie Biologicznej”), po prawej stronie pozycji „FAQ”. Trzy
    #: pola, nie jedno, bo to trzy różne decyzje: **czy** w ogóle go pokazywać, **jak szybko**
    #: się przesuwa i **których** partnerów pokazuje.
    #:
    #: ``sponsor_slider_enabled`` jest jedynym wyłącznikiem – bez niego trzeba by wyłączać slider
    #: „sekundami równymi zeru”, co dawało dwie drogi do tego samego stanu i dwa miejsca, w których
    #: mogły się rozjechać. Domyślnie włączony, bo organizator zamówił go od razu widocznym.
    sponsor_slider_enabled = models.BooleanField(
        "slider sponsorów włączony",
        default=True,
        help_text="Wyłączenie chowa pasek logotypów z menu na każdej stronie serwisu.",
    )
    sponsor_slider_seconds = models.PositiveSmallIntegerField(
        "slider sponsorów – sekundy",
        default=5,
        validators=[
            MinValueValidator(SPONSOR_SLIDER_MIN_SECONDS),
            MaxValueValidator(SPONSOR_SLIDER_MAX_SECONDS),
        ],
        help_text="Co ile sekund pasek przesuwa się o jeden logotyp.",
    )
    #: Puste = bez filtra, czyli każdy poziom współpracy z logotypem trafia do slidera – to jest
    #: stan domyślny każdego konkursu, więc dołożenie tego pola nie zmienia ani jednego menu.
    #: Klucze są tymi samymi kluczami, co w ``PartnersStreamBlock`` (``PartnerBlock.level``) –
    #: druga lista poziomów w tym samym serwisie mogłaby się z tamtą rozjechać.
    sponsor_slider_levels = models.JSONField(
        "slider sponsorów – poziomy",
        default=list,
        blank=True,
        validators=[validate_sponsor_slider_levels],
        help_text=(
            "Poziomy współpracy pokazywane w sliderze (patrz „Partnerzy” w /cms/). "
            "Puste = pokazuj partnerów każdego poziomu."
        ),
    )

    #: Kolejność, etykieta i nazwa znaku graficznego serwisów – jedna lista dla stopki i „Kontaktu”.
    #: Gdyby o kolejności decydował szablon, dołożenie piątego serwisu wymagałoby zgodnej poprawki
    #: w dwóch plikach, a rozjechanie się ich nie miałoby jak się ujawnić.
    SOCIAL_NETWORKS = (
        ("facebook_url", "Facebook", "facebook"),
        ("linkedin_url", "LinkedIn", "linkedin"),
        ("instagram_url", "Instagram", "instagram"),
        ("x_url", "X", "x"),
        ("tiktok_url", "TikTok", "tiktok"),
        ("youtube_url", "YouTube", "youtube"),
    )

    @property
    def social_links(self) -> list[dict]:
        """Niepuste adresy profili jako ``{"url", "label", "icon"}`` – materiał pętli w szablonie."""
        return [
            {"url": url, "label": label, "icon": icon}
            for field, label, icon in self.SOCIAL_NETWORKS
            if (url := getattr(self, field))
        ]

    panels = [
        MultiFieldPanel([FieldPanel("site_name"), FieldPanel("tagline")], heading="Serwis"),
        MultiFieldPanel(
            [
                FieldPanel("organizer_name"),
                FieldPanel("organizer_logo"),
                FieldPanel("organizer_address"),
                FieldPanel("organizer_registry"),
            ],
            heading="Organizator",
        ),
        MultiFieldPanel(
            [FieldPanel("contact_email"), FieldPanel("contact_phone"), FieldPanel("contact_url")],
            heading="Kontakt",
        ),
        MultiFieldPanel(
            [
                FieldPanel("facebook_url"),
                FieldPanel("linkedin_url"),
                FieldPanel("instagram_url"),
                FieldPanel("x_url"),
                FieldPanel("tiktok_url"),
                FieldPanel("youtube_url"),
            ],
            heading="Media społecznościowe",
        ),
        MultiFieldPanel(
            [
                FieldPanel("registration_note"),
                FieldPanel("supervisor_registration_enabled"),
                FieldPanel("supervisor_registration_intro"),
            ],
            heading="Rejestracja",
        ),
        # Osobna sekcja, a nie pole doklejone do „Rejestracji”: język interfejsu obowiązuje na
        # każdej stronie serwisu, a nie wyłącznie w formularzu zgłoszeniowym – przełącznik ukryty
        # pod cudzym nagłówkiem byłby ustawieniem, którego organizator nie znajdzie wtedy, gdy go
        # potrzebuje.
        MultiFieldPanel([FieldPanel("english_interface_enabled")], heading="Język interfejsu"),
        MultiFieldPanel([FieldPanel("ga_measurement_id")], heading="Analityka"),
        # Tempo i włącznik slidera stoją też tutaj – ten sam wiersz w bazie, co ekran koordynatora
        # (/coordinator/sponsor-slider/), więc obie drogi zapisu zawsze się zgadzają. Listę
        # partnerów redaguje się osobno, na stronie „Partnerzy”.
        MultiFieldPanel(
            [
                FieldPanel("sponsor_slider_enabled"),
                FieldPanel("sponsor_slider_seconds"),
                FieldPanel("sponsor_slider_levels"),
            ],
            heading="Slider sponsorów",
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
        if not self.is_second_level():
            return
        if self.slug in RESERVED_SLUGS:
            raise ValidationError(
                {
                    "slug": (
                        f"Adres „/{self.slug}/” należy do aplikacji (logowanie, panel, API). "
                        "Strona pod tym slugiem nigdy by się nie otworzyła – wybierz inny."
                    )
                }
            )
        # Prefiks ścieżki innego konkursu (§ 2.3). Komunikat jest osobny, bo i powód jest inny:
        # tam adres należy do aplikacji, tu – do organizatora, który stoi pod tym segmentem ze
        # swoim całym serwisem. Zapytanie wykonuje się dopiero po sprawdzeniu listy stałej, więc
        # strona o slugu ``login`` odpada bez ani jednego odczytu z bazy.
        if self.slug in competition_path_prefixes():
            raise ValidationError(
                {
                    "slug": (
                        f"Adres „/{self.slug}/” jest prefiksem innego konkursu na tej platformie. "
                        "Strona pod tym slugiem przechwyciłaby jego adresy – wybierz inny."
                    )
                }
            )


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


def _partners_with_entries(home):
    """Opublikowana strona partnerów, **o ile ma choć jeden wpis** – inaczej ``None``.

    Sekcja partnerów na stronie głównej ma się nie pojawić, dopóki organizator nie potwierdzi ani
    jednego patronatu: pusty pas logotypów albo nagłówek nad niczym czyta się jak awaria szablonu,
    a wcześniejsza wersja strony sugerowała patronaty, których nie ma (patrz ``PartnersPage``).
    Warunek jest tutaj, a nie w szablonie, bo ``{% if %}`` nad StreamFieldem ukryłby tylko sekcję,
    zostawiając zapytanie – a to samo pytanie zadaje sobie i szablon, i test.
    """
    page = PartnersPage.objects.live().child_of(home).first()
    return page if page is not None and len(page.partners) else None


class HomePage(CMSPage):
    """Strona główna serwisu (korzeń witryny). Przejmuje ``/`` po widoku ``web:home`` z T-08."""

    hero_title = models.CharField("nagłówek", max_length=200, blank=True)
    hero_text = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    # „O Olimpiadzie” była osobną pozycją menu i osobną stroną, na którą trafiał co czterdziesty
    # czytelnik: odpowiedź na „co to jest i kto to organizuje” stała jedno kliknięcie za hasłem,
    # które tę ciekawość wzbudzało. Treść wraca więc na stronę główną jako sekcja pod kotwicą
    # ``#o-olimpiadzie`` (stary adres przekierowuje właśnie tam), a nie jako tekst zaszyty
    # w szablonie – to nadal treść redakcyjna i ma ten sam zestaw bloków, co strona treści,
    # żeby przeniesienie akapitu w którąkolwiek stronę nie gubiło bloku.
    about_title = models.CharField(
        "nagłówek sekcji „O Olimpiadzie”",
        max_length=200,
        default="O Olimpiadzie",
        blank=True,
        help_text="Puste = sekcja się nie pokazuje.",
    )
    about_body = StreamField(DocumentStreamBlock(), verbose_name="O Olimpiadzie", blank=True)
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
        MultiFieldPanel([FieldPanel("about_title"), FieldPanel("about_body")], heading="O Olimpiadzie"),
        FieldPanel("show_timeline"),
        MultiFieldPanel([FieldPanel("steps_title"), FieldPanel("steps")], heading="Jak zacząć"),
    ]
    search_fields = Page.search_fields + [
        index.SearchField("hero_title"),
        # Treść „O Olimpiadzie” nie ma już własnej strony, więc bez tego wpisu przestałaby być
        # wyszukiwalna – a to ona odpowiada na pytanie „co to za olimpiada”.
        index.SearchField("about_body"),
    ]

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
        "cms.PartnersPage",
        "cms.ArchiveIndexPage",
        "cms.ResultsPage",
        # Najczęstsze pytania. Pod stroną główną, a nie w sekcji dokumentów: FAQ nie jest
        # dokumentem organizatora, tylko odpowiedzią na pytania o obsługę serwisu, i ma krótki
        # adres, bo trafia do listów i do formularza pomocy.
        "cms.FAQPage",
    ]
    max_count = 1

    class Meta:
        verbose_name = "strona główna"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        now = timezone.now()
        # Edycja **tego** konkursu, a nie „bieżąca w bazie”: w instalacji wielokonkursowej
        # pierwsza z brzegu jest cudza, a strona główna konkursu A pokazywałaby wtedy harmonogram
        # konkursu B. Konkurs bierzemy z żądania (za darmo) – patrz ``apps.cms.tenancy``.
        competition = competition_for_page(self, request)
        edition = current_edition(competition)
        context.update(
            {
                "now": now,
                "edition": edition,
                "current_stage": current_stage(edition, now) if edition else None,
                "stage_rows": stage_rows(edition, now),
                "latest_news": NewsPage.objects.live().descendant_of(self).order_by("-date", "-pk")[:3],
                # Newsroom nie ma już pozycji w menu (uwagi organizatora z 21.09.2026) – drogą do
                # pełnej listy jest panel „Aktualności” na stronie głównej, więc panel potrzebuje
                # adresu newsroomu. ``None`` = newsroom nieopublikowany i panelu nie ma wcale.
                "news_index": NewsIndexPage.objects.live().child_of(self).first(),
                "downloads": _download_rows(self),
                # Sekcja „Dokumenty do pobrania” prowadzi do pełnej listy; strona-indeks bywa
                # nieopublikowana (świeża baza przed seedem), więc szablon pyta o ``None``.
                "documents_index": DocumentIndexPage.objects.live().child_of(self).first(),
                "partners_page": _partners_with_entries(self),
            }
        )
        context.update(self._workshops_context(now))
        return context

    def _workshops_context(self, now) -> dict:
        """Trzy najbliższe warsztaty online – z tabeli na stronie „Warsztaty”, nie z własnej listy.

        Warsztaty były dotąd tylko tabelą w treści ``/harmonogram/``: kto nie wszedł na tę
        podstronę, nie dowiadywał się, że w ogóle są, a są bezpłatne i otwarte. Zapowiedź na
        stronie głównej czyta **tę samą** tabelę (``apps.cms.workshops``), więc redakcja nie
        utrzymuje drugiej listy i nie ma jak ich rozjechać.

        Parsujemy StreamField przy żądaniu, a nie w polu obok: harmonogram jest treścią
        redakcyjną, a jedyną kopią prawdy ma być tabela na stronie warsztatów. Koszt to jedno
        zapytanie o stronę; sama tabela leży w jednym polu JSON.
        """
        page = ContentPage.objects.live().child_of(self).filter(slug=WORKSHOPS_SLUG).first()
        return {
            "workshops_page": page,
            "workshops": upcoming_workshops(page, now=now),
        }


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


class PartnersPage(CMSPage):
    """Strona ``/partnerzy/``: partnerzy, patroni i sponsorzy pogrupowani po poziomie współpracy.

    Osobny typ zamiast ``ContentPage`` z listą wypunktowaną, bo lista partnerów jest **danymi**,
    a nie tekstem: każdy wpis ma poziom współpracy (decyduje o grupie i kolejności), logotyp,
    adres i jedno zdanie opisu. W akapicie redakcyjnym te pola byłyby konwencją zapisu, której
    nikt nie wyegzekwuje, a strona główna nie miałaby czego pokazać w pasie logotypów.

    Strona jest **opublikowana z pustą listą**. Poprzednia wersja (``ContentPage`` ze starego
    WordPressa) wymieniała trzy nazwy, z których jedna – „Uniwersytet Kwantowy” – jest instytucją
    nieistniejącą, a pozostałe dwie nie mają potwierdzonego patronatu; całość stała więc jako
    szkic i ``/partnerzy/`` odpowiadało 404. Puste zaproszenie do współpracy jest uczciwsze niż
    404 i nieporównanie uczciwsze niż sugerowanie patronatu, którego nie ma – dlatego lista
    partnerów startuje pusta, a ``partners_empty`` pilnuje, żeby pustka była komunikatem,
    a nie dziurą w układzie.

    ``max_count = 1``: „Partnerzy” to pozycja menu, a nie typ treści, którego bywa wiele.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)
    partners = StreamField(PartnersStreamBlock(), verbose_name="partnerzy", blank=True)
    become_partner_title = models.CharField(
        "nagłówek sekcji „zostań partnerem”",
        max_length=200,
        blank=True,
        default="Zostań partnerem",
        help_text="Puste = sekcja z zaproszeniem do współpracy się nie pokazuje.",
    )
    become_partner_body = RichTextField("zaproszenie do współpracy", features=RICH_TEXT_FEATURES, blank=True)
    contact_email = models.EmailField(
        "e-mail w sprawie współpracy",
        blank=True,
        help_text="Adres przycisku „Napisz do nas”. Puste = przycisku nie ma.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("partners"),
        MultiFieldPanel(
            [
                FieldPanel("become_partner_title"),
                FieldPanel("become_partner_body"),
                FieldPanel("contact_email"),
            ],
            heading="Zostań partnerem",
        ),
    ]
    search_fields = Page.search_fields + [index.SearchField("intro"), index.SearchField("partners")]

    template = "cms/partners_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = []
    max_count = 1

    class Meta:
        verbose_name = "partnerzy"
        verbose_name_plural = "partnerzy"

    def groups(self) -> list[dict]:
        """Partnerzy pogrupowani po poziomie współpracy, w kolejności ``PARTNER_LEVELS``.

        Grupowanie jest tutaj, a nie w szablonie: ``{% regroup %}`` porządkuje po kolejności
        wystąpienia, więc kolejność grup zależałaby od tego, w jakiej kolejności redaktor dodał
        wpisy – patron honorowy potrafiłby wylądować pod sponsorem złotym. Grupy puste nie
        wchodzą do wyniku, więc szablon nie zna ani jednego poziomu z nazwy.
        """
        labels = dict(PARTNER_LEVELS)
        buckets: dict[str, list] = {key: [] for key, _ in PARTNER_LEVELS}
        for block in self.partners:
            # Poziom spoza listy (wpis sprzed zmiany słownika) trafia do własnej grupy na końcu,
            # zamiast zniknąć ze strony bez śladu.
            buckets.setdefault(block.value["level"], []).append(block.value)
        return [
            {"level": key, "label": labels.get(key, key), "partners": entries}
            for key, entries in buckets.items()
            if entries
        ]

    def partners_empty(self) -> bool:
        return not len(self.partners)


class ProblemsPage(CMSPage):
    """Zadania bieżącego etapu. Treści PDF pokazujemy dopiero po ``Stage.opens_at``.

    Pod kartą etapu bieżącego stoi druga sekcja: **zadania treningowe**. To ten sam mechanizm
    (etap, zadania, PDF-y spod ``competitions:problem-statement``), tylko etap jest piaskownicą
    bez terminu (``StageKind.TRAINING``), więc jego treści są jawne od chwili utworzenia i nie
    znikają po żadnym deadline'u. Sekcja jest tutaj, a nie w treści redakcyjnej, bo lista zadań
    ma jedno źródło prawdy – bazę – i nie może rozjechać się z tym, co przyjmuje upload.
    """

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
        edition = current_edition(competition_for_page(self, request))
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
                or (
                    "Zadań jeszcze nie ogłoszono. Treści zadań tego etapu zostaną opublikowane "
                    "w chwili jego otwarcia."
                ),
            }
        )
        context.update(self._training_context(edition, now))
        return context

    def _training_context(self, edition, now) -> dict:
        """Etap treningowy i jego zadania – druga sekcja strony, niezależna od etapu bieżącego.

        ``has_opened`` sprawdzamy tak samo jak dla etapu zawodów: to ta sama reguła jawności
        treści, co w ``ProblemStatementView`` (link do PDF-a przed otwarciem etapu i tak dałby 404,
        więc strona nie może go pokazać). W praktyce trening jest otwarty od chwili posiania,
        ale reguła ma być jedna, a nie „jedna dla zawodów, druga dla treningu”.
        """
        stage = training_stage(edition)
        has_opened = bool(stage and stage.has_opened(now))
        return {
            "training_stage": stage,
            "training_problems": list(stage.problems.order_by("number")) if has_opened else [],
        }


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
        attachments = list(self.attachments.select_related("document"))
        context["attachments"] = attachments
        # Pierwszy PDF osobno: dostaje przycisk **nad** treścią, a nie tylko wiersz w karcie
        # „Do pobrania” niżej. Zgłoszenie organizatora dotyczyło co prawda odnośników przy
        # zgodach, ale wynika z niego to samo pytanie czytelnika: „gdzie jest plik”. Lista
        # jest już wczytana, więc wybór idzie po niej, a nie osobnym zapytaniem.
        context["pdf_attachment"] = next((item for item in attachments if item.is_pdf), None)
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
        competition = competition_for_page(self, request)
        edition = current_edition(competition)
        # Filtr po ``results_published_at`` zostaje: znacznik na etapie jest tym, co koordynator
        # zdejmuje, żeby wycofać ogłoszenie, a sam rekord publikacji ma zostać jako ślad.
        #
        # Zawężenie do konkursu jest **drugim** filtrem i musi być: sekcja „Archiwum” wymienia
        # wszystkie ogłoszone etapy, także z dawnych roczników, więc bez niego tabela wyników
        # jednej olimpiady wyliczałaby etapy drugiej – razem z odnośnikami do jej publikacji.
        # Drogę do konkursu (``stage__edition__competition``) zna manager modelu, a regułę
        # odwrotów dla instalacji bez konkursów – ``scope_to_competition``.
        publications = (
            scope_to_competition(
                ResultsPublication.objects.filter(stage__results_published_at__isnull=False),
                competition,
            )
            .select_related("stage", "stage__edition", "stage__scoring_scale")
            # Zadania etapów jednym zapytaniem na całą stronę – czyta je ``problem_maxima_by_number``
            # (nagłówki „Zad. 3 (max 12,5)”, wydanie 0.35.0). Bez tego każda tabela dokładałaby dwa
            # zapytania, a liczba zapytań rosłaby z liczbą ogłoszonych etapów.
            .prefetch_related("stage__problems")
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
                        # Maksima zadań do nagłówków kolumn („Zad. 3 (max 12,5)”, wydanie 0.35.0) –
                        # opis skali, nie dane uczestnika, więc wolno je czytać obok snapshotu.
                        "problem_maxima": problem_maxima_by_number(stage),
                    }
                )
            else:
                archive.append({"stage": stage, "publication": publication})
        context.update({"edition": edition, "tables": tables, "archive": archive})
        return context


class FAQPage(CMSPage):
    """Najczęstsze pytania (``/faq/``) – pytania pogrupowane w sekcje.

    Osobny typ zamiast ``ContentPage`` z listą nagłówków, bo pytanie i odpowiedź są **danymi**,
    a nie tekstem: każde pytanie ma własną kotwicę (żeby dało się je przesłać odnośnikiem w
    odpowiedzi na zgłoszenie), własną sekcję i własny stan „rozwinięte / zwinięte”. W akapicie
    redakcyjnym byłaby to konwencja zapisu, której nikt nie wyegzekwuje, a formularz zgłoszenia
    nie miałby dokąd odesłać czytelnika przed napisaniem sprawy.

    ``InlinePanel`` zamiast ``StreamField``: wpisy mają zawsze ten sam kształt (sekcja, pytanie,
    odpowiedź), a redakcja przestawia je kolejnością wierszy. StreamField z jednym typem bloku
    dawałby to samo, tylko z dodatkową warstwą wyboru „jaki blok wstawić”, w której wybór jest
    jeden.

    Grupowanie liczy ``sections()``, a nie ``{% regroup %}`` w szablonie: znacznik grupuje po
    **kolejności wystąpienia**, więc wpis dopisany na końcu do sekcji z początku listy zakładałby
    drugą sekcję o tej samej nazwie. Tutaj sekcja powstaje raz, w kolejności pierwszego wystąpienia.
    """

    intro = RichTextField("wprowadzenie", features=RICH_TEXT_FEATURES, blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        InlinePanel("entries", label="pytania"),
    ]
    search_fields = Page.search_fields + [index.SearchField("intro")]

    template = "cms/faq_page.html"
    parent_page_types = ["cms.HomePage"]
    subpage_types = []
    max_count = 1

    class Meta:
        verbose_name = "najczęstsze pytania"
        verbose_name_plural = "najczęstsze pytania"

    def sections(self) -> list[dict]:
        """Wpisy pogrupowane w sekcje, w kolejności pierwszego wystąpienia sekcji.

        Wpis bez sekcji trafia do grupy o pustej nazwie – szablon rysuje ją wtedy bez nagłówka,
        zamiast wymyślać podpis („Pozostałe”), którego redakcja nie napisała.
        """
        groups: dict[str, dict] = {}
        for entry in self.entries.all():
            group = groups.setdefault(entry.section, {"section": entry.section, "entries": []})
            group["entries"].append(entry)
        return list(groups.values())


class FAQEntry(Orderable):
    """Jedno pytanie z odpowiedzią.

    ``anchor`` jest wyliczany, a nie wpisywany: redakcja pisze pytanie, a nie identyfikator HTML,
    a kotwica wpisana ręcznie rozjeżdżałaby się z treścią przy pierwszej poprawce. Podstawą jest
    identyfikator wiersza, bo on **nie zmienia się** przy przeredagowaniu pytania – odnośnik
    wysłany w odpowiedzi na zgłoszenie ma działać także po tym, jak ktoś poprawi w pytaniu literówkę.
    """

    page = ParentalKey(FAQPage, on_delete=models.CASCADE, related_name="entries")
    section = models.CharField(
        "sekcja",
        max_length=100,
        blank=True,
        help_text="Nagłówek grupy, np. „Konto i rejestracja”. Puste = pytanie bez sekcji.",
    )
    question = models.CharField("pytanie", max_length=250)
    answer = RichTextField("odpowiedź", features=RICH_TEXT_FEATURES)

    panels = [FieldPanel("section"), FieldPanel("question"), FieldPanel("answer")]

    class Meta(Orderable.Meta):
        verbose_name = "pytanie"
        verbose_name_plural = "pytania"

    def __str__(self) -> str:
        return self.question

    @property
    def anchor(self) -> str:
        """Kotwica pytania: ``pytanie-<id>``. Stała w czasie, niezależna od brzmienia pytania."""
        return f"pytanie-{self.pk}"


#: Limit długości komunikatu w banerze. Pięćset znaków to około trzech zdań – tyle, ile da się
#: przeczytać w pasku nad treścią, zanim czytelnik przewinie stronę. Dłuższa wiadomość jest
#: aktualnością i ma własny typ strony.
ANNOUNCEMENT_MAX_TEXT_LENGTH = 500


class AnnouncementLevel(models.TextChoices):
    """Waga komunikatu. Trzy poziomy, bo tyle jest różnych reakcji czytelnika.

    ``INFO`` – wiadomość do przeczytania („ruszyły zapisy na warsztaty”). ``WARNING`` – coś, co
    zmienia plan czytelnika („termin przesunięty”). ``DANGER`` – coś, co właśnie nie działa
    („wysyłka plików jest niedostępna”). Czwarty poziom („sukces”) świadomie nie istnieje: pasek
    nad treścią całego serwisu nie jest miejscem na gratulacje.
    """

    INFO = "info", "informacja"
    WARNING = "warning", "ostrzeżenie"
    DANGER = "danger", "awaria"


class Announcement(models.Model):
    """Komunikat organizatora wyświetlany pod nagłówkiem na **każdej** stronie serwisu.

    Po co osobny model, skoro jest newsroom: bo to jest inna wiadomość. Aktualność czyta ten, kto
    wejdzie na ``/aktualnosci/``; komunikat („przedłużamy termin do piątku”, „logowanie przez
    Google nie działa”) musi zobaczyć każdy, kto jest w serwisie – łącznie z uczestnikiem, który
    właśnie próbuje wysłać pracę i nigdzie indziej nie zagląda.

    Reguły wyświetlania, pamięć podręczna i procesor kontekstu są w ``apps.cms.announcements``.
    """

    #: Właściciel komunikatu. Własny klucz obcy, a nie droga przez inny model
    #: (``docs/UNIWERSALNY-ETAP-1.md`` § 3.2), bo komunikat **nie ma** przez co dojść do konkursu:
    #: nie wisi przy edycji, etapie ani stronie – wisi nad całym serwisem. Bez tej kolumny baner
    #: był globalny dla instalacji, czyli organizator konkursu A ogłaszał przerwę techniczną na
    #: stronie konkursu B.
    #:
    #: Nullowalny i to jest stan przejściowy, nie projekt: wydanie C kładzie kolumnę przy kodzie,
    #: który ją wypełnia, a domknięcie na ``NOT NULL`` wchodzi w wydaniu D po kontroli z § 4.4.
    #: ``PROTECT`` – jak przy edycji: skasowanie konkursu z wiszącym komunikatem ma się zatrzymać
    #: na wyjątku, a nie zabrać po cichu jego ogłoszenia.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="announcements",
        verbose_name="konkurs",
    )
    text = models.TextField("treść", max_length=ANNOUNCEMENT_MAX_TEXT_LENGTH)
    # Odnośnik jest **parą pól**, a nie znacznikiem w treści: baner renderuje zwykły tekst
    # (autoescapowany), więc adres wpisany w treść byłby napisem, a nie linkiem – a pozwolenie
    # na HTML w komunikacie widocznym na całym serwisie otwierałoby powierzchnię bez potrzeby.
    link_url = models.URLField("adres odnośnika", max_length=300, blank=True)
    link_label = models.CharField(
        "etykieta odnośnika",
        max_length=80,
        blank=True,
        help_text="Puste = odnośnik się nie pokazuje, nawet gdy adres jest wpisany.",
    )
    level = models.CharField(
        "waga", max_length=16, choices=AnnouncementLevel.choices, default=AnnouncementLevel.INFO
    )
    # Okno czasowe zamiast ręcznego gaszenia: komunikat o przerwie technicznej znika sam.
    # Wyłącznik ``is_active`` zostaje obok jako hamulec awaryjny, bo „zdejmij to natychmiast”
    # jest osobną potrzebą od „to obowiązuje do piątku”.
    starts_at = models.DateTimeField("od", default=timezone.now)
    ends_at = models.DateTimeField(
        "do", null=True, blank=True, help_text="Puste = komunikat wisi do wyłączenia."
    )
    is_active = models.BooleanField(
        "włączony",
        default=True,
        help_text="Wyłącznik awaryjny. Odznaczenie zdejmuje komunikat natychmiast, niezależnie od dat.",
    )
    # Czy czytelnik może baner zamknąć. Wybór zapamiętuje ``localStorage`` przeglądarki
    # (``static/js/announcements.js``), a nie cookie ani konto – to preferencja widoku, a nie
    # dana o osobie. Komunikat niezamykalny (awaria, termin) zostaje na ekranie i tak ma być.
    dismissible = models.BooleanField(
        "można zamknąć",
        default=True,
        help_text=(
            "Czytelnik może schować komunikat w swojej przeglądarce. Odznacz dla komunikatów "
            "o awarii i terminach – te mają zostać na ekranie."
        ),
    )
    # ``SET_NULL``: skasowanie konta koordynatora nie może zdjąć ogłoszonego komunikatu. Bez autora
    # zostaje sama treść i data, czyli nadal ogłoszenie.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements",
        verbose_name="utworzył",
    )
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    #: Manager z ``for_competition``. Zawężenie należy do querysetu, a nie do widoku
    #: (``apps.tenancy.managers``): baner, ekran koordynatora i panel redakcyjny czytają ten sam
    #: model, więc filtr dopisany w jednym z nich byłby filtrem, który dwa pozostałe pominą.
    objects = CompetitionScopedManager()

    class Meta:
        verbose_name = "komunikat"
        verbose_name_plural = "komunikaty"
        ordering = ("-starts_at", "-id")
        constraints = [
            # Okno o niedodatniej długości nigdy nie jest otwarte, więc komunikat z takim oknem
            # byłby ogłoszeniem, którego nikt nigdy nie zobaczy.
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(starts_at__lt=F("ends_at")),
                name="cms_announcement_window_ordered",
            ),
        ]

    def __str__(self) -> str:
        return self.text[:60]

    def save(self, *args, **kwargs):
        """Nowy komunikat bez wskazanego konkursu dostaje ten „na teraz”.

        Reguła i uzasadnienie są te same, co przy ``competitions.Edition.save`` – celowo, bo to
        jest ta sama decyzja: wartość domyślna należy do **modelu**, a nie do każdego z wołających.
        Komunikat zapisują dziś trzy drogi i żadna z nich nie należy do tego zadania: ekran
        ``/coordinator/announcements/``, fragment w ``/cms/`` i ``/admin/``. Komunikat bez
        właściciela byłby niewidoczny dla banera, czyli ogłoszeniem, którego nikt nie zobaczy –
        a to jest dokładnie ta klasa regresji, której zabrania § 0 dokumentu.

        Wypełniamy **wyłącznie przy wstawianiu**: przy zapisie istniejącego wiersza konkurs jest
        faktem, a nie domyślną wartością – podstawianie go z kontekstu przy każdym ``save()``
        pozwoliłoby żądaniu jednego konkursu przepisać komunikat drugiego.
        """
        if self._state.adding and self.competition_id is None:
            self.competition = resolve_competition()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        # Ta sama reguła, co constraint wyżej – tylko z komunikatem, który da się pokazać pod
        # polem formularza. Błąd jest przypięty do „do”, bo to ono jest drugą, dopisywaną datą.
        if self.ends_at is not None and self.starts_at is not None and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": "Koniec komunikatu musi być po jego początku."})

    @property
    def has_link(self) -> bool:
        """Odnośnik pokazuje się wyłącznie z **obydwoma** polami – adres bez etykiety to goły URL."""
        return bool(self.link_url and self.link_label)

    def is_live(self, now=None) -> bool:
        """Czy komunikat obowiązuje „na teraz”. Ta sama reguła, co zapytanie ``active_announcements``."""
        now = now or timezone.now()
        if not self.is_active or self.starts_at > now:
            return False
        return self.ends_at is None or self.ends_at > now


class WorkshopAttendance(models.Model):
    """Obecność jednego uczestnika na jednych warsztatach – podstawa zaświadczenia z warsztatów.

    Dlaczego to mieszka w CMS-ie, a nie w ``apps.competitions``. Bo warsztaty **nie są etapem
    zawodów**: nie mają wpisu, terminu egzekwowanego przez serwer ani punktów, a ich harmonogram
    jest treścią redakcyjną (blok ``schedule`` na stronie „Warsztaty”). Tabela trzyma więc tylko
    to, czego treść redakcyjna nie umie zapamiętać – kto był – i wiąże to z wierszem harmonogramu
    przez ``workshop_key`` (``apps.cms.workshops.workshop_key``).

    Klucz tekstowy zamiast klucza obcego, bo po drugiej stronie **nie ma tabeli**: wiersz
    harmonogramu jest fragmentem StreamFielda w treści strony. Cena tego rozwiązania jest jawna
    i opisana przy ``workshop_key``: zmiana tematu albo daty tworzy nowy klucz, a stara obecność
    przestaje pasować do wiersza. To lepsze niż przenoszenie harmonogramu do bazy tylko po to,
    żeby dało się postawić klucz obcy – harmonogram jest i zostaje tekstem, który pisze redakcja.

    Obecność zapisuje **koordynator**, a nie uczestnik: to ona jest oświadczeniem organizatora,
    na którym stoi dokument. Stąd ``created_by`` i brak jakiejkolwiek drogi zapisu z panelu ucznia.
    """

    participant = models.ForeignKey(
        "accounts.Participant",
        on_delete=models.CASCADE,
        related_name="workshop_attendance",
        verbose_name="uczestnik",
    )
    workshop_key = models.CharField("warsztat", max_length=WORKSHOP_KEY_LENGTH)
    created_at = models.DateTimeField("zapisano", default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workshop_attendance_marked",
        verbose_name="odhaczył",
    )

    class Meta:
        verbose_name = "obecność na warsztatach"
        verbose_name_plural = "obecności na warsztatach"
        ordering = ("workshop_key", "id")
        constraints = [
            # Jedna obecność na parę uczestnik + warsztat. Bez tego dwa kliknięcia „Zapisz” na
            # tej samej tabeli dopisałyby drugi wiersz, a zaświadczenie wyliczyłoby te same
            # zajęcia dwa razy.
            models.UniqueConstraint(
                fields=["participant", "workshop_key"],
                name="cms_workshop_attendance_unique",
            ),
        ]
        indexes = [
            # Tabela obecności w panelu czyta wszystkie odhaczenia jednej kolumny naraz.
            models.Index(fields=["workshop_key"], name="cms_workshop_key_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id} @ {self.workshop_key}"
