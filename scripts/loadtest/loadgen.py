#!/usr/bin/env python3
"""Generator ruchu dnia zawodów IQO (PERF-01, docs/tasks/PERF-01.md, docs/OPERACJE.md § 42).

Scenariusz, nie pojedynczy adres: wirtualni uczniowie logują się w oknie przed startem etapu,
w chwili T0 wszyscy naraz otwierają panel i PDF-y treści, a potem przez resztę przebiegu robią to,
co robi przeglądarka ucznia w czasie zawodów – czat odpytuje wątek co 15 s, test online zapisuje
odpowiedzi co 20 s, część uczniów wgrywa skany rozwiązań po kilka MB, co jakiś czas ktoś przeładowuje
panel. Obok nich koordynatorzy (pulpit, postęp etapu, wyniki testu, eksport CSV) i anonimowy ruch
na stronach publicznych (strona główna, aktualności, wyniki – trafienia i chybienia cache'u stron).

Zależności: wyłącznie biblioteka standardowa i ``httpx`` (już w obrazie aplikacji jako zależność
przechodnia – nic się nie instaluje). Uruchamia go ``scripts/loadtest/run.sh`` w kontenerze
``loadgen`` stosu testowego; samodzielnie::

    python loadgen.py --base http://proxy --manifest /out/manifest.json --profile stage-open \\
        --students 300 --login-window 60 --burst 30 --duration 240 --out /out/run-300

**Bezpiecznik hosta.** Generator strzela wyłącznie w hosty lokalne (``proxy``, ``web``, ``localhost``,
``127.0.0.1``, ``*.localhost``, ``*.test``, ``*.local``). Każdy inny wymaga ``--allow-remote-host``
z **tą samą** nazwą i dodatkowo ogranicza liczbę uczniów (``--remote-max-students``) – procedura
z § 42.6, za osobną zgodą właściciela serwisu. Domeny i adres produkcji są odrzucane zawsze.

Wynik w katalogu ``--out``: ``requests.csv`` (każde żądanie), ``summary.csv`` i ``summary.md``
(p50/p95/p99, błędy, przepustowość per adres i faza), ``timeline.csv`` (10-sekundowe kubełki).
"""

# Losowość generatora ruchu to rozkład zachowań wirtualnych uczniów, nie kryptografia.
# ruff: noqa: S311

from __future__ import annotations

import argparse
import asyncio
import csv
import http.cookiejar
import json
import math
import os
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# --- bezpiecznik hosta ------------------------------------------------------------------------------

LOCAL_HOSTS = {"proxy", "web", "localhost", "127.0.0.1", "::1", "host.docker.internal"}
LOCAL_SUFFIXES = (".localhost", ".test", ".local")
#: Produkcja – nigdy, także z ``--allow-remote-host`` (test na produkcji nie jest procedurą § 42.6).
FORBIDDEN = ("olimpiadakwantowa.pl", "iqo-official.org", "169.58.242.197", "qaif.org")


def check_target(base: str, allow_remote: str) -> bool:
    """``True`` dla hosta lokalnego, ``False`` dla zatwierdzonego zdalnego; inaczej ``SystemExit``."""
    host = (urlsplit(base).hostname or "").lower()
    if any(host == f or host.endswith("." + f) for f in FORBIDDEN):
        raise SystemExit(f"loadgen: {host} to produkcja – generator nigdy tam nie strzela (§ 42.6).")
    if host in LOCAL_HOSTS or host.endswith(LOCAL_SUFFIXES):
        return True
    if allow_remote and allow_remote.lower() == host:
        return False
    raise SystemExit(
        f"loadgen: host {host!r} nie jest lokalny. Test poza stosem lokalnym wyłącznie za zgodą "
        "właściciela serwisu i z --allow-remote-host <ta sama nazwa> (docs/OPERACJE.md § 42.6)."
    )


# --- pomiar -----------------------------------------------------------------------------------------


@dataclass
class Sample:
    name: str
    phase: str
    start: float
    latency_ms: float
    status: int
    nbytes: int
    error: str = ""
    cache: str = ""


@dataclass
class Recorder:
    t0: float
    samples: list[Sample] = field(default_factory=list)
    t_stage: float = 0.0
    burst_end: float = 0.0
    aborted: str = ""

    def phase(self, now: float) -> str:
        if now < self.t_stage:
            return "login"
        if now < self.burst_end:
            return "burst"
        return "steady"

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)


def percentile(values: list[float], pct: float) -> float:
    """Najbliższa ranga (bez interpolacji) – wynik jest jednym z pomiarów, jak w djcms_bench.py."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct * len(ordered) / 100))
    return ordered[min(rank, len(ordered)) - 1]


def is_error(sample: Sample) -> bool:
    return bool(sample.error) or sample.status >= 500 or sample.status in (0, 429)


# --- wirtualny klient -------------------------------------------------------------------------------

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
POLL_RE = re.compile(r'hx-get="([^"]*fragment=messages[^"]*)"')
EVERY_RE = re.compile(r'hx-trigger="every (\d+)s, chat-visible')
QUESTION_RE = re.compile(r'name="q(\d+)"[^>]*value="(\d+)"')


class VirtualUser:
    """Jedna przeglądarka: własne ciasteczka i własny adres IP, wspólna pula połączeń (jak Caddy)."""

    def __init__(self, gen: Generator, ip: str, label: str):
        self.gen = gen
        self.ip = ip
        self.label = label
        self.cookies: dict[str, str] = {}

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"X-Loadtest-Client-IP": self.ip, "User-Agent": f"olimpiada-loadgen/{self.label}"}
        headers.update(self.gen.extra_headers)
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if extra:
            headers.update(extra)
        return headers

    def _store_cookies(self, response: httpx.Response) -> None:
        for raw in response.headers.get_list("set-cookie"):
            pair = raw.split(";", 1)[0]
            name, _, value = pair.partition("=")
            if "max-age=0" in raw.lower() or "expires=thu, 01 jan 1970" in raw.lower():
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value.strip()

    async def request(
        self, name: str, method: str, path: str, *, expect=(200,), **kwargs
    ) -> httpx.Response | None:
        gen = self.gen
        if gen.stopping:
            return None
        headers = self._headers(kwargs.pop("headers", None))
        if method != "GET" and "csrftoken" in self.cookies:
            headers.setdefault("X-CSRFToken", self.cookies["csrftoken"])
            headers.setdefault("Referer", gen.base + "/")
        start = time.monotonic()
        status, nbytes, error, cache, response = 0, 0, "", "", None
        try:
            response = await gen.client.request(method, gen.base + path, headers=headers, **kwargs)
            body = response.content
            nbytes = len(body)
            status = response.status_code
            cache = response.headers.get("x-page-cache", "")
            self._store_cookies(response)
            if status not in expect:
                error = f"unexpected {status}"
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        latency = (time.monotonic() - start) * 1000
        gen.rec.add(
            Sample(name, gen.rec.phase(start), start - gen.rec.t0, latency, status, nbytes, error, cache)
        )
        return response if not error else None

    async def login(self, email: str, password: str) -> bool:
        page = await self.request("GET /login/", "GET", "/login/")
        if page is None:
            return False
        match = CSRF_RE.search(page.text)
        data = {
            "username": email,
            "password": password,
            "csrfmiddlewaretoken": match.group(1) if match else "",
        }
        response = await self.request("POST /login/", "POST", "/login/", data=data, expect=(302,))
        return response is not None


# --- scenariusze ------------------------------------------------------------------------------------


def fake_pdf(size: int) -> bytes:
    """Skan rozwiązania: nagłówek PDF i losowe bajty (kompresja nie zmniejszy go po drodze)."""
    head = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n4 0 obj<</Length " + str(size).encode() + b">>stream\n"
    return head + os.urandom(size) + b"\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class Generator:
    def __init__(self, args, manifest: dict):
        self.args = args
        self.m = manifest
        self.base = args.base.rstrip("/")
        self.stopping = False
        self.rec = Recorder(t0=time.monotonic())
        self.client: httpx.AsyncClient | None = None
        self.uploads = {mb: fake_pdf(mb * 1024 * 1024) for mb in (1, 2, 3, 5)}
        self.counters: dict[str, int] = defaultdict(int)
        self.extra_headers = dict(item.split(":", 1) for item in args.header if ":" in item)

    # wspólne

    async def think(self, low: float, high: float) -> None:
        await asyncio.sleep(random.uniform(low, high) * self.args.think_scale)

    def until_end(self) -> bool:
        return not self.stopping and time.monotonic() < self.end

    async def sleep_until(self, moment: float) -> None:
        delay = moment - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    # uczeń

    async def student(self, index: int, login_at: float, open_at: float) -> None:
        info = self.m["students"][index]
        vu = VirtualUser(self, self._ip(index), f"student-{index}")
        await self.sleep_until(login_at)
        if self.stopping:
            return
        # Przed startem: strona główna (często z linku), logowanie, panel.
        await vu.request("GET / (anon)", "GET", "/")
        if not await vu.login(info["email"], self.m["password"]):
            self.counters["login_failed"] += 1
            return
        await vu.request("GET /me/", "GET", "/me/")
        # T0: wszyscy naraz przeładowują panel i pobierają treści zadań.
        await self.sleep_until(open_at)
        await vu.request("GET /me/ (T0)", "GET", "/me/")
        for problem_id in self.m["problem_ids"]:
            await vu.request(
                "GET statement PDF", "GET", f"/api/competitions/problems/{problem_id}/statement/"
            )
        quiz_mode = random.random() < self.args.quiz_share
        tasks = [asyncio.create_task(self._chat_loop(vu, info.get("conversation_id")))]
        if quiz_mode:
            tasks.append(asyncio.create_task(self._quiz_loop(vu)))
        else:
            tasks.append(asyncio.create_task(self._written_loop(vu)))
        await asyncio.gather(*tasks)

    async def _chat_loop(self, vu: VirtualUser, conversation_id) -> None:
        if not conversation_id or random.random() > self.args.chat_share:
            return
        await self.think(5, 60)
        page = await vu.request("GET chat thread", "GET", f"/me/messages/{conversation_id}/")
        if page is None:
            return
        match = POLL_RE.search(page.text)
        poll = (
            match.group(1).replace("&amp;", "&")
            if match
            else f"/me/messages/{conversation_id}/?fragment=messages"
        )
        # Odstęp z wyrenderowanego ``hx-trigger`` – ten sam, którego użyłaby przeglądarka
        # (``CHAT_POLL_SECONDS`` serwera, domyślnie 15 s).
        every = EVERY_RE.search(page.text)
        interval = int(every.group(1)) if every else 15
        while self.until_end():
            await asyncio.sleep(interval)
            await vu.request("GET chat poll", "GET", poll, expect=(200, 204), headers={"HX-Request": "true"})

    async def _quiz_loop(self, vu: VirtualUser) -> None:
        stage = self.m["quiz_stage_id"]
        await self.think(0, 20)
        start = await vu.request("GET quiz start", "GET", f"/me/stages/{stage}/test/")
        if start is None:
            return
        token = CSRF_RE.search(start.text)
        response = await vu.request(
            "POST quiz start",
            "POST",
            f"/me/stages/{stage}/test/",
            data={"csrfmiddlewaretoken": token.group(1) if token else ""},
            expect=(302,),
        )
        if response is None:
            return
        attempt_path = response.headers.get("location", "")
        sheet = await vu.request("GET quiz attempt", "GET", attempt_path)
        if sheet is None:
            return
        options: dict[str, list[str]] = defaultdict(list)
        for question, option in QUESTION_RE.findall(sheet.text):
            options[question].append(option)
        answers: dict[str, dict] = {}
        autosave = attempt_path.rstrip("/") + "/zapis/"
        while self.until_end():
            await asyncio.sleep(20)
            # Między zapisami uczeń zaznacza kolejne odpowiedzi – ładunek rośnie jak w prawdziwym teście.
            for question in random.sample(list(options), k=min(2, len(options))):
                answers[question] = {"options": [random.choice(options[question])]}
            await vu.request(
                "POST quiz autosave",
                "POST",
                autosave,
                content=json.dumps({"answers": answers}),
                headers={"Content-Type": "application/json"},
            )

    async def _written_loop(self, vu: VirtualUser) -> None:
        stage = self.m["written_stage_id"]
        numbers = list(self.m["problem_numbers"])
        remaining = self.end - time.monotonic()
        # Wysyłki rozłożone po przebiegu, z zagęszczeniem pod koniec (jak przed terminem).
        upload_times = sorted(
            time.monotonic() + remaining * (1 - random.random() ** self.args.upload_skew)
            for _ in numbers
            if random.random() < self.args.upload_share
        )
        next_reload = time.monotonic() + random.uniform(60, 240) * self.args.think_scale
        while self.until_end():
            now = time.monotonic()
            if upload_times and now >= upload_times[0]:
                upload_times.pop(0)
                number = random.choice(numbers)
                size = random.choice(self.args.upload_mb)
                await vu.request(
                    "POST upload",
                    "POST",
                    f"/me/stages/{stage}/problems/{number}/upload/",
                    data={"confirmed": "on", "csrfmiddlewaretoken": vu.cookies.get("csrftoken", "")},
                    files={"file": (f"solution-{number}.pdf", self.uploads[size], "application/pdf")},
                    headers={"HX-Request": "true", "HX-Target": f"problem-{number}"},
                )
                continue
            if now >= next_reload:
                await vu.request("GET /me/ (reload)", "GET", "/me/")
                next_reload = now + random.uniform(120, 300) * self.args.think_scale
            await asyncio.sleep(1)

    # koordynator

    async def coordinator(self, index: int) -> None:
        email = self.m["coordinators"][index % len(self.m["coordinators"])]
        vu = VirtualUser(self, f"10.250.0.{index + 1}", f"coordinator-{index}")
        await self.sleep_until(self.rec.t0 + random.uniform(0, 10))
        if not await vu.login(email, self.m["password"]):
            self.counters["login_failed"] += 1
            return
        written, quiz = self.m["written_stage_id"], self.m["quiz_stage_id"]
        pages = [
            ("GET /coordinator/", "/coordinator/", 4),
            ("GET coordinator stage progress", f"/coordinator/stages/{written}/progress/", 3),
            ("GET coordinator quiz results", f"/coordinator/stages/{quiz}/quiz/results/", 2),
            ("GET coordinator search", "/coordinator/search/?q=lt-student-001", 1),
            ("GET export participants CSV", "/coordinator/export/participants/csv/", 1),
        ]
        weights = [w for *_, w in pages]
        while self.until_end():
            name, path, _ = random.choices(pages, weights=weights)[0]
            await vu.request(name, "GET", path)
            await self.think(20, 60)

    # gość

    async def anonymous(self) -> None:
        """Strumień gości o stałym tempie (proces Poissona) – strony publiczne, cache stron."""
        # Tempo dzielone między procesy generatora (``--shards``): razem dają zadane ``--anon-rps``.
        rate = self.args.anon_rps / self.args.shards
        if rate <= 0:
            return
        results = self.m["results_stage_id"]
        news = [f"/aktualnosci/loadtest-news-{n}/" for n in range(1, 25)]
        pages = [
            ("GET / (anon)", "/", 30),
            ("GET /aktualnosci/", "/aktualnosci/", 8),
            ("GET news page", None, 10),
            ("GET /wyniki/", "/wyniki/", 6),
            ("GET /results/<id>/ (3000 rows)", f"/results/{results}/", 6),
            ("GET /harmonogram/", "/harmonogram/", 4),
            # Link z newslettera/mediów społecznościowych: parametr spoza ``?page=`` = BYPASS cache'u.
            ("GET /?utm_source=… (anon)", "/?utm_source=newsletter&utm_medium=email", 6),
            ("GET /aktualnosci/?page=N", None, 3),
        ]
        weights = [w for *_, w in pages]
        tasks = set()
        visitor = 0
        while self.until_end():
            await asyncio.sleep(random.expovariate(rate))
            name, path, _ = random.choices(pages, weights=weights)[0]
            if path is None:
                path = (
                    random.choice(news)
                    if name == "GET news page"
                    else f"/aktualnosci/?page={random.randint(1, 3)}"
                )
            visitor += 1
            vu = VirtualUser(
                self, f"10.{200 + self.args.shard}.{visitor // 250 % 250}.{visitor % 250 + 1}", "anon"
            )
            task = asyncio.create_task(vu.request(name, "GET", path, expect=(200, 404)))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _ip(self, index: int) -> str:
        """Adres wirtualnego ucznia. ``--students-per-ip`` > 1 symuluje szkołę za jednym NAT-em."""
        group = index // max(1, self.args.students_per_ip)
        return f"10.{100 + group // 62500 % 100}.{group // 250 % 250}.{group % 250 + 1}"

    # nadzór

    async def watchdog(self) -> None:
        """Kryteria przerwania (§ 42.6): odsetek błędów albo p95 w ostatnich 30 s ponad progiem."""
        while not self.stopping and time.monotonic() < self.end:
            await asyncio.sleep(5)
            now = time.monotonic() - self.rec.t0
            recent = [s for s in self.rec.samples[-5000:] if s.start >= now - 30]
            if len(recent) < 50:
                continue
            errors = sum(1 for s in recent if is_error(s)) / len(recent)
            p95 = percentile([s.latency_ms for s in recent], 95)
            print(
                f"[{now:6.0f}s] {len(recent) / 30:6.1f} req/s  p95 {p95:7.0f} ms  błędy {errors:5.1%}",
                flush=True,
            )
            if errors > self.args.abort_error_rate or p95 > self.args.abort_p95_ms:
                self.rec.aborted = f"t={now:.0f}s błędy={errors:.1%} p95={p95:.0f}ms"
                print(f"PRZERWANIE: {self.rec.aborted}", flush=True)
                self.stopping = True

    async def run(self) -> None:
        args = self.args
        limits = httpx.Limits(max_connections=args.pool, max_keepalive_connections=args.pool)
        timeout = httpx.Timeout(args.timeout, connect=10.0, pool=args.timeout)
        # Wspólny klient (pula połączeń jak Caddy → gunicorn), ale BEZ wspólnego słoika ciasteczek:
        # httpx domyślnie zapamiętuje Set-Cookie na kliencie i dokleja je do każdego następnego
        # żądania – sesja pierwszego zalogowanego ucznia trafiałaby wtedy do wszystkich gości i uczniów.
        # Ciasteczka trzyma każdy ``VirtualUser`` sam; słoik klienta odrzuca wszystko.
        no_cookies = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        async with httpx.AsyncClient(
            limits=limits, timeout=timeout, follow_redirects=False, cookies=no_cookies
        ) as client:
            self.client = client
            # Wspólny początek wszystkich procesów generatora (``--start-at``, czas ścienny): T0 ma być
            # jedną chwilą dla wszystkich uczniów, a nie osobną dla każdego procesu.
            start = time.monotonic() + max(0.0, (args.start_at or time.time()) - time.time())
            self.end = start + 1.0
            await self.sleep_until(start)
            self.rec.t0 = start
            self.rec.t_stage = start + args.login_window
            self.rec.burst_end = self.rec.t_stage + args.burst + 30
            self.end = self.rec.t_stage + args.duration
            students = min(args.students, len(self.m["students"]))
            jobs = [asyncio.create_task(self.watchdog()), asyncio.create_task(self.anonymous())]
            for i in range(students):
                if i % args.shards != args.shard:
                    continue
                # Losowanie per uczeń (ziarno z numeru), więc harmonogram nie zależy od liczby procesów.
                rnd = random.Random(args.seed * 100_003 + i)
                login_at = start + rnd.uniform(0, args.login_window)
                open_at = self.rec.t_stage + rnd.uniform(0, args.burst)
                jobs.append(asyncio.create_task(self.student(i, login_at, open_at)))
            for i in range(args.coordinators):
                if i % args.shards == args.shard:
                    jobs.append(asyncio.create_task(self.coordinator(i)))
            await asyncio.gather(*jobs)


# --- raport -----------------------------------------------------------------------------------------


def summarise(samples: list[Sample], duration_s: float) -> list[dict]:
    groups: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for s in samples:
        groups[(s.name, s.phase)].append(s)
        groups[(s.name, "all")].append(s)
        groups[("TOTAL", s.phase)].append(s)
        groups[("TOTAL", "all")].append(s)
    rows = []
    for (name, phase), items in sorted(groups.items()):
        ok = [s.latency_ms for s in items if not is_error(s)]
        lat = [s.latency_ms for s in items]
        span = (
            max(1.0, max(s.start for s in items) - min(s.start for s in items))
            if phase != "all"
            else duration_s
        )
        hits = sum(1 for s in items if s.cache == "HIT")
        cacheable = sum(1 for s in items if s.cache)
        rows.append(
            {
                "endpoint": name,
                "phase": phase,
                "requests": len(items),
                "errors": sum(1 for s in items if is_error(s)),
                "error_rate": round(sum(1 for s in items if is_error(s)) / len(items), 4),
                "rps": round(len(items) / span, 2),
                "p50_ms": round(percentile(lat, 50)),
                "p95_ms": round(percentile(lat, 95)),
                "p99_ms": round(percentile(lat, 99)),
                "max_ms": round(max(lat)),
                "p95_ok_ms": round(percentile(ok, 95)) if ok else "",
                "mb": round(sum(s.nbytes for s in items) / 1e6, 1),
                "cache_hit": f"{hits}/{cacheable}" if cacheable else "",
                "statuses": " ".join(
                    f"{code}:{n}"
                    for code, n in sorted(
                        _count(s.status if not s.error or s.status else s.error for s in items).items(),
                        key=lambda kv: str(kv[0]),
                    )
                ),
            }
        )
    return rows


def _count(values) -> dict:
    counts: dict = defaultdict(int)
    for value in values:
        counts[value] += 1
    return counts


def timeline(samples: list[Sample], bucket: int = 10) -> list[dict]:
    buckets: dict[int, list[Sample]] = defaultdict(list)
    for s in samples:
        buckets[int(s.start // bucket)].append(s)
    rows = []
    for key in sorted(buckets):
        items = buckets[key]
        lat = [s.latency_ms for s in items]
        rows.append(
            {
                "t_s": key * bucket,
                "rps": round(len(items) / bucket, 1),
                "p50_ms": round(percentile(lat, 50)),
                "p95_ms": round(percentile(lat, 95)),
                "errors": sum(1 for s in items if is_error(s)),
            }
        )
    return rows


def write_requests(out: Path, samples: list[Sample]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_s", "phase", "endpoint", "status", "latency_ms", "bytes", "error", "page_cache"])
        for s in samples:
            writer.writerow(
                [
                    f"{s.start:.3f}",
                    s.phase,
                    s.name,
                    s.status,
                    f"{s.latency_ms:.1f}",
                    s.nbytes,
                    s.error,
                    s.cache,
                ]
            )


def read_requests(path: Path) -> list[Sample]:
    with path.open(encoding="utf-8") as handle:
        return [
            Sample(
                row["endpoint"],
                row["phase"],
                float(row["t_s"]),
                float(row["latency_ms"]),
                int(row["status"]),
                int(row["bytes"]),
                row["error"],
                row["page_cache"],
            )
            for row in csv.DictReader(handle)
        ]


def write_reports(out: Path, samples: list[Sample], args, login_failed: int = 0, aborted: str = "") -> None:
    out.mkdir(parents=True, exist_ok=True)
    duration = max((s.start for s in samples), default=1.0)
    rows = summarise(samples, duration)
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    tl = timeline(samples)
    with (out / "timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(tl[0]))
        writer.writeheader()
        writer.writerows(tl)
    lines = [
        f"# Przebieg `{args.profile}` – {args.students} uczniów, {args.coordinators} koordynatorów, "
        f"goście {args.anon_rps}/s",
        "",
        f"- okno logowania {args.login_window} s, T0 rozłożone na {args.burst} s, stan ustalony do "
        f"{args.duration} s po T0, think-scale {args.think_scale}, uczniów na IP {args.students_per_ip}",
        f"- żądań: {len(samples)}, nieudanych logowań: {login_failed}, procesów generatora: {args.shards}"
        + (f", **PRZERWANO**: {aborted}" if aborted else ""),
        "",
    ]
    for phase in ("all", "login", "burst", "steady"):
        chosen = [r for r in rows if r["phase"] == phase]
        if not chosen:
            continue
        lines += [
            f"## Faza `{phase}`",
            "",
            "| adres | żądań | req/s | p50 ms | p95 ms | p99 ms | maks. ms | błędy | cache HIT | MB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(chosen, key=lambda r: (r["endpoint"] != "TOTAL", r["endpoint"])):
            lines.append(
                f"| {r['endpoint']} | {r['requests']} | {r['rps']} | {r['p50_ms']} | {r['p95_ms']} "
                f"| {r['p99_ms']} | {r['max_ms']} | {r['errors']} ({r['error_rate']:.1%}) "
                f"| {r['cache_hit']} | {r['mb']} |"
            )
        lines.append("")
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


# --- wejście ----------------------------------------------------------------------------------------

PROFILES = {
    # nazwa: (uczniów, okno logowania s, rozłożenie T0 s, czas po T0 s, goście/s, koordynatorów)
    "smoke": (5, 5, 5, 30, 1.0, 1),
    "stage-open-300": (300, 120, 30, 300, 3.0, 3),
    "stage-open-1000": (1000, 180, 60, 300, 5.0, 5),
    "stage-open-3000": (3000, 300, 60, 300, 8.0, 5),
    "steady-1000": (1000, 120, 120, 600, 3.0, 5),
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://proxy")
    parser.add_argument("--manifest", default="/out/manifest.json")
    parser.add_argument("--profile", default="smoke", choices=sorted(PROFILES))
    parser.add_argument("--students", type=int)
    parser.add_argument("--login-window", type=float, help="s: logowania rozłożone równomiernie przed T0")
    parser.add_argument("--burst", type=float, help="s: rozłożenie wejść na panel w chwili T0")
    parser.add_argument("--duration", type=float, help="s: czas trwania po T0")
    parser.add_argument("--anon-rps", type=float, help="goście stron publicznych na sekundę")
    parser.add_argument("--coordinators", type=int)
    parser.add_argument("--quiz-share", type=float, default=0.4, help="odsetek uczniów piszących test online")
    parser.add_argument("--chat-share", type=float, default=0.5, help="odsetek uczniów z otwartym czatem")
    parser.add_argument(
        "--upload-share", type=float, default=0.5, help="szansa wysłania każdego zadania w przebiegu"
    )
    parser.add_argument("--upload-skew", type=float, default=2.0, help=">1 zagęszcza wysyłki pod koniec")
    parser.add_argument("--upload-mb", type=int, nargs="+", default=[1, 2, 3, 5])
    parser.add_argument("--think-scale", type=float, default=1.0, help="mnożnik przerw między czynnościami")
    parser.add_argument("--students-per-ip", type=int, default=1, help=">1: szkoła za jednym NAT-em")
    parser.add_argument("--pool", type=int, default=1000, help="połączeń do proxy (jak Caddy do gunicorna)")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--abort-error-rate", type=float, default=0.25)
    parser.add_argument("--abort-p95-ms", type=float, default=30000)
    parser.add_argument("--allow-remote-host", default="")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="NAZWA:WARTOŚĆ do każdego żądania (np. przepustka strony prac technicznych, § 42.6)",
    )
    parser.add_argument("--remote-max-students", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="/out/run")
    # Jeden proces Pythona to jeden rdzeń: przy ok. 90 żądaniach/s (3000 uczniów po T0) generator sam
    # stawał się wąskim gardłem i mierzył własną kolejkę. ``run.sh`` uruchamia N procesów naraz.
    parser.add_argument("--shards", type=int, default=1, help="liczba procesów generatora")
    parser.add_argument("--shard", type=int, default=0, help="numer tego procesu (0..shards-1)")
    parser.add_argument(
        "--start-at", type=float, default=0.0, help="wspólny start (epoch s) wszystkich procesów"
    )
    parser.add_argument("--merge", default="", help="scal wyniki shard-*/ z katalogu i zakończ")
    args = parser.parse_args(argv)
    students, window, burst, duration, anon, coords = PROFILES[args.profile]
    for name, default in (
        ("students", students),
        ("login_window", window),
        ("burst", burst),
        ("duration", duration),
        ("anon_rps", anon),
        ("coordinators", coords),
    ):
        if getattr(args, name) is None:
            setattr(args, name, default)
    return args


def merge(args) -> int:
    """Raport łączny z katalogów ``shard-*`` (po przebiegu wieloprocesowym)."""
    root = Path(args.merge)
    samples: list[Sample] = []
    login_failed, aborted = 0, []
    for shard_dir in sorted(root.glob("shard-*")):
        samples.extend(read_requests(shard_dir / "requests.csv"))
        meta = json.loads((shard_dir / "meta.json").read_text(encoding="utf-8"))
        login_failed += meta.get("login_failed", 0)
        if meta.get("aborted"):
            aborted.append(f"{shard_dir.name}: {meta['aborted']}")
        args.shards = meta.get("shards", args.shards)
    samples.sort(key=lambda s: s.start)
    write_requests(root, samples)
    write_reports(root, samples, args, login_failed, "; ".join(aborted))
    return 2 if aborted else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.merge:
        return merge(args)
    local = check_target(args.base, args.allow_remote_host)
    if not local and args.students > args.remote_max_students:
        raise SystemExit(
            f"loadgen: poza stosem lokalnym najwyżej {args.remote_max_students} uczniów (§ 42.6)."
        )
    random.seed(args.seed * 1000 + args.shard)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    gen = Generator(args, manifest)
    print(
        f"loadgen: {args.profile} → {args.base}: {args.students} uczniów, {args.coordinators} koord., "
        f"goście {args.anon_rps}/s, okno logowania {args.login_window} s, T0 +{args.burst} s, "
        f"po T0 {args.duration} s",
        flush=True,
    )
    asyncio.run(gen.run())
    out = Path(args.out) / f"shard-{args.shard}" if args.shards > 1 else Path(args.out)
    write_requests(out, gen.rec.samples)
    (out / "meta.json").write_text(
        json.dumps(
            {"shards": args.shards, "login_failed": gen.counters["login_failed"], "aborted": gen.rec.aborted}
        ),
        encoding="utf-8",
    )
    write_reports(out, gen.rec.samples, args, gen.counters["login_failed"], gen.rec.aborted)
    return 2 if gen.rec.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
