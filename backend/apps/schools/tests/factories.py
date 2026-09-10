"""Fabryka słownika szkół. Używana wyłącznie w testach."""

import factory

from apps.accounts.models import Voivodeship
from apps.schools.models import School, SchoolKind


class SchoolFactory(factory.django.DjangoModelFactory):
    """Szkoła ze słownika. ``search_text`` liczy ``School.save()`` – fabryka go nie podaje."""

    class Meta:
        model = School

    rspo = factory.Sequence(lambda n: 100000 + n)
    name = factory.Sequence(lambda n: f"I LICEUM OGÓLNOKSZTAŁCĄCE NR {n}")
    kind = SchoolKind.LO
    voivodeship = Voivodeship.MAZOWIECKIE
    city = "Warszawa"
    postal_code = "00-001"
    address = "ul. Testowa 1"
    is_public = True
    is_active = True
