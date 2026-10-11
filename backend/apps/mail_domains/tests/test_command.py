"""``manage.py check_mail_dns`` (MAIL-01 § 4.1): zapis wyniku, kod wyjścia, ``--suggest``."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.mail_domains.management.commands.check_mail_dns import Command
from apps.mail_domains.models import SenderDomain
from apps.mail_domains.tests.fakes import DKIM_KEY, RELAY_IP, FakeResolver, verified_domain

pytestmark = pytest.mark.django_db


@pytest.fixture
def use_resolver(monkeypatch):
    def install(resolver):
        monkeypatch.setattr(Command, "resolver_factory", staticmethod(lambda nameservers: resolver))
        return resolver

    return install


def run(*args) -> str:
    out = StringIO()
    call_command("check_mail_dns", *args, stdout=out)
    return out.getvalue()


def test_verified_domain_is_saved_and_printed(use_resolver):
    use_resolver(verified_domain())
    output = run("iqo.test", "--ip", RELAY_IP, "--dkim-public-key", DKIM_KEY)
    row = SenderDomain.objects.get(domain="iqo.test")
    assert row.verified and row.verified_at == row.checked_at
    assert row.report["checks"][0]["name"] == "spf"
    assert "ZWERYFIKOWANA" in output and "zgodny z kluczem relaya" in output


def test_failed_domain_exits_with_error_and_keeps_last_success_time(use_resolver):
    use_resolver(verified_domain())
    run("iqo.test", "--ip", RELAY_IP)
    first_ok = SenderDomain.objects.get(domain="iqo.test").verified_at

    use_resolver(FakeResolver(txt={"iqo.test": ["v=spf1 -all"]}))
    with pytest.raises(CommandError, match="Niezweryfikowane: iqo.test"):
        run("iqo.test", "--ip", RELAY_IP)
    row = SenderDomain.objects.get(domain="iqo.test")
    assert not row.verified
    assert row.verified_at == first_ok
    assert row.checked_at > first_ok


def test_relay_ip_defaults_to_the_a_record_of_the_helo_name(use_resolver, settings):
    settings.SITE_DOMAIN = "platforma.test"
    settings.MAIL_PUBLIC_IP = ""
    resolver = verified_domain()
    resolver._a["mail.platforma.test"] = [RELAY_IP]
    use_resolver(resolver)
    output = run("iqo.test")
    assert f"IP relaya {RELAY_IP}" in output


def test_unknown_relay_ip_is_a_clear_error(use_resolver, settings):
    settings.MAIL_PUBLIC_IP = ""
    use_resolver(FakeResolver())
    with pytest.raises(CommandError, match="--ip"):
        run("iqo.test")


def test_suggest_prints_key_value_lines_for_the_script_and_saves_nothing(use_resolver):
    # Stan iqo-official.org z 5.10.2026: rekordy ochronne Squarespace, bez MX.
    use_resolver(
        FakeResolver(
            txt={
                "iqo.test": ["v=spf1 -all"],
                "_dmarc.iqo.test": ["v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s"],
            }
        )
    )
    lines = dict(line.split("=", 1) for line in run("iqo.test", "--ip", RELAY_IP, "--suggest").splitlines())
    assert lines["spf"] == f"v=spf1 ip4:{RELAY_IP} -all"
    assert "nie wysyłam poczty" in lines["spf_note"]
    assert lines["dmarc"] == "v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s"
    assert lines["mx"] == ""
    assert not SenderDomain.objects.exists()


def test_no_save_and_json(use_resolver):
    use_resolver(verified_domain())
    data = json.loads(run("iqo.test", "--ip", RELAY_IP, "--no-save", "--json"))
    assert data[0]["domain"] == "iqo.test" and data[0]["verified"] is True
    assert not SenderDomain.objects.exists()


@pytest.mark.parametrize("bad", ["noreply@iqo.test", "iqo", "x-.test", "a b.test"])
def test_not_a_domain_is_refused(use_resolver, bad):
    use_resolver(FakeResolver())
    with pytest.raises(CommandError, match="nazwa domeny"):
        run(bad, "--ip", RELAY_IP)


def test_domain_is_normalized_to_lowercase(use_resolver):
    use_resolver(verified_domain())
    run("IQO.test.", "--ip", RELAY_IP)
    assert SenderDomain.objects.filter(domain="iqo.test").exists()
