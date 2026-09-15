"""Rama serwisu po uwagach organizatora: pasek konta, pasek cookie, blok zgód, teksty.

Każdy test pilnuje jednej rzeczy, którą łatwo zepsuć przy kolejnej zmianie szablonu bazowego:
kolejności przycisków konta, obecności informacji o cookie (razem z nonce – bez niego CSP zablokuje
skrypt), układu bloku zgód i dwóch tekstów, które organizator zgłosił jako niezrozumiałe.
"""

import re

import pytest
from django.urls import reverse

from apps.cms.models import ResultsPage

from .conftest import close_submissions

pytestmark = pytest.mark.django_db


def flat(response) -> str:
    """Treść odpowiedzi ze zwiniętymi białymi znakami – łamanie wierszy w HTML nie jest treścią."""
    return re.sub(r"\s+", " ", response.content.decode())


def test_account_bar_stands_above_the_service_navigation(web_client, edition):
    content = web_client.get("/").content.decode()

    account = content.index('aria-label="Konto"')
    service = content.index('aria-label="Serwis"')

    # Pasek konta jest osobnym wierszem **nad** nawigacją serwisu, a nie jej ostatnią pozycją.
    assert content.index('class="topbar__account"') < account < service
    assert 'class="nav nav--roles"' not in content


def test_account_bar_of_anonymous_visitor_has_three_buttons_in_a_fixed_order(web_client, edition):
    bar = account_bar(web_client.get("/"))

    login = bar.index(f'class="btn btn--small btn--secondary account-bar__btn" href="{reverse("web:login")}"')
    register = bar.index(
        f'class="btn btn--small btn--accent account-bar__btn" href="{reverse("web:register")}"'
    )
    code = bar.index(
        f'class="account-bar__link account-bar__link--sub" href="{reverse("web:register-committee")}"'
    )

    # Kolejność wynika z tego, kto czego szuka: wejście dla osób z kontem, główne zaproszenie,
    # a na końcu – jako zwykły odnośnik – droga dla zaproszonych z kodem.
    assert login < register < code


def test_account_bar_marks_the_current_page(web_client, edition):
    text = flat(web_client.get(reverse("web:login")))

    assert f'href="{reverse("web:login")}" aria-current="page"' in text


def account_bar(response) -> str:
    """Sam pasek konta – asercje nie mogą zależeć od treści redakcyjnej strony głównej."""
    text = flat(response)
    start = text.index('aria-label="Konto"')
    return text[start : text.index('aria-label="Serwis"', start)]


def test_account_bar_of_logged_in_participant_has_panel_email_and_logout(web_client, participant):
    web_client.force_login(participant.user)
    bar = account_bar(web_client.get("/"))

    assert bar.index(f'class="account-bar__link" href="{reverse("web:me")}"') < bar.index(
        'class="account-bar__who'
    )
    assert participant.user.email in bar
    assert f'action="{reverse("web:logout")}" class="inline account-bar__logout"' in bar
    assert 'class="btn btn--small btn--secondary account-bar__btn">Wyloguj</button>' in bar
    # Osoba zalogowana nie widzi w pasku zaproszeń do rejestracji.
    assert reverse("web:register") not in bar
    assert "Rejestracja z kodem" not in bar


def test_cookie_notice_is_informational_and_dismissible(web_client, edition):
    """Bez identyfikatora GA4 pasek zostaje informacją – tak, jak przed dodaniem analityki."""
    response = web_client.get("/")
    content = response.content.decode()
    text = flat(response)

    assert "data-cookie-notice" in content
    assert (
        "Ten serwis używa wyłącznie niezbędnych plików cookie (sesja logowania, "
        "zabezpieczenie formularzy). Nie używamy cookies reklamowych ani analitycznych." in text
    )
    assert "data-cookie-notice-dismiss>Rozumiem</button>" in text
    # Pasek informuje, nie pyta o zgodę – nie ma w nim „odrzuć” ani żadnego przełącznika.
    assert "Odrzuć" not in text


def test_cookie_notice_script_is_external_and_carries_the_nonce(web_client, edition):
    response = web_client.get("/")
    content = response.content.decode()

    match = re.search(r'<script nonce="([^"]+)" src="([^"]*consent[^"]*\.js)">', content)

    assert match is not None, "brak skryptu paska cookie z nonce"
    assert f"'nonce-{match.group(1)}'" in response.headers["Content-Security-Policy"]


def test_cookie_policy_is_linked_from_the_bar_and_from_the_footer(web_client, edition):
    content = web_client.get("/").content.decode()

    assert 'class="footer__links"' in content
    assert content.count('href="/dokumenty/cookies/"') == 2
    assert "Polityka plików cookie" in content


def test_results_page_does_not_leak_a_template_comment(web_client):
    page = ResultsPage.objects.get()

    content = web_client.get(page.url).content.decode()

    # ``{# … #}`` jest w Django jednowierszowe: wielowierszowy komentarz renderował się jako tekst.
    assert "Starsze edycje zostają" not in content
    assert "apps/cms/models.py" not in content


def test_dashboard_explains_that_further_stages_have_no_sign_up(web_client, participant, elim_stage):
    # Etap po terminie i bez wpisu uczestnika – dokładnie ta gałąź, w której stało zdanie o
    # „etapie okręgowym i finale”.
    close_submissions(elim_stage)
    web_client.force_login(participant.user)

    text = flat(web_client.get("/me/"))

    assert "tworzy kwalifikacja" not in text
    assert "etapu okręgowego" not in text
    assert (
        "Do tego etapu nie ma zapisów – przechodzą do niego osoby zakwalifikowane "
        "w poprzednim etapie." in text
    )


def test_consents_fieldset_has_one_row_per_consent(web_client, edition):
    content = web_client.get(reverse("web:register")).content.decode()

    assert 'class="consents"' in content
    # Cztery zgody = cztery wiersze; każdy ma własne pole wyboru i własną etykietę.
    assert content.count('class="consents__item"') == 4
    assert content.count('type="checkbox"') >= 4
    # Wymagane bezwarunkowo są dwie (regulamin, RODO); pozostałe mówią o sobie podpowiedzią.
    assert content.count('class="consents__required"') == 2
    assert "wymagane dla osób niepełnoletnich" in content
    assert "dobrowolne" in content
