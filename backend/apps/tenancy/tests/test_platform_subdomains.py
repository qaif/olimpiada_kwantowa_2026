"""Konkursy pod ``*.SITE_DOMAIN``: host żądania, pytanie Caddy'ego o certyfikat i ``check_domains``.

Trzy rzeczy, o które chodzi, i wszystkie trzy są odwracalne jednym ustawieniem
(``PLATFORM_SUBDOMAINS``, domyślnie wyłączone):

1. **Wildcard nie może znaczyć „Konkurs #1 pod każdym adresem”.** Wpuszczenie całej domeny do
   ``ALLOWED_HOSTS`` sprawia, że dowolny host pod nią przechodzi walidację Django, a
   ``Site.find_for_request`` przy braku trafienia oddaje witrynę **domyślną**. Bez reguły z
   ``platform_subdomain_miss`` ``cokolwiek.olimpiadakwantowa.pl`` serwowałoby Olimpiadę Kwantową.
   Przy wyłączonym przełączniku hosty zachowują się dokładnie jak dotąd — i to jest osobny test,
   bo to jest warunek wdrożenia.
2. **Pytanie o certyfikat musi być wąskie i niewidoczne z zewnątrz.** ``/internal/tls-allowed``
   odpowiada 200 wyłącznie na komplet warunków; każdy brak to 404, łącznie z żądaniem przyszłym
   pod domeną publiczną. Matryca niżej wypisuje je po kolei, bo pominięcie któregokolwiek daje
   otwarty generator certyfikatów, a to widać dopiero po wyczerpaniu limitu Let's Encrypt.
3. **``check_domains`` nie może alarmować o konkursach, których nikt nie wpisze do ``.env``.**
   Konkurs w subdomenie zakłada koordynator z panelu, więc żądanie wpisu w ``EXTRA_DOMAINS``
   świeciłoby się na czerwono od chwili jego powstania i nauczyłoby czytać tę listę bez uwagi.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client

from apps.tenancy.models import Competition

from .conftest import make_competition, make_site

pytestmark = pytest.mark.django_db

#: Domena platformy w testach. Sufiks ``.test`` jest zarezerwowany normą (RFC 6761) i dopisany do
#: ``ALLOWED_HOSTS`` przez autouse'ową fiksturę pakietu, więc żądanie pod **dowolną** jego
#: subdomenę dochodzi do naszej warstwy zamiast kończyć się kodem 400 Django.
PLATFORM = "platforma.test"

#: Adres, który nie zależy od drzewa stron ani od zalogowania — chodzi o to, co robi **warstwa**,
#: a nie o to, co wyrenderuje widok.
PROBE_URL = "/healthz/"

ASK_URL = "/internal/tls-allowed"


@pytest.fixture
def platform(settings):
    """Instalacja z włączonymi subdomenami platformy, tak jak ustawia ją operator w ``.env``."""
    settings.SITE_DOMAIN = PLATFORM
    settings.PLATFORM_SUBDOMAINS = True
    settings.EXTRA_DOMAINS = []
    return PLATFORM


def probe(host: str) -> int:
    """Kod odpowiedzi na żądanie spod wskazanego hosta."""
    return Client(HTTP_HOST=host, SERVER_NAME=host).get(PROBE_URL).status_code


def ask(domain: str, *, host: str = "web:8000") -> int:
    return (
        Client(HTTP_HOST=host, SERVER_NAME=host.partition(":")[0])
        .get(ASK_URL, {"domain": domain})
        .status_code
    )


# =================================================================================================
# Host żądania
# =================================================================================================


def test_without_the_switch_an_unknown_subdomain_behaves_exactly_as_before(competition, settings):  # noqa: ARG001
    """Przełącznik wyłączony = dzisiejsze zachowanie: nieznany host schodzi na witrynę domyślną."""
    settings.SITE_DOMAIN = PLATFORM

    assert probe(f"nieznana.{PLATFORM}") == 200
    assert probe(PLATFORM) == 200


def test_an_unknown_platform_subdomain_is_a_plain_404(competition, platform):  # noqa: ARG001
    """Subdomena bez konkursu nie może oddać treści Konkursu #1 — 404 zamiast cudzej strony."""
    assert probe(f"nieznana.{platform}") == 404


def test_a_subdomain_with_an_active_competition_answers(platform):
    host = f"fizyczna.{platform}"
    make_competition(host, "fizyczna")

    assert probe(host) == 200


def test_a_subdomain_of_an_inactive_competition_is_a_404(platform):
    """Wyłączony konkurs znaczy „nie ma go”, a nie „pokaż zamiast niego witrynę domyślną”."""
    host = f"fizyczna.{platform}"
    make_competition(host, "fizyczna", is_active=False)

    assert probe(host) == 404


def test_a_site_without_a_competition_under_the_platform_domain_is_a_404(platform):
    """Sama witryna Wagtaila nie wystarcza: pod adresem konkursu ma stać **konkurs**."""
    host = f"osierocona.{platform}"
    make_site(host, own_root=True)

    assert probe(host) == 404


def test_the_platform_domain_itself_is_not_a_subdomain(competition, platform):  # noqa: ARG001
    """Reguła dotyczy subdomen; sama domena platformy nie może wpaść we własną bramkę."""
    assert probe(platform) == 200


def test_a_host_written_out_in_the_configuration_keeps_working(competition, platform, settings):  # noqa: ARG001
    """Hosty wypisane z nazwy mają starszeństwo nad wildcardem — inaczej włączenie ich zgasi."""
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, f"panel.{platform}"]
    settings.EXTRA_DOMAINS = [f"stary.{platform}"]

    assert probe(f"www.{platform}") == 200
    assert probe(f"panel.{platform}") == 200
    assert probe(f"stary.{platform}") == 200


def test_a_host_outside_the_platform_domain_is_untouched(competition, platform):  # noqa: ARG001
    """Konkurs pod własną domeną nie ma z tą regułą nic wspólnego."""
    assert probe(competition.primary_domain) == 200


def test_a_nested_label_under_the_platform_domain_is_a_404(competition, platform):  # noqa: ARG001
    """``a.b.platforma`` nie ma konkursu i nie obejmuje go certyfikat wieloznaczny."""
    assert probe(f"en.fizyczna.{platform}") == 404


# =================================================================================================
# Pytanie Caddy'ego o certyfikat (``GET /internal/tls-allowed?domain=…``)
# =================================================================================================


@pytest.fixture
def subdomain_competition(platform):
    """Aktywny konkurs pod ``fizyczna.<SITE_DOMAIN>`` — bohater matrycy odpowiedzi."""
    return make_competition(f"fizyczna.{platform}", "fizyczna")


def test_the_internal_host_gets_200_for_an_active_subdomain_competition(subdomain_competition, platform):  # noqa: ARG001
    response = Client(HTTP_HOST="web:8000", SERVER_NAME="web").get(
        ASK_URL, {"domain": f"fizyczna.{platform}"}
    )

    assert response.status_code == 200
    # Caddy czyta wyłącznie kod odpowiedzi; treść byłaby jedyną rzeczą, którą dałoby się z tego
    # adresu wyciągnąć o cudzych konkursach.
    assert response.content == b""


@pytest.mark.parametrize("host", ["web", "web:8000", "localhost", "127.0.0.1"])
def test_every_internal_host_may_ask(subdomain_competition, platform, host):  # noqa: ARG001
    assert ask(f"fizyczna.{platform}", host=host) == 200


def test_a_public_host_may_not_ask(subdomain_competition, platform):
    """Z domeny publicznej ten adres wygląda tak, jakby go nie było — także spod własnego konkursu."""
    assert ask(f"fizyczna.{platform}", host=f"fizyczna.{platform}") == 404
    assert ask(f"fizyczna.{platform}", host=subdomain_competition.primary_domain) == 404
    assert ask(f"fizyczna.{platform}", host=platform) == 404


def test_an_unknown_domain_gets_404(subdomain_competition, platform):  # noqa: ARG001
    assert ask(f"nieznana.{platform}") == 404


def test_an_inactive_competition_gets_404(platform):
    make_competition(f"fizyczna.{platform}", "fizyczna", is_active=False)

    assert ask(f"fizyczna.{platform}") == 404


def test_a_nested_label_gets_404(platform):
    """Certyfikat wieloznaczny ``*.platforma`` nie pokrywa ``en.fizyczna.platforma``."""
    make_competition(f"en.fizyczna.{platform}", "fizyczna-en")

    assert ask(f"en.fizyczna.{platform}") == 404


def test_a_competition_under_its_own_domain_gets_404(competition, platform):  # noqa: ARG001
    """Domena spoza platformy ma certyfikat z bloku Caddy'ego, a nie z pytania na żądanie."""
    assert ask(competition.primary_domain) == 404


def test_the_platform_domain_itself_gets_404(competition, platform):  # noqa: ARG001
    assert ask(platform) == 404


def test_a_missing_or_empty_domain_gets_404(subdomain_competition):  # noqa: ARG001
    assert Client(HTTP_HOST="web:8000", SERVER_NAME="web").get(ASK_URL).status_code == 404
    assert ask("") == 404


def test_the_switch_off_closes_the_endpoint(settings, subdomain_competition, platform):  # noqa: ARG001
    """Instalacja bez subdomen nie wystawia certyfikatów na żądanie — także dla swojego konkursu."""
    settings.PLATFORM_SUBDOMAINS = False

    assert ask(f"fizyczna.{platform}") == 404


def test_an_alias_site_of_an_active_competition_is_allowed(subdomain_competition, platform):
    """Druga witryna tego samego konkursu (drugi język treści) też potrzebuje certyfikatu."""
    from wagtail.models import Locale

    from apps.tenancy.aliases import CompetitionSiteAlias

    site = make_site(f"en-fizyczna.{platform}", own_root=True)
    CompetitionSiteAlias.objects.create(
        competition=subdomain_competition, site=site, locale=Locale.objects.order_by("pk").first()
    )

    assert ask(f"en-fizyczna.{platform}") == 200


def test_the_answer_is_cached_for_a_minute(subdomain_competition, platform):
    """Bufor 60 s: Caddy pyta przy pierwszym uścisku dłoni z każdym hostem, a boty skanują domenę.

    Sprawdzamy **skutek**, a nie liczbę zapytań: odpowiedź ma przeżyć zmianę w bazie, bo dokładnie
    to znaczy „nie pytamy o nią drugi raz”. Licznik zapytań mierzyłby przy okazji warstwy, które
    z tym adresem nie mają nic wspólnego.
    """
    from django.core.cache import cache

    assert ask(f"fizyczna.{platform}") == 200

    Competition.objects.filter(pk=subdomain_competition.pk).update(is_active=False)
    assert ask(f"fizyczna.{platform}") == 200

    cache.clear()
    assert ask(f"fizyczna.{platform}") == 404


def test_the_endpoint_is_read_only_and_ignores_other_methods(subdomain_competition, platform):
    before = Competition.objects.count()
    client = Client(HTTP_HOST="web:8000", SERVER_NAME="web")

    assert client.post(ASK_URL, {"domain": f"fizyczna.{platform}"}).status_code == 405

    assert Competition.objects.count() == before


# =================================================================================================
# ``manage.py check_domains``
# =================================================================================================


def run_check(**kwargs) -> str:
    out = StringIO()
    call_command("check_domains", stdout=out, **kwargs)
    return out.getvalue()


@pytest.fixture
def domains_configured(platform, settings):
    """Instalacja skonfigurowana poprawnie: wildcard subdomen **i** domena Konkursu #1.

    Konkurs #1 stoi w bazie testowej pod ``localhost`` (tak zakłada go migracja ``tenancy.0002``),
    a ``SITE_DOMAIN`` przestawiliśmy na domenę platformy — bez tego wpisu byłby w wydruku
    rozjazdem, o który w tych testach nie chodzi.
    """
    settings.ALLOWED_HOSTS = ["localhost", f".{platform}"]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost", f"https://*.{platform}"]
    settings.EXTRA_DOMAINS = ["localhost"]
    return platform


def test_check_domains_treats_a_platform_subdomain_as_covered(domains_configured):
    """Wildcard obejmuje trzy konfiguracje naraz, więc brak wpisu w ``EXTRA_DOMAINS`` nie jest błędem."""
    make_competition(f"fizyczna.{domains_configured}", "fizyczna")

    output = run_check()

    assert "UWAGA" not in output
    assert "rozjazdów: 0" in output
    # I mówi o tym wprost – czytający ma wiedzieć, dlaczego brak wpisu przestał tu być błędem.
    assert "Subdomeny platformy: włączone" in output


def test_check_domains_names_the_reason_in_the_detailed_listing(domains_configured):
    make_competition(f"fizyczna.{domains_configured}", "fizyczna")

    assert "subdomena platformy" in run_check(all=True)


def test_check_domains_still_alarms_without_the_switch(settings):
    """Ten sam konkurs na instalacji bez subdomen jest rozjazdem i ma się o tym dowiedzieć."""
    settings.SITE_DOMAIN = PLATFORM
    settings.PLATFORM_SUBDOMAINS = False
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.CSRF_TRUSTED_ORIGINS = []
    settings.EXTRA_DOMAINS = []
    make_competition(f"fizyczna.{PLATFORM}", "fizyczna")

    output = run_check()

    assert "EXTRA_DOMAINS" in output
    assert "DJANGO_CSRF_TRUSTED_ORIGINS" in output


def test_check_domains_still_alarms_for_a_domain_of_its_own(domains_configured):  # noqa: ARG001
    """Subdomeny są wyjątkiem; konkurs pod własną domeną nadal wymaga wpisu w trzech miejscach."""
    make_competition("olimpiadafizyczna.invalid", "fizyczna")

    assert "EXTRA_DOMAINS" in run_check()
