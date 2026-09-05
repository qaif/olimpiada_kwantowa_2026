"""Adres klienta w audycie (przegląd Critica T-05, finding 6).

``X-Real-IP`` jest faktem tylko wtedy, gdy nadał go proxy, któremu ufamy. Nagłówek przyjęty od
dowolnego nadawcy pozwalałby wpisać do audytu adres wybrany przez atakującego, a audyt jest
dowodem – nie wolno mu być sterowanym z zewnątrz.
"""

import pytest
from django.test import RequestFactory, override_settings

from apps.accounts.tests.factories import CoordinatorFactory
from apps.core.models import AuditLog, audit, client_ip

PROXY = "172.30.1.7"
CLIENT = "198.51.100.9"
STRANGER = "203.0.113.4"


def request_from(remote_addr: str, real_ip: str | None = None):
    request = RequestFactory().get("/", REMOTE_ADDR=remote_addr)
    if real_ip is not None:
        request.META["HTTP_X_REAL_IP"] = real_ip
    return request


@override_settings(TRUSTED_PROXY_IPS=["172.30.1.0/24"])
def test_header_from_untrusted_peer_is_ignored():
    """Połączenie spoza listy proxy: liczy się REMOTE_ADDR, nagłówek jest tylko życzeniem klienta."""
    assert client_ip(request_from(STRANGER, CLIENT)) == STRANGER


@override_settings(TRUSTED_PROXY_IPS=["172.30.1.0/24"])
def test_header_from_trusted_proxy_is_used():
    """Połączenie z zaufanego proxy (CIDR): adresem klienta jest treść X-Real-IP."""
    assert client_ip(request_from(PROXY, CLIENT)) == CLIENT


@override_settings(TRUSTED_PROXY_IPS=["172.30.1.7"])
def test_single_address_entry_is_accepted():
    """Lista przyjmuje też pojedyncze adresy, nie tylko sieci."""
    assert client_ip(request_from(PROXY, CLIENT)) == CLIENT
    assert client_ip(request_from("172.30.1.8", CLIENT)) == "172.30.1.8"


@override_settings(TRUSTED_PROXY_IPS=["172.30.1.0/24"])
@pytest.mark.parametrize("value", ["nie-adres", "", "1.2.3.4, 5.6.7.8", "999.999.999.999"])
def test_malformed_header_falls_back_to_remote_addr(value):
    """Śmieci w nagłówku nie mogą trafić do bazy – wracamy do adresu połączenia."""
    assert client_ip(request_from(PROXY, value)) == PROXY


@override_settings(TRUSTED_PROXY_IPS=[])
def test_empty_trust_list_means_remote_addr_only():
    """Domyślna konfiguracja (pusta lista) nie ufa nikomu."""
    assert client_ip(request_from(PROXY, CLIENT)) == PROXY


@override_settings(TRUSTED_PROXY_IPS=["nie-jest-siecia", "172.30.1.0/24"])
def test_broken_entry_does_not_break_the_rest():
    """Zła pozycja w konfiguracji jest pomijana, a nie wywraca żądania."""
    assert client_ip(request_from(PROXY, CLIENT)) == CLIENT


def test_missing_request_gives_no_address():
    assert client_ip(None) is None


@pytest.mark.django_db
@override_settings(TRUSTED_PROXY_IPS=["172.30.1.0/24"])
def test_audit_entry_records_the_forwarded_address():
    """Ścieżka end-to-end: helper ``audit`` zapisuje adres wyliczony tą samą regułą."""
    actor = CoordinatorFactory()

    entry = audit(actor, "test.action", actor, {}, request=request_from(PROXY, CLIENT))

    assert AuditLog.objects.get(pk=entry.pk).ip == CLIENT
