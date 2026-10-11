"""Napisy webinarów dla uczestników, komisji i gości mają przekład w każdym z 10 katalogów."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.utils import translation

from apps.core.tests.test_translations import parse_po

CATALOGS = sorted((Path(settings.BASE_DIR) / "locale").glob("*/LC_MESSAGES/django.po"))

#: Napisy z kodu i szablonów webinarów, które widzi ktoś spoza panelu koordynatora.
MSGIDS = (
    "Webinary",
    "Serwer webinarów nie odpowiada. Spróbuj za chwilę.",
    "Wejście otworzy się %(when)s. Wróć wtedy na tę stronę.",
    "Ten webinar już się zakończył.",
    "Prowadzący jeszcze nie rozpoczął webinaru. Spróbuj ponownie za kilka minut.",
    "Ten link nie jest już aktywny.",
    "Zaproszenie na webinar: %(title)s – %(competition)s",
    "Przypomnienie o webinarze: %(title)s – %(competition)s",
    "Zapraszamy na webinar „%(title)s”.",
    "Termin: %(when)s (strefa %(zone)s), czas trwania: %(minutes)s min.",
    "Nie chcesz dostawać listów o webinarach? Wyłącz je tutaj: %(link)s",
    "Rozpocznij jako prowadzący",
    "Wyłącz listy o webinarach",
    "Imię i nazwisko (albo nazwa) widoczne w spotkaniu",
    # pokój na platformie (``room.html``, ``views.webinars.room_strings``)
    "Podnieś rękę",
    "Daj głos",
    "Udostępnij ekran",
    "Mikrofon",
    "Kamera",
    "Czat",
    "Prowadzący dał Ci głos – możesz włączyć mikrofon i kamerę.",
    "Ekran: %(name)s",
)


def test_there_are_ten_catalogs():
    assert len(CATALOGS) == 10


@pytest.mark.parametrize("path", CATALOGS, ids=lambda path: path.parent.parent.name)
def test_webinar_strings_are_translated(path):
    entries = {entry["msgid"]: entry for entry in parse_po(path)}
    for msgid in MSGIDS:
        assert msgid in entries, (path, msgid)
        assert entries[msgid]["msgstr"][0].strip(), (path, msgid)


def test_room_strings_are_translated_at_request_time():
    from apps.web.views.webinars import room_strings

    with translation.override("ar"):
        strings = room_strings()
    assert strings["raiseHand"] == "ارفع يدك"
    assert "%(name)s" in strings["screenOf"]


def test_english_translation_is_active():
    with translation.override("en"):
        assert translation.gettext("Webinary") == "Webinars"
        assert translation.gettext("Ten webinar już się zakończył.") == "This webinar has already ended."
