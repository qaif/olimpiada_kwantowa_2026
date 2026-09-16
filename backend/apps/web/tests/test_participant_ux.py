"""Czytelność panelu uczestnika: nagłówek „Co teraz”, zakładki, odliczanie i stany puste.

Czego pilnują te testy i co najłatwiej zepsuć przy pierwszej „drobnej poprawce”:

- **zakładka liczy tylko siebie.** Pulpit był jedną stroną, która przy każdym wejściu liczyła
  wyniki, reklamacje i zgody – także wtedy, gdy uczestnik przyszedł wysłać plik. Test mierzy to
  po **tabelach w zapytaniach**, a nie po ich liczbie: liczba rośnie i maleje z powodów, które
  z tym pytaniem nie mają nic wspólnego (cache Wagtaila, kolejne pole w nagłówku),
- **„Co teraz” pokazuje jedną czynność.** Tabela decyzyjna ma jeden test na wiersz, na funkcji
  czystej – bez stawiania edycji, etapu i zgłoszenia,
- **odliczanie jest z serwera.** Tekst („za 3 dni, 14 godz.”) jest w HTML-u przed uruchomieniem
  jakiegokolwiek skryptu, a słowa, z których skrypt złoży go za minutę, jadą w atrybutach:
  bez kompletu tych atrybutów angielski interfejs napisałby po minucie „za 3 dni”,
- **puste sekcje mówią, co dalej.** „Brak danych” bez zdania o tym, co z tego wynika, czyta się
  jak awaria.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.consents import ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord
from apps.appeals.models import Appeal
from apps.grading.models import GradeMethod
from apps.grading.tests.factories import FinalGradeFactory
from apps.results.models import ResultsPublication
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory
from apps.web.participant_now import (
    ACTION_APPEAL,
    ACTION_GUARDIAN,
    ACTION_INTERVIEW,
    ACTION_REGISTER,
    ACTION_RESULTS,
    ACTION_UPLOAD,
    GUARDIAN_CONFIRMED,
    GUARDIAN_MISSING,
    GUARDIAN_NOT_REQUIRED,
    GUARDIAN_PENDING,
    STATE_BEFORE,
    STATE_GRADING,
    STATE_OPEN,
    STATE_RESULTS,
    countdown_words,
    next_action,
    now_panel,
    remaining_text,
    stage_state,
    status_chips,
)

from .conftest import close_submissions, shift_stage

pytestmark = pytest.mark.django_db

ME = "/me/"
TAB_RESULTS = "/me/?tab=wyniki"
TAB_APPEALS = "/me/?tab=reklamacje"
TAB_CONSENTS = "/me/?tab=zgody"

#: Argumenty ``next_action``, w których nic się nie dzieje. Każdy test zapala **jeden** fakt –
#: inaczej nie byłoby wiadomo, który z nich zdecydował o wyniku.
QUIET = {
    "guardian_state": GUARDIAN_NOT_REQUIRED,
    "can_register": False,
    "is_training_stage": False,
    "upload_open": False,
    "missing_numbers": (),
    "is_interview": False,
    "interview_booked": False,
    "interview_bookable": False,
    "appeal_window_open": False,
    "results_ready": False,
}


def logged(client, participant):
    client.force_login(participant.user)
    return client


# --- (a) tabela decyzyjna „Co teraz” ------------------------------------------------------------


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"guardian_state": GUARDIAN_MISSING}, ACTION_GUARDIAN),
        ({"can_register": True}, ACTION_REGISTER),
        ({"upload_open": True, "missing_numbers": (2, 3)}, ACTION_UPLOAD),
        ({"is_interview": True, "interview_bookable": True}, ACTION_INTERVIEW),
        ({"appeal_window_open": True}, ACTION_APPEAL),
        ({"results_ready": True}, ACTION_RESULTS),
    ],
)
def test_each_fact_alone_produces_its_action(facts, expected):
    action = next_action(**{**QUIET, **facts})

    assert action is not None
    assert action.key == expected


def test_nothing_to_do_is_a_normal_answer():
    """Brak czynności jest stanem, a nie brakiem danych – panel mówi to wprost (szablon)."""
    assert next_action(**QUIET) is None


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # Zgoda opiekuna wyprzedza wszystko: to jedyna rzecz, której uczestnik nie załatwi sam.
        (
            {"guardian_state": GUARDIAN_MISSING, "upload_open": True, "missing_numbers": (1,)},
            ACTION_GUARDIAN,
        ),
        # Bez wpisu do etapu nie ma czego wysyłać, więc zapis wyprzedza upload.
        ({"can_register": True, "results_ready": True}, ACTION_REGISTER),
        # Termin oddania zamyka się wcześniej niż okno reklamacji poprzedniego etapu.
        (
            {"upload_open": True, "missing_numbers": (1,), "appeal_window_open": True},
            ACTION_UPLOAD,
        ),
        # Wyniki są do przeczytania – ustępują wszystkiemu, co ma termin.
        ({"appeal_window_open": True, "results_ready": True}, ACTION_APPEAL),
    ],
)
def test_more_important_thing_wins(facts, expected):
    assert next_action(**{**QUIET, **facts}).key == expected


def test_upload_action_names_the_first_problem_without_a_solution():
    """„Wyślij rozwiązanie zadania 2” – numer jest w podpisie, bo to on kieruje wzrok na kartę."""
    action = next_action(**{**QUIET, "upload_open": True, "missing_numbers": (2, 3)})

    assert "2" in action.label
    assert "3" not in action.label


def test_uploaded_everything_leaves_no_upload_action():
    assert next_action(**{**QUIET, "upload_open": True, "missing_numbers": ()}) is None


def test_booked_interview_is_not_an_action_any_more():
    facts = {"is_interview": True, "interview_bookable": True, "interview_booked": True}

    assert next_action(**{**QUIET, **facts}) is None


# --- (b) znaczniki stanu konta ------------------------------------------------------------------


def test_adult_has_no_guardian_chip():
    """„Nie dotyczy” w rzędzie znaczników wygląda jak brak czegoś, czego brakować nie może."""
    keys = [
        chip.key
        for chip in status_chips(
            account_active=True, consents_complete=True, guardian_state=GUARDIAN_NOT_REQUIRED
        )
    ]

    assert keys == ["account", "consents"]


@pytest.mark.parametrize("state", [GUARDIAN_MISSING, GUARDIAN_PENDING, GUARDIAN_CONFIRMED])
def test_minor_always_gets_a_guardian_chip(state):
    chips = status_chips(account_active=True, consents_complete=True, guardian_state=state)

    assert [chip.key for chip in chips][-1] == "guardian"


def test_incomplete_consents_are_a_warning_not_a_silence():
    chips = {
        chip.key: chip
        for chip in status_chips(
            account_active=True, consents_complete=False, guardian_state=GUARDIAN_NOT_REQUIRED
        )
    }

    assert "badge--warn" in chips["consents"].tone
    assert "brakuje" in chips["consents"].label


# --- (c) odliczanie ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(days=3, hours=14, minutes=7), "za 3 dni, 14 godz."),
        (timedelta(days=1, hours=0, minutes=30), "za 1 dzień, 0 godz."),
        (timedelta(hours=14, minutes=30), "za 14 godz., 30 min."),
        (timedelta(minutes=5, seconds=40), "za 5 min."),
        (timedelta(seconds=20), "za chwilę"),
        (timedelta(seconds=0), "termin minął"),
        (timedelta(seconds=-60), "termin minął"),
    ],
)
def test_remaining_text_is_precise_where_it_matters(delta, expected):
    """Dokładność rośnie w miarę zbliżania się terminu; sekund nie ma nigdy."""
    assert remaining_text(delta) == expected


def test_countdown_words_travel_to_the_browser(web_client, participant, entry, problems):
    """Skrypt nie ma katalogu tłumaczeń – wszystkie słowa muszą przyjechać w ``data-word-*``.

    Bez kompletu tych atrybutów licznik po minucie napisałby w angielskim interfejsie „za 3 dni”,
    a przy braku pojedynczego słowa – „3 , 14” z dziurą w środku.
    """
    content = logged(web_client, participant).get(ME).content.decode()

    attributes = set(re.findall(r"data-word-([a-z]+)=", content))
    assert attributes == {word.lower() for word in countdown_words()}


def test_countdown_is_rendered_by_the_server(web_client, participant, entry, problems):
    """Strona bez JavaScriptu niesie prawdziwą liczbę – po prostu z chwili wczytania."""
    content = logged(web_client, participant).get(ME).content.decode()

    assert "data-countdown" in content
    assert "data-deadline=" in content
    assert "data-server-now=" in content
    assert re.search(r"data-countdown-value>za \d+", content)


def test_stage_without_a_deadline_counts_down_to_nothing(edition, elim_stage):
    """Trening ma w bazie wartownik z roku 2099 – odliczanie do niego byłoby kłamstwem."""
    elim_stage.name = "Trening"
    panel = now_panel(
        now=timezone.now(),
        stage=_no_deadline(elim_stage),
        entry=None,
        can_register=False,
        upload_open=False,
    )

    assert panel.deadline is None


def _no_deadline(stage):
    """Etap udający brak terminu – ``has_deadline`` jest właściwością, więc podmieniamy rodzaj."""
    from apps.competitions.models import StageKind

    stage.kind = StageKind.TRAINING
    return stage


# --- (d) stan etapu ------------------------------------------------------------------------------


def test_stage_state_before_opening(edition, elim_stage):
    shift_stage(elim_stage, opens=1, deadline=10, review=20, appeal_opens=21, appeal_closes=28)

    assert stage_state(elim_stage, timezone.now()) == STATE_BEFORE


def test_stage_state_while_open(elim_stage):
    assert stage_state(elim_stage, timezone.now()) == STATE_OPEN


def test_stage_state_after_the_deadline(elim_stage):
    close_submissions(elim_stage)

    assert stage_state(elim_stage, timezone.now()) == STATE_GRADING


def test_published_results_end_the_stage(elim_stage):
    elim_stage.results_published_at = timezone.now()

    assert stage_state(elim_stage, timezone.now()) == STATE_RESULTS


# --- (e) zakładki --------------------------------------------------------------------------------


def test_default_tab_is_the_one_people_come_for(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(ME).content.decode()

    assert re.search(r'href="/me/"\s+aria-current="page"', content)
    assert problems[0].title in content
    # Pozostałe sekcje nie są renderowane wcale – nie są tylko ukryte stylem.
    assert "Moje wyniki" not in content
    assert "Twoje zgody" not in content


@pytest.mark.parametrize(
    ("url", "heading"),
    [
        (TAB_RESULTS, "Moje wyniki"),
        (TAB_APPEALS, "Reklamacje"),
        (TAB_CONSENTS, "Twoje zgody"),
    ],
)
def test_each_tab_renders_its_own_section(web_client, participant, entry, problems, url, heading):
    content = logged(web_client, participant).get(url).content.decode()

    assert heading in content
    # Karta zadania z formularzem wysyłki zostaje na swojej zakładce.
    assert 'name="file"' not in content


def test_unknown_tab_opens_the_panel_instead_of_refusing(web_client, participant, entry, problems):
    """Parametr w adresie bywa uszkodzony przez skrócenie linku – panel ma się otworzyć."""
    response = logged(web_client, participant).get("/me/?tab=czego-tu-nie-ma")

    assert response.status_code == 200
    assert problems[0].title in response.content.decode()


def test_every_tab_carries_the_now_header(web_client, participant, entry, problems):
    """Nagłówek odpowiada na pytanie, z którym się wchodzi do panelu – nie na pytanie o sekcję."""
    client = logged(web_client, participant)

    for url in (ME, TAB_RESULTS, TAB_APPEALS, TAB_CONSENTS):
        assert 'id="co-teraz"' in client.get(url).content.decode(), url


def test_panel_links_to_the_separate_screens(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(ME).content.decode()

    for url in ("/me/calendar/", "/me/archive/", "/me/certificates/", "/me/profile/"):
        assert f'href="{url}"' in content


# --- (f) ile zakładka kosztuje -------------------------------------------------------------------


def measure(client, url) -> tuple[int, str]:
    """Liczba zapytań jednego żądania i ich zlepiony SQL.

    Rozgrzewka poza pomiarem: Wagtail cache'uje ``Site`` przy pierwszym renderze ``base.html``,
    więc bez niej pierwsze żądanie ma jedno zapytanie więcej z powodu niezwiązanego z panelem.
    """
    assert client.get(url).status_code == 200
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    return len(queries), " ".join(query["sql"] for query in queries).lower()


def add_results_and_appeal(participant, entry, problem) -> None:
    """Ogłoszone wyniki etapu, ocena i złożona reklamacja – komplet, który liczą dwie zakładki."""
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.GRADED_PROVISIONAL)
    FinalGradeFactory(submission=submission, score=5, method=GradeMethod.CONSENSUS)
    Appeal.objects.create(submission=submission, filed_by=participant, argument="x" * 60)
    ResultsPublication.objects.get_or_create(stage=entry.stage)
    entry.stage.results_published_at = timezone.now()
    entry.stage.save(update_fields=["results_published_at"])


def test_tasks_tab_does_not_pay_for_results_and_appeals(web_client, participant, entry, problems):
    """Zakładka zadań kosztuje tyle samo przed ogłoszeniem wyników i po nim.

    To jest cały sens rozbicia pulpitu na zakładki: uczestnik, który przyszedł wysłać plik, nie
    płaci za policzenie punktów, komentarzy recenzentów i kolejki reklamacji. Mierzymy **przyrost**
    przy rosnących danych, a nie liczbę bezwzględną – ta rośnie i maleje z powodów, które z tym
    pytaniem nie mają nic wspólnego (kolejne pole w nagłówku, zmiana w ``base.html``).
    """
    client = logged(web_client, participant)
    close_submissions(entry.stage)
    add_results_and_appeal(participant, entry, problems[0])
    before, sql = measure(client, ME)

    # Recenzje są najdroższą częścią wyników (prefetch po pracach) i tu nie mogą się pojawić.
    assert "grading_review" not in sql

    add_results_and_appeal(participant, entry, problems[1])

    after, _sql = measure(client, ME)
    assert after == before


def test_results_tab_pays_for_what_it_shows(web_client, participant, entry, problems):
    """Kontrola dla testu powyżej: gdyby pomiar niczego nie widział, tamten przechodziłby zawsze."""
    client = logged(web_client, participant)
    before, _sql = measure(client, TAB_RESULTS)

    close_submissions(entry.stage)
    add_results_and_appeal(participant, entry, problems[0])

    after, sql = measure(client, TAB_RESULTS)
    assert after > before
    assert "grading_review" in sql


# --- (g) stany puste -----------------------------------------------------------------------------


def test_problem_without_a_solution_says_what_to_do_and_by_when(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(ME).content.decode()

    assert "Nie masz jeszcze wysłanego rozwiązania" in content
    assert "Wyślij rozwiązanie zadania 1 do" in content


def test_results_tab_explains_the_empty_list(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(TAB_RESULTS).content.decode()

    assert "Wyniki jeszcze przed nami" in content
    assert "Gdy komitet je ogłosi" in content


def test_appeals_tab_explains_both_empty_lists(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(TAB_APPEALS).content.decode()

    assert "Nie ma czego reklamować" in content
    assert "Nie złożyłeś jeszcze żadnej reklamacji" in content


# --- (h) karta zadania ---------------------------------------------------------------------------


def test_single_version_needs_no_history(web_client, participant, entry, problems):
    """Jedna wersja jest już wypisana nad kartą – zwijacz „historia” byłby pusty."""
    SubmissionFactory(entry=entry, problem=problems[0])

    content = logged(web_client, participant).get(ME).content.decode()

    assert "wersja 1" in content
    assert "Historia wysyłek" not in content


def test_more_versions_are_collapsed(web_client, participant, entry, problems):
    SubmissionFactory(entry=entry, problem=problems[0], version=1)
    SubmissionFactory(entry=entry, problem=problems[0], version=2)

    content = logged(web_client, participant).get(ME).content.decode()

    assert "Historia wysyłek (2 wersje)" in content
    assert "<details" in content


def test_latest_scan_verdict_stays_outside_the_collapsed_history(web_client, participant, entry, problems):
    """„Czy plik przeszedł skan” jest pytaniem zadawanym zaraz po wysyłce – nie może być schowane."""
    SubmissionFileFactory(submission=SubmissionFactory(entry=entry, problem=problems[0], version=1))
    SubmissionFileFactory(submission=SubmissionFactory(entry=entry, problem=problems[0], version=2))

    content = logged(web_client, participant).get(ME).content.decode()
    visible = content.split("Historia wysyłek")[0]

    assert "Skan antywirusowy:" in visible
    assert "wersja 2" in visible


def test_card_badge_says_the_work_is_missing(web_client, participant, entry, problems):
    content = logged(web_client, participant).get(ME).content.decode()

    assert "brak rozwiązania" in content


def test_field_error_stands_next_to_the_field(web_client, participant, entry, problems):
    """„Zaznacz potwierdzenie” nad kartą z trzema polami nie mówi, które z nich poprawić."""
    from apps.submissions.tests.factories import pdf_upload

    client = logged(web_client, participant)
    response = client.post(
        f"/me/stages/{entry.stage_id}/problems/1/upload/",
        {"file": pdf_upload()},
        HTTP_HX_REQUEST="true",
    )
    content = response.content.decode()

    assert 'class="field-error"' in content
    assert "Zaznacz potwierdzenie" in content
    # Błąd stoi po polu wyboru, a nie w komunikacie nad kartą.
    assert content.index('name="confirmed"') < content.index('class="field-error"')


# --- (i) zgody w nagłówku ------------------------------------------------------------------------


def test_missing_consent_is_visible_without_opening_the_tab(web_client, participant, entry, problems):
    """Uczestnik ma zobaczyć brak zgody na pierwszym ekranie, a nie po wejściu w zakładkę."""
    content = logged(web_client, participant).get(ME).content.decode()

    assert 'data-chip="consents"' in content
    assert "brakuje zgody" in content


def test_complete_consents_light_the_chip(web_client, participant, entry, problems):
    for kind in (ConsentKind.TERMS, ConsentKind.PRIVACY):
        ConsentRecord.objects.create(
            participant=participant, kind=kind, document_version="1.0", source=ConsentSource.WEB
        )

    content = logged(web_client, participant).get(ME).content.decode()

    assert "zgody kompletne" in content


def test_guardian_consent_is_counted_only_by_its_own_chip(web_client, participant, entry, problems):
    """Oświadczenie dziecka „opiekun się zgodził” nie jest dowodem woli opiekuna.

    Gdyby wchodziło do znacznika „zgody kompletne”, w jednym rzędzie stałoby „zgody kompletne”
    obok „brak zgody opiekuna” – dwa znaczniki mówiące o tej samej rzeczy dwie różne rzeczy.
    Stan zgody opiekuna liczy wyłącznie ``accounts.guardian``.
    """
    for kind in (ConsentKind.TERMS, ConsentKind.PRIVACY):
        ConsentRecord.objects.create(
            participant=participant, kind=kind, document_version="1.0", source=ConsentSource.WEB
        )
    participant.birth_year = timezone.localdate().year - 16
    participant.save(update_fields=["birth_year"])

    content = logged(web_client, participant).get(ME).content.decode()

    assert "zgody kompletne" in content
    assert "brak zgody opiekuna" in content
