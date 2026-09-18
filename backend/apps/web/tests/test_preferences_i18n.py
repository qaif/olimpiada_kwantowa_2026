"""Angielska wersja interfejsu i tryb wysokiego kontrastu.

Pięć pytań, na które ten moduł odpowiada:

- **czy serwis wolno mieć tylko po polsku** – Olimpiada Kwantowa ma być polska „niezależnie od
  tego, o co prosi przeglądarka”, więc przy wyłączonym przełączniku
  ``cms.SiteSettings.english_interface_enabled`` polskie ma być **wszystko**: nagłówek
  ``Accept-Language``, ciasteczko, zapis na koncie i pasek konta (sekcja (e)),
- **czy angielski w ogóle działa** – czyli czy skompilowany katalog (``locale/en``) jest wczytywany
  i czy panel uczestnika mówi po angielsku, a nie po polsku z angielskim ``lang``,
- **kto wygrywa**: jawny wybór człowieka (ciasteczko, a dla konta wiersz ``UserPreference``) ma
  być ważniejszy niż ``Accept-Language`` przeglądarki, a nie odwrotnie,
- **czy kontrast dociera do dokumentu** – atrybut ``data-contrast`` na ``<html>`` jest jedynym
  zaczepieniem arkusza, więc jego brak znaczy tryb, którego nie widać,
- **czy przełącznik nie jest otwartym przekierowaniem**: pole ``next`` przychodzi od nadawcy
  żądania i obcy adres ma zostać odrzucony.

Testy angielskiego wołają fiksturę ``english_enabled_site`` (konftest projektu) i to nie jest
formalność: **domyślnie angielskiego nie ma**, więc test, który o niego pyta bez włączenia
przełącznika, pytałby o serwis, którego ta instalacja nie oferuje.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.accounts.models import UserPreference

pytestmark = pytest.mark.django_db

PREFERENCES_URL = "/account/preferences/"

#: Odnośnik „Przejdź do treści” z ``templates/base.html`` – jedyny napis interfejsu, który stoi na
#: **każdej** stronie serwisu, łącznie z tymi bez własnych tłumaczonych nagłówków. Dlatego to on
#: jest próbką języka tam, gdzie test wchodzi na kilka adresów naraz.
SKIP_LINK_PL = "Przejdź do treści"
SKIP_LINK_EN = "Skip to content"


def html_tag(content: str) -> str:
    """Sam znacznik ``<html …>`` – tylko on niesie język i tryb kontrastu."""
    start = content.index("<html")
    return content[start : content.index(">", start) + 1]


# --- (a) język ------------------------------------------------------------------------------------


def test_accept_language_header_is_respected(web_client, english_enabled_site):
    """Bez żadnego wyboru idziemy za przeglądarką – to ustawienie systemu, a nie zgadywanie."""
    english_enabled_site()

    english = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="en")
    polish = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl")

    assert 'lang="en"' in html_tag(english.content.decode())
    assert "The Olympiad in numbers" in english.content.decode()
    assert 'lang="pl"' in html_tag(polish.content.decode())
    assert "Olimpiada w liczbach" in polish.content.decode()


def test_setlang_persists_in_a_cookie(web_client, english_enabled_site):
    english_enabled_site()

    response = web_client.post(PREFERENCES_URL, {"language": "en", "next": "/statystyki/"})

    assert response.status_code == 302
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "en"
    # Ciasteczko wygrywa z nagłówkiem przeglądarki: to jawny wybór człowieka.
    later = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl")
    assert "The Olympiad in numbers" in later.content.decode()


def test_unknown_language_is_ignored(web_client, english_enabled_site):
    """Kod spoza ``LANGUAGES`` odpada nawet wtedy, gdy serwis **ma** z czego wybierać."""
    english_enabled_site()

    web_client.post(PREFERENCES_URL, {"language": "de", "next": "/statystyki/"})

    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert "Olimpiada w liczbach" in content


def test_account_choice_travels_with_the_person(web_client, participant, english_enabled_site):
    """Zapis na koncie ma wygrywać z przeglądarką – także na drugim urządzeniu (nowa sesja)."""
    english_enabled_site()
    web_client.force_login(participant.user)
    web_client.post(PREFERENCES_URL, {"language": "en", "next": "/me/"})

    preference = UserPreference.objects.get(user=participant.user)
    assert preference.language == "en"

    from django.test import Client

    other_device = Client()
    other_device.force_login(participant.user)
    content = other_device.get("/me/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert 'lang="en"' in html_tag(content)


def test_participant_panel_speaks_english(web_client, participant, entry, problems, english_enabled_site):
    english_enabled_site()
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert "Your entry" in content
    assert "Upload solution" in content
    assert "Twoje zgłoszenie" not in content


def test_coordinator_screens_stay_polish(web_client, coordinator, english_enabled_site):
    """Zakres tłumaczenia jest wąski z wyboru: panel organizatora zostaje po polsku.

    Angielski jest tu **włączony** i o to chodzi: bez tego test przechodziłby dlatego, że serwis
    nie ma angielskiego w ogóle, a nie dlatego, że ekrany koordynatora zostały nieprzetłumaczone.
    """
    english_enabled_site()
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


def test_contrast_switch_does_not_reset_the_language(web_client, participant, english_enabled_site):
    english_enabled_site()
    web_client.force_login(participant.user)
    web_client.post(PREFERENCES_URL, {"language": "en", "next": "/me/"})

    web_client.post(PREFERENCES_URL, {"language": "en", "high_contrast": "1", "next": "/me/"})

    preference = UserPreference.objects.get(user=participant.user)
    assert preference.language == "en"
    assert preference.high_contrast is True


# --- (c) przełącznik w pasku konta ----------------------------------------------------------------


def test_account_bar_offers_the_other_language_and_the_contrast(web_client, english_enabled_site):
    """Oba przełączniki są ikonami, więc sprawdzamy ich **nazwy dostępne**, a nie napisy.

    Flaga i kółko nie mają tekstu: gdyby test patrzył na „EN” i „Kontrast”, po zamianie napisów
    na rysunki przestałby cokolwiek chronić, a jedyne, co tu może cicho zniknąć, to właśnie
    nazwa – przycisk bez niej dalej wygląda dobrze i dalej działa myszą.
    """
    english_enabled_site()

    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert 'value="en"' in content
    # Polecenie zmiany języka jest w języku docelowym – czyta je ktoś, kto nie czyta polskiego.
    assert "Switch to English" in content
    assert 'aria-label="Wysoki kontrast: włącz"' in content
    assert 'aria-pressed="false"' in content


def test_account_bar_switches_are_icons_without_a_caption(web_client, english_enabled_site):
    """Napisów „EN” i „Kontrast” w pasku nie ma – organizator poprosił o flagę i ikonę."""
    english_enabled_site()

    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert ">EN</button>" not in content
    assert ">Kontrast<" not in content
    assert 'class="pref-flag"' in content
    assert 'class="pref-icon"' in content
    # Rysunek jest dekoracją: nazwę niesie ukryty tekst, a dwie nazwy czytnik ekranu czytałby dwa razy.
    flag = content[content.index('class="pref-flag"') :][:400]
    assert 'aria-hidden="true"' in flag


def test_contrast_button_says_what_the_click_will_do(web_client):
    """Etykieta opisuje **skutek**, a stan niesie ``aria-pressed`` – po włączeniu oba się zmieniają."""
    web_client.post(PREFERENCES_URL, {"high_contrast": "1", "next": "/statystyki/"})

    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="pl").content.decode()

    assert 'aria-label="Wysoki kontrast: wyłącz"' in content
    assert 'aria-pressed="true"' in content


def test_next_outside_the_site_is_refused(web_client):
    """``next`` przychodzi od nadawcy żądania – otwarte przekierowanie byłoby tu phishingiem."""
    response = web_client.post(PREFERENCES_URL, {"language": "en", "next": "https://cudza.strona.test/login"})

    assert response.status_code == 302
    assert response["Location"] == "/"


# --- (d) tłumaczenia poza interfejsem -------------------------------------------------------------


def test_problem_title_falls_back_to_polish(web_client, participant, entry, problems, english_enabled_site):
    """Zadanie bez tłumaczenia ma być czytelne, a nie puste – stąd odwrót na wersję polską."""
    english_enabled_site()
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert problems[0].title in content


def test_problem_title_uses_the_english_version_when_it_exists(
    web_client, participant, entry, problems, english_enabled_site
):
    english_enabled_site()
    problems[0].title_en = "Inequality of means"
    problems[0].save(update_fields=["title_en"])
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert "Inequality of means" in content
    assert problems[0].title not in content


# --- (e) serwis tylko po polsku -------------------------------------------------------------------
#
# Prośba organizatora brzmiała: „strona tylko w wersji polskiej (sam CMS może dawać opcję zrobienia
# strony w wersji angielskiej, ale do polskiej olimpiady niech będzie wersja tylko w języku polskim
# na razie)”. Ta sekcja jest jej zapisem i pyta o **każde** źródło języka z osobna, bo wystarczy
# jedno niedomknięte, żeby obietnica przestała być prawdą: uczeń z angielskim systemem zobaczyłby
# angielską stronę, o której organizator nigdy się nie dowie.
#
# Testy tej sekcji **nie** wołają ``english_enabled_site`` – i to jest ich przedmiot: stan domyślny
# instalacji ma być polski, bez ani jednego ustawienia w bazie.

#: Adresy, na których sprawdzamy polskość: strona główna (część informacyjna, drzewo Wagtaila)
#: oraz dwa ekrany kont, czyli te, które w ogóle są tłumaczone.
POLISH_ONLY_PATHS = ("/", "/register/", "/login/")


@pytest.mark.parametrize("path", POLISH_ONLY_PATHS)
def test_accept_language_does_not_switch_the_site_to_english(web_client, path):
    """``Accept-Language: en`` jest prośbą przeglądarki, a nie decyzją o języku tego serwisu."""
    response = web_client.get(path, HTTP_ACCEPT_LANGUAGE="en")

    content = response.content.decode()
    assert response.status_code == 200
    assert 'lang="pl"' in html_tag(content)
    assert SKIP_LINK_PL in content
    assert SKIP_LINK_EN not in content


def test_the_response_declares_polish_in_the_content_language_header(web_client):
    """Nagłówek ma mówić o języku, który naprawdę wyszedł – inaczej skłamie pośrednikom i czytnikom."""
    response = web_client.get("/", HTTP_ACCEPT_LANGUAGE="en")

    assert response["Content-Language"] == "pl"


def test_the_language_cookie_is_ignored(web_client):
    """Ciasteczko ``django_language`` zostaje w przeglądarce, ale nie rozstrzyga już o niczym.

    Ustawiamy je wprost, a nie przez przełącznik: przełącznika w pasku nie ma, a ciasteczko zostaje
    na dyskach osób, które wybrały angielski, **zanim** organizator go wyłączył. To one są tu
    przedmiotem testu.
    """
    web_client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    content = web_client.get("/statystyki/").content.decode()

    assert 'lang="pl"' in html_tag(content)
    assert "Olimpiada w liczbach" in content


def test_a_stored_english_preference_is_not_applied_but_is_kept(web_client, participant):
    """Zapis konta przestaje działać, a nie przestaje istnieć – wraca z przełącznikiem."""
    UserPreference.objects.create(user=participant.user, language="en")
    web_client.force_login(participant.user)

    content = web_client.get("/me/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert 'lang="pl"' in html_tag(content)
    assert UserPreference.objects.get(user=participant.user).language == "en"


def test_the_contrast_switch_does_not_erase_the_stored_english(web_client, participant):
    """Formularz kontrastu posyła ``language=pl`` – i to jest chwila, w której zapis mógłby zginąć.

    Pasek konta wysyła bieżący język razem z kontrastem, żeby jedno przestawienie nie kasowało
    drugiego. W serwisie bez angielskiego „bieżący język” to zawsze ``pl``, więc zapisanie go
    zamieniłoby kliknięcie w kontrast w ciche skasowanie cudzego wyboru.
    """
    UserPreference.objects.create(user=participant.user, language="en")
    web_client.force_login(participant.user)

    web_client.post(PREFERENCES_URL, {"language": "pl", "high_contrast": "1", "next": "/me/"})

    preference = UserPreference.objects.get(user=participant.user)
    assert preference.language == "en"
    assert preference.high_contrast is True


def test_posting_english_by_hand_changes_nothing(web_client, participant):
    """Przełącznika w pasku nie ma, ale samo żądanie da się wysłać z ręki – i ma zostać odrzucone."""
    web_client.force_login(participant.user)

    web_client.post(PREFERENCES_URL, {"language": "en", "next": "/me/"})

    content = web_client.get("/me/").content.decode()
    assert 'lang="pl"' in html_tag(content)
    assert UserPreference.objects.get(user=participant.user).language == ""


def test_the_language_switch_is_absent_from_the_account_bar(web_client):
    """Flagi nie ma, a przycisk kontrastu zostaje: to dwa osobne formularze i dwie osobne decyzje."""
    content = web_client.get("/statystyki/", HTTP_ACCEPT_LANGUAGE="en").content.decode()

    assert 'name="language" value="en"' not in content
    assert "Switch to English" not in content
    assert 'class="pref-flag"' not in content
    # Kontrast jest ustawieniem dostępności, a nie językiem – wyłączenie angielskiego go nie dotyczy.
    assert 'class="pref-icon"' in content
    assert 'aria-label="Wysoki kontrast: włącz"' in content
