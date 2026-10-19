// AI assistant on a standalone webTEX (no platform): normal projects are unlocked, read-only
// members are refused, per-user limits, the global switch, and compile issues in the context.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { mockServer } from './llm-helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'webtex-ai-sa-'));
let llm;
let base;
let server;
let llmMod;
let cfgPath;
let release = [];

before(async () => {
  llm = await mockServer(async (req, res, rec) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (rec.json.messages.at(-1).content === 'HOLD') await new Promise((r) => release.push(r));
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: 'ok' } }] })}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  cfgPath = path.join(tmp, 'llm.json');
  fs.writeFileSync(cfgPath, JSON.stringify({ providers: [{ id: 'local', type: 'openai-compatible', baseUrl: llm.url, models: ['m'] }] }));
  Object.assign(process.env, { DATA_DIR: tmp, WEBTEX_INTEGRATION: 'false', ALLOW_REGISTRATION: 'true' });
  for (const k of ['LLM_ENABLED', 'LLM_CONFIG']) delete process.env[k];

  const express = (await import('express')).default;
  const { authRouter, loadUser } = await import('../auth.js');
  const { aiRouter } = await import('../ai.js');
  const { joinRouter, projectsRouter } = await import('../projects.js');
  llmMod = await import('../llm/index.js');
  const app = express();
  app.use(express.json());
  app.use(loadUser);
  app.use('/api/auth', authRouter);
  app.use('/api/projects', projectsRouter);
  app.use('/api/join', joinRouter);
  app.use('/api/ai', aiRouter);
  app.use((err, _req, res, _next) => res.status(err.status || 500).json({ error: err.message }));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  for (const r of release) r();
  // Loaded Y.Docs keep y-protocols' awareness interval alive until collab unloads them (60 s).
  const { getShared } = await import('../collab.js');
  if (projectId) (await getShared(projectId)).awareness.destroy();
  server?.closeAllConnections?.();
  await new Promise((r) => (server ? server.close(r) : r()));
  await llm?.close();
});

const enable = (extra = {}) =>
  llmMod.configureLlm({ LLM_ENABLED: 'true', LLM_CONFIG: cfgPath, LLM_ENV_PROVIDERS: 'false', ...extra });

async function register(email) {
  const res = await fetch(`${base}/api/auth/register`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: 'password123', name: email }) });
  assert.equal(res.status, 200);
  return res.headers.get('set-cookie').split(';')[0];
}
const call = (method, url, cookie, body) =>
  fetch(`${base}${url}`, { method, headers: { 'Content-Type': 'application/json', cookie }, body: body ? JSON.stringify(body) : undefined });
const chat = (cookie, projectId, content = 'hi', extra = {}) => call('POST', '/api/ai/chat', cookie, { projectId, providerId: 'local', model: 'm', messages: [{ role: 'user', content }], ...extra });

let owner;
let viewer;
let projectId;

test('global switch: off by default, then a normal project is unlocked for editors', async () => {
  owner = await register('owner@example.com');
  viewer = await register('viewer@example.com');
  projectId = (await (await call('POST', '/api/projects', owner, { name: 'Notatki' })).json()).id;

  llmMod.configureLlm({ LLM_CONFIG: cfgPath, LLM_ENV_PROVIDERS: 'false' });
  assert.equal((await (await call('GET', `/api/projects/${projectId}`, owner)).json()).project.ai, false);
  assert.deepEqual(await (await call('GET', '/api/ai/providers', owner)).json(), { enabled: false, reason: 'not_configured', providers: [], defaultProvider: null });
  const off = await chat(owner, projectId);
  assert.equal(off.status, 404);
  assert.equal((await off.json()).error, 'ai_not_configured');

  enable();
  assert.equal((await (await call('GET', `/api/projects/${projectId}`, owner)).json()).project.ai, true);
  const res = await chat(owner, projectId);
  assert.equal(res.status, 200);
  assert.match(await res.text(), /event: delta\ndata: \{"text":"ok"\}/);
  assert.equal((await call('GET', '/api/ai/providers', null)).status, 401, 'login required');
});

test('read-only members do not get the assistant', async () => {
  const info = await (await call('GET', `/api/projects/${projectId}`, owner)).json();
  assert.equal((await call('POST', `/api/join/${info.project.viewToken}`, viewer)).status, 200);
  const v = await (await call('GET', `/api/projects/${projectId}`, viewer)).json();
  assert.equal(v.project.role, 'viewer');
  assert.equal(v.project.ai, false);
  assert.equal((await (await call('GET', `/api/ai/providers?projectId=${projectId}`, viewer)).json()).reason, 'forbidden');
  assert.equal((await chat(viewer, projectId)).status, 403);
});

test('per-user rate limit and concurrency', async () => {
  enable({ LLM_RATE_LIMIT: '2', LLM_RATE_WINDOW: '600', LLM_MAX_CONCURRENT: '1' });
  const held = chat(owner, projectId, 'HOLD');
  await new Promise((r) => setTimeout(r, 150));
  const busy = await chat(owner, projectId);
  assert.equal(busy.status, 429);
  assert.equal((await busy.json()).error, 'busy');
  for (const r of release.splice(0)) r();
  await (await held).text();
  assert.equal((await (await chat(owner, projectId)).text()).includes('event: done'), false, 'mock sends no done event');
  const limited = await chat(owner, projectId);
  assert.equal(limited.status, 429);
  const body = await limited.json();
  assert.equal(body.error, 'rate_limited');
  assert.ok(body.retryAfter > 0 && body.retryAfter <= 600);
  assert.equal(limited.headers.get('retry-after'), String(body.retryAfter));
  enable();
});

test('compile issues come from the last build on disk, with source snippets', async () => {
  const info = await (await call('GET', `/api/projects/${projectId}`, owner)).json();
  const main = info.entities.find((e) => e.name === 'main.tex');
  const src = await (await call('GET', `/api/projects/${projectId}/files/${main.id}`, owner)).text();
  const lines = src.split('\n');
  const line = lines.findIndex((l) => l.includes('\\begin{document}')) + 2;
  const build = path.join(tmp, 'outputs', projectId, `${Date.now().toString(36)}-test`);
  fs.mkdirSync(build, { recursive: true });
  fs.writeFileSync(
    path.join(build, 'output.log'),
    `This is pdfTeX\n(./main.tex\n./main.tex:${line}: Undefined control sequence.\nl.${line} \\foo\n                \n)\n`,
  );
  fs.writeFileSync(path.join(build, 'latexmk.txt'), '');
  const res = await chat(owner, projectId, 'Napraw', { context: { fileId: main.id, includeErrors: true } });
  await res.text();
  const system = llm.requests.at(-1).json.messages[0].content;
  assert.match(system, new RegExp(`1\\. \\[error\\] main\\.tex:${line}: Undefined control sequence\\.`));
  assert.ok(system.includes(`>${line}| ${lines[line - 1]}`), 'numbered snippet with the error line marked');
  assert.ok(!system.includes('## Current file'), 'file not included unless asked');
});
