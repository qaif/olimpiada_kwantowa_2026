"""Role zalogowanego użytkownika dla nawigacji w szablonach.

Nawigacja nie jest zabezpieczeniem – dostęp rozstrzygają mixiny ról (``apps.web.mixins``) i klasy
uprawnień DRF. Ten procesor służy wyłącznie temu, żeby nie pokazywać linków, które i tak dadzą 403.
Reguły są te same, co w mixinach: jedna definicja roli w całym systemie.
"""

from apps.accounts.models import GROUP_APPEALS, GROUP_COORDINATOR, GROUP_PARTICIPANT
from apps.accounts.services import active_reviewer_profile
from apps.appeals.services import appeals_committee_profile


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
