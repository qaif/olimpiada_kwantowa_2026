"""Przesuwanie osi czasu etapu w scenariuszu E2E – przez panel administracyjny Django.

Scenariusz musi w kilka minut przejść przez okno reklamacji rozpisane w danych demonstracyjnych
na tygodnie. T-10 dopuszcza dwie drogi: panel admina albo ``manage.py e2e_timeline`` przy
``E2E_MODE=1``. Kontener ``e2e`` widzi aplikację wyłącznie po HTTP (nie ma w nim ani kodu Django,
ani gniazda Dockera), więc tutaj używamy panelu – tej samej ścieżki, którą w razie awarii
zadziałałby koordynator. Rozkład offsetów jest **ten sam**, co w komendzie
``apps/competitions/management/commands/e2e_timeline.py``; komenda zostaje dla uruchomień
z hosta i z CI (``docker compose exec web python manage.py e2e_timeline …``).

Offsety są dobowe, więc pomyłka o strefę czasową (panel renderuje pola w czasie lokalnym
serwera, Europe/Warsaw) nie może przestawić fazy.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import expect

LOGGER = logging.getLogger("e2e")

PHASE_CLOSED = "closed"
PHASE_APPEALS_OPEN = "appeals_open"
PHASE_APPEALS_CLOSED = "appeals_closed"

#: Offsety w dniach względem „teraz”. Muszą zachować porządek wymuszony przez ``CheckConstraint``
#: na modelu ``Stage``: opens < deadline <= review <= appeal_opens < appeal_closes.
PHASE_OFFSETS: dict[str, dict[str, int]] = {
    PHASE_CLOSED: {
        "opens_at": -30,
        "deadline_at": -2,
        "review_deadline_at": -1,
        "appeal_window_opens_at": 7,
        "appeal_window_closes_at": 14,
    },
    PHASE_APPEALS_OPEN: {
        "opens_at": -30,
        "deadline_at": -4,
        "review_deadline_at": -3,
        "appeal_window_opens_at": -1,
        "appeal_window_closes_at": 7,
    },
    PHASE_APPEALS_CLOSED: {
        "opens_at": -30,
        "deadline_at": -5,
        "review_deadline_at": -4,
        "appeal_window_opens_at": -3,
        "appeal_window_closes_at": -1,
    },
}

#: Kształty dat, jakie renderuje admin zależnie od ``LANGUAGE_CODE``. Format wykrywamy z wartości
#: już wpisanej w polu, zamiast zakładać jeden – zmiana języka panelu nie ma wywracać scenariusza.
DATE_PATTERNS = (
    (re.compile(r"^\d{2}\.\d{2}\.\d{4}$"), "%d.%m.%Y"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y"),
)


def _date_format(sample: str) -> str:
    for pattern, fmt in DATE_PATTERNS:
        if pattern.match(sample.strip()):
            return fmt
    raise AssertionError(
        f"Nieznany format daty w panelu admina: {sample!r}. Dopisz wzorzec do DATE_PATTERNS."
    )


def set_phase(session, stage_id: int, phase: str, *, server_tz: str = "Europe/Warsaw") -> None:
    """Ustawia oś czasu etapu na zadaną fazę przez formularz ``/admin/competitions/stage/<id>/``.

    ``session`` to ``RoleSession`` koordynatora (konto z ``seed_demo`` jest superużytkownikiem).
    """
    offsets = PHASE_OFFSETS[phase]
    session.goto(f"/admin/competitions/stage/{stage_id}/change/")
    page = session.page
    expect(page.locator("#id_deadline_at_0")).to_be_visible()

    fmt = _date_format(page.locator("#id_opens_at_0").input_value())
    now = datetime.now(ZoneInfo(server_tz))
    for field, offset in offsets.items():
        moment = now + timedelta(days=offset)
        page.fill(f"#id_{field}_0", moment.strftime(fmt))
        page.fill(f"#id_{field}_1", moment.strftime("%H:%M:%S"))
    # Tolerancja po deadline doliczyłaby się do terminu i cofnęła fazę ``closed`` do otwartej.
    page.fill("#id_grace_seconds", "0")

    session.step(f"admin-etap-{phase}-przed-zapisem")
    page.click('input[name="_save"]')
    # Błąd walidacji zostaje na formularzu (``.errornote``) i nie zmienia adresu – bez tej
    # asercji scenariusz szedłby dalej z nieprzesuniętą osią czasu i wywracał się kilka kroków
    # później, w miejscu, które z przyczyną nie ma nic wspólnego.
    expect(page.locator(".errornote")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/admin/competitions/stage/$"))
    LOGGER.info("Etap %s przestawiony na fazę %s", stage_id, phase)
