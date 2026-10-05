"""Twarda blokada domen bez poczty (MAIL-02 § 1.2): MX, A, null MX, fail-open, cache, IDN, wyjątki."""

from __future__ import annotations

import pytest

from apps.email_delivery import dnscheck
from apps.mail_domains.tests.fakes import FakeResolver


@pytest.fixture
def dns_on(settings):
    settings.EMAIL_DOMAIN_DNS_CHECK = True


@pytest.fixture
def fake(monkeypatch):
    """Podstawia atrapę resolwera i oddaje ją testowi (lista ``queries`` – co było pytane)."""
    holder = {}

    def install(**tables):
        resolver = FakeResolver(**tables)
        holder["resolver"] = resolver
        monkeypatch.setattr(dnscheck, "resolver", lambda: resolver)
        return resolver

    return install


def test_domain_with_mx_accepts_mail(dns_on, fake):
    resolver = fake(mx={"szkola.edu.pl": [(10, "mx.szkola.edu.pl")]})
    assert dnscheck.domain_accepts_mail("szkola.edu.pl") is True
    # MX wystarczy – A nie jest pytany.
    assert resolver.queries == [("MX", "szkola.edu.pl")]


def test_domain_without_mx_but_with_a_accepts_mail(dns_on, fake):
    fake(a={"stara-szkola.pl": ["192.0.2.10"]})
    assert dnscheck.domain_accepts_mail("stara-szkola.pl") is True


def test_nxdomain_or_empty_answers_mean_no_mail(dns_on, fake):
    # Prawdziwy przypadek z logu relaya: ``o2.plo`` nie istnieje.
    fake()
    assert dnscheck.domain_accepts_mail("o2.plo") is False


def test_null_mx_means_no_mail(dns_on, fake):
    fake(mx={"nomail.example.org.pl": [(0, "")]}, a={"nomail.example.org.pl": ["192.0.2.1"]})
    assert dnscheck.domain_accepts_mail("nomail.example.org.pl") is False


def test_dns_error_fails_open(dns_on, fake):
    fake(broken=["awaria.pl"])
    assert dnscheck.domain_accepts_mail("awaria.pl") is None


def test_answer_is_cached_including_unknown(dns_on, fake):
    resolver = fake(mx={"cache.pl": [(10, "mx.cache.pl")]})
    assert dnscheck.domain_accepts_mail("cache.pl") is True
    assert dnscheck.domain_accepts_mail("CACHE.pl") is True
    assert resolver.queries == [("MX", "cache.pl")]

    broken = fake(broken=["zepsuta.pl"])
    assert dnscheck.domain_accepts_mail("zepsuta.pl") is None
    assert dnscheck.domain_accepts_mail("zepsuta.pl") is None
    assert len(broken.queries) == 1


def test_known_providers_and_reserved_domains_are_never_queried(dns_on, fake):
    resolver = fake()
    assert dnscheck.domain_accepts_mail("gmail.com") is True
    assert dnscheck.domain_accepts_mail("szkola.test") is None
    assert dnscheck.domain_accepts_mail("example.com") is None
    assert dnscheck.domain_accepts_mail("x.invalid") is None
    assert resolver.queries == []


def test_switch_off_means_no_queries(settings, fake):
    settings.EMAIL_DOMAIN_DNS_CHECK = False
    resolver = fake()
    assert dnscheck.domain_accepts_mail("o2.plo") is None
    assert resolver.queries == []


def test_idn_domain_is_queried_in_ascii(dns_on, fake):
    ascii_name = "żółw.pl".encode("idna").decode("ascii")
    resolver = fake(mx={ascii_name: [(5, "mx.zolw.pl")]})
    assert dnscheck.domain_accepts_mail("żółw.pl") is True
    assert resolver.queries == [("MX", ascii_name)]


def test_concurrent_lookups_are_capped_and_fail_open(dns_on, fake, monkeypatch):
    import threading

    resolver = fake(mx={"tlum.pl": [(10, "mx.tlum.pl")]})
    monkeypatch.setattr(dnscheck, "DNS_TIMEOUT", 0.01)
    busy = threading.BoundedSemaphore(1)
    busy.acquire()  # wszystkie miejsca zajęte przez inne wątki
    monkeypatch.setattr(dnscheck, "_LOOKUPS", busy)

    assert dnscheck.domain_accepts_mail("tlum.pl") is None
    assert resolver.queries == []

    busy.release()
    assert dnscheck.domain_accepts_mail("tlum.pl") is True  # „nie wiadomo” nie trafiło do cache'u


def test_unencodable_name_fails_open(dns_on, fake):
    resolver = fake()
    assert dnscheck.domain_accepts_mail("a" * 70 + ".pl") is None
    assert resolver.queries == []
