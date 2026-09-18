"""Marka konkursu w pliku ``.ics``: nazwa kalendarza, ``PRODID``, człon ``UID`` i nazwa pliku.

Cztery napisy i **trzy różne drogi**, bo każdy z nich znaczy u uczestnika co innego
(``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4, zadanie T14):

- ``X-WR-CALNAME`` to nazwa kalendarza widoczna w kliencie – idzie modułem marki
  (``apps.tenancy.branding.calendar_name``), tą samą drogą, co podpis pod listem,
- ``PRODID`` identyfikuje **program**, który plik złożył, więc jego zmiana niczego nie duplikuje –
  wzorzec z odwrotem na dzisiejszy literał,
- człon ``UID`` jest **kluczem wydarzenia**: klient kalendarza po nim rozpoznaje, że to ten sam
  finał, co wczoraj. Zmiana nie poprawia wpisu, tylko dokłada drugi obok, a plik bywa
  zasubskrybowany – dlatego ziarno skrótu jest zamrożone, a domena zmienia się **wyłącznie** przy
  świadomym włączeniu marki,
- nazwa pliku jest kosmetyką okna pobierania i idzie ze slugu konkursu.

Większość testów jest **bez bazy**: konkurs w pamięci opisuje świat testu dokładniej niż wiersz
z migracji, a moduł marki nie zadaje ani jednego zapytania. Na końcu stoją dwa testy przez
prawdziwe ``GET /me/calendar.ics`` – bo to widok rozstrzyga, czy konkurs w ogóle dojechał do
generatora. Niezmienność Konkursu #1 zamraża osobno ``apps/tenancy/tests/test_branding.py`` (T16).
"""

from datetime import date

import pytest

from apps.accounts.tests.factories import ParticipantFactory
from apps.cms.calendar import (
    ICS_FILENAME,
    PRODID,
    UID_DOMAIN,
    CalendarItem,
    calendar_ics,
    ics_filename,
    uid_domain,
)
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.tenancy.branding import BRANDING_FLAG
from apps.tenancy.models import Competition

ICS_URL = "/me/calendar.ics"

#: Konkurs drugi tak, jak widzi go ten moduł: własna domena, własny slug, nazwa skrócona.
DRUGI = dict(
    name="Olimpiada Matematyczna Juniorów",
    short_name="Olimpiada Juniorów",
    slug="juniorow",
    primary_domain="olimpiadajuniorow.pl",
)


def competition_with(*, branded: bool, **overrides) -> Competition:
    """Konkurs w pamięci z flagą marki ustawioną wprost (wzorzec z ``test_branding_unit.py``)."""
    values = {**DRUGI, **overrides}
    return Competition(feature_flags={BRANDING_FLAG: True} if branded else {}, **values)


def all_day(**overrides) -> CalendarItem:
    values = {
        "kind": "stage",
        "title": "Zjazd finałowy",
        "start": date(2026, 4, 10),
        "end": date(2026, 4, 12),
    }
    values.update(overrides)
    return CalendarItem(**values)


def lines(text: str) -> list[str]:
    return text.split("\r\n")


def uids(text: str) -> list[str]:
    return [line for line in lines(text) if line.startswith("UID:")]


# --- odwrót: bez konkursu i bez flagi wychodzi dzisiejszy plik ------------------------------------


def test_file_without_a_competition_is_todays_file():
    """Wołający bez konkursu (test jednostkowy, komenda) dostaje plik sprzed etapu 2."""
    text = calendar_ics([all_day()])

    assert f"PRODID:{PRODID}" in lines(text)
    assert "X-WR-CALNAME:Olimpiada Kwantowa" in lines(text)
    assert uids(text) == [f"UID:{all_day().uid}"]
    assert uid_domain() == UID_DOMAIN
    assert ics_filename() == ICS_FILENAME


def test_competition_without_the_flag_keeps_every_string():
    """Konkurs #1 ma ``feature_flags`` bez flagi marki – i to jest cały mechanizm ciągłości.

    Konkurs w tym teście ma **inną** nazwę, inną domenę i inny slug niż Olimpiada Kwantowa,
    a mimo to wychodzi z niego dzisiejszy plik: dopóki flagi nie ma, żadne z tych pól nie jest
    czytane.
    """
    competition = competition_with(branded=False)

    text = calendar_ics([all_day()], competition=competition)

    assert f"PRODID:{PRODID}" in lines(text)
    assert "X-WR-CALNAME:Olimpiada Kwantowa" in lines(text)
    assert uids(text) == [f"UID:{all_day().uid}"]
    assert uid_domain(competition) == UID_DOMAIN
    assert ics_filename(competition) == ICS_FILENAME


# --- flaga włączona: cztery napisy z konkursu -----------------------------------------------------


def test_flag_puts_the_competition_in_all_four_strings():
    competition = competition_with(branded=True)

    text = calendar_ics([all_day()], competition=competition)

    assert "PRODID:-//Olimpiada Juniorów//Kalendarz uczestnika//PL" in lines(text)
    assert "X-WR-CALNAME:Olimpiada Juniorów" in lines(text)
    assert uids(text) == [f"UID:{all_day().uid_in('olimpiadajuniorow.pl')}"]
    assert ics_filename(competition) == "juniorow.ics"


def test_uid_seed_does_not_change_with_the_branding():
    """Skrót przed ``@`` jest ten sam w obu konkursach – zmienia się **wyłącznie** domena.

    To jest cała ostrożność tego zadania wyrażona jedną asercją: ``UID`` wolno przenieść do innej
    domeny (konkurs, który marki jeszcze nie ogłaszał), ale nie wolno przeliczyć inaczej. Gdyby
    ziarno skrótu zależało od konkursu, ta sama pozycja miałaby dwa różne identyfikatory także
    tam, gdzie domena się nie zmieniła – czyli w każdym istniejącym kalendarzu.
    """
    branded = calendar_ics([all_day()], competition=competition_with(branded=True))
    plain = calendar_ics([all_day()], competition=competition_with(branded=False))

    assert uids(branded)[0].split("@")[0] == uids(plain)[0].split("@")[0]
    assert uids(branded)[0].endswith("@olimpiadajuniorow.pl")
    assert uids(plain)[0].endswith(f"@{UID_DOMAIN}")


def test_competition_without_a_domain_or_slug_falls_back_to_todays_values():
    """Pola puste znaczą „nie ma czego podstawić”, a nie „wstaw pustkę”.

    ``UID`` bez członu domenowego złamałby RFC 5545, a nazwa pliku ``.ics`` bez trzonu byłaby
    plikiem o nazwie samego rozszerzenia. W obu wypadkach poprawną odpowiedzią jest dzisiejsza
    wartość – konkurs bez domeny i tak nie ma jeszcze własnego adresu.
    """
    competition = competition_with(branded=True, primary_domain="", slug="")

    assert uid_domain(competition) == UID_DOMAIN
    assert ics_filename(competition) == ICS_FILENAME


def test_a_comma_in_the_name_does_not_split_the_header():
    """Przecinek w nazwie konkursu jest cytowany – RFC 5545 §3.3.11 dotyczy każdej wartości TEXT.

    Bez cytowania klient przeczytałby ``X-WR-CALNAME`` jako listę dwóch wartości i pokazał
    uczestnikowi kalendarz nazwany połową nazwy organizatora.
    """
    competition = competition_with(branded=True, short_name="Olimpiada Juniorów, edycja X")

    text = calendar_ics([], competition=competition)

    assert "X-WR-CALNAME:Olimpiada Juniorów\\, edycja X" in lines(text)


# --- przez widok ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_downloaded_file_carries_todays_strings_for_the_first_competition(web_client, competition):
    """Ten sam plik, co przed etapem 2 – pobrany tą samą drogą, co pobiera go uczestnik.

    Fikstury edycji i uczestnika są tu jawne (a nie z ``conftest``), bo test ma pokazać **cały**
    przebieg: konkurs → edycja → etap → pozycja kalendarza → nagłówek pliku.
    """
    edition = CurrentEditionFactory()
    StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I")
    participant = ParticipantFactory()
    web_client.force_login(participant.user)

    response = web_client.get(ICS_URL)
    body = response.content.decode("utf-8")

    assert response["Content-Disposition"] == f'attachment; filename="{ICS_FILENAME}"'
    assert f"PRODID:{PRODID}" in lines(body)
    assert "X-WR-CALNAME:Olimpiada Kwantowa" in lines(body)
    assert uids(body)
    assert all(uid.endswith(f"@{UID_DOMAIN}") for uid in uids(body))


@pytest.mark.django_db
def test_downloaded_file_follows_the_flag(web_client, competition):
    """Po włączeniu marki plik niesie markę tego konkursu – aż do nazwy pobieranego pliku.

    Flagę przestawiamy na Konkursie #1, bo to jego widok, jego edycja i jego uczestnik stoją
    w bazie testowej. Przedmiotem jest droga „widok → generator”, a nie to, który konkurs.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), BRANDING_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    edition = CurrentEditionFactory()
    StageFactory(edition=edition, kind=StageKind.ELIM, name="Etap I")
    participant = ParticipantFactory()
    web_client.force_login(participant.user)

    response = web_client.get(ICS_URL)
    body = response.content.decode("utf-8")

    assert response["Content-Disposition"] == f'attachment; filename="{competition.slug}.ics"'
    assert f"PRODID:-//{competition}//Kalendarz uczestnika//PL" in lines(body)
    assert f"X-WR-CALNAME:{competition}" in lines(body)
    assert uids(body)
    assert all(uid.endswith(f"@{competition.primary_domain}") for uid in uids(body))
