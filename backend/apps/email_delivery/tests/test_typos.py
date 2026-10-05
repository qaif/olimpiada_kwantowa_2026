"""Podpowiedź literówek (MAIL-02 § 1.1) – wspólne przypadki z JS, IDN, plus-adresy, odległość."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.email_delivery.typos import KNOWN_DOMAINS, TLD_TYPOS, distance, suggest, suggest_domain

CASES = json.loads((Path(__file__).with_name("typo_cases.json")).read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize(("address", "expected"), CASES)
def test_shared_cases(address, expected):
    assert suggest(address) == expected


def test_real_typos_from_the_relay_log():
    # 5.10.2026: relay odrzucił ``kcadera@o2.plo`` („Domain not found”).
    assert suggest("kcadera@o2.plo") == "kcadera@o2.pl"


def test_local_part_is_kept_verbatim_including_plus_and_case():
    assert suggest("Ala.Ma+Kota.2026@Gmial.Com") == "Ala.Ma+Kota.2026@gmail.com"
    assert suggest("a+b+c@hotmial.com") == "a+b+c@hotmail.com"


def test_unicode_idn_domain_gets_tld_fix_in_unicode():
    assert suggest("jan@żółw.plo") == "jan@żółw.pl"
    assert suggest("jan@żółw.pl") is None


def test_punycode_idn_domain_is_decoded_before_comparison():
    ascii_domain = "żółw".encode("idna").decode("ascii")
    assert suggest(f"jan@{ascii_domain}.plo") == "jan@żółw.pl"


def test_broken_punycode_does_not_raise():
    assert suggest("jan@xn--zzzzzzzz-.plo") == "jan@xn--zzzzzzzz-.pl"


def test_trailing_dot_and_case_are_normalised():
    assert suggest_domain("GMAIL.COM.") is None
    assert suggest_domain("Gmial.Com.") == "gmail.com"


def test_known_domains_never_suggest_each_other():
    # Każda znana domena jest sama sobie najbliższa – ``op.pl`` nie może podpowiadać ``o2.pl``.
    assert all(suggest_domain(domain) is None for domain in KNOWN_DOMAINS)


def test_short_domains_need_distance_one():
    # ``ab.pl`` jest w odległości 2 od ``wp.pl``/``op.pl`` – przy pięciu znakach to inny adres, nie literówka.
    assert suggest_domain("ab.pl") is None
    assert suggest_domain("wo.pl") == "wp.pl"


def test_real_country_tlds_outside_the_map_are_left_alone():
    assert "co" not in TLD_TYPOS
    assert suggest_domain("startup.co") is None


def test_distance_counts_adjacent_transposition_as_one_step():
    assert distance("gmail", "gamil") == 1
    assert distance("abc", "abc") == 0
    assert distance("", "abc") == 3
    assert distance("kitten", "sitting") == 3


def test_lists_have_no_duplicates_and_are_lowercase():
    assert len(set(KNOWN_DOMAINS)) == len(KNOWN_DOMAINS)
    assert all(domain == domain.lower() for domain in KNOWN_DOMAINS)
    assert all(
        key == key.lower() and value in {"pl", "com", "net", "org"} for key, value in TLD_TYPOS.items()
    )
