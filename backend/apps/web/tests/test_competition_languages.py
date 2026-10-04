"""Języki interfejsu per konkurs (docs/tasks/I18N-01.md).

Pytania, na które odpowiada ten moduł:

- **zbiór należy do konkursu** – ``Competition.ui_languages`` (kolejność, język domyślny zawsze
  w środku) i reguły ``clean()``, ekran „Ustawienia konkursu”, komenda operatora,
- **Olimpiada Kwantowa zostaje polska** – bez przełącznika, mimo ``Accept-Language``, ciasteczka
  i zapisu na koncie,
- **konkurs międzynarodowy ma wszystkie języki** – menu z jedenastoma pozycjami, gość bez nagłówka
  dostaje język domyślny konkursu (angielski), warianty z przeglądarki (``zh-CN``, ``pt-BR``)
  trafiają w nasze kody,
- **arabski jest od prawej do lewej** – ``dir="rtl"`` i ``lang="ar"`` na ``<html>``,
- **kluczowe strony renderują się** w pismach spoza łaciny (arabski, chiński, hindi),
- **list jest w języku odbiorcy** także poza parą pl/en, i tylko w języku, który konkurs oferuje.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import translation

from apps.accounts.models import UserPreference
from apps.accounts.preferences import language_for, text_direction
from apps.core.points import decimal_separator, format_points

pytestmark = pytest.mark.django_db

ALL_LANGUAGES = [code for code, _label in settings.LANGUAGES]
#: Dziesięć najczęściej używanych języków świata – zamówienie dla ``iqo`` (I18N-01 § 0).
TOP_TEN = ["en", "zh-hans", "hi", "es", "ar", "fr", "bn", "pt", "ru", "id"]


def html_tag(content: str) -> str:
    start = content.index("<html")
    return content[start : content.index(">", start) + 1]


@pytest.fixture
def international(competition):
    """Konkurs #1 przestawiony na wzór ``iqo``: angielski domyślny i wszystkie języki instalacji.

    Konkurs #1, a nie drugi, bo jego witryna jest domyślna – ``Client()`` i fikstury uczestnika
    (``participant``, ``entry``) pracują właśnie na nim, a test ma sprawdzić strony, a nie
    zakładanie konkursu.
    """
    competition.default_language = "en"
    competition.interface_languages = ALL_LANGUAGES
    competition.save(update_fields=["default_language", "interface_languages"])
    return competition


# --- (a) zbiór języków konkursu -----------------------------------------------------------------


def test_installation_offers_the_ten_most_spoken_languages_and_polish():
    assert set(TOP_TEN) <= set(ALL_LANGUAGES)
    assert ALL_LANGUAGES[0] == settings.LANGUAGE_CODE == "pl"
    assert "ar" in settings.LANGUAGES_BIDI


def test_competition_one_offers_polish_only(competition):
    assert competition.ui_languages == ("pl",)


def test_ui_languages_follow_the_installation_order_and_include_the_default(competition):
    competition.default_language = "fr"
    competition.interface_languages = ["ru", "en"]

    assert competition.ui_languages == ("en", "fr", "ru")


def test_ui_languages_skip_codes_the_installation_does_not_know(competition):
    """Odczyt jest wyrozumiały: kod wycofany z ``LANGUAGES`` nie wywraca strony konkursu."""
    competition.interface_languages = ["pl", "xx"]

    assert competition.ui_languages == ("pl",)


@pytest.mark.parametrize(
    ("default", "languages", "field"),
    [
        ("pl", ["pl", "xx"], "interface_languages"),
        ("en", ["pl", "fr"], "interface_languages"),
        ("pl", ["pl", "pl"], "interface_languages"),
        ("xx", ["pl"], "default_language"),
    ],
)
def test_clean_refuses_inconsistent_language_sets(competition, default, languages, field):
    competition.default_language = default
    competition.interface_languages = languages

    with pytest.raises(ValidationError) as excinfo:
        competition.full_clean()

    assert field in excinfo.value.message_dict


def test_settings_form_saves_the_language_set(competition):
    from apps.web.competition_forms import EDITABLE_FIELDS, CompetitionSettingsForm

    form = CompetitionSettingsForm(instance=competition)
    data = {name: form.initial.get(name) for name in EDITABLE_FIELDS if form.initial.get(name) is not None}
    data["logo"] = data.get("logo") or ""
    data["favicon"] = data.get("favicon") or ""
    data["default_language"] = "en"
    data["interface_languages"] = ["ar", "en", "pl"]
    form = CompetitionSettingsForm(data, instance=competition)

    assert form.is_valid(), form.errors
    saved = form.save()

    assert saved.interface_languages == ["pl", "en", "ar"]
    assert "interface_languages" in form.changed_fields()


def test_settings_form_refuses_a_default_outside_the_set(competition):
    from apps.web.competition_forms import EDITABLE_FIELDS, CompetitionSettingsForm

    form = CompetitionSettingsForm(instance=competition)
    data = {name: form.initial.get(name) for name in EDITABLE_FIELDS if form.initial.get(name) is not None}
    data["logo"] = data.get("logo") or ""
    data["favicon"] = data.get("favicon") or ""
    data["default_language"] = "en"
    data["interface_languages"] = ["pl", "fr"]

    form = CompetitionSettingsForm(data, instance=competition)

    assert not form.is_valid()
    assert "interface_languages" in form.errors


def test_operator_command_sets_and_shows_the_languages(competition, capsys):
    from apps.core.models import AuditLog

    call_command("competition_languages", competition.slug, *reversed(TOP_TEN), "--default", "en")

    competition.refresh_from_db()
    assert competition.default_language == "en"
    assert competition.interface_languages == TOP_TEN
    assert AuditLog.objects.filter(action="competition.languages_changed").exists()
    assert "Języki interfejsu: en zh-hans hi es ar fr bn pt ru id" in capsys.readouterr().out


def test_operator_command_refuses_unknown_codes(competition):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("competition_languages", competition.slug, "pl", "klingon")

    competition.refresh_from_db()
    assert competition.interface_languages == ["pl"]


# --- (b) Olimpiada Kwantowa: tylko polski --------------------------------------------------------


@pytest.mark.parametrize("header", ["en", "ar", "zh-CN,zh;q=0.9", "hi-IN"])
def test_competition_one_ignores_the_browser_language(client, competition, header):
    content = client.get("/login/", HTTP_ACCEPT_LANGUAGE=header).content.decode()

    assert 'lang="pl"' in html_tag(content)
    assert 'dir="ltr"' in html_tag(content)
    assert "lang-menu" not in content
    assert not re.search(r'<button[^>]*name="language"', content)


def test_competition_one_ignores_a_stored_arabic_choice(client, competition, participant):
    UserPreference.objects.create(user=participant.user, language="ar")
    client.force_login(participant.user)
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"

    response = client.get("/me/")

    assert 'lang="pl"' in html_tag(response.content.decode())
    assert response["Content-Language"] == "pl"
    # Zapis zostaje nietknięty – wraca, gdy konkurs arabski włączy.
    assert UserPreference.objects.get(user=participant.user).language == "ar"


# --- (c) konkurs międzynarodowy -----------------------------------------------------------------


def test_guest_without_a_header_gets_the_competition_default(client, international):
    content = client.get("/login/").content.decode()

    assert 'lang="en"' in html_tag(content)
    assert "Skip to content" in content


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-hans"),
        ("pt-BR,pt;q=0.9", "pt"),
        ("es-419", "es"),
        ("ar-EG", "ar"),
        ("de-DE,de;q=0.9", "en"),
    ],
)
def test_browser_variants_land_on_our_codes(client, international, header, expected):
    content = client.get("/login/", HTTP_ACCEPT_LANGUAGE=header).content.decode()

    assert f'lang="{expected}"' in html_tag(content)


def test_the_language_menu_lists_every_offered_language(client, international):
    content = client.get("/login/").content.decode()

    assert 'class="lang-menu"' in content
    # Bieżący (angielski) jest napisem, pozostałe dziesięć – przyciskami formularza.
    buttons = re.findall(r'<button type="submit" name="language" value="([^"]+)"', content)
    assert sorted(buttons) == sorted(code for code in ALL_LANGUAGES if code != "en")
    for code, label in settings.LANGUAGES:
        assert label in content, code
    assert 'lang="ar" dir="rtl"' in content


def test_a_language_outside_the_set_is_refused(client, competition, english_enabled_site):
    english_enabled_site(competition)

    response = client.post(
        "/account/preferences/", {"language": "ar", "high_contrast": "0", "next": "/login/"}
    )

    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value in {"pl", "en"}


def test_the_choice_is_saved_and_applied(client, international, participant):
    client.force_login(participant.user)

    client.post("/account/preferences/", {"language": "hi", "high_contrast": "0", "next": "/me/"})

    assert UserPreference.objects.get(user=participant.user).language == "hi"
    assert 'lang="hi"' in html_tag(client.get("/me/").content.decode())


def test_the_account_choice_beats_the_browser(client, international, participant):
    UserPreference.objects.create(user=participant.user, language="ru")
    client.force_login(participant.user)

    content = client.get("/me/", HTTP_ACCEPT_LANGUAGE="fr").content.decode()

    assert 'lang="ru"' in html_tag(content)


# --- (d) RTL ------------------------------------------------------------------------------------


def test_arabic_turns_the_document_right_to_left(client, international):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"

    tag = html_tag(client.get("/login/").content.decode())

    assert 'lang="ar"' in tag
    assert 'dir="rtl"' in tag


@pytest.mark.parametrize(
    ("code", "direction"), [("ar", "rtl"), ("pl", "ltr"), ("zh-hans", "ltr"), ("hi", "ltr")]
)
def test_text_direction(code, direction):
    assert text_direction(code) == direction


# --- (e) kluczowe strony w pismach spoza łaciny --------------------------------------------------


@pytest.mark.parametrize("code", ["ar", "zh-hans", "hi"])
@pytest.mark.parametrize("path", ["/", "/login/", "/register/", "/password-reset/"])
def test_public_pages_render_in_non_latin_scripts(client, international, code, path):
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = code

    response = client.get(path)

    assert response.status_code == 200, path
    assert f'lang="{code}"' in html_tag(response.content.decode())


@pytest.mark.parametrize("code", ["ar", "zh-hans", "hi"])
@pytest.mark.parametrize("path", ["/me/", "/me/messages/", "/account/profile/"])
def test_participant_pages_render_in_non_latin_scripts(
    client, international, participant, entry, problems, code, path
):
    UserPreference.objects.create(user=participant.user, language=code)
    client.force_login(participant.user)

    response = client.get(path, follow=True)

    assert response.status_code == 200, path
    assert f'lang="{code}"' in html_tag(response.content.decode())


def test_the_participant_panel_is_actually_translated(client, international, participant, entry, problems):
    """Nie sam atrybut ``lang``: napisy panelu przychodzą z katalogu tego języka."""
    UserPreference.objects.create(user=participant.user, language="es")
    client.force_login(participant.user)

    content = client.get("/me/").content.decode()

    assert "Twoje zgłoszenie" not in content
    assert "Your entry" not in content


# --- (f) listy ----------------------------------------------------------------------------------


def test_the_letter_is_in_the_recipients_language(international, participant):
    UserPreference.objects.create(user=participant.user, language="bn")

    with language_for(participant.user, international):
        assert translation.get_language() == "bn"


def test_the_letter_falls_back_to_the_competition_default(competition, participant):
    """Konkurs bez bengalskiego nie pisze po bengalsku – nawet do kogoś, kto go zapisał."""
    UserPreference.objects.create(user=participant.user, language="bn")
    competition.default_language = "pl"
    competition.interface_languages = ["pl", "en"]

    with language_for(participant.user, competition):
        assert translation.get_language() == "pl"


def test_an_account_without_a_choice_gets_the_competition_default(international, participant):
    with language_for(participant.user, international):
        assert translation.get_language() == "en"


# --- (g) liczby ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "separator"),
    [("pl", ","), ("en", "."), ("fr", ","), ("ru", ","), ("es", ","), ("hi", "."), ("zh-hans", ".")],
)
def test_decimal_separator_follows_the_language(code, separator):
    assert decimal_separator(code) == separator
    assert format_points("4.25", code) == f"4{separator}25"


def test_problem_title_uses_english_for_every_language_but_polish(problems):
    problem = problems[0]
    problem.title_en = "Inequality of means"

    with translation.override("ar"):
        assert problem.display_title == "Inequality of means"
    with translation.override("pl"):
        assert problem.display_title == problem.title


def test_competition_model_is_the_single_source(competition):
    """Przełącznik witryny nie jest już czytany ani pokazywany – kolumna czeka na skasowanie.

    Kolumna zostaje jedno wydanie dłużej (wdrożenie: stare procesy ``worker``/``beat``), ale nie ma
    jej w panelu ``/cms/`` i jej wartość niczego nie zmienia.
    """
    from apps.cms.models import SiteSettings

    panel_fields = {
        getattr(child, "field_name", None)
        for panel in SiteSettings.panels
        for child in getattr(panel, "children", [panel])
    }
    assert "english_interface_enabled" not in panel_fields
    row = SiteSettings.for_site(competition.site)
    row.english_interface_enabled = True
    row.save()
    assert competition.ui_languages == ("pl",)


def test_the_request_does_not_leave_its_language_active(client, international):
    """Po odpowiedzi wątek wraca do języka instalacji (``PreferencesMiddleware``, gthread)."""
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"

    client.get("/login/")

    assert translation.get_language() == settings.LANGUAGE_CODE


# --- (h) marka konkursu w listach (poprawka po przeglądzie) --------------------------------------


def test_letters_keep_the_polish_brand_without_the_branding_flag(competition):
    from apps.accounts.activation import activation_message

    with translation.override("pl"):
        body = activation_message("https://example.test/a/", competition)

    assert "założył konto w serwisie Olimpiady Kwantowej." in body


def test_letters_carry_the_competition_name_with_the_branding_flag(competition):
    from apps.accounts.activation import activation_message
    from apps.submissions.notifications import appeal_decided_message  # noqa: F401 - import kontroli

    competition.name = "International Quantum Olympiad"
    competition.short_name = ""
    competition.feature_flags = {**(competition.feature_flags or {}), "competition_branding_in_mail": True}

    with translation.override("en"):
        body = activation_message("https://example.test/a/", competition)

    assert "on the International Quantum Olympiad website" in body
    assert "Quantum Olympiad website" in body and "Olimpiad" not in body
