"""Bezpiecznik hosta generatora ruchu (PERF-01, przegląd L3) – ``unittest``, bez sieci.

Uruchomienie (obraz aplikacji ma ``httpx``, którego generator potrzebuje przy imporcie)::

    docker compose -f scripts/loadtest/docker-compose.loadtest.yml --profile loadgen run --rm \\
        --entrypoint python loadgen -m unittest -v test_loadgen_guard

Rozwiązywanie nazw jest podstawione (``socket.getaddrinfo``), więc test sprawdza logikę bramki,
a nie DNS komputera, na którym biegnie.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location("loadgen", Path(__file__).with_name("loadgen.py"))
loadgen = importlib.util.module_from_spec(_SPEC)
sys.modules["loadgen"] = loadgen
_SPEC.loader.exec_module(loadgen)

PROD = "169.58.242.197"


def _answer(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]


def fake_dns(table: dict):
    def resolve(host, *_args, **_kwargs):
        if host in table:
            return _answer(table[host])
        raise socket.gaierror("nie ma takiej nazwy")

    return mock.patch.object(loadgen.socket, "getaddrinfo", side_effect=resolve)


class TargetGuardTests(unittest.TestCase):
    def assert_rejected(self, base: str, allow: str = "", table: dict | None = None):
        with fake_dns(table or {}), self.assertRaises(SystemExit):
            loadgen.check_target(base, allow)

    def test_production_domains_are_rejected_even_with_the_flag(self):
        for base in ("https://olimpiadakwantowa.pl/", "https://www.iqo-official.org./", "http://QAIF.org/"):
            with self.subTest(base=base):
                self.assert_rejected(base, allow=base.split("/")[2])

    def test_production_address_in_any_spelling_is_rejected(self):
        spellings = {
            "169.58.242.197": PROD,
            "2839081669": PROD,  # zapis dziesiętny
            "0xa93af2c5": PROD,  # zapis szesnastkowy
            "::ffff:169.58.242.197": f"::ffff:{PROD}",  # IPv6 odwzorowany z IPv4
        }
        for host, address in spellings.items():
            with self.subTest(host=host):
                name = f"[{host}]" if ":" in host else f"{host}."  # kropka na końcu nazwy też
                self.assert_rejected(f"http://{name}/", allow=host, table={host: address})

    def test_local_name_pointing_at_production_is_rejected(self):
        self.assert_rejected("http://proxy/", table={"proxy": PROD})

    def test_local_names_pass(self):
        with fake_dns({"proxy": "172.31.77.10"}):
            self.assertTrue(loadgen.check_target("http://proxy/", ""))
            self.assertTrue(loadgen.check_target("http://localhost./", ""))

    def test_remote_host_needs_the_exact_flag_and_must_resolve(self):
        self.assert_rejected("https://staging.example.org/", table={"staging.example.org": "203.0.113.5"})
        self.assert_rejected(
            "https://staging.example.org/", allow="staging.example.org"
        )  # nie rozwiązuje się
        with fake_dns({"staging.example.org": "203.0.113.5"}):
            self.assertFalse(loadgen.check_target("https://staging.example.org./", "staging.example.org"))


if __name__ == "__main__":
    unittest.main()
