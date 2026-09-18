"""Nazwa organizatora w treści zgody bierze się z **tego** konkursu, a nie z pierwszej witryny.

Domknięcie śladu z etapu 1 (``docs/UNIWERSALNY-ETAP-1.md`` § 3.7, ``docs/UNIWERSALNY-ETAP-2.md``
§ 1.1.2): ``organizer_name`` czytało ``SiteSettings.objects.first()``, więc przy dwóch konkursach
klauzula RODO potrafiła nazwać administratorem danych cudzą fundację. To jest błąd izolacji, a nie
brak funkcji – dlatego naprawa nie stoi za żadną flagą i dlatego testy są tutaj, a nie w pliku
opisującym nową zdolność.

Dla Konkursu #1 nie zmienia się nic: migracja ``tenancy.0002`` przepisała mu nazwę organizatora
z ``cms.SiteSettings``, więc oba źródła niosą ten sam napis (``test_invariants`` pilnuje treści
zgód, a złote testy – strony rejestracji).
"""

import pytest

from apps.accounts import consents
from apps.accounts.consents import DEFAULT_ORGANIZER_NAME, ConsentKind


def site_settings_for(competition, organizer: str):
    """Ustawienia serwisu witryny tego konkursu z podaną nazwą organizatora."""
    from apps.cms.models import SiteSettings

    row = SiteSettings.for_site(competition.site)
    row.organizer_name = organizer
    row.save(update_fields=["organizer_name"])
    return row


def test_organizer_name_comes_from_the_request_competition(competition, other_competition, as_competition):
    """Każdy konkurs nazywa swojego organizatora – tego, który jest administratorem danych."""
    competition.organizer_name = "Fundacja Pierwsza"
    competition.save(update_fields=["organizer_name"])
    other_competition.organizer_name = "Instytut Drugi"
    other_competition.save(update_fields=["organizer_name"])

    with as_competition(competition):
        assert consents.organizer_name() == "Fundacja Pierwsza"
    with as_competition(other_competition):
        assert consents.organizer_name() == "Instytut Drugi"


def test_organizer_name_takes_the_competition_given_explicitly(competition, other_competition):
    """Konkurs podany wprost wygrywa z kontekstem – dla kodu poza żądaniem (zadania, komendy)."""
    other_competition.organizer_name = "Instytut Drugi"
    other_competition.save(update_fields=["organizer_name"])

    assert consents.organizer_name(other_competition) == "Instytut Drugi"


def test_organizer_name_falls_back_to_the_site_settings_of_that_competition(
    competition, other_competition, as_competition
):
    """Konkurs bez wypełnionej nazwy organizatora czyta **swoje** ustawienia serwisu, nie cudze."""
    site_settings_for(competition, "Fundacja Pierwsza")
    site_settings_for(other_competition, "Instytut Drugi")
    other_competition.organizer_name = ""
    other_competition.save(update_fields=["organizer_name"])

    with as_competition(other_competition):
        assert consents.organizer_name() == "Instytut Drugi"


def test_organizer_name_is_the_default_when_the_competition_cannot_be_resolved(
    competition, other_competition, unbound_competition
):
    """Dwa konkursy i puste wskazanie: wartość domyślna, a nie nazwa pierwszego z brzegu.

    „Nie wiadomo, o który konkurs chodzi” nie ma bezpiecznej odpowiedzi wziętej z bazy – każda
    byłaby czyjaś. Formularz rejestracji ma się przy tym otworzyć, więc odwrót jest miękki.
    """
    competition.organizer_name = "Fundacja Pierwsza"
    competition.save(update_fields=["organizer_name"])

    assert consents.organizer_name() == DEFAULT_ORGANIZER_NAME


@pytest.mark.parametrize("renderer", [consents.label, consents.plain_text])
def test_privacy_consent_names_the_organizer_of_that_competition(
    competition, other_competition, as_competition, renderer
):
    """Treść zgody RODO – w HTML i czystym tekstem – nazywa administratora danych tego konkursu."""
    other_competition.organizer_name = "Instytut Drugi"
    other_competition.save(update_fields=["organizer_name"])

    with as_competition(other_competition):
        rendered = str(renderer(consents.BY_KIND[ConsentKind.PRIVACY]))

    assert "Instytut Drugi" in rendered
