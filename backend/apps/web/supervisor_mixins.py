"""Mixin roli opiekuna szkolnego dla widoków HTML.

Osobny moduł od ``apps.web.mixins`` wyłącznie z powodu organizacji pracy nad tą wersją serwisu
(równoległe zmiany w tamtym pliku). Zachowanie jest identyczne z pozostałymi mixinami ról –
dziedziczy po ``RoleRequiredMixin``, więc kontrakt dostępu jest ten sam:

- niezalogowany → 302 na ``/login/?next=…``,
- zalogowany w złej roli → 403.

Definicja roli nie jest tu powtórzona: rozstrzyga ``apps.accounts.supervisors.supervisor_profile``,
ta sama funkcja, której używa nawigacja i przekierowanie po zalogowaniu.
"""

from __future__ import annotations

from apps.accounts.supervisors import supervisor_profile
from apps.web.mixins import RoleRequiredMixin


class SupervisorRequiredMixin(RoleRequiredMixin):
    """Opiekun szkolny: grupa ``supervisor`` i profil ``SchoolSupervisor``."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla opiekunów szkolnych."

    def has_role(self, user) -> bool:
        return supervisor_profile(user, self.competition) is not None

    @property
    def supervisor(self):
        """Profil opiekuna **w konkursie z żądania**.

        Konkurs podajemy wprost, choć ``supervisor_profile`` umie go wziąć z kontekstu: nauczyciel
        bywa opiekunem w dwóch olimpiadach, a panel pod domeną jednej z nich ma pokazywać jej
        uczniów. Zgodność z ``has_role`` wyżej jest tu warunkiem poprawności – gdyby bramka
        pytała o inny konkurs niż ekran, dostęp i dane rozjechałyby się przy pierwszym takim koncie.
        """
        return supervisor_profile(self.request.user, self.competition)
