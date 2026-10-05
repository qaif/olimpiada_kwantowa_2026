"""Ocena SPF/DKIM/DMARC i sugestie rekordów (MAIL-01 § 4.1) – na atrapie resolwera."""

from __future__ import annotations

import pytest

from apps.mail_domains import checks
from apps.mail_domains.tests.fakes import DKIM_KEY, RELAY_IP, FakeResolver, verified_domain

# --- SPF: ocena -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (f"v=spf1 ip4:{RELAY_IP} -all", "pass"),
        ("v=spf1 ip4:169.58.242.0/24 -all", "pass"),
        (f"v=spf1 -ip4:{RELAY_IP} +all", "fail"),
        ("v=spf1 ip4:192.0.2.1 -all", "fail"),
        ("v=spf1 ip4:192.0.2.1 ~all", "softfail"),
        ("v=spf1 ip4:192.0.2.1", "neutral"),
        ("V=SPF1 IP4:169.58.242.197 -ALL", "pass"),
        ("v=spf1 ip4:to-nie-ip -all", "permerror"),
        ("v=spf1 exists:%{i}.spf.example -all", "fail"),  # makra nie pasują – wynik ostrożny
    ],
)
def test_spf_mechanisms(record, expected):
    resolver = FakeResolver(txt={"d.test": [record]})
    assert checks.evaluate_spf(resolver, "d.test", RELAY_IP) == expected


def test_spf_include_redirect_a_and_mx():
    resolver = FakeResolver(
        txt={
            "inc.test": ["v=spf1 include:relay.test -all"],
            "relay.test": [f"v=spf1 ip4:{RELAY_IP} -all"],
            "red.test": ["v=spf1 redirect=relay.test"],
            "a.test": ["v=spf1 a:mail.a.test -all"],
            "mx.test": ["v=spf1 mx/24 -all"],
            "deadinc.test": ["v=spf1 include:nothing.test -all"],
        },
        a={"mail.a.test": [RELAY_IP], "mx1.mx.test": ["169.58.242.1"]},
        mx={"mx.test": [(10, "mx1.mx.test")]},
    )
    assert checks.evaluate_spf(resolver, "inc.test", RELAY_IP) == "pass"
    assert checks.evaluate_spf(resolver, "red.test", RELAY_IP) == "pass"
    assert checks.evaluate_spf(resolver, "a.test", RELAY_IP) == "pass"
    assert checks.evaluate_spf(resolver, "mx.test", RELAY_IP) == "pass"
    assert checks.evaluate_spf(resolver, "deadinc.test", RELAY_IP) == "permerror"


def test_spf_two_records_and_none():
    resolver = FakeResolver(
        txt={"two.test": ["v=spf1 -all", f"v=spf1 ip4:{RELAY_IP} -all"], "none.test": ["x"]}
    )
    assert checks.evaluate_spf(resolver, "two.test", RELAY_IP) == "permerror"
    assert checks.evaluate_spf(resolver, "none.test", RELAY_IP) == "none"


def test_spf_more_than_ten_lookups_is_permerror():
    chain = {f"l{i}.test": [f"v=spf1 include:l{i + 1}.test -all"] for i in range(12)}
    chain["l12.test"] = [f"v=spf1 ip4:{RELAY_IP} -all"]
    assert checks.evaluate_spf(FakeResolver(txt=chain), "l0.test", RELAY_IP) == "permerror"


def test_spf_dns_failure_is_temperror():
    resolver = FakeResolver(txt={"d.test": ["v=spf1 include:x.test -all"]}, broken=["x.test"])
    assert checks.evaluate_spf(resolver, "d.test", RELAY_IP) == "temperror"


# --- SPF: sugestia scalenia -------------------------------------------------------------------


def test_existing_spf_gets_our_ip4_merged_in_not_a_second_record():
    suggestion, why = checks.suggest_spf(["v=spf1 include:_spf.google.com ~all"], RELAY_IP, [])
    assert suggestion == f"v=spf1 ip4:{RELAY_IP} include:_spf.google.com ~all"
    assert "NIE dodawaj drugiego" in why


def test_null_spf_of_a_parked_domain_is_replaced():
    suggestion, why = checks.suggest_spf(["v=spf1 -all"], RELAY_IP, [])
    assert suggestion == f"v=spf1 ip4:{RELAY_IP} -all"
    assert "nie wysyłam poczty" in why and "ZMIEŃ" in why


def test_existing_spf_with_our_ip_is_left_alone():
    record = f"v=spf1 ip4:{RELAY_IP} -all"
    assert checks.suggest_spf([record], RELAY_IP, [])[0] == record


def test_google_mx_without_spf_suggests_google_include_and_softfail():
    suggestion, _why = checks.suggest_spf([], RELAY_IP, ["aspmx.l.google.com"])
    assert suggestion == f"v=spf1 ip4:{RELAY_IP} include:_spf.google.com ~all"


def test_unknown_mx_without_spf_suggests_softfail_and_names_the_mx():
    suggestion, why = checks.suggest_spf([], RELAY_IP, ["mx.provider.example"])
    assert suggestion == f"v=spf1 ip4:{RELAY_IP} ~all"
    assert "mx.provider.example" in why


def test_no_mx_and_no_spf_suggests_strict_record():
    assert checks.suggest_spf([], RELAY_IP, [])[0] == f"v=spf1 ip4:{RELAY_IP} -all"


def test_two_spf_records_suggestion_says_merge():
    suggestion, why = checks.suggest_spf(["v=spf1 a -all", "v=spf1 mx -all"], RELAY_IP, [])
    assert suggestion == f"v=spf1 ip4:{RELAY_IP} a -all"
    assert "JEDEN" in why


# --- DKIM ---------------------------------------------------------------------------------------


def test_dkim_ok_and_compared_with_relay_key():
    resolver = verified_domain()
    bind = (
        'olimpiada._domainkey\tIN\tTXT\t( "v=DKIM1; h=sha256; k=rsa; s=email; "\n'
        f'\t  "p={DKIM_KEY[:20]}"\n\t  "{DKIM_KEY[20:]}" )'
    )
    result = checks.check_dkim(resolver, "iqo.test", "olimpiada", bind)
    assert result.status == checks.STATUS_OK
    assert "zgodny" in result.detail


@pytest.mark.parametrize(
    ("records", "expected_key", "fragment"),
    [
        ([], "", "Brak rekordu"),
        (["v=DKIM1; p="], "", "pusty klucz"),
        ([f"v=DKIM1; p={DKIM_KEY}", "v=DKIM1; p=XYZ"], "", "2 rekordy"),
        ([f"v=DKIM1; p={DKIM_KEY}"], "INNYKLUCZ", "INNY"),
    ],
)
def test_dkim_failures(records, expected_key, fragment):
    resolver = FakeResolver(txt={"olimpiada._domainkey.iqo.test": records})
    result = checks.check_dkim(resolver, "iqo.test", "olimpiada", expected_key)
    assert result.status == checks.STATUS_FAIL
    assert fragment in result.detail


def test_dkim_value_with_spaces_from_the_panel_still_matches():
    resolver = FakeResolver(
        txt={"olimpiada._domainkey.iqo.test": [f"v=DKIM1; k=rsa; p={DKIM_KEY[:10]} {DKIM_KEY[10:]}"]}
    )
    assert checks.check_dkim(resolver, "iqo.test", "olimpiada", DKIM_KEY).status == checks.STATUS_OK


# --- DMARC --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("records", "status"),
    [
        (["v=DMARC1; p=quarantine"], checks.STATUS_OK),
        (["v=DMARC1; p=reject; rua=mailto:x@y.test"], checks.STATUS_OK),
        (["v=DMARC1; p=none"], checks.STATUS_WARN),
        ([], checks.STATUS_FAIL),
        (["v=DMARC1; p=none", "v=DMARC1; p=reject"], checks.STATUS_FAIL),
        (["v=DMARC1; rua=mailto:x@y.test"], checks.STATUS_FAIL),
    ],
)
def test_dmarc(records, status):
    resolver = FakeResolver(txt={"_dmarc.iqo.test": records})
    assert checks.check_dmarc(resolver, "iqo.test", "ops@qaif.test").status == status


def test_missing_dmarc_hint_is_p_none_with_rua():
    result = checks.check_dmarc(FakeResolver(), "iqo.test", "ops@qaif.test")
    assert "v=DMARC1; p=none; rua=mailto:ops@qaif.test; adkim=r; aspf=r; fo=1" in result.hint


# --- całość -------------------------------------------------------------------------------------


def test_complete_domain_is_verified():
    report = checks.check_domain(
        verified_domain(), "iqo.test", ip=RELAY_IP, selector="olimpiada", expected_dkim=DKIM_KEY
    )
    assert report.verified, report.as_dict()
    assert report.as_dict()["verified"] is True


def test_squarespace_domain_before_changes_is_not_verified_and_gets_merge_suggestion():
    # Stan iqo-official.org sprzed zmian: SPF Google Workspace, bez klucza i bez DMARC.
    resolver = FakeResolver(
        txt={
            "iqo.test": ["v=spf1 include:_spf.google.com ~all"],
            "_spf.google.com": ["v=spf1 ip4:35.190.247.0/24 ~all"],
        },
        mx={"iqo.test": [(1, "aspmx.l.google.com")]},
    )
    report = checks.check_domain(resolver, "iqo.test", ip=RELAY_IP, selector="olimpiada")
    assert not report.verified
    assert report.failed_names() == ["spf", "dkim", "dmarc"]
    assert report.suggested_spf == f"v=spf1 ip4:{RELAY_IP} include:_spf.google.com ~all"
    assert report.mx_hosts == ["aspmx.l.google.com"] and report.existing_dmarc == ""


def test_parked_domain_with_strict_dmarc_reject_passes_once_spf_and_dkim_are_added():
    # iqo-official.org: DMARC p=reject z adkim=s/aspf=s zostaje – d= i domena koperty to dokładnie
    # domena nadawcy, więc ścisłe dopasowanie przechodzi. Nie zmieniamy go na p=none.
    resolver = FakeResolver(
        txt={
            "iqo.test": [f"v=spf1 ip4:{RELAY_IP} -all"],
            "olimpiada._domainkey.iqo.test": [f"v=DKIM1; k=rsa; p={DKIM_KEY}"],
            "_dmarc.iqo.test": ["v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s"],
        }
    )
    report = checks.check_domain(
        resolver, "iqo.test", ip=RELAY_IP, selector="olimpiada", expected_dkim=DKIM_KEY
    )
    assert report.verified
    assert report.existing_dmarc.startswith("v=DMARC1; p=reject")


def test_dns_outage_is_reported_as_error_not_as_missing_records():
    resolver = FakeResolver(broken=["iqo.test", "olimpiada._domainkey.iqo.test", "_dmarc.iqo.test"])
    report = checks.check_domain(resolver, "iqo.test", ip=RELAY_IP, selector="olimpiada")
    assert {c.status for c in report.checks} == {checks.STATUS_ERROR}
    assert not report.verified
