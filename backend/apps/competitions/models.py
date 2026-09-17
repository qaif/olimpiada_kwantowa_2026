"""Modele domeny zawodów: edycja, etap, skala punktacji, próg kwalifikacji, zadanie, wpis do etapu.

Zasady:
- czas zawsze przez ``django.utils.timezone.now()`` (pola w UTC, ``USE_TZ=True``),
- reguły integralności są jednocześnie w ``clean()`` (czytelny ValidationError) i w bazie
  (constraint – ostatnia linia obrony przed zapisem z pominięciem ``full_clean()``),
- dostęp do danych wyłącznie przez ORM.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import GROUP_COORDINATOR, Participant

from .storage import private_media_storage
from .video import DEFAULT_VIDEO_BASE_URL, VideoProvider

# Domyślna skala Olimpiady Matematycznej. Kolejność rosnąca jest częścią kontraktu (walidacja niżej).
DEFAULT_SCORING_VALUES: list[dict] = [
    {"value": 0, "label": "brak istotnego postępu"},
    {"value": 2, "label": "istotny postęp, rozwiązanie niepełne"},
    {"value": 5, "label": "rozwiązanie pełne z drobnymi usterkami"},
    {"value": 6, "label": "rozwiązanie pełne i poprawne"},
]
DEFAULT_MAX_VALUE = 6

# Formaty, jakie w ogóle wolno dopuścić dla zadania (twarda lista – walidacja uploadu w T-04).
# ``jpg`` jest zdjęciem rozwiązania pisanego ręcznie (prośba organizatora): uczestnik bez skanera
# fotografuje kartkę telefonem. Kanoniczna nazwa formatu jest jedna – ``jpg`` – a rozszerzenie
# ``.jpeg`` jest do niej sprowadzane przy uploadzie (``submissions.validators``), żeby dozwolone
# formaty zadania, klucz obiektu w storage i nazwa w paczce ZIP nie rozjeżdżały się z powodu
# tego, jak aparat nazwał plik.
SUPPORTED_FILE_FORMATS = ("pdf", "ipynb", "py", "jpg")
#: Rozszerzenia, które przeglądarka ma proponować w oknie wyboru pliku dla danego formatu.
FILE_ACCEPT_EXTENSIONS = {
    "pdf": (".pdf",),
    "ipynb": (".ipynb",),
    "py": (".py",),
    "jpg": (".jpg", ".jpeg"),
}
# Zadanie dodane bez wskazania formatów przyjmuje sam PDF – to nadal domyślna forma rozwiązania,
# a zdjęcie jest wyborem koordynatora, nie stanem wyjściowym.
DEFAULT_ALLOWED_FORMATS = ["pdf"]
DEFAULT_MAX_FILE_MB = 20
# Domyślne okno na jedną recenzję (``Stage.review_deadline_days``). Dwa tygodnie to termin, którym
# organizator posługiwał się nieformalnie, zanim system zaczął go pilnować.
DEFAULT_REVIEW_DEADLINE_DAYS = 14
# Górna granica limitu na zadanie. Powyżej 100 MB plik i tak nie przeszedłby skanu: to
# ``StreamMaxLength`` clamd (``CLAMAV_STREAM_MAX_BYTES``), więc zgłoszenie utknęłoby bez werdyktu.
MAX_FILE_MB_LIMIT = 100


def default_scoring_values() -> list[dict]:
    """Kopia domyślnej skali – ``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return [dict(item) for item in DEFAULT_SCORING_VALUES]


def scoring_allowed_values(values) -> set[int]:
    """Zbiór ocen z listy pozycji skali. Wartości niebędące liczbami całkowitymi są pomijane.

    ``bool`` jest podklasą ``int``, więc ``True``/``False`` odpadają jawnie – inaczej „ocena True”
    przechodziłaby walidację jako jedynka.
    """
    result: set[int] = set()
    for item in values or []:
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            result.add(value)
    return result


def validate_scoring_values(values, max_value, *, values_field: str, max_field: str) -> None:
    """Reguły skali punktowej – wspólne dla skali etapu i dla nadpisania w zadaniu.

    Jedna definicja, bo to jest ta sama skala: zadanie z własnymi wartościami musi spełniać
    dokładnie te same warunki, co etap (0 w zestawie, wartości unikalne i rosnące, maksimum równe
    największej wartości). Dwie kopie tych reguł rozjechałyby się przy pierwszej zmianie, a skutek
    byłby widoczny dopiero przy wystawianiu oceny.

    Nazwy pól są parametrem, żeby komunikat stanął pod właściwym polem formularza – w etapie jest
    to ``values``/``max_value``, w zadaniu ``scoring_values``/``max_points``.
    """
    if not isinstance(values, list) or not values:
        raise ValidationError({values_field: "Skala musi być niepustą listą pozycji {value, label}."})
    numbers: list[int] = []
    for item in values:
        if not isinstance(item, dict) or "value" not in item or "label" not in item:
            raise ValidationError({values_field: "Każda pozycja skali wymaga pól 'value' i 'label'."})
        value = item["value"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValidationError({values_field: "Pole 'value' musi być nieujemną liczbą całkowitą."})
        if not isinstance(item["label"], str) or not item["label"].strip():
            raise ValidationError({values_field: "Pole 'label' musi być niepustym tekstem."})
        numbers.append(value)
    if len(set(numbers)) != len(numbers):
        raise ValidationError({values_field: "Wartości skali muszą być unikalne."})
    if numbers != sorted(numbers):
        raise ValidationError({values_field: "Wartości skali muszą być uporządkowane rosnąco."})
    if 0 not in numbers:
        raise ValidationError({values_field: "Skala musi zawierać wartość 0."})
    if max_value != max(numbers):
        raise ValidationError({max_field: f"{max_field} musi być równe największej wartości skali."})


def default_allowed_formats() -> list[str]:
    return list(DEFAULT_ALLOWED_FORMATS)


#: Powody, dla których rejestracja uczestników jest zamknięta – oraz jedyny powód, dla którego jest
#: otwarta. Kod maszynowy, nie komunikat: treść zdania dobiera warstwa, która je pokazuje
#: (``apps.competitions.registration``), a API oddaje sam kod.
#: Domyślny okres retencji danych osobowych uczestników edycji (miesiące). Decyzja organizatora:
#: dwa lata liczone od ostatniego deadline'u etapu. Tyle wystarcza na rozstrzygnięcie reklamacji,
#: wydanie duplikatu dyplomu i pytanie o kwalifikację do edycji następnej; dłuższe trzymanie
#: danych nie ma już podstawy w art. 5 ust. 1 lit. e RODO.
DEFAULT_RETENTION_MONTHS = 24

REGISTRATION_OPEN = "open"
REGISTRATION_DISABLED = "disabled"
REGISTRATION_NOT_YET = "not_yet"
REGISTRATION_CLOSED = "closed"


@dataclass(frozen=True)
class RegistrationStatus:
    """Stan rejestracji uczestników „na teraz”: czy wolno założyć konto i dlaczego nie.

    Struktura jest zamrożona i bez metod, bo przechodzi przez trzy warstwy naraz (serwis kont,
    kontekst szablonów, serializer API) i każda z nich ma ją tylko **czytać**. Powód (``reason``)
    jest osobno od ``is_open``, bo trzy różne „nie” wymagają trzech różnych zdań i trzech różnych
    zachowań interfejsu: wyłączona rejestracja chowa przycisk, rejestracja przed terminem zamienia
    go w zapowiedź.
    """

    is_open: bool
    reason: str
    opens_at: datetime | None = None
    closes_at: datetime | None = None


class Edition(models.Model):
    """Edycja olimpiady, np. „XV (2026/2027)”. Bieżąca może być tylko jedna."""

    year_label = models.CharField("oznaczenie edycji", max_length=64, unique=True)
    is_current = models.BooleanField("edycja bieżąca", default=False)
    created_at = models.DateTimeField("utworzona", default=timezone.now)
    # Okno rejestracji uczestników jest w ``Edition``, a nie w etapie eliminacyjnym: konto zakłada
    # się **do olimpiady**, a nie do etapu (zapis do etapu jest osobną operacją, patrz
    # ``register_for_stage``). Dopóki tych pól nie było, rejestracja stała otworem od chwili
    # postawienia serwisu – także wtedy, gdy strona główna zapowiadała start na wrzesień.
    #
    # Trzy pola zamiast jednego, bo to trzy różne decyzje: wyłącznik (awaryjny, natychmiastowy),
    # zapowiedziana data otwarcia (uczestnik ma ją zobaczyć na stronie, zanim nadejdzie) i
    # opcjonalne zamknięcie. Puste terminy oznaczają „bez ograniczenia z tej strony”, dzięki czemu
    # edycje sprzed tej zmiany zachowują się dokładnie tak, jak dotąd.
    registration_enabled = models.BooleanField("rejestracja włączona", default=True)
    registration_opens_at = models.DateTimeField("otwarcie rejestracji", null=True, blank=True)
    registration_closes_at = models.DateTimeField("zamknięcie rejestracji", null=True, blank=True)
    # Okres retencji danych osobowych uczestników tej edycji, liczony **od ostatniego deadline'u
    # etapu** (art. 5 ust. 1 lit. e RODO – ograniczenie przechowywania). Po jego upływie konta,
    # które nie wystartowały w żadnej późniejszej edycji, anonimizuje zadanie okresowe
    # (``apps.accounts.retention``): dane osobowe znikają, a pseudonimowy wiersz z kodem
    # publicznym i dokumentacja zawodów zostają.
    #
    # Pole jest w edycji, a nie w ustawieniach serwisu, bo to decyzja o **jednym roczniku**:
    # edycja, wokół której toczy się spór albo postępowanie, zostaje dłużej, a reszta nie czeka
    # na nią bez powodu. Dwa lata są wartością domyślną organizatora – tyle, żeby zmieściło się
    # odwołanie, wydanie duplikatu dyplomu i pytanie o kwalifikację do edycji następnej.
    data_retention_months = models.PositiveSmallIntegerField(
        "retencja danych (miesiące)",
        default=DEFAULT_RETENTION_MONTHS,
        help_text=(
            "Po ilu miesiącach od ostatniego deadline'u etapu anonimizować konta uczestników "
            "tej edycji. Zero wyłącza automatyczną anonimizację."
        ),
    )

    class Meta:
        verbose_name = "edycja"
        verbose_name_plural = "edycje"
        ordering = ("-created_at", "id")
        constraints = [
            # Częściowy indeks unikalny: dopuszcza wiele edycji archiwalnych, dokładnie jedną bieżącą.
            models.UniqueConstraint(
                fields=["is_current"],
                condition=Q(is_current=True),
                name="competitions_edition_single_current",
            ),
            # Okno rejestracji musi mieć dodatnią długość, o ile w ogóle ma oba końce. Bez tego
            # dałoby się zapisać okno, które nigdy nie jest otwarte – a strona pokazywałaby wtedy
            # zapowiedź startu, po którym rejestracja i tak by nie ruszyła.
            models.CheckConstraint(
                condition=Q(registration_opens_at__isnull=True)
                | Q(registration_closes_at__isnull=True)
                | Q(registration_opens_at__lt=F("registration_closes_at")),
                name="competitions_edition_registration_window_ordered",
            ),
        ]

    def __str__(self) -> str:
        return self.year_label

    def clean(self) -> None:
        super().clean()
        if self.is_current:
            others = Edition.objects.filter(is_current=True).exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    {"is_current": "Bieżąca może być tylko jedna edycja. Odznacz poprzednią."}
                )
        # Ta sama reguła, co constraint wyżej – tylko z komunikatem, który da się pokazać pod polem.
        # Błąd jest przypięty do „zamknięcia”, bo to ono jest drugą, dopisywaną datą.
        if (
            self.registration_opens_at is not None
            and self.registration_closes_at is not None
            and self.registration_opens_at >= self.registration_closes_at
        ):
            raise ValidationError(
                {"registration_closes_at": "Zamknięcie rejestracji musi być po jej otwarciu."}
            )

    def registration_status(self, now=None) -> RegistrationStatus:
        """Czy ta edycja przyjmuje **nowe konta uczestników** i dlaczego nie.

        Reguła jest jedna i mieści się w zdaniu: otwarta, gdy włączona i mieści się w oknie, przy
        czym pusty koniec okna oznacza brak ograniczenia. Kolejność sprawdzeń jest istotna –
        wyłącznik ma pierwszeństwo przed terminami, bo jest hamulcem awaryjnym: koordynator, który
        wyłącza rejestrację w trakcie okna, ma zobaczyć „wyłączona”, a nie „otwarta do…”.
        """
        now = now or timezone.now()
        if not self.registration_enabled:
            return RegistrationStatus(
                False, REGISTRATION_DISABLED, self.registration_opens_at, self.registration_closes_at
            )
        if self.registration_opens_at is not None and now < self.registration_opens_at:
            return RegistrationStatus(
                False, REGISTRATION_NOT_YET, self.registration_opens_at, self.registration_closes_at
            )
        if self.registration_closes_at is not None and now >= self.registration_closes_at:
            return RegistrationStatus(
                False, REGISTRATION_CLOSED, self.registration_opens_at, self.registration_closes_at
            )
        return RegistrationStatus(
            True, REGISTRATION_OPEN, self.registration_opens_at, self.registration_closes_at
        )


def current_registration_status(now=None) -> RegistrationStatus:
    """Stan rejestracji **bieżącej** edycji – jedno źródło prawdy dla całego serwisu.

    Brak bieżącej edycji to ``disabled``, a nie „otwarta”: nie ma wtedy czego organizować, więc
    konto założone w takiej chwili nie miałoby do czego należeć (zapis do etapu i tak odmówiłby).
    Zapytanie jest celowo najtańsze z możliwych – wywołuje je procesor kontekstu, czyli każde
    renderowanie szablonu bazowego.
    """
    edition = (
        Edition.objects.filter(is_current=True)
        .only("id", "registration_enabled", "registration_opens_at", "registration_closes_at")
        .first()
    )
    if edition is None:
        return RegistrationStatus(False, REGISTRATION_DISABLED)
    return edition.registration_status(now)


#: Strefa, w której organizator ogłasza wszystkie terminy. Ta sama, co ``settings.TIME_ZONE`` –
#: wpisana tu wprost, bo ``TRAINING_DEADLINE`` jest stałą modułu i liczy się przy imporcie, kiedy
#: ustawienia bywają jeszcze nieskonfigurowane (np. w skryptach pomocniczych).
WARSAW = ZoneInfo("Europe/Warsaw")

#: „Bez terminu” dla etapu treningowego. Etap treningowy ma być otwarty bez końca, ale osi czasu
#: etapu nie da się zostawić puste: ``opens_at < deadline_at <= review_deadline_at <=
#: appeal_window_opens_at < appeal_window_closes_at`` jest jednocześnie ``clean()`` i pięcioma
#: constraintami w bazie. Data-wartownik spełnia więc każdy z tych warunków, a interfejs jej nie
#: pokazuje: pyta o ``Stage.has_deadline`` i pisze „bez terminu” (rok 2099 na stronie byłby
#: informacją fałszywą, bo nikt nie zamierza przyjmować prac przez siedemdziesiąt lat).
TRAINING_DEADLINE = datetime(2099, 12, 31, 23, 59, tzinfo=WARSAW)


class StageKind(models.TextChoices):
    """Które to zawody w kolejności edycji – oraz jeden rodzaj, który zawodami nie jest.

    ``TRAINING`` to **piaskownica**: etap treningowy służy do przejścia całej ścieżki (zgłoszenie →
    upload → dwie recenzje ślepe → konsensus/moderacja → wyniki) na prawdziwych zadaniach, poza
    zawodami. Dlatego:

    - jest **otwarty bez końca** (``TRAINING_DEADLINE``) i nigdy nie zamyka się sam,
    - **nie bierze udziału w kwalifikacji**: nie ma następnego etapu i nie jest następnym etapem
      dla nikogo (``apps.results.services.STAGE_ORDER``),
    - **nie ma go na publicznej osi czasu** ani w odliczaniu na stronie głównej („etap bieżący” to
      zawsze etap zawodów, patrz ``services.current_stage``),
    - wyniki wolno w nim przeliczyć i ogłosić, ale tabela jest podpisana odznaką „trening”, żeby
      nikt nie wziął jej za wynik zawodów.

    Ograniczenie „jeden na edycję” wychodzi z istniejącego unikalnego (edycja, rodzaj) – nie ma
    potrzeby drugiej reguły.
    """

    ELIM = "ELIM", "Eliminacje"
    DISTRICT = "DISTRICT", "Wojewódzki"
    FINAL = "FINAL", "Finał"
    TRAINING = "TRAINING", "Trening"


class StageFormat(models.TextChoices):
    """Jak etap się odbywa – i czym w związku z tym jest „udział” w nim.

    Rodzaj etapu (``StageKind``) mówi, **które** to zawody w kolejności; forma mówi, **co** w nich
    robi uczestnik. Rozdział jest potrzebny, bo obie osie zmieniają się niezależnie: regulamin
    I edycji przewiduje etap okręgowy jako rozmowę kwalifikacyjną online, ale nic nie stoi na
    przeszkodzie, żeby w kolejnej edycji był to znowu arkusz zadań.
    """

    SUBMISSIONS = "SUBMISSIONS", "rozwiązania pisemne"
    INTERVIEW = "INTERVIEW", "rozmowa kwalifikacyjna online"
    # Test online sprawdzany automatycznie (``apps.quiz``). Trzecia forma, a nie odmiana etapu
    # pisemnego, bo różni się dokładnie tym, czym różnią się od siebie dwie pozostałe: tym, co
    # uczestnik robi i skąd biorą się jego punkty. W etapie pisemnym punkty wystawia recenzent,
    # w rozmowie – komisja, a tutaj nie ma ich kto wystawić: liczy je serwer w chwili zakończenia
    # podejścia. Dlatego etap w tej formie nie ma zadań do oddania ani przydziałów recenzenckich,
    # a jego suma punktów wchodzi do wyników inną drogą – patrz ``apps.quiz.services.stage_scores``.
    QUIZ = "QUIZ", "test online sprawdzany automatycznie"


class Stage(models.Model):
    """Etap edycji wraz z całą osią czasu: otwarcie, deadline (+grace), recenzje, okno reklamacji."""

    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="stages")
    kind = models.CharField("rodzaj", max_length=16, choices=StageKind.choices)
    # Własna nazwa etapu. Rodzaj (``kind``) jest tożsamością techniczną i porządkiem w edycji –
    # zmiana byłaby podmianą obiektu. Nazwa jest natomiast tym, co czyta uczestnik, a organizator
    # bywa przy niej dokładniejszy niż słownik trzech rodzajów: „Etap II – rozmowy kwalifikacyjne”
    # zamiast „Okręgowy”. Puste pole oznacza nazwę domyślną (patrz ``display_name``), więc etap
    # nigdy nie zostaje bez podpisu.
    name = models.CharField("nazwa", max_length=80, blank=True)
    # Forma etapu decyduje o tym, co uczestnik w nim robi: oddaje pliki albo zapisuje się na
    # rozmowę. Pole jest w ``Stage``, a nie osobną tabelą, bo to jedna wartość na etap i czytają
    # ją wszystkie ekrany osi czasu razem z terminami.
    format = models.CharField(
        "forma", max_length=16, choices=StageFormat.choices, default=StageFormat.SUBMISSIONS
    )
    # Miejsce zawodów – puste dla etapów zdalnych, „Kraków” dla finału stacjonarnego. Pole jest tu,
    # a nie w treści redakcyjnej, bo miejsce jest częścią tej samej informacji, co termin: uczestnik
    # planuje dojazd w tej samej chwili, w której czyta datę, a strona główna i harmonogram czytają
    # oba z jednego źródła. Wolny tekst, nie słownik miast: „Kraków, Wydział Fizyki UJ” i „online”
    # to ta sama rubryka, a systemowi nie jest do niczego potrzebna postać ustrukturyzowana.
    location = models.CharField("miejsce", max_length=120, blank=True)
    opens_at = models.DateTimeField("otwarcie")
    deadline_at = models.DateTimeField("deadline oddania")
    grace_seconds = models.PositiveIntegerField("tolerancja po deadline (s)", default=0)
    # Dni wydarzenia – i to jest **inny fakt** niż okno oddawania prac powyżej. Etap stacjonarny
    # ma jedno i drugie naraz: finał trwa 4–7 czerwca 2027 (uczestnik przyjeżdża na cztery dni),
    # a sesja egzaminacyjna, w której system przyjmuje pliki, to kilka godzin 5 czerwca. Dopóki
    # była jedna para dat, strona musiała skłamać w którąś stronę: albo ogłaszała „4–7 czerwca”
    # i wtedy upload stał otwarty przez cztery dni, albo pilnowała sesji i wtedy na stronie stało
    # „5 czerwca”, choć organizator zaprasza na cztery. Dlatego:
    #
    # - ``event_starts_on`` / ``event_ends_on`` to **dni pobytu**, czyli to, co czyta publiczność
    #   (oś czasu na stronie głównej i ``/harmonogram/``). Same daty, bez godzin: nikt nie ogłasza
    #   zjazdu z dokładnością do minuty, a godzina rozpoczęcia bywa w programie, nie w terminie,
    # - ``opens_at`` / ``deadline_at`` zostają tym, czym były: **oknem, które egzekwuje serwer**
    #   (upload, zamknięcie etapu, ``LOCKED``). Żadna z tych dat niczego w nim nie zmienia.
    #
    # Oba pola są opcjonalne, ale **tylko razem** (patrz ``clean()`` i ``event_range``): sam
    # początek bez końca nie jest terminem, który dałoby się ogłosić. Etapy zdalne zostawiają je
    # puste i wtedy publiczna oś czasu zachowuje się dokładnie jak dotąd.
    event_starts_on = models.DateField("początek wydarzenia", null=True, blank=True)
    event_ends_on = models.DateField("koniec wydarzenia", null=True, blank=True)
    review_deadline_at = models.DateTimeField("deadline recenzji")
    # Ile dni ma recenzent **od chwili przydziału** na oddanie jednej recenzji. To co innego niż
    # ``review_deadline_at``: tamto jest terminem całego etapu (do kiedy komplet ocen ma być
    # gotowy), a to jest terminem osobistym, liczonym każdemu od dnia, w którym dostał pracę.
    # Prace przydzielane są falami (zamknięcie etapu, potem dosyłki i zastępstwa), więc jeden
    # termin dla wszystkich dawałby ostatnim recenzentom dwa dni, a pierwszym trzy tygodnie.
    # Wynik wyliczenia ląduje w ``Review.due_at`` – reguła stoi w ``apps.grading.deadlines``.
    review_deadline_days = models.PositiveSmallIntegerField(
        "dni na recenzję", default=DEFAULT_REVIEW_DEADLINE_DAYS
    )
    appeal_window_opens_at = models.DateTimeField("otwarcie okna reklamacji")
    appeal_window_closes_at = models.DateTimeField("zamknięcie okna reklamacji")
    results_published_at = models.DateTimeField("wyniki opublikowane", null=True, blank=True)
    # Ustawiany przez beat (``apps.submissions.tasks.close_due_stages``) po ``submission_deadline``.
    # Znacznik pełni też rolę bezpiecznika idempotencji: etap zamykamy dokładnie raz.
    closed_at = models.DateTimeField("etap zamknięty", null=True, blank=True)
    # Dostawca pokoju wideo dla etapu w formie rozmowy. ``none`` zachowuje się dokładnie tak, jak
    # przed wprowadzeniem pola: link wpisuje koordynator przy terminie albo nie ma go wcale.
    # Reguła budowania adresu i cała reszta uzasadnienia: ``apps.competitions.video``.
    video_provider = models.CharField(
        "dostawca wideo", max_length=16, choices=VideoProvider.choices, default=VideoProvider.NONE
    )
    # Korzeń adresu pokoju. Domyślnie publiczna instancja Jitsi; własna instytucjonalna instancja
    # wpisuje się tutaj razem z ``video_provider = custom``. Pole jest w etapie, a nie w ustawieniach
    # serwisu, bo to etap decyduje o formie zawodów – finał potrafi mieć inny kanał niż okręgowy.
    video_base_url = models.URLField(
        "adres serwera wideo", max_length=200, blank=True, default=DEFAULT_VIDEO_BASE_URL
    )

    class Meta:
        verbose_name = "etap"
        verbose_name_plural = "etapy"
        ordering = ("edition", "opens_at", "id")
        constraints = [
            models.UniqueConstraint(fields=["edition", "kind"], name="competitions_stage_unique_kind"),
            models.CheckConstraint(
                condition=Q(opens_at__lt=F("deadline_at")),
                name="competitions_stage_opens_before_deadline",
            ),
            models.CheckConstraint(
                condition=Q(deadline_at__lte=F("review_deadline_at")),
                name="competitions_stage_deadline_before_review",
            ),
            models.CheckConstraint(
                condition=Q(review_deadline_at__lte=F("appeal_window_opens_at")),
                name="competitions_stage_review_before_appeal",
            ),
            models.CheckConstraint(
                condition=Q(appeal_window_opens_at__lt=F("appeal_window_closes_at")),
                name="competitions_stage_appeal_window_ordered",
            ),
            # Zakres jednodniowy jest dozwolony (zjazd na jeden dzień), odwrócony – nie. Przy
            # pustych datach porównanie daje NULL, więc constraint przepuszcza etap zdalny; parę
            # „tylko jedna z dwóch” odrzuca ``clean()``, bo to reguła o kompletności ogłoszenia,
            # a nie o kolejności dni.
            models.CheckConstraint(
                condition=Q(event_starts_on__lte=F("event_ends_on")),
                name="competitions_stage_event_dates_ordered",
            ),
            # Zero dni na recenzję znaczyłoby „termin minął w chwili przydziału”, czyli każda nowa
            # recenzja rodziłaby się po terminie i natychmiast szłaby do przypominajki.
            models.CheckConstraint(
                condition=Q(review_deadline_days__gte=1),
                name="competitions_stage_review_days_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.edition.year_label} – {self.display_name}"

    @property
    def display_name(self) -> str:
        """Podpis etapu na każdym ekranie: własna nazwa albo etykieta rodzaju.

        Jedno miejsce, w którym rozstrzyga się „czym podpisać etap” – szablony i serwisy wołają
        tę właściwość zamiast ``get_kind_display()``, więc zmiana nazwy w panelu jest widoczna
        wszędzie naraz i nigdzie nie zostaje stara etykieta rodzaju.
        """
        return self.name or self.get_kind_display()

    @property
    def is_interview(self) -> bool:
        """Czy etap jest rozmową kwalifikacyjną online (brak uploadu, zapisy na terminy)."""
        return self.format == StageFormat.INTERVIEW

    @property
    def is_quiz(self) -> bool:
        """Czy etap jest testem online sprawdzanym automatycznie (bez uploadu, bez recenzji).

        Pytanie zadawane w kilku warstwach naraz (panel uczestnika, menu koordynatora,
        przeliczenie wyników), więc stoi obok ``is_interview`` i z tego samego powodu: porównanie
        z literałem rozsiane po szablonach przeżyłoby zmianę nazwy formy, a ta właściwość nie.
        """
        return self.format == StageFormat.QUIZ

    @property
    def is_training(self) -> bool:
        """Czy etap jest piaskownicą treningową (poza zawodami, bez terminu) – patrz ``StageKind``."""
        return self.kind == StageKind.TRAINING

    @property
    def has_deadline(self) -> bool:
        """Czy etap ma termin, który wolno ogłosić.

        Jedno pytanie dla wszystkich ekranów pokazujących datę oddania: etap treningowy ma
        w bazie ``TRAINING_DEADLINE`` (rok 2099), bo inaczej nie przeszedłby walidacji osi czasu,
        ale ta data nie jest terminem – jest jego brakiem. Szablony pytają **tutaj**, a nie
        o ``kind``, więc dodanie kiedyś drugiego etapu „bez terminu” nie wymaga obchodzenia
        wszystkich widoków.
        """
        return not self.is_training

    def clean(self) -> None:
        super().clean()
        # Pominięte pola dają None; wtedy walidację kolejności robi walidacja pól, nie ta metoda.
        chain = (
            ("deadline_at", self.opens_at, self.deadline_at, False, "opens_at musi być przed deadline_at."),
            (
                "review_deadline_at",
                self.deadline_at,
                self.review_deadline_at,
                True,
                "review_deadline_at nie może być wcześniej niż deadline_at.",
            ),
            (
                "appeal_window_opens_at",
                self.review_deadline_at,
                self.appeal_window_opens_at,
                True,
                "appeal_window_opens_at nie może być wcześniej niż review_deadline_at.",
            ),
            (
                "appeal_window_closes_at",
                self.appeal_window_opens_at,
                self.appeal_window_closes_at,
                False,
                "appeal_window_closes_at musi być po appeal_window_opens_at.",
            ),
        )
        errors: dict[str, str] = {}
        for field, earlier, later, allow_equal, message in chain:
            if earlier is None or later is None:
                continue
            if later < earlier or (later == earlier and not allow_equal):
                errors[field] = message
        # Termin wydarzenia jest jedną informacją zapisaną w dwóch polach, więc walidacja pilnuje
        # obu naraz: połowa zakresu nie jest terminem, który dałoby się ogłosić na stronie.
        if self.event_starts_on and not self.event_ends_on:
            errors["event_ends_on"] = "Podaj też koniec wydarzenia albo wyczyść oba pola."
        if self.event_ends_on and not self.event_starts_on:
            errors["event_starts_on"] = "Podaj też początek wydarzenia albo wyczyść oba pola."
        if self.event_starts_on and self.event_ends_on and self.event_ends_on < self.event_starts_on:
            errors["event_ends_on"] = "Koniec wydarzenia nie może być przed jego początkiem."
        if errors:
            raise ValidationError(errors)

    @property
    def event_range(self) -> tuple[date, date] | None:
        """Dni wydarzenia jako para ``(początek, koniec)`` albo ``None``, gdy etap ich nie ma.

        Jedno wejście dla wszystkich czytających (oś czasu, panel, pulpit uczestnika): nikt nie
        sprawdza dwóch pól osobno, więc nigdzie nie powstanie ekran pokazujący pół zakresu.
        """
        if self.event_starts_on and self.event_ends_on:
            return (self.event_starts_on, self.event_ends_on)
        return None

    @property
    def submission_deadline(self):
        """Twardy moment zamknięcia uploadu: ``deadline_at`` powiększony o tolerancję."""
        return self.deadline_at + timedelta(seconds=self.grace_seconds or 0)

    def is_open_for_submissions(self, now=None) -> bool:
        """Czy etap przyjmuje rozwiązania. Zawsze liczone po stronie serwera, w UTC."""
        now = now or timezone.now()
        return self.opens_at <= now < self.submission_deadline

    def is_appeal_window_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.appeal_window_opens_at <= now < self.appeal_window_closes_at

    def has_opened(self, now=None) -> bool:
        """Czy etap już się rozpoczął (treści zadań stają się jawne dopiero wtedy)."""
        now = now or timezone.now()
        return self.opens_at <= now


class ScoringScale(models.Model):
    """Parametryzacja skali punktowej etapu (domyślnie 0/2/5/6)."""

    stage = models.OneToOneField(Stage, on_delete=models.CASCADE, related_name="scoring_scale")
    values = models.JSONField("wartości skali", default=default_scoring_values)
    max_value = models.PositiveSmallIntegerField("maksimum", default=DEFAULT_MAX_VALUE)

    class Meta:
        verbose_name = "skala punktacji"
        verbose_name_plural = "skale punktacji"

    def __str__(self) -> str:
        return f"skala {sorted(self.allowed_values())} dla {self.stage_id}"

    def allowed_values(self) -> set[int]:
        """Zbiór dopuszczalnych ocen. Używany przy walidacji ``Review.score`` (T-05)."""
        return scoring_allowed_values(self.values)

    def clean(self) -> None:
        super().clean()
        validate_scoring_values(self.values, self.max_value, values_field="values", max_field="max_value")


class QualificationMode(models.TextChoices):
    MIN_POINTS = "MIN_POINTS", "min. punktów"
    TOP_N = "TOP_N", "najlepszych N"
    TOP_N_PER_DISTRICT = "TOP_N_PER_DISTRICT", "N na województwo"
    HYBRID = "HYBRID", "min. punktów ORAZ top N"


class QualificationRule(models.Model):
    """Próg kwalifikacji do następnego etapu. Przeliczenie robi T-07, tutaj tylko parametry."""

    stage = models.OneToOneField(Stage, on_delete=models.CASCADE, related_name="qualification_rule")
    mode = models.CharField(
        "tryb", max_length=32, choices=QualificationMode.choices, default=QualificationMode.MIN_POINTS
    )
    min_points = models.PositiveIntegerField("minimum punktów", null=True, blank=True)
    top_n = models.PositiveIntegerField("liczba kwalifikowanych", null=True, blank=True)

    class Meta:
        verbose_name = "próg kwalifikacji"
        verbose_name_plural = "progi kwalifikacji"

    def __str__(self) -> str:
        return f"{self.get_mode_display()} (min={self.min_points}, top={self.top_n})"

    @property
    def requires_min_points(self) -> bool:
        return self.mode in (QualificationMode.MIN_POINTS, QualificationMode.HYBRID)

    @property
    def requires_top_n(self) -> bool:
        return self.mode in (
            QualificationMode.TOP_N,
            QualificationMode.TOP_N_PER_DISTRICT,
            QualificationMode.HYBRID,
        )

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.requires_min_points and self.min_points is None:
            errors["min_points"] = f"Tryb {self.mode} wymaga podania min_points."
        if self.requires_top_n and self.top_n is None:
            errors["top_n"] = f"Tryb {self.mode} wymaga podania top_n."
        if self.requires_top_n and self.top_n is not None and self.top_n < 1:
            errors["top_n"] = "top_n musi być dodatnie."
        if errors:
            raise ValidationError(errors)


class Problem(models.Model):
    """Zadanie etapu. Treść PDF jest jawna dopiero po ``stage.opens_at`` (patrz serializery)."""

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="problems")
    number = models.PositiveSmallIntegerField("numer")
    title = models.CharField("tytuł", max_length=300)
    # Wersja angielska tytułu. Puste znaczy „nie ma” i wtedy angielski interfejs pokazuje tytuł
    # polski – zadanie bez tłumaczenia ma być czytelne, a nie puste. Treść zadania jest **treścią
    # merytoryczną**, a nie napisem interfejsu, więc nie idzie przez gettext: tłumaczy ją komitet
    # w panelu, a nie tłumacz przy wydaniu aplikacji.
    title_en = models.CharField("tytuł (EN)", max_length=300, blank=True)
    # Storage jawnie prywatny (patrz apps.competitions.storage): ``default`` jest w produkcji
    # publicznym bucketem Wagtaila, a treść zadania przed ``opens_at`` musi być nieosiągalna.
    statement_pdf = models.FileField(
        "treść (PDF)",
        upload_to="problems/statements/",
        blank=True,
        storage=private_media_storage,
    )
    # Angielska wersja treści. Ten sam prywatny storage i ta sama bramka czasowa (``opens_at``) –
    # to jest ten sam dokument w drugim języku, a nie materiał o innej wrażliwości. Brak pliku
    # znaczy „nie ma tłumaczenia” i wtedy również angielski interfejs wydaje wersję polską: lepszy
    # arkusz po polsku niż komunikat „treść niedostępna” w trakcie zawodów.
    statement_pdf_en = models.FileField(
        "treść (PDF, EN)",
        upload_to="problems/statements-en/",
        blank=True,
        storage=private_media_storage,
    )
    # Rozwiązanie wzorcowe i klucz odpowiedzi – materiał **wyłącznie** dla oceniających. Ten sam
    # prywatny storage, co treść zadania, ale wydawany zupełnie inną drogą: treść staje się jawna
    # po ``opens_at`` (``ProblemStatementView``), a wzorcówka nie staje się jawna nigdy –
    # pobiera ją tylko aktywny członek komitetu albo koordynator
    # (``apps.web.views.reviewer_tools.ProblemModelSolutionView``). Gdyby leżała na tym samym
    # polu co treść, jedna pomyłka w warunku widoczności rozdawałaby uczestnikom klucz w trakcie
    # zawodów.
    model_solution_pdf = models.FileField(
        "rozwiązanie wzorcowe (PDF)",
        upload_to="problems/model-solutions/",
        blank=True,
        storage=private_media_storage,
    )
    # Uwagi dla recenzentów: czego nie widać we wzorcówce, a rozstrzyga o punktach („uznajemy
    # dowód przez indukcję bez podstawy, jeśli…”). Tekst, a nie plik, bo to kilka zdań, które
    # koordynator poprawia w trakcie oceniania – i wtedy zmiana ma być widoczna od razu, bez
    # wgrywania nowej wersji PDF-a.
    reviewer_notes = models.TextField("uwagi dla recenzentów", blank=True)
    allowed_formats = models.JSONField("dozwolone formaty", default=default_allowed_formats)
    max_file_mb = models.PositiveSmallIntegerField("limit rozmiaru pliku (MB)", default=DEFAULT_MAX_FILE_MB)
    # Nadpisanie skali etapu dla tego jednego zadania. ``null`` znaczy „dziedzicz po etapie”, a nie
    # „brak skali”: organizator punktuje zwykle wszystkie zadania tak samo, a wyjątkiem bywa jedno
    # (np. zadanie otwarte 0–10 obok zadań 0/2/5/6). Domyślnej wartości tu **nie ma** świadomie –
    # kopia skali etapu w każdym zadaniu zamroziłaby ją w chwili dodania zadania i zmiana skali
    # etapu przestałaby cokolwiek znaczyć.
    scoring_values = models.JSONField("skala punktacji zadania", null=True, blank=True)
    max_points = models.PositiveSmallIntegerField("maksimum punktów", null=True, blank=True)

    class Meta:
        verbose_name = "zadanie"
        verbose_name_plural = "zadania"
        ordering = ("stage", "number")
        constraints = [
            models.UniqueConstraint(fields=["stage", "number"], name="competitions_problem_unique_number"),
            models.CheckConstraint(condition=Q(number__gte=1), name="competitions_problem_number_positive"),
            models.CheckConstraint(
                condition=Q(max_file_mb__gte=1), name="competitions_problem_max_file_mb_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_file_mb__lte=MAX_FILE_MB_LIMIT),
                name="competitions_problem_max_file_mb_under_limit",
            ),
        ]

    def __str__(self) -> str:
        return f"Zadanie {self.number}: {self.title}"

    @property
    def accept_attribute(self) -> str:
        """Wartość atrybutu ``accept`` dla pola wyboru pliku, np. ``.pdf,.jpg,.jpeg``.

        Atrybut jest **uprzejmością**, a nie zabezpieczeniem – filtruje okno wyboru pliku, a nie
        żądanie. O przyjęciu pliku decyduje ``submissions.validators.validate_upload``, która patrzy
        na treść. Lista jest tu, a nie w szablonie, bo zdjęcie ma dwa rozszerzenia (``.jpg``
        i ``.jpeg``) i rozwijanie tego w szablonie kończyłoby się drugą definicją tej zależności.
        """
        extensions: list[str] = []
        for item in self.allowed_formats or []:
            extensions.extend(FILE_ACCEPT_EXTENSIONS.get(item, ()))
        return ",".join(extensions)

    @property
    def display_title(self) -> str:
        """Tytuł w języku, który uczestnik ma właśnie włączony – z odwrotem na polski.

        Jedno miejsce dla szablonów, eksportów i listów, żeby „a w tym widoku pokazuje się po
        polsku” nie było pytaniem do zadania trzy razy. Odwrót jest świadomy: zadanie bez
        tłumaczenia ma być czytelne, a nie puste.
        """
        from django.utils.translation import get_language

        if (get_language() or "").split("-")[0] == "en" and self.title_en:
            return self.title_en
        return self.title

    @property
    def statement_file(self):
        """Plik treści dla aktywnego języka – angielski, gdy jest, w przeciwnym razie polski.

        Zwraca obiekt ``FieldFile`` (a nie ścieżkę), bo wołający wydaje go przez widok, tak samo
        jak dotąd. Bramka czasowa (``stage.opens_at``) nie zmienia się ani na jotę: to ten sam
        dokument w drugim języku, nie materiał o innej wrażliwości.
        """
        from django.utils.translation import get_language

        if (get_language() or "").split("-")[0] == "en" and self.statement_pdf_en:
            return self.statement_pdf_en
        return self.statement_pdf

    @property
    def has_own_scale(self) -> bool:
        """Czy zadanie ma własną skalę. Puste nadpisanie znaczy „dziedzicz po etapie”."""
        return bool(self.scoring_values)

    def allowed_values(self) -> set[int]:
        """Oceny dopuszczalne dla **tego zadania** albo pusty zbiór, gdy skala jest etapowa.

        Pusty zbiór jest tu odpowiedzią „pytaj etapu”, a nie „nic nie wolno” – rozstrzyga o tym
        ``grading.services.allowed_scores``, jedyne miejsce, w którym stoi reguła pierwszeństwa.
        """
        return scoring_allowed_values(self.scoring_values)

    def clean(self) -> None:
        super().clean()
        formats = self.allowed_formats
        if not isinstance(formats, list) or not formats:
            raise ValidationError({"allowed_formats": "Podaj co najmniej jeden dozwolony format."})
        unknown = [item for item in formats if item not in SUPPORTED_FILE_FORMATS]
        if unknown:
            raise ValidationError(
                {"allowed_formats": f"Niedozwolone formaty: {', '.join(map(str, unknown))}."}
            )
        if len(set(formats)) != len(formats):
            raise ValidationError({"allowed_formats": "Formaty nie mogą się powtarzać."})
        if self.max_file_mb is not None and not (1 <= self.max_file_mb <= MAX_FILE_MB_LIMIT):
            raise ValidationError(
                {"max_file_mb": f"Limit rozmiaru musi mieścić się w 1–{MAX_FILE_MB_LIMIT} MB."}
            )
        # Skala i jej maksimum są jedną informacją zapisaną w dwóch polach – tak samo jak w etapie.
        # Puste **oba** znaczą „dziedzicz po etapie”; wypełnione jedno byłoby nadpisaniem bez treści.
        if not self.scoring_values and self.max_points is None:
            return
        if not self.scoring_values:
            raise ValidationError(
                {"scoring_values": "Podaj wartości skali zadania albo wyczyść maksimum punktów."}
            )
        validate_scoring_values(
            self.scoring_values,
            self.max_points,
            values_field="scoring_values",
            max_field="max_points",
        )


class StageEntryStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "zarejestrowany"
    QUALIFIED = "QUALIFIED", "zakwalifikowany"
    NOT_QUALIFIED = "NOT_QUALIFIED", "niezakwalifikowany"
    DISQUALIFIED = "DISQUALIFIED", "zdyskwalifikowany"


class ManualQualification(models.TextChoices):
    """Decyzja komitetu o kwalifikacji **wbrew regule punktowej** – albo jej brak.

    Wartość pusta (``NONE``) znaczy „rozstrzyga próg”, a nie „komitet odmówił”: to stan domyślny
    każdego wpisu i dlatego jest zwykłym pustym napisem, a nie osobnym kodem. Dzięki temu pytanie
    „czy ktoś tu ingerował” sprowadza się do sprawdzenia, czy pole jest niepuste.

    Po co w ogóle: regulamin przewiduje sytuacje, których próg punktowy nie opisuje – awaria
    łącza w trakcie rozmowy kwalifikacyjnej, praca oddana poza systemem na wyraźne polecenie
    organizatora, dyskwalifikacja za naruszenie zasad mimo wysokiego wyniku. Bez tego pola
    jedyną drogą byłoby majstrowanie przy punktach, czyli zapisanie w protokole nieprawdy.
    """

    NONE = "", "bez decyzji komitetu"
    QUALIFIED = "QUALIFIED", "zakwalifikowany decyzją komitetu"
    NOT_QUALIFIED = "NOT_QUALIFIED", "niezakwalifikowany decyzją komitetu"


#: Minimalna długość uzasadnienia decyzji ręcznej. Decyzja wbrew regule jest wyjątkiem, który
#: ktoś kiedyś będzie musiał wytłumaczyć uczestnikowi albo organowi odwoławczemu – „ok” w polu
#: uzasadnienia nie jest wtedy żadną odpowiedzią.
MIN_MANUAL_QUALIFICATION_REASON = 10


class StageEntryQuerySet(models.QuerySet):
    def for_user(self, user):
        """Filtr per rola w jednym miejscu (PROJEKT.md 2.3): uczestnik widzi wyłącznie swoje wpisy."""
        if not user or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.groups.filter(name=GROUP_COORDINATOR).exists():
            return self
        participant = getattr(user, "participant", None)
        if participant is None:
            return self.none()
        return self.filter(participant=participant)


class StageEntry(models.Model):
    """Udział uczestnika w etapie. Dla ELIM powstaje przez rejestrację, dalej przez kwalifikację (T-07)."""

    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="stage_entries")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="entries")
    status = models.CharField(
        "status", max_length=16, choices=StageEntryStatus.choices, default=StageEntryStatus.REGISTERED
    )
    total_points = models.PositiveIntegerField("suma punktów", null=True, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    # Kwalifikacja ręczna: decyzja komitetu, która **wygrywa z progiem punktowym** przy każdym
    # przeliczeniu wyników (patrz ``apps.results.services``). Pole jest przy wpisie, a nie przy
    # uczestniku, bo dotyczy jednego etapu – ktoś dopuszczony wyjątkowo do etapu wojewódzkiego
    # nie ma przez to żadnych praw w finale.
    manual_qualification = models.CharField(
        "kwalifikacja ręczna",
        max_length=16,
        choices=ManualQualification.choices,
        blank=True,
        default=ManualQualification.NONE,
    )
    manual_qualification_reason = models.TextField("uzasadnienie decyzji", blank=True)
    # Kto i kiedy. ``SET_NULL``, bo skasowanie konta koordynatora nie może wymazać tego, że
    # decyzja w ogóle zapadła – bez autora zostaje data i uzasadnienie, czyli nadal dokument.
    manual_qualified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_qualifications",
        verbose_name="decyzję podjął",
    )
    manual_qualified_at = models.DateTimeField("decyzja z dnia", null=True, blank=True)

    objects = StageEntryQuerySet.as_manager()

    class Meta:
        verbose_name = "wpis do etapu"
        verbose_name_plural = "wpisy do etapów"
        ordering = ("stage", "participant")
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "stage"], name="competitions_stageentry_unique_participant"
            ),
            # Decyzja bez uzasadnienia nie jest decyzją, tylko przestawionym polem. Ostatnia linia
            # obrony przed zapisem z pominięciem serwisu (``apps.results.manual``): pusta decyzja
            # przechodzi, każda inna wymaga niepustego tekstu.
            models.CheckConstraint(
                condition=Q(manual_qualification="") | ~Q(manual_qualification_reason=""),
                name="competitions_stageentry_manual_reason_required",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.participant.public_code} @ {self.stage_id} ({self.status})"

    @property
    def has_manual_qualification(self) -> bool:
        """Czy o tym wpisie rozstrzygnęła decyzja komitetu, a nie próg punktowy.

        Jedno pytanie dla wszystkich czytelników (przeliczenie wyników, tabela publiczna, panel),
        żeby nigdzie nie powstało porównanie z literałem – pusta wartość znaczy „rozstrzyga próg”
        i to jest jedyne miejsce, w którym ta reguła jest zapisana.
        """
        return self.manual_qualification != ManualQualification.NONE


class InterviewSlot(models.Model):
    """Termin rozmowy kwalifikacyjnej wyznaczony przez koordynatora.

    Terminy są **zasobem etapu**, a nie kalendarzem jednej komisji: kilka komisji może rozmawiać
    równolegle, więc dwa sloty o tych samych godzinach są w porządku i model ich nie zabrania.
    Rozróżnia je ``note`` („komisja A”) i ``capacity``.

    ``meeting_url`` jest polem etapu, a nie uczestnika: link do pokoju wideo jest wspólny dla
    całego slotu. Widzi go wyłącznie osoba zapisana na ten termin (patrz szablon panelu
    uczestnika) – adres pokoju, do którego wchodzi się bez logowania, jest de facto poświadczeniem.
    """

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="interview_slots")
    starts_at = models.DateTimeField("początek")
    ends_at = models.DateTimeField("koniec")
    capacity = models.PositiveSmallIntegerField("liczba miejsc", default=1)
    meeting_url = models.URLField("link do rozmowy", blank=True, max_length=500)
    note = models.CharField("oznaczenie", max_length=200, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    class Meta:
        verbose_name = "termin rozmowy"
        verbose_name_plural = "terminy rozmów"
        ordering = ("stage", "starts_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")),
                name="competitions_interviewslot_starts_before_ends",
            ),
            models.CheckConstraint(
                condition=Q(capacity__gte=1), name="competitions_interviewslot_capacity_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.starts_at.isoformat()} – {self.ends_at.isoformat()} ({self.capacity} miejsc)"

    def clean(self) -> None:
        """Te same reguły, co constrainty – tylko z komunikatem, który da się pokazać w formularzu."""
        super().clean()
        errors: dict[str, str] = {}
        if self.starts_at is not None and self.ends_at is not None and self.starts_at >= self.ends_at:
            errors["ends_at"] = "Koniec terminu musi być po jego początku."
        if self.capacity is not None and self.capacity < 1:
            errors["capacity"] = "Termin musi mieć co najmniej jedno miejsce."
        if errors:
            raise ValidationError(errors)


class InterviewBooking(models.Model):
    """Zapis uczestnika na jeden termin rozmowy.

    ``entry`` jest relacją jeden-do-jednego: uczestnik ma w etapie dokładnie jeden termin, a zmiana
    terminu jest przeniesieniem zapisu, nie drugim zapisem (patrz ``interviews.book_slot``). Dzięki
    temu „ilu ludzi przyjdzie na rozmowę” liczy się z bazy, a nie z domysłu.

    ``slot`` jest z ``PROTECT``: skasowanie terminu, na który ktoś się zapisał, zabrałoby ze sobą
    zapis i zostawiło uczestnika bez rozmowy, o czym nikt by się nie dowiedział. Usunięcie terminu
    z zapisami odmawia (``interviews.delete_slot``).
    """

    slot = models.ForeignKey(InterviewSlot, on_delete=models.PROTECT, related_name="bookings")
    entry = models.OneToOneField(StageEntry, on_delete=models.CASCADE, related_name="interview_booking")
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    # Adres pokoju wideo wyznaczony w chwili zapisu (``apps.competitions.video``). Kopia, a nie
    # odczyt z terminu, i to jest celowe: uczestnik ma widzieć **ten** adres, który dostał w liście,
    # także wtedy, gdy koordynator zmienił później pole przy terminie. Pusty dla etapów bez wideo.
    meeting_url = models.URLField("link do rozmowy", blank=True, max_length=500)
    # Kiedy poszło przypomnienie o jutrzejszej rozmowie. Jedyny bezpiecznik przed drugim listem –
    # patrz ``apps.competitions.tasks.remind_interviews``.
    reminder_sent_at = models.DateTimeField("przypomnienie wysłane", null=True, blank=True)

    class Meta:
        verbose_name = "zapis na rozmowę"
        verbose_name_plural = "zapisy na rozmowy"
        ordering = ("slot", "created_at", "id")

    def __str__(self) -> str:
        return f"{self.entry_id} → {self.slot_id}"


class EditionEvent(models.Model):
    """Wydarzenie edycji dopisywane przez koordynatora – wszystko, czego nie egzekwuje serwer.

    Etapy mają w bazie terminy, bo system ich **pilnuje**: po ``deadline_at`` zamyka upload.
    Konferencja prasowa, gala, obóz naukowy czy dzień otwarty niczego w systemie nie otwierają
    ani nie zamykają – a mimo to są częścią kalendarza, który uczestnik czyta w nagłówku. Dopóki
    tej tabeli nie było, jedyną drogą na linię czasu był etap zawodów; dopisanie gali wymagałoby
    więc utworzenia fikcyjnego etapu z fikcyjnym oknem oddawania prac.

    Dlaczego to model, a nie treść redakcyjna: linia czasu układa wydarzenia **względem siebie**
    (pozycja na osi, kolejność, stan „minione/teraz/przed nami”), więc potrzebuje dat jako dat,
    a nie jako zdania. Redakcja opisuje wydarzenie słowem na swojej stronie i podlinkowuje je
    z ``url`` – to jest podział pracy, który obowiązuje w projekcie wszędzie.

    ``ends_on`` puste znaczy „jeden dzień”, a nie „bez końca”: kalendarz nie ma pojęcia wydarzenia
    bez końca, a pusty koniec w formularzu jest najczęstszym przypadkiem (gala, webinar).
    """

    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="events")
    title = models.CharField("nazwa", max_length=80)
    starts_on = models.DateField("początek")
    ends_on = models.DateField("koniec", null=True, blank=True)
    # Krótki dopisek pod nazwą: „online”, „Kraków, ICE”, „dla nauczycieli”. Wolny tekst, bo to
    # przypis do jednego wiersza kalendarza, a nie dana, po której cokolwiek filtrujemy.
    note = models.CharField("dopisek", max_length=120, blank=True)
    # Adres wewnętrzny (``/warsztaty/``) albo zewnętrzny. Walidację postaci robi serwis
    # (``apps.competitions.events``), bo ``URLField`` nie przyjąłby ścieżki względnej, a to
    # najczęstszy przypadek: wydarzenie ma zwykle własną stronę w tym samym serwisie.
    url = models.CharField("odnośnik", max_length=300, blank=True)
    # Wyłącznik pojedynczego wiersza. Koordynator wpisuje termin, zanim go ogłosi – a skasowanie
    # i wpisanie go ponownie za tydzień byłoby utratą tego, co już ustalił.
    show_on_timeline = models.BooleanField("na linii czasu", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dodane przez",
    )
    created_at = models.DateTimeField("utworzone", default=timezone.now)

    class Meta:
        verbose_name = "wydarzenie edycji"
        verbose_name_plural = "wydarzenia edycji"
        ordering = ("starts_on", "id")
        constraints = [
            # Ostatnia linia obrony przed zapisem z pominięciem ``full_clean()``. Pusty koniec
            # przechodzi, bo znaczy „jeden dzień”.
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(ends_on__gte=F("starts_on")),
                name="competitions_editionevent_ends_after_starts",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.starts_on.isoformat()})"

    def clean(self) -> None:
        """Ta sama reguła, co constraint – z komunikatem, który da się pokazać pod polem."""
        super().clean()
        if self.ends_on is not None and self.starts_on is not None and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "Koniec wydarzenia nie może być przed jego początkiem."})

    @property
    def date_range(self) -> tuple[date, date]:
        """Termin jako para ``(początek, koniec)``. Puste ``ends_on`` to wydarzenie jednodniowe.

        Jedno wejście dla wszystkich czytających – tak samo jak ``Stage.event_range`` – żeby
        nigdzie nie powstał ekran liczący „koniec albo początek” po swojemu.
        """
        return (self.starts_on, self.ends_on or self.starts_on)
