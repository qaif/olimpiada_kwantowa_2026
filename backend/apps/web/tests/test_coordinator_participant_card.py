"""Karta uczestnika w panelu koordynatora (``/coordinator/participants/<pk>/``).

Co te testy pilnują i dlaczego akurat tego:

- **kto tu wchodzi.** Karta jest jedynym ekranem, który zestawia w jednym miejscu nazwisko, adres,
  szkołę, prace i oceny tej samej osoby. Wpuszczenie na nią recenzenta albo uczestnika byłoby
  wyciekiem szerszym niż którykolwiek pojedynczy ekran, z których karta się składa,
- **że wszystkie sekcje rzeczywiście stoją.** Strona składa się z danych sześciu aplikacji i każda
  z nich potrafi zniknąć po cichu (zła nazwa relacji w szablonie renderuje się jako pusty napis,
  a nie jako błąd). Test na komplecie danych sprawdza więc treść, a nie sam status 200,
- **że czynności celują w istniejące adresy.** Formularze karty nie mają własnych widoków zapisu –
  gdyby któryś adres się rozjechał, strona nadal by się wyświetliła, a przycisk przestałby działać,
- **że liczba zapytań nie rośnie z liczbą wierszy.** To jest cała obietnica ``participant_card``:
  uczestnik z dwoma etapami, czterema pracami w kilku wersjach i kompletem recenzji ma kosztować
  tyle samo zapytań, co uczestnik z jedną pracą.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.consents import ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord
from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.appeals.models import Appeal, AppealDecision, AppealStatus
from apps.competitions.models import ManualQualification, StageEntryStatus
from apps.competitions.tests.factories import (
    InterviewBookingFactory,
    InterviewSlotFactory,
    ProblemFactory,
    StageEntryFactory,
)
from apps.core.models import audit
from apps.core.tests.query_budgets import budget
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.models import Anonymization, ResultsPublication
from apps.submissions.models import AvStatus, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db


def card_url(participant) -> str:
    return reverse("web:coordinator-participant", args=[participant.pk])


@pytest.fixture
def card(participant, elim_stage, problems, coordinator):
    """Komplet danych, na którym karta ma pokazać **każdą** sekcję.

    Budowany jawnie, a nie serwisami, bo przedmiotem testu jest strona, a nie droga, którą dane
    powstają: przejście całego obiegu (rejestracja → upload → blokada → przydział → ocena →
    publikacja → reklamacja) zajęłoby kilkadziesiąt linii i wywracałoby się na regułach, których
    ta strona nie dotyczy.
    """
    entry = StageEntryFactory(
        participant=participant,
        stage=elim_stage,
        status=StageEntryStatus.QUALIFIED,
        total_points=11,
        manual_qualification=ManualQualification.QUALIFIED,
        manual_qualification_reason="Awaria łącza w trakcie rozmowy – decyzja komitetu z 3 marca.",
        manual_qualified_by=coordinator,
    )
    # Dwie wersje tej samej pracy: starsza jest w karcie dokumentem („tak wyglądało oddanie”),
    # nowsza – przedmiotem oceny. Obie muszą być widoczne.
    old = SubmissionFactory(entry=entry, problem=problems[0], version=1, status=SubmissionStatus.SUBMITTED)
    SubmissionFileFactory(submission=old, av_status=AvStatus.CLEAN, page_count=3)
    latest = SubmissionFactory(entry=entry, problem=problems[0], version=2, status=SubmissionStatus.IN_REVIEW)
    SubmissionFileFactory(submission=latest, av_status=AvStatus.CLEAN, page_count=5)
    reviewer = ActiveReviewerFactory(user__email="recenzent-karty@example.test")
    review = ReviewFactory(submission=latest, reviewer=reviewer, status=ReviewStatus.SUBMITTED, score=5)
    grade = FinalGradeFactory(submission=latest, score=5)
    appeal = Appeal.objects.create(
        submission=latest,
        filed_by=participant,
        argument="Proszę o ponowne przeliczenie punktów.",
        status=AppealStatus.ACCEPTED,
    )
    AppealDecision.objects.create(
        appeal=appeal,
        decided_by=coordinator,
        new_score=6,
        justification="Uzasadnienie komisji odwoławczej.",
    )
    ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.PRIVACY,
        document_version="2026-01",
        source=ConsentSource.WEB,
    )
    # Ogłoszona tabela: suma z ``entry_totals`` (stan zamrożony) i miejsce ze snapshotu.
    ResultsPublication.objects.create(
        stage=elim_stage,
        anonymization=Anonymization.CODE,
        snapshot=[{"rank": 1, "display": participant.public_code, "points": {}, "total": 11}],
        entry_totals={str(entry.pk): 11},
    )
    audit(participant.user, "submission.created", latest, {"version": 2})
    return {
        "entry": entry,
        "old": old,
        "latest": latest,
        "reviewer": reviewer,
        "review": review,
        "grade": grade,
        "appeal": appeal,
    }


def test_other_roles_get_403(web_client, participant, reviewer, entry):
    """Uczestnik i recenzent dostają 403, niezalogowany – przekierowanie na logowanie.

    Rozróżnienie jest kontraktowe (T-08): 403 mówi „jesteś zalogowany, ale nie tą rolą”,
    a 302 na ``/login/`` – „nie wiem, kim jesteś”.
    """
    url = card_url(participant)

    assert web_client.get(url).status_code == 302

    web_client.force_login(participant.user)
    assert web_client.get(url).status_code == 403
    web_client.logout()

    web_client.force_login(reviewer.user)
    assert web_client.get(url).status_code == 403


def test_coordinator_sees_every_section(web_client, coordinator, participant, card, problems):
    """Karta na komplecie danych: dane, zgody, etapy i prace, wyniki, reklamacje, audyt.

    Asercje idą po **treści**, a nie po samym statusie 200: literówka w nazwie relacji w szablonie
    Django renderuje się jako pusty napis, więc strona bez żadnej z tych sekcji nadal zwróciłaby 200.
    """
    web_client.force_login(coordinator)
    response = web_client.get(card_url(participant))
    assert response.status_code == 200
    html = response.content.decode()

    # 1. Dane
    assert participant.public_code in html
    assert participant.user.email in html
    assert participant.school in html
    # 2. Zgody
    assert "Zgody" in html
    assert "2026-01" in html
    # 3. Etapy i prace – obie wersje, recenzent, ocena końcowa
    assert "Zadania" in html or problems[0].title in html
    assert problems[0].title in html
    assert "v1" in html and "v2" in html
    assert card["reviewer"].user.email in html
    assert "Ocena końcowa" in html
    # Kwalifikacja ręczna razem z uzasadnieniem – bez niego decyzja jest tylko przestawionym polem.
    assert "Awaria łącza" in html
    # 4. Wyniki – miejsce z ogłoszonej tabeli
    assert "Wyniki" in html
    # 5. Reklamacje
    assert "Reklamacje" in html
    assert AppealStatus.ACCEPTED.label in html
    assert "Uzasadnienie" in html
    # 6. Historia (audyt)
    assert "submission.created" in html


def test_inline_forms_point_at_existing_action_endpoints(web_client, coordinator, participant, card):
    """Formularze karty celują w istniejące widoki-akcje koordynatora, a nie w nowe adresy."""
    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    latest = card["latest"]
    assert reverse("web:coordinator-submission-assign", args=[latest.pk]) in html
    assert reverse("web:coordinator-review-score", args=[card["review"].pk]) in html
    assert reverse("web:coordinator-review-unassign", args=[card["review"].pk]) in html
    assert reverse("web:coordinator-final-grade", args=[latest.pk]) in html
    assert reverse("web:coordinator-account-edit", args=[participant.user.pk]) in html
    assert reverse("web:coordinator-account-delete", args=[participant.user.pk]) in html


def test_assign_form_posts_to_existing_endpoint(web_client, coordinator, participant, card):
    """Przydział recenzenta z karty przechodzi przez istniejący endpoint i zapisuje recenzję.

    Sprawdzamy **skutek w bazie**, a nie adres przekierowania: dokąd wraca akcja, rozstrzyga baza
    widoków-akcji (dziś: ekran przydziałów etapu), a karta świadomie nie narzuca własnego powrotu.
    """
    from apps.grading.models import Review

    web_client.force_login(coordinator)
    second = ActiveReviewerFactory(user__email="drugi-recenzent@example.test", district="")
    response = web_client.post(
        reverse("web:coordinator-submission-assign", args=[card["latest"].pk]),
        {"reviewer_id": second.pk},
    )

    assert response.status_code == 302
    assert Review.objects.filter(submission=card["latest"], reviewer=second).exists()


def test_lock_button_replaces_assign_form_for_submitted_work(
    web_client, coordinator, participant, elim_stage, problems
):
    """Praca oddana, jeszcze niezablokowana: zamiast przydziału stoi „Zablokuj do oceny”.

    Warunek jest powtórzony za ekranem przydziałów etapu i tak samo jak tam **nie jest bramką** –
    chodzi o to, żeby nie stawiać formularza, którego zapis i tak by odmówił.
    """
    entry = StageEntryFactory(participant=participant, stage=elim_stage)
    submission = SubmissionFactory(
        entry=entry, problem=problems[0], version=1, status=SubmissionStatus.SUBMITTED
    )
    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert reverse("web:coordinator-submission-lock", args=[submission.pk]) in html
    assert reverse("web:coordinator-submission-assign", args=[submission.pk]) not in html


def test_interview_booking_is_shown(web_client, coordinator, participant, interview_stage):
    """Zapis na rozmowę kwalifikacyjną stoi przy etapie, który jest rozmową."""
    entry = StageEntryFactory(
        participant=participant, stage=interview_stage, status=StageEntryStatus.QUALIFIED
    )
    slot = InterviewSlotFactory(stage=interview_stage, note="komisja A")
    InterviewBookingFactory(slot=slot, entry=entry)

    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert "Rozmowa kwalifikacyjna" in html
    assert "komisja A" in html


def test_file_before_scan_has_no_download_link(web_client, coordinator, participant, elim_stage, problems):
    """Plik przed czystym skanem antywirusowym nie dostaje odnośnika – tak samo jak w API pobrań."""
    entry = StageEntryFactory(participant=participant, stage=elim_stage)
    submission = SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    SubmissionFileFactory(submission=submission, av_status=AvStatus.PENDING)

    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert reverse("submissions:submission-download", args=[submission.pk]) not in html
    assert "skan w toku" in html


def test_participant_without_entries_renders(web_client, coordinator, participant):
    """Uczestnik świeżo po rejestracji: karta stoi i mówi wprost, że wpisów jeszcze nie ma.

    Najczęstszy przypadek pierwszego wejścia na kartę (telefon w dniu rejestracji), a jednocześnie
    ten, w którym każdy nieostrożny ``[0]`` albo ``.first().pk`` w kodzie składającym dane wywaliłby
    stronę.
    """
    web_client.force_login(coordinator)
    response = web_client.get(card_url(participant))

    assert response.status_code == 200
    assert "nie ma jeszcze wpisu do żadnego etapu" in response.content.decode()


def test_missing_support_app_hides_the_section(web_client, coordinator, participant, monkeypatch):
    """Gdy aplikacji zgłoszeń nie ma, sekcja „Zgłoszenia” po prostu nie powstaje.

    ``None`` z ``support_tickets`` znaczy „nie ma takiej funkcji” i różni się od pustej listy,
    która znaczy „ta osoba niczego nie zgłaszała”. Test pilnuje obu stron tej różnicy naraz:
    karta bez modułu nie może obiecywać, że zgłoszeń nie było.
    """
    from apps.accounts import participant_card as card_module

    monkeypatch.setattr(card_module, "support_tickets", lambda user: None)
    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert 'id="zgloszenia"' not in html


def test_support_tickets_section_renders_when_app_is_installed(web_client, coordinator, participant):
    """Aplikacja zgłoszeń jest zainstalowana, więc sekcja stoi – z treścią zgłoszenia tej osoby."""
    from apps.support.models import SupportTicket

    SupportTicket.objects.create(user=participant.user, subject="Nie działa upload pracy")

    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert 'id="zgloszenia"' in html
    assert "Nie działa upload pracy" in html


def test_audit_covers_the_person_as_actor_and_as_target(web_client, coordinator, participant, entry):
    """Audyt pokazuje obie strony: czynności tej osoby i czynności **na** niej."""
    audit(participant.user, "consent.given", participant, {"kind": "GDPR"})
    audit(coordinator, "account.updated_by_coordinator", participant.user, {"fields": ["last_name"]})

    web_client.force_login(coordinator)
    html = web_client.get(card_url(participant)).content.decode()

    assert "consent.given" in html
    assert "account.updated_by_coordinator" in html
    # Odnośnik do przeglądarki audytu zawężonej po wykonawcy – karta pokazuje wycinek, nie całość.
    assert reverse("web:coordinator-audit") in html


def test_accounts_list_links_to_the_card(web_client, coordinator, participant):
    """Lista kont prowadzi do karty wyłącznie przy koncie z profilem uczestnika."""
    from apps.accounts.tests.factories import UserFactory

    UserFactory(email="bez-profilu@example.test")
    web_client.force_login(coordinator)
    html = web_client.get(reverse("web:coordinator-accounts")).content.decode()

    assert card_url(participant) in html
    assert html.count(">Karta</a>") == 1


def test_participant_card_url_tag_is_empty_without_participant():
    """Znacznik zwraca pusty napis dla wiersza bez uczestnika – szablon ma stracić odnośnik, nie paść."""
    from apps.web.templatetags.coordinator_extras import participant_card_url

    assert participant_card_url(None) == ""
    assert participant_card_url("") == ""


def _fill_stage(participant, stage, problems, *, versions: int) -> None:
    """Wpis do etapu z kompletem prac, wersji, recenzji i ocen – materiał do pomiaru zapytań."""
    entry = StageEntryFactory(participant=participant, stage=stage)
    for problem in problems:
        for version in range(1, versions + 1):
            submission = SubmissionFactory(
                entry=entry, problem=problem, version=version, status=SubmissionStatus.IN_REVIEW
            )
            SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN, page_count=4)
            if version == versions:
                ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=5)
                FinalGradeFactory(submission=submission, score=5)


def test_query_count_does_not_grow_with_rows(participant, elim_stage, problems, django_assert_num_queries):
    """Więcej prac, wersji i recenzji nie znaczy więcej zapytań – cała obietnica ``participant_card``.

    Mierzymy samo składanie danych, a nie całe żądanie: sesja, uprawnienia, menu panelu i pasek
    osi czasu należą do innych części serwisu i wolno im się zmieniać, a wtedy test o tamtych
    zapytaniach mówiłby co innego niż to, czego pilnuje. Porównanie jest **równościowe**, bo tylko
    ono wyklucza zapytanie na wiersz: dwie prace w jednej wersji i cztery w trzech wersjach mają
    kosztować dokładnie tyle samo.
    """
    from apps.accounts.participant_card import participant_card

    _fill_stage(participant, elim_stage, problems[:1], versions=1)
    with CaptureQueriesContext(connection) as small:
        participant_card(participant)
    baseline = len(small.captured_queries)

    # Ten sam wpis, ale znacznie więcej wierszy: trzy zadania więcej, po trzy wersje każde,
    # z recenzją i oceną końcową przy najnowszej.
    entry = participant.stage_entries.get(stage=elim_stage)
    for number in (7, 8, 9):
        problem = ProblemFactory(stage=elim_stage, number=number)
        for version in (1, 2, 3):
            submission = SubmissionFactory(
                entry=entry, problem=problem, version=version, status=SubmissionStatus.IN_REVIEW
            )
            SubmissionFileFactory(submission=submission, av_status=AvStatus.CLEAN, page_count=4)
            if version == 3:
                ReviewFactory(submission=submission, status=ReviewStatus.SUBMITTED, score=5)
                FinalGradeFactory(submission=submission, score=5)

    with django_assert_num_queries(baseline):
        participant_card(participant)


def test_page_query_budget_is_bounded(
    web_client, coordinator, participant, elim_stage, problems, django_assert_max_num_queries
):
    """Całe żądanie mieści się w stałym budżecie zapytań, niezależnie od liczby prac.

    Granica jest granicą, a nie dokładną liczbą: poza kartą stoją tu jeszcze sesja, uprawnienia,
    menu panelu i pasek osi czasu, a te należą do innych agentów i wolno im się zmieniać. Chodzi
    o to, żeby uczestnik z kompletem prac nie wywoływał zapytania na wiersz.
    """
    _fill_stage(participant, elim_stage, problems, versions=3)

    web_client.force_login(coordinator)
    with django_assert_max_num_queries(budget("coordinator/participant-card")):
        assert web_client.get(card_url(participant)).status_code == 200
