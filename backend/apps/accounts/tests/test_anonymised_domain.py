"""Domena adresu po anonimizacji konta: daną zostaje, konfiguracją nie jest.

Adres ``deleted-<pk>@invalid.olimpiadakwantowa.pl`` stoi w kolumnie logowania
(``USERNAME_FIELD = "email"``) pod więzem ``accounts_user_email_ci_uniq``, więc nie jest napisem,
który da się „poprawić” później: przepisanie go migracją zmieniłoby klucz logowania kont, których
właściciele skorzystali z prawa do usunięcia danych. Dlatego etap 2 rozstrzyga tu wyłącznie
o adresach nadawanych **od teraz** (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4, zadanie T14), a kont
już zanonimizowanych nie rusza nic.

Że Konkurs #1 zostaje przy swojej domenie także w nowych anonimizacjach, zamraża
``apps/tenancy/tests/test_branding.py`` (T16) – tutaj stoi reguła, czyli co się dzieje z konkursem,
który markę ma włączoną.
"""

import pytest

from apps.accounts.profile import ANONYMISED_EMAIL_DOMAIN, anonymise_account, anonymised_email_domain
from apps.accounts.tests.factories import ParticipantFactory
from apps.tenancy.branding import BRANDING_FLAG
from apps.tenancy.models import Competition


def competition_with(*, branded: bool, primary_domain: str = "olimpiadajuniorow.pl") -> Competition:
    """Konkurs w pamięci: reguła domeny nie zadaje zapytań i nie potrzebuje wiersza."""
    return Competition(
        name="Olimpiada Matematyczna Juniorów",
        primary_domain=primary_domain,
        feature_flags={BRANDING_FLAG: True} if branded else {},
    )


def test_without_a_competition_the_domain_is_todays():
    """Kosiarka retencyjna wołana bez kontekstu ma wpisać tę samą domenę, co wszystkie dotąd."""
    assert anonymised_email_domain() == ANONYMISED_EMAIL_DOMAIN


def test_without_the_flag_the_domain_is_todays():
    competition = competition_with(branded=False)

    assert anonymised_email_domain(competition) == ANONYMISED_EMAIL_DOMAIN


def test_flag_moves_new_addresses_to_the_competition_domain():
    """``invalid.`` zostaje z przodu: ``.invalid`` z RFC 2606 gwarantuje, że list nie wyjdzie."""
    competition = competition_with(branded=True)

    assert anonymised_email_domain(competition) == "invalid.olimpiadajuniorow.pl"


def test_competition_without_a_domain_falls_back():
    """Konkurs bez własnego adresu nie ma czego podstawić – a adres musi być poprawny."""
    competition = competition_with(branded=True, primary_domain="")

    assert anonymised_email_domain(competition) == ANONYMISED_EMAIL_DOMAIN


@pytest.mark.django_db
def test_anonymisation_uses_the_domain_of_the_competition_in_context(competition):
    """Cała droga: konkurs kontekstu → reguła domeny → wiersz ``User`` po anonimizacji.

    Flagę przestawiamy Konkursowi #1, bo to jego konkurs wiąże kontekst testu. Przedmiotem jest
    to, że ``anonymise_account`` w ogóle o konkurs pyta – sama reguła jest sprawdzona wyżej.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), BRANDING_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    user = ParticipantFactory(competition=competition).user

    anonymise_account(user)

    assert user.email == f"deleted-{user.pk}@invalid.{competition.primary_domain}"


@pytest.mark.django_db
def test_an_account_anonymised_before_the_flag_keeps_its_address(competition):
    """Konto wytarte wczoraj zostaje ze swoim adresem – żadna migracja danych go nie dotknie.

    Test jest zakazem wyrażonym wykonalnie: adres jest kluczem logowania, a nie napisem do
    odświeżenia razem z marką.
    """
    user = ParticipantFactory(competition=competition).user
    anonymise_account(user)
    before = user.email

    competition.feature_flags = {**(competition.feature_flags or {}), BRANDING_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    user.refresh_from_db()

    assert user.email == before
    assert user.email.endswith(f"@{ANONYMISED_EMAIL_DOMAIN}")
