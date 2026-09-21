"""Rama serwisu po uwagach organizatora: pasek konta, pasek cookie, blok zgód, teksty.

Każdy test pilnuje jednej rzeczy, którą łatwo zepsuć przy kolejnej zmianie szablonu bazowego:
kolejności przycisków konta, obecności informacji o cookie (razem z nonce – bez niego CSP zablokuje
skrypt), układu bloku zgód i dwóch tekstów, które organizator zgłosił jako niezrozumiałe.
"""

import re
from pathlib import Path

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


def test_account_bar_of_anonymous_visitor_has_two_buttons_in_a_fixed_order(web_client, edition):
    bar = account_bar(web_client.get("/"))

    login = bar.index(f'class="btn btn--small btn--secondary account-bar__btn" href="{reverse("web:login")}"')
    register = bar.index(
        f'class="btn btn--small btn--accent account-bar__btn" href="{reverse("web:register")}"'
    )

    # Kolejność wynika z tego, kto czego szuka: wejście dla osób z kontem, potem zaproszenie.
    assert login < register
    # Trzecia pozycja („Rejestracja z kodem”) zeszła stąd do stopki – patrz test niżej.
    assert reverse("web:register-committee") not in bar


def test_registration_with_a_code_has_no_link_in_the_chrome_but_the_address_still_works(web_client, edition):
    """Uwaga organizatora z 21.09: odnośnik zniknął też ze stopki, gdzie stanął po uwadze z 16.09.

    Wtedy organizator poprosił o przeniesienie go z paska konta do stopki – kod zaproszenia
    dostaje kilkanaście osób na edycję (komitet, recenzenci, jury), a stopkę ogląda każdy
    odwiedzający, tak samo jak pasek. Teraz obie belki mają zostać krótkie i bez odnośników
    rzadkich: droga zostaje żywa (zaproszenie prowadzi tam wprost z listu), znika tylko z ramy
    serwisu w całości. Test pilnuje **braku miejsca w chrome**, a nie istnienia drogi.
    """
    response = web_client.get("/")
    url = reverse("web:register-committee")

    assert url not in account_bar(response)
    assert url not in footer(response)
    assert web_client.get(url).status_code == 200


def test_the_registration_with_a_code_link_stays_out_of_the_footer_for_a_logged_in_user_too(
    web_client, participant, edition
):
    """Kto ma konto uczestnika, a dostał kod do komitetu, nie widzi go w stopce – tak samo jak gość.

    Adres zostaje żywy niezależnie od tego, kto go szuka (test wyżej pilnuje samego braku
    w chrome, ten – że decyzja nie zależy od stanu logowania).
    """
    web_client.force_login(participant.user)
    url = reverse("web:register-committee")

    assert url not in footer(web_client.get("/"))
    assert web_client.get(url).status_code == 200


def test_account_bar_marks_the_current_page(web_client, edition):
    text = flat(web_client.get(reverse("web:login")))

    assert f'href="{reverse("web:login")}" aria-current="page"' in text


def test_support_link_is_in_the_account_bar_for_an_anonymous_visitor_too(web_client, edition):
    """Uwaga organizatora z 21.09.2026: „Zgłoś problem” ma stać w pasku konta także dla gościa.

    Wcześniej pasek prowadził do zgłoszenia tylko osobom zalogowanym; gość, któremu coś nie
    zadziałało zanim założył konto, miał drogę do organizatora wyłącznie w stopce. Oba miejsca
    (pasek i stopka) rysuje ten sam fragment (``web/_support_link.html``) i prowadzą pod ten sam
    adres – formularz, nie listę zgłoszeń (patrz ``apps/support/tests/test_support.py``).
    """
    url = reverse("web:support-new")
    response = web_client.get("/")

    assert f'href="{url}"' in account_bar(response)
    assert "Zgłoś problem" in account_bar(response)
    assert f'href="{url}"' in footer(response)
    assert flat(response).count(f'href="{url}"') == 2


def account_bar(response) -> str:
    """Sam pasek konta – asercje nie mogą zależeć od treści redakcyjnej strony głównej."""
    text = flat(response)
    start = text.index('aria-label="Konto"')
    return text[start : text.index('aria-label="Serwis"', start)]


def footer(response) -> str:
    """Sama stopka – z tego samego powodu, co wyżej."""
    text = flat(response)
    return text[text.index('<footer class="footer">') :]


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


def test_the_phone_input_is_as_wide_as_the_other_text_fields(web_client, edition):
    """Uwaga organizatora z 16.09: „okno do wprowadzenia nr telefonu ma wyraźnie mniejszy rozmiar”.

    Dwie asercje, bo przyczyna mogła być w dwóch miejscach. W znaczniku jej nie ma – pole nie
    nosi atrybutu ``size``, który liczyłby szerokość w znakach bieżącego kroju. Była w arkuszu:
    wspólna reguła pól tekstowych nie wymieniała ``input[type="tel"]``, więc kontrolka zostawała
    przy domyślnych dwudziestu znakach przeglądarki, obok pól rozciągniętych na 100 %.
    """
    body = web_client.get(reverse("web:register")).content.decode()

    phone = re.search(r"<input[^>]*name=\"phone\"[^>]*>", body)
    assert phone is not None, "brak pola telefonu w formularzu rejestracji"
    assert 'type="tel"' in phone.group(0)
    assert "size=" not in phone.group(0)

    css = (Path(__file__).resolve().parents[3] / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert 'input[type="tel"],\nselect,\ntextarea {\n  width: 100%;' in css
