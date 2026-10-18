"""Import obecności z raportu Google Meet – dopasowanie, podgląd, zapis, uprawnienia (WS-ATT-02).

Parser raportu i pobieranie z linku sprawdza ``apps/cms/tests/test_meet_report.py`` bez bazy; tu
jest wszystko, co zależy od uczestników konkursu: krąg kandydatów, reguły dopasowania, token
podglądu, zapis „tylko dokładający” i audyt bez danych osobowych.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import SchoolSupervisor
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.cms import meet_report
from apps.cms.models import ContentPage, HomePage, WorkshopAttendance
from apps.cms.workshops import WORKSHOPS_SLUG, workshop_rows
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import current_or_default_competition

pytestmark = pytest.mark.django_db

URL = "web:coordinator-workshop-attendance-meet"
FIRST_DATE = date(2026, 11, 12)
SECOND_DATE = date(2026, 12, 3)
SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd"
HEADER = "Imię,Nazwisko,Adres e-mail,Czas trwania\n"


@pytest.fixture
def logged_coordinator(web_client, coordinator):
    web_client.force_login(coordinator)
    return coordinator


@pytest.fixture
def workshops_page() -> ContentPage:
    """Strona „Warsztaty” z dwoma terminami – dwie kolumny tabeli obecności."""
    home = HomePage.objects.get()
    rows = [
        {"topic": topic, "date": "", "date_value": when, "time": "17:00–18:30", "lecturer": ""}
        for topic, when in (("Kubity i bramki", FIRST_DATE), ("Splątanie", SECOND_DATE))
    ]
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "Godziny",
                    "lecturer_label": "",
                    "rows": rows,
                },
            )
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    return ContentPage.objects.get(pk=page.pk)


@pytest.fixture
def key(workshops_page) -> str:
    return workshop_rows(workshops_page)[0]["key"]


def student(stage, first: str, last: str, email: str | None = None):
    """Uczestnik tego konkursu z wpisem do etapu bieżącej edycji – kandydat do dopasowania."""
    user = UserFactory(first_name=first, last_name=last, **({"email": email} if email else {}))
    participant = ParticipantFactory(user=user)
    StageEntryFactory(stage=stage, participant=participant)
    return participant


def report_file(body: str, name: str = "raport.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, (HEADER + body).encode(), content_type="text/csv")


def preview(client, body: str, *, workshop: str = "", threshold: int = 30, name: str = "raport.csv"):
    return client.post(
        reverse(URL),
        {"workshop": workshop, "threshold": threshold, "file": report_file(body, name), "link": ""},
    )


def buckets(response) -> dict[str, list]:
    data = response.context["preview"]
    return {
        name: getattr(data, name)
        for name in ("matched", "below", "ambiguous", "unknown", "teachers", "already")
    }


# --- dopasowanie ---------------------------------------------------------------------------------


def test_dopasowanie_po_emailu_z_normalizacja_gmaila(web_client, logged_coordinator, elim_stage, key):
    """Zapis „jan.kowalski+olimpiada@gmail.com”, w Meet konto Google „JanKowalski@gmail.com”."""
    participant = student(elim_stage, "Jan", "Kowalski", "jan.kowalski+olimpiada@gmail.com")

    response = preview(web_client, "Janek,K.,JanKowalski@gmail.com,45 min\n", workshop=key)

    assert response.status_code == 200
    (row,) = buckets(response)["matched"]
    assert row.participant == participant
    assert row.via == "email"


def test_dopasowanie_po_imieniu_i_nazwisku_takze_w_odwrotnej_kolejnosci(
    web_client, logged_coordinator, elim_stage, key
):
    participant = student(elim_stage, "Łucja", "Żółć")

    response = web_client.post(
        reverse(URL),
        {
            "workshop": key,
            "threshold": 30,
            "file": SimpleUploadedFile(
                "r.csv", "Uczestnik,Czas trwania\nżółć  ŁUCJA,1 godz. 5 min\n".encode()
            ),
        },
    )

    (row,) = buckets(response)["matched"]
    assert row.participant == participant
    assert row.via == "name"


def test_dwoch_kandydatow_to_niejednoznacznosc(web_client, logged_coordinator, elim_stage, key):
    first = student(elim_stage, "Jan", "Kowalski")
    second = student(elim_stage, "Jan", "Kowalski")

    response = preview(web_client, "Jan,Kowalski,prywatny@example.org,50 min\n", workshop=key)

    result = buckets(response)
    assert result["matched"] == []
    (row,) = result["ambiguous"]
    assert {candidate.pk for candidate in row.candidates} == {first.pk, second.pk}


def test_dopasowany_po_emailu_nie_jest_kandydatem_po_nazwisku(
    web_client, logged_coordinator, elim_stage, key
):
    """Dwóch Janów Kowalskich: jeden przyszedł ze znanym adresem, drugi ma już jednego kandydata."""
    first = student(elim_stage, "Jan", "Kowalski", "jan1@example.org")
    second = student(elim_stage, "Jan", "Kowalski", "jan2@example.org")

    response = preview(
        web_client,
        "Jan,Kowalski,jan1@example.org,50 min\nJan,Kowalski,inny@example.org,40 min\n",
        workshop=key,
    )

    matched = {row.participant.pk: row.via for row in buckets(response)["matched"]}
    assert matched == {first.pk: "email", second.pk: "name"}


def test_uczestnik_innego_konkursu_nie_jest_kandydatem(
    web_client, logged_coordinator, elim_stage, key, other_competition
):
    stranger = ParticipantFactory(
        competition=other_competition,
        user=UserFactory(first_name="Obca", last_name="Osoba", email="obca@example.org"),
    )

    response = preview(web_client, "Obca,Osoba,obca@example.org,60 min\n", workshop=key)

    result = buckets(response)
    assert [row.label for row in result["unknown"]] == ["Obca Osoba"]
    assert all(row.participant != stranger for row in result["matched"])


def test_bez_wpisu_do_edycji_nie_jest_kandydatem(web_client, logged_coordinator, elim_stage, key):
    """Krąg jest ten sam, co wiersze tabeli obecności – konto bez wpisu nie ma tam wiersza."""
    ParticipantFactory(user=UserFactory(first_name="Bez", last_name="Wpisu", email="bez@example.org"))

    response = preview(web_client, "Bez,Wpisu,bez@example.org,60 min\n", workshop=key)

    assert len(buckets(response)["unknown"]) == 1


def test_nauczyciel_jest_pominiety(web_client, logged_coordinator, elim_stage, key):
    SchoolSupervisor.objects.create(
        user=UserFactory(email="nauczycielka@szkola.edu.pl", first_name="Anna", last_name="Nauczycielska"),
        school="XIV LO",
        competition=current_or_default_competition(),
    )

    response = preview(web_client, "Anna,Nauczycielska,Nauczycielka@szkola.edu.pl,90 min\n", workshop=key)

    result = buckets(response)
    assert [row.label for row in result["teachers"]] == ["Anna Nauczycielska"]
    assert result["unknown"] == []


def test_prog_i_sumowanie_wierszy(web_client, logged_coordinator, elim_stage, key):
    """20 + 15 minut tej samej osoby to 35 minut – powyżej progu 30; 20 minut innej – poniżej."""
    staying = student(elim_stage, "Ola", "Lis", "ola@example.org")
    leaving = student(elim_stage, "Ewa", "Kot", "ewa@example.org")

    response = preview(
        web_client,
        "Ola,Lis,ola@example.org,20 min\nEwa,Kot,ewa@example.org,20 min\nOla,Lis,ola@example.org,15 min\n",
        workshop=key,
    )

    result = buckets(response)
    assert [row.participant for row in result["matched"]] == [staying]
    assert [row.participant for row in result["below"]] == [leaving]

    lowered = preview(web_client, "Ewa,Kot,ewa@example.org,20 min\n", workshop=key, threshold=15)
    assert [row.participant for row in buckets(lowered)["matched"]] == [leaving]


def test_juz_oznaczeni_osobno(web_client, logged_coordinator, elim_stage, key):
    participant = student(elim_stage, "Ola", "Lis", "ola@example.org")
    WorkshopAttendance.objects.create(participant=participant, workshop_key=key)

    result = buckets(preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key))

    assert [row.participant for row in result["already"]] == [participant]
    assert result["matched"] == []


def test_podpowiedz_warsztatu_po_dacie_z_raportu(web_client, logged_coordinator, elim_stage, workshops_page):
    student(elim_stage, "Ola", "Lis", "ola@example.org")

    response = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", name="Splątanie 2026-12-03.csv")

    data = response.context["preview"]
    assert data.workshop["key"] == workshop_rows(workshops_page)[1]["key"]
    assert data.workshop_from_date is True


def test_bez_warsztatu_i_bez_pasujacej_daty_prosi_o_wybor(web_client, logged_coordinator, elim_stage, key):
    response = preview(web_client, "Ola,Lis,ola@example.org,60 min\n")

    assert response.status_code == 400
    assert "Wybierz warsztat" in response.content.decode()


def test_inna_data_niz_wybranego_warsztatu_ostrzega(web_client, logged_coordinator, elim_stage, key):
    response = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key, name="2026-12-03.csv")

    assert response.context["preview"].date_mismatch is True
    assert "inna niż data wybranego warsztatu" in response.content.decode()


def test_podglad_nic_nie_zapisuje(web_client, logged_coordinator, elim_stage, key):
    student(elim_stage, "Ola", "Lis", "ola@example.org")

    preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key)

    assert not WorkshopAttendance.objects.exists()
    assert not AuditLog.objects.filter(action="workshop.attendance_imported").exists()


def test_plik_i_link_naraz_albo_zaden_to_blad(web_client, logged_coordinator, elim_stage, key):
    neither = web_client.post(reverse(URL), {"workshop": key, "threshold": 30})
    both = web_client.post(
        reverse(URL),
        {
            "workshop": key,
            "threshold": 30,
            "file": report_file("Ola,Lis,ola@example.org,60 min\n"),
            "link": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
        },
    )

    assert neither.status_code == 400
    assert both.status_code == 400


# --- zatwierdzenie -------------------------------------------------------------------------------


def test_zapis_doklada_i_niczego_nie_kasuje(web_client, logged_coordinator, elim_stage, key):
    """Import mówi „ci byli”, a nie „tylko ci byli” – ręczne odhaczenie zostaje."""
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")
    marked_by_hand = student(elim_stage, "Ewa", "Kot", "ewa@example.org")
    WorkshopAttendance.objects.create(participant=marked_by_hand, workshop_key=key)
    token = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key).context["preview"].token

    response = web_client.post(reverse(URL), {"token": token, "attend": [present.pk]})

    assert response.status_code == 302
    assert set(WorkshopAttendance.objects.values_list("participant_id", flat=True)) == {
        present.pk,
        marked_by_hand.pk,
    }


def test_odznaczony_dopasowany_nie_jest_zapisany(web_client, logged_coordinator, elim_stage, key):
    student(elim_stage, "Ola", "Lis", "ola@example.org")
    token = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key).context["preview"].token

    web_client.post(reverse(URL), {"token": token})

    assert not WorkshopAttendance.objects.exists()


def test_zatwierdzenie_przyjmuje_tylko_osoby_z_podgladu(web_client, logged_coordinator, elim_stage, key):
    """Ręcznie dopisany identyfikator spoza tokenu nie dostaje obecności."""
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")
    outsider = student(elim_stage, "Ewa", "Kot", "ewa@example.org")
    token = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key).context["preview"].token

    web_client.post(
        reverse(URL), {"token": token, "attend": [present.pk, outsider.pk], "below": [outsider.pk]}
    )

    assert list(WorkshopAttendance.objects.values_list("participant_id", flat=True)) == [present.pk]


def test_ponizej_progu_mozna_oznaczyc_mimo_to(web_client, logged_coordinator, elim_stage, key):
    short = student(elim_stage, "Ewa", "Kot", "ewa@example.org")
    token = preview(web_client, "Ewa,Kot,ewa@example.org,5 min\n", workshop=key).context["preview"].token

    web_client.post(reverse(URL), {"token": token, "below": [short.pk]})

    assert WorkshopAttendance.objects.filter(participant=short, workshop_key=key).exists()


def test_wybor_niejednoznacznego_i_kod_recznie(web_client, logged_coordinator, elim_stage, key):
    student(elim_stage, "Jan", "Kowalski")
    chosen = student(elim_stage, "Jan", "Kowalski")
    by_code = student(elim_stage, "Piotr", "Zaręba", "piotr@example.org")
    data = preview(
        web_client,
        "Jan,Kowalski,prywatny@example.org,50 min\n"
        "Gość,Z Telefonu,,50 min\n"
        "Nikt,Taki,nikt@example.org,50 min\n",
        workshop=key,
    ).context["preview"]
    assert len(data.ambiguous) == 1
    assert len(data.unknown) == 2

    response = web_client.post(
        reverse(URL),
        {
            "token": data.token,
            "choice_0": chosen.pk,
            "code_0": by_code.public_code.lower(),
            "code_1": "XX-NIEMA",
        },
        follow=True,
    )

    assert set(WorkshopAttendance.objects.values_list("participant_id", flat=True)) == {chosen.pk, by_code.pk}
    assert "XX-NIEMA" in response.content.decode()


def test_wybor_spoza_kandydatow_jest_ignorowany(web_client, logged_coordinator, elim_stage, key):
    student(elim_stage, "Jan", "Kowalski")
    student(elim_stage, "Jan", "Kowalski")
    outsider = student(elim_stage, "Ewa", "Kot", "ewa@example.org")
    token = preview(web_client, "Jan,Kowalski,x@example.org,50 min\n", workshop=key).context["preview"].token

    web_client.post(reverse(URL), {"token": token, "choice_0": outsider.pk})

    assert not WorkshopAttendance.objects.exists()


def test_kod_uczestnika_innego_konkursu_nie_przechodzi(
    web_client, logged_coordinator, elim_stage, key, other_competition
):
    stranger = ParticipantFactory(competition=other_competition)
    token = preview(web_client, "Nikt,Taki,nikt@example.org,50 min\n", workshop=key).context["preview"].token

    web_client.post(reverse(URL), {"token": token, "code_0": stranger.public_code})

    assert not WorkshopAttendance.objects.exists()


def test_token_innego_koordynatora_jest_odrzucony(web_client, logged_coordinator, elim_stage, key):
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")
    token = preview(web_client, "Ola,Lis,ola@example.org,60 min\n", workshop=key).context["preview"].token
    web_client.force_login(CoordinatorFactory())

    response = web_client.post(reverse(URL), {"token": token, "attend": [present.pk]}, follow=True)

    assert not WorkshopAttendance.objects.exists()
    assert "Podgląd importu wygasł" in response.content.decode()


def test_podrobiony_token_jest_odrzucony(web_client, logged_coordinator, elim_stage, key):
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")

    web_client.post(reverse(URL), {"token": "abc:def", "attend": [present.pk]})

    assert not WorkshopAttendance.objects.exists()


def test_audyt_bez_danych_osobowych(web_client, logged_coordinator, elim_stage, key):
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")
    token = (
        preview(
            web_client,
            "Ola,Lis,ola@example.org,60 min\nGość,Tajemniczy,gosc@example.org,60 min\n",
            workshop=key,
        )
        .context["preview"]
        .token
    )

    web_client.post(reverse(URL), {"token": token, "attend": [present.pk]})

    entry = AuditLog.objects.get(action="workshop.attendance_imported")
    assert entry.diff["workshop"] == key
    assert entry.diff["source"] == "file"
    assert entry.diff["added"] == 1
    assert entry.diff["unknown"] == 1
    text = str(entry.diff)
    for personal in ("@", "Ola", "Lis", "Gość", "Tajemniczy", present.public_code):
        assert personal not in text


def test_token_nie_niesie_danych_z_raportu(web_client, logged_coordinator, elim_stage, key):
    from django.core import signing

    from apps.cms.meet_import import PREVIEW_SALT

    student(elim_stage, "Ola", "Lis", "ola@example.org")
    token = (
        preview(
            web_client,
            "Ola,Lis,ola@example.org,60 min\nGość,Tajemniczy,gosc@example.org,60 min\n",
            workshop=key,
        )
        .context["preview"]
        .token
    )

    payload = str(signing.loads(token, salt=PREVIEW_SALT))
    assert "@" not in payload
    assert "Tajemniczy" not in payload


# --- link ----------------------------------------------------------------------------------------


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/csv"}

    def __init__(self, body: bytes):
        self.body = body

    def iter_content(self, chunk_size=65536):  # noqa: ARG002
        yield self.body

    def close(self):
        pass


def rest_framework_with(**rates):
    config = dict(settings.REST_FRAMEWORK)
    config["DEFAULT_THROTTLE_RATES"] = {**config["DEFAULT_THROTTLE_RATES"], **rates}
    return config


def test_import_z_linku(web_client, logged_coordinator, elim_stage, key, monkeypatch):
    present = student(elim_stage, "Ola", "Lis", "ola@example.org")
    calls = []

    def fake_get(url):
        calls.append(url)
        return FakeResponse((HEADER + "Ola,Lis,ola@example.org,60 min\n").encode())

    monkeypatch.setattr(meet_report, "_http_get", fake_get)

    response = web_client.post(
        reverse(URL),
        {
            "workshop": key,
            "threshold": 30,
            "link": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=5",
        },
    )

    assert calls == [f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=5"]
    assert [row.participant for row in buckets(response)["matched"]] == [present]
    assert response.context["preview"].source == "link"


def test_link_spoza_google_nie_wychodzi_do_sieci(
    web_client, logged_coordinator, elim_stage, key, monkeypatch
):
    monkeypatch.setattr(meet_report, "_http_get", lambda url: pytest.fail(f"nie wolno pobierać {url}"))

    response = web_client.post(
        reverse(URL), {"workshop": key, "threshold": 30, "link": "https://evil.example/spreadsheets/d/x"}
    )

    assert response.status_code == 400
    assert "Akceptujemy wyłącznie link do arkusza Google" in response.content.decode()


@override_settings(REST_FRAMEWORK=rest_framework_with(meet_link="1/hour"))
def test_limit_pobran_z_linku(web_client, logged_coordinator, elim_stage, key, monkeypatch):
    monkeypatch.setattr(
        meet_report, "_http_get", lambda url: FakeResponse((HEADER + "A,B,a@example.org,5\n").encode())
    )
    data = {
        "workshop": key,
        "threshold": 30,
        "link": f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
    }

    assert web_client.post(reverse(URL), data).status_code == 200
    throttled = web_client.post(reverse(URL), data)

    assert throttled.status_code == 429
    assert "Zbyt wiele pobrań z linku" in throttled.content.decode()
    # Plik limitu nie zużywa.
    assert preview(web_client, "A,B,a@example.org,5 min\n", workshop=key).status_code == 200


# --- uprawnienia i ekran -------------------------------------------------------------------------


def test_ekran_tylko_dla_koordynatora(web_client, participant, workshops_page):
    web_client.force_login(participant.user)

    assert web_client.get(reverse(URL)).status_code == 403
    assert web_client.post(reverse(URL), {"token": "x"}).status_code == 403


def test_anonim_trafia_na_logowanie(web_client, workshops_page):
    response = web_client.get(reverse(URL))

    assert response.status_code == 302


def test_formularz_i_przycisk_na_ekranie_obecnosci(
    web_client, logged_coordinator, elim_stage, workshops_page
):
    form = web_client.get(reverse(URL)).content.decode()
    table = web_client.get(reverse("web:coordinator-workshop-attendance")).content.decode()

    assert "Splątanie" in form
    assert "rozpoznaj po dacie z raportu" in form
    assert reverse(URL) in table
