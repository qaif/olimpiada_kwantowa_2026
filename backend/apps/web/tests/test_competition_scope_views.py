"""Zakres konkursu w warstwie WWW: po jednym reprezentatywnym ekranie z każdego panelu.

``apps/tenancy/tests/test_isolation.py`` sprawdza regułę krzyżową **po obszarach danych** (etap,
praca, recenzja, dyplom…). Ten moduł patrzy z drugiej strony – **po panelach** – bo to panele,
a nie modele, są tym, co zadanie T5 zmieniało:

- koordynator: pulpit, wyszukiwarka, lista kont, audyt,
- recenzent: kolejka przydziałów,
- uczestnik: pulpit i własne dokumenty,
- opiekun szkolny: lista uczniów,
- komisja odwoławcza: kolejka reklamacji,
- część publiczna: tabela wyników i statystyki.

Wspólny kształt każdego przypadku: **ta sama osoba** ma rolę w obu konkursach albo w jednym,
a żądanie idzie pod domenę konkursu A. Widać wtedy wyłącznie dane A. Ten układ nie jest
teoretyczny: nauczyciel bywa opiekunem w dwóch olimpiadach, a recenzent recenzuje w obu –
i wtedy kolejność „najpierw konkurs, potem rola” (§ 3.5) jest jedyną rzeczą, która je rozdziela.

Reguła odpowiedzi jest ta sama, co w suicie izolacji: **404** na cudzym obiekcie (nie 403),
**403** na złej roli we własnym konkursie, **302** dla anonima.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.accounts.models import CommitteeStatus, CompetitionRole, SchoolSupervisor
from apps.accounts.tests.factories import (
    ActiveReviewerFactory,
    CoordinatorFactory,
    ParticipantFactory,
    UserFactory,
)
from apps.appeals.tests.factories import AppealFactory, AppealsCommitteeMemberFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.grading.tests.factories import ReviewFactory
from apps.results.tests.factories import CertificateFactory, ResultsPublicationFactory
from apps.submissions.tests.factories import SubmissionFactory
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def logged_in(client_for, competition, user):
    """Klient pod domeną konkursu, zalogowany na wskazane konto."""
    client = client_for(competition)
    client.force_login(user)
    return client


@pytest.fixture
def world_b(other_competition):
    """Komplet danych drugiego konkursu: etap, zadanie, praca i wpis uczestnika.

    Jedna fikstura zamiast pięciu, bo w każdym z tych testów świat B jest tłem, a nie
    przedmiotem: chodzi o to, żeby **istniał** i żeby nie było go widać.
    """
    stage = StageFactory(competition=other_competition)
    problem = ProblemFactory(competition=other_competition, stage=stage)
    submission = SubmissionFactory(competition=other_competition, problem=problem, entry__stage=stage)
    return {
        "competition": other_competition,
        "stage": stage,
        "problem": problem,
        "submission": submission,
        "entry": submission.entry,
    }


# --- panel koordynatora ---------------------------------------------------------------------


@pytest.fixture
def coordinator_a(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return logged_in(client_for, competition, user)


def test_dashboard_counts_only_this_competition(coordinator_a, competition, world_b):
    """Pulpit pokazuje same liczby, więc błąd zakresu widać na nim **najpóźniej**.

    Karty etapów są kartami bieżącej edycji konkursu, a etap konkursu B ma własną edycję – więc
    jego nazwa nie ma prawa stanąć na tej stronie.
    """
    StageFactory(competition=competition)

    content = coordinator_a.get("/coordinator/").content.decode()

    assert world_b["stage"].display_name not in content or world_b["stage"].pk == 0
    assert f"/coordinator/stages/{world_b['stage'].pk}/" not in content


def test_search_does_not_reach_across_competitions(coordinator_a, competition, other_competition):
    """Wyszukiwarka panelu pyta naraz o uczestników, komisję, zadania, etapy i zgłoszenia.

    Asercja idzie po **kodzie publicznym**, a nie po nazwisku: fraza z adresu wraca na stronę
    w polu wyszukiwarki, więc „nazwiska nie ma w treści” byłoby asercją fałszywie negatywną.
    Kod publiczny stoi wyłącznie w wierszu wyniku i jest tym, co ten ekran naprawdę wydaje.
    """
    mine = ParticipantFactory(competition=competition, user__last_name="Nazwiskowski")
    stranger = ParticipantFactory(competition=other_competition, user__last_name="Nazwiskowski")

    content = coordinator_a.get("/coordinator/search/?q=Nazwiskowski").content.decode()

    assert mine.public_code in content
    assert stranger.public_code not in content


def test_accounts_list_shows_members_of_this_competition_only(coordinator_a, competition, other_competition):
    mine = ParticipantFactory(competition=competition, user__email="moj@example.invalid")
    grant_membership(mine.user, competition, CompetitionRole.PARTICIPANT)
    stranger = ParticipantFactory(competition=other_competition, user__email="obcy@example.invalid")
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    content = coordinator_a.get("/coordinator/accounts/").content.decode()

    assert mine.user.email in content
    assert stranger.user.email not in content


def test_account_of_another_competition_is_not_found(coordinator_a, other_competition):
    stranger = ParticipantFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    assert coordinator_a.get(f"/coordinator/accounts/{stranger.user.pk}/").status_code == 404


def test_password_reset_of_another_competitions_account_is_not_found(coordinator_a, other_competition):
    """Zawężenie jest to samo, co na karcie konta: koordynator A nie tknie konta konkursu B.

    404, a nie 403 – patrz nagłówek modułu: istnienie cudzego konta nie jest niczyją informacją.
    """
    from django.core import mail

    stranger = ParticipantFactory(competition=other_competition)
    grant_membership(stranger.user, other_competition, CompetitionRole.PARTICIPANT)

    response = coordinator_a.post(f"/coordinator/accounts/{stranger.user.pk}/password-reset/")

    assert response.status_code == 404
    assert len(mail.outbox) == 0


def test_audit_browser_hides_entries_about_objects_of_another_competition(
    coordinator_a, competition, world_b
):
    """Zakres audytu idzie od wydania D **własną kolumną** wpisu (``AuditLog.competition``, § 3.9).

    Wpisy powstają w ``competition_context``, czyli tak, jak powstają poza żądaniem: w zadaniu
    Celery, w komendzie i w migracji. W żądaniu konkurs bierze się z ``request.competition``
    i nie ma jak go pominąć – tu podajemy go wprost, bo wołamy helper bez żądania.
    """
    from apps.core.models import audit
    from apps.tenancy.context import competition_context

    mine = StageFactory(competition=competition)
    with competition_context(competition):
        audit(None, "stage.closed", mine)
    with competition_context(world_b["competition"]):
        audit(None, "stage.rule_updated", world_b["stage"])

    content = coordinator_a.get("/coordinator/audit/").content.decode()

    assert f"competitions.stage#{mine.pk}" in content
    assert f"competitions.stage#{world_b['stage'].pk}" not in content


def test_audit_filter_choices_are_scoped_too(coordinator_a, world_b):
    """Sama nazwa akcji z cudzego konkursu bywa informacją („u kogoś padło ``stage.closed``”)."""
    from apps.core.models import audit
    from apps.tenancy.context import competition_context

    with competition_context(world_b["competition"]):
        audit(None, "results.published", world_b["stage"])

    content = coordinator_a.get("/coordinator/audit/").content.decode()

    assert 'value="results.published"' not in content


def test_platform_audit_entries_stay_visible(coordinator_a, competition):
    """Wpis bez konkursu (konto, witryna, alert) zostaje na ekranie – i to jest reguła, nie luka.

    Koordynator Olimpiady Kwantowej czyta te wiersze od zawsze, a schowanie ich po wdrożeniu
    wielokonkursowości byłoby zmianą widoczną i niezamówioną (§ 0). ``visible_to`` dopuszcza je
    świadomie i pod własną nazwą – ``for_competition`` ich **nie** oddaje.
    """
    from apps.accounts.tests.factories import UserFactory
    from apps.core.models import audit

    account = UserFactory(email="platformowe@example.invalid")
    audit(None, "account.blocked", account)

    content = coordinator_a.get("/coordinator/audit/").content.decode()

    assert f"accounts.user#{account.pk}" in content


# --- panel recenzenta -----------------------------------------------------------------------


def test_reviewer_queue_is_scoped_before_the_assignment(client_for, competition, other_competition, world_b):
    """Ta sama osoba recenzuje w obu olimpiadach – rozdziela je wyłącznie kolejność z § 3.5."""
    reviewer_a = ActiveReviewerFactory(competition=competition)
    grant_membership(reviewer_a.user, competition, CompetitionRole.REVIEWER)
    review_b = ReviewFactory(
        competition=other_competition, submission=world_b["submission"], reviewer=reviewer_a
    )
    client = logged_in(client_for, competition, reviewer_a.user)

    listing = client.get("/review/").content.decode()

    assert f"/review/{review_b.pk}/" not in listing
    assert client.get(f"/review/{review_b.pk}/").status_code == 404


# --- panel uczestnika -----------------------------------------------------------------------


def test_participant_panel_shows_only_this_competitions_profile(client_for, competition, other_competition):
    """Pulpit uczestnika pokazuje profil spod domeny, na której stoimy – i tylko jego.

    Dwa **konta**, a nie jedno z dwoma profilami: w wydaniu B ``Participant.user`` jest wciąż
    ``OneToOne`` (kolumna konkursu dopiero powstała, unikalność zamyka się w wydaniu D, § 4.4),
    więc jedna osoba nie ma jeszcze jak wystartować w dwóch olimpiadach. Przedmiotem testu jest
    to, co obowiązuje **już teraz**: cudzy profil nie przecieka na ten pulpit.
    """
    here = ParticipantFactory(competition=competition, school="Szkoła Tutejsza")
    grant_membership(here.user, competition, CompetitionRole.PARTICIPANT)
    there = ParticipantFactory(competition=other_competition, school="Szkoła Obca")
    grant_membership(there.user, other_competition, CompetitionRole.PARTICIPANT)

    content = logged_in(client_for, competition, here.user).get("/me/").content.decode()

    assert here.public_code in content
    assert there.public_code not in content


def test_participant_certificate_of_another_competition_is_not_found(
    client_for, competition, other_competition, world_b
):
    here = ParticipantFactory(competition=competition)
    grant_membership(here.user, competition, CompetitionRole.PARTICIPANT)
    there = ParticipantFactory(competition=other_competition)
    entry_b = StageEntryFactory(competition=other_competition, participant=there, stage=world_b["stage"])
    certificate_b = CertificateFactory(competition=other_competition, entry=entry_b)
    client = logged_in(client_for, competition, here.user)

    assert client.get(f"/me/certificates/{certificate_b.pk}/").status_code == 404


# --- panel opiekuna -------------------------------------------------------------------------


def test_supervisor_sees_students_of_this_competition_only(
    client_for, competition, other_competition, world_b
):
    """Nauczyciel prowadzi klasę w dwóch olimpiadach; wiąże je **jeden** adres e-mail."""
    # ``SchoolSupervisor`` nie ma własnej fabryki (profil zakłada serwis rejestracji), więc
    # zakładamy go wprost – tak samo, jak robi to ``apps/web/tests/test_supervisor.py``.
    user = UserFactory(email="nauczyciel@example.invalid", groups=["supervisor"])
    SchoolSupervisor.objects.create(user=user, school="XIV LO", competition=competition)
    grant_membership(user, competition, CompetitionRole.SUPERVISOR)
    email = user.email
    mine = ParticipantFactory(competition=competition, supervisor_email=email, user__last_name="Tutejski")
    stranger = ParticipantFactory(
        competition=other_competition, supervisor_email=email, user__last_name="Obcy"
    )

    client = logged_in(client_for, competition, user)
    content = client.get("/supervisor/students/").content.decode()

    assert mine.public_code in content
    assert stranger.public_code not in content


# --- komisja odwoławcza ---------------------------------------------------------------------


def test_appeals_queue_is_scoped(client_for, competition, other_competition, world_b):
    member = AppealsCommitteeMemberFactory(competition=competition)
    grant_membership(member.user, competition, CompetitionRole.APPEALS)
    appeal_b = AppealFactory(competition=other_competition, submission=world_b["submission"])

    content = logged_in(client_for, competition, member.user).get("/appeals/").content.decode()

    assert world_b["submission"].entry.participant.public_code not in content
    assert f"/appeals/{appeal_b.pk}/" not in content


# --- część publiczna ------------------------------------------------------------------------


def test_public_results_of_another_competition_are_not_found(
    client_for, competition, other_competition, world_b
):
    """Adres bez logowania i z kolejnymi identyfikatorami – najtańsza droga do cudzych wyników."""
    stage_b = world_b["stage"]
    stage_b.results_published_at = timezone.now()
    stage_b.save(update_fields=["results_published_at"])
    publication = ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    assert client_for(competition).get(f"/results/{publication.stage_id}/").status_code == 404


def test_public_statistics_cover_this_competition_only(client_for, competition, other_competition, world_b):
    stage_b = world_b["stage"]
    stage_b.results_published_at = timezone.now()
    stage_b.save(update_fields=["results_published_at"])
    ResultsPublicationFactory(competition=other_competition, stage=stage_b)

    content = client_for(competition).get("/statystyki/").content.decode()

    assert stage_b.edition.year_label not in content


# --- kontrakt odpowiedzi --------------------------------------------------------------------


def test_wrong_role_still_gets_403_not_404(client_for, competition):
    """Zła rola **we własnym konkursie** zostaje przy 403: tam nie ma czego ukrywać (§ 3.6)."""
    participant = ParticipantFactory(competition=competition)
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)

    response = logged_in(client_for, competition, participant.user).get("/coordinator/")

    assert response.status_code == 403


def test_anonymous_still_gets_a_login_redirect(client_for, competition):
    response = client_for(competition).get("/coordinator/")

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_reviewer_of_another_competition_gets_403_on_this_panel(client_for, competition, other_competition):
    """Rola nadana u sąsiada nie jest rolą tutaj – ale odmowa dotyczy **roli**, więc 403."""
    reviewer_b = ActiveReviewerFactory(competition=other_competition, status=CommitteeStatus.ACTIVE)
    grant_membership(reviewer_b.user, other_competition, CompetitionRole.REVIEWER)
    competition.feature_flags = {**(competition.feature_flags or {}), "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])

    response = logged_in(client_for, competition, reviewer_b.user).get("/review/")

    assert response.status_code == 403


# --- wydanie D: domknięcie ekranów, które do wydania C zawężały się „przez edycję” -------------
#
# Cztery ekrany panelu zostały po wydaniu C z zakresem zbudowanym z tego, co akurat było pod ręką:
# retencja liczyła plan konkursu, ale przycisk uruchamiał przebieg całej instalacji; kolejka
# zgłoszeń nie zawężała się wcale; klucze API, odbiorcy webhooków i szablony dokumentów uznawały
# wiersz „na wszystkie edycje” za wspólną półkę instalacji. Wydanie D dało wszystkim tym modelom
# własną kolumnę konkursu – te testy pilnują, że ekrany faktycznie z niej korzystają.


def test_retention_run_now_does_not_touch_another_competition(coordinator_a, other_competition):
    """Przycisk „Wykonaj teraz” jest nieodwracalny, więc nie ma prawa wyjść poza swój konkurs.

    Świat B jest zbudowany tak, żeby przebieg **chciał** go ruszyć: edycja archiwalna z minionym
    terminem retencji, etap z ogłoszonymi wynikami i uczestnik z jednym wpisem. Gdyby zakres nie
    działał, konto sąsiada zostałoby zanonimizowane jednym kliknięciem pod cudzą domeną – a tego
    nie da się cofnąć ani wytłumaczyć.
    """
    from datetime import timedelta

    from apps.accounts.profile import ANONYMISED_EMAIL_DOMAIN
    from apps.competitions.models import StageKind
    from apps.competitions.tests.factories import EditionFactory

    past = timezone.now() - timedelta(days=31 * 30)
    edition_b = EditionFactory(competition=other_competition, data_retention_months=24)
    stage_b = StageFactory(
        competition=other_competition,
        edition=edition_b,
        kind=StageKind.ELIM,
        opens_at=past - timedelta(days=30),
        deadline_at=past,
        review_deadline_at=past + timedelta(days=14),
        appeal_window_opens_at=past + timedelta(days=16),
        appeal_window_closes_at=past + timedelta(days=23),
        results_published_at=past + timedelta(days=24),
    )
    stranger = ParticipantFactory(competition=other_competition, user__email="sasiad@example.invalid")
    StageEntryFactory(competition=other_competition, participant=stranger, stage=stage_b)

    listing = coordinator_a.get("/coordinator/retention/").content.decode()
    coordinator_a.post("/coordinator/retention/")

    stranger.user.refresh_from_db()
    assert edition_b.year_label not in listing
    assert ANONYMISED_EMAIL_DOMAIN not in stranger.user.email


def test_support_queue_lists_only_this_competitions_tickets(coordinator_a, competition, other_competition):
    """Sprawa idzie do organizatora **swojego** konkursu: on ją czyta, on na nią odpowiada."""
    from apps.support.tests.factories import SupportTicketFactory

    mine = SupportTicketFactory(competition=competition, subject="Sprawa tutejsza")
    stranger = SupportTicketFactory(competition=other_competition, subject="Sprawa sąsiada")

    content = coordinator_a.get("/coordinator/support/").content.decode()

    assert mine.subject in content
    assert stranger.subject not in content


def test_platform_support_ticket_stays_with_the_operator(coordinator_a):
    """Zgłoszenie **do operatora platformy** (bez konkursu) nie jest sprawą żadnego koordynatora.

    ``SupportTicket.competition`` jest ``null=True`` właśnie dla tych spraw (§ 3.2), a kolejka
    stoi na ``for_competition``, które – inaczej niż ``AuditLog.visible_to`` – wierszy bez
    konkursu **nie** oddaje. Tu nie ma czego pokazywać koordynatorowi: adresatem jest ktoś inny.
    """
    from apps.support.models import SupportTicket

    ticket = SupportTicket.objects.create(
        email="ktos@example.invalid", subject="Sprawa do operatora", competition=None
    )

    assert ticket.subject not in coordinator_a.get("/coordinator/support/").content.decode()
    assert coordinator_a.get(f"/coordinator/support/{ticket.pk}/").status_code == 404


def test_api_key_of_another_competition_is_invisible_and_cannot_be_revoked(coordinator_a, other_competition):
    """Klucz „na wszystkie edycje” jest od wydania D kluczem jednego konkursu, nie instalacji."""
    from apps.integrations.services import create_api_key

    key, _token = create_api_key(
        name="Partner sąsiada", scopes=["read:results"], competition=other_competition
    )

    content = coordinator_a.get("/coordinator/integrations/").content.decode()

    assert key.name not in content
    assert coordinator_a.post(f"/coordinator/integrations/keys/{key.pk}/revoke/").status_code == 404


def test_webhook_endpoint_of_another_competition_is_not_found(coordinator_a, other_competition):
    """Odbiorca bez edycji też ma właściciela – inaczej zdarzenia A jechałyby na serwer B."""
    from apps.integrations.services import create_endpoint

    endpoint = create_endpoint(
        url="https://sasiad.example.invalid/hook",
        events=["stage.closed"],
        competition=other_competition,
    )

    content = coordinator_a.get("/coordinator/integrations/").content.decode()

    assert endpoint.url not in content
    assert coordinator_a.post(f"/coordinator/integrations/webhooks/{endpoint.pk}/delete/").status_code == 404


def test_certificate_template_of_another_competition_is_not_found(coordinator_a, other_competition):
    """Winieta „na wszystkie edycje” sąsiada nie ma się pojawić ani na liście, ani pod adresem."""
    from apps.results.models import CertificateTemplate

    template = CertificateTemplate.objects.create(
        name="Winieta sąsiada", kind="", competition=other_competition
    )

    content = coordinator_a.get("/coordinator/certificates/templates/").content.decode()

    assert template.name not in content
    assert coordinator_a.get(f"/coordinator/certificates/templates/{template.pk}/").status_code == 404


def test_new_certificate_template_belongs_to_this_competition(coordinator_a, competition):
    """Szablon wgrany pod domeną konkursu jest jego szablonem – bez pola i bez pytania."""
    from apps.results.models import CertificateTemplate

    coordinator_a.post(
        "/coordinator/certificates/templates/new/",
        {"name": "Winieta tutejsza", "kind": "", "edition": "", "is_active": "on"},
    )

    saved = CertificateTemplate.objects.get(name="Winieta tutejsza")
    assert saved.competition_id == competition.pk


def test_accounts_list_hides_a_participant_of_another_competition(
    coordinator_a, competition, other_competition
):
    """Konto „niczyje” zostaje widoczne, ale konto startujące **wyłącznie u sąsiada** – nie.

    Reguła T5 („bez ani jednego członkostwa = widoczne dla każdego”) zostaje, bo konto bez ról
    jest w tej bazie stanem realnym. Profil uczestnika jest jednak drugim, mocniejszym dowodem
    własności: wiersz ``Participant`` bywa dopisany poza serwisem nadającym role, a razem z nim
    na listę weszłyby nazwisko, szkoła i kod publiczny cudzego ucznia.
    """
    stranger = ParticipantFactory(
        competition=other_competition, user__email="tylko-u-sasiada@example.invalid"
    )
    nobodys = UserFactory(email="niczyje@example.invalid")

    content = coordinator_a.get("/coordinator/accounts/").content.decode()

    assert nobodys.email in content
    assert stranger.user.email not in content
    assert coordinator_a.get(f"/coordinator/accounts/{stranger.user.pk}/").status_code == 404


def test_accounts_list_shows_the_role_of_this_competition(coordinator_a, competition, other_competition):
    """Kolumna „rola” mówi, kim ta osoba jest **tutaj** – a nie kim bywa gdzie indziej.

    Układ testu jest tym, który powstaje sam: recenzent olimpiady kwantowej startuje jako
    uczestnik w olimpiadzie sąsiada. Na liście konkursu A ma stać „członek komitetu”, bo profil
    komitetu należy do A; profil uczestnika konkursu B nie jest tu żadną rolą i nie ma prawa
    przykryć tamtej etykiety.
    """
    reviewer = ActiveReviewerFactory(competition=competition)
    grant_membership(reviewer.user, competition, CompetitionRole.REVIEWER)
    elsewhere = ParticipantFactory(competition=other_competition, user=reviewer.user)

    content = coordinator_a.get(f"/coordinator/accounts/{reviewer.user.pk}/").content.decode()

    assert "członek komitetu" in content
    assert elsewhere.public_code not in content
