"""Fabryka zgłoszeń do organizatora. Używana wyłącznie w testach.

Zgłoszenie idzie do organizatora **tego** konkursu, więc ma (a do czasu odpowiedniej migracji:
będzie miało) własny klucz obcy – ``null=True``, bo istnieją też sprawy kierowane do operatora
platformy (``docs/UNIWERSALNY-ETAP-1.md`` § 3.2). Fabryka wpisuje konkurs dopiero wtedy, gdy model
ma już to pole; mechanika i powód: ``apps/tenancy/tests/factories.py``.
"""

import factory
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.support.models import SupportCategory, SupportTicket, TicketStatus
from apps.tenancy.tests.factories import CompetitionScopedFactory


class SupportTicketFactory(CompetitionScopedFactory):
    """Sprawa zgłoszona z konta. Wariant bez konta podaje się jawnie: ``user=None, email=…``."""

    class Meta:
        model = SupportTicket

    user = factory.SubFactory(UserFactory)
    # Adres zwrotny zostaje pusty, bo zgłoszenie jest z konta – adresem jest wtedy adres konta.
    # Constraint bazy wymaga jednego albo drugiego, więc test wariantu anonimowego musi podać oba.
    email = ""
    category = SupportCategory.OTHER
    subject = factory.Sequence(lambda n: f"Sprawa testowa {n}")
    status = TicketStatus.OPEN
    created_at = factory.LazyFunction(timezone.now)
