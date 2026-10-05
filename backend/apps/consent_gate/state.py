"""Stan zgód uczestnika w konkursie: czego brakuje – liczone tanio, bo pyta o to każde żądanie panelu.

Jedno pytanie, jedna reguła (``docs/tasks/CONS-01.md`` § 1): zgoda **wymagana od tej osoby**
(``consents.required_kinds`` – zawsze wymagane plus zgoda opiekuna dla małoletniego) jest spełniona
wyłącznie aktywnym wpisem ``ConsentRecord`` z ``document_version`` równą **bieżącej** wersji z zestawu
konkursu (``consents.consent_set``). Zmiana wersji dokumentu znaczy więc „zgoda do ponowienia”,
a poprawka literówki w treści bez zmiany wersji – nic.

Koszt (§ 2). Bramka pyta o stan na każdym żądaniu obszaru uczestnika, więc wynik siedzi w cache'u:

- **stan konta w konkursie** – profil (data urodzenia, rocznik) i zbiór par ``(rodzaj, wersja)``
  aktywnych wpisów; jedno zapytanie przy chybieniu (``ArraySubquery``), 5 minut w cache'u.
  Nie trzymamy w nim *wyniku* („brakuje X”), tylko *dane* – wynik liczy się na żądaniu z bieżącym
  zestawem zgód, więc zmiana wersji działa od następnego żądania, bez czekania na TTL,
- **zestaw zgód** – przy wyłączonej fladze ``per_competition_consents`` stała, zero zapytań; przy
  włączonej osobny wpis cache'a na konkurs.

Unieważnienie robią sygnały (``signals.py``); TTL jest siatką bezpieczeństwa dla tego, czego żaden
sygnał nie zobaczy – osiemnastych urodzin i zapisów z pominięciem ORM-u.

Wartości w cache'u to krotki typów wbudowanych i dataklasa ``Consent`` (zamrożona, z modułu, który
się nie przenosi) – serializator cache'a to pickle (``config/settings/base.py``), a stan zapisany
przez poprzednie wydanie ma się dać odczytać po wdrożeniu albo zniknąć bez wyjątku.
"""

from __future__ import annotations

import logging
from datetime import date

from django.core.cache import cache

from apps.accounts.consents import CONSENT_FEATURE_FLAG, Consent, consent_set, required_kinds

logger = logging.getLogger(__name__)

#: Prefiks kluczy. Wersja w nazwie: zmiana kształtu wartości = nowy prefiks, a stare wpisy
#: wygasają same, zamiast wywracać odczyt po wdrożeniu.
CACHE_PREFIX = "consent_gate:v1"
#: Czas życia stanu konta. Krótki, bo to siatka bezpieczeństwa (urodziny, zapis z pominięciem ORM-u),
#: a nie główna droga unieważnienia – tą są sygnały.
STATE_TTL = 300
#: Czas życia zestawu zgód konkursu z flagą ``per_competition_consents``.
DEFINITIONS_TTL = 300
#: Separator rodzaju i wersji w parze zwracanej przez bazę. Rodzaj jest zamkniętą listą
#: (``ConsentKind``) bez tego znaku, więc dzielimy po **pierwszym** wystąpieniu.
PAIR_SEPARATOR = "\x1f"

#: Stan „to konto nie ma profilu uczestnika w tym konkursie” – też trzymany w cache'u, żeby
#: koordynator zaglądający na forum nie płacił zapytaniem przy każdym żądaniu.
NO_PROFILE = (None, None, None, frozenset())


def state_key(competition_id: int, user_id: int) -> str:
    return f"{CACHE_PREFIX}:state:{competition_id}:{user_id}"


def definitions_key(competition_id: int) -> str:
    return f"{CACHE_PREFIX}:defs:{competition_id}"


def consents_for(competition) -> tuple[Consent, ...]:
    """Zestaw zgód konkursu – bez zapytania przy wyłączonej fladze, z cache'a przy włączonej."""
    if not competition.has_feature(CONSENT_FEATURE_FLAG):
        return consent_set(competition)
    key = definitions_key(competition.pk)
    consents = cache.get(key)
    if consents is None:
        consents = tuple(consent_set(competition))
        cache.set(key, consents, DEFINITIONS_TTL)
    return consents


def records_subquery():
    """Pary ``rodzaj<sep>wersja`` aktywnych wpisów profilu – jako tablica w **tym samym** zapytaniu.

    ``ArraySubquery`` zamiast ``prefetch_related``: prefetch to drugie zapytanie, a budżet bramki
    to jedno (§ 2). Kolejność wpisów nie ma znaczenia (zbiór), więc ``order_by()`` zdejmuje
    domyślne sortowanie modelu z podzapytania.
    """
    from django.contrib.postgres.expressions import ArraySubquery
    from django.db.models import CharField, OuterRef, Value
    from django.db.models.functions import Concat

    from apps.accounts.models import ConsentRecord

    return ArraySubquery(
        ConsentRecord.objects.filter(participant=OuterRef("pk"), withdrawn_at__isnull=True)
        .order_by()
        .annotate(pair=Concat("kind", Value(PAIR_SEPARATOR), "document_version", output_field=CharField()))
        .values("pair")
    )


def pairs(raw) -> frozenset[tuple[str, str]]:
    """Tablica z bazy → zbiór par ``(rodzaj, wersja)``."""
    result = set()
    for item in raw or ():
        kind, _sep, version = (item or "").partition(PAIR_SEPARATOR)
        result.add((kind, version))
    return frozenset(result)


def _load(user_id: int, competition_id: int) -> tuple:
    from apps.accounts.models import Participant

    row = (
        Participant.objects.filter(user_id=user_id, competition_id=competition_id)
        .annotate(consent_pairs=records_subquery())
        .values_list("pk", "birth_date", "birth_year", "consent_pairs")
        .first()
    )
    if row is None:
        return NO_PROFILE
    pk, birth_date, birth_year, raw = row
    return (pk, birth_date, birth_year, pairs(raw))


def participant_state(user, competition) -> tuple:
    """``(id profilu | None, data urodzenia, rocznik, pary)`` – z cache'a albo jednym zapytaniem."""
    key = state_key(competition.pk, user.pk)
    state = cache.get(key)
    if state is None:
        state = _load(user.pk, competition.pk)
        cache.set(key, state, STATE_TTL)
    return state


def missing_from(
    consents: tuple[Consent, ...],
    birth_date: date | None,
    birth_year: int | None,
    records: frozenset[tuple[str, str]],
) -> tuple[Consent, ...]:
    """Wymagane zgody bez aktywnego wpisu **bieżącej** wersji – w kolejności zestawu.

    Regułę „co jest wymagane” trzyma ``consents.required_kinds`` (z ``is_minor`` w środku); tu
    zostaje wyłącznie porównanie z dowodami.
    """
    required = set(required_kinds(birth_date, birth_year, consents=consents))
    return tuple(
        consent
        for consent in consents
        if consent.kind in required and (consent.kind, consent.version) not in records
    )


def missing_consents(user, competition) -> tuple[Consent, ...] | None:
    """Brakujące zgody konta w konkursie; ``None`` – konto nie jest tu uczestnikiem."""
    found = gap(user, competition)
    return None if found is None else found[0]


def gap(user, competition) -> tuple[tuple[Consent, ...], bool, int] | None:
    """``(brakujące zgody, czy to wyłącznie ponowienia, id profilu)``; ``None`` – nie uczestnik tutaj.

    „Ponowienie” to brak, przy którym uczestnik ma aktywną zgodę **tego samego rodzaju** pod inną
    wersją – czyli organizator zmienił dokument, a nie: uczestnik nigdy się nie zgodził. Ponowienie
    przepuszcza zapis pracy w toku bez dalszych pytań (CONS-01 H1, ``middleware.WORK_IN_PROGRESS_VIEWS``);
    brak „nowy” – dopiero po sprawdzeniu, że uczestnik ma wpis w otwartym etapie (:func:`has_open_work`).
    """
    participant_id, birth_date, birth_year, records = participant_state(user, competition)
    if participant_id is None:
        return None
    missing = missing_from(consents_for(competition), birth_date, birth_year, records)
    consented_kinds = {kind for kind, _version in records}
    return missing, all(consent.kind in consented_kinds for consent in missing), participant_id


def has_open_work(participant_id: int, *, now=None) -> bool:
    """Czy uczestnik ma wpis w **trwającym** etapie – pracę, której zmiana zestawu zgód nie przerwie.

    Trwający = otwarty (``opens_at`` minął), niezamknięty (``closed_at`` puste) i jeszcze coś przyjmuje:
    okno oddawania z tolerancją (``deadline_at + grace_seconds``), okno reklamacji albo podejście do testu
    w toku. Jedno zapytanie, liczone **wyłącznie** dla widoków zapisu pracy i wyłącznie przy braku, który
    nie jest ponowieniem (nowa wymagana zgoda w trakcie etapu, decyzja koordynatora po przeglądzie #97) –
    reszta ruchu go nie płaci. Granice okien egzekwują i tak same widoki; tu chodzi wyłącznie o to, czy
    uczestnik był już w zawodach, zanim zestaw zgód się zmienił.
    """
    from datetime import timedelta

    from django.db.models import (
        DateTimeField,
        DurationField,
        Exists,
        ExpressionWrapper,
        F,
        OuterRef,
        Q,
        Value,
    )
    from django.utils import timezone

    from apps.competitions.models import StageEntry
    from apps.quiz.models import AttemptStatus, QuizAttempt

    now = now or timezone.now()
    grace = ExpressionWrapper(
        F("stage__grace_seconds") * Value(timedelta(seconds=1), output_field=DurationField()),
        output_field=DurationField(),
    )
    attempt_in_progress = QuizAttempt.objects.filter(entry=OuterRef("pk"), status=AttemptStatus.IN_PROGRESS)
    return (
        StageEntry.objects.filter(
            participant_id=participant_id, stage__closed_at__isnull=True, stage__opens_at__lte=now
        )
        .annotate(
            submissions_end=ExpressionWrapper(F("stage__deadline_at") + grace, output_field=DateTimeField())
        )
        .filter(
            Q(submissions_end__gt=now)
            | Q(stage__appeal_window_opens_at__lte=now, stage__appeal_window_closes_at__gt=now)
            | Exists(attempt_in_progress)
        )
        .exists()
    )


def previous_versions(records: frozenset[tuple[str, str]]) -> dict[str, list[str]]:
    """Wersje, pod którymi uczestnik złożył zgodę danego rodzaju – do zdania „zaakceptowano wersję X”."""
    found: dict[str, list[str]] = {}
    for kind, version in records:
        found.setdefault(kind, []).append(version)
    return {kind: sorted(versions) for kind, versions in found.items()}


def forget_state(competition_id: int, user_id: int) -> None:
    """Unieważnia stan konta w konkursie. Awaria cache'a nie może wywrócić zapisu zgody."""
    try:
        cache.delete(state_key(competition_id, user_id))
    except Exception:  # noqa: BLE001 - zapis zgody ważniejszy niż cache; TTL dokończy sprzątanie
        logger.warning("Bramka zgód: nie udało się unieważnić stanu %s/%s.", competition_id, user_id)


def forget_definitions(competition_id: int) -> None:
    try:
        cache.delete(definitions_key(competition_id))
    except Exception:  # noqa: BLE001 - jw.
        logger.warning("Bramka zgód: nie udało się unieważnić zestawu zgód konkursu %s.", competition_id)
