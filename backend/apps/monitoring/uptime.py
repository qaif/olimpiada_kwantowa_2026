"""Sprawdzanie dostępności serwisów „z zewnątrz” i listy alarmowe (OPS-02 § 4, OPERACJE § 44.4).

**Wyłącznie biblioteka standardowa** i ani jednego importu z Django ani z reszty aplikacji: ten plik
da się skopiować na dowolną maszynę z Pythonem ≥ 3.10 i uruchomić z crona::

    python3 uptime.py --once            # jeden przebieg (cron co minutę)
    python3 uptime.py --loop            # pętla co UPTIME_INTERVAL_SECONDS (usługa compose `uptime`)
    python3 uptime.py --once --dry-run  # wypisz, co by poszło, nic nie wysyłaj

Na serwerze chodzi jako usługa ``uptime`` profilu ``monitoring`` (obraz aplikacji, ``python -m
apps.monitoring.uptime --loop``) i puka w **publiczne** adresy – przez DNS, TLS i Caddy'ego, czyli
tą samą drogą co uczestnik. Monitor na tej samej maszynie nie zauważy jednak jej śmierci; dlatego
OPERACJE § 44.5 zaleca drugą kopię tego pliku (albo darmową usługę zewnętrzną) gdzie indziej.

Logika alarmów (:func:`evaluate`) jest czystą funkcją stanu – testy sprawdzają ją bez sieci:

- **awaria** = ``UPTIME_FAIL_THRESHOLD`` (3) porażek pod rząd → jeden list „AWARIA”,
- **przypomnienia** w trakcie awarii: po 1 h, potem 2 h, 4 h … aż do 24 h między listami,
- **powrót** = ``UPTIME_RECOVER_THRESHOLD`` (2) sukcesy pod rząd → list „POWRÓT” wyłącznie wtedy,
  gdy poszedł list o awarii (potknięcie krótsze niż próg nie daje żadnego listu),
- **certyfikat** < ``UPTIME_TLS_WARN_DAYS`` (14) dni → list od razu, potem raz na dobę; odnowiony →
  „certyfikat OK”,
- wszystko, co zmieniło się w jednym przebiegu, to **jeden** list (śmierć hosta ≠ dziesięć listów),
- twardy limit ``UPTIME_MAX_MAILS_PER_HOUR`` (6): nadmiar czeka na następny przebieg i jedzie
  w jednym liście z nowymi zmianami.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone  # noqa: UP017 – kopia poza serwerem bywa na Pythonie 3.10
from email.message import EmailMessage
from urllib.parse import urlsplit

USER_AGENT = "olimpiada-uptime/1 (+docs/OPERACJE.md 44)"
TRUE_VALUES = {"1", "true", "yes", "on"}


# --- konfiguracja -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """Jeden sprawdzany adres. ``expect``: ``page`` (kod 2xx po przekierowaniach) albo ``json-ok``
    (kod 200 i ``"status": "ok"`` w treści – ``/healthz/``, ``/status.json``)."""

    url: str
    expect: str = "page"

    @property
    def key(self) -> str:
        return f"http {self.url}"


@dataclass(frozen=True)
class Config:
    targets: tuple[Target, ...]
    recipients: tuple[str, ...] = ()
    sender: str = "uptime@localhost"
    smtp_host: str = "mail"
    smtp_port: int = 587
    smtp_starttls: bool = False
    site: str = "localhost"
    interval: int = 60
    timeout: float = 10.0
    fail_threshold: int = 3
    recover_threshold: int = 2
    remind_first: int = 3600
    remind_max: int = 86400
    tls_warn_days: int = 14
    tls_remind: int = 86400
    max_mails_per_hour: int = 6
    state_file: str = ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def _int(env: dict, name: str, default: int) -> int:
    try:
        return int(env.get(name, "") or default)
    except ValueError:
        return default


def default_targets(env: dict) -> tuple[Target, ...]:
    """Cele wyprowadzone z tych samych zmiennych, z których Caddy składa konfigurację.

    ``UPTIME_URLS`` (lista adresów) zastępuje całość; ``UPTIME_EXTRA_URLS`` dokłada (np. notebook).
    Adres z sufiksem ``|json`` oznacza sprawdzenie ``"status": "ok"``.
    """

    def parse(raw: str) -> list[Target]:
        out = []
        for item in raw.split():
            url, _, kind = item.partition("|")
            out.append(Target(url, "json-ok" if kind == "json" else "page"))
        return out

    explicit = (env.get("UPTIME_URLS") or "").strip()
    if explicit:
        return tuple(parse(explicit))
    site = (env.get("SITE_DOMAIN") or "").strip()
    targets: list[Target] = []
    if site and site != "localhost":
        domains = [site]
        for host in (env.get("EXTRA_DOMAINS") or "").split():
            # `www.` to przekierowanie na domenę główną, a host z portem – nie serwis publiczny.
            if not host.startswith("www.") and ":" not in host and host not in domains:
                domains.append(host)
        for domain in domains:
            targets.append(Target(f"https://{domain}/"))
            targets.append(Target(f"https://{domain}/healthz/", "json-ok"))
        targets.append(Target(f"https://{site}/status.json", "json-ok"))
        if _truthy(env.get("ERRORS_PROXY")):
            targets.append(Target(f"https://errors.{site}/_health/"))
    livekit = (env.get("LIVEKIT_URL") or "").strip()
    if livekit.startswith(("wss://", "https://")):
        host = urlsplit(livekit).netloc
        if host:
            # Serwer LiveKit odpowiada na `GET /` zwykłym „OK” (200) – to jest jego sonda życia.
            targets.append(Target(f"https://{host}/"))
    targets.extend(parse(env.get("UPTIME_EXTRA_URLS") or ""))
    seen: set[str] = set()
    return tuple(t for t in targets if not (t.url in seen or seen.add(t.url)))


def config_from_env(env: dict | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    site = (env.get("SITE_DOMAIN") or "localhost").strip()
    recipients = env.get("UPTIME_ALERT_EMAILS") or env.get("ALERT_EMAILS") or ""
    return Config(
        targets=default_targets(env),
        recipients=tuple(r.strip() for r in recipients.replace(";", ",").split(",") if r.strip()),
        sender=env.get("UPTIME_FROM") or f"uptime@{site}",
        smtp_host=env.get("UPTIME_SMTP_HOST") or "mail",
        smtp_port=_int(env, "UPTIME_SMTP_PORT", 587),
        smtp_starttls=_truthy(env.get("UPTIME_SMTP_STARTTLS")),
        site=site,
        interval=max(15, _int(env, "UPTIME_INTERVAL_SECONDS", 60)),
        timeout=float(_int(env, "UPTIME_TIMEOUT_SECONDS", 10)),
        fail_threshold=max(1, _int(env, "UPTIME_FAIL_THRESHOLD", 3)),
        recover_threshold=max(1, _int(env, "UPTIME_RECOVER_THRESHOLD", 2)),
        remind_first=max(60, _int(env, "UPTIME_REMIND_FIRST_SECONDS", 3600)),
        remind_max=max(60, _int(env, "UPTIME_REMIND_MAX_SECONDS", 86400)),
        tls_warn_days=_int(env, "UPTIME_TLS_WARN_DAYS", 14),
        max_mails_per_hour=max(1, _int(env, "UPTIME_MAX_MAILS_PER_HOUR", 6)),
        state_file=env.get("UPTIME_STATE_FILE") or "",
    )


# --- sprawdzenia --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """Wynik jednego sprawdzenia. ``ok=None`` – „nie wiadomo” (np. TLS bez połączenia, które i tak
    zgłasza sprawdzenie HTTP) – nie zmienia stanu."""

    ok: bool | None
    detail: str = ""


def check_http(target: Target, timeout: float) -> Result:
    # Wyłącznie http(s): `urllib` otworzyłby też `file:` i `ftp:`, a adres przychodzi ze zmiennej.
    if urlsplit(target.url).scheme not in ("http", "https"):
        return Result(False, "adres spoza http/https – popraw UPTIME_URLS/UPTIME_EXTRA_URLS")
    request = urllib.request.Request(  # noqa: S310 - schemat sprawdzony wyżej
        target.url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - adresy z konfiguracji
            status = response.status
            body = response.read(65536)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096) if exc.fp else b""
        hint = ""
        if b'"maintenance"' in body:
            hint = " (strona prac technicznych: przerwa planowa albo aplikacja nie odpowiada)"
        return Result(False, f"HTTP {exc.code}{hint}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return Result(False, f"brak odpowiedzi: {type(reason).__name__}: {str(reason)[:160]}")
    elapsed = time.monotonic() - started
    if not 200 <= status < 300:
        return Result(False, f"HTTP {status}")
    if target.expect == "json-ok":
        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError, ValueError:
            return Result(False, "odpowiedź nie jest JSON-em")
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            state = payload.get("status") if isinstance(payload, dict) else None
            return Result(False, f'"status": {json.dumps(state)} zamiast "ok"')
    return Result(True, f"HTTP {status}, {elapsed:.1f} s")


def check_tls(host: str, port: int, timeout: float, warn_days: int, now: float | None = None) -> Result:
    """Dni do wygaśnięcia certyfikatu: mniej niż ``warn_days`` albo błąd weryfikacji (wygasły, zła
    nazwa) = porażka; brak połączenia = ``None`` (zgłasza go sprawdzenie HTTP tego samego hosta)."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return Result(False, f"certyfikat odrzucony: {exc.verify_message or exc}")
    except (OSError, ssl.SSLError) as exc:
        return Result(None, f"brak połączenia TLS: {type(exc).__name__}")
    expires = ssl.cert_time_to_seconds(cert["notAfter"])
    days = (expires - (time.time() if now is None else now)) / 86400
    return Result(days >= warn_days, f"certyfikat wygasa za {days:.1f} dni ({cert['notAfter']})")


def run_checks(cfg: Config) -> dict[str, Result]:
    results: dict[str, Result] = {}
    tls_hosts: list[tuple[str, int]] = []
    for target in cfg.targets:
        results[target.key] = check_http(target, cfg.timeout)
        parts = urlsplit(target.url)
        if parts.scheme == "https" and parts.hostname:
            hp = (parts.hostname, parts.port or 443)
            if hp not in tls_hosts:
                tls_hosts.append(hp)
    for host, port in tls_hosts:
        results[f"tls {host}:{port}"] = check_tls(host, port, cfg.timeout, cfg.tls_warn_days)
    return results


# --- logika alarmów (czysta funkcja – testy) ---------------------------------------------------


@dataclass
class Notification:
    kind: str  # down | reminder | recovered | tls | tls-reminder | tls-ok
    key: str
    detail: str
    at: float
    since: float | None = None

    def line(self) -> str:
        when = datetime.fromtimestamp(self.at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")  # noqa: UP017
        label = {
            "down": "AWARIA",
            "reminder": "NADAL NIE DZIAŁA",
            "recovered": "POWRÓT",
            "tls": "CERTYFIKAT",
            "tls-reminder": "CERTYFIKAT (przypomnienie)",
            "tls-ok": "CERTYFIKAT OK",
        }[self.kind]
        extra = ""
        if self.since is not None and self.kind in ("reminder", "recovered"):
            minutes = int((self.at - self.since) // 60)
            extra = f" – od {minutes} min" if self.kind == "reminder" else f" – przerwa {minutes} min"
        return f"[{label}] {self.key}: {self.detail}{extra} ({when})"


@dataclass
class Evaluation:
    state: dict
    notifications: list[Notification] = field(default_factory=list)


def _fresh() -> dict:
    return {
        "fails": 0,
        "oks": 0,
        "down": False,
        "alerted": False,
        "since": None,
        "last_alert": None,
        "reminders": 0,
        "detail": "",
    }


def evaluate(state: dict, results: dict[str, Result], now: float, cfg: Config) -> Evaluation:
    """Nowy stan i powiadomienia po jednym przebiegu. Nie mutuje ``state`` wejściowego."""
    targets = {k: dict(v) for k, v in (state.get("targets") or {}).items()}
    out: list[Notification] = []
    for key, result in results.items():
        if result.ok is None:
            continue
        st = targets.get(key) or _fresh()
        is_tls = key.startswith("tls ")
        fail_threshold = 1 if is_tls else cfg.fail_threshold
        recover_threshold = 1 if is_tls else cfg.recover_threshold
        if result.ok:
            st["fails"] = 0
            st["oks"] += 1
            if st["down"] and st["oks"] >= recover_threshold:
                if st["alerted"]:
                    out.append(
                        Notification(
                            "tls-ok" if is_tls else "recovered", key, result.detail, now, st["since"]
                        )
                    )
                st.update(down=False, alerted=False, since=None, last_alert=None, reminders=0)
        else:
            if st["fails"] == 0:
                st["first_fail"] = now
            st["oks"] = 0
            st["fails"] += 1
            st["detail"] = result.detail
            if not st["down"] and st["fails"] >= fail_threshold:
                st["down"] = True
                st["since"] = st.get("first_fail", now)
            if st["down"]:
                if not st["alerted"]:
                    out.append(
                        Notification("tls" if is_tls else "down", key, result.detail, now, st["since"])
                    )
                    st.update(alerted=True, last_alert=now, reminders=0)
                else:
                    if is_tls:
                        wait = cfg.tls_remind
                    else:
                        wait = min(cfg.remind_first * (2 ** st["reminders"]), cfg.remind_max)
                    last = st["last_alert"] if st["last_alert"] is not None else now
                    if now - last >= wait:
                        out.append(
                            Notification(
                                "tls-reminder" if is_tls else "reminder", key, result.detail, now, st["since"]
                            )
                        )
                        st["last_alert"] = now
                        st["reminders"] += 1
        targets[key] = st
    # Cele, których już nie sprawdzamy (zmiana konfiguracji) – stan znika razem z nimi.
    for key in list(targets):
        if key not in results:
            del targets[key]
    new_state = {
        "targets": targets,
        "pending": list(state.get("pending") or []),
        "mails": list(state.get("mails") or []),
    }
    return Evaluation(new_state, out)


def take_batch(state: dict, new: list[Notification], now: float, cfg: Config) -> list[dict]:
    """Dokleja nowe powiadomienia do zaległych i oddaje paczkę do wysłania – albo ``[]``, gdy
    w ostatniej godzinie poszło już ``max_mails_per_hour`` listów (wtedy wszystko czeka w stanie)."""
    pending = list(state.get("pending") or []) + [n.__dict__ for n in new]
    state["mails"] = [t for t in state.get("mails") or [] if now - t < 3600]
    if not pending:
        state["pending"] = []
        return []
    if len(state["mails"]) >= cfg.max_mails_per_hour:
        state["pending"] = pending[-200:]
        return []
    state["pending"] = []
    return pending


def mark_sent(state: dict, now: float) -> None:
    state.setdefault("mails", []).append(now)


def restore_pending(state: dict, batch: list[dict]) -> None:
    """Wysyłka się nie udała – paczka wraca na początek kolejki (najwyżej 200 wpisów)."""
    state["pending"] = (batch + list(state.get("pending") or []))[-200:]


def compose_mail(batch: list[dict], cfg: Config) -> EmailMessage:
    notes = [Notification(**item) for item in batch]
    counts: dict[str, int] = {}
    for n in notes:
        group = {"reminder": "down", "tls-reminder": "tls"}.get(n.kind, n.kind)
        counts[group] = counts.get(group, 0) + 1
    words = {
        "down": "awaria",
        "recovered": "powrót",
        "tls": "certyfikat",
        "tls-ok": "certyfikat OK",
    }
    summary = ", ".join(f"{words[k]}: {v}" for k, v in counts.items())
    msg = EmailMessage()
    msg["Subject"] = f"[{cfg.site}] dostępność – {summary}"
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    body = [n.line() for n in notes]
    body += [
        "",
        "Co dalej: docs/OPERACJE.md § 7 (lista kontrolna incydentu) i § 44 (ten monitor).",
        f"Listy z tego monitora: najwyżej {cfg.max_mails_per_hour} na godzinę; przypomnienia o trwającej "
        "awarii co 1 h, 2 h, 4 h … do 24 h.",
    ]
    msg.set_content("\n".join(body))
    return msg


def send_mail(msg: EmailMessage, cfg: Config) -> None:
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        if cfg.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        smtp.send_message(msg)


# --- stan na dysku i pętla ----------------------------------------------------------------------


def load_state(path: str) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except OSError, ValueError:
        return {}


def save_state(path: str, state: dict) -> None:
    """Zapis atomowy (plik obok + ``os.replace``). Błąd zapisu nie zatrzymuje monitora – w pętli
    stan i tak żyje w pamięci, a plik jest wyłącznie po to, żeby restart nie gubił awarii."""
    if not path:
        return
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"uptime: nie zapisałem stanu {path}: {exc}", file=sys.stderr, flush=True)


def run_once(
    cfg: Config,
    state: dict,
    *,
    dry_run: bool = False,
    now: float | None = None,
    checker=run_checks,
    sender=send_mail,
) -> dict:
    now = time.time() if now is None else now
    results = checker(cfg)
    for key, result in results.items():
        mark = {True: "ok  ", False: "FAIL", None: "?   "}[result.ok]
        print(f"uptime: {mark} {key} – {result.detail}", flush=True)
    evaluation = evaluate(state, results, now, cfg)
    new_state = evaluation.state
    batch = take_batch(new_state, evaluation.notifications, now, cfg)
    if batch:
        msg = compose_mail(batch, cfg)
        if dry_run or not cfg.recipients:
            # Bez odbiorców (instalacja deweloperska) list idzie do logu i przepada – jak watchdog
            # przy pustym ALERT_EMAILS: nikogo nie budzimy i nie gromadzimy zaległości.
            why = "--dry-run" if dry_run else "brak UPTIME_ALERT_EMAILS/ALERT_EMAILS"
            print(f"uptime: list NIE wysłany ({why}): {msg['Subject']}\n{msg.get_content()}", flush=True)
        else:
            try:
                sender(msg, cfg)
                mark_sent(new_state, now)
                print(f"uptime: wysłano list ({len(batch)} zmian) do {', '.join(cfg.recipients)}", flush=True)
            except (OSError, smtplib.SMTPException) as exc:
                restore_pending(new_state, batch)
                print(
                    f"uptime: wysyłka nieudana ({exc}) – spróbuję w następnym przebiegu",
                    file=sys.stderr,
                    flush=True,
                )
    return new_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprawdzanie dostępności i listy alarmowe (OPS-02).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="jeden przebieg (domyślnie)")
    mode.add_argument("--loop", action="store_true", help="pętla co UPTIME_INTERVAL_SECONDS")
    parser.add_argument("--dry-run", action="store_true", help="nic nie wysyłaj, wypisz list")
    args = parser.parse_args(argv)
    cfg = config_from_env()
    if not cfg.targets:
        print(
            "uptime: brak celów (SITE_DOMAIN=localhost i puste UPTIME_URLS) – nie ma czego sprawdzać",
            file=sys.stderr,
        )
        return 2
    state = load_state(cfg.state_file)
    while True:
        state = run_once(cfg, state, dry_run=args.dry_run)
        save_state(cfg.state_file, state)
        if not args.loop:
            return 0
        time.sleep(cfg.interval)


if __name__ == "__main__":
    sys.exit(main())
