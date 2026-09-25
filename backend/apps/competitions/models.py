"""Modele domeny zawodów: edycja, etap, skala punktacji, próg kwalifikacji, zadanie, wpis do etapu.

Zasady:
- czas zawsze przez ``django.utils.timezone.now()`` (pola w UTC, ``USE_TZ=True``),
- reguły integralności są jednocześnie w ``clean()`` (czytelny ValidationError) i w bazie
  (constraint – ostatnia linia obrony przed zapisem z pominięciem ``full_clean()``),
- dostęp do danych wyłącznie przez ORM.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import COORDINATOR_GROUPS, Participant, generate_public_code
from apps.core.points import POINTS_PLACES, SCORE_MAX_DIGITS, TOTAL_MAX_DIGITS
from apps.tenancy.managers import CompetitionScopedQuerySet

from .scoping import (
    competition_scoped_manager,
    require_competition,
    resolve_competition,
    scope_to_competition,
)
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


def required_scale_offset(values) -> int:
    """Przesunięcie, jakiego wymaga ta skala: ile dodać do każdej wartości, żeby najniższa dała zero.

    Skala bez wartości ujemnych wymaga zera i to jest **każda** skala Olimpiady Kwantowej, więc
    funkcja jest dla niej stałą (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.6 b, decyzja organizatora
    D10). Przesunięcie jest wyliczane, a nie wpisywane ręcznie: dwie deklaracje tej samej prawdy
    (najniższa wartość skali i liczba, o którą się ją przesuwa) rozjechałyby się przy pierwszej
    zmianie skali, a skutkiem byłaby ocena zapisana w bazie w innej skali niż odczytywana.
    """
    numbers = sorted(scoring_allowed_values(values))
    if not numbers or numbers[0] >= 0:
        return 0
    return -numbers[0]


def validate_scoring_values(
    values, max_value, *, values_field: str, max_field: str, allow_negative: bool = False
) -> None:
    """Reguły skali punktowej – wspólne dla skali etapu i dla nadpisania w zadaniu.

    Jedna definicja, bo to jest ta sama skala: zadanie z własnymi wartościami musi spełniać
    dokładnie te same warunki, co etap (0 w zestawie, wartości unikalne i rosnące, maksimum równe
    największej wartości). Dwie kopie tych reguł rozjechałyby się przy pierwszej zmianie, a skutek
    byłby widoczny dopiero przy wystawianiu oceny.

    Nazwy pól są parametrem, żeby komunikat stanął pod właściwym polem formularza – w etapie jest
    to ``values``/``max_value``, w zadaniu ``scoring_values``/``max_points``.

    ``allow_negative`` (etap 2 § 1.2.6 b) znosi **wyłącznie** wymóg nieujemności i jest
    przekazywany przez wołającego na podstawie flagi ``weighted_scoring``. Przy wartości domyślnej
    funkcja zachowuje się bit w bit jak przed etapem 2, łącznie z brzmieniem komunikatów, które
    koordynator czyta na ``/coordinator/stages/<id>/scale/``. Wymóg obecności zera **zostaje także
    przy punktach ujemnych**: skala bez zera nie ma jak wyrazić „brak istotnego postępu”, a
    ``max_value`` nadal musi równać się największej wartości.
    """
    if not isinstance(values, list) or not values:
        raise ValidationError({values_field: "Skala musi być niepustą listą pozycji {value, label}."})
    numbers: list[int] = []
    for item in values:
        if not isinstance(item, dict) or "value" not in item or "label" not in item:
            raise ValidationError({values_field: "Każda pozycja skali wymaga pól 'value' i 'label'."})
        value = item["value"]
        if not isinstance(value, int) or isinstance(value, bool) or (value < 0 and not allow_negative):
            raise ValidationError(
                {
                    values_field: (
                        "Pole 'value' musi być liczbą całkowitą."
                        if allow_negative
                        else "Pole 'value' musi być nieujemną liczbą całkowitą."
                    )
                }
            )
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
    """Edycja olimpiady, np. „XV (2026/2027)”. Bieżąca może być tylko jedna.

    Edycja jest **korzeniem domeny zawodów**: etap, zadanie, wpis, praca, recenzja, wynik i dyplom
    dochodzą do właściciela przez nią (``docs/UNIWERSALNY-ETAP-1.md`` § 3.4). Dlatego to ona – jako
    jedyny model tej aplikacji – ma własną kolumnę konkursu, a nie każdy z nich z osobna: druga
    droga do tej samej prawdy to druga okazja do rozjazdu, a rozjazd w tabeli izolacji znaczy wyciek.

    Wszystko, co dotąd stało na edycji, na niej **zostaje**: ``year_label``, ``is_current``, okno
    rejestracji i retencja są decyzjami o **roczniku**, a nie o konkursie.
    """

    #: ``NOT NULL`` od wydania D (§ 4.1): nullowalność była stanem przejściowym, w którym stara
    #: i nowa wersja aplikacji mogły przez chwilę stać obok siebie. Od tej chwili edycja bez
    #: konkursu nie jest „edycją do uzupełnienia”, tylko rocznikiem bez właściciela – czyli
    #: wierszem niewidocznym dla ``current_edition()`` i niewidocznym w zakresowaniu.
    #:
    #: ``PROTECT``, bo skasowanie konkursu razem z jego edycjami zabrałoby ze sobą prace, recenzje
    #: i wyniki – czyli dokumentację zawodów, które się odbyły.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="editions",
        verbose_name="konkurs",
    )
    #: Bez ``unique=True``: „I edycja 2026/2027” ma prawo istnieć w każdym konkursie z osobna,
    #: a unikalność obowiązuje w parze z właścicielem (``competitions_edition_unique_year_label``
    #: w ``Meta.constraints``). Globalna unikalność znaczyłaby, że pierwszy organizator zajmuje
    #: oznaczenie rocznika wszystkim pozostałym.
    year_label = models.CharField("oznaczenie edycji", max_length=64)
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

    #: Domyślna ścieżka queryseta (``competition``) wystarcza – edycja ma własną kolumnę.
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "edycja"
        verbose_name_plural = "edycje"
        ordering = ("-created_at", "id")
        constraints = [
            # Częściowy indeks unikalny: dopuszcza wiele edycji archiwalnych, dokładnie jedną
            # bieżącą – **w konkursie**. Nazwa więzi zostaje ta sama, co przed wydaniem D, żeby
            # migracja była ``RemoveConstraint`` + ``AddConstraint`` o jednej nazwie, a nie zmianą,
            # którą trzeba potem tropić w logach (§ 1.4).
            models.UniqueConstraint(
                fields=["competition", "is_current"],
                condition=Q(is_current=True),
                name="competitions_edition_single_current",
            ),
            # Oznaczenie rocznika jest unikalne **u organizatora**, a nie w instalacji: dwie
            # olimpiady mają prawo prowadzić równolegle „I edycję 2026/2027”.
            models.UniqueConstraint(
                fields=["competition", "year_label"],
                name="competitions_edition_unique_year_label",
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

    def save(self, *args, **kwargs):
        """Nowa edycja bez wskazanego konkursu dostaje ten „na teraz”.

        Domyślna wartość jest **w modelu**, a nie w każdym z wołających, bo edycje zakłada dziś
        kilka dróg, z których część nie należy do tego zadania: panel ``/admin/``, komendy seedów
        (``seed_edition_kwantowa``, ``seed_demo``), ``create_competition`` i import z integracji.
        Edycja bez właściciela byłaby niewidoczna dla ``current_edition()``, czyli zniknęłaby
        z nagłówka, harmonogramu i strony głównej – a to jest dokładnie ta klasa regresji, której
        zabrania § 0 dokumentu.

        Wypełniamy **wyłącznie przy wstawianiu**: przy zapisie istniejącego wiersza konkurs jest
        faktem, a nie domyślną wartością, a podstawianie go z kontekstu przy każdym ``save()``
        pozwoliłoby żądaniu jednego konkursu przepisać edycję drugiego.

        ``None`` (instalacja bez konkursów, baza z dwoma i bez wskazania) zostaje ``None`` i jest
        widoczne w kontroli przed ``NOT NULL`` (§ 4.4) – zgadywanie właściciela byłoby cichym
        przypisaniem danych cudzemu organizatorowi.
        """
        if self._state.adding and self.competition_id is None:
            self.competition = resolve_competition()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.is_current:
            # Zakres tego sprawdzenia jest dokładnie taki, jak zakres więzi w bazie
            # (``competitions_edition_single_current`` na parze ``(competition, is_current)``).
            # Walidacja luźniejsza od bazy zamieniłaby czytelny ``ValidationError`` w
            # ``IntegrityError`` w środku zapisu, a ostrzejsza – zabraniałaby organizatorowi B
            # ustawienia własnej edycji bieżącej dlatego, że ma ją organizator A.
            #
            # Formularz waliduje się **przed** ``save()``, więc konkursu w polu jeszcze nie ma;
            # bierzemy wtedy ten sam, który wpisze ``save()`` (kontekst żądania albo jedyny
            # konkurs instalacji). ``None`` nie pasuje po wydaniu D do żadnego wiersza, więc
            # brak rozstrzygnięcia nie zamienia się tu w odmowę zapisu z cudzego powodu.
            competition_id = self.competition_id or getattr(resolve_competition(), "pk", None)
            others = Edition.objects.filter(competition_id=competition_id, is_current=True).exclude(
                pk=self.pk
            )
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


def current_registration_status(now=None, competition=None) -> RegistrationStatus:
    """Stan rejestracji **bieżącej** edycji konkursu – jedno źródło prawdy dla całego serwisu.

    Brak bieżącej edycji to ``disabled``, a nie „otwarta”: nie ma wtedy czego organizować, więc
    konto założone w takiej chwili nie miałoby do czego należeć (zapis do etapu i tak odmówiłby).
    Ta sama odpowiedź należy się konkursowi, którego edycji **nie widać** – w bazie
    wielokonkursowej otwarta rejestracja sąsiada nie jest otwartą rejestracją tutaj.

    ``competition=None`` znaczy „konkurs z kontekstu” (żądanie ustawia go warstwą, zadanie –
    ``competition_context``), a nie „dowolny”. Argument stoi **za** ``now``, żeby jedyne dzisiejsze
    wywołanie pozycyjne (``current_registration_status(now)`` w ``registration.py``) zostało tym,
    czym było. Konkurs nierozstrzygnięty w instalacji, która konkursy ma, podnosi
    ``CompetitionNotResolved``: „rejestracja wyłączona” byłoby tu odpowiedzią wyglądającą
    poprawnie i zamykałoby zapisy komuś, o kogo nikt nie pytał (§ 2.4).

    Zapytanie jest celowo najtańsze z możliwych – wywołuje je procesor kontekstu, czyli każde
    renderowanie szablonu bazowego.
    """
    edition = (
        Edition.objects.filter(competition=require_competition(competition), is_current=True)
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
    potrzeby drugiej reguły. Wyjątkiem jest ``ROUND``.

    ``ROUND`` to **rodzaj bez miejsca w kolejności**: konkurs o pięciu rundach nie ma dla nich
    pięciu nazw rodzaju, a rozróżnia je ``Stage.name`` („Runda 1”, „Runda 2”). Dlatego i tylko
    dlatego więz ``competitions_stage_unique_kind`` jest od etapu 2 warunkowy – nazwa więzi
    zostaje ta sama, żeby nie trzeba było jej tropić w logach wdrożenia. Kolejność takich etapów
    bierze się z ``PipelineStep.position``, a nie z rodzaju. Olimpiada Kwantowa nie ma ani jednego
    etapu tego rodzaju, więc więz działa dla niej dosłownie jak przed zmianą.
    """

    ELIM = "ELIM", "Eliminacje"
    DISTRICT = "DISTRICT", "Wojewódzki"
    FINAL = "FINAL", "Finał"
    TRAINING = "TRAINING", "Trening"
    ROUND = "ROUND", "Runda"


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

    #: Bez własnej kolumny: etap dochodzi do konkursu przez edycję (§ 3.4). Jedna droga zamiast
    #: dwóch – wtedy nie ma czego uzgadniać i nie ma jak się rozjechać.
    objects = competition_scoped_manager("edition__competition")

    class Meta:
        verbose_name = "etap"
        verbose_name_plural = "etapy"
        ordering = ("edition", "opens_at", "id")
        constraints = [
            # Warunek, a nie nowa więź: „jeden etap danego rodzaju na edycję” zostaje regułą dla
            # każdego rodzaju poza ``ROUND``, bo rundy są rozróżniane nazwą, a nie rodzajem
            # (patrz ``StageKind``). Nazwa więzi jest nietknięta – migracja zdejmuje ją i zakłada
            # pod tą samą nazwą, więc w logach wdrożenia nie pojawia się nowy byt do wytłumaczenia.
            models.UniqueConstraint(
                fields=["edition", "kind"],
                condition=~Q(kind=StageKind.ROUND),
                name="competitions_stage_unique_kind",
            ),
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
    # Przesunięcie skali (etap 2 § 1.2.6 b, decyzja organizatora D10). ``values`` trzyma skalę
    # **taką, jaką wpisał organizator** – z punktami ujemnymi, gdy konkurs je ma. W bazie ocen
    # punkt ujemny nie ma gdzie stanąć: ``Review.score``, ``FinalGrade.score`` i
    # ``StageEntry.total_points`` mają więz „nie mniej niż zero” (od wydania 0.35.0 są
    # ``DecimalField`` z jawnym ``CheckConstraint``, wcześniej ``Positive*IntegerField``). Ta
    # bazodanowa gwarancja łapie błąd serwisu, zanim dojdzie do tabeli wyników, więc zostaje.
    # Dlatego ocena leży w bazie **przesunięta**: zapisujemy ``wartość + offset``, czytamy
    # ``wartość_z_bazy - offset``, a ``offset`` jest dokładnie tą liczbą, która najniższą wartość
    # skali sprowadza do zera (``required_scale_offset``). Konkurs #1 ma ``offset = 0`` i przy
    # zerze żadne z tych działań nie zmienia ani jednej liczby.
    offset = models.SmallIntegerField("przesunięcie skali", default=0)
    # Tryb oceniania etapu (prośba organizatora z 2026-09-24, wydanie 0.35.0). ``False`` – „tylko
    # wartości ze skali” – jest zachowaniem sprzed tego wydania i wartością **każdego** etapu, także
    # nowego: dowolne wartości są decyzją organizatora, a nie stanem wyjściowym. ``True`` znaczy
    # „dowolna wartość od minimum do maksimum skali, co 0,01”: recenzent wpisuje np. 4,25, a pozycje
    # ``values`` zostają przy polu oceny jako **podpowiedź** („5 – rozwiązanie pełne z drobnymi
    # usterkami”), a nie lista zamknięta.
    #
    # Tryb jest cechą **etapu**, a nie zadania: zadanie z własną skalą bierze z niej granice
    # (minimum i maksimum), ale o tym, czy między nimi wolno wpisać 4,25, rozstrzyga etap. Jedno
    # pytanie „jak się tu ocenia” ma jedną odpowiedź dla całej komisji etapu. Regułę składa
    # ``apps.competitions.scoring.score_rule`` i nikt poza nią.
    free_values = models.BooleanField("dowolne wartości", default=False)

    #: Droga przez etap. Skali nie czyta dziś żaden ekran „po konkursie” – zakres jest tu po to,
    #: żeby audyt izolacji (§ 3.9) nie natrafił na model, o którym nie wiadomo, czyj jest.
    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "skala punktacji"
        verbose_name_plural = "skale punktacji"

    def __str__(self) -> str:
        return f"skala {sorted(self.allowed_values())} dla {self.stage_id}"

    def allowed_values(self) -> set[int]:
        """Zbiór dopuszczalnych ocen **w postaci wpisanej przez organizatora** (z minusem, gdy jest).

        To jest skala do pokazania człowiekowi: formularz koordynatora, lista wyboru recenzenta,
        tabela wyników. Do porównania z tym, co leży w kolumnie ``score``, służy
        ``stored_allowed_values`` – przy ``offset = 0`` (czyli w całym Konkursie #1) oba zbiory są
        tym samym zbiorem.
        """
        return scoring_allowed_values(self.values)

    def stored_allowed_values(self) -> set[int]:
        """Te same oceny w postaci, w jakiej wolno je zapisać w bazie: wartość powiększona o offset.

        Jedyny zbiór, z którym wolno porównywać ``Review.score`` i ``FinalGrade.score`` – bo to
        one w tej postaci leżą (``grading.services.allowed_scores``).
        """
        offset = self.offset or 0
        return {value + offset for value in self.allowed_values()}

    @property
    def stored_minimum(self) -> int:
        """Najniższa ocena skali w postaci przechowywanej (przy poprawnej skali: zero)."""
        values = self.stored_allowed_values()
        return min(values) if values else 0

    @property
    def stored_maximum(self) -> int:
        """Najwyższa ocena skali w postaci przechowywanej: ``max_value`` powiększone o offset."""
        values = self.stored_allowed_values()
        return max(values) if values else (self.max_value or 0) + (self.offset or 0)

    def clean(self) -> None:
        super().clean()
        offset = self.offset or 0
        # Punkty ujemne wolno wpisać dokładnie wtedy, kiedy skala jest przesunięta – a przesunięcie
        # nakłada serwis (``set_scoring_scale``) i tylko przy włączonej fladze ``weighted_scoring``.
        # Model nie czyta flagi sam: droga do konkursu wiedzie przez etap i edycję, więc każde
        # ``full_clean()`` skali kosztowałoby dwa zapytania, także w konkursie, który o punktach
        # ujemnych nigdy nie słyszał.
        validate_scoring_values(
            self.values,
            self.max_value,
            values_field="values",
            max_field="max_value",
            allow_negative=offset > 0,
        )
        required = required_scale_offset(self.values)
        if offset != required:
            raise ValidationError(
                {
                    "offset": (
                        f"Przesunięcie skali musi być równe {required} – tyle brakuje najniższej "
                        "wartości do zera."
                    )
                }
            )


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
    # Próg jest liczbą dziesiętną od wydania 0.35.0: suma etapu w trybie dowolnych wartości bywa
    # ułamkowa (38,75), więc próg „co najmniej 38,5” musi dać się zapisać. Nieujemność pilnuje
    # więz w bazie – ta sama gwarancja, którą wcześniej dawał ``PositiveIntegerField``.
    min_points = models.DecimalField(
        "minimum punktów",
        max_digits=TOTAL_MAX_DIGITS,
        decimal_places=POINTS_PLACES,
        null=True,
        blank=True,
    )
    top_n = models.PositiveIntegerField("liczba kwalifikowanych", null=True, blank=True)

    #: Jw. – próg należy do etapu, a etap do edycji. Reguł kwalifikacji ta zmiana nie dotyka.
    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "próg kwalifikacji"
        verbose_name_plural = "progi kwalifikacji"
        constraints = [
            models.CheckConstraint(
                condition=Q(min_points__isnull=True) | Q(min_points__gte=0),
                name="competitions_qualificationrule_min_points_non_negative",
            ),
        ]

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
    # Maksimum zadania. Z wypełnioną ``scoring_values`` jest maksimum **jego skali** (musi równać
    # się największej wartości). Od wydania 0.35.0 może stać **samo** – bez listy wartości – i to
    # wyłącznie w etapie w trybie dowolnych wartości (``ScoringScale.free_values``): „zadania mogą
    # mieć różną ilość punktów” (prośba organizatora z 2026-09-24). Zadanie ocenia się wtedy
    # dowolną liczbą od 0 do tego maksimum, co 0,01. Stąd typ dziesiętny: maksimum 12,5 jest
    # równie dobrym maksimum jak 7. Nieujemność – więz w bazie, jak przy każdej kolumnie punktów.
    max_points = models.DecimalField(
        "maksimum punktów",
        max_digits=SCORE_MAX_DIGITS,
        decimal_places=POINTS_PLACES,
        null=True,
        blank=True,
    )
    # Waga zadania w sumie etapu (etap 2 § 1.2.6 a) – **ułamek zwykły**, a nie liczba
    # zmiennoprzecinkowa, i to jest decyzja, nie przesada: waga ``1/3`` zapisana jako ``0.333…``
    # daje sumę zależną od kolejności dodawania, czyli tabelę wyników zmieniającą się przy
    # przeliczeniu. Sumowanie idzie przez ``fractions.Fraction`` (``services.stage_scoring``),
    # a zaokrąglenie zapada raz, na końcu. Waga ``1/1`` – jedyna, jaką ma Konkurs #1 – daje
    # dokładnie tę samą liczbę, co dzisiejsze sumowanie ``int``.
    # Licznik wolno ustawić na zero: to zadanie, które się liczy do oceniania, ale nie do sumy
    # etapu (zadanie treningowe wewnątrz etapu). Mianownik zerowy jest niewyrażalny i pilnuje tego
    # więz w bazie, a nie tylko ``clean()`` – dzielenie przez zero w przeliczeniu wyników byłoby
    # błędem 500 na ekranie, który koordynator otwiera w dniu ogłoszenia.
    weight_numerator = models.PositiveSmallIntegerField("waga – licznik", default=1)
    weight_denominator = models.PositiveSmallIntegerField("waga – mianownik", default=1)

    #: Treść zadania bywa pobierana adresem, którego nikt nie musi znać (``/api/competitions/
    #: problems/<pk>/statement/``), więc zakres jest tu regułą bezpieczeństwa, a nie porządkiem.
    objects = competition_scoped_manager("stage__edition__competition")

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
            models.CheckConstraint(
                condition=Q(weight_denominator__gte=1),
                name="competitions_problem_weight_denominator_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_points__isnull=True) | Q(max_points__gte=0),
                name="competitions_problem_max_points_non_negative",
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

    @property
    def has_own_max(self) -> bool:
        """Czy zadanie ma **samo** maksimum, bez listy wartości (tryb dowolnych wartości, 0.35.0).

        Czy takie zadanie rządzi się własnym zakresem, rozstrzyga ``scoring.uses_own_range`` –
        zależy to od trybu etapu, którego model zadania nie czyta sam (kosztowałoby to zapytanie
        przy każdym odczycie).
        """
        return not self.scoring_values and self.max_points is not None

    @property
    def weight(self) -> Fraction:
        """Waga zadania jako ułamek zwykły. ``1`` dla każdego zadania, które wagi nie dostało.

        Ułamek, a nie ``float`` – powód stoi przy polach. Czytelnikiem tej wartości jest
        ``apps.competitions.services.stage_scoring`` i nikt poza nim: suma etapu ma jedną
        implementację, żeby tabela koordynatora i tabela ogłoszona nie mogły się rozejść.
        """
        return Fraction(self.weight_numerator, self.weight_denominator or 1)

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
        if not self.weight_denominator:
            raise ValidationError({"weight_denominator": "Mianownik wagi musi być dodatni."})
        # Skala i jej maksimum są jedną informacją zapisaną w dwóch polach – tak samo jak w etapie.
        # Puste **oba** znaczą „dziedzicz po etapie”; wypełnione jedno byłoby nadpisaniem bez treści.
        # Wyjątek od 0.35.0: w etapie z dowolnymi wartościami samo maksimum **jest** treścią
        # („to zadanie punktujemy od 0 do 12,5”), więc przechodzi bez listy wartości.
        if not self.scoring_values and self.max_points is None:
            return
        if not self.scoring_values:
            if self._stage_has_free_values():
                if self.max_points <= 0:
                    raise ValidationError({"max_points": "Maksimum punktów zadania musi być dodatnie."})
                return
            raise ValidationError(
                {
                    "scoring_values": (
                        "Podaj wartości skali zadania albo wyczyść maksimum punktów. Samo maksimum "
                        "wolno podać tylko w etapie z dowolnymi wartościami ocen."
                    )
                }
            )
        validate_scoring_values(
            self.scoring_values,
            self.max_points,
            values_field="scoring_values",
            max_field="max_points",
        )

    def _stage_has_free_values(self) -> bool:
        """Czy etap tego zadania ocenia dowolnymi wartościami. Etap bez skali – nie.

        Jedno zapytanie (skala etapu), płacone wyłącznie przez zadanie z samym maksimum: tylko
        takie zadanie o to pyta. Zadanie ze skalą albo bez nadpisania do tej metody nie dochodzi.
        """
        if not self.stage_id:
            return False
        return ScoringScale.objects.filter(stage_id=self.stage_id, free_values=True).exists()


# =================================================================================================
# Kategorie uczestników (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.4)
# =================================================================================================


class Category(models.Model):
    """Kategoria uczestników: rocznik, klasa, typ szkoły albo cokolwiek, co ustali organizator.

    Kategoria jest **danymi konkursu**, a nie edycji („szkoła podstawowa / ponadpodstawowa” ma
    przeżyć rocznik); przypisanie jest natomiast per **wpis do etapu** (``StageEntry.category``),
    bo uczeń zmienia klasę między edycjami.

    Konkurs #1 kategorii nie ma i mieć nie będzie: tabela zostaje pusta, ``StageEntry.category``
    zostaje ``NULL``, a flaga ``categories`` jest domyślnie wyłączona – czyli ranking, próg
    i snapshot wyglądają dokładnie tak, jak przed etapem 2 (§ 0.1). Kategorie definiuje **panel**
    (decyzja D12), a szablony ``kwantowa`` i ``pusty`` nie zakładają ani jednej.

    Druga strona tej samej decyzji: kategoria ma **własną** kolumnę konkursu, bo jest jedynym
    sposobem, w jaki może do niego dojść – nie wisi na edycji ani na etapie (§ 1.0 (a)).
    """

    #: ``PROTECT``, tak samo jak przy ``Edition.competition``: skasowanie konkursu razem
    #: z kategoriami pociągnęłoby za sobą wpisy do etapów, czyli dokumentację odbytych zawodów.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="categories",
        verbose_name="konkurs",
    )
    #: Kod jest stały i nadaje się do wpisania w regulaminie, adresie i imporcie grupowym; nazwa
    #: bywa poprawiana w trakcie sezonu. Unikalność obowiązuje **w konkursie**, a nie w instalacji
    #: – „podstawowa” należy się każdemu organizatorowi z osobna.
    code = models.SlugField("kod", max_length=32)
    name = models.CharField("nazwa", max_length=120)
    position = models.PositiveSmallIntegerField("kolejność", default=0)
    #: Reguła automatycznego przypisania – **opcjonalna**. Puste = kategorię wskazuje uczestnik albo
    #: koordynator. Zakres klas jest jedyną regułą, którą system policzy sam, bo ``Participant.grade``
    #: jest jedyną cechą o porządku; wszystko inne (typ szkoły, rocznik urodzenia, język) jest
    #: wyborem, a nie wyliczeniem, i zgadywanie go byłoby wpisaniem uczestnikowi decyzji za niego.
    grade_min = models.PositiveSmallIntegerField("klasa od", null=True, blank=True)
    grade_max = models.PositiveSmallIntegerField("klasa do", null=True, blank=True)
    #: Wycofanie kategorii jest przestawieniem tego pola, a nie usunięciem wiersza: wpisy z lat
    #: poprzednich mają dalej pokazywać, w jakiej kategorii ktoś startował.
    is_active = models.BooleanField("aktywna", default=True)

    #: Domyślna ścieżka queryseta (``competition``) – kategoria ma własną kolumnę.
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "kategoria"
        verbose_name_plural = "kategorie"
        ordering = ("competition", "position", "id")
        constraints = [
            models.UniqueConstraint(fields=["competition", "code"], name="competitions_category_unique_code"),
            # Zakres odwrócony („od 5 do 2”) nie jest kategorią pustą, tylko literówką – a literówka
            # w regule automatycznego przypisania jest niewidoczna do chwili, w której uczestnik
            # wyląduje w cudzym rankingu. Ostatnia linia obrony przed zapisem z pominięciem
            # ``full_clean()``; warunek przepuszcza każdy zakres jednostronny i pusty.
            models.CheckConstraint(
                condition=Q(grade_min__isnull=True)
                | Q(grade_max__isnull=True)
                | Q(grade_min__lte=F("grade_max")),
                name="competitions_category_grades_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def clean(self) -> None:
        super().clean()
        if self.grade_min is not None and self.grade_max is not None and self.grade_min > self.grade_max:
            raise ValidationError({"grade_max": "Klasa „do” nie może być niższa niż klasa „od”."})

    @property
    def has_grade_rule(self) -> bool:
        """Czy kategoria ma regułę automatyczną. Brak reguły znaczy „wskazuje ją człowiek”."""
        return self.grade_min is not None or self.grade_max is not None

    def matches_grade(self, grade) -> bool:
        """Czy uczeń tej klasy wpada do tej kategorii **regułą**, a nie wskazaniem.

        Kategoria bez reguły odpowiada ``False`` – i to jest właściwa odpowiedź, a nie „pasuje
        do wszystkiego”: gdyby brak zakresu znaczył „bez ograniczeń”, pierwsza kategoria
        wpisana bez klas przechwytywałaby cały konkurs.
        """
        if grade is None or not self.has_grade_rule:
            return False
        if self.grade_min is not None and grade < self.grade_min:
            return False
        return not (self.grade_max is not None and grade > self.grade_max)

    @classmethod
    def auto_for_grade(cls, competition, grade) -> "Category | None":
        """Kategoria wyliczona z klasy uczestnika albo ``None``, gdy żadna reguła nie pasuje.

        Jedyne miejsce, w którym stoi reguła automatycznego przypisania – wołają ją rejestracja,
        import grupowy i panel, żeby wszystkie trzy drogi dawały tę samą kategorię. Zakresy mają
        być rozłączne; gdy nie są, wygrywa pierwsza po ``position``, bo to kolejność, którą
        organizator widzi na ekranie, a nie kolejność wstawiania do tabeli.
        """
        if grade is None:
            return None
        for category in cls.objects.for_competition(competition).filter(is_active=True):
            if category.matches_grade(grade):
                return category
        return None


# =================================================================================================
# Drużyny (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3)
# =================================================================================================


class Team(models.Model):
    """Drużyna jako **właściciel wpisu do etapu** – a nie nowy byt obok uczestnika.

    Decyzja, z której wynika reszta tego modelu: drużyna nie dostaje własnej punktacji, własnej
    tabeli wyników ani własnej ścieżki oceniania. Dostaje to samo, co uczestnik – wpis do etapu
    (``StageEntry``) – a wszystko, co dzieje się dalej (zgłoszenie, recenzje, konsensus, suma,
    próg, dyplom), chodzi po wpisach i dlatego **nie zmienia się wcale**. Gdyby drużyna była
    drugim rodzajem zawodnika, każde z tych miejsc musiałoby umieć dwie arytmetyki naraz.

    Stąd ``public_code``: kod publiczny jest identyfikatorem **w tabeli wyników**, a w tabeli
    wyników konkursu drużynowego stoi drużyna. Generator jest ten sam, co dla uczestnika
    (``apps.accounts.models.generate_public_code``) i bierze prefiks z konkursu, więc kody obu
    rodzajów właścicieli wyglądają jednakowo i jednakowo się je przepisuje z wydruku.

    Konkurs #1 drużyn nie ma i mieć nie będzie: tabela zostaje pusta, ``StageEntry.team`` zostaje
    ``NULL``, a flaga ``team_entries`` jest domyślnie wyłączona – czyli wpis ma zawsze uczestnika,
    dokładnie jak przed etapem 2 (§ 0.1).

    Drużyna ma **własną** kolumnę konkursu (§ 1.0 (a)), bo jest bytem konkursu, a nie edycji:
    ``edition`` mówi, w którym roczniku ta drużyna wystartowała, ale to konkurs rozstrzyga
    o prefiksie kodu i o unikalności tego kodu. Rozjazdu obu dróg pilnuje ``clean()``.
    """

    #: ``PROTECT``, tak samo jak przy ``Edition.competition`` i ``Category.competition``:
    #: skasowanie konkursu razem z drużynami pociągnęłoby za sobą wpisy do etapów, czyli
    #: dokumentację odbytych zawodów.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="teams",
        verbose_name="konkurs",
    )
    #: ``CASCADE`` w ślad za edycją: drużyna jest składem **na jeden rocznik** (skład się zmienia,
    #: nazwa bywa powtarzana), więc bez swojej edycji nie opisuje niczego.
    edition = models.ForeignKey(
        Edition, on_delete=models.CASCADE, related_name="teams", verbose_name="edycja"
    )
    name = models.CharField("nazwa", max_length=120)
    #: Ta sama rola, co ``Participant.public_code``, i ten sam generator z prefiksem konkursu.
    #: ``default`` jest wołany przez Django bez argumentów, więc bez konkursu schodzi do prefiksu
    #: zastępczego – poprawia to serwis ``create_team``, który konkurs zna (tak samo jak
    #: ``apps.accounts.services.create_participant_with_public_code``).
    public_code = models.CharField("kod publiczny", max_length=16, default=generate_public_code)
    #: Szkoła drużyny jest napisem, a nie kluczem do wykazu: drużyna bywa międzyszkolna albo
    #: pozaszkolna, a napis jest dokładnie tym, co trafia na listę i na dyplom.
    school = models.CharField("szkoła", max_length=255, blank=True)
    supervisor_email = models.EmailField("opiekun", blank=True)

    #: Domyślna ścieżka queryseta (``competition``) – drużyna ma własną kolumnę.
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "drużyna"
        verbose_name_plural = "drużyny"
        ordering = ("edition", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "public_code"],
                name="competitions_team_public_code_per_competition",
            ),
            # Nazwa rozstrzyga w **edycji**, a nie w konkursie: „Kwanty 1” z roku 2026 i „Kwanty 1”
            # z roku 2027 to dwa różne składy i obie nazwy muszą dać się wpisać.
            models.UniqueConstraint(
                fields=["edition", "name"], name="competitions_team_unique_name_per_edition"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.public_code})"

    def clean(self) -> None:
        """Drużyna i jej edycja muszą należeć do tego samego konkursu.

        Reguły nie da się zapisać jako więzu bazy (porównanie z kolumną sąsiedniej tabeli), więc
        stoi wyłącznie tutaj – tak samo jak przy ``PipelineStep``. Drużyna wpisana do cudzej
        edycji startowałaby w cudzych zawodach z kodem publicznym swojego organizatora.
        """
        super().clean()
        if self.edition_id is None or self.competition_id is None:
            return
        if self.edition.competition_id != self.competition_id:
            raise ValidationError({"edition": "Edycja należy do innego konkursu niż drużyna."})


class TeamMember(models.Model):
    """Uczestnik w składzie drużyny. Członkostwo, a nie drugi profil.

    ``participant`` jest z ``PROTECT``: skasowanie profilu uczestnika, który startował w drużynie,
    zabrałoby ze składu osobę, której nazwisko stoi w protokole. Drużyna z ``CASCADE``, bo skład
    bez drużyny nie znaczy nic.

    Kapitan jest **wskazaniem**, a nie rolą w RBAC: nie daje żadnych uprawnień w systemie i służy
    wyłącznie temu, żeby wiadomo było, do kogo pisać w sprawie drużyny. Dlatego jest polem
    logicznym przy członkostwie, a nie kluczem obcym przy drużynie – skład z kapitanem usuniętym
    ze składu byłby drużyną z kapitanem spoza drużyny.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members", verbose_name="drużyna")
    participant = models.ForeignKey(
        Participant, on_delete=models.PROTECT, related_name="team_memberships", verbose_name="uczestnik"
    )
    is_captain = models.BooleanField("kapitan", default=False)

    #: Przez drużynę, bo to ona ma kolumnę konkursu. Droga przez uczestnika byłaby drugą drogą do
    #: tej samej prawdy – pilnuje jej ``clean()``, a filtruje się po jednej (§ 3.4 etapu 1).
    objects = competition_scoped_manager("team__competition")

    class Meta:
        verbose_name = "członek drużyny"
        verbose_name_plural = "członkowie drużyny"
        # Kapitan na początku składu: tak wygląda lista na ekranie i na wydruku. Porządek po
        # ``id``, a nie po uczestniku, bo porządek po kluczu obcym ciągnąłby ``Participant``
        # i jego ``ordering`` – czyli złączenie w każdym zapytaniu o skład.
        ordering = ("team", "-is_captain", "id")
        constraints = [
            models.UniqueConstraint(fields=["team", "participant"], name="competitions_teammember_unique"),
            # Kapitan jest jeden – dwóch kapitanów to nie jest drużyna o dwóch kapitanach, tylko
            # niedokończone przekazanie funkcji. Warunek zdejmuje więz ze zwykłych członków,
            # więc skład bez kapitana jest dopuszczalny (drużyna dopiero się zbiera).
            models.UniqueConstraint(
                fields=["team"],
                condition=Q(is_captain=True),
                name="competitions_teammember_single_captain",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id} w drużynie {self.team_id}"

    def clean(self) -> None:
        """Uczestnik i drużyna muszą należeć do tego samego konkursu.

        Bez tej reguły skład drużyny byłby jedynym miejscem, w którym profil jednego organizatora
        wchodzi do zawodów drugiego – a stamtąd trafiłby do tabeli wyników przez wpis drużyny.
        """
        super().clean()
        if self.team_id is None or self.participant_id is None:
            return
        if self.participant.competition_id != self.team.competition_id:
            raise ValidationError({"participant": "Uczestnik należy do innego konkursu niż drużyna."})


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


class StageEntryQuerySet(CompetitionScopedQuerySet):
    """Wpisy do etapów, z drogą do konkursu przez etap i jego edycję (§ 3.4).

    ``entry.participant.competition`` jest **drugą** drogą do tej samej prawdy i celowo nie jest
    tą, po której filtrujemy: zgodności obu pilnuje walidacja wpisu, a filtr ma być jeden.
    """

    competition_path = "stage__edition__competition"

    def for_user(self, user, competition=None):
        """Filtr per rola w jednym miejscu (PROJEKT.md 2.3): uczestnik widzi wyłącznie swoje wpisy.

        **Kolejność jest regułą, nie stylem** (§ 3.5): najpierw ``for_competition`` (własność),
        potem rola. Odwrotnie znaczyłoby, że koordynator konkursu A dostaje wpisy konkursu B,
        bo „jest koordynatorem” – a rola jest rolą **w konkursie**, nie w instalacji.

        ``competition=None`` bierze konkurs z kontekstu żądania; szczegóły odwrotów i jedyny
        przypadek, w którym zawężenia nie ma, opisuje ``apps.competitions.scoping``.

        **Wpis drużynowy** (§ 1.2.3) widzi każdy, kto jest w składzie – i wyłącznie w konkursie
        z włączoną flagą ``team_entries``. Gałąź jest bramkowana flagą, a nie samymi danymi, bo
        to jest ten jeden filtr, który stanowi o tym, czy uczestnik widzi cudze wpisy: dopóki
        organizator drużyn nie prowadzi, zapytanie ma zostać dokładnie takie, jak przed etapem 2
        – bez drugiego złączenia i bez szansy, że ``team IS NULL`` po obu stronach zrówna dwa
        wpisy niczyje. Konkurs #1 nie ma ani jednej drużyny **i** nie ma flagi, więc wychodzi
        stąd tą samą drogą, co dotąd.
        """
        scoped = scope_to_competition(self, competition)
        if not user or not user.is_authenticated or not user.is_active:
            return scoped.none()
        if user.groups.filter(name__in=COORDINATOR_GROUPS).exists():
            return scoped
        # ``participant_for`` zamiast ``user.participant``: profil jest odtąd profilem **w tym
        # konkursie**, a relacja jeden-do-jednego oddawałaby po wydaniu D dowolny z nich.
        from apps.accounts.services import participant_for

        resolved = resolve_competition(competition)
        participant = participant_for(user, resolved)
        if participant is None:
            return scoped.none()
        if resolved is not None and resolved.has_feature("team_entries"):
            return scoped.filter(Q(participant=participant) | Q(team__members__participant=participant))
        return scoped.filter(participant=participant)


class StageEntry(models.Model):
    """Udział uczestnika **albo drużyny** w etapie. Dla ELIM powstaje przez rejestrację, dalej przez
    kwalifikację (T-07).

    Właściciel wpisu jest od etapu 2 jeden z dwóch (§ 1.2.3) i pilnuje tego więz
    ``competitions_stageentry_single_owner``: albo ``participant``, albo ``team``, nigdy oba i nigdy
    żaden. Jedynym wejściem do właściciela jest ``apps.competitions.services.entry_owner`` – które
    dla wpisu bez właściciela podnosi ``AttributeError``, a nie oddaje ``None``. Po znullowaniu
    ``participant`` cicha odpowiedź ``None`` byłaby bowiem gorsza od wywrócenia: kilkanaście miejsc
    czyta dziś ``entry.participant`` wprost i każde z nich policzyłoby wynik dla nikogo.

    Konkurs #1 ma ``team`` puste we wszystkich wierszach i flagę ``team_entries`` wyłączoną, więc
    właścicielem jest zawsze uczestnik – dokładnie jak przed etapem 2 (§ 0.1).
    """

    #: Nullowalne **wyłącznie** dlatego, że właścicielem wpisu bywa drużyna (§ 1.2.3), i wyłącznie
    #: razem z więzem ``single_owner`` – bez niego kolumna nullowalna znaczyłaby „wpis niczyj”.
    participant = models.ForeignKey(
        Participant, on_delete=models.CASCADE, related_name="stage_entries", null=True, blank=True
    )
    #: ``PROTECT``, bo skasowanie drużyny razem z jej wpisami zabrałoby wyniki etapu; drużynę
    #: rozwiązuje się przez usunięcie ze składu, a nie przez usunięcie wiersza z wynikami.
    team = models.ForeignKey(
        "Team",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stage_entries",
        verbose_name="drużyna",
    )
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="entries")
    status = models.CharField(
        "status", max_length=16, choices=StageEntryStatus.choices, default=StageEntryStatus.REGISTERED
    )
    # Suma etapu. Dziesiętna od wydania 0.35.0, bo w trybie dowolnych wartości suma ocen 4,25
    # i 3,5 jest 7,75 – a suma ważona zaokrągla się wtedy do 0,01, a nie do pełnego punktu
    # (``competitions.services.StageScoring``). W etapie „tylko ze skali” leżą tu dalej liczby
    # całkowite. Więz „nie mniej niż zero” stoi w ``Meta`` – ten sam, który dawał wcześniej
    # ``PositiveIntegerField``.
    total_points = models.DecimalField(
        "suma punktów",
        max_digits=TOTAL_MAX_DIGITS,
        decimal_places=POINTS_PLACES,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    # Kategoria startowa (etap 2, § 1.2.4). Przypisanie jest przy **wpisie**, a nie przy
    # uczestniku, bo uczeń zmienia klasę między edycjami – ta sama osoba startuje raz
    # w „podstawowej”, rok później w „ponadpodstawowej”, a wiersz sprzed roku ma pokazywać
    # kategorię, w której faktycznie startowała.
    #
    # ``NULL`` znaczy „konkurs bez kategorii” i tak jest w Konkursie #1: pole nie ma wartości
    # domyślnej, nie wchodzi do żadnego istniejącego zapytania i nie dokłada joinu, więc migracja
    # jest samym ``AddField`` bez zmiany danych (§ 0.1, § 0.7).
    #
    # ``PROTECT``, bo skasowanie kategorii razem z wpisami zabrałoby wyniki etapu; kategorię
    # wycofuje się przez ``Category.is_active``, a nie przez usunięcie wiersza.
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stage_entries",
        verbose_name="kategoria",
    )
    # Wpisowe (etap 2, § 1.5.1). Skrót do należności uczestnika za tę edycję – po to, żeby ekran
    # etapu pokazał „nieopłacone” **bez złączenia przez edycję**. Skrót, a nie drugie źródło
    # prawdy: pustą wartość ``apps.tenancy.fees.submission_blocked`` uzupełnia sobie sam, szukając
    # należności po uczestniku i edycji.
    #
    # ``NULL`` znaczy „konkurs bez wpisowego” i tak jest w Konkursie #1: pole nie ma wartości
    # domyślnej, nie wchodzi do żadnego istniejącego zapytania i nie dokłada joinu, więc migracja
    # jest samym ``AddField`` bez zmiany danych (§ 0.1, § 0.7). Domknięcia na ``NOT NULL`` nie
    # będzie nigdy – zawody bezpłatne są stanem docelowym, a nie brakiem do uzupełnienia.
    #
    # ``SET_NULL``, a nie ``PROTECT``: rejestr należności i wynik etapu są dwiema różnymi
    # dokumentacjami, a skasowanie wiersza rozliczenia (pomyłkowe naliczenie) nie może zabrać ze
    # sobą wpisu do etapu ani go zablokować.
    fee = models.ForeignKey(
        "tenancy.ParticipantFee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stage_entries",
        verbose_name="wpisowe",
    )
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
            # Ten sam więz dla drugiego rodzaju właściciela. Warunek jest konieczny, a nie
            # ozdobny: w Postgresie ``NULL`` nie jest równy ``NULL``, więc bez niego więz byłby
            # martwy dla wpisów uczestników (``team`` puste), a i tak nic by nie łapał.
            models.UniqueConstraint(
                fields=["team", "stage"],
                condition=Q(team__isnull=False),
                name="competitions_stageentry_unique_team",
            ),
            # Właściciel wpisu jest dokładnie jeden (§ 1.2.3). To jedyny powód, dla którego
            # ``participant`` wolno było znullować – i dlatego oba te fakty stoją w jednej
            # migracji, a nie w dwóch: baza nie ma stanu pośredniego, w którym wpis jest niczyj.
            models.CheckConstraint(
                condition=Q(participant__isnull=False, team__isnull=True)
                | Q(participant__isnull=True, team__isnull=False),
                name="competitions_stageentry_single_owner",
            ),
            models.CheckConstraint(
                condition=Q(total_points__isnull=True) | Q(total_points__gte=0),
                name="competitions_stageentry_total_points_non_negative",
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
        # Przez ``entry_owner``, a nie przez ``self.participant``: reguła „kto jest właścicielem
        # wpisu” ma jedno miejsce, a wpis drużynowy ma w logu i w ``/admin/`` pokazywać kod
        # drużyny. Import lokalny, bo ``services`` importuje ``models`` na poziomie modułu.
        from .services import entry_owner

        return f"{entry_owner(self).public_code} @ {self.stage_id} ({self.status})"

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

    #: Termin jest zasobem etapu, więc droga do konkursu jest ta sama, co u etapu.
    objects = competition_scoped_manager("stage__edition__competition")

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

    #: Przez wpis do etapu, a nie przez termin: to wpis niesie uczestnika, czyli osobę, do której
    #: idzie przypomnienie – i to jego konkurs rozstrzyga, jaką domenę ma nieść link w liście.
    objects = competition_scoped_manager("entry__stage__edition__competition")

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

    #: Wydarzenie należy do edycji, a edycja do konkursu – kalendarz jednego organizatora nie ma
    #: prawa pokazać gali drugiego.
    objects = competition_scoped_manager("edition__competition")

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


# --- edytor procesu: przebieg edycji jako dane (UNIWERSALNY-ETAP-2 § 1.2) -------------------------
#
# Dwa fakty o przebiegu zawodów są dziś zapisane w kodzie: **kolejność etapów** jako krotka
# ``STAGE_ORDER`` (``apps/results/services.py``) i **próg przejścia** jako ``QualificationRule``
# związany jeden-do-jednego z etapem. Poniższe modele zapisują te same dwa fakty jako wiersze.
#
# Trzy rzeczy, których ta część **nie** robi, bo inaczej byłaby przepisaniem zachowania:
# ``QualificationRule`` i ``QualificationMode`` zostają bez zmiany pola; ``STAGE_ORDER`` zostaje
# na miejscu i nadal jest jedynym czytanym źródłem kolejności, dopóki konkurs ma flagę
# ``process_editor`` wyłączoną (gałąź flagi dokłada T30); migracja ``pipeline_from_stages``
# wpisuje Konkursowi #1 dokładnie to, co w jego bazie stoi dzisiaj, i niczego nie włącza.


class PipelineStep(models.Model):
    """Jeden krok przebiegu edycji: który etap, w którym miejscu kolejki.

    Model jest listą, a nie polem ``Stage.order``, bo krok niesie **dwie** rzeczy: miejsce
    w kolejce i regułę przejścia (``TransitionRule``). Pole porządkowe na etapie zostawiłoby regułę
    bez właściciela, a reguł bywa kilka na krok (osobny próg na kategorię).

    ``edition`` jest tu obok ``stage``, mimo że etap edycję zna. Powód jest jeden i jest
    w ``Meta``: więz „jedno miejsce w kolejce zajmuje jeden krok” musi dać się wyrazić w bazie,
    a więz na kolumnie z sąsiedniej tabeli nie istnieje. Rozjazdu pilnuje ``clean()``.
    """

    edition = models.ForeignKey(
        Edition, on_delete=models.CASCADE, related_name="pipeline_steps", verbose_name="edycja"
    )
    stage = models.OneToOneField(
        Stage, on_delete=models.CASCADE, related_name="pipeline_step", verbose_name="etap"
    )
    position = models.PositiveSmallIntegerField("miejsce w kolejce")
    #: Krok poza torem zawodów: trening, warsztat, sesja próbna. Nie kwalifikuje, nie jest
    #: następnikiem ani poprzednikiem i nie liczy się do osi czasu. Dla etapu treningowego znaczy
    #: dokładnie tyle, co dziś znaczy jego nieobecność w ``STAGE_ORDER``.
    off_pipeline = models.BooleanField("poza torem zawodów", default=False)

    #: Krok należy do edycji, a edycja do konkursu – własnej kolumny nie ma z tego samego powodu,
    #: co etap (§ 3.4 etapu 1): druga droga do tej samej prawdy to druga okazja do rozjazdu.
    objects = competition_scoped_manager("edition__competition")

    class Meta:
        verbose_name = "krok przebiegu"
        verbose_name_plural = "kroki przebiegu"
        ordering = ("edition", "position", "id")
        constraints = [
            # Warunek na ``off_pipeline`` jest częścią reguły, a nie optymalizacją: kroki poza
            # torem nie mają miejsca w kolejce, więc wszystkie stoją na pozycji 0 i nie mają
            # o co się bić. Bez warunku drugi trening w edycji byłby ``IntegrityError``.
            models.UniqueConstraint(
                fields=["edition", "position"],
                condition=Q(off_pipeline=False),
                name="competitions_pipelinestep_unique_position",
            )
        ]

    def __str__(self) -> str:
        place = "poza torem" if self.off_pipeline else f"miejsce {self.position}"
        return f"krok etapu {self.stage_id} ({place})"

    def clean(self) -> None:
        """Krok i jego etap muszą należeć do tej samej edycji.

        Reguły nie da się zapisać jako więzu bazy (porównanie z kolumną sąsiedniej tabeli), więc
        stoi wyłącznie tutaj – i dlatego jest jawna, a nie schowana w serwisie: krok wpisany do
        cudzej edycji ustawiałby kolejność zawodów, których nie dotyczy.
        """
        super().clean()
        if self.stage_id is None or self.edition_id is None:
            return
        if self.stage.edition_id != self.edition_id:
            raise ValidationError({"stage": "Etap należy do innej edycji niż krok przebiegu."})


class TransitionMode(models.TextChoices):
    """Tryby przejścia do następnego kroku – nadzbiór dzisiejszego ``QualificationMode``.

    Pierwsze cztery są dokładnym odwzorowaniem czterech trybów, które system liczy dziś
    (``MIN_POINTS``, ``TOP_N``, ``TOP_N_PER_DISTRICT`` → ``TOP_N_PER_GROUP`` z podziałem po
    regionie, ``HYBRID``). ``PERCENTILE`` i ``MANUAL`` są nowe i Olimpiada Kwantowa ich nie używa.
    """

    MIN_POINTS = "MIN_POINTS", "minimum punktów"
    TOP_N = "TOP_N", "najlepszych N"
    TOP_N_PER_GROUP = "TOP_N_PER_GROUP", "N w grupie"
    HYBRID = "HYBRID", "minimum punktów ORAZ top N"
    PERCENTILE = "PERCENTILE", "najlepsze P procent"
    MANUAL = "MANUAL", "wyłącznie decyzja komitetu"


class TransitionGroupBy(models.TextChoices):
    """Po czym dzielimy pole przed zastosowaniem progu. Puste = bez podziału, czyli jedna tabela.

    Dzisiejszy ``TOP_N_PER_DISTRICT`` to ten sam tryb ``TOP_N_PER_GROUP`` z podziałem ``REGION``:
    „N na województwo” jest szczególnym przypadkiem „N w grupie”, a nie osobną arytmetyką.
    """

    NONE = "", "bez podziału"
    REGION = "REGION", "region"
    CATEGORY = "CATEGORY", "kategoria"


class TransitionRule(models.Model):
    """Reguła przejścia z kroku do następnego. **Kilka reguł na krok = suma zakwalifikowanych.**

    Model jest listą, bo organizator pisze w regulaminie zdania typu „do finału przechodzi 30
    najlepszych **oraz** każdy, kto zdobył co najmniej 90 punktów”. Suma zbiorów jest jedyną
    dozwoloną kompozycją; iloczyn ma własny tryb (``HYBRID``), bo „oraz” w regulaminie bywa jednym
    i drugim, a obie operacje wyrażone tą samą listą byłyby nieczytelne.

    ``QualificationRule`` **nie znika i nie zmienia pól**: to ona jest czytana, dopóki konkurs ma
    flagę ``process_editor`` wyłączoną. Trzy zachowania brzegowe, które przy włączonej fladze mają
    wyjść identycznie (liczy je T30, nie ten model): zero nie kwalifikuje w trybach z ``top_n``,
    remis na progu wpuszcza wszystkich, a decyzja komitetu bije regułę – także sumę reguł.
    """

    step = models.ForeignKey(
        PipelineStep, on_delete=models.CASCADE, related_name="transition_rules", verbose_name="krok"
    )
    mode = models.CharField("tryb", max_length=24, choices=TransitionMode.choices)
    group_by = models.CharField("podział", max_length=16, choices=TransitionGroupBy.choices, blank=True)
    #: Zawężenie do jednej kategorii; puste = reguła dotyczy wszystkich. ``CASCADE``, a nie
    #: ``PROTECT``: reguła bez kategorii, do której się odnosi, nie jest regułą – zostałaby progiem
    #: dla zbioru, którego nie ma. Konkurs #1 kategorii nie ma, więc kolumna zostaje pusta.
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transition_rules",
        verbose_name="kategoria",
    )
    #: Dziesiętny z tego samego powodu, co ``QualificationRule.min_points`` (wydanie 0.35.0).
    min_points = models.DecimalField(
        "minimum punktów",
        max_digits=TOTAL_MAX_DIGITS,
        decimal_places=POINTS_PLACES,
        null=True,
        blank=True,
    )
    top_n = models.PositiveIntegerField("liczba kwalifikowanych", null=True, blank=True)
    percentile = models.PositiveSmallIntegerField("procent", null=True, blank=True)
    position = models.PositiveSmallIntegerField("kolejność", default=0)

    #: Reguła dochodzi do konkursu krokiem, krok edycją. Reguła przejścia jest **konfiguracją**
    #: konkursu, więc czytający ją kod woła ``require_competition`` (§ 1.0 b), a nie miękki odwrót.
    objects = competition_scoped_manager("step__edition__competition")

    class Meta:
        verbose_name = "reguła przejścia"
        verbose_name_plural = "reguły przejścia"
        ordering = ("step", "position", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(percentile__isnull=True) | (Q(percentile__gte=1) & Q(percentile__lte=100)),
                name="competitions_transitionrule_percentile_range",
            ),
            models.CheckConstraint(
                condition=Q(top_n__isnull=True) | Q(top_n__gte=1),
                name="competitions_transitionrule_top_n_positive",
            ),
            models.CheckConstraint(
                condition=Q(min_points__isnull=True) | Q(min_points__gte=0),
                name="competitions_transitionrule_min_points_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_mode_display()} (min={self.min_points}, top={self.top_n})"

    @property
    def requires_min_points(self) -> bool:
        return self.mode in (TransitionMode.MIN_POINTS, TransitionMode.HYBRID)

    @property
    def requires_top_n(self) -> bool:
        return self.mode in (
            TransitionMode.TOP_N,
            TransitionMode.TOP_N_PER_GROUP,
            TransitionMode.HYBRID,
        )

    @property
    def requires_percentile(self) -> bool:
        return self.mode == TransitionMode.PERCENTILE

    def clean(self) -> None:
        """Ta sama reguła, co w ``QualificationRule.clean`` – rozszerzona o dwa nowe tryby.

        ``MANUAL`` nie wymaga żadnego parametru i to nie jest przeoczenie: tryb znaczy „przechodzi
        wyłącznie ten, komu komitet wpisał decyzję ręcznie”, więc próg byłby w nim liczbą, której
        nikt nie czyta.
        """
        super().clean()
        errors: dict[str, str] = {}
        if self.requires_min_points and self.min_points is None:
            errors["min_points"] = f"Tryb {self.mode} wymaga podania min_points."
        if self.requires_top_n and self.top_n is None:
            errors["top_n"] = f"Tryb {self.mode} wymaga podania top_n."
        if self.requires_top_n and self.top_n is not None and self.top_n < 1:
            errors["top_n"] = "top_n musi być dodatnie."
        if self.requires_percentile and self.percentile is None:
            errors["percentile"] = f"Tryb {self.mode} wymaga podania percentile."
        if self.percentile is not None and not 1 <= self.percentile <= 100:
            errors["percentile"] = "percentile musi być z zakresu 1–100."
        if self.mode == TransitionMode.TOP_N_PER_GROUP and not self.group_by:
            errors["group_by"] = "Tryb N w grupie wymaga wskazania podziału."
        if errors:
            raise ValidationError(errors)


# =================================================================================================
# Komponenty etapu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.3)
# =================================================================================================
#
# Dziś etap ma **jedną** formę (``Stage.format``) i dwie wykluczające się ścieżki punktów: zadania
# albo test. Komentarz w ``apps.results.services.compute_stage_results`` mówi wprost, że etap
# o dwóch formach naraz „nie da się opisać ani w regulaminie, ani w tabeli wyników”. Etap 2 znosi
# tę wykluczalność **nie przez dołożenie trzeciej gałęzi**, tylko przez zamianę jednej osi na listę.
#
# ``Stage.format`` zostaje i zostaje autorytatywne dla etapu **bez ani jednego komponentu** – czyli
# dla każdego etapu Olimpiady Kwantowej. Komponenty są dołożeniem wymiaru, a nie przepisaniem: ta
# sama decyzja, co przy konkursie w etapie 1.


class ComponentKind(models.TextChoices):
    """Skąd komponent bierze punkty. Rodzaj jest **źródłem**, a nie etykietą na ekranie.

    Trzy pierwsze odpowiadają dzisiejszym formom etapu (``StageFormat``), dwa ostatnie są nowe.
    ``ONSITE`` i ``TEAM`` czytają ``FinalGrade`` tą samą drogą, co ``SUBMISSIONS`` – różni je to,
    czyja to praca i kto ją wystawił, a nie arytmetyka; osobne wartości istnieją, żeby tabela
    wyników mogła je nazwać w nagłówku kolumny i żeby reguła rozstrzygania remisów (T40) mogła
    wskazać jedną z nich.
    """

    SUBMISSIONS = "SUBMISSIONS", "rozwiązania pisemne"
    QUIZ = "QUIZ", "test online"
    INTERVIEW = "INTERVIEW", "rozmowa"
    ONSITE = "ONSITE", "zawody na miejscu"
    TEAM = "TEAM", "praca drużynowa"


class StageComponent(models.Model):
    """Jedna forma w etapie, z własną wagą i własnym źródłem punktów.

    Etap **bez ani jednego komponentu** zachowuje się dokładnie jak dziś: ``compute_stage_results``
    czyta wtedy ``Stage.format`` i liczy sumę bez wag. Dopiero pierwszy komponent przełącza etap na
    sumowanie po komponentach – i to jest cały przełącznik, obok flagi ``process_editor``.

    **Waga jest ułamkiem zwykłym, nie zmiennoprzecinkowym** (§ 1.2.6), i to jest decyzja, a nie
    przesada: waga ``1/3`` zapisana jako ``0.333…`` daje sumę zależną od kolejności dodawania,
    czyli tabelę wyników zmieniającą się przy przeliczeniu. Sumowanie idzie przez ``Fraction``,
    a zaokrąglenie następuje **raz**, na końcu. Dla ``1/1`` wynik jest identyczny z dzisiejszym
    sumowaniem liczb całkowitych – i to jest osobna asercja testu (§ 5.2).
    """

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="components", verbose_name="etap")
    kind = models.CharField("forma", max_length=16, choices=ComponentKind.choices)
    #: Puste = nazwą jest etykieta rodzaju. Własna nazwa jest tym, co czyta uczestnik w nagłówku
    #: kolumny („Część teoretyczna”), a rodzaj zostaje tożsamością techniczną – ten sam rozdział,
    #: co między ``Stage.kind`` a ``Stage.name``.
    name = models.CharField("nazwa", max_length=80, blank=True)
    position = models.PositiveSmallIntegerField("kolejność", default=1)
    #: Waga jako **ułamek zwykły** – uzasadnienie w docstringu klasy i w § 1.2.6.
    weight_numerator = models.PositiveSmallIntegerField("licznik wagi", default=1)
    weight_denominator = models.PositiveSmallIntegerField("mianownik wagi", default=1)
    #: ``True`` odtwarza dzisiejsze ``_assert_finalized`` (``STAGE_NOT_FINALIZED``): praca w trakcie
    #: oceniania albo bez oceny końcowej nie pozwala zamknąć tabeli. ``False`` liczy brak wyniku
    #: jako zero i jest dla komponentu nieobowiązkowego – zawodów dodatkowych, w których nie każdy
    #: startuje.
    required = models.BooleanField("wymagany", default=True)

    #: Komponent dochodzi do konkursu etapem, etap edycją – własnej kolumny nie ma z tego samego
    #: powodu, co etap (§ 1.0 (a)).
    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "komponent etapu"
        verbose_name_plural = "komponenty etapu"
        ordering = ("stage", "position", "id")
        constraints = [
            # Dwa komponenty tego samego rodzaju w jednym etapie są sensowne („test wstępny”
            # i „test finałowy”), ale nie na tym samym miejscu w kolejności – wtedy tabela
            # wyników miałaby dwie kolumny bez porządku między nimi.
            models.UniqueConstraint(
                fields=["stage", "kind", "position"], name="competitions_stagecomponent_unique"
            ),
            # Ostatnia linia obrony przed dzieleniem przez zero w sumie punktów. Mianownik zerowy
            # nie jest „wagą neutralną”, tylko wywróconym przeliczeniem całego etapu.
            models.CheckConstraint(
                condition=Q(weight_denominator__gte=1),
                name="competitions_stagecomponent_denominator_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} (etap {self.stage_id})"

    @property
    def display_name(self) -> str:
        """Nazwa do pokazania: własna, a bez niej etykieta rodzaju. Komponent nigdy bez podpisu."""
        return self.name or self.get_kind_display()

    @property
    def weight(self) -> Fraction:
        """Waga jako ``Fraction`` – jedyne miejsce, w którym para liczb staje się liczbą.

        Sumowanie po komponentach mnoży przez **tę** wartość i nigdy przez ``float``: dokładność
        ułamka jest tu jedyną gwarancją, że dwie publikacje tych samych danych dadzą ten sam plik.
        """
        return Fraction(self.weight_numerator, self.weight_denominator or 1)

    def clean(self) -> None:
        """Mianownik dodatni – ta sama reguła, co w więzie, tylko z czytelnym komunikatem."""
        super().clean()
        if not self.weight_denominator:
            raise ValidationError({"weight_denominator": "Mianownik wagi musi być dodatni."})


# =================================================================================================
# Punkty z rozmowy – brakujące ogniwo komponentu ``INTERVIEW`` (§ 1.2.3, decyzja organizatora D9)
# =================================================================================================
#
# Dziś rozmowa **nie ma ścieżki punktów**: ``results.services.compute_stage_results`` daje dla etapu
# w formie rozmowy same zera, a punkty wpisuje koordynator w ``/admin/`` (``docs/BACKLOG.md``).
# Tabela niżej domyka tę lukę i jest jedynym źródłem punktów komponentu ``INTERVIEW``. Wejście do
# niej prowadzi przez ``competitions.interviews.record_interview_score`` – za flagą ``process_editor``
# i z wpisem audytowym ``interview.scored``, bo dla Konkursu #1 ekran zamiast ``/admin/`` **jest**
# zmianą widoczną (§ 0.1).


class InterviewScore(models.Model):
    """Punkty z rozmowy – wpis komisji, nie recenzja.

    Osobny model, a nie ``FinalGrade`` bez ``Submission``: ``FinalGrade`` jest jeden-do-jednego ze
    zgłoszeniem, a cała jego semantyka – konsensus, trzeci recenzent, moderacja, reklamacja –
    dotyczy pracy oddanej jako plik. Rozmowa nie ma pliku, nie ma rundy ślepej i nie ma czego
    zastąpić nową wersją.

    **Wpis stoi przy parze (wpis do etapu, komponent), a nie przy terminie rozmowy**, i to jest
    jedyne odstępstwo od szkicu w § 1.2.3. Trzy powody, wszystkie wynikłe z tego, że komponenty
    (§ 1.2.3, T35) weszły przed tą tabelą:

    - punkty czyta suma etapu **po komponencie** (``results.services._component_sources``), więc
      etap o dwóch rozmowach – wstępnej i finałowej – potrzebuje dwóch niezależnych wyników;
      wpis przy terminie dałby jeden, bo termin jest jeden na wpis do etapu,
    - rozmowa, która odbyła się poza kalendarzem systemu (uczestnik umówiony telefonicznie, komisja
      obradująca na miejscu), nie ma wiersza ``InterviewBooking`` – a punkty ma. Wpis przy terminie
      znaczyłby „nie ma zapisu, nie ma oceny”,
    - ``StageEntry`` jest tym, co niesie sumę etapu i kwalifikację; termin jest szczegółem
      organizacyjnym obok. Termin zostaje osiągalny przez ``entry.interview_booking``.

    ``max_points`` jest **kopią maksimum skali z chwili wpisu**, a nie odczytem na żywo: ekran
    komisji pisze „ile z ilu”, a skala etapu bywa poprawiana już po rozmowie. Bez kopii ten sam
    wynik czytałoby się po zmianie skali jako inny ułamek, a tabela wyników zmieniałaby się bez
    ani jednej decyzji.
    """

    entry = models.ForeignKey(
        StageEntry, on_delete=models.CASCADE, related_name="interview_scores", verbose_name="wpis do etapu"
    )
    component = models.ForeignKey(
        StageComponent,
        on_delete=models.CASCADE,
        related_name="interview_scores",
        verbose_name="komponent",
    )
    #: Dziesiętne od wydania 0.35.0: rozmowa w etapie z dowolnymi wartościami ocen bywa oceniona
    #: na 7,5. ``max_points`` zostaje całkowite – to kopia maksimum **skali** etapu, a wartości
    #: skali są liczbami całkowitymi.
    points = models.DecimalField("punkty", max_digits=SCORE_MAX_DIGITS, decimal_places=POINTS_PLACES)
    max_points = models.PositiveSmallIntegerField("maksimum")
    #: Krótka uwaga komisji – **bez danych osobowych i bez uzasadnień o zdrowiu czy poglądach**.
    #: Pole jest krótkie z rozmysłem: protokół rozmowy nie jest przedmiotem tej tabeli, a notatka,
    #: która mieści akapit, staje się nią sama z siebie.
    note = models.CharField("uwaga komisji", max_length=200, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_scores",
        verbose_name="wpisał",
    )
    recorded_at = models.DateTimeField("wpisane", default=timezone.now)

    #: Przez wpis do etapu, tak samo jak zapis na rozmowę – to wpis niesie uczestnika i to jego
    #: konkurs rozstrzyga, czyja to tabela wyników (§ 1.0 (a)).
    objects = competition_scoped_manager("entry__stage__edition__competition")

    class Meta:
        verbose_name = "punkty z rozmowy"
        verbose_name_plural = "punkty z rozmów"
        ordering = ("component", "entry", "id")
        constraints = [
            # Jedna rozmowa to jeden wynik. Drugi wiersz tej samej pary nie byłby „poprawką”, tylko
            # dwiema wersjami prawdy o tym samym komponencie – a suma etapu musi mieć jedną.
            models.UniqueConstraint(fields=["entry", "component"], name="competitions_interviewscore_unique"),
            # Ostatnia linia obrony przed „12 punktów z 10”: wynik spoza skali wchodziłby do sumy
            # etapu i wyszedłby dopiero w ogłoszonej tabeli.
            models.CheckConstraint(
                condition=Q(points__lte=F("max_points")),
                name="competitions_interviewscore_points_within_max",
            ),
            models.CheckConstraint(
                condition=Q(points__gte=0), name="competitions_interviewscore_points_non_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.points}/{self.max_points} (wpis {self.entry_id}, komponent {self.component_id})"

    def clean(self) -> None:
        """Komponent musi być rozmową **tego** etapu, a wynik mieścić się w maksimum.

        Te same reguły, co w więzach, plus jedna, której ``CheckConstraint`` wyrazić nie może:
        komponent z innego etapu jest w tabeli wyników tego etapu liczbą znikąd. Komunikaty są po
        to, żeby koordynator przeczytał je w formularzu ekranu wpisu punktów, a nie zobaczył błędu
        bazy.
        """
        super().clean()
        if self.max_points is not None and self.points is not None and self.points > self.max_points:
            raise ValidationError({"points": "Punkty nie mogą przekraczać maksimum skali."})
        if self.component_id is None:
            return
        if self.component.kind != ComponentKind.INTERVIEW:
            raise ValidationError(
                {"component": "Punkty z rozmowy wolno wpisać wyłącznie komponentowi rozmowy."}
            )
        if self.entry_id is not None and self.component.stage_id != self.entry.stage_id:
            raise ValidationError({"component": "Komponent musi należeć do tego samego etapu, co wpis."})


# =================================================================================================
# Rozstrzyganie remisów jako uporządkowane dane (``docs/UNIWERSALNY-ETAP-2.md`` § 1.2.6 c)
# =================================================================================================
#
# Dziś remis znaczy **to samo miejsce** i nie ma żadnego kryterium, które by go rozstrzygało
# (``results.services._rank_rows``); porządek wewnątrz remisu jest po ``public_code`` i jest
# porządkiem powtarzalności wydruku, a nie kryterium. Etap 2 tego nie zmienia – dokłada obok
# **listę** kryteriów, którą etap może mieć albo nie mieć. Etap bez ani jednego wiersza ``TieBreak``
# układa tabelę dokładnie tak, jak przed etapem 2, a Konkurs #1 nie dostaje w żadnej migracji ani
# jednego wiersza, bo jego regulamin remisów nie rozstrzyga.


class TieBreakKey(models.TextChoices):
    """Czym rozstrzygamy remis. Wartość jest **źródłem liczby**, a nie napisem na ekranie.

    ``NONE`` nie jest brakiem kryterium, tylko jawnym „dalej już nie rozstrzygamy”: wiersze
    równe na wszystkich wcześniejszych kryteriach dzielą miejsce. Istnieje po to, żeby regulamin
    dający dwa kryteria i wspólne miejsce po nich dało się zapisać w danych, zamiast wyrażać go
    nieobecnością wierszy – a przy okazji żeby ekran edytora (T34) miał czym pokazać koniec listy.
    """

    HIGHEST_SINGLE = "HIGHEST_SINGLE", "najwyższy wynik w jednym zadaniu"
    PROBLEM_SCORE = "PROBLEM_SCORE", "wynik we wskazanym zadaniu"
    COMPONENT_SCORE = "COMPONENT_SCORE", "wynik we wskazanym komponencie"
    SOLVED_COUNT = "SOLVED_COUNT", "liczba zadań z pełnym wynikiem"
    SUBMITTED_AT = "SUBMITTED_AT", "wcześniejsze oddanie ostatniej pracy"
    NONE = "NONE", "bez rozstrzygania (wspólne miejsce)"


class TieBreak(models.Model):
    """Jedno kryterium rozstrzygania remisów etapu, z miejscem w kolejności.

    Model jest **listą**, a nie polem ``Stage.tie_break``, z tego samego powodu, dla którego
    kolejność etapów jest listą kroków (§ 1.2.2): regulamin rozstrzyga remis kilkoma kryteriami po
    kolei („wyżej ten, kto ma więcej punktów w zadaniu 3, a przy dalszym remisie ten, kto oddał
    pracę wcześniej”). Jedno pole wyrażałoby wyłącznie pierwsze z nich.

    Kierunek jest osobnym polem, bo nie wynika z kryterium: przy punktach „lepiej” znaczy więcej,
    a przy czasie oddania – wcześniej. Domyślne ``descending=True`` jest prawdziwe dla czterech
    kryteriów punktowych; przy ``SUBMITTED_AT`` koordynator ustawia ``False``.

    Odczytuje ten model **wyłącznie** ``results.services.tie_break_keys`` – suma etapu i miejsce
    w tabeli mają jedną implementację, żeby tabela koordynatora i tabela ogłoszona nie mogły się
    rozejść.
    """

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="tie_breaks", verbose_name="etap")
    key = models.CharField("kryterium", max_length=24, choices=TieBreakKey.choices)
    #: Zadanie wskazane przez ``PROBLEM_SCORE``. ``CASCADE``, a nie ``PROTECT``: kryterium bez
    #: zadania nie ma czego liczyć, więc usunięcie zadania ma zabrać także regułę, a nie zablokować
    #: porządki w etapie, którego nikt jeszcze nie ogłosił.
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tie_breaks",
        verbose_name="zadanie",
    )
    #: Komponent wskazany przez ``COMPONENT_SCORE`` – ta sama reguła usuwania, co przy zadaniu.
    component = models.ForeignKey(
        StageComponent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tie_breaks",
        verbose_name="komponent",
    )
    descending = models.BooleanField("malejąco", default=True)
    position = models.PositiveSmallIntegerField("kolejność", default=0)

    #: Kryterium dochodzi do konkursu etapem, etap edycją – własnej kolumny konkursu nie ma z tego
    #: samego powodu, co etap i komponent (§ 1.0 (a)).
    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "rozstrzyganie remisu"
        verbose_name_plural = "rozstrzyganie remisów"
        ordering = ("stage", "position", "id")
        constraints = [
            # Dwa kryteria na tym samym miejscu w kolejności znaczyłyby tabelę wyników zależną od
            # przypadkowego porządku wierszy w bazie – czyli dwa różne pliki z tych samych danych.
            models.UniqueConstraint(
                fields=["stage", "position"], name="competitions_tiebreak_unique_position"
            ),
            # Kryterium „wynik we wskazanym zadaniu” bez wskazanego zadania nie jest kryterium
            # niepełnym, tylko kryterium, które po cichu nie rozstrzyga niczego. Lepiej, żeby nie
            # dało się go zapisać.
            models.CheckConstraint(
                condition=~Q(key=TieBreakKey.PROBLEM_SCORE) | Q(problem__isnull=False),
                name="competitions_tiebreak_problem_required",
            ),
            models.CheckConstraint(
                condition=~Q(key=TieBreakKey.COMPONENT_SCORE) | Q(component__isnull=False),
                name="competitions_tiebreak_component_required",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.position}. {self.get_key_display()} (etap {self.stage_id})"

    def clean(self) -> None:
        """Wskazanie musi pasować do kryterium i należeć do **tego** etapu.

        Więzy w bazie pilnują obecności wskazania; tutaj dochodzi druga połowa reguły, której
        ``CheckConstraint`` wyrazić nie może: zadanie z innego etapu jest w tabeli wyników tego
        etapu liczbą znikąd. Komunikaty są po to, żeby koordynator przeczytał je w formularzu
        edytora (T34), a nie zobaczył błędu bazy.
        """
        super().clean()
        if self.key == TieBreakKey.PROBLEM_SCORE and self.problem_id is None:
            raise ValidationError({"problem": "Kryterium „wynik we wskazanym zadaniu” wymaga zadania."})
        if self.key == TieBreakKey.COMPONENT_SCORE and self.component_id is None:
            raise ValidationError(
                {"component": "Kryterium „wynik we wskazanym komponencie” wymaga komponentu."}
            )
        if self.problem_id is not None and self.problem.stage_id != self.stage_id:
            raise ValidationError({"problem": "Zadanie musi należeć do tego samego etapu."})
        if self.component_id is not None and self.component.stage_id != self.stage_id:
            raise ValidationError({"component": "Komponent musi należeć do tego samego etapu."})


# Logistyka etapu stacjonarnego (miejsce zawodów, deklaracja przyjazdu, obecność, ustawienia
# obszaru) mieszka razem z resztą swojej logiki w ``apps.competitions.logistics`` – modele
# i czynności w jednym pliku, bo czyta się je wyłącznie razem, a zmienia z innego powodu niż
# punktację i kwalifikację (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.2). Django rejestruje modele
# wtedy, gdy importuje ``models`` aplikacji, więc bez tej linijki ``makemigrations`` nie zobaczyłby
# tabel. Import stoi na końcu pliku – tak samo jak ``apps.accounts.twofactor`` w ``accounts`` –
# bo ``logistics`` sięga do ``StageEntry`` wyłącznie przez nazwę („competitions.StageEntry”).
from .logistics import (  # noqa: E402,F401  (import dla rejestracji modeli)
    ArrivalForm,
    AttendanceRecord,
    LogisticsSettings,
    Venue,
)
