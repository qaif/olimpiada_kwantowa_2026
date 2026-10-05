"""Atrapa resolwera DNS: słownik nazwa → rekordy, bez sieci."""

from __future__ import annotations

from apps.mail_domains.dnsquery import DnsError

RELAY_IP = "169.58.242.197"
DKIM_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtestkey"


class FakeResolver:
    def __init__(self, txt=None, a=None, mx=None, broken=()):
        self._txt = {k.lower(): v for k, v in (txt or {}).items()}
        self._a = {k.lower(): v for k, v in (a or {}).items()}
        self._mx = {k.lower(): v for k, v in (mx or {}).items()}
        self.broken = {name.lower() for name in broken}
        self.queries: list[tuple[str, str]] = []

    def _get(self, table, kind, name):
        self.queries.append((kind, name))
        if name.lower() in self.broken:
            raise DnsError(f"SERVFAIL {name}")
        return list(table.get(name.lower(), []))

    def txt(self, name):
        return self._get(self._txt, "TXT", name)

    def a(self, name):
        return self._get(self._a, "A", name)

    def mx(self, name):
        return sorted(self._get(self._mx, "MX", name))


def verified_domain(domain: str = "iqo.test", **overrides) -> FakeResolver:
    """Domena z kompletem: SPF z Google i naszym IP, klucz DKIM, DMARC quarantine."""
    txt = {
        domain: [f"v=spf1 ip4:{RELAY_IP} include:_spf.google.com ~all", "google-site-verification=xyz"],
        "_spf.google.com": ["v=spf1 include:_netblocks.google.com ~all"],
        "_netblocks.google.com": ["v=spf1 ip4:35.190.247.0/24 ~all"],
        f"olimpiada._domainkey.{domain}": [f"v=DKIM1; h=sha256; k=rsa; s=email; p={DKIM_KEY}"],
        f"_dmarc.{domain}": ["v=DMARC1; p=quarantine; rua=mailto:dmarc@qaif.test"],
    }
    txt.update(overrides.pop("txt", {}))
    return FakeResolver(txt=txt, mx={domain: [(1, "aspmx.l.google.com")]}, **overrides)
