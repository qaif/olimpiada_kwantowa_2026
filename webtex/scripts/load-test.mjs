#!/usr/bin/env node
// Load test of the compile queue (docs/OPERACJE.md § 55.6): the last hour before a submission
// deadline, against a RUNNING webTEX in integration mode (normally the compose service with the real
// TeX Live image). It plays both sides: the platform (SSO tokens, signed POST /api/integration/build,
// i.e. „Złóż PDF z edytora”) and the participants' browsers (interactive compiles with the session
// cookie). It creates throw-away users/projects (sub "load-…") and reports latency percentiles,
// refusals, failures, the queue's own counters and the container's peak memory/PIDs (`docker stats`).
//
//   node scripts/load-test.mjs --url http://localhost:3002 --users 50 [options]
//   WEBTEX_SHARED_KEY=… (>= 32 chars, the server's key) is required.
//
// Options:
//   --users N         participants, each with an own project (default 50)
//   --rounds R        interactive compiles per participant, one after another like a browser tab
//                     (default 3); M = N × R interactive compiles
//   --builds B        submission builds, fired at random moments of the first --spread seconds
//                     (default = N: everybody submits once)
//   --spread S        seconds over which participants start and builds are fired (default 20)
//   --build-parallel P  at most P builds in flight (default 16): the platform sends them from its
//                     gunicorn threads (WEB_WORKERS × WEB_THREADS = 4 × 4), so no more can be open
//   --heavy F         share of projects with a heavier document (TikZ, quantikz, 6 pages; default 0.3)
//   --think MS        max pause between a participant's compiles (default 2000)
//   --container NAME  container for `docker stats` (default: auto, the one publishing the url's port)
//   --json FILE       also write the summary as JSON
import crypto from 'node:crypto';
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import { promisify } from 'node:util';
import { deriveKeys, signJwt, signRequest } from '../server/integrationCrypto.js';

const run = promisify(execFile);

function args() {
  const out = {};
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    const m = /^--([\w-]+)$/.exec(argv[i]);
    if (!m) throw new Error(`unknown argument ${argv[i]}`);
    out[m[1]] = argv[i + 1];
    i++;
  }
  return out;
}

const opt = args();
const base = String(opt.url || process.env.WEBTEX_URL || 'http://localhost:3001').replace(/\/+$/, '');
const sharedKey = process.env.WEBTEX_SHARED_KEY || '';
const issuer = process.env.WEBTEX_ISSUER || 'olimpiada';
const users = Number(opt.users ?? 50);
const rounds = Number(opt.rounds ?? 3);
const builds = Number(opt.builds ?? users);
const spreadMs = Number(opt.spread ?? 20) * 1000;
const heavyShare = Number(opt.heavy ?? 0.3);
const thinkMs = Number(opt.think ?? 2000);
const buildParallel = Number(opt['build-parallel'] ?? 16);
if (sharedKey.length < 32) {
  console.error('WEBTEX_SHARED_KEY (>= 32 chars, the same as the server) is required');
  process.exit(2);
}
const { ssoKey, apiKey } = deriveKeys(sharedKey);
const origin = new URL(base).origin;
const runId = crypto.randomBytes(3).toString('hex');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const HEAVY = String.raw`\documentclass[11pt]{article}
\usepackage[T1]{fontenc}
\usepackage[polish]{babel}
\usepackage{lmodern,amsmath,amssymb,physics,braket,tikz,quantikz,lipsum}
\usepackage[a4paper,margin=2.5cm]{geometry}
\usepackage{hyperref}
\begin{document}
\section*{Rozwiązanie}
\begin{quantikz}
\lstick{$\ket{0}$} & \gate{H} & \ctrl{1} & \gate{R_z(\theta)} & \meter{} \\
\lstick{$\ket{0}$} & \qw & \targ{} & \gate{H} & \meter{}
\end{quantikz}
\foreach \n in {1,...,6}{%
\subsection*{Część \n}
\lipsum[1-4]
\begin{equation}
  \ket{\psi_{\n}} = \frac{1}{\sqrt{2}}\left(\ket{0} + e^{i\pi/\n}\ket{1}\right),\qquad
  \expval{\sigma_z}{\psi_{\n}} = \int_0^{\n} \sin^2 x \,\dd x .
\end{equation}
\begin{center}\begin{tikzpicture}
  \draw[->] (0,0) -- (4,0); \draw[->] (0,0) -- (0,2.5);
  \draw[domain=0:4,smooth,variable=\x,blue] plot ({\x},{1+sin(\n*\x r)});
\end{tikzpicture}\end{center}
\clearpage}
\end{document}
`;

// ---------- the two sides ----------

function token(i) {
  const iat = Math.floor(Date.now() / 1000);
  return signJwt(
    {
      iss: issuer,
      aud: 'webtex',
      iat,
      exp: iat + 120,
      jti: crypto.randomBytes(18).toString('base64url'),
      sub: `load-${runId}-${i}`,
      name: `Load ${i}`,
      lang: 'pl',
      project: `load-${runId}/p${i}`,
      scope: `load-${runId}`,
      share: false,
      title: `Zadanie testowe ${i}`,
      code: `QO-LOAD-${i}`,
      return_url: 'https://platform.example/',
    },
    ssoKey,
  );
}

async function signed(method, path, body) {
  const raw = body === undefined ? '' : JSON.stringify(body);
  const headers = { 'X-Webtex-Signature': signRequest({ method, path, body: raw, key: apiKey }) };
  if (raw) headers['Content-Type'] = 'application/json';
  return fetch(`${base}${path}`, { method, headers, body: raw || undefined, signal: AbortSignal.timeout(120_000) });
}

async function setupParticipant(i) {
  const res = await fetch(`${base}/api/integration/sso`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify({ token: token(i) }),
  });
  const body = await res.json().catch(() => ({}));
  const cookie = (res.headers.get('set-cookie') || '').match(/webtex_sid=[^;]+/)?.[0];
  if (res.status !== 200 || !cookie) throw new Error(`SSO of participant ${i}: HTTP ${res.status} ${JSON.stringify(body)}`);
  const heavy = i < Math.round(users * heavyShare);
  if (heavy) {
    const form = new FormData();
    form.append('files', new Blob([HEAVY], { type: 'text/x-tex' }), 'main.tex');
    const up = await fetch(`${base}/api/projects/${body.projectId}/upload`, { method: 'POST', headers: { Cookie: cookie }, body: form });
    if (!up.ok) throw new Error(`upload of participant ${i}: HTTP ${up.status}`);
  }
  return { i, cookie, projectId: body.projectId, heavy };
}

const samples = { interactive: [], build: [] };
function record(kind, entry) {
  samples[kind].push(entry);
}

async function interactiveCompile(p) {
  const started = performance.now();
  try {
    const res = await fetch(`${base}/api/projects/${p.projectId}/compile`, {
      method: 'POST',
      headers: { Cookie: p.cookie, 'Content-Type': 'application/json' },
      body: '{}',
      signal: AbortSignal.timeout(300_000),
    });
    const body = await res.json().catch(() => ({}));
    const ms = performance.now() - started;
    if (res.status === 200) {
      const ok = body.status === 'success' && body.hasPdf;
      record('interactive', { ms, outcome: ok ? 'ok' : `compile_${body.status}`, queuedMs: body.queuedMs ?? null, durationMs: body.durationMs ?? null, heavy: p.heavy });
      if (!ok && body.status !== 'success') p.lastError = body.message || body.issues?.[0]?.message;
      return ok ? 'ok' : 'failed';
    }
    record('interactive', { ms, outcome: res.status === 503 || res.status === 429 ? `busy_${res.status}` : `http_${res.status}`, retryAfter: body.retryAfter });
    return res.status === 503 || res.status === 429 ? 'busy' : 'failed';
  } catch (err) {
    record('interactive', { ms: performance.now() - started, outcome: `error_${err.name}` });
    return 'failed';
  }
}

let buildsInFlight = 0;
const buildSlots = [];
async function build(p) {
  // Waiting for a free platform thread is not part of the measured latency.
  while (buildsInFlight >= buildParallel) await new Promise((r) => buildSlots.push(r));
  buildsInFlight++;
  try {
    await timedBuild(p);
  } finally {
    buildsInFlight--;
    buildSlots.shift()?.();
  }
}

async function timedBuild(p) {
  const started = performance.now();
  try {
    const res = await signed('POST', '/api/integration/build', { sub: `load-${runId}-${p.i}`, project: `load-${runId}/p${p.i}` });
    const buf = Buffer.from(await res.arrayBuffer());
    const ms = performance.now() - started;
    let outcome = `http_${res.status}`;
    if (res.status === 200 && buf.subarray(0, 5).toString() === '%PDF-') outcome = 'ok';
    else if (res.status === 503) outcome = `busy_${JSON.parse(buf.toString() || '{}').error || 503}`;
    else if (res.status === 422) outcome = 'compile_failed';
    record('build', { ms, outcome, heavy: p.heavy });
  } catch (err) {
    record('build', { ms: performance.now() - started, outcome: `error_${err.name}` });
  }
}

async function participant(p) {
  await sleep(Math.random() * spreadMs);
  for (let r = 0; r < rounds; r++) {
    const res = await interactiveCompile(p);
    // A refused browser backs off like the editor does (client/src/editor/compileBackoff.js).
    await sleep(res === 'busy' ? 5000 + Math.random() * 5000 : Math.random() * thinkMs);
  }
}

// ---------- monitoring ----------

const monitor = { mem: [], pids: [], cpu: [], queue: [], stop: false };

function parseMem(s) {
  const m = /([\d.]+)\s*([KMG]i?B|B)/.exec(s || '');
  if (!m) return null;
  const mult = { B: 1, KiB: 2 ** 10, MiB: 2 ** 20, GiB: 2 ** 30, KB: 1e3, MB: 1e6, GB: 1e9 }[m[2]];
  return Number(m[1]) * mult;
}

async function findContainer() {
  if (opt.container) return opt.container;
  const port = new URL(base).port;
  const { stdout } = await run('docker', ['ps', '--format', '{{.Names}}\t{{.Ports}}']).catch(() => ({ stdout: '' }));
  const line = stdout.split('\n').find((l) => l.includes(`:${port}->`));
  return line ? line.split('\t')[0] : null;
}

async function watchDocker(name) {
  while (!monitor.stop) {
    try {
      const { stdout } = await run('docker', ['stats', '--no-stream', '--format', '{{.MemUsage}}\t{{.PIDs}}\t{{.CPUPerc}}', name], { timeout: 15000 });
      const [mem, pids, cpu] = stdout.trim().split('\t');
      monitor.mem.push(parseMem(mem.split('/')[0]));
      monitor.pids.push(Number(pids));
      monitor.cpu.push(parseFloat(cpu));
    } catch {}
    await sleep(500);
  }
}

async function watchQueue() {
  while (!monitor.stop) {
    try {
      const res = await signed('GET', '/api/integration/health');
      const body = await res.json();
      if (body.queue) monitor.queue.push({ at: Date.now(), ...body.queue });
    } catch {}
    await sleep(1000);
  }
}

// ---------- report ----------

const pct = (values, p) => {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((s.length - 1) * p))];
};
const sec = (ms) => (ms == null ? '–' : `${(ms / 1000).toFixed(1)} s`);
const max = (values) => values.filter((v) => Number.isFinite(v)).reduce((a, b) => Math.max(a, b), 0);

function summarize(kind) {
  const list = samples[kind];
  const counts = {};
  for (const s of list) counts[s.outcome] = (counts[s.outcome] || 0) + 1;
  const ok = list.filter((s) => s.outcome === 'ok');
  const queued = list.map((s) => s.queuedMs).filter((v) => v != null);
  return {
    total: list.length,
    outcomes: counts,
    okP50Ms: pct(ok.map((s) => s.ms), 0.5),
    okP95Ms: pct(ok.map((s) => s.ms), 0.95),
    okMaxMs: max(ok.map((s) => s.ms)),
    queueWaitP50Ms: pct(queued, 0.5),
    queueWaitP95Ms: pct(queued, 0.95),
    compileP50Ms: pct(list.map((s) => s.durationMs).filter((v) => v != null), 0.5),
  };
}

async function main() {
  const health = await (await signed('GET', '/api/integration/health')).json().catch(() => ({}));
  console.log(`webTEX ${base}: backend ${health.backend}, queue ${JSON.stringify(health.queue ? { concurrency: health.queue.concurrency, maxQueue: health.queue.maxQueue } : 'n/a')}`);
  const container = await findContainer();
  let restartsBefore = null;
  if (container) {
    restartsBefore = (await run('docker', ['inspect', '--format', '{{.RestartCount}}', container])).stdout.trim();
    console.log(`container ${container} (docker stats every ~2 s)`);
  } else console.log('no container found for docker stats (use --container)');

  console.log(`setting up ${users} participants (${Math.round(users * heavyShare)} with the heavy document)…`);
  const participants = [];
  for (let i = 0; i < users; i += 10) {
    participants.push(...(await Promise.all(Array.from({ length: Math.min(10, users - i) }, (_, k) => setupParticipant(i + k)))));
  }

  console.log(`rush: ${users} × ${rounds} interactive compiles + ${builds} builds over ${spreadMs / 1000} s…`);
  const watchers = [container ? watchDocker(container) : null, watchQueue()].filter(Boolean);
  const started = performance.now();
  const buildJobs = Array.from({ length: builds }, (_, k) =>
    sleep(Math.random() * spreadMs).then(() => build(participants[k % participants.length])),
  );
  await Promise.all([...participants.map(participant), ...buildJobs]);
  const wallMs = performance.now() - started;
  monitor.stop = true;
  await Promise.all(watchers);

  let oom = null;
  if (container) {
    const { stdout } = await run('docker', ['inspect', '--format', '{{.State.OOMKilled}} {{.RestartCount}}', container]);
    const [killed, restarts] = stdout.trim().split(' ');
    oom = { oomKilled: killed === 'true', restarts: Number(restarts) - Number(restartsBefore) };
  }
  const q = monitor.queue;
  const summary = {
    url: base,
    users,
    rounds,
    builds,
    buildParallel,
    spreadSec: spreadMs / 1000,
    heavyShare,
    wallSec: Math.round(wallMs / 100) / 10,
    concurrency: health.queue?.concurrency,
    maxQueue: health.queue?.maxQueue,
    interactive: summarize('interactive'),
    build: summarize('build'),
    peak: {
      memoryMiB: Math.round(max(monitor.mem) / 2 ** 20),
      pids: max(monitor.pids),
      cpuPercent: max(monitor.cpu),
      running: max(q.map((s) => s.running)),
      queued: max(q.map((s) => s.queued)),
      oldestQueuedMs: max(q.map((s) => s.oldestQueuedMs)),
    },
    queueTotals: q.at(-1) ? Object.fromEntries(['started', 'completed', 'failed', 'coalesced', 'rejected', 'expired', 'abandoned'].map((k) => [k, q.at(-1)[k]])) : null,
    container: oom,
  };

  const line = (name, s) =>
    `${name.padEnd(12)} ${String(s.total).padStart(4)}  ok p50 ${sec(s.okP50Ms)}  p95 ${sec(s.okP95Ms)}  max ${sec(s.okMaxMs)}` +
    `  | queue wait p50 ${sec(s.queueWaitP50Ms)} p95 ${sec(s.queueWaitP95Ms)} | latexmk p50 ${sec(s.compileP50Ms)}  | ${JSON.stringify(s.outcomes)}`;
  console.log(`\n=== ${users} users, concurrency ${summary.concurrency}, wall ${summary.wallSec} s ===`);
  console.log(line('interactive', summary.interactive));
  console.log(line('build', summary.build));
  console.log(
    `peak: memory ${summary.peak.memoryMiB} MiB, PIDs ${summary.peak.pids}, CPU ${summary.peak.cpuPercent}% | queue: running ${summary.peak.running}, ` +
      `queued ${summary.peak.queued}, oldest ${sec(summary.peak.oldestQueuedMs)} | totals ${JSON.stringify(summary.queueTotals)}`,
  );
  if (oom) console.log(`container: OOMKilled ${oom.oomKilled}, restarts during the run ${oom.restarts}`);
  const errors = participants.map((p) => p.lastError).filter(Boolean);
  if (errors.length) console.log(`compile errors seen (${errors.length}), first: ${errors[0]}`);
  if (opt.json) await fs.writeFile(opt.json, JSON.stringify(summary, null, 2));
  const failed = Object.entries({ ...summary.interactive.outcomes, ...summary.build.outcomes }).some(([k, n]) => n && k !== 'ok' && !k.startsWith('busy'));
  process.exitCode = failed || oom?.oomKilled || oom?.restarts ? 1 : 0;
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
