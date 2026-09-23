"""Liczenie pobrań: kto się nie liczy, jak powstaje pseudonim IP i jak liczą się „unikalne IP”.

Trzy grupy reguł, każda z powodem w ``apps.promo.tracking`` / ``apps.promo.stats``:

- **nie liczymy** robotów, żądań bez nagłówka przeglądarki, ``HEAD`` i koordynatora konkursu,
- **pseudonim** to HMAC adresu IP z kluczem z ``SECRET_KEY``: ten sam adres daje ten sam skrót,
  inny klucz – inny, a zwykły SHA-256 adresu (łamany słownikiem) nie jest tym, co zapisujemy;
  adres bierzemy wyłącznie z połączenia albo od zaufanego proxy,
- **unikalne IP** są unikalne w całym oknie (7 dni, 30 dni, od początku), a w wierszu sumy –
  także między plakatami; wyzerowany pseudonim nie jest „jednym wspólnym adresem”.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, UserFactory
from apps.promo import stats, tasks, tracking
from apps.promo.models import IP_HASH_RETENTION_MONTHS, PromoDownload
from apps.promo.tests.helpers import make_download, make_material
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)


def request_from(ip: str = "203.0.113.7", *, user_agent: str = BROWSER, method: str = "GET", **meta):
    from django.contrib.auth.models import AnonymousUser

    factory = RequestFactory()
    request = factory.generic(
        method, "/plakaty/1/pobierz/", REMOTE_ADDR=ip, HTTP_USER_AGENT=user_agent, **meta
    )
    request.user = AnonymousUser()
    return request


# --- kto się nie liczy ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_agent",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "WhatsApp/2.23.20.0",
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 BingPreview/1.0b",
        "TelegramBot (like TwitterBot)",
        "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
        "curl/8.5.0",
        "Wget/1.21.4",
        "python-requests/2.32.3",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/140.0 Safari/537.36",
        "AhrefsBot/7.0; +http://ahrefs.com/robot/",
        "Mozilla/5.0 (compatible; YandexBot/3.0)",
        "",
        "   ",
    ],
)
def test_robots_and_tools_are_not_counted(user_agent):
    assert tracking.is_bot(user_agent) is True


@pytest.mark.parametrize(
    "user_agent",
    [
        BROWSER,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        "Mozilla/5.0 (Windows NT 10.0; rv:131.0) Gecko/20100101 Firefox/131.0",
        # Telefon marki Cubot – „CUBOT” w nagłówku nie jest robotem.
        "Mozilla/5.0 (Linux; Android 12; CUBOT P80) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36",
    ],
)
def test_browsers_are_counted(user_agent):
    assert tracking.is_bot(user_agent) is False


def test_head_is_not_recorded(competition):
    material = make_material(competition)

    assert tracking.record_download(request_from(method="HEAD"), material) is None
    assert PromoDownload.objects.count() == 0


def test_coordinator_of_the_competition_is_not_recorded(competition):
    material = make_material(competition)
    coordinator = CoordinatorFactory()
    grant_membership(coordinator, competition, CompetitionRole.COORDINATOR)
    request = request_from()
    request.user = coordinator

    assert tracking.record_download(request, material) is None


def test_logged_in_teacher_is_recorded(competition):
    material = make_material(competition)
    request = request_from()
    request.user = UserFactory()

    assert tracking.record_download(request, material) is not None


# --- pseudonim adresu IP ---------------------------------------------------------------------------


def test_the_event_stores_a_keyed_hash_never_the_address(competition):
    material = make_material(competition)

    event = tracking.record_download(request_from("203.0.113.7"), material)

    assert event.ip_hash == tracking.ip_hash("203.0.113.7")
    assert len(event.ip_hash) == 64
    assert "203.0.113.7" not in event.ip_hash
    # Nie zwykły SHA-256 adresu – ten dałoby się odwrócić słownikiem wszystkich adresów IPv4.
    assert event.ip_hash != hashlib.sha256(b"203.0.113.7").hexdigest()
    fields = {field.name for field in PromoDownload._meta.get_fields()}
    assert not fields & {"ip", "ip_address", "user_agent", "user"}


def test_same_address_same_hash_regardless_of_browser(competition):
    material = make_material(competition)

    first = tracking.record_download(request_from("198.51.100.1", user_agent=BROWSER), material)
    second = tracking.record_download(request_from("198.51.100.1", user_agent=BROWSER + " Edg/140"), material)

    assert first.ip_hash == second.ip_hash


def test_the_key_comes_from_secret_key(settings):
    before = tracking.ip_hash("198.51.100.1")
    settings.SECRET_KEY = django_settings.SECRET_KEY + "-inny"

    assert tracking.ip_hash("198.51.100.1") != before


def test_client_supplied_forwarding_headers_are_ignored(competition, settings):
    """Adres z nagłówka liczy się wyłącznie od zaufanego proxy – inaczej skrypt „miałby” milion IP."""
    settings.TRUSTED_PROXY_IPS = []
    material = make_material(competition)

    forged = tracking.record_download(
        request_from("198.51.100.1", HTTP_X_FORWARDED_FOR="1.2.3.4", HTTP_X_REAL_IP="5.6.7.8"), material
    )

    assert forged.ip_hash == tracking.ip_hash("198.51.100.1")


def test_real_ip_from_the_trusted_proxy_is_used(competition, settings):
    settings.TRUSTED_PROXY_IPS = ["172.30.1.0/24"]
    material = make_material(competition)

    event = tracking.record_download(request_from("172.30.1.5", HTTP_X_REAL_IP="203.0.113.99"), material)

    assert event.ip_hash == tracking.ip_hash("203.0.113.99")


# --- unikalne IP w oknach i w sumie ---------------------------------------------------------------


def test_unique_ip_is_unique_across_the_whole_window_not_per_day(competition):
    material = make_material(competition)
    now = timezone.now()
    for days_ago in (0, 1, 2, 3):
        make_download(material, ip_hash="a" * 64, at=now - timedelta(days=days_ago))
    make_download(material, ip_hash="b" * 64, at=now - timedelta(days=20))
    make_download(material, ip_hash="c" * 64, at=now - timedelta(days=100))

    [row] = stats.material_stats(competition)

    assert (row.counts.total_7, row.counts.unique_7) == (4, 1)
    assert (row.counts.total_30, row.counts.unique_30) == (5, 2)
    assert (row.counts.total, row.counts.unique) == (6, 3)


def test_totals_count_an_address_once_across_materials(competition):
    """Szkoła, która pobrała dwa plakaty z jednego adresu, to jeden unikalny adres, a nie dwa."""
    first = make_material(competition, title="A")
    second = make_material(competition, title="B")
    make_download(first, ip_hash="a" * 64)
    make_download(second, ip_hash="a" * 64)
    make_download(second, ip_hash="b" * 64)

    rows = stats.material_stats(competition)
    total = stats.totals(competition)

    assert sum(row.counts.unique for row in rows) == 3
    assert (total.total, total.unique) == (3, 2)
    assert (total.total_7, total.unique_7) == (3, 2)


def test_cleared_hashes_count_as_downloads_but_not_as_one_shared_address(competition):
    material = make_material(competition)
    make_download(material, ip_hash=None)
    make_download(material, ip_hash=None)
    make_download(material, ip_hash="a" * 64)

    total = stats.totals(competition)

    assert (total.total, total.unique) == (3, 1)


def test_daily_series_has_both_numbers_and_zero_days(competition):
    material = make_material(competition)
    today = timezone.localdate()
    make_download(material, ip_hash="a" * 64)
    make_download(material, ip_hash="a" * 64)
    make_download(material, ip_hash="b" * 64)

    series = stats.daily_series(competition, today=today)

    assert len(series) == stats.CHART_DAYS
    assert series[0]["day"] == today
    assert (series[0]["count"], series[0]["unique"]) == (3, 2)
    assert series[0]["count_width"] == "w100"
    assert series[1]["count"] == 0
    assert series[1]["count_width"] == "w0"


def test_stats_of_another_competition_are_invisible(competition, other_competition):
    mine = make_material(competition)
    theirs = make_material(other_competition)
    make_download(mine)
    make_download(theirs)
    make_download(theirs)

    assert stats.totals(competition).total == 1
    assert [row.material for row in stats.material_stats(competition)] == [mine]


# --- retencja pseudonimu -----------------------------------------------------------------------------


def test_retention_clears_hashes_older_than_twelve_months_and_keeps_the_events(competition):
    material = make_material(competition)
    now = timezone.now()
    old = make_download(material, ip_hash="a" * 64, at=now - timedelta(days=400))
    fresh = make_download(material, ip_hash="b" * 64, at=now - timedelta(days=300))

    assert tasks.clear_expired_ip_hashes(now) == 1

    old.refresh_from_db()
    fresh.refresh_from_db()
    assert old.ip_hash is None
    assert fresh.ip_hash == "b" * 64
    assert PromoDownload.objects.count() == 2
    assert IP_HASH_RETENTION_MONTHS == 12


def test_beat_runs_the_retention_daily(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["promo-clear-expired-ip-hashes"]

    assert entry["task"] == "apps.promo.tasks.clear_expired_ip_hashes"
    assert entry["schedule"] == 86400.0
    assert tasks.clear_expired_ip_hashes_task.name == entry["task"]


def test_processing_register_describes_the_pseudonym_and_its_retention():
    from apps.accounts.processing_register import ACTIVITIES

    entry = next(item for item in ACTIVITIES if item.key == "plakaty")

    assert "lit. f" in entry.legal_basis
    assert any("HMAC" in category for category in entry.categories)
    assert "12 miesięcy" in entry.retention
