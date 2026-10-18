"""Raport obecności Google Meet: odczyt, czasy, link do arkusza i pobranie (WS-ATT-02 § 1–2).

Bez bazy: parser i pobieranie nie dotykają modeli, a sieć zastępuje atrapa ``_http_get``.
Dopasowanie do uczestników i ekran sprawdza ``apps/web/tests/test_coordinator_meet_import.py``.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from datetime import time as dtime

import pytest
import requests

from apps.cms import meet_report
from apps.cms.meet_report import (
    MAX_REPORT_ROWS,
    MeetReportError,
    find_date,
    normalize_email,
    parse_bytes,
    parse_duration,
    parse_sheet_link,
)

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd"


# --- czasy trwania -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("1 godz. 5 min", 3900),
        ("1 godz. 23 min", 4980),
        ("1 godzina 5 minut", 3900),
        ("2 godziny", 7200),
        ("1 hr 5 min", 3900),
        ("1 hour 5 mins", 3900),
        ("2 hrs", 7200),
        ("1h 5m", 3900),
        ("45 min", 2700),
        ("65 min", 3900),
        ("30 s", 30),
        ("01:05:00", 3900),
        ("1:05", 3900),
        ("65", 3900),
        ("12,5", 750),
        (timedelta(hours=1, minutes=5), 3900),
        (dtime(1, 5), 3900),
        (0.0451388889, 3900),  # ułamek doby z XLSX
        (65, 3900),
    ],
)
def test_czas_trwania_w_kazdej_postaci(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", [None, "", "brak", "—", True])
def test_czas_nieczytelny_to_none(value):
    assert parse_duration(value) is None


# --- nagłówki i układ ----------------------------------------------------------------------------


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def test_naglowki_polskie_i_sumowanie_wierszy_jednej_osoby():
    """Wyjście i powrót tej samej osoby to dwa wiersze – czasy się sumują (WS-ATT-02 § 1.3)."""
    report = parse_bytes(
        csv_bytes(
            "Imię,Nazwisko,Adres e-mail,Czas trwania,Godzina dołączenia,Godzina wyjścia\n"
            "Anna,Nowak,anna@example.org,40 min,10:00,10:40\n"
            "Jan,Kowalski,jan@example.org,1 godz. 5 min,10:00,11:05\n"
            "Anna,Nowak,ANNA@example.org,25 min,10:50,11:15\n"
        ),
        "raport.csv",
    )

    people = {person.email.lower(): person for person in report.people}
    assert report.rows == 3
    assert people["anna@example.org"].seconds == 65 * 60
    assert people["anna@example.org"].rows == 2
    assert people["jan@example.org"].seconds == 65 * 60
    assert report.longest_seconds == 65 * 60


def test_naglowki_angielskie_w_innej_kolejnosci_i_blok_meta_z_data():
    """Nad tabelą stoi kod i data spotkania – nagłówek jest niżej, a data wchodzi do podpowiedzi."""
    report = parse_bytes(
        csv_bytes(
            "Meeting code,abc-defg-hij\n"
            'Created on,"Nov 12, 2026"\n'
            "\n"
            "Email,Duration,Last name,First name,Time joined,Time exited\n"
            "ola@example.org,1 hr 5 min,Lis,Ola,5:00 PM,6:05 PM\n"
        ),
        "",
    )

    (person,) = report.people
    assert (person.first_name, person.last_name, person.email) == ("Ola", "Lis", "ola@example.org")
    assert person.seconds == 3900
    assert report.meeting_date == date(2026, 11, 12)


def test_bez_kolumny_czasu_trwania_liczy_wyjscie_minus_dolaczenie():
    report = parse_bytes(
        csv_bytes(
            "Imię i nazwisko;Czas dołączenia;Czas opuszczenia\n"
            "Jan Kowalski;2026-11-12 17:02:00;2026-11-12 18:32:30\n"
            "Nocny Marek;23:50;00:20\n"
        ),
        "",
    )

    seconds = {person.full_name: person.seconds for person in report.people}
    assert seconds["Jan Kowalski"] == 90 * 60 + 30
    assert seconds["Nocny Marek"] == 30 * 60
    assert report.meeting_date == date(2026, 11, 12)


def test_csv_w_cp1250_ze_srednikiem():
    """Polski Excel zapisuje „CSV” w Windows-1250 i ze średnikiem."""
    report = parse_bytes(
        csv_bytes("Imię;Nazwisko;E-mail;Czas trwania\nŁucja;Żółć;lucja@example.org;45 min\n", "cp1250"),
        "raport.csv",
    )

    (person,) = report.people
    assert (person.first_name, person.last_name) == ("Łucja", "Żółć")
    assert person.seconds == 2700


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le"])
def test_csv_w_utf16_z_tabulatorem(encoding):
    """„Tekst Unicode” z Excela: UTF-16 (z BOM albo bez) i tabulator."""
    report = parse_bytes(
        csv_bytes(
            "First name\tLast name\tEmail\tDuration\nŁucja\tŻółć\tlucja@example.org\t1:05:00\n", encoding
        ),
        "raport.txt",
    )

    (person,) = report.people
    assert person.last_name == "Żółć"
    assert person.seconds == 3900


def test_csv_z_bom_utf8():
    report = parse_bytes("﻿Imię,Nazwisko,Czas trwania\nAnna,Nowak,30 min\n".encode(), "")

    assert report.people[0].first_name == "Anna"


def test_xlsx_z_czasem_jako_timedelta_i_karta_podgrup():
    """Pierwsza karta z rozpoznanym nagłówkiem wygrywa; czas trwania w komórce czasu."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Spotkanie"
    sheet.append(["Kod spotkania", "abc-defg-hij"])
    sheet.append(["Data", date(2026, 11, 12)])
    sheet.append([])
    sheet.append(["Imię", "Nazwisko", "Adres e-mail", "Czas trwania"])
    sheet.append(["Anna", "Nowak", "anna@example.org", timedelta(minutes=50)])
    sheet.append(["Jan", "Kowalski", "jan@example.org", "1 godz. 5 min"])
    breakout = workbook.create_sheet("Pokoje podgrup")
    breakout.append(["Pokój podgrupy", "Pokój otwarty"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    report = parse_bytes(buffer.getvalue(), "raport.xlsx")

    seconds = {person.email: person.seconds for person in report.people}
    assert seconds == {"anna@example.org": 3000, "jan@example.org": 3900}
    assert report.meeting_date == date(2026, 11, 12)


def test_brak_naglowka_to_czytelna_odmowa():
    with pytest.raises(MeetReportError, match="Nie rozpoznano raportu"):
        parse_bytes(b"kod,warsztat\nKW-123,2026-11-12-kubity\n", "obecnosc.csv")


def test_limit_wierszy():
    lines = ["Email,Duration"] + [f"osoba{i}@example.org,30 min" for i in range(MAX_REPORT_ROWS + 1)]

    with pytest.raises(MeetReportError, match="więcej niż"):
        parse_bytes("\n".join(lines).encode(), "")


def test_wiersz_podsumowania_bez_osoby_jest_pomijany():
    report = parse_bytes(b"Email,Duration\nanna@example.org,30 min\n,\nRazem,\n", "")

    assert report.rows == 1


def test_za_duzy_plik():
    with pytest.raises(MeetReportError, match="za duży"):
        parse_bytes(b"x" * (meet_report.MAX_REPORT_BYTES + 1), "a.csv")


def test_data_z_nazwy_pliku_gdy_raport_jej_nie_ma():
    report = parse_bytes(b"Email,Duration\nanna@example.org,30 min\n", "Kubity - raport 12.11.2026.csv")

    assert report.meeting_date == date(2026, 11, 12)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-11-12", date(2026, 11, 12)),
        ("12.11.2026", date(2026, 11, 12)),
        ("12 listopada 2026", date(2026, 11, 12)),
        ("12 lis 2026", date(2026, 11, 12)),
        ("Nov 12, 2026", date(2026, 11, 12)),
        ("12 Nov 2026", date(2026, 11, 12)),
        ("abc-defg-hij", None),
        ("31.02.2026", None),
    ],
)
def test_daty_spotkania(text, expected):
    assert find_date(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Jan.Kowalski+olimpiada@Gmail.com", "jankowalski@gmail.com"),
        ("jan.kowalski@googlemail.com", "jankowalski@gmail.com"),
        ("Jan.Kowalski+x@szkola.edu.pl", "jan.kowalski+x@szkola.edu.pl"),
        ("  ANNA@Example.ORG ", "anna@example.org"),
    ],
)
def test_normalizacja_adresu(value, expected):
    assert normalize_email(value) == expected


# --- link do arkusza -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "gid"),
    [
        (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=123456", "123456"),
        (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid=42#gid=42", "42"),
        (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing", "0"),
        (f"https://docs.google.com/spreadsheets/u/1/d/{SHEET_ID}/edit", "0"),
        (f"  <https://DOCS.google.com/spreadsheets/d/{SHEET_ID}>  ", "0"),
    ],
)
def test_link_id_i_gid(url, gid):
    link = parse_sheet_link(url)

    assert link.sheet_id == SHEET_ID
    assert link.gid == gid
    assert link.export_url == f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"


@pytest.mark.parametrize(
    "url",
    [
        f"http://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
        f"https://drive.google.com/file/d/{SHEET_ID}/view",
        f"https://docs.google.com.evil.example/spreadsheets/d/{SHEET_ID}/edit",
        f"https://evil.example/?u=https://docs.google.com/spreadsheets/d/{SHEET_ID}",
        f"https://user:pass@docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
        f"https://docs.google.com:8443/spreadsheets/d/{SHEET_ID}/edit",
        f"https://docs.google.com/document/d/{SHEET_ID}/edit",
        "https://docs.google.com/spreadsheets/d/e/2PACX-1vAbCdEfGhIjKlMnOpQrStUvWxYz/pubhtml",
        "https://docs.google.com/spreadsheets/d/krotkie/edit",
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=12a",
        f"https://sheets.googleusercontent.com/spreadsheets/d/{SHEET_ID}/export",
        "http://169.254.169.254/latest/meta-data/",
        "",
        "nie link",
    ],
)
def test_link_spoza_docs_google_com_jest_odrzucony(url):
    with pytest.raises(MeetReportError):
        parse_sheet_link(url)


# --- pobieranie (atrapa HTTP) --------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, chunks=None, error=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [body]
        self._error = error
        self.closed = False

    def iter_content(self, chunk_size=65536):  # noqa: ARG002 - podpis jak w requests
        yield from self._chunks
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


@pytest.fixture
def http(monkeypatch):
    """Kolejka odpowiedzi atrapy i lista adresów, o które serwer pytał."""
    calls: list[str] = []
    queue: list = []

    def fake_get(url):
        calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(meet_report, "_http_get", fake_get)
    return calls, queue


def link():
    return parse_sheet_link(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=7")


CSV = "Imię,Nazwisko,Adres e-mail,Czas trwania\nAnna,Nowak,anna@example.org,45 min\n".encode()


def test_pobranie_csv_przez_przekierowanie_na_googleusercontent(http):
    calls, queue = http
    export = "https://doc-0s-1c-sheets.googleusercontent.com/export/abc?format=csv&gid=7"
    queue.extend(
        [
            FakeResponse(307, headers={"Location": export}),
            FakeResponse(200, CSV, headers={"Content-Type": "text/csv; charset=utf-8"}),
        ]
    )

    report = meet_report.read_link(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=7")

    assert calls == [link().export_url, export]
    assert report.people[0].email == "anna@example.org"


def test_strona_logowania_przez_przekierowanie_mowi_jak_udostepnic(http):
    _, queue = http
    queue.append(
        FakeResponse(302, headers={"Location": "https://accounts.google.com/ServiceLogin?continue=x"})
    )

    with pytest.raises(MeetReportError, match="Każdy, kto ma link"):
        meet_report.fetch_sheet_csv(link())


def test_strona_logowania_jako_html_z_200(http):
    _, queue = http
    queue.append(
        FakeResponse(200, b"<!DOCTYPE html><html>Zaloguj</html>", headers={"Content-Type": "text/html"})
    )

    with pytest.raises(MeetReportError, match="pobierz raport jako CSV"):
        meet_report.fetch_sheet_csv(link())


def test_html_bez_naglowka_typu_tez_jest_strona_logowania(http):
    _, queue = http
    queue.append(FakeResponse(200, b"  <html><body>Sign in</body></html>"))

    with pytest.raises(MeetReportError, match="nie jest udostępniony"):
        meet_report.fetch_sheet_csv(link())


@pytest.mark.parametrize("status", [401, 403])
def test_odmowa_dostepu(http, status):
    _, queue = http
    queue.append(FakeResponse(status))

    with pytest.raises(MeetReportError, match="nie jest udostępniony"):
        meet_report.fetch_sheet_csv(link())


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/export.csv",
        "http://doc-0s-sheets.googleusercontent.com/export",
        "https://googleusercontent.com.evil.example/x",
        "https://doc.googleusercontent.com:444/x",
        "https://docs.google.com/document/d/x/export",
    ],
)
def test_przekierowanie_poza_google_jest_odrzucone(http, location):
    calls, queue = http
    queue.append(FakeResponse(302, headers={"Location": location}))

    with pytest.raises(MeetReportError, match="nieoczekiwany adres"):
        meet_report.fetch_sheet_csv(link())
    assert len(calls) == 1


def test_petla_przekierowan(http):
    _, queue = http
    target = "https://doc.googleusercontent.com/again"
    queue.extend(
        [FakeResponse(302, headers={"Location": target}) for _ in range(meet_report.MAX_REDIRECTS + 1)]
    )

    with pytest.raises(MeetReportError, match="Za dużo przekierowań"):
        meet_report.fetch_sheet_csv(link())


def test_za_duzy_arkusz_po_naglowku(http):
    _, queue = http
    queue.append(FakeResponse(200, b"", headers={"Content-Length": str(meet_report.MAX_REPORT_BYTES + 1)}))

    with pytest.raises(MeetReportError, match="za duży"):
        meet_report.fetch_sheet_csv(link())


def test_za_duzy_arkusz_w_strumieniu(http):
    _, queue = http
    chunk = b"a" * 65536
    queue.append(FakeResponse(200, chunks=[chunk] * (meet_report.MAX_REPORT_BYTES // len(chunk) + 2)))

    with pytest.raises(MeetReportError, match="za duży"):
        meet_report.fetch_sheet_csv(link())


def test_timeout_polaczenia(http):
    _, queue = http
    queue.append(requests.ConnectTimeout("timeout"))

    with pytest.raises(MeetReportError, match="nie odpowiedział na czas"):
        meet_report.fetch_sheet_csv(link())


def test_timeout_w_trakcie_odczytu(http):
    _, queue = http
    queue.append(FakeResponse(200, chunks=[b"Email,"], error=requests.ReadTimeout("slow")))

    with pytest.raises(MeetReportError, match="nie odpowiedział na czas"):
        meet_report.fetch_sheet_csv(link())


def test_calkowity_limit_czasu(http, monkeypatch):
    """Serwer sączący bajty mieści się w limicie na kawałek, ale nie w limicie całości."""
    _, queue = http
    queue.append(FakeResponse(200, chunks=[b"a", b"b", b"c"]))
    clock = iter([0.0, 100.0, 200.0, 300.0])
    monkeypatch.setattr(meet_report.time, "monotonic", lambda: next(clock))

    with pytest.raises(MeetReportError, match="zbyt długo"):
        meet_report.fetch_sheet_csv(link())


def test_brak_arkusza(http):
    _, queue = http
    queue.append(FakeResponse(404))

    with pytest.raises(MeetReportError, match="Nie ma takiego arkusza"):
        meet_report.fetch_sheet_csv(link())
