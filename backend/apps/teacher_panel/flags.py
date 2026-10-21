"""Flaga formalności uczniów w panelu nauczyciela – osobno, bez importów, bo czyta ją też ``apps.accounts``.

Strona zgody na opiekuna, list z prośbą o zgodę, opis pola w profilu ucznia i rejestr czynności pytają
o tę samą flagę, co panel nauczyciela (UX-PANEL-02 D5) – informacja dla ucznia i to, co naprawdę widzi
nauczyciel, nie mogą się rozjechać. Moduł nie importuje niczego z aplikacji, więc wołanie go z serwisów
kont nie tworzy cyklu importów.
"""

from __future__ import annotations

#: Flaga konkursu: zaświadczenie, zgoda rodzica i obecność na warsztatach w panelu nauczyciela.
DETAILS_FLAG = "supervisor_class_details"


def details_enabled(competition) -> bool:
    """Czy konkurs pokazuje nauczycielowi formalności i obecność uczniów. Odczyt pola – bez bazy."""
    return competition is not None and competition.has_feature(DETAILS_FLAG)
