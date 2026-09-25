"""Klient clamd na poziomie protokołu: ramki ``zINSTREAM``, ``zPING`` i prawdziwy skaner na żądanie.

Każdy test suity rozmawia z **podstawionym** clamd (``backend/conftest.py``, ``_fake_clamd``), który
parsuje ramki ściśle – więc testy niżej sprawdzają, że ``scan_stream`` pakuje dane tak, jak wymaga
protokół (4-bajtowa długość, porcje, zero na końcu), także wtedy, gdy plik jest dłuższy niż jedna
porcja. Test z markerem ``clamav`` idzie do prawdziwego ``CLAMAV_HOST:CLAMAV_PORT`` i pomija się,
gdy skanera nie ma (CI, maszyna bez compose).
"""

from __future__ import annotations

import io

import pytest

from apps.submissions.antivirus import VERDICT_CLEAN, VERDICT_INFECTED, ping, scan_stream

#: Standardowy plik testowy EICAR – rozpoznaje go każdy skaner, nie jest szkodliwy.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def test_clean_stream_split_into_many_chunks_is_reported_clean(_fake_clamd):
    payload = b"%PDF-1.4 " + b"x" * 10_000

    assert scan_stream(io.BytesIO(payload), chunk_size=1024) == (VERDICT_CLEAN, "")

    (connection,) = _fake_clamd.connections
    assert bytes(connection.sent).startswith(b"zINSTREAM\0")
    assert bytes(connection.sent).endswith(b"\0\0\0\0")


def test_eicar_is_reported_infected_with_its_signature():
    verdict, signature = scan_stream(io.BytesIO(EICAR))

    assert verdict == VERDICT_INFECTED
    assert signature


def test_ping_answers_pong():
    assert ping() is True


@pytest.mark.clamav
def test_real_clamd_recognises_eicar():
    """Prawdziwy skaner (compose: usługa ``clamav``). Bez niego – pominięty, a nie czerwony."""
    if not ping(timeout=2):
        pytest.skip("clamd niedostępny pod CLAMAV_HOST:CLAMAV_PORT")

    assert scan_stream(io.BytesIO(EICAR))[0] == VERDICT_INFECTED
    assert scan_stream(io.BytesIO(b"zwykly tekst"))[0] == VERDICT_CLEAN
