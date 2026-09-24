"""Kto może oglądać materiały z warsztatów – jedna reguła dla widoków, zapowiedzi i testów.

Organizator: „tylko na stronie po zalogowaniu”. Samo „zalogowany” byłoby jednak za szerokie
w instalacji wielokonkursowej – konto jest wspólne dla platformy (§ 3.4), więc uczestnik olimpiady
B zalogowany pod domeną olimpiady A byłby „zalogowany”, a nie ma z jej warsztatami nic wspólnego.
Reguła brzmi więc: **konto, które w tym konkursie kimś jest**:

- uczestnik – profil uczestnika **w tym konkursie** (``participant_for``). Sama grupa Django
  ``participant`` nie wystarcza: przy wyłączonym ``memberships_enforced`` grupa jest globalna
  i wpuszczałaby uczestników wszystkich konkursów instalacji,
- koordynator, recenzent (członek komitetu), komisja odwoławcza i opiekun szkolny – rola w tym
  konkursie według ``has_role`` (przez ``roles_for``, czyli jednym zapytaniem). To jest ta sama
  definicja roli, na której stoją wszystkie panele: przy wyłączonym ``memberships_enforced``
  grupa jest globalna i ta reguła zachowuje się dokładnie tak, jak każdy inny ekran serwisu.

Superużytkownik nie jest tu eskalowany (ta sama zasada, co w ``has_role``): konto operatora
platformy nie jest kontem członka komitetu olimpiady.
"""

from __future__ import annotations

from apps.accounts.models import CompetitionRole
from apps.accounts.services import participant_for, roles_for

from .models import FEATURE_FLAG

#: Role, które wpuszczają bez profilu uczestnika. Wartości (napisy), a nie człony wyliczenia:
#: ``roles_for`` oddaje zbiór napisów, a przecięcie zbiorów porównuje skróty elementów.
STAFF_ROLES = frozenset(
    role.value
    for role in (
        CompetitionRole.COORDINATOR,
        CompetitionRole.REVIEWER,
        CompetitionRole.APPEALS,
        CompetitionRole.SUPERVISOR,
    )
)


def feature_enabled(competition) -> bool:
    """Czy konkurs włączył materiały z warsztatów. Brak konkursu = nie."""
    return competition is not None and competition.has_feature(FEATURE_FLAG)


def can_view(user, competition) -> bool:
    """Czy to konto może oglądać materiały **tego** konkursu – patrz docstring modułu."""
    if competition is None or user is None:
        return False
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if roles_for(user, competition) & STAFF_ROLES:
        return True
    return participant_for(user, competition) is not None
