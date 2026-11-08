// Version history (server/history.js) end to end: the real routers on a throw-away data directory,
// collaborators as Yjs WebSocket clients, and a fake compiler (no TeX on the test machines).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import * as Y from 'yjs';
import * as syncProtocol from 'y-protocols/sync';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';
import { unzipSync } from 'fflate';

const SHARED = 'history-test-shared-key-0123456789abcdef';
const SCOPE = 'oq-2026/s9';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'webtex-history-'));
let base;
let server;
let mods;
let keys;
let compileOutcome = 'success';
const sockets = [];

before(async () => {
  Object.assign(process.env, {
    DATA_DIR: tmp,
    WEBTEX_INTEGRATION: 'true',
    WEBTEX_SHARED_KEY: SHARED,
    WEBTEX_ISSUER: 'olimpiada',
    HISTORY_KEEP_DAYS: '30',
  });
  const express = (await import('express')).default;
  const { config, paths } = await import('../config.js');
  const { integrationRouter } = await import('../integration.js');
  const { loadUser, userFromCookieHeader } = await import('../auth.js');
  const { projectsRouter, joinRouter, roleFor } = await import('../projects.js');
  const collab = await import('../collab.js');
  const { compilerTestHooks } = await import('../compiler.js');
  const history = await import('../history.js');
  const crypto_ = await import('../integrationCrypto.js');
  const db = await import('../db.js');
  mods = { config, paths, collab, history, db, ...crypto_ };
  keys = crypto_.deriveKeys(SHARED);
  // Automatic snapshots only where a test asks for them.
  config.history.idleSec = 3600;
  config.history.activeSec = 7200;

  compilerTestHooks.compile = async (project) => {
    if (compileOutcome !== 'success') return { status: 'failure', hasPdf: false, buildId: null, issues: [{ level: 'error', file: 'main.tex', line: 1, message: 'Undefined control sequence' }] };
    const buildId = `${Date.now().toString(36)}-${crypto.randomBytes(3).toString('hex')}`;
    fs.mkdirSync(paths.build(project.id, buildId), { recursive: true });
    fs.writeFileSync(path.join(paths.build(project.id, buildId), 'output.pdf'), '%PDF-1.5 fake');
    return { status: 'success', hasPdf: true, buildId, issues: [] };
  };

  const app = express();
  app.use('/api/integration', integrationRouter);
  app.use(express.json());
  app.use(loadUser);
  app.use('/api/projects', projectsRouter);
  app.use('/api/join', joinRouter);
  app.use((err, _req, res, _next) => res.status(err.status || 500).json({ error: err.message }));
  server = http.createServer(app);
  collab.attachCollab(server, (req, projectId) => {
    const user = userFromCookieHeader(req.headers.cookie);
    const role = user && roleFor(user.id, projectId);
    return role ? { user, role } : null;
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  for (const ws of sockets) ws.terminate();
  // Loaded Y.Docs keep y-protocols' awareness interval alive until collab unloads them (60 s).
  for (const { id } of mods?.db.all(`SELECT id FROM projects`) || []) (await mods.collab.getShared(id)).awareness.destroy();
  server?.closeAllConnections?.();
  await new Promise((r) => (server ? server.close(r) : r()));
});

// ---------- helpers ----------

let jtiCounter = 0;
async function sso(sub, name, claims = {}) {
  const iat = Math.floor(Date.now() / 1000);
  const token = mods.signJwt(
    { iss: 'olimpiada', aud: 'webtex', iat, exp: iat + 120, jti: `${crypto.randomBytes(12).toString('base64url')}${jtiCounter++}`, sub, name, project: `${SCOPE}/p1`, scope: SCOPE, share: true, title: 'Zadanie 1', code: 'QO-1', ...claims },
    keys.ssoKey,
  );
  const res = await fetch(`${base}/api/integration/sso`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
  assert.equal(res.status, 200, await res.clone().text());
  return { cookie: res.headers.get('set-cookie').split(';')[0], projectId: (await res.json()).projectId };
}
function signedPost(pathname, obj) {
  const raw = JSON.stringify(obj);
  return fetch(`${base}${pathname}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Webtex-Signature': mods.signRequest({ method: 'POST', path: pathname, body: raw, key: keys.apiKey }) },
    body: raw,
  });
}
const call = (method, url, cookie, body) =>
  fetch(`${base}${url}`, { method, headers: { cookie, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
const getJson = async (url, cookie) => {
  const res = await call('GET', url, cookie);
  assert.equal(res.status, 200, `${url}: ${await res.clone().text()}`);
  return res.json();
};
const postJson = async (url, cookie, body = {}) => {
  const res = await call('POST', url, cookie, body);
  assert.equal(res.status, 200, `${url}: ${await res.clone().text()}`);
  return res.json();
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(pred, ms = 4000, what = 'condition') {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await pred()) return;
    await sleep(20);
  }
  throw new Error(`timed out waiting for ${what}`);
}
const snapshots = (projectId) => mods.db.all(`SELECT * FROM snapshots WHERE project_id = ? ORDER BY created_at`, projectId);

/** A collaborator's editor: a Y.Doc synced over the project WebSocket (like client/src/editor/provider.js). */
async function yClient(projectId, cookie) {
  const { WebSocket } = await import('ws');
  const doc = new Y.Doc();
  const ws = new WebSocket(`${base.replace('http', 'ws')}/ws/projects/${projectId}`, { headers: { cookie } });
  sockets.push(ws);
  const events = [];
  let synced = false;
  ws.on('message', (data, isBinary) => {
    if (!isBinary) return events.push(JSON.parse(data.toString()));
    const decoder = decoding.createDecoder(new Uint8Array(data));
    if (decoding.readVarUint(decoder) !== 0) return;
    const encoder = encoding.createEncoder();
    encoding.writeVarUint(encoder, 0);
    const type = syncProtocol.readSyncMessage(decoder, encoder, doc, ws);
    if (encoding.length(encoder) > 1) ws.send(encoding.toUint8Array(encoder));
    if (type === syncProtocol.messageYjsSyncStep2) synced = true;
  });
  doc.on('update', (update, origin) => {
    if (origin === ws || ws.readyState !== ws.OPEN) return;
    const enc = encoding.createEncoder();
    encoding.writeVarUint(enc, 0);
    syncProtocol.writeUpdate(enc, update);
    ws.send(encoding.toUint8Array(enc));
  });
  await new Promise((resolve, reject) => {
    ws.once('open', resolve);
    ws.once('error', reject);
  });
  const enc = encoding.createEncoder();
  encoding.writeVarUint(enc, 0);
  syncProtocol.writeSyncStep1(enc, doc);
  ws.send(encoding.toUint8Array(enc));
  await until(() => synced, 4000, 'sync');
  const texts = doc.getMap('texts');
  return { doc, ws, events, texts, text: (id) => texts.get(id)?.toString() };
}

const serverText = async (projectId, id) => (await mods.collab.getShared(projectId)).texts.get(id)?.toString();

// ---------- the scenario ----------

let owner;
let collaborator;
let viewer;
let mainId;
let ownerUserId;

test('setup: a platform project with an editor and a viewer', async () => {
  owner = await sso('100', 'Ada Lovelace');
  ownerUserId = mods.db.one(`SELECT owner_id FROM projects WHERE id = ?`, owner.projectId).owner_id;
  const info = await getJson(`/api/projects/${owner.projectId}`, owner.cookie);
  mainId = info.entities.find((e) => e.name === 'main.tex').id;
  collaborator = await sso('101', 'Grace Hopper', { project: `${SCOPE}/p2` });
  viewer = await sso('102', 'Alan Turing', { project: `${SCOPE}/p3` });
  await postJson(`/api/join/${info.project.editToken}`, collaborator.cookie);
  await postJson(`/api/join/${info.project.viewToken}`, viewer.cookie);
  assert.deepEqual(await getJson(`/api/projects/${owner.projectId}/history`, owner.cookie), { snapshots: [], canRestore: true, keepDays: 30 });
});

test('trigger: edits that settle make one automatic snapshot, attributed to who typed', async () => {
  mods.config.history.idleSec = 0.3;
  const a = await yClient(owner.projectId, collaborator.cookie);
  try {
    for (let i = 0; i < 5; i++) {
      a.texts.get(mainId).insert(0, `% line ${i}\n`);
      await sleep(30);
    }
    assert.equal(snapshots(owner.projectId).length, 0, 'nothing while typing');
    await until(() => snapshots(owner.projectId).length === 1, 3000, 'idle snapshot');
    await sleep(500);
    assert.equal(snapshots(owner.projectId).length, 1, 'one snapshot per settled burst');
    const [s] = (await getJson(`/api/projects/${owner.projectId}/history`, owner.cookie)).snapshots;
    assert.equal(s.kind, 'auto');
    assert.deepEqual(s.authors.map((u) => u.name), ['Grace Hopper']);
    assert.ok(a.events.some((e) => e.type === 'history'), 'open editors are told to refresh the panel');
  } finally {
    a.ws.close();
    mods.config.history.idleSec = 3600;
  }
});

test('trigger: continuous editing still snapshots every HISTORY_ACTIVE_SEC', async () => {
  mods.config.history.idleSec = 0.5;
  mods.config.history.activeSec = 0.6;
  const a = await yClient(owner.projectId, owner.cookie);
  const before = snapshots(owner.projectId).length;
  try {
    const end = Date.now() + 1500;
    while (Date.now() < end) {
      a.texts.get(mainId).insert(0, '%');
      await sleep(60); // never idle for 0.5 s
    }
    assert.ok(snapshots(owner.projectId).length > before, 'a snapshot was taken during the burst');
  } finally {
    a.ws.close();
    mods.config.history.idleSec = 3600;
    mods.config.history.activeSec = 7200;
  }
  await mods.history.takeSnapshot(owner.projectId); // settle what is pending
});

test('trigger: a successful compile the user asked for snapshots; auto and failed compiles do not', async () => {
  const count = () => snapshots(owner.projectId).filter((s) => s.kind === 'compile').length;
  const a = await yClient(owner.projectId, owner.cookie);
  try {
    a.texts.get(mainId).insert(0, '% compiled\n');
    await until(async () => (await serverText(owner.projectId, mainId)).startsWith('% compiled'));
    await postJson(`/api/projects/${owner.projectId}/compile`, owner.cookie, { auto: true });
    assert.equal(count(), 0, 'auto compile');
    compileOutcome = 'failure';
    await postJson(`/api/projects/${owner.projectId}/compile`, owner.cookie, {});
    assert.equal(count(), 0, 'failed compile');
    compileOutcome = 'success';
    await postJson(`/api/projects/${owner.projectId}/compile`, owner.cookie, {});
    assert.equal(count(), 1);
    await postJson(`/api/projects/${owner.projectId}/compile`, owner.cookie, {});
    assert.equal(count(), 1, 'unchanged content is not stored again');
  } finally {
    a.ws.close();
  }
});

test('trigger: a platform build stores a labelled submission snapshot (label optional, backward compatible)', async () => {
  let res = await signedPost('/api/integration/build', { sub: '100', project: `${SCOPE}/p1`, label: 'Złożone jako rozwiązanie – wersja 1' });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('content-type'), 'application/pdf');
  res = await signedPost('/api/integration/build', { sub: '100', project: `${SCOPE}/p1` });
  assert.equal(res.status, 200, 'old platforms send no label');
  res = await signedPost('/api/integration/build', { sub: '100', project: `${SCOPE}/p1`, label: 7 });
  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'bad_request' });
  compileOutcome = 'failure';
  res = await signedPost('/api/integration/build', { sub: '100', project: `${SCOPE}/p1`, label: 'nie złożono' });
  assert.equal(res.status, 422);
  compileOutcome = 'success';

  const subs = snapshots(owner.projectId).filter((s) => s.kind === 'submission');
  assert.deepEqual(subs.map((s) => s.label), ['Złożone jako rozwiązanie – wersja 1', null], 'every submission is recorded, even unchanged');
  assert.equal(subs[0].state_hash, subs[1].state_hash, '…sharing the stored content');
  assert.deepEqual(JSON.parse(subs[0].authors), [ownerUserId]);
  const list = (await getJson(`/api/projects/${owner.projectId}/history`, viewer.cookie)).snapshots;
  assert.equal(list[0].kind, 'submission');
  assert.equal(list[0].actor.name, 'Ada Lovelace');
});

test('dedupe: identical binary files are stored once; unchanged states are not stored again', async () => {
  const png = crypto.randomBytes(2048);
  const form = new FormData();
  form.append('paths', JSON.stringify(['img/a.png', 'img/b.png']));
  form.append('files', new Blob([png]), 'a.png');
  form.append('files', new Blob([png]), 'b.png');
  const up = await fetch(`${base}/api/projects/${owner.projectId}/upload`, { method: 'POST', headers: { cookie: owner.cookie }, body: form });
  assert.equal(up.status, 200, await up.clone().text());
  await mods.history.takeSnapshot(owner.projectId, { kind: 'manual', label: 'z obrazkami', force: true });
  await mods.history.takeSnapshot(owner.projectId, { kind: 'manual', label: 'jeszcze raz', force: true });
  const dir = mods.paths.history(owner.projectId);
  const blobs = fs.readdirSync(path.join(dir, 'blobs'));
  assert.deepEqual(blobs, [crypto.createHash('sha256').update(png).digest('hex')]);
  const states = new Set(snapshots(owner.projectId).map((s) => s.state_hash));
  assert.equal(fs.readdirSync(path.join(dir, 'states')).length, states.size, 'one file per distinct state');
  const r = await mods.history.takeSnapshot(owner.projectId);
  assert.equal(r.created, false, 'an automatic snapshot of an unchanged project is skipped');
});

test('details, file and .zip of a version compared with the project now', async () => {
  const a = await yClient(owner.projectId, owner.cookie);
  let snapId;
  try {
    const before = a.text(mainId);
    snapId = (await mods.history.takeSnapshot(owner.projectId, { kind: 'manual', label: 'przed skreśleniem', force: true })).snapshot.id;
    // Half of the solution disappears.
    a.texts.get(mainId).delete(0, Math.floor(before.length / 2));
    await until(async () => (await serverText(owner.projectId, mainId)) === a.text(mainId));
    const d = await getJson(`/api/projects/${owner.projectId}/history/${snapId}`, viewer.cookie);
    const main = d.files.find((f) => f.id === mainId);
    assert.equal(main.status, 'modified');
    assert.ok(main.lines.removed > 0, 'the deleted lines are counted');
    assert.equal(d.files.find((f) => f.path === 'img/a.png').status, 'same');
    const file = await call('GET', `/api/projects/${owner.projectId}/history/${snapId}/files/${mainId}`, viewer.cookie);
    assert.equal(file.status, 200);
    assert.equal(await file.text(), before);
    const zipRes = await call('GET', `/api/projects/${owner.projectId}/history/${snapId}/download`, viewer.cookie);
    assert.equal(zipRes.headers.get('content-type'), 'application/zip');
    const zip = unzipSync(new Uint8Array(await zipRes.arrayBuffer()));
    assert.deepEqual(Object.keys(zip).sort(), ['img/a.png', 'img/b.png', 'main.tex']);
    assert.equal(new TextDecoder().decode(zip['main.tex']), before);
  } finally {
    a.ws.close();
  }
});

test('restore the whole project as a new change: every collaborator converges, nothing rewritten, undoable', async () => {
  const a = await yClient(owner.projectId, owner.cookie);
  const b = await yClient(owner.projectId, collaborator.cookie);
  try {
    const good = a.text(mainId);
    const snapId = (await mods.history.takeSnapshot(owner.projectId, { kind: 'manual', label: 'dobra', force: true })).snapshot.id;
    // The collaborator wrecks the draft: deletes most of it and a file.
    b.texts.get(mainId).delete(10, good.length - 20);
    b.texts.get(mainId).insert(0, 'ZEPSUTE\n');
    const imgB = (await getJson(`/api/projects/${owner.projectId}`, owner.cookie)).entities.find((e) => e.name === 'b.png');
    assert.equal((await call('DELETE', `/api/projects/${owner.projectId}/entities/${imgB.id}`, collaborator.cookie)).status, 200);
    await until(async () => (await serverText(owner.projectId, mainId)) === b.text(mainId));
    await until(() => a.text(mainId) === b.text(mainId));
    const wrecked = a.text(mainId);
    const shared = await mods.collab.getShared(owner.projectId);
    const svBefore = Y.decodeStateVector(Y.encodeStateVector(shared.doc));

    const r = await postJson(`/api/projects/${owner.projectId}/history/${snapId}/restore`, owner.cookie);
    assert.ok(r.undoId && r.snapshotId);

    await until(() => a.text(mainId) === good && b.text(mainId) === good, 4000, 'collaborators to receive the restore');
    assert.equal(await serverText(owner.projectId, mainId), good);
    // A new change on top of the existing history, not a replaced document.
    const svAfter = Y.decodeStateVector(Y.encodeStateVector(shared.doc));
    for (const [client, clock] of svBefore) assert.ok((svAfter.get(client) || 0) >= clock);
    assert.ok([...svAfter.values()].reduce((x, y) => x + y) > [...svBefore.values()].reduce((x, y) => x + y));
    assert.ok(a.events.some((e) => e.type === 'tree' && e.entities.some((x) => x.name === 'b.png')), 'the deleted file is back in everyone\'s tree');
    const restoredB = mods.db.one(`SELECT id FROM entities WHERE project_id = ? AND name = 'b.png'`, owner.projectId);
    assert.equal(restoredB.id, imgB.id, 'same entity id as before');
    assert.ok(fs.existsSync(mods.paths.blob(owner.projectId, imgB.id)));

    // The collaborator keeps typing after the restore: still in sync.
    b.texts.get(mainId).insert(0, '% po przywróceniu\n');
    await until(() => a.text(mainId) === b.text(mainId) && a.text(mainId).startsWith('% po przywróceniu'));

    const kinds = snapshots(owner.projectId).slice(-2).map((s) => s.kind);
    assert.deepEqual(kinds, ['pre-restore', 'restore']);
    // Undo = restore the pre-restore snapshot.
    await postJson(`/api/projects/${owner.projectId}/history/${r.undoId}/restore`, owner.cookie);
    await until(() => a.text(mainId) === wrecked && b.text(mainId) === wrecked, 4000, 'undo');
  } finally {
    a.ws.close();
    b.ws.close();
  }
});

test('restore a single file: a deleted file comes back where it was; others are untouched', async () => {
  const a = await yClient(owner.projectId, owner.cookie);
  try {
    const doc = await postJson(`/api/projects/${owner.projectId}/entities`, owner.cookie, { name: 'rozdzial.tex', kind: 'doc' });
    await until(() => a.texts.get(doc.id));
    a.texts.get(doc.id).insert(0, 'Rozdział 1\n');
    await until(async () => (await serverText(owner.projectId, doc.id)) === 'Rozdział 1\n');
    const snapId = (await mods.history.takeSnapshot(owner.projectId, { kind: 'manual', label: 'z rozdziałem', force: true })).snapshot.id;
    a.texts.get(mainId).insert(0, '% zmiana w main\n');
    assert.equal((await call('DELETE', `/api/projects/${owner.projectId}/entities/${doc.id}`, owner.cookie)).status, 200);
    await until(async () => (await serverText(owner.projectId, mainId)).startsWith('% zmiana w main'));
    const mainNow = await serverText(owner.projectId, mainId);

    await postJson(`/api/projects/${owner.projectId}/history/${snapId}/restore`, owner.cookie, { entityId: doc.id });
    await until(() => a.text(doc.id) === 'Rozdział 1\n', 4000, 'restored file');
    assert.ok(mods.db.one(`SELECT 1 FROM entities WHERE id = ? AND project_id = ?`, doc.id, owner.projectId));
    assert.equal(await serverText(owner.projectId, mainId), mainNow, 'main.tex not touched');
    const res = await call('POST', `/api/projects/${owner.projectId}/history/${snapId}/restore`, owner.cookie, { entityId: 'nope' });
    assert.equal(res.status, 404);
  } finally {
    a.ws.close();
  }
});

test('permissions: viewers look, editors restore, outsiders and locked sharing see nothing', async () => {
  const [latest] = (await getJson(`/api/projects/${owner.projectId}/history`, viewer.cookie)).snapshots;
  assert.equal((await getJson(`/api/projects/${owner.projectId}/history`, viewer.cookie)).canRestore, false);
  assert.equal((await call('POST', `/api/projects/${owner.projectId}/history/${latest.id}/restore`, viewer.cookie, {})).status, 403);
  assert.equal((await call('POST', `/api/projects/${owner.projectId}/history`, viewer.cookie, { label: 'x' })).status, 403);
  assert.equal((await call('POST', `/api/projects/${owner.projectId}/history`, collaborator.cookie, { label: '  ' })).status, 400);
  const named = await postJson(`/api/projects/${owner.projectId}/history`, collaborator.cookie, { label: 'Wersja Grace' });
  assert.equal(named.snapshot.kind, 'manual');
  assert.equal(named.snapshot.actor.name, 'Grace Hopper');

  const outsider = await sso('199', 'Obcy', { project: `${SCOPE}/p9` });
  assert.equal((await call('GET', `/api/projects/${owner.projectId}/history`, outsider.cookie)).status, 404);
  assert.equal((await call('GET', `/api/projects/${owner.projectId}/history/${latest.id}/download`, outsider.cookie)).status, 404);
  assert.equal((await call('GET', `/api/projects/${outsider.projectId}/history/${latest.id}`, outsider.cookie)).status, 404, 'a version of another project');

  // The organiser locks sharing: members lose the history together with the project.
  assert.equal((await signedPost('/api/integration/sharing', { scope: SCOPE, enabled: false })).status, 200);
  assert.equal((await call('GET', `/api/projects/${owner.projectId}/history`, collaborator.cookie)).status, 404);
  assert.equal((await call('POST', `/api/projects/${owner.projectId}/history/${latest.id}/restore`, collaborator.cookie, {})).status, 404);
  assert.equal((await call('GET', `/api/projects/${owner.projectId}/history`, viewer.cookie)).status, 404);
  assert.equal((await call('GET', `/api/projects/${owner.projectId}/history`, owner.cookie)).status, 200, 'the owner keeps it');
  assert.equal((await signedPost('/api/integration/sharing', { scope: SCOPE, enabled: true })).status, 200);
});

test('retention: all for 24 h, hourly for 7 days, daily after, nothing past HISTORY_KEEP_DAYS; protected kinds stay', async () => {
  const projectId = (await sso('300', 'Retencja', { project: `${SCOPE}/p300` })).projectId;
  const { snapshot } = await mods.history.takeSnapshot(projectId, { kind: 'auto' });
  const row = mods.db.one(`SELECT * FROM snapshots WHERE id = ?`, snapshot.id);
  mods.db.run(`DELETE FROM snapshots WHERE id = ?`, row.id);
  const H = 3600_000;
  const D = 24 * H;
  const t0 = Math.floor(Date.now() / D) * D + 12 * H; // midday, so hour/day buckets are predictable
  const ages = {
    fresh1: 5 * 60_000, fresh2: 3 * H, fresh3: 23 * H, // all kept
    h1a: 30 * H + 5 * 60_000, h1b: 30 * H + 15 * 60_000, h1c: 30 * H + 25 * 60_000, // same hour: newest kept
    h2: 50 * H,
    d1a: 10 * D, d1b: 10 * D + 2 * H, // same day: newest kept
    d2: 12 * D,
    sub: 10 * D + 3 * H, // a submission in that day: kept anyway
    old: 31 * D, oldSub: 40 * D, // past HISTORY_KEEP_DAYS=30: gone, even a submission
  };
  for (const [name, age] of Object.entries(ages)) {
    mods.db.run(
      `INSERT INTO snapshots (id, project_id, created_at, kind, label, authors, state_hash, state_size, tree, tree_hash) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)`,
      name, projectId, t0 - age, name === 'sub' || name === 'oldSub' ? 'submission' : 'auto', name, row.state_hash, row.state_size, row.tree, row.tree_hash,
    );
  }
  const deleted = await mods.history.applyRetention(projectId, t0);
  const left = snapshots(projectId).map((s) => s.id).sort();
  assert.deepEqual(left, ['d1a', 'd2', 'fresh1', 'fresh2', 'fresh3', 'h1a', 'h2', 'sub'].sort());
  assert.equal(deleted, 5);
  assert.ok(fs.existsSync(path.join(mods.paths.history(projectId), 'states', `${row.state_hash}.gz`)), 'content still referenced stays');

  // Everything past the keep window: the stored objects go too.
  mods.db.run(`UPDATE snapshots SET created_at = ? WHERE project_id = ?`, t0 - 60 * D, projectId);
  await mods.history.applyRetention(projectId, t0);
  assert.equal(snapshots(projectId).length, 0);
  assert.deepEqual(fs.readdirSync(path.join(mods.paths.history(projectId), 'states')), []);
});

test('retention: the size cap drops the oldest unprotected snapshots first, never the newest', async () => {
  const p = await sso('301', 'Limit', { project: `${SCOPE}/p301` });
  const info = await getJson(`/api/projects/${p.projectId}`, p.cookie);
  const main = info.entities.find((e) => e.name === 'main.tex').id;
  const ids = [];
  for (let i = 0; i < 6; i++) {
    // Incompressible content, so each state is ~8 KB on disk.
    await mods.collab.setTexts(p.projectId, [[main, crypto.randomBytes(6000).toString('base64')]]);
    const kind = i === 1 ? 'submission' : 'auto';
    ids.push((await mods.history.takeSnapshot(p.projectId, { kind })).snapshot.id);
  }
  const total = () => fs.readdirSync(path.join(mods.paths.history(p.projectId), 'states')).reduce((n, f) => n + fs.statSync(path.join(mods.paths.history(p.projectId), 'states', f)).size, 0);
  const before = total();
  const saved = mods.config.history.maxBytes;
  mods.config.history.maxBytes = Math.floor(before / 2);
  try {
    await mods.history.applyRetention(p.projectId);
  } finally {
    mods.config.history.maxBytes = saved;
  }
  const left = snapshots(p.projectId).map((s) => s.id);
  assert.ok(left.includes(ids[5]), 'the newest stays');
  assert.ok(left.includes(ids[1]), 'the submission stays');
  assert.ok(!left.includes(ids[0]) && !left.includes(ids[2]), 'the oldest unprotected go first');
  assert.ok(total() <= Math.floor(before / 2) + 9000, 'under the cap (give or take the protected one)');
});

test('deleting a project deletes its history', async () => {
  const projectId = (await sso('300', 'Retencja', { project: `${SCOPE}/p300` })).projectId;
  await mods.history.takeSnapshot(projectId, { kind: 'manual', label: 'x', force: true });
  assert.ok(snapshots(projectId).length > 0);
  (await mods.collab.getShared(projectId)).awareness.destroy();
  mods.db.run(`DELETE FROM projects WHERE id = ?`, projectId);
  assert.equal(snapshots(projectId).length, 0, 'rows cascade with the project');
  assert.equal(await mods.history.takeSnapshot(projectId), null, 'a pending snapshot of a deleted project is a no-op');
});
