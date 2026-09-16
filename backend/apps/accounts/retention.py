"""Retencja danych osobowych uczestników: kiedy edycja przestaje być powodem do ich trzymania.

Art. 5 ust. 1 lit. e RODO (ograniczenie przechowywania) mówi, że dane wolno trzymać nie dłużej,
niż wymaga tego cel przetwarzania. Celem jest **przeprowadzenie zawodów** – a te kończą się
wynikami, oknem reklamacji i wydaniem dyplomów. Dopóki tego modułu nie było, konto założone do
pierwszej edycji leżało w bazie bez terminu: nikt go nie kasował, bo nikt nie wiedział, kiedy
wolno, a „na wszelki wypadek” jest w RODO odpowiedzią najgorszą z możliwych.

Jak liczymy termin: **ostatni deadline etapu edycji + ``Edition.data_retention_months``**. Nie
data utworzenia edycji (bo edycja żyje rok) i nie data publikacji wyników (bo ta bywa przesuwana
i bywa cofana). Deadline ostatniego etapu jest momentem, po którym w zawodach nie dzieje się już
nic, co zależałoby od danych uczestnika – reszta to ocenianie, reklamacje i dokumentacja, na które
właśnie ustawia się okres retencji.

Czego ten moduł **nie** kasuje i dlaczego:

- **kont z otwartą reklamacją.** Sprawa w toku jest podstawą przetwarzania sama w sobie: komisja
  odwoławcza rozstrzyga ją w oparciu o pracę i o to, czyja ona jest. Anonimizacja w środku sprawy
  zabrałaby uczestnikowi możliwość dowiedzenia się, jak ją rozstrzygnięto,
- **kont z nieogłoszonymi wynikami.** Etap bez ``results_published_at`` znaczy, że zawody nie
  zostały domknięte – a skoro nie zostały, to termin retencji policzony z ich deadline'u jest
  liczbą bez pokrycia. Taki wiersz zostaje i czeka, aż organizator skończy sprawę,
- **kont, które startują w edycji późniejszej.** Uczestnik trzeciej klasy wraca w kolejnym roku
  i to jego konto, a nie nowe – zabranie mu danych osobowych w trakcie drugiego startu byłoby
  anonimizacją czynnego uczestnika,
- **kont komitetu i koordynatora.** Recenzent nie jest osobą, której dane zbieramy „do zawodów
  rocznika X”: jego konto jest kontem funkcyjnym, żyje między edycjami i odpowiada za nie
  organizator. Retencja dotyczy uczestników.

Co zostaje po anonimizacji: dokładnie to, co przy żądaniu z art. 17 – pseudonimowy wiersz
uczestnika (``public_code``, województwo) i cała dokumentacja zawodów. Robi to jedna i ta sama
funkcja ``apps.accounts.profile.anonymise_account``; ten moduł jedynie **wybiera**, czyje konta
i kiedy. Dwie implementacje wycierania danych to prędzej czy później dwie różne odpowiedzi na
pytanie „co dokładnie zniknęło”.

Wykonawcą jest ``None``: anonimizacji nie żąda ani właściciel konta, ani koordynator – wynika
z upływu terminu, który organizator ustawił wcześniej. Dlatego każde konto dostaje **własny**
wpis ``account.anonymised_by_retention`` z identyfikatorem edycji: bez niego w aktach zostawałby
sam skutek („dane zniknęły”), bez powodu.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime

from celery import shared_task
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import audit

from .models import Participant, User

logger = logging.getLogger(__name__)


def add_months(moment: datetime, months: int) -> datetime:
    """``moment`` przesunięty o ``months`` miesięcy, z przycięciem dnia do długości miesiąca.

    Własna arytmetyka, bo ``timedelta`` miesięcy nie zna, a 30 dni to nie miesiąc: przy 24
    miesiącach różnica narosłaby do kilkunastu dni i termin retencji przestałby odpowiadać
    zdaniu „dwa lata od ostatniego etapu”. Przycięcie dnia (31 stycznia + 1 miesiąc → 28 lutego)
    jest tą samą regułą, którą stosują biblioteki kalendarzowe – dzień 31 w lutym nie istnieje,
    a przeniesienie go na 3 marca wydłużałoby okres retencji bez powodu.
    """
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def retention_deadline(edition, *, last_deadline: datetime | None = None) -> datetime | None:
    """Moment, po którym dane uczestników tej edycji tracą podstawę przechowywania.

    ``None`` znaczy „nie ma terminu”: edycja bez etapów (nic się w niej nie odbyło) albo
    z retencją ustawioną na zero (organizator świadomie wyłączył automat dla tego rocznika).

    ``last_deadline`` można podać z zewnątrz, żeby ekran koordynatora policzył terminy wszystkich
    edycji jednym zapytaniem z ``annotate`` zamiast jednego zapytania na wiersz tabeli.
    """
    months = edition.data_retention_months or 0
    if months <= 0:
        return None
    if last_deadline is None:
        last_deadline = edition.stages.aggregate(last=Max("deadline_at"))["last"]
    if last_deadline is None:
        return None
    return add_months(last_deadline, months)


def expired_editions(now=None) -> list:
    """Edycje, którym upłynął okres retencji – bez bieżącej i bez tych bez terminu.

    Edycja bieżąca jest wykluczona **bezwarunkowo**, a nie przez sam termin: gdyby organizator
    ustawił rocznikowi retencję krótszą niż jego własny kalendarz, automat anonimizowałby
    uczestników trwających właśnie zawodów. Ten błąd jest łatwy do popełnienia i nieodwracalny.

    Terminy liczymy w Pythonie, a nie w SQL-u: arytmetyka miesięcy różni się między silnikami,
    a edycji jest w tej bazie tyle, ile było roczników olimpiady.
    """
    from apps.competitions.models import Edition

    now = now or timezone.now()
    editions = Edition.objects.filter(is_current=False).annotate(last_deadline=Max("stages__deadline_at"))
    due = []
    for edition in editions:
        deadline = retention_deadline(edition, last_deadline=edition.last_deadline)
        if deadline is not None and deadline < now:
            edition.retention_deadline = deadline
            due.append(edition)
    return due


#: Powody, dla których konto zostaje mimo upływu terminu. Kody, nie zdania: ta sama lista opisuje
#: wiersz tabeli w panelu, wiersz raportu z komendy i licznik w podsumowaniu przebiegu.
BLOCKED_LATER_EDITION = "later_edition"
BLOCKED_OPEN_APPEAL = "open_appeal"
BLOCKED_UNPUBLISHED_RESULTS = "unpublished_results"
BLOCKED_ALREADY_ANONYMISED = "already_anonymised"

#: Zdania dla człowieka. Osobno od kodów, bo kod idzie do audytu i do liczników, a zdanie na ekran.
BLOCKED_LABELS = {
    BLOCKED_LATER_EDITION: "startuje w późniejszej edycji",
    BLOCKED_OPEN_APPEAL: "ma nierozstrzygniętą reklamację",
    BLOCKED_UNPUBLISHED_RESULTS: "ma etap bez ogłoszonych wyników",
    BLOCKED_ALREADY_ANONYMISED: "już zanonimizowane",
}


@dataclass(frozen=True)
class RetentionCandidate:
    """Jedno konto uczestnika w planie retencji: co się z nim stanie i dlaczego.

    Struktura jest zamrożona, bo przechodzi przez trzy czytelniki naraz (raport z komendy, ekran
    koordynatora, sam przebieg anonimizacji) i żaden z nich nie ma prawa jej zmieniać. ``blocked``
    puste znaczy „do anonimizacji”; wypełnione niesie **kod** powodu, nie zdanie – tłumaczy go
    dopiero warstwa, która pokazuje.
    """

    participant: Participant
    blocked: str = ""

    @property
    def is_due(self) -> bool:
        return not self.blocked

    @property
    def reason(self) -> str:
        return BLOCKED_LABELS.get(self.blocked, self.blocked)


def _is_anonymised(user: User) -> bool:
    """Czy konto przeszło już przez anonimizację (własną, koordynatora albo retencyjną).

    Rozpoznajemy po domenie adresu, bo to ona jest skutkiem, którego nie da się osiągnąć inaczej:
    ``.invalid`` jest zarezerwowana przez RFC 2606 i formularz rejestracji takiego adresu nie
    przyjmie. Sam ``is_active=False`` by nie wystarczył – niesie też „konto zablokowane przez
    organizatora”, a takiemu nadal wolno mieć dane.
    """
    from .profile import ANONYMISED_EMAIL_DOMAIN

    return user.email.endswith(f"@{ANONYMISED_EMAIL_DOMAIN}")


def _blocked_reason(participant: Participant, *, expired_ids: set[int]) -> str:
    """Pierwsza przeszkoda, która zatrzymuje anonimizację tego konta (``""`` = brak przeszkód).

    Kolejność sprawdzeń jest kolejnością pytań, jakie zadaje człowiek patrzący na listę: czy konto
    w ogóle jeszcze ma dane, czy uczestnik nie startuje dalej, czy nie ma sprawy w toku i czy
    zawody, do których należy, zostały domknięte.
    """
    from apps.appeals.models import PENDING_STATUSES, Appeal
    from apps.competitions.models import StageEntry

    if _is_anonymised(participant.user):
        return BLOCKED_ALREADY_ANONYMISED
    entries = StageEntry.objects.filter(participant=participant).select_related("stage")
    # „Późniejsza edycja” = każda, której termin retencji jeszcze nie minął (w tym bieżąca).
    # Warunek jest po zbiorze edycji przeterminowanych, a nie po dacie: edycje bywają prowadzone
    # równolegle (rocznik zamykany i rocznik trwający), więc porównanie dat rozstrzygałoby
    # o „późniejszości” tam, gdzie pytanie brzmi „czy uczestnik jeszcze startuje”.
    if entries.exclude(stage__edition_id__in=expired_ids).exists():
        return BLOCKED_LATER_EDITION
    if Appeal.objects.filter(filed_by=participant, status__in=PENDING_STATUSES).exists():
        return BLOCKED_OPEN_APPEAL
    if entries.filter(stage__results_published_at__isnull=True).exists():
        return BLOCKED_UNPUBLISHED_RESULTS
    return ""


def candidates(edition, *, expired_ids: set[int] | None = None) -> list[RetentionCandidate]:
    """Konta uczestników tej edycji razem z rozstrzygnięciem „anonimizować / zostaje, bo…”.

    Zwracamy **wszystkie** konta, także wstrzymane, bo to jest treść dry-runu: koordynator ma
    zobaczyć, czego automat nie ruszy i z jakiego powodu. Sama anonimizacja bierze z tej listy
    wyłącznie ``is_due``.
    """
    from apps.competitions.models import StageEntry

    if expired_ids is None:
        expired_ids = {item.pk for item in expired_editions()}
    participant_ids = (
        StageEntry.objects.filter(stage__edition_id=edition.pk)
        .values_list("participant_id", flat=True)
        .distinct()
    )
    participants = (
        Participant.objects.filter(pk__in=participant_ids).select_related("user").order_by("public_code")
    )
    return [
        RetentionCandidate(participant, _blocked_reason(participant, expired_ids=expired_ids))
        for participant in participants
    ]


@dataclass(frozen=True)
class RetentionPlan:
    """Plan dla jednej edycji: termin retencji i konta z rozstrzygnięciem."""

    edition: object
    deadline: datetime | None
    candidates: list[RetentionCandidate]

    @property
    def due(self) -> list[RetentionCandidate]:
        return [item for item in self.candidates if item.is_due]

    @property
    def blocked(self) -> list[RetentionCandidate]:
        return [item for item in self.candidates if not item.is_due]


def plan(now=None) -> list[RetentionPlan]:
    """Co automat zrobiłby, gdyby ruszył teraz. Nie zmienia ani jednego wiersza.

    Jedno wejście dla obu czytelników dry-runu: komendy ``retention_report`` i ekranu
    ``/coordinator/retention/``. Gdyby każde z nich liczyło po swojemu, raport z terminala
    i tabela w panelu mogłyby pokazywać różne liczby – a wtedy żadnej z nich nie dałoby się ufać.
    """
    editions = expired_editions(now)
    expired_ids = {edition.pk for edition in editions}
    return [
        RetentionPlan(
            edition=edition,
            deadline=getattr(edition, "retention_deadline", None),
            candidates=candidates(edition, expired_ids=expired_ids),
        )
        for edition in editions
    ]


@transaction.atomic
def anonymise_expired_editions(now=None) -> dict:
    """Anonimizuje konta uczestników edycji, którym upłynął okres retencji.

    Zwraca ``{"editions": n, "anonymised": m, "blocked": k}`` – tyle, ile potrzeba do komunikatu
    w panelu i do logu przebiegu, i ani jednego adresu e-mail.

    Przebieg jest w **jednej** transakcji: anonimizacja połowy rocznika, przerwana błędem na
    trzydziestym koncie, zostawiłaby edycję w stanie, którego nie da się opisać ani uczestnikom,
    ani w rejestrze czynności. Przy rozmiarach tej bazy (rocznik to setki kont) koszt długiej
    transakcji jest mniejszy niż koszt takiego stanu.
    """
    from .profile import anonymise_account

    anonymised = 0
    blocked = 0
    plans = plan(now)
    for item in plans:
        for candidate in item.candidates:
            if not candidate.is_due:
                blocked += 1
                continue
            user = candidate.participant.user
            anonymise_account(user, actor=None)
            # Drugi wpis, obok ``account.anonymised`` z samej anonimizacji: tamten mówi, co się
            # stało z danymi, ten – dlaczego. Bez powodu w aktach zostaje sam skutek.
            audit(
                None,
                "account.anonymised_by_retention",
                user,
                {"user_id": user.pk, "edition_id": item.edition.pk},
            )
            anonymised += 1
    result = {"editions": len(plans), "anonymised": anonymised, "blocked": blocked}
    logger.info(
        "Retencja danych: %s edycji przeterminowanych, zanonimizowano %s kont, wstrzymano %s.",
        result["editions"],
        anonymised,
        blocked,
    )
    return result


@shared_task(name="apps.accounts.retention.anonymise_expired_editions")
def anonymise_expired_editions_task() -> dict:
    """Wywołanie retencji z ``beat`` (raz na dobę, ``CELERY_BEAT_SCHEDULE``).

    Raz na dobę, bo termin retencji jest liczony w miesiącach: przebieg co godzinę przesuwałby
    moment anonimizacji o kilkadziesiąt minut i nie zmieniał niczego poza obciążeniem bazy.
    """
    return anonymise_expired_editions()
