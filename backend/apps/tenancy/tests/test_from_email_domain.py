"""``Competition.from_email`` tylko z domeny platformy albo domeny własnej konkursu (audyt 10.10.2026).

Wspólny przekaźnik poczty wyśle list z dowolnym adresem w ``From`` – bez tej reguły koordynator
mógłby podpisywać listy aktywacyjne i resety hasła adresem innego konkursu albo obcej instytucji.
Reguła dotyczy **zmiany** pola: wiersz zastany zapisuje się dalej przy innej poprawce.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def errors_for(competition) -> dict:
    try:
        competition.full_clean()
    except ValidationError as exc:
        return exc.message_dict
    return {}


def test_a_sender_in_a_foreign_domain_is_refused(competition):
    competition.from_email = "rektor@uczelnia.example.org"

    assert "from_email" in errors_for(competition)


def test_a_sender_in_the_platform_or_own_domain_is_accepted(competition, settings):
    settings.DEFAULT_FROM_EMAIL = "olimpiada@platforma.example"
    competition.from_email = "noreply@platforma.example"
    assert "from_email" not in errors_for(competition)

    competition.from_email = f"listy@{competition.primary_domain}"
    assert "from_email" not in errors_for(competition)


def test_a_subdomain_of_the_platform_is_another_competition(competition, settings):
    settings.SITE_DOMAIN = "platforma.example"
    settings.DEFAULT_FROM_EMAIL = "olimpiada@platforma.example"
    competition.from_email = "noreply@fizyczna.platforma.example"

    assert "from_email" in errors_for(competition)


def test_an_unchanged_legacy_sender_does_not_block_other_edits(competition):
    from apps.tenancy.models import Competition

    Competition.objects.filter(pk=competition.pk).update(from_email="stary@obca-domena.example")
    competition.refresh_from_db()
    competition.name = "Nowa nazwa"

    assert "from_email" not in errors_for(competition)
