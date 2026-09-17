"""Przeciąganie pliku na kartę zadania (panel uczestnika).

Sama obsługa ``drop`` jest w przeglądarce i tych testów nie da się nią zrobić. Testujemy to,
co przeglądarce **podajemy** – i to jest właśnie ta część, którą najłatwiej zepsuć:

- **strefa niesie limity tego zadania**, a nie limity wpisane w skrypcie. ``static/js/upload-dropzone.js``
  nie zna ani listy formatów, ani megabajtów: dostaje je w ``data-accept`` i ``data-max-mb``
  z ``competitions.Problem``. Skopiowanie ich kiedyś do skryptu byłoby drugą definicją tej samej
  reguły – i rozjechałoby się z pierwszą zmianą limitu przez koordynatora,
- **komunikaty odmowy składa serwer.** Przeglądarka nie ma katalogu tłumaczeń, więc zdania jadą
  gotowe, w aktywnym języku, w ``data-msg-*`` – ta sama droga, co słowa odliczania w ``_now.html``.
  Podstawiane są tylko ``{name}`` i ``{size}``, których serwer znać nie może,
- **natywne pole zostaje na miejscu.** Strefa jest dodatkiem, a nie zamiennikiem: bez niej
  (wyłączony JavaScript, przeglądarka bez ``DataTransfer``, klawiatura) wysyłka ma działać
  dokładnie tak, jak działała – stąd test na obecne, widoczne i wymagane ``input[type=file]``,
- **wysyłka nadal jest zwykłym POST-em.** Strefa niczego nie wysyła i niczego nie zmienia po
  stronie żądania; karta wracająca po uploadzie musi wrócić uzbrojona, bo HTMX podmienia ją
  bez przeładowania strony i skrypt uzbraja wyłącznie to, co dostanie w ``htmx:afterSwap``.

Skrypt ładuje się z ``nonce`` – bez tego CSP (``script-src`` bez ``'unsafe-inline'``) po prostu
go nie wykona, a funkcja zniknie po cichu, bez śladu w testach, które patrzą tylko na HTML.
"""

from __future__ import annotations

import re

import pytest

from apps.competitions.tests.factories import ProblemFactory
from apps.submissions.models import Submission
from apps.submissions.tests.factories import pdf_upload

pytestmark = pytest.mark.django_db

TASKS_URL = "/me/?tab=zadania"


@pytest.fixture
def photo_problem(elim_stage):
    """Zadanie ze zdjęciem i **nietypowym** limitem.

    Obie wartości są celowo inne niż domyślne (``["pdf"]`` i 20 MB): przy domyślnych test
    przeszedłby również wtedy, gdyby szablon wpisywał stałą zamiast czytać zadanie.
    """
    return ProblemFactory(
        stage=elim_stage,
        number=3,
        title="Zdjęcie rozwiązania",
        allowed_formats=["pdf", "jpg"],
        max_file_mb=7,
    )


def tasks_page(web_client, participant) -> str:
    web_client.force_login(participant.user)
    return web_client.get(TASKS_URL).content.decode()


def dropzone_tag(content: str) -> str:
    """Sam znacznik otwierający strefę – tylko on niesie limity i komunikaty.

    Szukamy znacznika, a nie całej karty: asercja na całej karcie przechodziłaby także wtedy,
    gdyby atrybut wylądował przy sąsiednim elemencie, gdzie skrypt go nie zobaczy.
    """
    match = re.search(r'<div class="dropzone"[^>]*>', content)
    assert match is not None, "karta zadania bez strefy upuszczania"
    return match.group(0)


def upload_url(entry, number: int) -> str:
    return f"/me/stages/{entry.stage_id}/problems/{number}/upload/"


# --- (a) co dostaje skrypt ------------------------------------------------------------------------


def test_the_dropzone_carries_the_limits_of_this_problem(web_client, participant, entry, photo_problem):
    tag = dropzone_tag(tasks_page(web_client, participant))

    assert "data-dropzone" in tag
    # Ta sama lista rozszerzeń, co w atrybucie ``accept`` pola: skrypt ma odrzucać dokładnie to,
    # czego okno wyboru pliku i tak nie pokaże (``.jpeg`` jest drugim rozszerzeniem zdjęcia).
    assert f'data-accept="{photo_problem.accept_attribute}"' in tag
    assert 'data-accept=".pdf,.jpg,.jpeg"' in tag
    assert 'data-max-mb="7"' in tag


def test_the_refusals_are_composed_by_the_server(web_client, participant, entry, photo_problem):
    """Gotowe zdania, a nie kody – przeglądarka nie ma katalogu tłumaczeń."""
    tag = dropzone_tag(tasks_page(web_client, participant))

    assert "Niedozwolony format – dozwolone: PDF, JPG" in tag
    assert "Plik jest za duży (maks. 7 MB)" in tag
    # Nazwa i rozmiar są jedynymi miejscami, których serwer nie zna – zostają jako znaczniki.
    assert "Wybrano: {name} ({size} MB)" in tag


def test_the_status_line_is_announced_politely(web_client, participant, entry, photo_problem):
    """Potwierdzenie własnej czynności czyta się po kolei, a nie przerywa czytanie strony."""
    content = tasks_page(web_client, participant)

    match = re.search(r'<p class="dropzone__status"[^>]*>', content)
    assert match is not None, "brak miejsca na komunikat o wybranym pliku"
    assert "data-dropzone-status" in match.group(0)
    assert 'aria-live="polite"' in match.group(0)


def test_the_hint_names_the_formats_and_the_limit(web_client, participant, entry, photo_problem):
    """Podpowiedź mówi, co wolno upuścić – inaczej granicę poznaje się dopiero po odmowie."""
    content = tasks_page(web_client, participant)

    assert "Przeciągnij plik tutaj albo wybierz z dysku (PDF, JPG do 7 MB)." in content


# --- (b) strefa jest dodatkiem, a nie zamiennikiem ------------------------------------------------


def test_the_native_file_input_stays_visible_and_required(web_client, participant, entry, photo_problem):
    """Bez JavaScriptu (i z klawiatury) wysyłka ma działać tak, jak działała."""
    content = tasks_page(web_client, participant)

    match = re.search(r'<input type="file"[^>]*>', content)
    assert match is not None, "strefa zjadła natywne pole wyboru pliku"
    field = match.group(0)
    assert "required" in field
    assert f'accept="{photo_problem.accept_attribute}"' in field
    # Ani ukryte, ani odsunięte poza drzewo dostępności – to jedyna droga do pliku bez myszy.
    assert "hidden" not in field
    assert 'aria-hidden="true"' not in field
    assert f'<label class="dropzone__label" for="file-{photo_problem.pk}"' in content


def test_the_script_is_loaded_with_a_nonce(web_client, participant, entry, photo_problem):
    web_client.force_login(participant.user)
    response = web_client.get(TASKS_URL)

    match = re.search(
        r'<script defer nonce="([^"]+)" src="([^"]*upload-dropzone[^"]*\.js)">',
        response.content.decode(),
    )
    assert match is not None, "brak skryptu strefy upuszczania z nonce"
    # Nonce musi być **tym** nonce'em z polityki – obcy jest tyle wart, co żaden.
    assert f"'nonce-{match.group(1)}'" in response.headers["Content-Security-Policy"]


# --- (c) wysyłka niczego nie zmienia --------------------------------------------------------------


def test_a_plain_post_still_creates_a_submission(web_client, participant, entry, photo_problem):
    """Strefa nie dotyka żądania: ten sam POST, ta sama praca w bazie."""
    web_client.force_login(participant.user)

    response = web_client.post(
        upload_url(entry, photo_problem.number),
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert Submission.objects.filter(entry=entry, problem=photo_problem).count() == 1


def test_the_card_returned_after_an_upload_is_armed_again(web_client, participant, entry, photo_problem):
    """HTMX podmienia kartę bez przeładowania – nowa karta musi przyjść z kompletem ``data-*``.

    Skrypt uzbraja wyłącznie to, co dostanie w ``htmx:afterSwap``. Karta wracająca bez atrybutów
    wyglądałaby tak samo, a przeciąganie działałoby dokładnie raz – do pierwszej wysyłki.
    """
    web_client.force_login(participant.user)

    response = web_client.post(
        upload_url(entry, photo_problem.number),
        {"file": pdf_upload(), "confirmed": "1"},
        HTTP_HX_REQUEST="true",
    )

    tag = dropzone_tag(response.content.decode())
    assert "data-dropzone" in tag
    assert 'data-max-mb="7"' in tag
