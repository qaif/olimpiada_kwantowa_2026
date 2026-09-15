"""Google Analytics 4: nagłówek CSP i szablon bazowy – z identyfikatorem i bez niego.

Cała funkcja ma jeden przełącznik: ``cms.SiteSettings.ga_measurement_id``. Dlatego każdy test
tutaj jest parą „bez identyfikatora / z identyfikatorem”, a najważniejszy z nich pilnuje tego,
czego przy dokładaniu zewnętrznej usługi najłatwiej nie zauważyć: **serwis, w którym nikt nie
włączył analityki, ma zachowywać się co do bajtu tak, jak przed jej dodaniem** – ta sama polityka
bezpieczeństwa, ten sam pasek cookie, ani jednego hosta Google'a w dokumencie.

JavaScriptu (moment wczytania ``gtag/js``, zapis zgody, kasowanie ``_ga``) te testy nie ruszają –
robi to kontrola przeglądarkowa ``e2e/check_consent.py``.
"""

import re

import pytest

from apps.cms import analytics
from apps.cms.models import SiteSettings
from apps.web.middleware import build_policy

pytestmark = pytest.mark.django_db

MEASUREMENT_ID = "G-TESTTEST1"

#: Hosty, które w polityce pojawiają się wyłącznie razem z identyfikatorem.
GA_SCRIPT_HOST = "https://www.googletagmanager.com"
GA_COLLECT_HOSTS = ("https://*.google-analytics.com", "https://*.analytics.google.com")


@pytest.fixture(autouse=True)
def clean_analytics_cache():
    """Odpowiedź „czy analityka jest włączona” żyje w pamięci procesu – patrz apps/cms/analytics.py."""
    analytics.reset_cache()
    yield
    analytics.reset_cache()


@pytest.fixture
def analytics_on():
    """Identyfikator wpisany w ``/cms/``. ``save()`` (a nie ``update()``) – tak robi to redaktor."""
    row = SiteSettings.objects.get()
    row.ga_measurement_id = MEASUREMENT_ID
    row.save()
    return row


def directive(policy: str, name: str) -> str:
    return next(part for part in policy.split("; ") if part.startswith(f"{name} "))


def nonce_of(policy: str) -> str:
    match = re.search(r"'nonce-([^']+)'", policy)
    assert match is not None, "polityka bez nonce'a"
    return match.group(1)


# --- nagłówek CSP -------------------------------------------------------------------------------


def test_without_a_measurement_id_the_policy_is_the_one_from_before_analytics(web_client, edition):
    """Serwis bez analityki ma nagłówek dokładnie taki, jak przed dodaniem GA – co do bajtu."""
    policy = web_client.get("/").headers["Content-Security-Policy"]

    assert policy == build_policy(nonce_of(policy))
    assert "google" not in policy


def test_with_a_measurement_id_the_policy_allows_the_three_kinds_of_google_traffic(
    web_client, edition, analytics_on
):
    policy = web_client.get("/").headers["Content-Security-Policy"]

    # Skrypt: fallback dla przeglądarek bez 'strict-dynamic' – i dlatego musi stać **przed** nim.
    script_src = directive(policy, "script-src")
    assert GA_SCRIPT_HOST in script_src
    assert script_src.index(GA_SCRIPT_HOST) < script_src.index("'strict-dynamic'")
    # Wysyłka zdarzeń (fetch/sendBeacon) i konfiguracja strumienia.
    connect_src = directive(policy, "connect-src")
    for host in (*GA_COLLECT_HOSTS, GA_SCRIPT_HOST):
        assert host in connect_src
    # Starszy wariant wysyłki: ``collect`` jako obrazek 1×1.
    img_src = directive(policy, "img-src")
    assert "https://*.google-analytics.com" in img_src
    assert GA_SCRIPT_HOST in img_src


def test_analytics_does_not_leak_into_the_other_directives(web_client, edition, analytics_on):
    """Pliki wideo, ramki i style nie mają z analityką nic wspólnego – i nie mogą jej dostać."""
    policy = web_client.get("/").headers["Content-Security-Policy"]

    for name in ("media-src", "frame-src", "style-src", "font-src", "form-action"):
        assert "google" not in directive(policy, name)


def test_admin_policy_never_mentions_analytics(web_client, edition, analytics_on):
    """Panel ma własną, luźniejszą politykę – ale statystyk odwiedzin redakcji nie zbieramy.

    Odpowiedzią jest przekierowanie na logowanie; nagłówek i tak powstaje, bo o wyborze polityki
    decyduje ścieżka, a nie to, czy ktoś się zalogował.
    """
    policy = web_client.get("/cms/").headers["Content-Security-Policy"]

    assert "google" not in policy


# --- szablon bazowy ------------------------------------------------------------------------------


def test_without_a_measurement_id_the_page_has_no_trace_of_analytics(web_client, edition):
    content = web_client.get("/").content.decode()

    assert "ga-measurement-id" not in content
    assert "js/analytics.js" not in content
    assert "data-cookie-consent" not in content
    assert "Ustawienia cookies" not in content
    # Pasek zostaje informacją z jednym przyciskiem.
    assert "data-cookie-notice-dismiss" in content


def test_with_a_measurement_id_the_page_carries_the_id_and_the_loader(web_client, edition, analytics_on):
    response = web_client.get("/")
    content = response.content.decode()

    assert f'<meta name="ga-measurement-id" content="{MEASUREMENT_ID}">' in content
    match = re.search(r'<script defer nonce="([^"]+)" src="([^"]*analytics[^"]*\.js)">', content)
    assert match is not None, "brak loadera analityki z nonce"
    # Nonce z tej samej odpowiedzi – jest jednorazowy, więc drugie żądanie miałoby już inny.
    assert f"'nonce-{match.group(1)}'" in response.headers["Content-Security-Policy"]
    # Identyfikator jest w dokumencie, ale samego skryptu Google'a w nim nie ma: wstrzykuje go
    # loader dopiero po zgodzie. Gdyby adres wyciekł do szablonu, zgoda przestałaby cokolwiek znaczyć.
    assert "googletagmanager.com" not in content


def test_with_a_measurement_id_the_bar_asks_instead_of_informing(web_client, edition, analytics_on):
    content = web_client.get("/").content.decode()
    text = re.sub(r"\s+", " ", content)

    assert (
        "Używamy plików cookie niezbędnych do działania serwisu oraz – za Twoją zgodą – "
        "Google Analytics do statystyk odwiedzin." in text
    )
    assert 'data-cookie-consent="all">Akceptuję wszystkie</button>' in text
    assert 'data-cookie-consent="necessary">Tylko niezbędne</button>' in text
    # Stary, informacyjny wariant znika w całości – inaczej pasek mówiłby dwie różne rzeczy naraz.
    assert "data-cookie-notice-dismiss" not in content
    assert "Nie używamy cookies reklamowych ani analitycznych" not in text
    # Odmowa nie może być trudniejsza od zgody: oba przyciski są tej samej wielkości.
    assert text.count("btn btn--small btn--accent cookie-notice__ok") == 1
    assert text.count("btn btn--small btn--secondary cookie-notice__ok") == 1


def test_footer_offers_to_reopen_the_bar_only_when_there_is_consent_to_withdraw(
    web_client, edition, analytics_on
):
    content = web_client.get("/").content.decode()

    assert "data-cookie-settings>Ustawienia cookies</button>" in content
    # Odnośnik stoi w tym samym rzędzie, co polityki – tam go czytelnik szuka.
    footer = content[content.index('class="footer__links"') :]
    assert footer.index("Ustawienia cookies") < footer.index("</p>")


def test_cookie_policy_is_still_linked_from_the_bar_and_from_the_footer(web_client, edition, analytics_on):
    """Pasek zgody nie może zgubić odnośnika do polityki – to on tłumaczy, na co jest zgoda."""
    content = web_client.get("/").content.decode()

    assert content.count('href="/dokumenty/cookies/"') == 2
