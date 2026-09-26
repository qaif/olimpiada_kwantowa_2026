#!/usr/bin/env python3
"""Pomiar czasu odpowiedzi stron publicznych: django CMS kontra Wagtail (docs/tasks/DJ-02.md § 10.6, § 11).

Lekki generator ruchu bez zależności (tylko biblioteka standardowa): N żądań GET rozłożonych po
podanych adresach, stała współbieżność (wątki, każdy z własnym połączeniem keep-alive – jak
przeglądarka), wynik p50/p95/p99/średnia/maks. na adres i łącznie, przepustowość, kody odpowiedzi
i nagłówek ``X-Djcms-Mode`` (który upstream naprawdę odpowiedział: ``primary``/``preview`` = djcms,
brak = web).

Uruchamia go ``scripts/tests/djcms_primary_e2e.sh`` (krok D) z kontenera klienta w sieci ``edge``
stosu E2E – żądania idą przez prawdziwego Caddy'ego, bez pętli przez host. Samodzielnie::

    python scripts/djcms_bench.py --connect proxy --url https://olimpiada.test/ \\
        --url https://olimpiada.test/zadania/ --requests 300 --concurrency 8 [--cookie djcms_view=wagtail]
    python scripts/djcms_bench.py --summary e2e/artifacts/djcms     # tabela ze wszystkich bench-*.json

``--connect HOST[:PORT]`` – dokąd otworzyć połączenie TCP (np. kontener ``proxy``), a nazwa z adresu
idzie w SNI i w ``Host`` – tak jak ``curl --resolve``. Certyfikat nie jest sprawdzany (lokalne CA
Caddy'ego w E2E). ``--warmup`` żądań na adres przed pomiarem (bufory, połączenia z bazą) nie liczy się.

Uwaga do interpretacji: pomiar na maszynie deweloperskiej mierzy KOD (render, zapytania), a nie
serwer produkcyjny. VPS produkcyjny traci 12–37 % czasu CPU na kradzież hiperwizora (Contabo),
więc bezwzględne liczby są tam gorsze, a ogon (p95) bardziej rozciągnięty – porównywać należy
stosunki djcms/Wagtail zmierzone w tym samym przebiegu.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import statistics
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Połączenie TLS do ``connect_host`` z SNI nazwy z adresu (jak ``curl --resolve``)."""

    def __init__(self, sni: str, connect_host: str, port: int, *, context: ssl.SSLContext, timeout: float):
        super().__init__(connect_host, port, context=context, timeout=timeout)
        self._sni = sni
        self._ctx = context

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self._sni)


def _connection(url: str, connect: str | None, timeout: float) -> http.client.HTTPConnection:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target, target_port = host, port
    if connect:
        target, _, maybe_port = connect.partition(":")
        target_port = int(maybe_port) if maybe_port else port
    if parts.scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return _PinnedHTTPSConnection(host, target, target_port, context=ctx, timeout=timeout)
    return http.client.HTTPConnection(target, target_port, timeout=timeout)


def percentile(values: list[float], pct: float) -> float:
    """Percentyl metodą najbliższej rangi (bez interpolacji – wynik jest jednym z pomiarów)."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, int(-(-pct * len(ordered) // 100)))  # sufit
    return ordered[min(rank, len(ordered)) - 1]


def _one(conn_holder: dict, url: str, args) -> tuple[float, int, str]:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    headers = {
        "Host": parts.netloc,
        "User-Agent": "djcms-bench/1",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip",
    }
    if args.cookie:
        headers["Cookie"] = "; ".join(args.cookie)
    for attempt in (1, 2):
        conn = conn_holder.get(parts.netloc)
        if conn is None:
            conn = conn_holder[parts.netloc] = _connection(url, args.connect, args.timeout)
        started = time.perf_counter()
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            response.read()
            elapsed = time.perf_counter() - started
            mode = response.getheader("X-Djcms-Mode") or "web"
            if response.getheader("Connection", "").lower() == "close":
                conn.close()
                conn_holder.pop(parts.netloc, None)
            return elapsed, response.status, mode
        except (http.client.HTTPException, OSError):
            conn.close()
            conn_holder.pop(parts.netloc, None)
            if attempt == 2:
                return time.perf_counter() - started, 0, "error"
    raise AssertionError("unreachable")  # pragma: no cover


def run(args) -> dict:
    urls: list[str] = args.url
    for url in urls:  # rozgrzewka: połączenia z bazą, bufory szablonów, certyfikat on-demand
        holder: dict = {}
        for _ in range(args.warmup):
            _one(holder, url, args)
    plan = [urls[i % len(urls)] for i in range(args.requests)]
    lock = threading.Lock()
    results: list[tuple[str, float, int, str]] = []
    cursor = iter(range(len(plan)))

    def worker() -> None:
        holder: dict = {}
        while True:
            with lock:
                index = next(cursor, None)
            if index is None:
                break
            url = plan[index]
            elapsed, status, mode = _one(holder, url, args)
            with lock:
                results.append((url, elapsed, status, mode))
        for conn in holder.values():
            conn.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(args.concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.perf_counter() - started

    def stats(rows) -> dict:
        times = [r[1] * 1000 for r in rows]
        statuses = Counter(str(r[2]) for r in rows)
        modes = Counter(r[3] for r in rows)
        return {
            "n": len(rows),
            "ok": sum(1 for r in rows if 200 <= r[2] < 400),
            "p50_ms": round(percentile(times, 50), 1),
            "p95_ms": round(percentile(times, 95), 1),
            "p99_ms": round(percentile(times, 99), 1),
            "mean_ms": round(statistics.fmean(times), 1) if times else None,
            "max_ms": round(max(times), 1) if times else None,
            "statuses": dict(statuses),
            "upstream": dict(modes),
        }

    report = {
        "label": args.label or ",".join(urls),
        "requests": args.requests,
        "concurrency": args.concurrency,
        "cookie": args.cookie,
        "wall_s": round(wall, 2),
        "rps": round(len(results) / wall, 1) if wall else None,
        "total": stats(results),
        "per_url": {url: stats([r for r in results if r[0] == url]) for url in urls},
    }
    return report


def print_report(report: dict) -> None:
    print(
        f"== {report['label']}: {report['requests']} żądań, współbieżność {report['concurrency']}, "
        f"{report['wall_s']} s, {report['rps']} req/s"
    )
    header = (
        f"   {'adres':<42} {'n':>4} {'ok':>4} {'p50':>7} {'p95':>7} {'p99':>7} {'śr.':>7} {'maks.':>7}"
        "  upstream / kody"
    )
    print(header)
    rows = list(report["per_url"].items()) + [("RAZEM", report["total"])]
    for url, s in rows:
        print(
            f"   {url[-42:]:<42} {s['n']:>4} {s['ok']:>4} {s['p50_ms']:>7} {s['p95_ms']:>7} {s['p99_ms']:>7} "
            f"{s['mean_ms']:>7} {s['max_ms']:>7}  {s['upstream']} {s['statuses']}"
        )


def summary(directory: Path) -> int:
    files = sorted(directory.glob("bench-*.json"))
    if not files:
        print(f"brak plików bench-*.json w {directory}", file=sys.stderr)
        return 1
    reports = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    print("| pomiar | n | ok | p50 [ms] | p95 [ms] | p99 [ms] | śr. [ms] | req/s | upstream |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in reports:
        t = r["total"]
        up = ", ".join(f"{k}×{v}" for k, v in t["upstream"].items())
        print(
            f"| {r['label']} | {t['n']} | {t['ok']} | {t['p50_ms']} | {t['p95_ms']} | {t['p99_ms']} | "
            f"{t['mean_ms']} | {r['rps']} | {up} |"
        )
    print()
    print("| pomiar | adres | p50 [ms] | p95 [ms] |")
    print("|---|---|---:|---:|")
    for r in reports:
        for url, s in r["per_url"].items():
            print(f"| {r['label']} | {urlsplit(url).path} | {s['p50_ms']} | {s['p95_ms']} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", action="append", default=[], help="adres (powtarzalne); żądania po kolei")
    parser.add_argument("--connect", help="HOST[:PORT] połączenia TCP (SNI i Host z adresu)")
    parser.add_argument(
        "--cookie", action="append", default=[], help="ciasteczko nazwa=wartość (powtarzalne)"
    )
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=10, help="żądań rozgrzewki na adres (poza pomiarem)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--label", default="")
    parser.add_argument("--json", help="zapis wyniku do pliku JSON")
    parser.add_argument("--summary", metavar="KATALOG", help="tylko tabela ze wszystkich bench-*.json")
    args = parser.parse_args(argv)
    if args.summary:
        return summary(Path(args.summary))
    if not args.url:
        parser.error("podaj co najmniej jeden --url")
    report = run(args)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = report["total"]["n"] - report["total"]["ok"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
