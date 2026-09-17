"""Fabryki factory_boy dla wyników i dokumentów. Używane wyłącznie w testach.

Ani publikacja wyników, ani dyplom nie dostają własnego klucza do konkursu: pierwsza dochodzi do
niego przez etap, drugi przez edycję (``docs/UNIWERSALNY-ETAP-1.md`` § 3.4). Argument
``competition`` służy tu więc do **propagacji** – ``ResultsPublicationFactory(competition=inny)``
ma założyć etap tego samego konkursu, o który test pyta, a nie dowolny.

Testy wyników mają swoje bogatsze budowniczki w ``conftest.py`` tego pakietu (``make_stage``,
``graded_entry``) i to one zostają drogą domyślną. Te dwie fabryki istnieją dla testów krzyżowych,
którym potrzebny jest sam **wiersz należący do drugiego konkursu**, bez całej otoczki ocen.
"""

import factory
from django.utils import timezone

from apps.competitions.tests.factories import EditionFactory, StageEntryFactory, StageFactory
from apps.results.models import (
    Anonymization,
    Certificate,
    CertificateKind,
    ResultsPublication,
    generate_verification_code,
)
from apps.tenancy.tests.factories import SAME_COMPETITION, CompetitionScopedFactory


class ResultsPublicationFactory(CompetitionScopedFactory):
    """Ogłoszona tabela wyników etapu. Pusty snapshot – wiersze dokłada test, który ich potrzebuje."""

    class Meta:
        model = ResultsPublication

    stage = factory.SubFactory(StageFactory, competition=SAME_COMPETITION)
    published_at = factory.LazyFunction(timezone.now)
    anonymization = Anonymization.CODE
    snapshot = factory.LazyFunction(list)


class CertificateFactory(CompetitionScopedFactory):
    """Dyplom uczestnika. Numer i kod są unikalne globalnie – dokument weryfikuje się bez konkursu."""

    class Meta:
        model = Certificate

    edition = factory.SubFactory(EditionFactory, competition=SAME_COMPETITION)
    entry = factory.SubFactory(StageEntryFactory, competition=SAME_COMPETITION)
    kind = CertificateKind.UCZESTNIK
    number = factory.Sequence(lambda n: f"OK/2026/{n + 1:04d}")
    code = factory.LazyFunction(generate_verification_code)
    issued_at = factory.LazyFunction(timezone.now)
