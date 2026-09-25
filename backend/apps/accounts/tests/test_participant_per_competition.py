"""Wydanie D: profil uczestnika należy do konkursu, a jedno konto ma ich tyle, w ilu startuje.

Przedmiotem pakietu jest cena decyzji z § 3.3 dokumentu ``docs/UNIWERSALNY-ETAP-1.md`` – ta sama,
którą § 6 (T2) wymienia jako największe ryzyko całego etapu:

1. ``Participant.user`` przestał być ``OneToOne``. Relacja odwrotna nazywa się ``participations``,
   a nazwy ``participant`` **nie ma celowo**: przeoczone ``user.participant`` ma podnieść
   ``AttributeError`` przy pierwszym wywołaniu, a nie oddać profil z przypadkowego konkursu.
2. Kod publiczny jest unikalny **w konkursie**, a prefiks jest własnością konkursu. Konkurs #1
   zostaje przy ``OLM-``, więc żaden istniejący kod się nie zmienia (§ 0).
3. Kolumna konkursu jest ``NOT NULL``, a migracja domykająca sprawdza to **przed** zmianą
   schematu i przerywa wdrożenie, gdy znajdzie choć jeden wiersz bez właściciela (§ 4.4).
"""

import pytest
from django.db import IntegrityError, transaction

from apps.accounts import services
from apps.accounts.models import PUBLIC_CODE_PREFIX, Participant, generate_public_code
from apps.accounts.services import participant_for, participations_of
from apps.core.tests.migration_helpers import migrate_to, rewound_database

from .factories import ParticipantFactory, UserFactory

BEFORE = ("accounts", "0021_participant_per_competition")
AFTER = ("accounts", "0022_competition_not_null")


# --- dwa profile jednego konta ------------------------------------------------------------------


@pytest.mark.django_db
def test_one_account_holds_a_profile_in_each_competition(competition, other_competition):
    """Uczeń startujący w dwóch olimpiadach ma dwa profile i jedno konto – po jednym na organizatora."""
    user = UserFactory()
    here = ParticipantFactory(user=user, competition=competition)
    there = ParticipantFactory(user=user, competition=other_competition)

    assert participant_for(user, competition) == here
    assert participant_for(user, other_competition) == there
    assert set(participations_of(user)) == {here, there}


@pytest.mark.django_db
def test_the_old_one_to_one_accessor_is_gone(competition):
    """``user.participant`` podnosi ``AttributeError`` – to jest mitygacja z § 6 (T2), nie skutek uboczny.

    Gdyby relacja odwrotna nazywała się dalej ``participant``, każde przeoczone miejsce oddawałoby
    profil **jakiegoś** konkursu i myliło się po cichu. Zerwana nazwa zamienia cichą pomyłkę
    w wyjątek przy pierwszym wywołaniu.
    """
    participant = ParticipantFactory(competition=competition)

    with pytest.raises(AttributeError):
        participant.user.participant  # noqa: B018 - przedmiotem testu jest sam dostęp do atrybutu


@pytest.mark.django_db
def test_a_second_profile_in_the_same_competition_is_refused(competition):
    """Tyle zostaje z dawnego ``OneToOne``: jedno konto, jeden profil **w tym** konkursie."""
    user = UserFactory()
    ParticipantFactory(user=user, competition=competition)

    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():
            ParticipantFactory(user=user, competition=competition)
    assert "accounts_participant_unique_per_competition" in str(exc.value)


# --- kod publiczny ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_same_public_code_is_allowed_in_two_competitions(competition, other_competition):
    """Ten sam ciąg w dwóch konkursach to dwa różne kody w dwóch różnych tabelach wyników."""
    code = "OLM-ABC234"
    here = ParticipantFactory(competition=competition, public_code=code)
    there = ParticipantFactory(competition=other_competition, public_code=code)

    assert here.public_code == there.public_code
    assert here.competition_id != there.competition_id


@pytest.mark.django_db
def test_the_same_public_code_twice_in_one_competition_is_refused(competition):
    """Unikalność nie znika, tylko zmienia zasięg z instalacji na konkurs."""
    code = "OLM-ABC234"
    ParticipantFactory(competition=competition, public_code=code)

    with pytest.raises(IntegrityError) as exc:
        with transaction.atomic():
            ParticipantFactory(competition=competition, public_code=code)
    assert "accounts_participant_public_code_per_competition" in str(exc.value)


@pytest.mark.django_db
def test_competition_one_keeps_the_olm_prefix(competition):
    """§ 0: dla Olimpiady Kwantowej nie zmienia się nic – ani prefiks kodu, ani prefiks dyplomu."""
    assert competition.public_code_prefix == PUBLIC_CODE_PREFIX == "OLM-"
    assert competition.certificate_prefix == "OK"
    assert generate_public_code(competition).startswith("OLM-")


@pytest.mark.django_db
def test_a_new_competition_stamps_its_own_prefix_on_new_codes(other_competition):
    """Prefiks bierze się z konkursu profilu, a nie ze stałej modułu."""
    other_competition.public_code_prefix = "FIZ-"
    other_competition.save(update_fields=["public_code_prefix"])

    participant = services.create_participant_with_public_code(
        user=UserFactory(),
        competition=other_competition,
        school="LO nr 2",
        district="mazowieckie",
        birth_year=2008,
    )

    assert participant.public_code.startswith("FIZ-")


@pytest.mark.django_db
def test_a_code_generated_without_a_competition_falls_back_to_the_module_constant():
    """Kolumna ma ``default=generate_public_code``, a Django woła ``default`` bez argumentów."""
    assert generate_public_code().startswith(PUBLIC_CODE_PREFIX)
    assert generate_public_code(None).startswith(PUBLIC_CODE_PREFIX)


# --- zapytanie kontrolne przed ``NOT NULL`` (§ 4.4) ----------------------------------------------
#
# Dwa testy migracji w module, który poza nimi ma zwykłe testy – dlatego przewinięcie jest
# fiksturą **testu**, a nie modułu: przewinięta baza nie może zostać pod testami, które jej nie
# zamawiały. Transakcja fikstury jest wycofywana po teście, a z nią przewinięcie
# (``apps/core/tests/migration_helpers.py``).


@pytest.fixture
def rewound_apps(django_db_setup, django_db_blocker):
    with rewound_database(django_db_blocker, BEFORE) as db:
        yield db.apps


def _orphan_participant(apps) -> int:
    """Wiersz uczestnika bez konkursu – dokładnie to, czego migracja domykająca ma nie przepuścić."""
    User = apps.get_model("accounts", "User")
    Participant = apps.get_model("accounts", "Participant")
    user = User.objects.create(email="bez-konkursu@example.test", is_active=True)
    return Participant.objects.create(
        user=user,
        competition=None,
        public_code="OLM-SIEROT",
        school="LO nr 1",
        district="mazowieckie",
        birth_year=2008,
    ).pk


@pytest.mark.django_db
@pytest.mark.migrations
def test_the_control_query_stops_the_deployment_and_names_the_table(rewound_apps):
    """Niezerowy wynik zapytania kontrolnego = ``RuntimeError`` **przed** zmianą schematu.

    Sprawdzamy dwie rzeczy naraz, bo obie są treścią § 4.4: że wdrożenie staje i że komunikat
    wymienia tabelę z nazwy. Sam ``NotNullViolation`` z PostgreSQL też by je zatrzymał, ale nie
    powiedziałby ani ile wierszy jest do poprawienia, ani czy chodzi o jedną tabelę, czy o pięć.
    """
    pk = _orphan_participant(rewound_apps)

    with pytest.raises(RuntimeError) as exc:
        migrate_to(AFTER)
    assert "accounts_participant" in str(exc.value)
    # Wdrożenie stanęło przed zmianą schematu: wiersz czeka na poprawkę, nic nie zostało skasowane.
    assert rewound_apps.get_model("accounts", "Participant").objects.filter(pk=pk).exists()


@pytest.mark.django_db
@pytest.mark.migrations
def test_a_database_without_orphans_migrates_cleanly(rewound_apps):  # noqa: ARG001 - jw.
    """Ta sama migracja na bazie po backfillu przechodzi i domyka kolumnę na ``NOT NULL``."""
    migrate_to(AFTER)

    assert not Participant._meta.get_field("competition").null
