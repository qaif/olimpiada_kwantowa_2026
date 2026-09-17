"""Logika domenowa zawodów.

Widoki tylko orkiestrują: walidacja reguł biznesowych, tworzenie obiektów zależnych i błędy
domenowe (``DomainError``) żyją tutaj. Czas zawsze przez ``timezone.now()``.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import Participant
from apps.core.api import DomainError

from .models import (
    DEFAULT_MAX_VALUE,
    DEFAULT_REVIEW_DEADLINE_DAYS,
    Edition,
    InterviewBooking,
    InterviewSlot,
    Problem,
    QualificationMode,
    QualificationRule,
    ScoringScale,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageFormat,
    StageKind,
    default_scoring_values,
)
from .scoping import scope_to_competition
from .video import DEFAULT_VIDEO_BASE_URL, VideoProvider

#: Pola osi czasu etapu, którymi koordynator zarządza z panelu. ``results_published_at`` i
#: ``closed_at`` są **poza** tą listą świadomie: pierwsze nakłada publikacja wyników, drugie –
#: zamknięcie etapu. Obie wartości są śladem zdarzenia, które już zaszło, a nie planem; ręczne
#: przestawienie ich w formularzu cofałoby skutek operacji, nie zmieniając niczego, co z niej wynikło.
#: ``name`` i ``format`` stoją na początku, bo w takiej kolejności formularz je pokazuje: najpierw
#: „czym jest ten etap”, potem jego oś czasu.
#: ``event_starts_on``/``event_ends_on`` stoją za oknem oddawania prac, bo opisują **inny** fakt:
#: dni, na które uczestnik przyjeżdża, a nie godziny, w których serwer przyjmuje pliki.
STAGE_EDITABLE_FIELDS = (
    "name",
    "format",
    "location",
    "opens_at",
    "deadline_at",
    "grace_seconds",
    "event_starts_on",
    "event_ends_on",
    "review_deadline_at",
    # Dni na jedną recenzję stoją przy deadline recenzji, bo opisują tę samą sprawę z dwóch stron:
    # tamto jest terminem całego etapu, to – terminem osobistym każdego recenzenta, liczonym od
    # chwili przydziału (``apps.grading.deadlines``).
    "review_deadline_days",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
    # Pokój wideo rozmowy kwalifikacyjnej (``apps.competitions.video``). Stoi przy formie etapu,
    # bo ma sens wyłącznie dla ``StageFormat.INTERVIEW`` – i tak samo jak ona opisuje sposób,
    # w jaki odbywają się zawody, a nie ich terminy.
    "video_provider",
    "video_base_url",
)

#: Co wolno zmienić po zamknięciu etapu. Terminy oddania rozwiązań są wtedy faktem historycznym –
#: upload jest zablokowany, a wersje mają status ``LOCKED``, więc przesunięcie ``opens_at`` czy
#: ``deadline_at`` opisywałoby przebieg, który się nie wydarzył. Recenzje i okno reklamacji dopiero
#: przed etapem stoją, a to właśnie one bywają przedłużane (choroba recenzenta, spór o termin).
#: ``name`` jest na tej liście, a ``format`` nie: przemianowanie zamkniętego etapu niczego nie
#: cofa (podpis w archiwum wolno poprawić), natomiast zmiana formy opisywałaby inny przebieg
#: zawodów niż ten, który się odbył.
STAGE_FIELDS_EDITABLE_AFTER_CLOSE = (
    "name",
    "location",
    "review_deadline_at",
    # Ocenianie zaczyna się **po** zamknięciu etapu, więc liczba dni na recenzję jest wtedy
    # najbardziej potrzebna: to po zamknięciu wychodzi, że komisja potrzebuje tygodnia więcej.
    # Zmiana dotyczy przydziałów przyszłych – terminy już przyznane zostają w ``Review.due_at``.
    "review_deadline_days",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
)

#: Pola zadania, którymi zarządza panel. ``statement_pdf`` idzie osobno – jest plikiem.
#: ``scoring_values``/``max_points`` to nadpisanie skali etapu; puste znaczy „dziedzicz”.
PROBLEM_EDITABLE_FIELDS = (
    "number",
    "title",
    # Angielski tytuł jest zwykłym polem tekstowym, więc idzie tędy; angielska treść, jako plik,
    # osobno – dokładnie tak samo jak treść polska.
    "title_en",
    "allowed_formats",
    "max_file_mb",
    "scoring_values",
    "max_points",
    # Uwagi dla recenzentów są zwykłym polem tekstowym (wzorcówka, jako plik, idzie osobno –
    # parametrem ``model_solution`` – dokładnie tak, jak treść zadania).
    "reviewer_notes",
)

#: Okno rejestracji uczestników – jedyne pola ``Edition``, które panel koordynatora zmienia.
#: ``is_current`` i ``year_label`` zostają w ``/admin/``: przełączenie bieżącej edycji jest
#: operacją na całym serwisie (etapy, wyniki, harmonogram), a nie ustawieniem rejestracji.
REGISTRATION_EDITABLE_FIELDS = (
    "registration_enabled",
    "registration_opens_at",
    "registration_closes_at",
)

#: Okres retencji danych osobowych. Stoi na tym samym ekranie, co okno rejestracji, bo obie
#: wartości odpowiadają na to samo pytanie o **ramy czasowe edycji**: od kiedy wolno zbierać dane
#: i do kiedy wolno je trzymać. Osobna stała, bo to nie jest ustawienie rejestracji – wskazuje ją
#: wprost ``EDITION_EDITABLE_FIELDS``, czyli pełna lista pól, które panel koordynatora zmienia.
RETENTION_EDITABLE_FIELDS = ("data_retention_months",)

EDITION_EDITABLE_FIELDS = (*REGISTRATION_EDITABLE_FIELDS, *RETENTION_EDITABLE_FIELDS)


def current_edition(competition=None) -> Edition | None:
    """Bieżąca edycja **konkursu** albo ``None``. Unikalność ``is_current`` pilnuje constraint.

    ``None`` w argumencie znaczy „konkurs z kontekstu” – w żądaniu ustawia go
    ``CompetitionMiddleware``, w zadaniu Celery ``competition_context``, a w instalacji
    jednokonkursowej wychodzi na to samo, co dotąd (``apps.competitions.scoping``).

    ``None`` w wyniku znaczy „nie wiadomo, o który konkurs chodzi” i jest **odpowiedzią pustą**,
    a nie zaproszeniem do wzięcia pierwszej edycji z brzegu: w bazie wielokonkursowej pierwsza
    z brzegu jest cudza, a strona główna konkursu A pokazywałaby wtedy harmonogram konkursu B.

    Sygnatura ma argument **opcjonalny**, choć § 3.5 dokumentu wymaga go dla ``for_user``.
    Różnica jest zamierzona: ``for_user`` rozstrzyga **widoczność cudzych danych**, a ta funkcja
    czyta pojedynczy wiersz konfiguracji rocznika. Wymuszenie argumentu znaczyłoby poprawkę
    w 54 miejscach naraz (§ 2.4), czyli jedno wydanie z wszystkimi zmianami zamiast czterech –
    dokładnie tego, czego zabrania § 4.1. Wołających po kolei przestawiają T4 i T5.
    """
    return scope_to_competition(Edition.objects.filter(is_current=True), competition).first()


def current_stage(edition: Edition, now=None) -> Stage | None:
    """Etap „na teraz”: otwarty, a jeśli żaden nie jest otwarty – najbliższy przyszły, inaczej ostatni.

    Kolejność jest wyznaczana po ``opens_at``, nie po rodzaju etapu – terminy są jedynym źródłem prawdy.

    Etap treningowy jest **pomijany**. Jest otwarty bez końca (``TRAINING_DEADLINE``), więc gdyby
    wchodził do tej listy, przy każdej przerwie między zawodami zostawałby „etapem bieżącym” –
    strona główna odliczałaby do roku 2099, a pulpit uczestnika pokazywałby trening zamiast
    najbliższych zawodów. Trening ma własne wejście: ``training_stage``.
    """
    now = now or timezone.now()
    stages = [stage for stage in edition.stages.order_by("opens_at", "id") if not stage.is_training]
    if not stages:
        return None
    for stage in stages:
        if stage.is_open_for_submissions(now):
            return stage
    upcoming = [stage for stage in stages if stage.opens_at > now]
    if upcoming:
        return upcoming[0]
    return stages[-1]


def training_stage(edition: Edition | None) -> Stage | None:
    """Etap treningowy edycji albo ``None``. Osobne wejście, bo trening nie jest „etapem bieżącym”.

    Bez argumentu ``now``, w odróżnieniu od ``current_stage``: trening nie ma terminu, więc zegar
    niczego tu nie rozstrzyga – piaskownica jest otwarta, dopóki koordynator jej nie zamknie.
    Unikalne (edycja, rodzaj) gwarantuje, że ``first()`` nie ukrywa drugiego etapu.
    """
    if edition is None:
        return None
    return edition.stages.filter(kind=StageKind.TRAINING).first()


@transaction.atomic
def create_stage(
    *,
    edition: Edition,
    kind: str,
    opens_at,
    deadline_at,
    review_deadline_at,
    appeal_window_opens_at,
    appeal_window_closes_at,
    # Domyślne dwa tygodnie na jedną recenzję. Wartość jest tu **jawna**, a nie brana z modelu:
    # formularz etapu przysyła to pole zawsze, a wywołania z shella mają widzieć, co ustawiają.
    review_deadline_days: int = DEFAULT_REVIEW_DEADLINE_DAYS,
    grace_seconds: int = 0,
    location: str = "",
    event_starts_on=None,
    event_ends_on=None,
    name: str = "",
    format: str = StageFormat.SUBMISSIONS,
    # Pokój wideo rozmowy kwalifikacyjnej (``apps.competitions.video``). Domyślne „bez wideo”
    # i domyślny serwer, bo etap pisemny żadnego pokoju nie potrzebuje – ustawia to koordynator
    # dopiero wtedy, gdy nadaje etapowi formę rozmowy.
    video_provider: str = VideoProvider.NONE,
    video_base_url: str = DEFAULT_VIDEO_BASE_URL,
    scoring_values: list[dict] | None = None,
    max_value: int | None = None,
    qualification_mode: str = QualificationMode.MIN_POINTS,
    min_points: int | None = 0,
    top_n: int | None = None,
) -> Stage:
    """Tworzy etap wraz z domyślną skalą punktacji i progiem kwalifikacji.

    Obiekty zależne powstają tu, a nie w sygnale ``post_save`` – dzięki temu fabryki testowe
    tworzą dokładnie to, co deklarują, a zachowanie serwisu jest jawne i testowalne.
    """
    stage = Stage(
        edition=edition,
        kind=kind,
        name=name,
        format=format,
        location=location,
        opens_at=opens_at,
        deadline_at=deadline_at,
        grace_seconds=grace_seconds,
        event_starts_on=event_starts_on,
        event_ends_on=event_ends_on,
        review_deadline_at=review_deadline_at,
        review_deadline_days=review_deadline_days,
        appeal_window_opens_at=appeal_window_opens_at,
        appeal_window_closes_at=appeal_window_closes_at,
        video_provider=video_provider or VideoProvider.NONE,
        video_base_url=video_base_url or DEFAULT_VIDEO_BASE_URL,
    )
    stage.full_clean()
    stage.save()

    values = scoring_values if scoring_values is not None else default_scoring_values()
    scale = ScoringScale(
        stage=stage,
        values=values,
        max_value=max_value if max_value is not None else DEFAULT_MAX_VALUE,
    )
    scale.full_clean()
    scale.save()

    rule = QualificationRule(stage=stage, mode=qualification_mode, min_points=min_points, top_n=top_n)
    rule.full_clean()
    rule.save()
    return stage


def ensure_stage_defaults(stage: Stage) -> None:
    """Dopina domyślną skalę i próg do etapu utworzonego z pominięciem ``create_stage`` (np. admin)."""
    ScoringScale.objects.get_or_create(
        stage=stage, defaults={"values": default_scoring_values(), "max_value": DEFAULT_MAX_VALUE}
    )
    QualificationRule.objects.get_or_create(
        stage=stage, defaults={"mode": QualificationMode.MIN_POINTS, "min_points": 0}
    )


# --- skala punktacji --------------------------------------------------------------------------


def _stage_allowed_values(stage: Stage) -> set[int]:
    """Oceny dopuszczalne przez skalę etapu albo pusty zbiór, gdy etap skali nie ma."""
    scale = getattr(stage, "scoring_scale", None)
    return scale.allowed_values() if scale is not None else set()


def scores_in_use(stage: Stage, *, problem: Problem | None = None) -> set[int]:
    """Oceny, które **już padły** w tym etapie: punkty recenzji i oceny końcowe.

    Bez ``problem`` pytamy wyłącznie o zadania **dziedziczące** skalę etapu – zadanie z własną
    skalą nie jest przez skalę etapu rządzone, więc jego oceny nie mogą blokować zmiany w etapie.
    Z ``problem`` pytamy o to jedno zadanie.

    Recenzje anulowane też się liczą. Ich punkty nie wchodzą do oceny końcowej, ale zostają
    w tabeli przydziałów jako historia – wartość spoza skali wyglądałaby tam na uszkodzone dane,
    a koordynator nie ma jak jej poprawić (recenzji anulowanej się nie edytuje).

    Import jest lokalny: ``apps.grading`` zależy od ``apps.competitions``, więc zależność w drugą
    stronę na poziomie modułu byłaby cyklem przy starcie aplikacji.
    """
    from apps.grading.models import FinalGrade, Review

    reviews = Review.objects.filter(submission__entry__stage=stage, score__isnull=False)
    grades = FinalGrade.objects.filter(submission__entry__stage=stage)
    if problem is not None:
        reviews = reviews.filter(submission__problem=problem)
        grades = grades.filter(submission__problem=problem)
    else:
        reviews = reviews.filter(submission__problem__scoring_values__isnull=True)
        grades = grades.filter(submission__problem__scoring_values__isnull=True)
    return set(reviews.values_list("score", flat=True)) | set(grades.values_list("score", flat=True))


def assert_scale_covers_existing_scores(used: set[int], allowed: set[int], *, subject: str) -> None:
    """Odmawia zdjęcia ze skali wartości, którą ktoś już komuś wystawił.

    Dokładanie wartości i zmiana etykiet są zawsze wolne – nic nie unieważniają. Usunięcie
    wartości już wystawionej zostawiłoby w bazie oceny spoza skali: tabela wyników liczyłaby się
    z nich dalej, a koordynator zobaczyłby w formularzu listę bez tej wartości i nie miałby jak
    wybrać tego, co faktycznie stoi w recenzji. Dlatego jest to ``409``, a nie ostrzeżenie.
    """
    orphaned = sorted(used - allowed)
    if orphaned:
        raise DomainError(
            f"Wartości {', '.join(str(value) for value in orphaned)} są już wystawione w ocenach "
            f"({subject}) – nie można ich usunąć ze skali. Najpierw popraw te oceny.",
            "SCALE_LOCKED",
            status.HTTP_409_CONFLICT,
        )


@transaction.atomic
def set_scoring_scale(stage: Stage, values, max_value, *, actor, request=None) -> ScoringScale:
    """Zapisuje skalę punktacji etapu – tworząc ją, jeśli etapu jeszcze jej nie ma.

    Etap bez skali jest stanem do naprawienia (nie da się w nim ocenić ani jednej pracy), więc
    ekran „Skala punktacji” musi umieć go naprawić, a nie tylko odmówić edycji.

    Audyt (``stage.scale_updated``) notuje pełną skalę przed i po. To jedyne dane, po których da
    się później odtworzyć, według jakiej skali zapadły oceny z danego dnia – a skala nie zawiera
    niczego osobowego, więc wchodzi do ``diff`` w całości.
    """
    from apps.core.models import audit

    scale = ScoringScale.objects.select_for_update().filter(stage=stage).first()
    before = (
        {"values": scale.values, "max_value": scale.max_value}
        if scale is not None
        else {"values": None, "max_value": None}
    )
    if scale is None:
        scale = ScoringScale(stage=stage)
    scale.values = values
    scale.max_value = max_value
    try:
        scale.full_clean()
    except ValidationError as exc:
        raise DomainError(
            "; ".join(exc.messages), "SCORING_SCALE_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    assert_scale_covers_existing_scores(
        scores_in_use(stage), scale.allowed_values(), subject=f"etap {stage.display_name}"
    )
    scale.save()
    audit(
        actor,
        "stage.scale_updated",
        stage,
        {"from": before, "to": {"values": scale.values, "max_value": scale.max_value}},
        request=request,
    )
    return scale


def _registration_closed() -> DomainError:
    return DomainError(
        "Rejestracja do tego etapu jest zamknięta.",
        "REGISTRATION_CLOSED",
        status.HTTP_403_FORBIDDEN,
    )


def _already_registered() -> DomainError:
    return DomainError(
        "Uczestnik jest już zarejestrowany do tego etapu.",
        "ALREADY_REGISTERED",
        status.HTTP_409_CONFLICT,
    )


#: Etapy, do których uczestnik zapisuje się **sam**. Eliminacje są pierwszym etapem zawodów, a
#: trening piaskownicą poza zawodami – w obu wypadkach nie ma poprzedniego etapu, z którego mogłaby
#: przyjść kwalifikacja, więc jedyną drogą wejścia jest własne zgłoszenie.
SELF_REGISTRATION_KINDS = (StageKind.ELIM, StageKind.TRAINING)


@transaction.atomic
def register_for_stage(participant: Participant, stage: Stage, *, now=None) -> StageEntry:
    """Samodzielna rejestracja uczestnika do etapu eliminacyjnego albo treningowego.

    Do etapu okręgowego i finału wpisy tworzy wyłącznie kwalifikacja (T-07) – ręczna próba
    kończy się ``STAGE_NOT_OPEN_FOR_REGISTRATION``.
    """
    now = now or timezone.now()
    if stage.kind not in SELF_REGISTRATION_KINDS:
        raise DomainError(
            "Do tego etapu wpisy tworzy kwalifikacja, nie rejestracja.",
            "STAGE_NOT_OPEN_FOR_REGISTRATION",
            status.HTTP_403_FORBIDDEN,
        )
    if not stage.edition.is_current or not stage.is_open_for_submissions(now):
        # Etap edycji archiwalnej nie przyjmuje rejestracji, nawet jeśli jego terminy są "otwarte".
        raise _registration_closed()
    if StageEntry.objects.filter(participant=participant, stage=stage).exists():
        raise _already_registered()
    try:
        # Savepoint: kolizja unikalności przy wyścigu nie może unieważnić całej transakcji żądania.
        with transaction.atomic():
            return StageEntry.objects.create(
                participant=participant, stage=stage, status=StageEntryStatus.REGISTERED
            )
    except IntegrityError as exc:
        raise _already_registered() from exc


def entries_for_user(user, competition=None):
    """Wpisy widoczne dla użytkownika. Filtr jest w querysecie, nie w widoku (PROJEKT.md 2.3).

    ``select_related("stage__edition")`` stoi tu od początku i po zakresowaniu robi za drugie:
    filtr ``stage__edition__competition`` korzysta z tego samego złączenia, więc liczba zapytań
    nie rośnie ani o jedno.
    """
    return (
        StageEntry.objects.select_related("stage", "stage__edition", "participant")
        .for_user(user, competition)
        .order_by("stage__opens_at", "id")
    )


# --- zarządzanie etapami z panelu koordynatora ------------------------------------------------


def missing_stage_kinds(edition: Edition) -> list[tuple[str, str]]:
    """Rodzaje etapów, których edycja jeszcze nie ma – lista wyboru przy dodawaniu etapu.

    Para (edycja, rodzaj) jest unikalna w bazie, więc formularz z pełną listą kończyłby się
    ``IntegrityError`` przy próbie dołożenia rodzaju, który edycja już ma. Kolejność jest
    kolejnością z ``StageKind``, więc „Trening” stoi na końcu listy – za etapami zawodów.
    """
    taken = set(Stage.objects.filter(edition=edition).values_list("kind", flat=True))
    return [(value, label) for value, label in StageKind.choices if value not in taken]


def stage_has_submissions(stage: Stage) -> bool:
    """Czy do etapu wpłynęło choć jedno rozwiązanie (dowolnej wersji i dowolnego statusu).

    Import jest lokalny: ``apps.submissions`` zaciąga ``apps.competitions`` przy starcie, więc
    zależność w drugą stronę na poziomie modułu byłaby cyklem.
    """
    from apps.submissions.models import Submission

    return Submission.objects.filter(entry__stage=stage).exists()


def stage_has_interview_bookings(stage: Stage) -> bool:
    """Czy ktokolwiek zapisał się już na rozmowę w tym etapie."""
    return InterviewBooking.objects.filter(slot__stage=stage).exists()


def slots_outside_window(stage: Stage, opens_at, deadline_at) -> bool:
    """Czy któryś termin rozmowy wypadłby poza oknem ``[opens_at, deadline_at]``.

    Pytanie zadajemy przez negację (``exclude`` warunku „mieści się w całości”), bo termin
    wystający oknem choćby o minutę – z jednej albo z drugiej strony – jest tak samo zły:
    ``create_slots`` nie pozwoliłby go założyć, więc przesunięcie okna nie może go po cichu
    stworzyć.
    """
    return (
        InterviewSlot.objects.filter(stage=stage)
        .exclude(starts_at__gte=opens_at, ends_at__lte=deadline_at)
        .exists()
    )


def _audit_value(value):
    """Wartość do wpisu audytowego: daty w ISO, reszta bez zmian (``diff`` jest JSON-em).

    ``date`` łapie się przed ``datetime`` w jednym warunku, bo ``datetime`` jest jego podklasą:
    oba mają ``isoformat()``, a rozróżnienie i tak wychodzi w zapisie („2027-06-04” kontra
    „2027-06-04T09:00:00+02:00”). Bez tego dzień wydarzenia wysadzałby serializację ``diff``.
    """
    if isinstance(value, date):
        return value.isoformat()
    return value


def _stage_closed_error(fields: list[str]) -> DomainError:
    return DomainError(
        "Etap jest zamknięty – można w nim zmienić już tylko termin recenzji, okno reklamacji "
        f"i miejsce zawodów. Zablokowane pola: {', '.join(fields)}.",
        "STAGE_CLOSED",
        status.HTTP_409_CONFLICT,
    )


@transaction.atomic
def update_stage(stage: Stage, actor, *, request=None, now=None, **fields) -> Stage:
    """Zmiana osi czasu etapu z panelu koordynatora. Zwraca etap po zapisie.

    Reguły, których nie da się wyrazić w ``Stage.clean()``, bo zależą od stanu innych tabel
    i od zegara:

    - **etap zamknięty** (``closed_at``) przyjmuje już tylko terminy, które go poprzedzają w czasie:
      recenzje, okno reklamacji i miejsce (patrz ``STAGE_FIELDS_EDITABLE_AFTER_CLOSE``),
    - **deadline nie cofa się w przeszłość, gdy są zgłoszenia.** Uczestnik, który oddał pracę
      zgodnie z ogłoszonym terminem, nie może po fakcie znaleźć się „po deadline”; a gdyby etap
      zamknął się w tej samej chwili, jego wersje dostałyby ``LOCKED`` z datą, której nie było
      na stronie w chwili uploadu,
    - **formy nie zmienia się w etapie, który już się toczy.** Przestawienie „rozwiązania pisemne”
      na „rozmowa” w etapie z oddanymi pracami unieważniałoby te prace bez śladu, a odwrotna
      zmiana zostawiałaby zapisy na terminy, których nikt już nie obsłuży. Tak samo blokują
      **zadania**: etap w formie rozmowy zadań mieć nie może (``create_problem`` odmawia), więc
      zmiana formy zostawiłaby arkusz-sierotę, niewidoczny w panelu i nieusuwalny z niego,
    - **okna etapu nie zawęża się pod wyznaczonymi terminami rozmów.** Termin poza oknem etapu to
      godzina, której nie ma na harmonogramie – a uczestnik zapisany na 9:00 nie dowiedziałby się,
      że etap „kończy się” o 8:00.

    Kolejność terminów sprawdza ``full_clean()`` – ta sama reguła, co w formularzu i w bazie.
    Blokada ``select_for_update`` szereguje dwa równoległe zapisy: bez niej druga transakcja
    czytałaby stan sprzed pierwszej i zapisywała diff względem nieaktualnych wartości.
    """
    now = now or timezone.now()
    unknown = sorted(set(fields) - set(STAGE_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu edycji etapu: {', '.join(unknown)}.")

    locked = Stage.objects.select_for_update().get(pk=stage.pk)
    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    if not changed:
        return locked

    if locked.closed_at is not None:
        blocked = sorted(set(changed) - set(STAGE_FIELDS_EDITABLE_AFTER_CLOSE))
        if blocked:
            raise _stage_closed_error(blocked)

    if "deadline_at" in changed and changed["deadline_at"] < now and stage_has_submissions(locked):
        raise DomainError(
            "Do etapu wpłynęły już rozwiązania – terminu oddania nie można cofnąć w przeszłość. "
            "Wybierz termin w przyszłości albo zamknij etap ręcznie.",
            "STAGE_DEADLINE_IN_PAST",
            status.HTTP_409_CONFLICT,
        )

    if "format" in changed and (
        stage_has_submissions(locked) or stage_has_interview_bookings(locked) or locked.problems.exists()
    ):
        raise DomainError(
            "Etap ma już oddane rozwiązania, zapisy na rozmowy albo zadania – formy nie można "
            "zmienić. Utwórz nowy etap, jeżeli zawody mają się odbyć inaczej.",
            "STAGE_FORMAT_LOCKED",
            status.HTTP_409_CONFLICT,
        )

    if locked.is_interview and ("opens_at" in changed or "deadline_at" in changed):
        new_opens = changed.get("opens_at", locked.opens_at)
        new_deadline = changed.get("deadline_at", locked.deadline_at)
        if slots_outside_window(locked, new_opens, new_deadline):
            raise DomainError(
                "Etap ma wyznaczone terminy rozmów, które nie zmieściłyby się w nowym oknie "
                f"({timezone.localtime(new_opens):%Y-%m-%d %H:%M} – "
                f"{timezone.localtime(new_deadline):%Y-%m-%d %H:%M}, czas polski). Najpierw usuń "
                "albo przenieś te terminy.",
                "STAGE_WINDOW_HAS_SLOTS",
                status.HTTP_409_CONFLICT,
            )

    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    locked.full_clean()
    locked.save(update_fields=list(changed))

    from apps.core.models import audit

    audit(actor, "stage.updated", locked, diff, request=request)
    for name, value in changed.items():
        setattr(stage, name, value)
    return locked


# --- ramy czasowe edycji: okno rejestracji i retencja danych ------------------------------------


@transaction.atomic
def update_registration_window(edition: Edition, *, actor, request=None, **fields) -> Edition:
    """Zmiana ram czasowych edycji z panelu koordynatora. Zwraca edycję po zapisie.

    Reguła jest jedna i wyrażalna w modelu (otwarcie przed zamknięciem), więc pilnuje jej
    ``full_clean()`` – ten sam warunek, co constraint w bazie i co komunikat pod polem formularza.
    Serwis dokłada do tego trzy rzeczy, których formularz dać nie może: blokadę wiersza (dwa
    równoległe zapisy nie mogą policzyć różnicy względem nieaktualnego stanu), wpis audytowy
    z różnicą pól i jedno wejście dla ewentualnych innych wywołujących.

    Wyłączenie rejestracji **nie rusza** zapisanych terminów: koordynator, który zatrzymuje zapisy
    na godzinę, ma po ponownym włączeniu odzyskać to samo okno, a nie puste pola.

    Zakres pól to ``EDITION_EDITABLE_FIELDS``, czyli okno rejestracji **i** okres retencji danych.
    Retencja jedzie tą samą drogą, bo jest tą samą decyzją o ramach czasowych edycji i ma zostawić
    taki sam ślad w audycie – osobny serwis dla jednej liczby byłby drugą kopią blokady wiersza,
    walidacji i wpisu audytowego.
    """
    unknown = sorted(set(fields) - set(EDITION_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu ustawień edycji: {', '.join(unknown)}.")

    locked = Edition.objects.select_for_update().get(pk=edition.pk)
    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    if not changed:
        return locked

    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    try:
        locked.full_clean()
    except ValidationError as exc:
        raise DomainError(
            "; ".join(exc.messages), "REGISTRATION_WINDOW_INVALID", status.HTTP_400_BAD_REQUEST
        ) from exc
    locked.save(update_fields=list(changed))

    from apps.core.models import audit

    audit(actor, "edition.registration_updated", locked, diff, request=request)
    for name, value in changed.items():
        setattr(edition, name, value)
    return locked


# --- zarządzanie zadaniami z panelu koordynatora ----------------------------------------------


def _duplicate_number(number) -> DomainError:
    return DomainError(
        f"Zadanie o numerze {number} już istnieje w tym etapie.",
        "PROBLEM_NUMBER_TAKEN",
        status.HTTP_409_CONFLICT,
    )


def _validation_error(exc: ValidationError) -> DomainError:
    """``ValidationError`` modelu → błąd domenowy z czytelnym komunikatem dla panelu."""
    return DomainError("; ".join(exc.messages), "PROBLEM_INVALID", status.HTTP_400_BAD_REQUEST)


@transaction.atomic
def create_problem(
    *, stage: Stage, actor, statement=None, statement_en=None, model_solution=None, request=None, **fields
) -> Problem:
    """Nowe zadanie etapu. ``statement`` i ``model_solution`` to pliki z formularza albo ``None``.

    Etap w formie rozmowy zadań nie ma i mieć nie może: nie ma czego oddać, więc arkusz zadań
    byłby treścią bez odbiorcy, a uczestnik zobaczyłby w panelu upload, którego serwis i tak by
    nie przyjął (``submissions.create_submission``). Odmowa jest jednym warunkiem – lepsza niż
    ukrywanie przycisku, bo to samo żądanie wysłane skryptem też musi dostać 409.
    """
    unknown = sorted(set(fields) - set(PROBLEM_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu zadania: {', '.join(unknown)}.")
    if stage.is_interview:
        raise DomainError(
            "Ten etap ma formę rozmowy kwalifikacyjnej – nie ma w nim zadań do oddania. "
            "Terminy rozmów wyznaczasz na osobnym ekranie.",
            "STAGE_NOT_ACCEPTING_PROBLEMS",
            status.HTTP_409_CONFLICT,
        )

    problem = Problem(stage=stage, **fields)
    if statement is not None:
        problem.statement_pdf = statement
    if statement_en is not None:
        # Angielska treść nie ma własnej bramki – to ten sam dokument w drugim języku i podlega
        # dokładnie tej samej regule widoczności (``opens_at``), co wersja polska.
        problem.statement_pdf_en = statement_en
    if model_solution is not None:
        # Wzorcówka nie ma żadnej bramki czasowej (w przeciwieństwie do treści): nie staje się
        # jawna nigdy, więc wgranie jej przed otwarciem etapu i po nim jest tą samą czynnością.
        problem.model_solution_pdf = model_solution
    try:
        problem.full_clean()
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        # Savepoint: wyścig o ten sam numer nie może unieważnić całej transakcji żądania.
        with transaction.atomic():
            problem.save()
    except IntegrityError as exc:
        raise _duplicate_number(problem.number) from exc

    from apps.core.models import audit

    audit(
        actor,
        "problem.created",
        problem,
        {
            "stage": stage.pk,
            "number": problem.number,
            "allowed_formats": list(problem.allowed_formats or []),
            "max_file_mb": problem.max_file_mb,
            "has_statement": bool(problem.statement_pdf),
            "has_model_solution": bool(problem.model_solution_pdf),
        },
        request=request,
    )
    return problem


@transaction.atomic
def update_problem(
    problem: Problem,
    actor,
    *,
    statement=None,
    statement_en=None,
    model_solution=None,
    confirm_open_stage: bool = False,
    request=None,
    now=None,
    **fields,
) -> Problem:
    """Zmiana zadania. Podmiana treści po otwarciu etapu wymaga jawnego potwierdzenia.

    Uczestnik, który pobrał PDF w pierwszej godzinie etapu, rozwiązuje **tę** wersję zadania:
    cicha podmiana pliku dzieli zawodników na dwie grupy z różnym poleceniem. Potwierdzenie nie
    zabrania operacji (bywa konieczna – literówka w treści), tylko wymusza świadomą decyzję,
    po której koordynator ogłasza erratę.
    """
    now = now or timezone.now()
    unknown = sorted(set(fields) - set(PROBLEM_EDITABLE_FIELDS))
    if unknown:  # pragma: no cover - błąd programisty, nie danych
        raise ValueError(f"Pola spoza zakresu zadania: {', '.join(unknown)}.")

    locked = Problem.objects.select_for_update().select_related("stage").get(pk=problem.pk)
    if statement is not None and locked.stage.has_opened(now) and not confirm_open_stage:
        raise DomainError(
            "Etap jest już otwarty, a uczestnicy widzą treść tego zadania. Podmiana pliku wymaga "
            "potwierdzenia w formularzu.",
            "STATEMENT_CHANGE_NEEDS_CONFIRMATION",
            status.HTTP_400_BAD_REQUEST,
        )

    changed = {name: value for name, value in fields.items() if getattr(locked, name) != value}
    diff = {
        name: {"from": _audit_value(getattr(locked, name)), "to": _audit_value(value)}
        for name, value in changed.items()
    }
    for name, value in changed.items():
        setattr(locked, name, value)
    if statement is not None:
        # Przypisanie nowego pliku do ``FileField`` zapisuje go pod nową nazwą; stary plik zostaje
        # w storage (kasowanie jest poza zakresem – w prywatnym buckecie nic go nie wystawia).
        diff["statement_pdf"] = {"from": locked.statement_pdf.name or "", "to": statement.name}
        locked.statement_pdf = statement
    if statement_en is not None:
        # To samo potwierdzenie, co przy treści polskiej, **nie** jest tu wymagane: angielska
        # wersja jest tłumaczeniem dokładanym zwykle po otwarciu etapu, a wgranie jej nie zmienia
        # polecenia, które uczestnicy już mają. Ślad zostaje w audycie.
        diff["statement_pdf_en"] = {"from": locked.statement_pdf_en.name or "", "to": statement_en.name}
        locked.statement_pdf_en = statement_en
    if model_solution is not None:
        # Podmiana wzorcówki **nie** wymaga potwierdzenia jak podmiana treści: uczestnik nigdy jej
        # nie widział, więc nowa wersja nie dzieli zawodników na dwie grupy. Ślad zostaje w audycie.
        diff["model_solution_pdf"] = {
            "from": locked.model_solution_pdf.name or "",
            "to": model_solution.name,
        }
        locked.model_solution_pdf = model_solution
    if not diff:
        return locked

    try:
        locked.full_clean()
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    if "scoring_values" in diff or "max_points" in diff:
        # Ta sama reguła, co przy skali etapu: wolno dokładać wartości i zmieniać etykiety, nie
        # wolno zdjąć wartości, którą ktoś już wystawił. Dotyczy też wyczyszczenia nadpisania –
        # po powrocie do skali etapu oceny zadania muszą się w niej mieścić.
        allowed = locked.allowed_values() or _stage_allowed_values(locked.stage)
        assert_scale_covers_existing_scores(
            scores_in_use(locked.stage, problem=locked),
            allowed,
            subject=f"zadanie {locked.number}",
        )
    try:
        with transaction.atomic():
            locked.save()
    except IntegrityError as exc:
        raise _duplicate_number(locked.number) from exc

    from apps.core.models import audit

    audit(actor, "problem.updated", locked, diff, request=request)
    return locked


@transaction.atomic
def delete_problem(problem: Problem, actor, *, request=None) -> None:
    """Usunięcie zadania. Możliwe wyłącznie, dopóki nikt nie oddał do niego rozwiązania.

    ``Submission.problem`` jest kluczem z ``CASCADE``, więc skasowanie zadania z pracami zabrałoby
    ze sobą rozwiązania, recenzje i oceny – a te są dowodem przebiegu zawodów.
    """
    from apps.submissions.models import Submission

    if Submission.objects.filter(problem=problem).exists():
        raise DomainError(
            "Do zadania wpłynęły rozwiązania – nie można go usunąć.",
            "PROBLEM_HAS_SUBMISSIONS",
            status.HTTP_409_CONFLICT,
        )

    from apps.core.models import audit

    # Audyt przed skasowaniem: po ``delete()`` obiekt nie ma już ``pk``, więc wpis wskazywałby
    # na ``None`` i nie dałoby się go połączyć z historią zadania.
    audit(
        actor,
        "problem.deleted",
        problem,
        {"stage": problem.stage_id, "number": problem.number, "title": problem.title},
        request=request,
    )
    problem.delete()
