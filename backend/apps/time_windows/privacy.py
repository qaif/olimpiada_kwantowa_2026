"""Okna czasowe a RODO: sekcja eksportu danych konta i anonimizacja (TZ-01 § 6).

Eksport (art. 15 i 20 RODO): sekcja „okna_czasowe” jest w pliku **zawsze** (pusta strefa i pusta
lista przy konkursie bez okien) – kształt eksportu ma być ten sam dla każdego konta. Bez tożsamości
koordynatora, który ustawił wyjątek: to dane pracownika organizatora, nie uczestnika (ta sama
granica, co przy zaświadczeniach o statusie ucznia).

Anonimizacja: strefa ucznia znika (opisuje osobę, a nie zawody), a wyjątek zostaje – okno
i dodatkowy czas są dokumentacją warunków, w jakich powstała praca – tylko bez powodu, bo powód
bywa śladem dostosowania. Usunięcie konta zabiera oba wiersze kaskadą z profilem.
"""

from __future__ import annotations

from .models import ParticipantTimezone, ParticipantWindow

#: Powód po anonimizacji. Kolumna ma więz „niepusty” – wyjątek bez powodu nie istnieje.
ANONYMISED_REASON = "-"


def export_section(participant) -> dict:
    if participant is None:
        return {"strefa_czasowa": None, "wyjatki": []}
    zone = ParticipantTimezone.objects.filter(participant=participant).values_list("timezone", flat=True).first()
    rows = (
        ParticipantWindow.objects.filter(participant=participant)
        .select_related("plan__stage", "window")
        .order_by("plan__stage__opens_at", "id")
    )
    return {
        "strefa_czasowa": zone,
        "wyjatki": [
            {
                "etap": row.plan.stage.display_name,
                "okno": row.window.label if row.window_id else None,
                "dodatkowy_czas_min": row.extra_minutes,
                "powod": row.reason,
                "ustawiono": row.set_at.isoformat(),
            }
            for row in rows
        ],
    }


def erase_for_participants(participants) -> None:
    """Anonimizacja: bez strefy i bez powodów wyjątków; okno i dodatkowy czas zostają."""
    ParticipantTimezone.objects.filter(participant__in=participants).delete()
    ParticipantWindow.objects.filter(participant__in=participants).update(reason=ANONYMISED_REASON)
