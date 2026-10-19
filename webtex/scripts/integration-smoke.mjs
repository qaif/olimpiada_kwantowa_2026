#!/usr/bin/env node
// End-to-end smoke test of the olympiad platform integration (docs/tasks/TEX-01.md) against a
// RUNNING webTEX server started with WEBTEX_INTEGRATION=true. It plays the Django side: mints SSO
// tokens, signs API requests, builds the PDF and toggles sharing. It starts nothing itself and
// creates throw-away users/projects (sub "smoke-…").
//
//   node scripts/integration-smoke.mjs <server-url> <shared-key>
//   WEBTEX_URL=http://localhost:3457 WEBTEX_SHARED_KEY=… node scripts/integration-smoke.mjs
import crypto from 'node:crypto';
import { deriveKeys, signJwt, signRequest } from '../server/integrationCrypto.js';

const base = (process.argv[2] || process.env.WEBTEX_URL || '').replace(/\/+$/, '');
const sharedKey = process.argv[3] || process.env.WEBTEX_SHARED_KEY || '';
const issuer = process.env.WEBTEX_ISSUER || 'olimpiada';
if (!base || sharedKey.length < 32) {
  console.error('usage: node scripts/integration-smoke.mjs <server-url> <shared-key (>= 32 chars)>');
  process.exit(2);
}
const { ssoKey, apiKey } = deriveKeys(sharedKey);
const origin = new URL(base).origin;

let failures = 0;
function check(ok, label, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${!ok && detail ? `  → ${detail}` : ''}`);
  if (!ok) failures++;
  return ok;
}

const run = crypto.randomBytes(4).toString('hex');
const scope = `smoke-${run}/s1`;
const projectKey = `${scope}/p1`;
const returnUrl = 'https://platform.example/me/stages/1/';

function token(over = {}) {
  const iat = Math.floor(Date.now() / 1000);
  return signJwt(
    {
      iss: issuer,
      aud: 'webtex',
      iat,
      exp: iat + 120,
      jti: crypto.randomBytes(18).toString('base64url'),
      sub: `smoke-${run}`,
      name: 'Smoke Test',
      lang: 'pl',
      project: projectKey,
      scope,
      share: false,
      title: 'Zadanie 1. Splątanie & „cudzysłowy” 100%',
      code: 'QO-SMOKE_1',
      return_url: returnUrl,
      ...over,
    },
    over.key || ssoKey,
  );
}

async function sso(jwt) {
  const res = await fetch(`${base}/api/integration/sso`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: origin },
    body: JSON.stringify({ token: jwt }),
  });
  const cookie = (res.headers.get('set-cookie') || '').match(/webtex_sid=[^;]+/)?.[0] || null;
  return { status: res.status, body: await res.json().catch(() => ({})), cookie };
}

async function signed(method, path, body) {
  const raw = body === undefined ? '' : JSON.stringify(body);
  const headers = { 'X-Webtex-Signature': signRequest({ method, path, body: raw, key: apiKey }) };
  if (raw) headers['Content-Type'] = 'application/json';
  return fetch(`${base}${path}`, { method, headers, body: raw || undefined });
}

const asUser = (cookie, method, path, body) =>
  fetch(`${base}${path}`, {
    method,
    headers: { Cookie: cookie, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });

// ---- health ----
let res = await signed('GET', '/api/integration/health');
const health = await res.json().catch(() => ({}));
check(res.status === 200 && health.ok === true, `signed health → 200 (backend: ${health.backend})`, `${res.status}`);
res = await fetch(`${base}/api/integration/health`);
check(res.status === 401, 'unsigned health → 401', `${res.status}`);

// ---- SSO ----
const first = token();
const a = await sso(first);
check(a.status === 200 && !!a.body.projectId && !!a.cookie, 'SSO → 200, projectId + session cookie', JSON.stringify(a.body));
check((await sso(first)).status === 409, 'replayed token → 409');
const iat = Math.floor(Date.now() / 1000) - 1000;
check((await sso(token({ iat, exp: iat + 120 }))).status === 410, 'expired token → 410');
check((await sso(token({ key: apiKey }))).status === 400, 'token signed with the wrong key → 400');
check((await sso(token({ aud: 'other' }))).status === 400, 'wrong audience → 400');
if (!a.cookie) {
  console.error('SSO failed, cannot continue');
  process.exit(1);
}
const again = await sso(token());
check(again.status === 200 && again.body.projectId === a.body.projectId, 'second SSO reuses the same project');

res = await asUser(a.cookie, 'GET', `/api/projects/${a.body.projectId}`);
const info = await res.json();
check(info.project?.returnUrl === returnUrl, 'project has return_url');
check(info.project?.sharingLocked === true && !info.project.editToken, 'share=false → sharing locked, no tokens');
const main = info.entities?.find((e) => e.name === 'main.tex');
const tex = main ? await (await asUser(a.cookie, 'GET', `/api/projects/${a.body.projectId}/files/${main.id}`)).text() : '';
check(tex.includes('Kod uczestnika: \\texttt{QO-SMOKE\\_1}') && tex.includes('\\usepackage{braket}'), 'solution template with participant code');
const me = await (await asUser(a.cookie, 'GET', '/api/auth/me')).json();
check(me.integration === true && me.user?.external === true && me.user.email === null, '/api/auth/me reports integration, external user');

// ---- integration-mode restrictions ----
res = await fetch(`${base}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"email":"a@b.c","password":"12345678"}' });
check(res.status === 403, 'password login → 403', `${res.status}`);
check((await asUser(a.cookie, 'POST', '/api/projects', { name: 'side project' })).status === 403, 'external user cannot create projects');
check((await asUser(a.cookie, 'POST', `/api/projects/${a.body.projectId}/copy`, {})).status === 403, 'external user cannot copy projects');
check((await asUser(a.cookie, 'DELETE', `/api/projects/${a.body.projectId}`)).status === 403, 'external user cannot delete projects');
check((await asUser(a.cookie, 'POST', `/api/projects/${a.body.projectId}/tokens`, {})).status === 403, 'locked project: tokens endpoint → 403');
check((await asUser(a.cookie, 'PATCH', `/api/projects/${a.body.projectId}`, { compiler: 'lualatex' })).status === 400, 'LuaLaTeX refused');

// ---- build ----
const t0 = Date.now();
res = await signed('POST', '/api/integration/build', { sub: `smoke-${run}`, project: projectKey });
const pdf = Buffer.from(await res.arrayBuffer());
check(
  res.status === 200 && res.headers.get('content-type') === 'application/pdf' && pdf.subarray(0, 4).toString() === '%PDF',
  `build → 200 %PDF (${pdf.length} B, ${Date.now() - t0} ms, errors: ${res.headers.get('x-webtex-errors')}, build: ${res.headers.get('x-webtex-build')})`,
  `${res.status} ${pdf.subarray(0, 200).toString()}`,
);
check(res.headers.get('x-webtex-errors') === '0' && !!res.headers.get('x-webtex-build'), 'X-Webtex-Errors: 0 and X-Webtex-Build present');
res = await signed('POST', '/api/integration/build', { sub: `smoke-${run}-nobody`, project: projectKey });
check(res.status === 404 && (await res.json()).error === 'no_project', 'unknown participant → 404 no_project');
res = await fetch(`${base}/api/integration/build`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-Webtex-Signature': signRequest({ method: 'POST', path: '/api/integration/build', body: '{}', key: apiKey }) },
  body: JSON.stringify({ sub: `smoke-${run}`, project: projectKey }),
});
check(res.status === 401, 'body not matching the signature → 401', `${res.status}`);

// ---- sharing ----
res = await signed('POST', '/api/integration/sharing', { scope, enabled: true });
check(res.status === 200 && (await res.json()).projects === 1, 'enable sharing → {"projects": 1}');
const unlocked = await (await asUser(a.cookie, 'GET', `/api/projects/${a.body.projectId}`)).json();
check(unlocked.project.sharingLocked === false && !!unlocked.project.editToken, 'unlocked project exposes share links');

const b = await sso(token({ sub: `smoke-${run}-b`, name: 'Collaborator', project: `${scope}/p1`, share: true }));
res = await asUser(b.cookie, 'POST', `/api/join/${unlocked.project.editToken}`);
check(res.status === 200, 'second participant joins by link while sharing is on', `${res.status}`);
check((await asUser(b.cookie, 'GET', `/api/projects/${a.body.projectId}`)).status === 200, 'collaborator can open the project');

res = await signed('POST', '/api/integration/sharing', { scope, enabled: false });
check(res.status === 200 && (await res.json()).projects === 2, 'disable sharing → {"projects": 2} (both participants\' projects)');
check((await asUser(b.cookie, 'GET', `/api/projects/${a.body.projectId}`)).status === 404, 'collaborator loses access immediately');
res = await asUser(b.cookie, 'POST', `/api/join/${unlocked.project.editToken}`);
const joinBody = await res.json().catch(() => ({}));
check(res.status === 403 && joinBody.error === 'Udostępnianie wyłączone przez organizatora', 'join while locked → 403 with the organiser message', `${res.status} ${JSON.stringify(joinBody)}`);
res = await signed('POST', '/api/integration/sharing', { scope, enabled: false });
check(res.status === 200 && (await res.json()).projects === 0, 'disabling again changes nothing');

console.log(failures ? `\n${failures} check(s) FAILED` : '\nAll checks passed');
process.exit(failures ? 1 : 0);
