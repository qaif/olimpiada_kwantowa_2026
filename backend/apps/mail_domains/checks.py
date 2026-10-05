"""Ocena opublikowanych rekordów poczty domeny nadawcy: SPF, DKIM, DMARC (MAIL-01 § 4.1).

Funkcje dostają **resolwer** (``txt``/``a``/``mx`` – :class:`apps.mail_domains.dnsquery.Resolver`
albo atrapa w testach) i nie wiedzą nic o Django; zapis wyniku i ostrzeżenia są w ``services``.

Co znaczy „domena zweryfikowana”: list od ``<cokolwiek>@<domena>`` wysłany przez relay platformy
przejdzie DMARC u odbiorcy. To wymaga (a) SPF, który autoryzuje adres IP relaya – sprawdzane
**oceną** rekordu (``check_host`` z RFC 7208), a nie szukaniem napisu, bo właściciel domeny zwykle
ma już SPF swojego dostawcy poczty i nasz adres bywa w nim przez ``include``; (b) klucza DKIM pod
naszym selektorem; (c) rekordu DMARC – bez niego Gmail i Yahoo od 2024 r. odrzucają pocztę masową,
a raporty ``rua`` są jedynym sposobem, żeby zobaczyć, kto jeszcze wysyła z tej domeny.
"""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass, field

from apps.mail_domains.dnsquery import DnsError

STATUS_OK = "ok"
STATUS_WARN = "warn"  # przechodzi, ale jest coś do zrobienia (np. DMARC p=none)
STATUS_FAIL = "fail"
STATUS_ERROR = "error"  # DNS nie odpowiedział – nic nie wiadomo

#: Limit zapytań DNS w ocenie SPF (RFC 7208 § 4.6.4). Przekroczenie = ``permerror`` u odbiorcy,
#: czyli SPF nie działa dla **nikogo** – także dla dotychczasowego dostawcy poczty domeny.
SPF_LOOKUP_LIMIT = 10

#: Mechanizm SPF dostawcy poczty rozpoznanego po MX. Domena z Google Workspace bez SPF po dopisaniu
#: samego ``ip4:<nasz>`` i ``-all`` straciłaby SPF dla własnych listów z Gmaila – stąd sugestia.
MX_PROVIDER_SPF = (
    ((".google.com", ".googlemail.com"), "include:_spf.google.com"),
    ((".outlook.com",), "include:spf.protection.outlook.com"),
)

SPF_RESULT_BY_QUALIFIER = {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    record: str = ""
    hint: str = ""


@dataclass
class DomainReport:
    domain: str
    selector: str
    ip: str
    checks: list[CheckResult] = field(default_factory=list)
    suggested_spf: str = ""
    spf_note: str = ""
    suggested_dmarc: str = ""
    #: Opublikowany DMARC (pusty = brak). Skrypt ``mail_add_domain.sh`` mówi wtedy „nie dodawaj drugiego”.
    existing_dmarc: str = ""
    mx_hosts: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return bool(self.checks) and all(c.status in (STATUS_OK, STATUS_WARN) for c in self.checks)

    def failed_names(self) -> list[str]:
        return [c.name for c in self.checks if c.status not in (STATUS_OK, STATUS_WARN)]

    def as_dict(self) -> dict:
        data = asdict(self)
        data["verified"] = self.verified
        return data


# --- SPF --------------------------------------------------------------------------------------


def spf_records(resolver, domain: str) -> list[str]:
    """Rekordy ``v=spf1`` domeny (wielkość liter wersji dowolna, RFC 7208 § 4.5)."""
    return [r for r in resolver.txt(domain) if r.lower() == "v=spf1" or r.lower().startswith("v=spf1 ")]


class _SpfState:
    def __init__(self):
        self.lookups = 0

    def count(self) -> None:
        self.lookups += 1
        if self.lookups > SPF_LOOKUP_LIMIT:
            raise _SpfPermError(f"więcej niż {SPF_LOOKUP_LIMIT} zapytań DNS w ocenie SPF")


class _SpfPermError(Exception):
    pass


def _split_cidr(argument: str, default_len: int = 32) -> tuple[str, int]:
    target, _, length = argument.partition("/")
    return target, int(length) if length.isdigit() else default_len


def _any_in(addresses: list[str], ip, prefix: int) -> bool:
    for address in addresses:
        try:
            if ip in ipaddress.ip_network(f"{address}/{prefix}", strict=False):
                return True
        except ValueError:
            continue
    return False


def evaluate_spf(resolver, domain: str, ip: str, _state: _SpfState | None = None, _depth: int = 0) -> str:
    """Wynik ``check_host(ip, domain)``: pass/fail/softfail/neutral/none/permerror/temperror.

    Obsługiwane: ``ip4``, ``a``, ``mx``, ``include``, ``redirect``, ``all``. ``ip6``, ``exists``,
    ``ptr`` i makra (``%{…}``) nie pasują nigdy – relay wychodzi przez IPv4 (compose:
    ``POSTFIX_inet_protocols: ipv4``), a ``exists``/``ptr``/makr w rekordach domen konkursów nie
    spotkaliśmy. Nie pasujący mechanizm, który u odbiorcy by pasował, daje tu wynik ostrożniejszy
    („nie autoryzuje”), nigdy fałszywe „autoryzuje”.
    """
    state = _state or _SpfState()
    address = ipaddress.ip_address(ip)
    try:
        records = spf_records(resolver, domain)
    except DnsError:
        return "temperror"
    if not records:
        return "none"
    if len(records) > 1:
        return "permerror"
    redirect = None
    try:
        for term in records[0].split()[1:]:
            lowered = term.lower()
            if "=" in lowered and lowered.split("=", 1)[0].isalpha():
                name, _, value = term.partition("=")
                if name.lower() == "redirect":
                    redirect = value
                continue
            qualifier = term[0] if term[0] in "+-~?" else "+"
            mechanism = term[1:] if term[0] in "+-~?" else term
            name, _, argument = mechanism.partition(":")
            name = name.lower()
            if "/" in name:  # ``a/24`` i ``mx/24`` – długość prefiksu bez nazwy domeny
                name, _, cidr = name.partition("/")
                argument = f"{domain}/{cidr}"
            if "%" in argument:
                continue
            if name == "all":
                return SPF_RESULT_BY_QUALIFIER[qualifier]
            if name == "ip4":
                try:
                    if address in ipaddress.ip_network(argument, strict=False):
                        return SPF_RESULT_BY_QUALIFIER[qualifier]
                except ValueError as exc:
                    raise _SpfPermError(f"zły mechanizm {term}") from exc
            elif name == "a":
                state.count()
                target, prefix = _split_cidr(argument or domain)
                if _any_in(resolver.a(target or domain), address, prefix):
                    return SPF_RESULT_BY_QUALIFIER[qualifier]
            elif name == "mx":
                state.count()
                target, prefix = _split_cidr(argument or domain)
                for _pref, host in resolver.mx(target or domain)[:10]:
                    if _any_in(resolver.a(host), address, prefix):
                        return SPF_RESULT_BY_QUALIFIER[qualifier]
            elif name == "include":
                state.count()
                if _depth > SPF_LOOKUP_LIMIT:
                    raise _SpfPermError("zbyt głęboko zagnieżdżone include")
                result = evaluate_spf(resolver, argument, ip, state, _depth + 1)
                if result == "pass":
                    return SPF_RESULT_BY_QUALIFIER[qualifier]
                if result in ("none", "permerror"):
                    raise _SpfPermError(f"include:{argument} bez poprawnego SPF")
                if result == "temperror":
                    return "temperror"
            elif name in ("exists", "ptr"):
                state.count()
        if redirect:
            state.count()
            result = evaluate_spf(resolver, redirect, ip, state, _depth + 1)
            return "permerror" if result == "none" else result
    except _SpfPermError:
        return "permerror"
    except DnsError:
        return "temperror"
    return "neutral"


def mx_provider_mechanism(mx_hosts: list[str]) -> str:
    for suffixes, mechanism in MX_PROVIDER_SPF:
        if any(host.rstrip(".").endswith(suffix) for host in mx_hosts for suffix in suffixes):
            return mechanism
    return ""


def suggest_spf(existing: list[str], ip: str, mx_hosts: list[str]) -> tuple[str, str]:
    """(sugerowany rekord SPF, zdanie wyjaśnienia). Scalenie, nigdy drugi rekord ``v=spf1``."""
    ours = f"ip4:{ip}"
    if len(existing) == 1:
        terms = existing[0].split()
        if any(term.lower() in (ours, f"+{ours}") for term in terms[1:]):
            return existing[0], "Istniejący SPF zawiera już adres relaya – zostaw go bez zmian."
        merged = " ".join([terms[0], ours, *terms[1:]])
        if all(term.lower().lstrip("+-~?") == "all" for term in terms[1:]):
            # ``v=spf1 -all`` = „z tej domeny nie wychodzi żadna poczta” – rekord ochronny, który
            # Squarespace (i inni rejestratorzy) wstawiają domenom bez skrzynek. Stan iqo-official.org
            # z 5.10.2026. Zmieniamy go, a nie dopisujemy drugi.
            return merged, (
                "Domena ma rekord „nie wysyłam poczty” (" + existing[0] + "). ZMIEŃ go na wartość wyżej. "
                "NIE dodawaj drugiego rekordu v=spf1: dwa rekordy unieważniają SPF."
            )
        return merged, (
            "Domena ma już SPF (zapewne dla dotychczasowego dostawcy poczty). ZMIEŃ ten rekord na "
            "wartość wyżej – dopisany jest tylko mechanizm ip4. NIE dodawaj drugiego rekordu v=spf1: "
            "dwa rekordy unieważniają SPF dla wszystkich nadawców domeny."
        )
    if len(existing) > 1:
        merged = " ".join([existing[0].split()[0], ours, *existing[0].split()[1:]])
        return merged, (
            f"Domena ma {len(existing)} rekordy v=spf1 – to błąd już dziś (permerror). Połącz je w JEDEN "
            "rekord: wartość wyżej powstała z pierwszego; dopisz do niej mechanizmy z pozostałych."
        )
    provider = mx_provider_mechanism(mx_hosts)
    if provider:
        return f"v=spf1 {ours} {provider} ~all", (
            "Domena nie ma SPF, a jej MX wskazuje dostawcę poczty – mechanizm dostawcy jest w sugestii, "
            "żeby jego listy (np. z Gmaila) nie straciły SPF."
        )
    if mx_hosts:
        return f"v=spf1 {ours} ~all", (
            "Domena nie ma SPF, a ma MX nieznanego dostawcy (" + ", ".join(mx_hosts) + "). Jeśli ten "
            "dostawca też wysyła listy z domeny, dopisz jego mechanizm (include:…) przed ~all."
        )
    return f"v=spf1 {ours} -all", "Domena nie ma SPF ani MX – rekord autoryzuje wyłącznie relay platformy."


def check_spf(resolver, domain: str, ip: str, report: DomainReport | None = None) -> CheckResult:
    """Ocena SPF; sugestię scalenia, MX i wyjaśnienie dopisuje do ``report`` (gdy podany)."""
    report = report or DomainReport(domain=domain, selector="", ip=ip)
    try:
        existing = spf_records(resolver, domain)
        report.mx_hosts = [host for _pref, host in resolver.mx(domain)]
    except DnsError as exc:
        return CheckResult("spf", STATUS_ERROR, f"DNS nie odpowiedział: {exc}")
    report.suggested_spf, report.spf_note = suggest_spf(existing, ip, report.mx_hosts)
    result = evaluate_spf(resolver, domain, ip)
    record = " | ".join(existing)
    if result == "pass":
        return CheckResult("spf", STATUS_OK, f"SPF autoryzuje {ip} (pass).", record)
    if result == "temperror":
        return CheckResult("spf", STATUS_ERROR, "DNS nie odpowiedział w trakcie oceny SPF.", record)
    detail = {
        "none": "Domena nie ma rekordu SPF.",
        "permerror": "SPF jest błędny (dwa rekordy v=spf1, zły mechanizm albo ponad 10 zapytań DNS).",
    }.get(result, f"SPF nie autoryzuje {ip} (wynik: {result}).")
    return CheckResult("spf", STATUS_FAIL, detail, record, f"{report.suggested_spf}  — {report.spf_note}")


# --- DKIM -------------------------------------------------------------------------------------


def parse_tags(record: str) -> dict[str, str]:
    """Lista ``tag=wartość; …`` (DKIM, DMARC). Białe znaki w wartościach usunięte – panel DNS je wstawia."""
    tags: dict[str, str] = {}
    for part in record.split(";"):
        name, sep, value = part.partition("=")
        if sep:
            tags[name.strip().lower()] = "".join(value.split())
    return tags


def normalize_public_key(value: str) -> str:
    """Sam klucz ``p=`` z wartości TXT albo z pliku BIND z ``opendkim-genkey`` (cudzysłowy, nawiasy)."""
    text = value
    if '"' in text:
        text = "".join(text.split('"')[1::2])
    tags = parse_tags(text) if "p=" in text else {"p": text}
    return "".join(tags.get("p", "").split())


def check_dkim(resolver, domain: str, selector: str, expected_key: str = "") -> CheckResult:
    name = f"{selector}._domainkey.{domain}"
    try:
        records = [r for r in resolver.txt(name) if "p=" in r]
    except DnsError as exc:
        return CheckResult("dkim", STATUS_ERROR, f"DNS nie odpowiedział: {exc}")
    if not records:
        return CheckResult("dkim", STATUS_FAIL, f"Brak rekordu TXT {name}.")
    if len(records) > 1:
        return CheckResult("dkim", STATUS_FAIL, f"{name} ma {len(records)} rekordy z kluczem – zostaw jeden.")
    published = normalize_public_key(records[0])
    if not published:
        return CheckResult("dkim", STATUS_FAIL, f"{name} ma pusty klucz p= (klucz odwołany).", records[0])
    expected = normalize_public_key(expected_key) if expected_key else ""
    if expected and expected != published:
        return CheckResult(
            "dkim",
            STATUS_FAIL,
            f"Klucz w {name} jest INNY niż klucz relaya – rekord jest nieaktualny albo wklejony z błędem.",
            records[0],
            "Wklej wartość z mail-dns-<domena>.txt jeszcze raz, w całości.",
        )
    detail = f"Klucz DKIM opublikowany pod {name}"
    detail += (
        " i zgodny z kluczem relaya." if expected else " (nieporównany z relayem – bez --dkim-public-key)."
    )
    return CheckResult("dkim", STATUS_OK, detail, records[0])


# --- DMARC ------------------------------------------------------------------------------------


def suggest_dmarc(rua: str) -> str:
    rua_part = f" rua=mailto:{rua};" if rua else ""
    return f"v=DMARC1; p=none;{rua_part} adkim=r; aspf=r; fo=1"


def check_dmarc(resolver, domain: str, rua: str = "") -> CheckResult:
    name = f"_dmarc.{domain}"
    try:
        records = [r for r in resolver.txt(name) if r.replace(" ", "").lower().startswith("v=dmarc1")]
    except DnsError as exc:
        return CheckResult("dmarc", STATUS_ERROR, f"DNS nie odpowiedział: {exc}")
    if not records:
        return CheckResult(
            "dmarc", STATUS_FAIL, f"Brak rekordu {name}.", hint=f"Dodaj TXT {name}: {suggest_dmarc(rua)}"
        )
    if len(records) > 1:
        return CheckResult(
            "dmarc",
            STATUS_FAIL,
            f"{name} ma {len(records)} rekordy v=DMARC1 – odbiorcy zignorują oba.",
            " | ".join(records),
        )
    policy = parse_tags(records[0]).get("p", "").lower()
    if policy == "none":
        return CheckResult(
            "dmarc",
            STATUS_WARN,
            "DMARC p=none – tryb obserwacji. Po 2 tygodniach czystych raportów zmień na p=quarantine.",
            records[0],
        )
    if policy in ("quarantine", "reject"):
        return CheckResult("dmarc", STATUS_OK, f"DMARC p={policy}.", records[0])
    return CheckResult(
        "dmarc", STATUS_FAIL, f"DMARC bez poprawnej polityki p= ({policy or 'brak'}).", records[0]
    )


# --- całość -----------------------------------------------------------------------------------


def check_domain(
    resolver, domain: str, *, ip: str, selector: str, expected_dkim: str = "", dmarc_rua: str = ""
) -> DomainReport:
    report = DomainReport(domain=domain, selector=selector, ip=ip)
    spf = check_spf(resolver, domain, ip, report)
    dmarc = check_dmarc(resolver, domain, dmarc_rua)
    report.suggested_dmarc = suggest_dmarc(dmarc_rua)
    report.existing_dmarc = dmarc.record
    report.checks = [spf, check_dkim(resolver, domain, selector, expected_dkim), dmarc]
    return report
