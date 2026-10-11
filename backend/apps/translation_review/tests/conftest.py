"""Fikstury testów przeglądu tłumaczeń (L10N-01)."""

from __future__ import annotations

import pytest
from django.utils.translation import trans_real

from apps.accounts import super_coordinator as super_coordinator_role
from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.tenancy.tests.factories import grant_membership
from apps.translation_review import catalogs, runtime
from apps.translation_review.models import GrantLevel, TranslatorGrant
from apps.translation_review.validation import TAG, placeholders


@pytest.fixture
def overrides_on(settings):
    """Nakładka włączona (w ``settings/test.py`` jest wyłączona) i stan procesu od zera.

    Po teście warstwa jest wyjmowana z każdego załadowanego obiektu tłumaczenia – bez tego
    następny test w tym samym procesie xdist dostałby cudze poprawki w swoich napisach.
    """
    settings.TRANSLATION_OVERRIDES_ENABLED = True
    runtime.forget()
    yield
    settings.TRANSLATION_OVERRIDES_ENABLED = False
    for translation in list(trans_real._translations.values()):
        runtime._strip(translation)
    runtime.forget()


def simple_row(language: str = "es") -> catalogs.Row:
    """Pierwszy zwykły napis języka: bez placeholderów, znaczników, kontekstu i liczby mnogiej."""
    for row in catalogs.index(language).rows:
        if (
            row.translation
            and row.msgctxt is None
            and row.plural_index is None
            and not placeholders(row.msgid)
            and not TAG.search(row.msgid)
            and "'" not in row.msgid
            and '"' not in row.msgid
            and 5 < len(row.msgid) < 60
        ):
            return row
    raise AssertionError("Brak zwykłego napisu w katalogu.")


def placeholder_row(language: str = "es") -> catalogs.Row:
    for row in catalogs.index(language).rows:
        if row.translation and row.plural_index is None and "%(" in row.msgid and not TAG.search(row.msgid):
            return row
    raise AssertionError("Brak napisu z placeholderem.")


def grant_language(user, language="es", level=GrantLevel.TRANSLATOR, competition=None):
    return TranslatorGrant.objects.create(user=user, language=language, level=level, competition=competition)


@pytest.fixture
def translator(db):  # noqa: ARG001
    user = UserFactory()
    grant_language(user)
    return user


@pytest.fixture
def reviewer(db):  # noqa: ARG001
    user = UserFactory()
    grant_language(user, level=GrantLevel.REVIEWER)
    return user


@pytest.fixture
def super_coordinator(db):  # noqa: ARG001
    user = UserFactory()
    super_coordinator_role.grant(user)
    return user


@pytest.fixture
def multilingual(competition, english_enabled_site):
    return english_enabled_site(competition, languages=("pl", "en", "es"))


@pytest.fixture
def coordinator(multilingual):
    user = CoordinatorFactory()
    grant_membership(user, multilingual, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def member(multilingual):
    """Kierownik delegacji – tu: uczestnik konkursu, czyli konto związane z konkursem."""
    user = UserFactory()
    ParticipantFactory(competition=multilingual, user=user)
    grant_membership(user, multilingual, CompetitionRole.PARTICIPANT)
    return user
