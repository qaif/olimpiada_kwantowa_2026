"""Role zalogowanego użytkownika oraz dane „ramy” serwisu dla szablonu bazowego.

Nawigacja nie jest zabezpieczeniem – dostęp rozstrzygają mixiny ról (``apps.web.mixins``) i klasy
uprawnień DRF. Ten procesor służy wyłącznie temu, żeby nie pokazywać linków, które i tak dadzą 403.
Reguły są te same, co w mixinach: jedna definicja roli w całym systemie.

``site_chrome`` dokłada wyłącznie dane prezentacyjne nagłówka i stopki (etykieta bieżącej edycji,
numer wersji). Nie ma tam żadnej reguły domenowej, a błąd bazy nie może wywrócić szablonu
bazowego – dlatego zapytanie jest opakowane w ``try``.
"""

import logging

from django.db import DatabaseError

from apps.accounts.models import GROUP_APPEALS, GROUP_COORDINATOR, GROUP_PARTICIPANT
from apps.accounts.services import active_reviewer_profile
from apps.appeals.services import appeals_committee_profile

logger = logging.getLogger(__name__)

#: Wersja interfejsu pokazywana w stopce. Zmieniana ręcznie razem z wydaniem – nie jest to numer
#: schematu API (ten mieszka w ``SPECTACULAR_SETTINGS``) ani numer migracji.
APP_VERSION = "1.0"


def roles(request) -> dict:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return {
            "is_participant": False,
            "is_reviewer": False,
            "is_coordinator": False,
            "is_appeals_committee": False,
        }
    names = set(user.groups.values_list("name", flat=True))
    return {
        "is_participant": GROUP_PARTICIPANT in names and hasattr(user, "participant"),
        "is_reviewer": active_reviewer_profile(user) is not None,
        "is_coordinator": GROUP_COORDINATOR in names,
        "is_appeals_committee": GROUP_APPEALS in names and appeals_committee_profile(user) is not None,
    }


def site_chrome(request) -> dict:
    """Etykieta bieżącej edycji (podtytuł logotypu) i wersja aplikacji (stopka)."""
    from apps.competitions.services import current_edition

    label = ""
    try:
        edition = current_edition()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
        logger.warning("Nie udało się odczytać bieżącej edycji dla nagłówka.")
    else:
        label = edition.year_label if edition is not None else ""
    return {"site_edition_label": label, "app_version": APP_VERSION}
