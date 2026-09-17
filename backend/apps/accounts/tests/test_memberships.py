"""T2: role w konkursie (``Membership``), ``has_role`` i profil uczestnika per konkurs.

Przedmiotem tego pakietu jest **jedna** reguła: kto jest kim w konkursie. Dwa pytania są tu
najważniejsze i mają po własnym zestawie testów:

1. **Parzystość.** Przy wyłączonym przełączniku ``memberships_enforced`` odpowiedź ma być bajt
   w bajt taka, jak przed T2 – czyli z grupy Django. To jest warunek § 0 dokumentu („zachowaj
   działającą Olimpiadę Kwantową”): wydanie B dokłada tabelę i wypełnia ją, ale **nie** zmienia
   ani jednej odpowiedzi serwisu.
2. **Izolacja.** Przy włączonym przełączniku rola nadana w konkursie A nie ma prawa otworzyć
   niczego w konkursie B – i to niezależnie od tego, że grupa Django jest jedna dla instalacji.

Test drugiego punktu bez włączonej flagi byłby testem pustym: przy grupach globalnych koordynator
jest koordynatorem wszędzie i taki **jest** dziś stan faktyczny. Dlatego każdy test izolacji
najpierw jawnie przestawia flagę – i to jest zarazem dowód, że przełącznik cokolwiek przełącza.
"""

import pytest
from django.db import IntegrityError, transaction
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    RBAC_GROUPS,
    CompetitionRole,
    Membership,
    Participant,
)
from apps.accounts.permissions import IsCoordinator, IsParticipant
from apps.accounts.services import (
    default_competition,
    grant_role,
    has_role,
    memberships_enforced,
    participant_for,
    roles_for,
)
from apps.tenancy.permissions import IsCompetitionCoordinator

from .factories import CoordinatorFactory, ParticipantFactory, UserFactory


def enforce(competition):
    """Przestawia konkurs na autoryzację po członkostwach (wydanie C).

    Zapis przez ``feature_flags``, a nie przez podmianę ``has_feature``: przełącznik ma być
    sprawdzony tą samą drogą, którą przestawi go wdrożenie – czyli wartością w bazie.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), "memberships_enforced": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def call(view_class, user, competition):
    """Woła widok DRF chroniony ``view_class`` tak, jak zrobiłaby to warstwa: z konkursem żądania.

    ``request.competition`` ustawia w produkcji ``apps.tenancy.middleware``; tutaj wpisujemy je
    wprost, bo przedmiotem testu jest **uprawnienie**, a nie rozstrzyganie hosta (to ma własny
    pakiet w ``apps/tenancy/tests/test_resolution.py``).
    """
    request = APIRequestFactory().get("/test/")
    request.competition = competition
    force_authenticate(request, user=user)
    return view_class.as_view()(request)


class CoordinatorOnlyView(APIView):
    permission_classes = [IsCoordinator]

    def get(self, request):
        return Response({"ok": True})


class ScopedCoordinatorOnlyView(APIView):
    permission_classes = [IsCompetitionCoordinator]

    def get(self, request):
        return Response({"ok": True})


class ParticipantOnlyView(APIView):
    permission_classes = [IsParticipant]

    def get(self, request):
        return Response({"ok": True})


# --- katalog ról ------------------------------------------------------------------------------


def test_role_values_match_the_rbac_group_names():
    """Wartości ról = nazwy grup. Na tym stoi backfill i to nie może zależeć od niczyjej pamięci."""
    assert set(CompetitionRole.values) == set(RBAC_GROUPS)


# --- nadawanie roli ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_granting_a_role_writes_both_the_membership_and_the_django_group(competition):
    """Jedno wywołanie, dwa zapisy: rola w konkursie i uprawnienie do panelu redakcyjnego.

    Rozdzielenie ich na dwa serwisy skończyłoby się kontem, które ma rolę, ale nie ma ``/cms/``.
    """
    coordinator = CoordinatorFactory()
    user = UserFactory()

    grant_role(user, CompetitionRole.COORDINATOR, competition=competition, granted_by=coordinator)

    assert user.groups.filter(name=GROUP_COORDINATOR).exists()
    membership = Membership.objects.get(user=user, competition=competition)
    assert membership.role == CompetitionRole.COORDINATOR
    assert membership.granted_by == coordinator


@pytest.mark.django_db
def test_granting_the_same_role_twice_leaves_one_row(competition):
    """Nadanie roli jest faktem, a nie zdarzeniem – powtórzenie nie ma prawa podnieść wyjątku."""
    user = UserFactory()

    grant_role(user, CompetitionRole.REVIEWER, competition=competition)
    grant_role(user, CompetitionRole.REVIEWER, competition=competition)

    assert Membership.objects.filter(user=user, role=CompetitionRole.REVIEWER).count() == 1


@pytest.mark.django_db
def test_granting_a_role_without_a_competition_still_writes_the_group(competition):  # noqa: ARG001
    """Baza bez konkursu (świeża instalacja) nie może wywrócić rejestracji.

    Pusta kolumna jest widoczna w kontroli przed ``NOT NULL`` (§ 4.4) – wyjątek w tym miejscu
    byłby błędem 500 w formularzu uczestnika.
    """
    user = UserFactory()

    assert grant_role(user, CompetitionRole.PARTICIPANT, competition=None) is None
    assert user.groups.filter(name=GROUP_PARTICIPANT).exists()


@pytest.mark.django_db
def test_an_unknown_role_is_rejected_at_the_call_site(competition):
    """Literówka w nazwie roli ma podnieść wyjątek, a nie wyglądać jak brak uprawnień."""
    with pytest.raises(ValueError):
        grant_role(UserFactory(), "koordynator", competition=competition)


# --- has_role: przełącznik wyłączony (stan dzisiejszy) -----------------------------------------


@pytest.mark.django_db
def test_with_the_switch_off_the_django_group_decides(competition):
    """Konto z grupą, ale **bez** członkostwa, jest koordynatorem – dokładnie jak przed T2."""
    user = UserFactory(groups=[GROUP_COORDINATOR])
    Membership.objects.filter(user=user).delete()

    assert memberships_enforced(competition) is False
    assert has_role(user, competition, CompetitionRole.COORDINATOR) is True
    assert call(CoordinatorOnlyView, user, competition).status_code == 200


@pytest.mark.django_db
def test_with_the_switch_off_a_membership_alone_is_not_enough(competition):
    """Symetrycznie: samo członkostwo nie otwiera niczego, dopóki reguła czyta grupy.

    Ten test pilnuje, żeby backfill nie „przeciekł” w drugą stronę – wydanie B ma wypełnić tabelę
    i na tym poprzestać.
    """
    user = UserFactory()
    Membership.objects.create(user=user, competition=competition, role=CompetitionRole.COORDINATOR)

    assert has_role(user, competition, CompetitionRole.COORDINATOR) is False
    assert call(CoordinatorOnlyView, user, competition).status_code == 403


@pytest.mark.django_db
def test_without_a_competition_the_rule_falls_back_to_groups():
    """Żądanie pod hostem bez konkursu odpowiada tak, jak odpowiadało przed wielokonkursowością."""
    user = UserFactory(groups=[GROUP_COORDINATOR])

    assert has_role(user, None, CompetitionRole.COORDINATOR) is True


# --- has_role: przełącznik włączony (wydanie C) ------------------------------------------------


@pytest.mark.django_db
def test_with_the_switch_on_the_membership_decides(competition):
    user = UserFactory(groups=[GROUP_COORDINATOR])
    Membership.objects.filter(user=user).delete()
    enforce(competition)

    assert has_role(user, competition, CompetitionRole.COORDINATOR) is False

    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)

    assert has_role(user, competition, CompetitionRole.COORDINATOR) is True


@pytest.mark.django_db
def test_a_coordinator_of_one_competition_is_not_a_coordinator_of_the_other(competition, other_competition):
    """Sedno całej zmiany: globalna grupa przestaje być przepustką do cudzego konkursu."""
    user = CoordinatorFactory()
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)
    enforce(competition)
    enforce(other_competition)

    assert has_role(user, competition, CompetitionRole.COORDINATOR) is True
    assert has_role(user, other_competition, CompetitionRole.COORDINATOR) is False
    # 403, a nie 404: rola jest sprawą konta, nie cudzego obiektu (§ 3.6).
    assert call(CoordinatorOnlyView, user, other_competition).status_code == 403
    assert call(ScopedCoordinatorOnlyView, user, other_competition).status_code == 403
    assert call(ScopedCoordinatorOnlyView, user, competition).status_code == 200


@pytest.mark.django_db
def test_a_request_without_a_competition_is_closed_for_the_scoped_permission(competition):
    """``IsCompetitionCoordinator`` wymaga konkursu **zawsze** – także przy wyłączonej fladze."""
    user = CoordinatorFactory()

    assert call(ScopedCoordinatorOnlyView, user, None).status_code == 403
    assert call(CoordinatorOnlyView, user, competition).status_code == 200


@pytest.mark.django_db
def test_a_superuser_is_not_escalated_into_a_coordinator(competition):
    """Operator platformy ma ``/admin/``, a nie cichy dostęp do danych uczestników."""
    operator = UserFactory(is_superuser=True, is_staff=True)
    enforce(competition)

    assert has_role(operator, competition, CompetitionRole.COORDINATOR) is False
    assert call(CoordinatorOnlyView, operator, competition).status_code == 403


@pytest.mark.django_db
def test_a_blocked_account_has_no_roles_at_all(competition):
    """Blokada konta ma zamykać dostęp od razu, bez sprzątania członkostw."""
    user = CoordinatorFactory()
    grant_role(user, CompetitionRole.COORDINATOR, competition=competition)
    enforce(competition)
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert has_role(user, competition, CompetitionRole.COORDINATOR) is False
    assert roles_for(user, competition) == set()


# --- roles_for --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_roles_for_returns_the_same_set_with_the_switch_off_and_on(competition):
    """Parzystość na komplecie ról: przełączenie flagi nie zmienia tego, kim ta osoba jest."""
    user = UserFactory(groups=[GROUP_REVIEWER])
    grant_role(user, CompetitionRole.REVIEWER, competition=competition)

    before = roles_for(user, competition)
    enforce(competition)
    after = roles_for(user, competition)

    assert before == {CompetitionRole.REVIEWER.value} == after


@pytest.mark.django_db
def test_roles_for_ignores_groups_that_are_not_competition_roles(competition):
    """Grupa spoza katalogu ról (np. techniczna) nie jest rolą w konkursie i nie ma nią zostać."""
    from django.contrib.auth.models import Group

    user = UserFactory(groups=[GROUP_REVIEWER])
    technical, _ = Group.objects.get_or_create(name="operatorzy-kopii-zapasowych")
    user.groups.add(technical)

    assert roles_for(user, competition) == {CompetitionRole.REVIEWER.value}


# --- profil uczestnika per konkurs -------------------------------------------------------------


@pytest.mark.django_db
def test_participant_for_returns_the_profile_of_this_competition(competition):
    participant = ParticipantFactory(competition=competition)

    assert participant_for(participant.user, competition) == participant


@pytest.mark.django_db
def test_participant_for_does_not_return_a_profile_of_another_competition(competition, other_competition):
    """Profil jest oświadczeniem złożonym **jednemu** organizatorowi – drugi go nie widzi."""
    participant = ParticipantFactory(competition=other_competition)

    assert participant_for(participant.user, other_competition) == participant
    assert participant_for(participant.user, competition) is None


@pytest.mark.django_db
def test_participant_for_without_a_competition_behaves_like_the_old_one_to_one(competition):
    """``None`` znaczy „nie wiadomo który” i oddaje profil bez zawężania.

    Tak woła tę funkcję kod spoza żądania (komenda, zadanie) na bazie jednokonkursowej: pytanie
    „profil tej osoby” ma tam dokładnie jedną poprawną odpowiedź.
    """
    participant = ParticipantFactory(competition=competition)

    assert participant_for(participant.user, None) == participant


@pytest.mark.django_db
def test_a_profile_without_an_owner_cannot_be_created_any_more(competition):
    """Wydanie D domknęło kolumnę na ``NOT NULL`` – wiersza bez właściciela nie da się już zapisać.

    Test pilnuje więzu w **bazie**, a nie w formularzu: to on jest ostatnią linią izolacji i to on
    ma przewrócić każdą drogę zapisu, która pominęłaby konkurs (§ 4.1, wydanie D).
    """
    participant = ParticipantFactory(competition=competition)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Participant.objects.filter(pk=participant.pk).update(competition=None)


@pytest.mark.django_db
def test_the_participant_permission_needs_both_the_role_and_a_profile_here(competition, other_competition):
    """Rola bez profilu w tym konkursie to 403, a nie 500 w widoku czytającym profil."""
    participant = ParticipantFactory(competition=other_competition)
    enforce(competition)
    enforce(other_competition)
    grant_role(participant.user, CompetitionRole.PARTICIPANT, competition=other_competition)

    assert call(ParticipantOnlyView, participant.user, other_competition).status_code == 200
    assert call(ParticipantOnlyView, participant.user, competition).status_code == 403


# --- konkurs domyślny --------------------------------------------------------------------------


@pytest.mark.django_db
def test_default_competition_takes_the_one_from_the_context(competition, as_competition):
    with as_competition(competition):
        assert default_competition() == competition


@pytest.mark.django_db
def test_default_competition_falls_back_to_the_only_competition(competition, as_competition):
    """Zadanie Celery i komenda nie mają żądania – w bazie jednokonkursowej odpowiedź jest jedna."""
    with as_competition(None):
        assert default_competition() == competition


@pytest.mark.django_db
def test_default_competition_refuses_to_guess_between_two(competition, other_competition, as_competition):  # noqa: ARG001
    """Przy dwóch konkursach „pierwszy z brzegu” przypisałby uczestnika cudzemu organizatorowi."""
    with as_competition(None):
        assert default_competition() is None


# --- serwisy zapisu ustawiają właściciela -------------------------------------------------------


@pytest.mark.django_db
def test_registration_stamps_the_participant_and_the_membership_with_the_competition(
    competition, open_registration
):  # noqa: ARG001
    """Rejestracja przez serwis zostawia profil **i** rolę w jednym konkursie – tym z kontekstu."""
    from apps.accounts.services import register_participant

    participant = register_participant(
        email="nowy@example.invalid",
        password="Poprawne-Haslo-2026",
        first_name="Jan",
        last_name="Testowy",
        district="mazowieckie",
        birth_year=2008,
        grade=3,
        gdpr_consent=True,
        terms_consent=True,
        # Rocznik małoletni – komplet zgód jest warunkiem rejestracji i ta reguła nie jest
        # przedmiotem tego testu, tylko jego wejściem (bramkę sprawdza ``test_consents.py``).
        guardian_consent=True,
        phone="+48600100200",
        school="LO nr 1",
    )

    assert participant.competition == competition
    assert Membership.objects.filter(
        user=participant.user, competition=competition, role=CompetitionRole.PARTICIPANT
    ).exists()


@pytest.mark.django_db
def test_the_scoped_manager_filters_by_competition(competition, other_competition):
    """``for_competition`` jest w managerze, nie w widoku – filtr dopisany w widoku bywa pominięty."""
    mine = ParticipantFactory(competition=competition)
    theirs = ParticipantFactory(competition=other_competition)

    assert list(Participant.objects.for_competition(competition)) == [mine]
    assert list(Participant.objects.for_competition(other_competition)) == [theirs]
    # ``None`` nie widzi niczego – domyślnie zamknięte.
    assert list(Participant.objects.for_competition(None)) == []
