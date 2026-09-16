"""Angielska wersja interfejsu i tryb wysokiego kontrastu.

Cztery pytania, na które ten moduł odpowiada:

- **czy angielski w ogóle działa** – czyli czy skompilowany katalog (``locale/en``) jest wczytywany
  i czy panel uczestnika mówi po angielsku, a nie po polsku z angielskim ``lang``,
- **kto wygrywa**: jawny wybór człowieka (ciasteczko, a dla konta wiersz ``UserPreference``) ma
  być ważniejszy niż ``Accept-Language`` przeglądarki, a nie odwrotnie,
- **czy kontrast dociera do dokumentu** – atrybut ``data-contrast`` na ``<html>`` jest jedynym
  zaczepieniem arkusza, więc jego brak znaczy tryb, którego nie widać,
- **czy przełącznik nie jest otwartym przekierowaniem**: pole ``next`` przychodzi od nadawcy
  żądania i obcy adres ma zostać odrzucony.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.accounts.models import UserPreference

pytestmark = pytest.mark.django_db

PREFERENCES_URL = "/account/preferences/"


def html_tag(content: str) -> str:
    """Sam znacznik ``<html …>`` – tylko on niesie język i tryb kontrastu."""
    start = content.index("<html")
    return content[start : content.index(">", start) + 1]


# --- (a) język ------------------------------------------------------------------------------------


def test_accept_language_header_is_respected(web_client):
    """Bez żadnego wyboru idziemy za przeglądarką – to ustawienie systemu, a nie zgadywanie."""
    english = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="en")
    polish = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl")

    assert 'lang="en"' in html_tag(english.content.decode())
    assert "The Olympiad in numbers" in english.content.decode()
    assert 'lang="pl"' in html_tag(polish.content.decode())
    assert "Olimpiada w liczbach" in polish.content.decode()


def test_setlang_persists_in_a_cookie(web_client):
    response = web_client.post(PREFERENCES_URL, {"language": "en", "next": "/statystyki/"})

    assert response.status_code == 302
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"
    # Ciasteczko wygrywa z nagłówkiem przeglądarki: to jawny wybór człowieka.
    later = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl")
    assert "The Olympiad in numbers" in later.content.decode()


def test_unknown_language_is_ignored(web_client):
    web_client.post(PREFERENCES_URL, {"language": "de", "next": "/statystyki/"})

    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert "Olimpiada w liczbach" in content


def test_account_choice_travels_with_the_person(web_client, participant):
    """Zapis na koncie ma wygrywać z przeglądarką – także na drugim urządzeniu (nowa sesja)."""
    web_client.force_login(participant.user)
    web_client.post(PREFERENCES_URL, {"language": "en", "next": "/me/"})

    preference = UserPreference.objects.get(user=participant.user)
    assert preference.language == "en"

    from django.test import Client

    other_device = Client()
    other_device.force_login(participant.user)
    content = other_device.get("/me/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert 'lang="en"' in html_tag(content)


def test_participant_panel_speaks_english(web_client, participant, entry, problems):
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert "Your entry" in content
    assert "Upload solution" in content
    assert "Twoje zgłoszenie" not in content


def test_coordinator_screens_stay_polish(web_client, coordinator):
    """Zakres tłumaczenia jest wąski z wyboru: panel organizatora zostaje po polsku."""
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert "Panel koordynatora" in content


# --- (b) tryb wysokiego kontrastu -----------------------------------------------------------------


def test_contrast_attribute_is_absent_by_default(web_client):
    content = web_client.get("/statystyki/").content.decode()

    assert "data-contrast" not in html_tag(content)


def test_contrast_toggle_reaches_the_document(web_client):
    web_client.post(PREFERENCES_URL, {"high_contrast": "1", "next": "/statystyki/"})

    content = web_client.get("/statystyki/").content.decode()

    assert 'data-contrast="high"' in html_tag(content)


def test_contrast_is_stored_on_the_account(web_client, participant):
    web_client.force_login(participant.user)

    web_client.post(PREFERENCES_URL, {"high_contrast": "1", "next": "/me/"})

    assert UserPreference.objects.get(user=participant.user).high_contrast is True


def test_contrast_switch_does_not_reset_the_language(web_client, participant):
    web_client.force_login(participant.user)
    web_client.post(PREFERENCES_URL, {"language": "en", "next": "/me/"})

    web_client.post(PREFERENCES_URL, {"language": "en", "high_contrast": "1", "next": "/me/"})

    preference = UserPreference.objects.get(user=participant.user)
    assert preference.language == "en"
    assert preference.high_contrast is True


# --- (c) przełącznik w pasku konta ----------------------------------------------------------------


def test_account_bar_offers_the_other_language_and_the_contrast(web_client):
    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert 'value="en"' in content
    assert "Kontrast" in content


def test_next_outside_the_site_is_refused(web_client):
    """``next`` przychodzi od nadawcy żądania – otwarte przekierowanie byłoby tu phishingiem."""
    response = web_client.post(PREFERENCES_URL, {"language": "en", "next": "https://cudza.strona.test/login"})

    assert response.status_code == 302
    assert response["Location"] == "/"


# --- (d) tłumaczenia poza interfejsem -------------------------------------------------------------


def test_problem_title_falls_back_to_polish(web_client, participant, entry, problems):
    """Zadanie bez tłumaczenia ma być czytelne, a nie puste – stąd odwrót na wersję polską."""
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert problems[0].title in content


def test_problem_title_uses_the_english_version_when_it_exists(web_client, participant, entry, problems):
    problems[0].title_en = "Inequality of means"
    problems[0].save(update_fields=["title_en"])
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert "Inequality of means" in content
    assert problems[0].title not in content
