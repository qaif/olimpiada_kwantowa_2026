"""Fabryka komunikatów organizatora. Używana wyłącznie w testach.

Baner wisi na **każdej** stronie serwisu, więc komunikat globalny w bazie wielokonkursowej
znaczyłby, że organizator konkursu A ogłasza przerwę techniczną na stronie konkursu B. Stąd własny
klucz obcy (``docs/UNIWERSALNY-ETAP-1.md`` § 3.2), dokładany w zadaniu T4 – fabryka ustawia go
dopiero wtedy, gdy model już go ma (mechanika: ``apps/tenancy/tests/factories.py``).

Stron CMS fabryką nie budujemy i budować nie będziemy: drzewo stron Konkursu #1 stawia migracja
``cms.0002``, a testy mają pracować dokładnie na tym, co dostanie produkcja. Strony drugiego
konkursu zakłada ``apps/tenancy/tests/golden.py`` przez API Wagtaila (``add_child``), czyli tą samą
drogą, którą zakłada je redaktor.
"""

import factory
from django.utils import timezone

from apps.cms.models import Announcement, AnnouncementLevel
from apps.tenancy.tests.factories import CompetitionScopedFactory


class AnnouncementFactory(CompetitionScopedFactory):
    """Włączony komunikat bez końca okna – czyli taki, który widać od razu."""

    class Meta:
        model = Announcement

    text = factory.Sequence(lambda n: f"Komunikat testowy {n}")
    level = AnnouncementLevel.INFO
    starts_at = factory.LazyFunction(timezone.now)
    ends_at = None
    is_active = True
    dismissible = True
