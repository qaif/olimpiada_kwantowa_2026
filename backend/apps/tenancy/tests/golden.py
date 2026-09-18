"""„Złota” fikstura: **kształt** produkcji w bazie testowej, bez ani jednej danej osobowej.

Po co ona jest (``docs/UNIWERSALNY-ETAP-1.md`` § 0.4): ograniczenie nadrzędne całego etapu brzmi
„zachowaj działającą i skonfigurowaną obecną Olimpiadę Kwantową”. Testy jednostkowe sprawdzają to
po kawałku – każdy w świecie, który sam sobie ustawił i który jest mniejszy od produkcji o dwa
rzędy wielkości. Ta fikstura odbudowuje **całość naraz**: jedną edycję z czterema etapami
w produkcyjnych rodzajach i formatach, komplet stron CMS o produkcyjnych slugach, po jednym koncie
każdej roli, uczestników z wpisami i pracami w różnych stanach, recenzje, oceny, publikację
wyników, reklamację, dokumenty, partnerów, komunikaty i wydarzenia. Dopiero na takim świecie
pytanie „czy po zakresowaniu serwis odpowiada tak samo” ma sens, bo dopiero tam jest co zepsuć.

**Zero danych osobowych i to jest reguła, nie ostrożność.** Nazwiska są generowane
(``Testowy 001``), adresy stoją w domenie ``example.invalid`` (RFC 2606 – nigdy nie zostanie
kupiona, więc list wysłany przez pomyłkę nie ma dokąd dojść), a szkoły są napisami. Fikstura
opisuje kształt, nie treść: nie ma tu ani jednego wiersza przepisanego z produkcji.

Czego tu **nie ma** i nie będzie: wywołania ``seed_*``, importu z pliku i literałów z bazy
organizatora. Strony pierwszego poziomu, które stawia migracja ``cms.0002``
(``aktualnosci``/``zadania``/``archiwum``/``wyniki``), bierzemy **z drzewa**, a nie tworzymy
ponownie – testy mają pracować dokładnie na tym, co dostanie produkcja.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.accounts.models import CommitteeStatus, CompetitionRole, Participant, User, Voivodeship
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.appeals.models import Appeal
from apps.appeals.tests.factories import AppealFactory, AppealsCommitteeMemberFactory
from apps.cms.models import (
    ContentPage,
    DocumentIndexPage,
    DocumentPage,
    FAQPage,
    HomePage,
    PartnersPage,
)
from apps.cms.tests.factories import AnnouncementFactory
from apps.competitions.models import (
    Edition,
    EditionEvent,
    Stage,
    StageEntryStatus,
    StageFormat,
    StageKind,
)
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    InterviewSlotFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import ROUND_BLIND, ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.models import Anonymization
from apps.results.tests.factories import ResultsPublicationFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

#: Strefa, w której organizator ogłasza terminy. Ta sama stała, co w ``apps.competitions.models``
#: – powtórzona, bo fikstura ma opisywać **kształt** terminów, a nie zależeć od tego, jak liczy je
#: moduł, którego zachowania właśnie pilnujemy.
WARSAW = ZoneInfo("Europe/Warsaw")

#: Etykieta edycji w kształcie produkcyjnym („I edycja …”), ale bez rocznika z produkcji: rok jest
#: liczony od „teraz”, żeby fikstura nie zaczęła opisywać przeszłości po zmianie kalendarza.
EDITION_LABEL = "I edycja testowa"

#: Domena adresów w fiksturze. RFC 2606 gwarantuje, że nie istnieje i nie zacznie istnieć.
MAIL_DOMAIN = "example.invalid"

#: Ilu uczestników zakłada fikstura. Trzech, bo tyle wystarczy na trzy różne stany wpisu
#: (zakwalifikowany, zarejestrowany, odrzucony) – a każdy kolejny kosztowałby w każdym teście
#: złotym tyle samo, nie dokładając ani jednego pytania, na które ten test odpowiada.
PARTICIPANT_COUNT = 3

#: Slugi stron dokładanych przez fiksturę do drzewa z migracji ``cms.0002``. Dokładnie te, które
#: stoją na produkcji – lista jest tu wprost, bo jej rozjazd z produkcją jest błędem fikstury.
DOCUMENT_SLUGS = ("regulamin", "rodo")
CONTENT_SLUGS = (("harmonogram", "Harmonogram"), ("warsztaty", "Warsztaty"), ("kontakt", "Kontakt"))


@dataclass
class Golden:
    """Świat złotej fikstury – wyłącznie to, o co pytają testy, z nazwami z domeny, nie z ORM-a."""

    competition: object
    edition: Edition
    training: Stage
    elim: Stage
    district: Stage
    final: Stage
    problems: list = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    coordinator: User | None = None
    reviewer: object = None
    appeals_member: object = None
    supervisor_user: User | None = None
    publication: object = None
    appeal: Appeal | None = None
    documents: DocumentIndexPage | None = None
    partners: PartnersPage | None = None


def _stage_window(stage_index: int) -> tuple[datetime, datetime]:
    """Okno etapu w układzie produkcyjnym: etapy idą po sobie, każdy trwa około kwartału.

    Daty są liczone względem „teraz”, a nie wpisane literałem: fikstura, która opisuje rok 2026,
    po dwóch sezonach opisywałaby etap dawno zamknięty i każdy test złoty sprawdzałby archiwum
    zamiast bieżących zawodów.
    """
    now = timezone.now().astimezone(WARSAW)
    opens = now - timedelta(days=30) + timedelta(days=90 * stage_index)
    return opens, opens + timedelta(days=60)


def _scored_stage(competition, edition, *, kind: str, index: int, stage_format=StageFormat.SUBMISSIONS):
    """Etap ze skalą 0/2/5/6 i progiem kwalifikacji – tak, jak zakłada go ``create_stage``."""
    opens_at, deadline_at = _stage_window(index)
    stage = StageFactory(
        competition=competition,
        edition=edition,
        kind=kind,
        format=stage_format,
        opens_at=opens_at,
        deadline_at=deadline_at,
    )
    ScoringScaleFactory(competition=competition, stage=stage)
    QualificationRuleFactory(competition=competition, stage=stage, min_points=1)
    return stage


def _training_stage(competition, edition) -> Stage:
    """Piaskownica: etap treningowy otwarty bez końca (``TRAINING_DEADLINE``, rok 2099).

    Termin bierzemy z modelu, a nie z literału: gdyby kiedyś przesunął się o rok, fikstura ma
    opisywać etap dalej otwarty, a nie etap, który właśnie przestał się mieścić w walidacji osi.
    """
    from apps.competitions.models import TRAINING_DEADLINE

    return StageFactory(
        competition=competition,
        edition=edition,
        kind=StageKind.TRAINING,
        format=StageFormat.SUBMISSIONS,
        opens_at=datetime(2020, 1, 1, tzinfo=WARSAW),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE + timedelta(days=14),
        appeal_window_opens_at=TRAINING_DEADLINE + timedelta(days=16),
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=23),
    )


def _people(competition) -> dict:
    """Po jednym koncie każdej roli – z rolą nadaną obiema drogami (grupa i członkostwo)."""
    coordinator = CoordinatorFactory(
        email=f"koordynator@{MAIL_DOMAIN}", first_name="Koordynator", last_name="Testowy"
    )
    reviewer = ActiveReviewerFactory(
        competition=competition,
        user__email=f"recenzent@{MAIL_DOMAIN}",
        user__first_name="Recenzent",
        user__last_name="Testowy",
    )
    appeals_member = AppealsCommitteeMemberFactory(
        competition=competition,
        user__email=f"odwolawcza@{MAIL_DOMAIN}",
        user__first_name="Komisja",
        user__last_name="Testowa",
        status=CommitteeStatus.ACTIVE,
    )
    supervisor_user = UserFactory(email=f"opiekun@{MAIL_DOMAIN}", first_name="Opiekun", last_name="Testowy")
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    grant_membership(reviewer.user, competition, CompetitionRole.REVIEWER)
    grant_membership(appeals_member.user, competition, CompetitionRole.APPEALS)
    grant_membership(supervisor_user, competition, CompetitionRole.SUPERVISOR)
    return {
        "coordinator": coordinator,
        "reviewer": reviewer,
        "appeals_member": appeals_member,
        "supervisor_user": supervisor_user,
    }


def add_participants(
    competition, elim: Stage, problems: list, *, count: int | None = None
) -> list[Participant]:
    """Uczestnicy z wpisami w trzech różnych stanach i z pracami w trzech różnych stanach.

    Różnorodność jest tu przedmiotem, a nie ozdobą: panel uczestnika, kolejka recenzenta i pulpit
    koordynatora renderują **inne** gałęzie szablonu dla wpisu zarejestrowanego, zakwalifikowanego
    i niezakwalifikowanego, a test złoty ma przejść przez każdą z nich.

    Numeracja kont zaczyna się od tego, ilu uczestników w tym konkursie już jest, bo tę funkcję
    woła się także **drugi raz** – w teście „koszt ekranu nie rośnie z liczbą danych”. Numer liczony
    od zera dawałby wtedy kolizję adresów e-mail, czyli błąd fikstury udający błąd testu.
    """
    statuses = (
        StageEntryStatus.QUALIFIED,
        StageEntryStatus.REGISTERED,
        StageEntryStatus.NOT_QUALIFIED,
    )
    submission_states = (SubmissionStatus.FINAL, SubmissionStatus.SUBMITTED, None)
    people = []
    offset = Participant.objects.count()
    for index in range(offset, offset + (count or PARTICIPANT_COUNT)):
        participant = ParticipantFactory(
            competition=competition,
            user__email=f"uczestnik-{index + 1:03d}@{MAIL_DOMAIN}",
            user__first_name="Uczestnik",
            user__last_name=f"Testowy {index + 1:03d}",
            user__groups=[CompetitionRole.PARTICIPANT.value],
            school=f"Liceum testowe nr {index + 1}",
            district=Voivodeship.MAZOWIECKIE,
        )
        grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
        entry = StageEntryFactory(
            competition=competition,
            participant=participant,
            stage=elim,
            status=statuses[index % len(statuses)],
        )
        state = submission_states[index % len(submission_states)]
        if state is not None:
            SubmissionFactory(competition=competition, entry=entry, problem=problems[0], status=state)
        people.append(participant)
    return people


def _grading(competition, reviewer, participants: list) -> None:
    """Dwie recenzje na pracę – tak, jak wymaga tego przepływ ocen (PROJEKT.md § 2.4).

    Ocena końcowa powstaje wyłącznie przy pracy sfinalizowanej: ``FinalGrade`` przy pracy w stanie
    ``SUBMITTED`` opisywałby stan, którego przepływ nie potrafi wyprodukować.
    """
    from apps.submissions.models import Submission

    for submission in Submission.objects.filter(entry__participant__in=participants):
        ReviewFactory(
            competition=competition,
            submission=submission,
            reviewer=reviewer,
            round=ROUND_BLIND,
            status=ReviewStatus.SUBMITTED,
            score=6,
        )
        if submission.status == SubmissionStatus.FINAL:
            FinalGradeFactory(competition=competition, submission=submission, score=6)


def _cms_pages(competition) -> dict:
    """Strony dokładane do drzewa z migracji: dokumenty, partnerzy, treści, FAQ.

    Zakładamy je przez API Wagtaila (``add_child``), czyli tą samą drogą, którą zakłada je
    redaktor w ``/cms/`` – a nie wstawką do bazy. Test złoty ma przechodzić po tym, co powstaje
    normalnym zapisem strony, razem z jego ``url_path`` i rewizją.
    """
    home = HomePage.objects.descendant_of(competition.site.root_page, inclusive=True).first()
    if home is None:  # pragma: no cover - drzewo stron stawia migracja cms.0002
        raise RuntimeError("Brak strony głównej w drzewie – złota fikstura nie ma do czego dołożyć.")

    documents = home.add_child(instance=DocumentIndexPage(title="Dokumenty", slug="dokumenty"))
    for slug in DOCUMENT_SLUGS:
        documents.add_child(
            instance=DocumentPage(
                title=slug.capitalize(),
                slug=slug,
                version_label="1.0",
                document_date=date.today(),
            )
        )
    partners = home.add_child(instance=PartnersPage(title="Partnerzy", slug="partnerzy"))
    for slug, title in CONTENT_SLUGS:
        home.add_child(instance=ContentPage(title=title, slug=slug, show_in_menu=True))
    home.add_child(instance=FAQPage(title="FAQ", slug="faq"))
    return {"documents": documents, "partners": partners}


def build_golden(competition) -> Golden:
    """Składa cały świat i oddaje uchwyty do tego, o co pytają testy złote.

    Jedna funkcja, a nie dziesięć fikstur: ten świat ma sens wyłącznie **w całości** – panel
    koordynatora liczy w jednym zapytaniu prace, recenzje i reklamacje naraz, więc zbudowany
    z połowy części odpowiadałby inaczej niż produkcja i nie sprawdzałby niczego.
    """
    edition = CurrentEditionFactory(competition=competition, year_label=EDITION_LABEL)
    training = _training_stage(competition, edition)
    elim = _scored_stage(competition, edition, kind=StageKind.ELIM, index=0)
    district = _scored_stage(
        competition,
        edition,
        kind=StageKind.DISTRICT,
        index=1,
        stage_format=StageFormat.INTERVIEW,
    )
    final = _scored_stage(competition, edition, kind=StageKind.FINAL, index=2)

    problems = [
        ProblemFactory(competition=competition, stage=elim, number=number, title=f"Zadanie {number}")
        for number in (1, 2, 3)
    ]
    people = _people(competition)
    participants = add_participants(competition, elim, problems)
    _grading(competition, people["reviewer"], participants)

    EditionEvent.objects.create(
        edition=edition,
        title="Gala finałowa",
        starts_on=timezone.localdate() + timedelta(days=200),
        note="online",
    )
    AnnouncementFactory(competition=competition, text="Termin I etapu przedłużony do piątku.")

    pages = _cms_pages(competition)
    return Golden(
        competition=competition,
        edition=edition,
        training=training,
        elim=elim,
        district=district,
        final=final,
        problems=problems,
        participants=participants,
        coordinator=people["coordinator"],
        reviewer=people["reviewer"],
        appeals_member=people["appeals_member"],
        supervisor_user=people["supervisor_user"],
        documents=pages["documents"],
        partners=pages["partners"],
    )


def publish_results(golden: Golden) -> object:
    """Ogłasza wyniki I etapu. Osobno, bo publikacja jest **zdarzeniem**, a nie stanem świata.

    Testy panelu uczestnika i kolejki recenzenta pracują na etapie jeszcze nieogłoszonym; test
    strony wyników – na ogłoszonym. Jedna fikstura dla obu opisywałaby świat, w którym wyniki są
    zawsze ogłoszone, czyli świat, w którym połowa gałęzi szablonu nigdy się nie renderuje.
    """
    stage = golden.elim
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    golden.publication = ResultsPublicationFactory(
        competition=golden.competition,
        stage=stage,
        anonymization=Anonymization.CODE,
        snapshot=[
            {
                "rank": index + 1,
                "display": participant.public_code,
                "district": participant.district,
                "points": {"1": 6},
                "total": 6,
                "qualified": True,
            }
            for index, participant in enumerate(golden.participants)
        ],
    )
    return golden.publication


def file_appeal(golden: Golden) -> Appeal:
    """Reklamacja od pierwszego uczestnika – ścieżka odwoławcza też należy do kształtu produkcji."""
    from apps.submissions.models import Submission

    submission = Submission.objects.filter(entry__participant=golden.participants[0]).first()
    golden.appeal = AppealFactory(
        competition=golden.competition,
        submission=submission,
        filed_by=golden.participants[0],
    )
    return golden.appeal


# --- zdarzenia wysyłające listy -------------------------------------------------------------------
#
# Cztery funkcje poniżej stoją **poza** ``build_golden`` z tego samego powodu, co ``publish_results``
# i ``file_appeal``: są zdarzeniami, a nie stanem świata. Dołożenie ich do fikstury dorzuciłoby
# każdemu testowi złotemu zgłoszenie, rozmowę i zaległą recenzję, a razem z nimi kilka zapytań do
# progów z ``QUERY_BUDGET`` – czyli zmieniłoby pomiar, którego ta fikstura ma pilnować.
#
# Są tu, a nie w teście, bo pilnują tematów sześciu listów, których do etapu 2 nie sprawdzał żaden
# test niezmienności (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1). Temat składa się w serwisie
# z podstawieniem, więc jedynym sposobem odczytania go takim, jaki dojdzie do człowieka, jest
# **wysłanie listu** – stąd droga przez serwis i ``django.core.mail.outbox``, a nie przez import
# stałej, której w tych pięciu miejscach po prostu nie ma.


def open_support_ticket(golden: Golden, *, body: str = "Nie widzę zadania w panelu.") -> object:
    """Zgłoszenie uczestnika do organizatora. Zwraca sprawę; list idzie do organizatora.

    Wysyłka jest zakolejkowana **po commicie** (``apps.accounts.activation.queue_mail``), więc
    wołający opakowuje to wywołanie w ``django_capture_on_commit_callbacks(execute=True)`` –
    inaczej ``mail.outbox`` zostaje pusty i test przechodzi, nie sprawdziwszy niczego.
    """
    from apps.support.models import SupportCategory
    from apps.support.services import open_ticket

    return open_ticket(
        user=golden.participants[0].user,
        category=SupportCategory.OTHER,
        subject="Pytanie o termin",
        body=body,
    )


def answer_support_ticket(golden: Golden, ticket, *, body: str = "Termin jest w regulaminie.") -> object:
    """Odpowiedź koordynatora na zgłoszenie. Zwraca wypowiedź; list idzie do zgłaszającego."""
    from apps.support.services import reply

    return reply(ticket, body, author=golden.coordinator, from_coordinator=True)


def book_interview(golden: Golden, *, participant=None, hours_ahead: int = 20) -> object:
    """Zapis na rozmowę w etapie okręgowym (forma ``INTERVIEW``). Zwraca zapis.

    Termin stoi **dwadzieścia godzin** przed nami, a nie dobę: mieści się wtedy w oknie
    przypomnienia (``apps.competitions.video.bookings_to_remind``, ``[teraz, teraz + 24 h)``),
    więc na tym samym zapisie da się sprawdzić temat potwierdzenia i temat przypomnienia.
    """
    participant = participant or golden.participants[0]
    StageEntryFactory(
        competition=golden.competition,
        participant=participant,
        stage=golden.district,
        status=StageEntryStatus.QUALIFIED,
    )
    slot = InterviewSlotFactory(
        competition=golden.competition,
        stage=golden.district,
        starts_at=timezone.now() + timedelta(hours=hours_ahead),
    )
    from apps.competitions.interviews import book_slot

    return book_slot(participant, slot)


def assign_pending_review(golden: Golden, *, overdue: bool = True) -> object:
    """Recenzja czekająca na wykonanie – jedyny stan, o którym przypomina beat oceniania.

    Recenzje złotej fikstury są **wystawione** (``SUBMITTED``), więc żadna z nich nie wchodzi do
    przebiegu przypomnień; praca przechodzi tu w stan ``IN_REVIEW``, a recenzja dostaje własnego
    recenzenta, bo więz ``grading_review_unique_assignment`` nie dopuszcza drugiego przydziału tej
    samej pracy tej samej osobie w tej samej rundzie.

    ``overdue`` rozstrzyga, który z **dwóch** tematów przypomnienia złoży serwis
    (``apps.grading.deadlines.reminder_message``): „po terminie” czy „zbliża się termin”.
    """
    from apps.submissions.models import Submission

    submission = Submission.objects.filter(entry__participant__in=golden.participants).first()
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    reviewer = ActiveReviewerFactory(
        competition=golden.competition,
        user__email=f"recenzent-zalegly@{MAIL_DOMAIN}",
        user__first_name="Recenzent",
        user__last_name="Zaległy",
    )
    now = timezone.now()
    return ReviewFactory(
        competition=golden.competition,
        submission=submission,
        reviewer=reviewer,
        round=ROUND_BLIND,
        status=ReviewStatus.ASSIGNED,
        due_at=now - timedelta(days=2) if overdue else now + timedelta(days=1),
    )


#: Pola osi czasu etapu przesuwane razem, w jednym kawałku. Kolejność jest tu bez znaczenia –
#: przesuwamy wszystkie o tę samą różnicę, więc wzajemny porządek dat zostaje nietknięty.
STAGE_TIMELINE_FIELDS = (
    "opens_at",
    "deadline_at",
    "review_deadline_at",
    "appeal_window_opens_at",
    "appeal_window_closes_at",
)


def ready_for_results(golden: Golden, *stages: Stage, score: int = 2) -> None:
    """Przenosi wskazane etapy do chwili, w której wolno ogłosić ich wyniki.

    Złota fikstura opisuje świat **w trakcie** sezonu: etapy mają otwarte okna reklamacji, jedna
    praca jest dopiero oddana, a przy jednym wpisie nie ma żadnej. ``compute_stage_results`` odmawia
    wtedy przeliczenia (``STAGE_NOT_FINALIZED``), a próg – policzenia (``APPEAL_WINDOW_OPEN``)
    i tak ma być: tabelę wyników ogłasza się dopiero po zamknięciu oceniania i reklamacji. Funkcja
    przesuwa ten sam świat o jeden krok dalej i dlatego stoi **poza** ``build_golden``, tak samo jak
    ``publish_results`` i ``file_appeal``: jest zdarzeniem, a nie stanem świata.

    Dwie rzeczy naraz, bo obie są tą samą chwilą w kalendarzu zawodów:

    - **oś czasu** etapu wędruje wstecz o tyle, żeby okno reklamacji zamknęło się dobę temu.
      Różnicę liczymy z danych etapu, a nie stałą liczbą dni: etapy złotej fikstury stoją
      w odstępach kwartalnych, więc jedno przesunięcie dla wszystkich zostawiłoby finał
      z oknem nadal otwartym,
    - **ocenianie**: każda praca dostaje stan ``FINAL`` i ocenę końcową. Wpis bez ani jednej pracy
      zostaje bez zmian i to jest przedmiot, a nie przeoczenie: brak zgłoszenia znaczy zero
      punktów, czyli wiersz, który w każdej ogłoszonej tabeli istnieje.

    Woła to test snapshotu tabel wyników (``docs/UNIWERSALNY-ETAP-2.md`` § 5.2, T28).
    """
    from apps.submissions.models import Submission

    now = timezone.now()
    for stage in stages:
        offset = stage.appeal_window_closes_at - (now - timedelta(days=1))
        for name in STAGE_TIMELINE_FIELDS:
            setattr(stage, name, getattr(stage, name) - offset)
        stage.save(update_fields=list(STAGE_TIMELINE_FIELDS))
    for submission in Submission.objects.filter(entry__stage__in=stages):
        if submission.status != SubmissionStatus.FINAL:
            submission.status = SubmissionStatus.FINAL
            submission.save(update_fields=["status"])
        if getattr(submission, "final_grade", None) is None:
            FinalGradeFactory(competition=golden.competition, submission=submission, score=score)
