"""Wysyłka komunikatów organizatora do grup odbiorców (panel koordynatora).

Moduł robi trzy rzeczy i nic poza nimi: rozwiązuje grupę odbiorców na listę adresów, zapisuje
wysyłkę w rejestrze (``MessageBroadcast``) i przekazuje listy do kolejki porcjami.

Dlaczego porcjami, a nie jednym zadaniem na całą wysyłkę: lista uczestników edycji to kilka
tysięcy adresów. Jedno zadanie oznaczałoby jeden proces workera zajęty przez kilkanaście minut,
jeden wyjątek kasujący całą resztę wysyłki i jeden ogromny ładunek w brokerze. Porcja po
``RECIPIENT_CHUNK`` adresów jest krótka, a jej ponowienie nie dubluje listów wysłanych w innych
porcjach.

Dlaczego mimo to **jeden list na odbiorcę**, a nie jedna koperta z wieloma adresatami: lista
adresów w nagłówku ``To:`` pokazałaby każdemu uczestnikowi adresy wszystkich pozostałych. To samo
dotyczy ``Cc``. Kopertę z ``Bcc`` odrzuca część serwerów odbiorczych jako spam, a i tak nie da się
wtedy powiedzieć, do kogo list nie doszedł.

Kogo **nigdy** nie ma na liście odbiorców: kont nieaktywnych i kont, których adresu nikt nie
potwierdził. Konto bez potwierdzonego adresu to najczęściej literówka albo cudzy adres wpisany
przy rejestracji (patrz ``apps.accounts.activation``) – wysyłka do niego jest w najlepszym razie
bezcelowa, a w najgorszym listem do osoby, która o olimpiadzie nigdy nie słyszała.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q

from apps.core.models import audit

from .models import (
    GROUP_REVIEWER,
    GROUP_SUPERVISOR,
    BroadcastGroup,
    BroadcastStatus,
    CommitteeMember,
    CommitteeStatus,
    MessageBroadcast,
    Participant,
    SchoolSupervisor,
    User,
    normalize_voivodeship,
)

logger = logging.getLogger(__name__)

#: Ile adresów wchodzi do jednej porcji. Pięćdziesiąt to kompromis: porcja kończy się w kilka
#: sekund (a więc jej ponowienie jest tanie), a tysiąc odbiorców to dwadzieścia zadań, a nie
#: tysiąc – kolejka nie zamienia się w log wysyłki.
RECIPIENT_CHUNK = 50

#: Konto, do którego wolno wysłać komunikat: aktywne i z potwierdzonym adresem e-mail.
DELIVERABLE = Q(is_active=True) & Q(email_verified_at__isnull=False)


def _emails(queryset) -> list[str]:
    """Adresy z zapytania o użytkowników: znormalizowane, bez pustych i bez powtórzeń.

    Powtórzenia są realne, a nie teoretyczne: ta sama osoba bywa i uczestnikiem, i członkiem
    komitetu, a przy grupie zbieranej po wpisach do etapów jeden uczestnik ma ich kilka. Dwa listy
    o tej samej treści pod ten sam adres wyglądają jak awaria serwisu.

    Kolejność jest posortowana, bo od niej zależy podział na porcje – a powtarzalny podział jest
    warunkiem tego, żeby ponowienie wysyłki dało się porównać z pierwotną.
    """
    return sorted({(email or "").strip().lower() for email in queryset if (email or "").strip()})


def parse_address_list(text: str) -> list[str]:
    """Adresy z pola „wklejona lista”: rozdzielone przecinkiem, średnikiem albo końcem wiersza.

    Świadomie liberalnie, bo źródłem takiej listy jest zwykle kopiuj-wklej z arkusza albo
    z klienta pocztowego, a każdy z nich rozdziela adresy inaczej. Walidacja kształtu adresu
    należy do formularza – tutaj rozstrzygamy wyłącznie, gdzie kończy się jeden, a zaczyna drugi.
    """
    separated = text.replace(";", "\n").replace(",", "\n")
    return _emails(part for part in separated.splitlines())


#: Grupy, które bez wskazanego etapu nie mają sensu. Stała mieszka tu, a nie w formularzu, bo tę
#: samą listę czyta ``resolve_recipients`` (zapytanie po wpisach) i ``BroadcastForm.parameter_field``
#: (wymagalność pola i to, co trafia do historii).
STAGE_GROUPS = frozenset(
    {
        BroadcastGroup.STAGE_REGISTERED,
        BroadcastGroup.STAGE_QUALIFIED,
        BroadcastGroup.STAGE_NO_SUBMISSION,
    }
)

#: Prefiksy klucza szkoły w liście wyboru (``school_choices``). Wykazy placówek są dwa (rejestr SIO
#: i słownik organizatora), a do tego dochodzi nazwa wpisana ręcznie – klucz musi mówić, do której
#: kolumny ``Participant`` się odnosi, bo identyfikator 17 w jednym wykazie i 17 w drugim to dwie
#: różne szkoły.
SCHOOL_KEY_SIO = "sio"
SCHOOL_KEY_CUSTOM = "inst"
SCHOOL_KEY_NAME = "name"


def _competition_for(competition, *, edition=None, stage=None):
    """Konkurs, w którego obrębie wolno szukać odbiorców – albo ``None``, czyli „nikogo”.

    To jest **jedyne** miejsce, w którym rozstrzyga się zakres wysyłki, i rozstrzyga się je
    domyślnie zamknięcie:

    - wołający podał konkurs (ekran zawsze podaje ``request.competition``) – bierzemy ten,
    - nie podał – etap albo edycja wskazują swój konkurs, a bez nich zostaje odwrót
      ``default_competition`` (jedyny konkurs w instalacji; przy dwóch – ``None``),
    - podał konkurs **i** etap z innego konkursu – ``None``. Formularz na to nie pozwoli (lista
      etapów jest listą bieżącej edycji tego konkursu), ale gdyby kiedyś pozwolił, sprzeczność
      między „dla kogo” a „o czym” ma kończyć się pustą grupą, a nie listem do cudzych uczestników.
    """
    if stage is not None:
        edition = stage.edition
    anchor = edition.competition if edition is not None else None
    if competition is None:
        if anchor is not None:
            return anchor
        from .services import default_competition

        return default_competition()
    if anchor is not None and anchor.pk != competition.pk:
        return None
    return competition


def _participant_emails(participants) -> list[str]:
    """Adresy kont stojących za profilami uczestników z zapytania ``participants``.

    Jedna droga dla wszystkich grup uczestników, i to z dwóch powodów:

    - **zakres** – każdy wołający podaje queryset zawężony już ``for_competition``, a tutaj konto
      jest wybierane wyłącznie przez identyfikator profilu z tego querysetu. Nie ma złączenia
      „konto → dowolny jego profil”, po którym warunek jednego konkursu mógłby dotknąć profilu
      z innego (ta sama osoba bywa uczestnikiem dwóch olimpiad),
    - **koszt** – ``pk__in`` z podzapytaniem to jedno zapytanie z półzłączeniem, bez ``DISTINCT``
      i bez mnożenia wierszy przez wpisy do etapów. Przy kilku tysiącach uczestników to wciąż
      pojedyncze milisekundy, a nie pętla po profilach w Pythonie.
    """
    return _emails(
        User.objects.filter(DELIVERABLE, pk__in=participants.values("user_id")).values_list(
            "email", flat=True
        )
    )


def _role_filter(competition, role: str) -> Q:
    """Warunek „konto ma rolę ``role`` w tym konkursie”, zapisany na ``User`` – do zapytań zbiorczych.

    To jest ``apps.accounts.services.has_role`` przepisane z pytania o jedną osobę na warunek dla
    tysięcy: przy włączonej fladze ``memberships_enforced`` rolą jest wiersz ``Membership`` **tego**
    konkursu, bez niej – globalna grupa Django, dokładnie jak w ``has_role``. Dwie różne reguły
    roli w dwóch miejscach (bramka panelu i lista odbiorców) oznaczałyby listy do osób, które panelu
    już nie widzą.
    """
    from .services import memberships_enforced

    if memberships_enforced(competition):
        return Q(memberships__competition=competition, memberships__role=role)
    return Q(groups__name=role)


def parse_school_key(key: str | None) -> Q | None:
    """Warunek na profil uczestnika dla klucza szkoły z listy wyboru – albo ``None`` przy śmieciach.

    ``name:`` (szkoła wpisana ręcznie) dopasowuje wyłącznie profile **bez** dowiązania do wykazu.
    Inaczej „LO nr 1” wpisane ręcznie łapałoby także uczniów pięciu różnych „LO nr 1” z wykazu,
    z pięciu różnych miast – a te stoją na liście osobno, każde ze swoją miejscowością.
    """
    kind, _, value = (key or "").partition(":")
    if kind == SCHOOL_KEY_SIO and value.isdigit():
        return Q(school_ref_id=int(value))
    if kind == SCHOOL_KEY_CUSTOM and value.isdigit():
        return Q(custom_institution_ref_id=int(value))
    if kind == SCHOOL_KEY_NAME and value.strip():
        return Q(school_ref__isnull=True, custom_institution_ref__isnull=True, school=value)
    return None


def school_choices(competition) -> list[tuple[str, str, int]]:
    """Szkoły, z których **są** uczestnicy tego konkursu – lista wyboru grupy „wybrana szkoła”.

    Lista pochodzi z profili, a nie z wykazu SIO, i to jest cała jej zaleta: wykaz ma osiem
    tysięcy szkół, z których większość nie ma tu ani jednego ucznia, a komunikat do szkoły bez
    uczniów trafiłby do nikogo. Zbiór jest więc zamknięty i od razu wiadomo, że każda pozycja ma
    odbiorców. Wynik to trójki ``(klucz, nazwa, liczba profili)``: nazwa idzie do historii wysyłek,
    a liczba – do listy wyboru (liczba **adresów** wychodzi dopiero w podglądzie, bo z profili
    odpadają konta nieaktywne).

    Wyszukiwarki szkół z rejestracji (``_school_picker.html``) tu nie ma świadomie: ona szuka
    w całym wykazie i dopuszcza nazwę wpisaną ręcznie, czyli odpowiada na pytanie „jaka jest twoja
    szkoła”, a nie „do której z naszych szkół piszesz”. Lista z przeglądarkowym wyszukiwaniem po
    pierwszych literach wystarcza przy kilkuset pozycjach.

    Trzy zapytania grupujące (wykaz SIO, słownik organizatora, nazwa wpisana ręcznie) zamiast
    jednego: każde grupuje po innej kolumnie, a jedno zapytanie z ``CASE`` byłoby trudniejsze do
    przeczytania niż trzy proste.
    """
    participants = Participant.objects.for_competition(competition)
    rows: list[tuple[str, str, int]] = []
    for row in (
        participants.filter(school_ref__isnull=False)
        .values("school_ref_id", "school_ref__name", "school_ref__city")
        .annotate(count=Count("id"))
        .order_by()
    ):
        label = f"{row['school_ref__name']}, {row['school_ref__city']}"
        rows.append((f"{SCHOOL_KEY_SIO}:{row['school_ref_id']}", label, row["count"]))
    for row in (
        participants.filter(school_ref__isnull=True, custom_institution_ref__isnull=False)
        .values("custom_institution_ref_id", "custom_institution_ref__name")
        .annotate(count=Count("id"))
        .order_by()
    ):
        label = str(row["custom_institution_ref__name"])
        rows.append((f"{SCHOOL_KEY_CUSTOM}:{row['custom_institution_ref_id']}", label, row["count"]))
    for row in (
        participants.filter(school_ref__isnull=True, custom_institution_ref__isnull=True)
        .exclude(school="")
        .values("school")
        .annotate(count=Count("id"))
        .order_by()
    ):
        label = f"{row['school']} (nazwa wpisana ręcznie)"
        rows.append((f"{SCHOOL_KEY_NAME}:{row['school']}", label, row["count"]))
    # ``.order_by()`` przy każdym grupowaniu: grupa ma powstawać wyłącznie z kolumn ``values()``,
    # a jawnie pusty porządek nie zostawia porządkowi domyślnemu modelu (``public_code``) żadnej
    # drogi do ``GROUP BY``. Kolejność listy i tak ustala sortowanie po nazwie tutaj.
    return sorted(rows, key=lambda row: row[1].casefold())


def grade_choices(competition) -> list[tuple[int, str]]:
    """Klasy, w których **są** uczestnicy tego konkursu – ta sama zasada, co przy szkołach.

    Rocznika (``birth_year``) jako grupy nie ma i to jest decyzja: klasa jest pojęciem szkolnym,
    w którym organizator myśli o uczestnikach („list do maturzystów”), a wiek jest daną, którą
    zbieramy wyłącznie do rozstrzygnięcia o zgodzie opiekuna (rejestr czynności, „Prowadzenie kont
    uczestników”) – dobieranie po nim adresatów byłoby nowym celem przetwarzania.
    """
    grades = (
        Participant.objects.for_competition(competition)
        .exclude(grade=None)
        .values_list("grade", flat=True)
        .distinct()
        .order_by("grade")
    )
    return [(grade, f"klasa {grade}") for grade in grades]


def workshop_choices(competition) -> list[tuple[str, str]]:
    """Warsztaty z harmonogramu **tego** konkursu – te same kolumny, co w tabeli obecności.

    Klucz jest kluczem obecności (``cms.WorkshopAttendance.workshop_key``), więc grupa
    „obecni na warsztacie” jest dokładnie tym, co koordynator odhaczył w ``/coordinator/workshops/
    attendance/``. Wiersz, którego w harmonogramie już nie ma, na listę nie trafia – z tego samego
    powodu, dla którego nie ma go na zaświadczeniu.
    """
    from apps.cms.workshops import workshop_rows, workshops_page

    return [
        (row["key"], f"{row['date'] or row['date_value'].strftime('%d.%m.%Y')} – {row['topic']}")
        for row in workshop_rows(workshops_page(competition))
    ]


def resolve_recipients(
    group: str,
    *,
    competition=None,
    edition=None,
    stage=None,
    district: str | None = None,
    region=None,
    school: str | None = None,
    grade: int | None = None,
    workshop: str | None = None,
    addresses: str = "",
) -> list[str]:
    """Adresy odbiorców dla wybranej grupy. Nieznana grupa to pusta lista, nigdy wyjątek widoku.

    **Zakres konkursu jest warunkiem każdej grupy poza wklejoną listą.** Rozstrzyga go
    :func:`_competition_for`, a każde zapytanie niżej zaczyna się od ``for_competition`` – list do
    „wszystkich uczestników” olimpiady A nie może dotknąć konta, które ma profil wyłącznie
    w olimpiadzie B, choć konto (``User``) jest wspólne dla platformy.

    „Wszyscy uczestnicy konkursu” to **każdy profil uczestnika w tym konkursie**, bez względu na
    wpisy do etapów – także ktoś, kto założył konto i jeszcze nie zapisał się do etapu (albo
    startował w poprzedniej edycji). „Uczestnicy bieżącej edycji” to węższa, dotychczasowa grupa:
    ktoś z wpisem do któregokolwiek etapu tej edycji. Zawężenia (region, szkoła, klasa) liczą się od
    grupy szerszej, bo tak brzmi prośba organizatora („uczestnicy z wybranego województwa”, a nie
    „zapisani do etapu z wybranego województwa”).

    Grupy etapowe idą wyłącznie po wpisach **uczestnika** (``StageEntry.participant``). Wpis
    drużynowy (flaga ``team_entries``) nie ma jednego adresata – Konkurs #1 drużyn nie ma, a list
    do składów drużyn jest osobnym pytaniem do organizatora.

    Brak wymaganego kontekstu (edycji przy grupie edycyjnej, etapu przy grupie etapowej, szkoły przy
    grupie szkolnej…) też daje pustą listę. Ekran nie wyśle wtedy niczego i powie o tym wprost – to
    bezpieczniejszy kierunek niż domyślanie się, o który etap chodziło.
    """
    if group == BroadcastGroup.CUSTOM:
        return parse_address_list(addresses or "")

    competition = _competition_for(competition, edition=edition, stage=stage)
    if competition is None:
        return []
    participants = Participant.objects.for_competition(competition)

    if group == BroadcastGroup.ALL_PARTICIPANTS:
        return _participant_emails(participants)
    if group == BroadcastGroup.EDITION_PARTICIPANTS:
        if edition is None:
            return []
        return _participant_emails(participants.filter(stage_entries__stage__edition=edition))
    if group in STAGE_GROUPS:
        if stage is None:
            return []
        from apps.competitions.models import StageEntry, StageEntryStatus

        entries = StageEntry.objects.filter(stage=stage, participant__isnull=False)
        if group == BroadcastGroup.STAGE_QUALIFIED:
            entries = entries.filter(status=StageEntryStatus.QUALIFIED)
        elif group == BroadcastGroup.STAGE_NO_SUBMISSION:
            from apps.submissions.models import Submission, SubmissionStatus

            # Praca odrzucona przez antywirusa nie jest pracą: nigdy nie weszła do oceniania
            # (ta sama reguła, co w ``apps.results.services``), więc jej autor ma dostać
            # przypomnienie tak samo, jak ktoś, kto nie wysłał niczego.
            sent = Submission.objects.filter(entry=OuterRef("pk")).exclude(
                status=SubmissionStatus.REJECTED_INFECTED
            )
            # Tylko wpisy „w grze”: zdyskwalifikowany i niezakwalifikowany dostaliby list o terminie,
            # który ich już nie dotyczy – a „wyślij pracę” do osoby wykluczonej z zawodów jest
            # gorsze od braku listu.
            entries = entries.filter(
                status__in=(StageEntryStatus.REGISTERED, StageEntryStatus.QUALIFIED)
            ).exclude(Exists(sent))
        return _participant_emails(participants.filter(pk__in=entries.values("participant_id")))
    if group == BroadcastGroup.REGION_PARTICIPANTS:
        if region is not None:
            if region.competition_id != competition.pk:
                return []
            # Profil zapisany przed włączeniem flagi ``custom_regions`` ma samo ``district``,
            # a kody regionów startowych są dosłownie wartościami ``district`` (migracja
            # ``accounts.0026``) – bez drugiej połowy warunku region pomijałby uczniów zapisanych
            # przed flagą.
            return _participant_emails(
                participants.filter(Q(region=region) | Q(region__isnull=True, district=region.code))
            )
        normalized = normalize_voivodeship(district)
        if not normalized:
            return []
        return _participant_emails(participants.filter(district=normalized))
    if group == BroadcastGroup.SCHOOL_PARTICIPANTS:
        condition = parse_school_key(school)
        if condition is None:
            return []
        return _participant_emails(participants.filter(condition))
    if group == BroadcastGroup.GRADE_PARTICIPANTS:
        # ``str(...).isdigit()``, a nie ``int(...)`` w ``try``: klasa spoza liczb to po prostu
        # „nikt”, tak samo jak brak parametru – wyjątek widoku nie jest tu lepszą odpowiedzią.
        if not str(grade if grade is not None else "").isdigit():
            return []
        return _participant_emails(participants.filter(grade=int(grade)))
    if group == BroadcastGroup.WORKSHOP_ATTENDEES:
        if not workshop:
            return []
        from apps.cms.models import WorkshopAttendance

        attended = WorkshopAttendance.objects.filter(workshop_key=workshop).values("participant_id")
        return _participant_emails(participants.filter(pk__in=attended))
    if group == BroadcastGroup.SUPERVISORS:
        # Profil opiekuna **tego** konkursu i rola ``supervisor`` w nim – ta sama definicja, co
        # w ``apps.accounts.supervisors.supervisor_profile``. Sam profil bez roli (rola odebrana)
        # nie czyni nikogo opiekunem, więc nie czyni też adresatem.
        supervisors = SchoolSupervisor.objects.for_competition(competition)
        return _emails(
            User.objects.filter(DELIVERABLE, _role_filter(competition, GROUP_SUPERVISOR))
            .filter(pk__in=supervisors.values("user_id"))
            .values_list("email", flat=True)
            .distinct()
        )
    if group in (BroadcastGroup.COMMITTEE, BroadcastGroup.COMMITTEE_DISTRICT):
        # Ta sama definicja „aktywnego recenzenta”, co w ``apps.grading.services.reviewer_pool``:
        # status ACTIVE **i** rola ``reviewer``. Komunikat do komitetu nie może trafić do osoby,
        # która zgłosiła się, ale nie została jeszcze zatwierdzona – ona nie jest członkiem.
        # Komitet jest komitetem **tego** konkursu (``CommitteeMember.competition``).
        members = CommitteeMember.objects.for_competition(competition).filter(status=CommitteeStatus.ACTIVE)
        if group == BroadcastGroup.COMMITTEE_DISTRICT:
            normalized = normalize_voivodeship(district)
            if not normalized:
                return []
            members = members.filter(district=normalized)
        return _emails(
            User.objects.filter(DELIVERABLE, _role_filter(competition, GROUP_REVIEWER))
            .filter(pk__in=members.values("user_id"))
            .values_list("email", flat=True)
            .distinct()
        )
    return []


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_broadcast_chunk(self, broadcast_id: int, recipients: list[str]) -> int:
    """Przekazuje jedną porcję komunikatu do wysyłki. Zwraca liczbę zakolejkowanych listów.

    Zadanie **nie wysyła** samo – dla każdego adresu woła ``apps.core.tasks.send_mail_task``.
    Dzięki temu ponowienia przy chwilowej awarii MTA zostają tam, gdzie już są opisane i
    przetestowane, a to zadanie odpowiada wyłącznie za rozbicie listy na pojedyncze koperty
    i za dopisanie licznika do rejestru.

    Temat i treść czytamy z rejestru, a nie z argumentów: ładunek zadania to wtedy identyfikator
    i porcja adresów, a nie kopia całego listu powielona przy każdej porcji. Stamtąd bierze się też
    nadawca – z konkursu, do którego należy wysyłka (``MessageBroadcast.competition``). Przez moduł
    marki nie idzie tu nic i nie ma czego: temat i treść komunikatu napisał koordynator, więc jego
    zdania nie są napisem w kodzie, który wolno podmienić na wzorzec.

    Licznik zwiększamy wyrażeniem ``F``, bo porcje idą równolegle – odczyt i zapis w Pythonie
    gubiłby część inkrementów przy dwóch workerach.
    """
    # ``select_related``, bo z konkursu czytamy nadawcę listów – bez tego doszłoby drugie zapytanie
    # na każdą porcję adresów.
    broadcast = MessageBroadcast.objects.filter(pk=broadcast_id).select_related("competition").first()
    if broadcast is None:
        # Rejestr skasowano między zakolejkowaniem a wykonaniem – nie ma czego wysyłać ani
        # czego liczyć. Ponowienie niczego nie naprawi, więc kończymy cicho.
        logger.warning(
            "Komunikat %s nie istnieje – porcja %s adresów pominięta.", broadcast_id, len(recipients)
        )
        return 0

    from apps.core.tasks import mail_from, send_mail_task

    # Nadawcę czytamy raz na porcję: jest własnością konkursu, a nie pojedynczej koperty.
    from_email = mail_from(broadcast.competition)
    queued = 0
    for recipient in recipients:
        send_mail_task.delay(broadcast.subject, broadcast.body, [recipient], from_email)
        queued += 1
    MessageBroadcast.objects.filter(pk=broadcast_id).update(sent_count=F("sent_count") + queued)
    broadcast.refresh_from_db(fields=["sent_count", "recipient_count"])
    if broadcast.sent_count >= broadcast.recipient_count:
        MessageBroadcast.objects.filter(pk=broadcast_id).update(status=BroadcastStatus.SENT)
    logger.info("Komunikat %s: zakolejkowano %s listów.", broadcast_id, queued)
    return queued


def _chunks(recipients: list[str]) -> list[list[str]]:
    return [
        recipients[index : index + RECIPIENT_CHUNK] for index in range(0, len(recipients), RECIPIENT_CHUNK)
    ]


@transaction.atomic
def send_broadcast(
    *,
    group: str,
    subject: str,
    body: str,
    recipients: list[str],
    actor=None,
    request=None,
    competition=None,
    target: dict | None = None,
) -> MessageBroadcast:
    """Zapisuje komunikat w rejestrze i kolejkuje wysyłkę **po commicie**.

    Kolejność jest tu regułą, a nie szczegółem: zadanie czyta temat i treść z rejestru, więc nie
    wolno go zakolejkować, zanim wiersz naprawdę będzie w bazie. ``transaction.on_commit`` daje
    dokładnie tę gwarancję – ten sam wzorzec, co przy liście aktywacyjnym
    (``apps.accounts.activation.queue_mail``).

    ``competition`` podaje ekran (``request.competition``) i to ono jest właścicielem wiersza –
    tym samym konkursem, w którym ``resolve_recipients`` szukał odbiorców. Odwrót
    ``default_competition`` zostaje dla wołających spoza żądania (testy, komendy): w instalacji
    jednokonkursowej daje ten sam wynik, a w wielokonkursowej – ``None``, czyli błąd zapisu
    zamiast wysyłki przypisanej cudzemu organizatorowi.

    ``target`` to parametr grupy (etap, region, szkoła, klasa, warsztat) z etykietą – patrz
    ``MessageBroadcast.target``. Trafia do rejestru **i** do audytu, bo bez niego wpis
    „uczestnicy z wybranej szkoły, 14 odbiorców” nie mówi, do kogo poszedł list.

    Do audytu (``broadcast.sent``) idą grupa, jej parametr i liczniki. Ani adresów, ani treści:
    audyt czytają także osoby bez prawa do danych kontaktowych uczestników, a treść komunikatu jest
    już w rejestrze, do którego wchodzi wyłącznie koordynator.
    """
    # Import lokalny: ``services`` ciągnie za sobą aktywację i zgody, a ten moduł jest ładowany
    # przez Celery przy starcie workera – zależność w drugą stronę zamknęłaby cykl.
    from .services import default_competition

    target = dict(target or {})
    broadcast = MessageBroadcast.objects.create(
        # Rejestr wysyłek należy do organizatora, który je zrobił; grupy odbiorców są zakresowane
        # jego konkursem, więc wiersz bez konkursu nie dałby się odczytać.
        competition=competition or default_competition(),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        group=group,
        target=target,
        subject=subject,
        body=body,
        recipient_count=len(recipients),
        status=BroadcastStatus.QUEUED,
    )
    chunks = _chunks(recipients)

    def _enqueue() -> None:
        for chunk in chunks:
            send_broadcast_chunk.delay(broadcast.pk, chunk)

    transaction.on_commit(_enqueue)
    audit(
        actor,
        "broadcast.sent",
        broadcast,
        {"group": group, "target": target, "recipients": len(recipients), "chunks": len(chunks)},
        request=request,
    )
    logger.info(
        "Komunikat %s do grupy %s: %s odbiorców w %s porcjach.",
        broadcast.pk,
        group,
        len(recipients),
        len(chunks),
    )
    return broadcast


#: Ile komunikatów pokazujemy na ekranie. Rejestr jest narzędziem do pytania „co ostatnio poszło
#: i do kogo”, a nie archiwum korespondencji – pełną historię ma audyt (``broadcast.sent``).
BROADCAST_HISTORY_LIMIT = 50


def recent_broadcasts(competition, limit: int = BROADCAST_HISTORY_LIMIT) -> list[MessageBroadcast]:
    """Ostatnie komunikaty **tego** konkursu, od najnowszego – z doczytanym autorem.

    Konkurs jest argumentem obowiązkowym, a nie odczytem z kontekstu: historia komunikatów sąsiada
    nie jest historią tego organizatora, a funkcja bez zakresu (tak wyglądała do 24.09.2026, choć
    nikt jej nie wołał) była gotowym wyciekiem dla pierwszego, kto by po nią sięgnął. Zakres idzie
    do zapytania **przed** limitem – inaczej limit obcinałby wiersze obu konkursów razem.
    ``competition=None`` nie widzi niczego (``for_competition``).
    """
    return list(
        MessageBroadcast.objects.for_competition(competition)
        .select_related("created_by")
        .order_by("-created_at", "-id")[:limit]
    )
