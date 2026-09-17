"""Opiekun szkolny: rejestracja konta, dopasowanie uczniów i oświadczenie o udziale szkoły.

Skąd bierze się uprawnienie opiekuna. **Od ucznia**, a nie od organizatora: opiekun widzi
dokładnie tych uczestników, którzy sami wpisali jego adres e-mail w swoim profilu. Nie ma tu
zatwierdzania przez koordynatora ani przypisywania po nazwie szkoły – oba rozwiązania kusiły,
bo wyglądają na porządniejsze, ale oba znaczyłyby, że nauczyciel dostaje wgląd w dane ucznia
bez jego udziału. Dopisanie adresu w profilu jest odwracalne jednym kliknięciem i to właśnie
uczeń jest stroną, która tę decyzję podejmuje.

Czego opiekun **nie** widzi i nie może:

- **punktów przed ogłoszeniem wyników etapu.** Ścieżka statusu mówi „oddane / w ocenie /
  oceniona / wyniki” i nic ponadto; liczba punktów pojawia się dopiero wtedy, gdy jest publiczna,
- **prac uczniów.** Ani plików, ani komentarzy recenzentów. Panel jest listą stanów, a nie
  drugim wejściem do akt oceniania,
- **żadnej czynności zmieniającej przebieg zawodów.** Jedyny zapis, jaki opiekun wykonuje, to
  potwierdzenie udziału własnej szkoły w edycji – oświadczenie o nim samym, nie o uczniach.

Dopasowanie idzie po **znormalizowanym** adresie (małe litery, bez spacji), bo uczeń przepisuje
adres nauczyciela ze słuchu albo z tablicy: „J.Kowalski@Szkola.pl” i „j.kowalski@szkola.pl” to
ten sam człowiek i ta sama skrzynka.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from .activation import send_activation_email
from .models import GROUP_SUPERVISOR, Participant, SchoolParticipation, SchoolSupervisor, User

logger = logging.getLogger(__name__)


def normalize_supervisor_email(email: str | None) -> str:
    """Postać porównawcza adresu opiekuna: bez spacji, małymi literami.

    Ta sama normalizacja obowiązuje przy zapisie w profilu ucznia i przy szukaniu uczniów
    w panelu opiekuna – inaczej wpisanie adresu wielkimi literami cicho odcinałoby nauczyciela
    od jego własnej listy.
    """
    return (email or "").strip().lower()


def registration_enabled() -> bool:
    """Czy serwis **oferuje** dziś zakładanie kont opiekuna szkolnego.

    Rozstrzyga organizator przełącznikiem ``cms.SiteSettings.supervisor_registration_enabled``,
    domyślnie **wyłączonym**: w pierwszej edycji rola nie jest ogłaszana publicznie, a konta
    powstają na prośbę. Reguła mieszka tutaj, a nie w widoku, bo pytają o nią trzy niezależne
    miejsca (adres rejestracji, pole „adres opiekuna” w profilu uczestnika, testy kontraktu)
    i rozjazd któregokolwiek z nich znaczyłby ukrycie pozorne.

    Nie pytamy o witrynę z żądania (``Site.find_for_request``), tylko o **którąkolwiek** z włączonym
    przełącznikiem – tak samo, jak ``apps.cms.analytics.analytics_enabled`` i z tego samego powodu:
    rozstrzygnięcie jest binarne, a dodatkowe zapytanie o witrynę kosztowałoby więcej niż warte
    jest rozróżnienie domeny produkcyjnej od stagingowej przy jednorazowej decyzji organizatora.

    Błąd bazy znaczy „nie” (świeża baza przed migracjami): domyślną odpowiedzią przełącznika,
    który **ukrywa** funkcję, musi być jej ukrycie.

    Czego ta funkcja **nie** rozstrzyga: dostępu opiekunów, którzy konto już mają. Ich panel
    stoi na ``supervisor_profile`` niżej i przełącznik go nie dotyka – ukrycie drogi wejścia nie
    jest tym samym, co odebranie komuś dostępu do danych, które już ogląda.
    """
    from django.db import DatabaseError

    from apps.cms.models import SiteSettings

    try:
        return SiteSettings.objects.filter(supervisor_registration_enabled=True).exists()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli ustawień
        logger.warning("Nie udało się odczytać przełącznika rejestracji opiekunów szkolnych.")
        return False


def supervisor_profile(user, competition=None) -> SchoolSupervisor | None:
    """Profil opiekuna, o ile konto ma do tego prawo: rola ``supervisor`` **i** profil.

    Jedna definicja roli dla mixinu widoku, nawigacji i przekierowania po zalogowaniu – tak samo
    jak ``active_reviewer_profile`` dla recenzenta. Reguła jest domyślnie zamknięta: konto bez
    roli albo bez profilu nie jest opiekunem, choćby miało wpisaną szkołę.

    ``competition=None`` znaczy „weź konkurs z kontekstu”, tak samo jak przy recenzencie.
    """
    from .services import current_competition, has_role

    if not user or not user.is_authenticated or not user.is_active:
        return None
    if not has_role(user, competition or current_competition(), GROUP_SUPERVISOR):
        return None
    return getattr(user, "school_supervisor", None)


@transaction.atomic
def register_supervisor(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    school: str = "",
    school_id: int | None = None,
    phone: str = "",
    request=None,
) -> SchoolSupervisor:
    """Zakłada konto opiekuna szkolnego. Konto czeka na potwierdzenie adresu, jak każde inne.

    Rejestracja jest **otwarta** (bez kodu zaproszenia), bo opiekun nie dostaje dostępu do
    niczyich danych samym założeniem konta: pusty panel zobaczy każdy, kogo żaden uczeń nie
    wskazał. Przed zasypaniem bazy kontami chroni to samo, co rejestrację uczestnika – CAPTCHA,
    limit żądań i aktywacja adresu (konto nieaktywowane kasuje kosiarka po czterech godzinach).

    Szkoła jest **opcjonalna**, w odróżnieniu od profilu uczestnika: nauczyciel bywa opiekunem
    uczniów z kilku placówek (zespół szkół, korepetycje, koło pozaszkolne), a wymuszanie jednej
    nazwy kazałoby mu wybrać nieprawdę. Do zaświadczenia i tak potrzebna jest weryfikacja przez
    organizatora (``SchoolSupervisor.verified``).
    """
    # Importy lokalne: ``services`` importuje ``activation`` i modele, a reguły walidacji (szkoła,
    # telefon, konto hasłowe) mieszkają właśnie tam i nie ma powodu pisać ich tu drugi raz.
    from .phones import normalize_phone
    from .services import _create_user, _resolve_school, default_competition, grant_role

    # Szkoła ze słownika tylko wtedy, gdy ktoś ją stamtąd wybrał; w przeciwnym razie zostaje
    # zwykły tekst. Nie przepuszczamy go przez ``_resolve_school``, bo tamta funkcja pilnuje
    # reguły „szkoła jest obowiązkowa i ma sensowną nazwę”, słusznej dla uczestnika (grupowanie
    # k-anonimowe w wynikach) i bezprzedmiotowej dla opiekuna, którego szkoła nie wchodzi do
    # żadnego rachunku.
    if school_id is not None:
        school_name, school_obj = _resolve_school("", school_id)
    else:
        school_name, school_obj = (school or "").strip()[:255], None
    user = _create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=False,
    )
    # Jedna wartość dla profilu i dla członkostwa – ``verified`` jest oświadczeniem sprawdzonym
    # przez **tego** organizatora, więc profil i rola muszą wskazywać ten sam konkurs.
    competition = default_competition()
    profile = SchoolSupervisor.objects.create(
        user=user,
        competition=competition,
        school=school_name,
        school_ref=school_obj,
        phone=normalize_phone(phone) if (phone or "").strip() else "",
    )
    grant_role(user, GROUP_SUPERVISOR, competition=competition)
    send_activation_email(user, request=request)
    logger.info("Założono konto opiekuna szkolnego %s.", user.pk)
    return profile


# --- lista uczniów --------------------------------------------------------------------------------


def students_of(supervisor: SchoolSupervisor) -> list[Participant]:
    """Uczestnicy, którzy wskazali ten adres jako adres swojego opiekuna.

    Adres bierzemy z **konta** opiekuna, a nie z osobnego pola profilu: zmiana adresu e-mail
    przechodzi przez potwierdzenie na nowej skrzynce, więc jest tak samo wiarygodna jak adres
    z rejestracji, a drugie pole prędzej czy później rozjechałoby się z pierwszym.
    """
    email = normalize_supervisor_email(supervisor.user.email)
    if not email:
        return []
    return list(
        Participant.objects.filter(supervisor_email__iexact=email)
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "public_code")
    )


def _least_advanced(tracks: list) -> object | None:
    """Ścieżka, która zaszła najdalej **najmniej** – czyli stan całego etapu, a nie jednej pracy.

    Uczestnik oddaje w etapie kilka zadań i każde ma własną ścieżkę. Opiekun pyta jednak
    o etap („czy Kasia ma już wszystko oddane”), więc wiersz podpisujemy tym zadaniem, które
    jest najdalej w tyle: jedna praca w ocenie i dwie ocenione znaczą „w ocenie”, a nie
    „ocenione”. Pokazanie najdalszej ścieżki sugerowałoby, że sprawa jest zamknięta.
    """
    if not tracks:
        return None
    return min(tracks, key=lambda track: sum(1 for step in track.steps if step.state == "done"))


def student_rows(supervisor: SchoolSupervisor, edition) -> list[dict]:
    """Wiersze panelu: uczeń, jego wpisy do etapów edycji i ścieżka statusu każdego z nich.

    Wszystko czytamy hurtem (wpisy, zgłoszenia, publikacje), bo lista opiekuna liczy zwykle
    kilkunastu uczniów razy trzy etapy – pętla z zapytaniem w środku dawałaby tu kilkadziesiąt
    zapytań na jedno wejście na stronę.

    Punkty trafiają do wiersza **wyłącznie** dla etapu z ogłoszonymi wynikami i są wtedy tą samą
    liczbą, którą każdy widzi w publicznej tabeli. Przed publikacją klucz ma wartość ``None``,
    a nie zero – zero wyglądałoby jak wynik.
    """
    from apps.competitions.models import StageEntry
    from apps.results.models import ResultsPublication
    from apps.submissions.models import Submission, SubmissionStatus
    from apps.submissions.status_track import status_track

    participants = students_of(supervisor)
    if not participants or edition is None:
        return [{"participant": participant, "stages": []} for participant in participants]

    entries = list(
        StageEntry.objects.filter(participant__in=participants, stage__edition=edition)
        .select_related("stage")
        .order_by("stage__opens_at", "stage_id")
    )
    publications = {
        publication.stage_id: publication
        for publication in ResultsPublication.objects.filter(stage__edition=edition)
    }
    submissions = (
        Submission.objects.filter(entry__in=entries)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .order_by("entry_id", "problem_id", "-version")
    )
    latest: dict[int, dict[int, Submission]] = {}
    for submission in submissions:
        latest.setdefault(submission.entry_id, {}).setdefault(submission.problem_id, submission)

    by_participant: dict[int, list[dict]] = {}
    for entry in entries:
        stage = entry.stage
        publication = publications.get(stage.pk)
        tracks = [
            status_track(submission=submission, stage=stage, publication=publication)
            for submission in latest.get(entry.pk, {}).values()
        ]
        track = _least_advanced(tracks) or status_track(submission=None, stage=stage, publication=publication)
        by_participant.setdefault(entry.participant_id, []).append(
            {
                "stage": stage,
                "entry": entry,
                "track": track,
                "published": publication is not None,
                "total_points": entry.total_points if publication is not None else None,
            }
        )
    return [
        {"participant": participant, "stages": by_participant.get(participant.pk, [])}
        for participant in participants
    ]


# --- oświadczenie o udziale szkoły ------------------------------------------------------------


@transaction.atomic
def confirm_participation(supervisor: SchoolSupervisor, edition, *, actor=None, request=None) -> dict:
    """Zapisuje (albo cofa) oświadczenie „moja szkoła bierze udział w tej edycji”.

    Jedno kliknięcie przełącza stan, bo to jest oświadczenie o dwóch wartościach, a nie decyzja
    wymagająca uzasadnienia. Zwraca ``{"confirmed": bool}``: panel pisze wtedy zdanie, które
    faktycznie obowiązuje, zamiast zgadywać po tym, co wysłał.

    Brak bieżącej edycji jest odmową, a nie cichą nieobecnością przycisku: bez edycji
    oświadczenie nie miałoby czego dotyczyć, a opiekun ma prawo wiedzieć, dlaczego nic się
    nie stało.
    """
    if edition is None:
        raise DomainError(
            "Nie ma bieżącej edycji olimpiady – nie ma czego potwierdzać.",
            "NO_CURRENT_EDITION",
            http.HTTP_409_CONFLICT,
        )
    existing = SchoolParticipation.objects.filter(supervisor=supervisor, edition=edition).first()
    if existing is not None:
        existing.delete()
        audit(
            actor,
            "school.participation_withdrawn",
            supervisor,
            {"edition_id": edition.pk},
            request=request,
        )
        return {"confirmed": False}
    SchoolParticipation.objects.create(supervisor=supervisor, edition=edition, confirmed_at=timezone.now())
    audit(actor, "school.participation_confirmed", supervisor, {"edition_id": edition.pk}, request=request)
    return {"confirmed": True}


def has_confirmed(supervisor: SchoolSupervisor, edition) -> bool:
    """Czy opiekun potwierdził udział szkoły w tej edycji. Brak edycji to „nie ma czego”."""
    if edition is None:
        return False
    return SchoolParticipation.objects.filter(supervisor=supervisor, edition=edition).exists()


def supervisors_for_edition(edition) -> list[SchoolSupervisor]:
    """Opiekunowie, którzy potwierdzili udział szkoły – krąg odbiorców zaświadczeń dla opiekunów.

    Potwierdzenie jest tu warunkiem, a nie ozdobą: zaświadczenie dla opiekuna poświadcza pracę
    z uczniami, a adres wpisany przez ucznia w formularzu jest dopiero przesłanką, że taka praca
    miała miejsce. Nauczyciel, który oświadczenia nie złożył, nie znajdzie się na liście.
    """
    if edition is None:
        return []
    return list(
        SchoolSupervisor.objects.filter(participations__edition=edition)
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "id")
    )


def set_supervisor_email(participant: Participant, email: str | None, *, actor=None, request=None) -> str:
    """Zapisuje adres opiekuna w profilu uczestnika i zwraca wartość po normalizacji.

    Osobna funkcja, a nie kolejne pole w ``apps.accounts.profile``, bo zdarzenie jest inne:
    uczestnik **nadaje albo odbiera komuś wgląd** w swój przebieg w zawodach, a nie poprawia
    swoje dane. Audyt notuje sam fakt zmiany bez adresu – adres opiekuna jest daną osobową
    osoby trzeciej, a wpisy audytowe czytają także ci, którzy do niej dostępu nie mają.
    """
    normalized = normalize_supervisor_email(email)
    before = participant.supervisor_email
    if before == normalized:
        return normalized
    participant.supervisor_email = normalized
    participant.save(update_fields=["supervisor_email"])
    audit(
        actor,
        "participant.supervisor_email_set",
        participant,
        {"had_supervisor": bool(before), "has_supervisor": bool(normalized)},
        request=request,
    )
    return normalized


def supervisor_user_exists(email: str) -> bool:
    """Czy adres opiekuna należy do istniejącego konta – wyłącznie do podpowiedzi w panelu.

    Odpowiedź trafia do własnego panelu uczestnika („Twój opiekun ma już konto”), a nie do
    publicznego formularza: tam byłaby wyszukiwarką kont po adresie.
    """
    normalized = normalize_supervisor_email(email)
    if not normalized:
        return False
    return User.objects.filter(email=normalized, school_supervisor__isnull=False).exists()
