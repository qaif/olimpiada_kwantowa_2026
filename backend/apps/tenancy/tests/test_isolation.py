"""Reguła krzyżowa: obiekt cudzego konkursu nie istnieje dla tego konkursu.

To jest komplet minimalny z ``docs/UNIWERSALNY-ETAP-1.md`` § 7.2 – **siedemnaście** przypadków, po
jednym na obszar danych: etapy, zadania, zgłoszenia, recenzje, oceny końcowe, reklamacje,
publikacje wyników, dyplomy, uczestnicy, członkowie komitetu, kody zaproszeń, komunikaty
organizatora, zgłoszenia do supportu, wydarzenia edycji, wpisy audytu, banery, strony CMS.

**404, nie 403**, i to nie jest kosmetyka odpowiedzi (§ 3.6): 403 mówi „jesteś, ale nie tobie”,
czyli potwierdza istnienie obiektu; 404 mówi „nie ma tego tutaj”. Koordynator konkursu A nie ma
prawa dowiedzieć się z kodu odpowiedzi, że etap o takim identyfikatorze w ogóle istnieje – to jest
informacja o konkursie B. Odwrotnie: zła **rola** w swoim konkursie zostaje przy 403, bo tam nie ma
czego ukrywać.

Znacznik ``xfail(strict=True)`` towarzyszył tym przypadkom, dopóki zakresowanie wchodziło
zadaniami T3 (domena zawodów) i T5 (widoki), a T7 biegł równolegle. ``strict`` sprawia, że test,
który **zacznie** przechodzić, zgłasza się sam jako błąd („XPASS”), więc znacznik zdejmuje się
w tej samej zmianie, która zamyka obszar. Po zadaniu T5 zostaje **jeden** taki znacznik i jego
powód jest inny niż „jeszcze nie zrobione” – patrz ``NO_COLUMN`` niżej.

Świat drugiego konkursu budują fabryki z argumentem ``competition`` – mechanika i jej granice:
``apps/tenancy/tests/factories.py``. Dopóki model nie ma jeszcze kolumny konkursu, argument jest
po cichu pomijany, więc obiekt należy „do nikogo” i widok go pokazuje.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole, MessageBroadcast
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    InvitationCodeFactory,
    ParticipantFactory,
)
from apps.appeals.tests.factories import AppealFactory, AppealsCommitteeMemberFactory
from apps.cms.models import ContentPage
from apps.cms.tests.factories import AnnouncementFactory
from apps.competitions.models import EditionEvent
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.core.models import AuditLog
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.tests.factories import CertificateFactory, ResultsPublicationFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.support.tests.factories import SupportTicketFactory
from apps.tenancy.tests.factories import create_scoped, grant_membership

pytestmark = pytest.mark.django_db

#: Jedyny pozostały powód znacznika: model **nie ma jeszcze kolumny konkursu**, więc nie ma czym
#: zawęzić ani zapytania, ani widoku. ``support.SupportTicket`` ma ją dostać zgodnie z § 3.2
#: (``null=True``, bo istnieją też sprawy kierowane do operatora platformy), ale ``apps/support/``
#: nie należy do żadnego z zadań T1–T7 – patrz raport T5. Do tego czasu ``create_scoped`` po cichu
#: pomija argument, zgłoszenie należy „do nikogo” i kolejka koordynatora je pokazuje.
NO_COLUMN = "model nie ma jeszcze kolumny konkursu (support.SupportTicket, § 3.2)"


@pytest.fixture
def coordinator_a(competition):
    """Koordynator Konkursu #1 – rola nadana obiema drogami, tak jak nadaje ją serwis.

    Grupa Django odpowiada za dostęp do ``/cms/`` i za bramki sprzed T2, członkostwo – za rolę
    w konkursie. Po przełączeniu flagi ``memberships_enforced`` liczy się drugie; do tego czasu
    pierwsze. Test nadaje oba, bo jego przedmiotem jest **izolacja**, a nie moment przełączenia.
    """
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def client_a(client_for, competition, coordinator_a):
    """Zalogowany koordynator Konkursu #1, wysyłający żądania pod domenę swojego konkursu."""
    client = client_for(competition)
    client.force_login(coordinator_a)
    return client


@pytest.fixture
def stage_b(other_competition):
    """Etap drugiego konkursu – korzeń wszystkiego, co w tych testach należy „nie tutaj”."""
    return StageFactory(competition=other_competition)


@pytest.fixture
def submission_b(other_competition, stage_b):
    """Praca oddana w drugim konkursie, razem z zadaniem i wpisem do etapu tego samego konkursu."""
    problem = ProblemFactory(competition=other_competition, stage=stage_b)
    return SubmissionFactory(competition=other_competition, problem=problem, entry__stage=stage_b)


# --- zawody ---------------------------------------------------------------------------------


def test_stage_of_another_competition_is_not_found(client_a, stage_b):
    assert client_a.get(f"/coordinator/stages/{stage_b.pk}/edit/").status_code == 404


def test_problem_of_another_competition_is_not_found(client_a, other_competition, stage_b):
    problem = ProblemFactory(competition=other_competition, stage=stage_b)

    assert client_a.get(f"/coordinator/problems/{problem.pk}/").status_code == 404


def test_submission_of_another_competition_is_not_found(client_a, submission_b):
    """Blokada pracy do recenzji jest zapisem – tym bardziej nie wolno jej wykonać na cudzej."""
    response = client_a.post(f"/coordinator/submissions/{submission_b.pk}/lock-for-review/")

    assert response.status_code == 404


def test_review_of_another_competition_is_not_found(client_for, competition, other_competition, submission_b):
    """Recenzent Konkursu #1 otwierający recenzję **przydzieloną jemu**, ale z pracy konkursu B.

    Bez znacznika ``xfail``: ten obszar zamknęło zadanie T3 (``reviews_for_reviewer`` zawęża
    kolejkę do konkursu **przed** przydziałem, § 3.5), a test zgłosił to sam jako ``XPASS``.

    Przydział jest tu celowo ten sam (``reviewer=`` profil konkursu A): gdyby recenzja należała do
    kogoś innego, 404 padłoby z powodu „to nie twoja recenzja” i test przechodziłby, nie mówiąc nic
    o zakresie. Tak zbudowany przypadek zostawia **jedyną** przyczynę 404: praca należy do innego
    konkursu. Taki układ danych powstaje sam, gdy ta sama osoba recenzuje w dwóch olimpiadach –
    i wtedy kolejność „najpierw konkurs, potem rola” (§ 3.5) jest jedyną rzeczą, która je rozdziela.
    """
    reviewer = ActiveReviewerFactory(competition=competition)
    grant_membership(reviewer.user, competition, CompetitionRole.REVIEWER)
    review_b = ReviewFactory(competition=other_competition, submission=submission_b, reviewer=reviewer)
    client = client_for(competition)
    client.force_login(reviewer.user)

    assert client.get(f"/review/{review_b.pk}/").status_code == 404


def test_final_grade_of_another_competition_is_not_found(client_a, other_competition, submission_b):
    FinalGradeFactory(competition=other_competition, submission=submission_b)

    response = client_a.post(
        f"/coordinator/submissions/{submission_b.pk}/final-grade/", {"score": 6, "rationale": "x"}
    )

    assert response.status_code == 404


def test_appeal_of_another_competition_is_not_found(client_for, competition, other_competition, submission_b):
    member = AppealsCommitteeMemberFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.APPEALS)
    appeal_b = AppealFactory(competition=other_competition, submission=submission_b)
    client = client_for(competition)
    client.force_login(member.user)

    assert client.post(f"/appeals/{appeal_b.pk}/decide/").status_code == 404


def test_results_publication_of_another_competition_is_not_found(
    client_for, competition, other_competition, stage_b
):
    """Tabela wyników jest publiczna, ale publiczna **w swoim konkursie**.

    Adres ``/results/<id>/`` nie wymaga logowania, więc bez zakresowania byłby najtańszą drogą do
    cudzych wyników: wystarczy przejechać identyfikatory etapów.
    """
    stage_b.results_published_at = timezone.now()
    stage_b.save(update_fields=["results_published_at"])
    publication = ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    assert client_for(competition).get(f"/results/{publication.stage_id}/").status_code == 404


def test_certificate_of_another_competition_is_not_found(client_a, other_competition, stage_b):
    """Pobranie dokumentu z panelu koordynatora. Publiczna weryfikacja po kodzie zostaje globalna:

    kod jest unikalny w całej instalacji, a strona weryfikacji ma działać bez wskazania konkursu.
    """
    certificate = CertificateFactory(competition=other_competition, entry__stage=stage_b)

    assert client_a.get(f"/coordinator/certificates/{certificate.pk}/download/").status_code == 404


# --- konta i role ----------------------------------------------------------------------------


def test_participant_of_another_competition_is_not_found(client_a, other_competition):
    participant = ParticipantFactory(competition=other_competition)

    assert client_a.get(f"/coordinator/participants/{participant.pk}/").status_code == 404


def test_committee_member_of_another_competition_is_not_found(client_a, other_competition):
    member = ActiveReviewerFactory(competition=other_competition)

    assert client_a.get(f"/coordinator/members/{member.pk}/").status_code == 404


def test_invitation_code_of_another_competition_is_not_listed(client_a, other_competition):
    """Kody zaproszeń nie mają ekranu szczegółu – reguła obowiązuje więc na liście.

    Adres zaproszenia wysyłanego e-mailem niesie sam kod, a nie identyfikator, więc powierzchnią
    jest tutaj widoczność cudzego adresu w panelu („wysłane zaproszenia” na ekranie komisji),
    a nie odgadnięcie numeru.
    """
    invitation = InvitationCodeFactory(competition=other_competition, email="obcy@example.invalid")

    content = client_a.get("/coordinator/committee/").content.decode()

    assert invitation.email not in content


def test_broadcast_of_another_competition_is_not_listed(client_a, other_competition, coordinator_a):
    """Rejestr wysyłek organizatora: historia komunikatów konkursu B nie jest historią konkursu A."""
    broadcast = create_scoped(
        MessageBroadcast,
        other_competition,
        created_by=coordinator_a,
        subject="Komunikat obcego konkursu",
        body="treść",
        group="EDITION_PARTICIPANTS",
        recipient_count=1,
        sent_count=1,
    )

    assert broadcast.subject not in client_a.get("/coordinator/messages/").content.decode()


# --- korespondencja, kalendarz, ślad ----------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_COLUMN)
def test_support_ticket_of_another_competition_is_not_found(client_a, other_competition):
    ticket = SupportTicketFactory(competition=other_competition)

    assert client_a.get(f"/coordinator/support/{ticket.pk}/").status_code == 404


def test_edition_event_of_another_competition_is_not_found(client_a, stage_b):
    event = EditionEvent.objects.create(
        edition=stage_b.edition, title="Gala obcego konkursu", starts_on=timezone.localdate()
    )

    assert client_a.get(f"/coordinator/events/{event.pk}/edit/").status_code == 404


def test_audit_entry_of_another_competition_is_not_listed(client_a, other_competition, stage_b):
    """Ślad audytowy koordynatora jest śladem **jego** konkursu.

    Obiektem wpisu jest **istniejący** etap konkursu B, a nie wymyślony identyfikator, i to jest
    istotne: ``core.AuditLog`` nie ma jeszcze kolumny konkursu (§ 3.9), więc zakres wychodzi
    z obiektu, o którym wpis mówi (``apps.web.scoping.audit_scope``). Wpis o obiekcie, którego
    w bazie nie ma – bo go skasowano – zostaje widoczny i tak ma być: ślad po skasowanym etapie
    jest dokładnie tym, po co audyt istnieje.

    Wpisy o obiektach platformowych (konto, witryna) też zostają widoczne i to jest dziś
    odpowiedź poprawna: koordynator Olimpiady Kwantowej czyta je od zawsze, a schowanie ich
    byłoby zmianą, której nikt nie zamawiał (§ 0). Zamyka to dopiero kolumna ``competition``
    wypełniana w chwili zapisu.
    """
    entry = create_scoped(
        AuditLog,
        other_competition,
        action="stage.closed",
        target_type="competitions.stage",
        target_id=str(stage_b.pk),
    )

    assert (
        f"{entry.target_type}#{entry.target_id}" not in client_a.get("/coordinator/audit/").content.decode()
    )


def test_announcement_of_another_competition_is_not_listed(client_a, other_competition):
    """Baner wisi na każdej stronie serwisu, więc komunikat globalny byłby komunikatem cudzym."""
    announcement = AnnouncementFactory(
        competition=other_competition, text="Przerwa techniczna w obcym konkursie"
    )

    assert announcement.text not in client_a.get("/coordinator/announcements/").content.decode()


def test_cms_page_of_another_competition_is_not_found(client_a, other_competition):
    """Strona z drzewa konkursu B nie otwiera się spod domeny konkursu A.

    Ten przypadek jest zamknięty **od T1** i dlatego nie ma znacznika: Wagtail rozstrzyga adres od
    korzenia witryny wskazanej hostem, a konkurs B ma własną witrynę i własne poddrzewo. Test
    strzeże tego, żeby dołożenie wspólnego korzenia „dla wygody” nie otworzyło cichej furtki.
    """
    root = other_competition.site.root_page
    page = root.add_child(instance=ContentPage(title="Strona obcego konkursu", slug="obca-strona"))

    assert client_a.get(f"/{page.slug}/").status_code == 404
