#!/usr/bin/env python3
"""Monitoring z zewnątrz: czy serwer produkcyjny w ogóle żyje (OPS-03, docs/tasks/OPS-03.md).

Watchdog aplikacyjny i Uptime Kuma stoją na tym samym VPS-ie, co serwis, więc śmierć hosta zabiera je
razem z nim. Ten skrypt chodzi **gdzie indziej** – w GitHub Actions (``.github/workflows/uptime.yml``,
co 10 minut) – i pyta przez publiczne HTTPS o to, co da się zobaczyć z zewnątrz:

- każda witryna: ``/`` (kod 200, czas), ``/healthz/`` (``status: ok``), ``/status.json`` (``status``
  i ``backup_restore_check``), certyfikat TLS (dni do wygaśnięcia),
- LiveKit: ``/`` odpowiada dokładnie ``OK``, plus certyfikat TLS.

Awaria jest **potwierdzona**, gdy to samo sprawdzenie nie przejdzie w dwóch próbach odległych o
``--recheck-delay`` sekund (domyślnie 120) – pojedyncza zgubiona odpowiedź nikogo nie budzi.
Z ``--issues`` skrypt prowadzi jedno zgłoszenie GitHuba z etykietą ``awaria`` (``gh`` z obrazu
runnera, token z ``GH_TOKEN``): zakłada je przy potwierdzonej awarii, komentuje przy zmianie zestawu
awarii, zamyka po powrocie. Stanem jest samo zgłoszenie – żadnych plików, cache'u ani artefaktów.

Wyłącznie biblioteka standardowa i składnia Pythona 3.10 (test w ``scripts/tests``): skrypt ma się
dać uruchomić z każdego laptopa i każdej maszyny z cronem, nie tylko z runnera GitHuba::

    python3 scripts/uptime_external.py                       # sprawdzenie, wynik na stdout
    python3 scripts/uptime_external.py --recheck-delay 0 --site https://example.org
    GH_TOKEN=… GITHUB_REPOSITORY=qaif/olimpiada_kwantowa_2026 \\
        python3 scripts/uptime_external.py --issues [--dry-run]

Kod wyjścia: 0 – monitoring zadziałał (także gdy serwis leży: alarmem jest zgłoszenie), 2 – zepsuł
się sam monitoring (np. ``gh`` odmówił), czyli czerwony przebieg w Actions ma znaczyć „napraw
monitor”, a nie „serwis leży” co 10 minut.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_SITES = ("https://olimpiadakwantowa.pl", "https://iqo-official.org")
DEFAULT_LIVE = ("https://live.olimpiadakwantowa.pl",)

#: Timeout pojedynczego żądania. Awarią jest dopiero brak odpowiedzi w tym czasie – wolna odpowiedź
#: jest tylko ostrzeżeniem (VPS traci 12–37 % CPU na kradzież hiperwizora, ogon p95 to host).
TIMEOUT_S = 20.0
SLOW_WARN_S = 5.0
#: Caddy odnawia certyfikat Let's Encrypt na ~30 dni przed końcem. 14 dni = odnawianie stoi od dwóch
#: tygodni (ostrzeżenie), 7 dni = zostało tyle, że trzeba działać dziś (awaria → zgłoszenie).
TLS_WARN_DAYS = 14
TLS_FAIL_DAYS = 7
RECHECK_DELAY_S = 120
#: Ile bajtów treści czytamy: ``/status.json`` ma kilkaset, strona główna kilkadziesiąt kilobajtów.
MAX_BODY = 512 * 1024

#: Wartości ``backup_restore_check`` (``apps.core.restore_check.LEVEL_*``), które są awarią: test
#: odtwarzania mówi „kopia zła” albo nie chodzi od 36 h. ``unknown`` to brak danych – ostrzeżenie.
RESTORE_CHECK_FAILED = ("failed", "stale")

ISSUE_LABEL = "awaria"
ISSUE_LABEL_COLOR = "B60205"
ISSUE_MARKER = "<!-- uptime-external -->"
STATE_RE = re.compile(r"<!-- uptime-state: (\[.*?\]) -->")
USER_AGENT = "olimpiada-uptime-external/1 (+https://github.com/qaif/olimpiada_kwantowa_2026)"

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Response:
    status: int | None
    body: bytes
    elapsed: float
    error: str | None = None


@dataclass
class Result:
    #: Stały identyfikator sprawdzenia – po nim porównujemy próby i zestaw awarii w zgłoszeniu.
    check: str
    level: str
    detail: str


# --------------------------------------------------------------------------------------------- sieć


def fetch(url: str, timeout: float = TIMEOUT_S) -> Response:
    """GET z weryfikacją certyfikatu i przekierowaniami; błąd sieci zamienia na ``Response.error``."""
    if urlsplit(url).scheme != "https":
        # Adresy przychodzą także ze zmiennych repozytorium – ``file:`` czy ``ftp:`` nie mają tu sensu.
        return Response(None, b"", 0.0, error=f"ValueError: tylko https:// ({url[:60]})")
    headers = {"User-Agent": USER_AGENT, "Cache-Control": "no-cache", "Accept": "*/*"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - schemat sprawdzony wyżej
    start = time.monotonic()
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:  # noqa: S310
            return Response(resp.status, resp.read(MAX_BODY), time.monotonic() - start)
    except urllib.error.HTTPError as exc:
        # 4xx/5xx to też odpowiedź – treść się przydaje (JSON trybu prac technicznych na 503).
        try:
            body = exc.read(MAX_BODY)
        except Exception:  # noqa: BLE001 - treść błędu jest tylko dodatkiem
            body = b""
        return Response(exc.code, body, time.monotonic() - start)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return Response(None, b"", time.monotonic() - start, error=f"{type(reason).__name__}: {reason}")


def read_cert_not_after(host: str, port: int = 443, timeout: float = TIMEOUT_S) -> datetime:
    """Data końca ważności certyfikatu po **zweryfikowanym** uzgodnieniu TLS (UTC).

    Certyfikat wygasły albo wystawiony na inną nazwę kończy się wyjątkiem – i dobrze: przeglądarka
    uczestnika też by go odrzuciła, więc dla monitoringu to awaria, a nie „0 dni”.
    """
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)


# ------------------------------------------------------------------------------------- ocena wyników


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _join(site: str, path: str) -> str:
    return site.rstrip("/") + path


def _timing(resp: Response, check: str, slow_warn: float) -> Result:
    if resp.elapsed > slow_warn:
        return Result(check, WARN, f"200, wolno: {resp.elapsed:.1f} s (próg {slow_warn:g} s)")
    return Result(check, OK, f"200, {resp.elapsed:.2f} s")


def _http_failure(resp: Response) -> str:
    if resp.status is None:
        return f"brak odpowiedzi po {resp.elapsed:.1f} s ({resp.error})"
    detail = f"HTTP {resp.status} po {resp.elapsed:.1f} s"
    if resp.status == 503 and _json(resp.body).get("status") == "maintenance":
        detail += " – tryb prac technicznych"
    return detail


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _value(value: object) -> str:
    """Wartość z odpowiedzi serwera do opisu – krótko, bo trafia do zgłoszenia."""
    return repr(value)[:40]


def eval_page(site: str, resp: Response, slow_warn: float = SLOW_WARN_S) -> Result:
    check = f"{_host(site)} /"
    if resp.status != 200:
        return Result(check, FAIL, _http_failure(resp))
    return _timing(resp, check, slow_warn)


def eval_healthz(site: str, resp: Response, slow_warn: float = SLOW_WARN_S) -> Result:
    check = f"{_host(site)} /healthz/"
    if resp.status != 200:
        return Result(check, FAIL, _http_failure(resp))
    status = _json(resp.body).get("status")
    if status != "ok":
        return Result(check, FAIL, f"status = {_value(status)}")
    return _timing(resp, check, slow_warn)


def eval_status_json(site: str, resp: Response, slow_warn: float = SLOW_WARN_S) -> list[Result]:
    """Dwa wyniki: ogólny ``status`` i ``backup_restore_check`` (osobno, żeby zgłoszenie mówiło co).

    ``/status.json`` oddaje **zawsze 200** (werdykt jest w treści) – poza trybem prac technicznych,
    kiedy Caddy podaje 503 z ``{"status": "maintenance"}``.
    """
    host = _host(site)
    check, check_backup = f"{host} /status.json", f"{host} /status.json backup_restore_check"
    data = _json(resp.body)
    if resp.status != 200 or not data:
        detail = _http_failure(resp) if resp.status != 200 else f"HTTP 200, ale nie JSON ({len(resp.body)} B)"
        return [
            Result(check, FAIL, detail),
            Result(check_backup, WARN, "brak danych (status.json nieczytelny)"),
        ]

    status = data.get("status")
    if status != "ok":
        services = data.get("services")
        down = sorted(k for k, v in services.items() if v is False) if isinstance(services, dict) else []
        detail = f"status = {_value(status)}" + (f"; nie działa: {', '.join(down)}" if down else "")
        main = Result(check, FAIL, detail)
    else:
        main = _timing(resp, check, slow_warn)

    level = data.get("backup_restore_check")
    if level in RESTORE_CHECK_FAILED:
        backup = Result(check_backup, FAIL, f"backup_restore_check = {level} (OPERACJE § 43.5)")
    elif level == "ok":
        backup = Result(check_backup, OK, "ok")
    else:
        backup = Result(check_backup, WARN, f"backup_restore_check = {_value(level)}")
    return [main, backup]


def eval_live(url: str, resp: Response, slow_warn: float = SLOW_WARN_S) -> Result:
    """Serwer LiveKit odpowiada na ``GET /`` dokładnie ``OK`` – inna treść to nie LiveKit."""
    check = f"{_host(url)} / (LiveKit)"
    if resp.status != 200:
        return Result(check, FAIL, _http_failure(resp))
    if resp.body.strip() != b"OK":
        return Result(check, FAIL, f"HTTP 200, ale treść ≠ OK ({len(resp.body)} B)")
    return _timing(resp, check, slow_warn)


def eval_tls(
    host: str,
    not_after: datetime | None,
    now: datetime,
    error: str | None = None,
    warn_days: int = TLS_WARN_DAYS,
    fail_days: int = TLS_FAIL_DAYS,
) -> Result:
    check = f"{host} TLS"
    if not_after is None:
        return Result(check, FAIL, f"uzgodnienie TLS nieudane ({error})")
    days = (not_after - now).total_seconds() / 86400
    expiry = not_after.strftime("%Y-%m-%d %H:%M UTC")
    if days < 0:
        return Result(check, FAIL, f"certyfikat wygasł {expiry}")
    if days < fail_days:
        return Result(check, FAIL, f"certyfikat wygasa za {days:.1f} dni ({expiry}), próg {fail_days}")
    if days < warn_days:
        return Result(check, WARN, f"certyfikat wygasa za {days:.1f} dni ({expiry}), próg {warn_days}")
    return Result(check, OK, f"{int(days)} dni (do {expiry})")


# ------------------------------------------------------------------------------------------ przebieg

Fetcher = Callable[[str], Response]
CertReader = Callable[[str], datetime]


def run_checks(
    sites: Iterable[str],
    live: Iterable[str],
    fetcher: Fetcher = fetch,
    cert_reader: CertReader = read_cert_not_after,
    now: datetime | None = None,
    slow_warn: float = SLOW_WARN_S,
) -> list[Result]:
    """Jedna próba wszystkich sprawdzeń. Funkcje sieciowe są wstrzykiwane – testy działają bez sieci."""
    now = now or datetime.now(timezone.utc)
    results: list[Result] = []
    hosts: list[str] = []
    for site in sites:
        results.append(eval_page(site, fetcher(_join(site, "/")), slow_warn))
        results.append(eval_healthz(site, fetcher(_join(site, "/healthz/")), slow_warn))
        results.extend(eval_status_json(site, fetcher(_join(site, "/status.json")), slow_warn))
        hosts.append(_host(site))
    for url in live:
        results.append(eval_live(url, fetcher(_join(url, "/")), slow_warn))
        hosts.append(_host(url))
    for host in dict.fromkeys(hosts):
        try:
            results.append(eval_tls(host, cert_reader(host), now))
        except (OSError, ssl.SSLError, ValueError, KeyError) as exc:
            results.append(eval_tls(host, None, now, error=f"{type(exc).__name__}: {exc}"))
    return results


def failing(results: Iterable[Result]) -> list[str]:
    return sorted({r.check for r in results if r.level == FAIL})


@dataclass
class Outcome:
    #: Wyniki ostatniej wykonanej próby – to jest „stan teraz”, pokazywany w zgłoszeniu.
    results: list[Result]
    #: Sprawdzenia, które nie przeszły w **obu** próbach.
    confirmed: list[str]
    #: Czy cokolwiek nie przeszło w którejkolwiek próbie (migotanie blokuje zamknięcie zgłoszenia).
    any_failure: bool


def run_with_recheck(
    run: Callable[[], list[Result]], delay: float, sleep: Callable[[float], None] = time.sleep
) -> Outcome:
    """„Dwa razy z rzędu”: druga próba tylko wtedy, gdy pierwsza coś znalazła (docs/tasks/OPS-03.md § 2)."""
    first = run()
    first_failed = set(failing(first))
    if not first_failed:
        return Outcome(first, [], False)
    sleep(delay)
    second = run()
    second_failed = set(failing(second))
    return Outcome(second, sorted(first_failed & second_failed), True)


# --------------------------------------------------------------------------------------- zgłoszenie

OPEN, UPDATE, CLOSE, NONE = "open", "update", "close", "none"


def find_issue(issues: Iterable[dict]) -> dict | None:
    """Nasze zgłoszenie: etykieta ``awaria`` **i** znacznik w treści (ręczne zgłoszenia pomijamy)."""
    ours = [i for i in issues if ISSUE_MARKER in (i.get("body") or "")]
    return min(ours, key=lambda i: i["number"]) if ours else None


def parse_state(body: str) -> list[str]:
    match = STATE_RE.search(body or "")
    if not match:
        return []
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return []
    return sorted(str(item) for item in state) if isinstance(state, list) else []


def decide(outcome: Outcome, issue_state: list[str] | None) -> str:
    """Co zrobić ze zgłoszeniem. ``issue_state`` = ``None``: otwartego zgłoszenia nie ma."""
    if issue_state is None:
        return OPEN if outcome.confirmed else NONE
    if outcome.confirmed:
        return UPDATE if sorted(outcome.confirmed) != sorted(issue_state) else NONE
    return NONE if outcome.any_failure else CLOSE


#: W kodzie (`…`) Markdown nie robi linków, obrazków ani wzmianek – groźne są tylko znaki, które
#: z kodu wychodzą (odwrotny apostrof), dzielą komórkę tabeli (``|``) albo wiersz (znaki sterujące).
_UNSAFE = re.compile(r"[\x00-\x1f\x7f`|]")


def _cell(text: str) -> str:
    """Tekst do komórki tabeli Markdown – zawsze jako kod.

    Szczegóły zawierają komunikaty wyjątków i wartości z odpowiedzi serwera; serwer przejęty przez
    kogoś nie może przez nie wstawić linku, obrazka ani wzmianki ``@osoba`` do zgłoszenia.
    """
    return "`" + _UNSAFE.sub(" ", text)[:200] + "`"


_ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


def render_table(results: Iterable[Result], confirmed: Iterable[str]) -> str:
    confirmed = set(confirmed)
    lines = ["| | Sprawdzenie | Szczegóły |", "|---|---|---|"]
    for r in sorted(results, key=lambda r: ({FAIL: 0, WARN: 1, OK: 2}[r.level], r.check)):
        icon = _ICON[r.level] + (" (potwierdzona)" if r.check in confirmed else "")
        lines.append(f"| {icon} | {_cell(r.check)} | {_cell(r.detail)} |")
    return "\n".join(lines)


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_issue_body(outcome: Outcome, now: datetime, run_url: str) -> str:
    return "\n".join(
        [
            ISSUE_MARKER,
            f"<!-- uptime-state: {json.dumps(sorted(outcome.confirmed))} -->",
            "Monitoring z zewnątrz (`.github/workflows/uptime.yml`) potwierdził awarię – to samo "
            "sprawdzenie nie przeszło w dwóch próbach odległych o 2 minuty.",
            "",
            f"Stan z {_stamp(now)} ([przebieg]({run_url})):",
            "",
            render_table(outcome.results, outcome.confirmed),
            "",
            "Co dalej: `docs/OPERACJE.md` § 46.3. Zgłoszenie zamknie się samo po powrocie serwisu; "
            "nowe komentarze pojawiają się tylko, gdy zmienia się zestaw awarii.",
        ]
    )


def render_update_comment(outcome: Outcome, previous: list[str], now: datetime, run_url: str) -> str:
    gone = sorted(set(previous) - set(outcome.confirmed))
    new = sorted(set(outcome.confirmed) - set(previous))
    lines = [f"Zmiana zestawu awarii ({_stamp(now)}, [przebieg]({run_url})):", ""]
    if new:
        lines.append("Nowe: " + ", ".join(_cell(c) for c in new))
    if gone:
        lines.append("Wróciły: " + ", ".join(_cell(c) for c in gone))
    lines += ["", render_table(outcome.results, outcome.confirmed)]
    return "\n".join(lines)


def render_close_comment(outcome: Outcome, now: datetime, run_url: str) -> str:
    warnings = [r for r in outcome.results if r.level == WARN]
    lines = [f"Wszystkie sprawdzenia przechodzą ({_stamp(now)}, [przebieg]({run_url})). Zamykam."]
    if warnings:
        lines += ["", "Zostały ostrzeżenia (nie są awarią):", "", render_table(warnings, [])]
    return "\n".join(lines)


def issue_title(confirmed: list[str]) -> str:
    more = f" (+{len(confirmed) - 1})" if len(confirmed) > 1 else ""
    return f"Awaria widoczna z zewnątrz: {confirmed[0]}{more}"


# ------------------------------------------------------------------------------------------ gh CLI


class GhError(RuntimeError):
    pass


def gh(args: list[str], stdin: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(  # noqa: S603 - stała lista argumentów, bez powłoki
        ["gh", *args],  # noqa: S607 - gh z obrazu runnera (PATH), jak w każdym kroku `run:`
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and proc.returncode != 0:
        raise GhError(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def apply_issue_action(
    outcome: Outcome, repo: str, run_url: str, now: datetime, dry_run: bool = False, runner=gh
) -> str:
    """Odczyt otwartego zgłoszenia, decyzja i jej wykonanie. Zwraca podjętą akcję."""
    raw = runner(
        ["issue", "list", "--repo", repo, "--label", ISSUE_LABEL, "--state", "open",
         "--json", "number,body", "--limit", "100"]
    )  # fmt: skip
    issue = find_issue(json.loads(raw or "[]"))
    state = parse_state(issue["body"]) if issue else None
    action = decide(outcome, state)
    if dry_run or action == NONE:
        return action
    if action == OPEN:
        # Etykieta może nie istnieć (pierwsza awaria w historii repozytorium): bez niej
        # ``issue create --label`` odmawia. Błąd „już istnieje” jest tu oczekiwany.
        runner(
            ["label", "create", ISSUE_LABEL, "--repo", repo, "--color", ISSUE_LABEL_COLOR,
             "--description", "Awaria produkcji (monitoring z zewnątrz, OPS-03)"],
            check=False,
        )  # fmt: skip
        runner(
            ["issue", "create", "--repo", repo, "--title", issue_title(outcome.confirmed),
             "--label", ISSUE_LABEL, "--body-file", "-"],
            stdin=render_issue_body(outcome, now, run_url),
        )  # fmt: skip
    elif action == UPDATE:
        number = str(issue["number"])
        runner(
            ["issue", "comment", number, "--repo", repo, "--body-file", "-"],
            stdin=render_update_comment(outcome, state or [], now, run_url),
        )
        runner(
            ["issue", "edit", number, "--repo", repo, "--body-file", "-"],
            stdin=render_issue_body(outcome, now, run_url),
        )
    elif action == CLOSE:
        number = str(issue["number"])
        runner(
            ["issue", "comment", number, "--repo", repo, "--body-file", "-"],
            stdin=render_close_comment(outcome, now, run_url),
        )
        runner(["issue", "close", number, "--repo", repo])
    return action


# ------------------------------------------------------------------------------------------- wyjście


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _annotation(text: str, prop: bool = False) -> str:
    """Treść komendy przepływu GitHuba (``::error::``) – bez nowych linii, które zaczęłyby nową komendę.

    ``prop``: wartość właściwości (``title=…``), gdzie dwukropek i przecinek też są składnią.
    """
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def report(outcome: Outcome, out=sys.stdout, annotate: bool = False) -> None:
    for r in sorted(outcome.results, key=lambda r: r.check):
        mark = "POTWIERDZONA " if r.check in outcome.confirmed else ""
        # Znaki sterujące poza wierszem: nowa linia z ``::`` na początku byłaby komendą przepływu.
        out.write(_CONTROL.sub(" ", f"[{r.level.upper():4}] {mark}{r.check}: {r.detail}") + "\n")
    if annotate:
        for r in outcome.results:
            if r.level == FAIL or r.level == WARN:
                kind = "error" if r.check in outcome.confirmed else "warning"
                out.write(f"::{kind} title={_annotation(r.check, prop=True)}::{_annotation(r.detail)}\n")


def write_summary(outcome: Outcome, action: str, now: datetime) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"## Monitoring z zewnątrz – {_stamp(now)}\n\n")
        fh.write(f"Potwierdzone awarie: {len(outcome.confirmed)}; zgłoszenie: `{action}`\n\n")
        fh.write(render_table(outcome.results, outcome.confirmed) + "\n")


def _env_list(name: str, default: Iterable[str]) -> list[str]:
    value = os.environ.get(name, "").split()
    return value or list(default)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--site", action="append", help="witryna aplikacji (powtarzalne; env UPTIME_SITES)")
    parser.add_argument("--live", action="append", help="adres LiveKit (powtarzalne; env UPTIME_LIVE)")
    parser.add_argument("--no-live", action="store_true", help="bez sprawdzenia LiveKit")
    parser.add_argument(
        "--recheck-delay", type=float, default=RECHECK_DELAY_S, help="sekundy do drugiej próby"
    )
    parser.add_argument("--issues", action="store_true", help="prowadź zgłoszenie `awaria` (gh, GH_TOKEN)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/repo")
    parser.add_argument("--dry-run", action="store_true", help="z --issues: tylko pokaż decyzję")
    args = parser.parse_args(argv)

    sites = args.site or _env_list("UPTIME_SITES", DEFAULT_SITES)
    live = [] if args.no_live else (args.live or _env_list("UPTIME_LIVE", DEFAULT_LIVE))
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"

    outcome = run_with_recheck(lambda: run_checks(sites, live), args.recheck_delay)
    now = datetime.now(timezone.utc)
    report(outcome, annotate=in_actions)

    action = "-"
    if args.issues:
        if not args.repo:
            print("--issues wymaga --repo albo GITHUB_REPOSITORY", file=sys.stderr)
            return 2
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_id = os.environ.get("GITHUB_RUN_ID")
        run_url = f"{server}/{args.repo}/actions/runs/{run_id}" if run_id else f"{server}/{args.repo}/actions"
        try:
            action = apply_issue_action(outcome, args.repo, run_url, now, dry_run=args.dry_run)
        except (GhError, OSError, subprocess.SubprocessError, ValueError) as exc:
            print(
                f"::error title=monitoring::{_annotation(str(exc))}" if in_actions else str(exc),
                file=sys.stderr,
            )
            return 2
        print(f"zgłoszenie: {action}" + (" (dry-run)" if args.dry_run else ""))
    write_summary(outcome, action, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
