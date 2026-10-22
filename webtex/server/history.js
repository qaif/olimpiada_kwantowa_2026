// Project version history. Snapshots of a project's content are taken automatically (edits settled
// for HISTORY_IDLE_SEC, every HISTORY_ACTIVE_SEC of continuous editing, each explicit successful
// compile, each submission to the olympiad platform) or by hand ("named version"), thinned by a
// retention policy, and listed, compared, downloaded and restored from the editor's History panel.
//
// A snapshot is a row in `snapshots` (db.js) pointing at content-addressed files under
// projects/<id>/history/ (so the nightly backup of webtex_data carries them):
//   states/<sha256>.gz   gzip of Y.encodeStateAsUpdate(project Y.Doc): every document's text
//   blobs/<sha256>       binary files (images, PDFs…), stored once however many snapshots use them
// plus the file tree (names, folders, which blob each file is) as JSON in the row.
// Restoring never rewrites the live Y.Doc's history: the difference is applied to it as a new change,
// which every connected collaborator receives, after saving the current state as a snapshot.
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';
import { promisify } from 'node:util';
import { zipSync } from 'fflate';
import * as Y from 'yjs';
import { config, paths } from './config.js';
import { all, one, run, tx } from './db.js';
import { broadcast, getShared, onDocChange, setTexts } from './collab.js';
import { createFolder, deleteEntity, entityPaths, findChild, insertEntity, listEntities } from './tree.js';
import { diffStats, textEdits } from './linediff.js';
import { HttpError, exists, newId, now } from './util.js';

const gzip = promisify(zlib.gzip);
const gunzip = promisify(zlib.gunzip);

const HOUR = 3600_000;
const DAY = 24 * HOUR;
// Never thinned or dropped for size, only by HISTORY_KEEP_DAYS: evidence (submissions) and named versions.
export const PROTECTED_KINDS = new Set(['submission', 'manual']);

const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');
const objectsDir = (projectId, kind) => path.join(paths.history(projectId), kind);
const stateFile = (projectId, hash) => path.join(objectsDir(projectId, 'states'), `${hash}.gz`);
const blobFile = (projectId, hash) => path.join(objectsDir(projectId, 'blobs'), hash);

// One snapshot/retention/restore at a time per project.
const locks = new Map();
async function withLock(projectId, fn) {
  const previous = locks.get(projectId) || Promise.resolve();
  const job = previous.catch(() => {}).then(fn);
  locks.set(projectId, job);
  try {
    return await job;
  } finally {
    if (locks.get(projectId) === job) locks.delete(projectId);
  }
}

// ---------- automatic snapshots ----------

const pending = new Map(); // projectId -> { since, authors: Set<userId>, timer }

/** Records a change of the project (by `userId`, if known) and schedules the automatic snapshot. */
export function noteChange(projectId, userId = null) {
  if (!config.history.enabled) return;
  let p = pending.get(projectId);
  if (!p) pending.set(projectId, (p = { since: now(), authors: new Set(), timer: null }));
  if (userId) p.authors.add(userId);
  clearTimeout(p.timer);
  const fire = () => takeSnapshot(projectId, { kind: 'auto' }).catch((err) => console.error(`[history] snapshot of ${projectId} failed`, err));
  if (now() - p.since >= config.history.activeSec * 1000) {
    p.timer = null;
    fire();
    return;
  }
  p.timer = setTimeout(fire, config.history.idleSec * 1000);
  p.timer.unref?.();
}
onDocChange(noteChange);

/** Takes the snapshots still waiting for edits to settle (server shutdown). */
export async function flushHistory() {
  await Promise.all([...pending.keys()].map((id) => takeSnapshot(id, { kind: 'auto' }).catch((err) => console.error(err))));
}

// ---------- capturing ----------

const hashCache = new Map(); // blob path -> { size, mtimeMs, hash }
async function blobHash(projectId, entityId) {
  const file = paths.blob(projectId, entityId);
  const st = await fs.stat(file).catch(() => null);
  if (!st) return null;
  const cached = hashCache.get(file);
  if (cached && cached.size === st.size && cached.mtimeMs === st.mtimeMs) return cached;
  const value = { size: st.size, mtimeMs: st.mtimeMs, hash: sha256(await fs.readFile(file)) };
  if (hashCache.size > 20_000) hashCache.clear();
  hashCache.set(file, value);
  return value;
}

/** The project's content right now: { update, tree }. */
export async function captureState(projectId) {
  const shared = await getShared(projectId);
  const update = Y.encodeStateAsUpdate(shared.doc);
  const project = one(`SELECT main_file_id FROM projects WHERE id = ?`, projectId);
  const entities = [];
  for (const e of listEntities(projectId)) {
    const item = { id: e.id, parentId: e.parentId ?? null, name: e.name, kind: e.kind };
    if (e.kind === 'file') {
      const blob = await blobHash(projectId, e.id);
      if (!blob) continue;
      item.size = blob.size;
      item.sha256 = blob.hash;
    }
    entities.push(item);
  }
  return { update, tree: { entities, mainFileId: project?.main_file_id ?? null } };
}

/** Capture for a snapshot taken after an operation (compile, build) of the state it saw; null if history is off. */
export const captureForSnapshot = (projectId) =>
  config.history.enabled ? captureState(projectId).catch((err) => (console.error('[history] capture failed', err), null)) : null;

// ---------- storing ----------

/**
 * Stores a snapshot of the project's current content (or of `capture`, taken earlier). Returns
 * { snapshot, created }: created is false when nothing changed since the latest snapshot (which is
 * then returned) and `force` is not set. Null when history is disabled or the project is gone.
 */
export async function takeSnapshot(projectId, { kind = 'auto', label = null, meta = null, actorId = null, capture = null, force = false } = {}) {
  // An earlier capture (compile) may predate edits still waiting: those keep their own timer.
  const p = capture ? null : pending.get(projectId);
  if (p) {
    clearTimeout(p.timer);
    pending.delete(projectId);
  }
  if (!config.history.enabled) return null;
  return withLock(projectId, async () => {
    if (!one(`SELECT 1 FROM projects WHERE id = ?`, projectId)) return null;
    const state = capture || (await captureState(projectId));
    const treeJson = JSON.stringify(state.tree);
    const stateHash = sha256(state.update);
    const treeHash = sha256(treeJson);
    const latest = one(`SELECT * FROM snapshots WHERE project_id = ? ORDER BY created_at DESC LIMIT 1`, projectId);
    if (!force && latest && latest.state_hash === stateHash && latest.tree_hash === treeHash) {
      return { snapshot: describe([latest])[0], created: false };
    }

    let stateSize = (await fs.stat(stateFile(projectId, stateHash)).catch(() => null))?.size;
    if (stateSize === undefined) {
      const gz = await gzip(state.update);
      await writeObject(stateFile(projectId, stateHash), (tmp) => fs.writeFile(tmp, gz));
      stateSize = gz.length;
    }
    for (const e of state.tree.entities) {
      if (e.kind !== 'file' || (await exists(blobFile(projectId, e.sha256)))) continue;
      await writeObject(blobFile(projectId, e.sha256), (tmp) => fs.copyFile(paths.blob(projectId, e.id), tmp));
    }

    const authors = [...new Set([...(p?.authors || []), ...(actorId ? [actorId] : [])])];
    const id = newId(9);
    // Strictly increasing per project, so ordering by time is total.
    const createdAt = Math.max(now(), (latest?.created_at ?? 0) + 1);
    run(
      `INSERT INTO snapshots (id, project_id, created_at, kind, label, meta, authors, actor_id, state_hash, state_size, tree, tree_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      id,
      projectId,
      createdAt,
      kind,
      label,
      meta ? JSON.stringify(meta) : null,
      JSON.stringify(authors),
      actorId,
      stateHash,
      stateSize,
      treeJson,
      treeHash,
    );
    await retain(projectId).catch((err) => console.error(`[history] retention of ${projectId} failed`, err));
    broadcast(projectId, { type: 'history' });
    return { snapshot: describe([one(`SELECT * FROM snapshots WHERE id = ?`, id)])[0], created: true };
  });
}

async function writeObject(file, write) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${newId(4)}.tmp`;
  try {
    await write(tmp);
    await fs.rename(tmp, file);
  } catch (err) {
    await fs.rm(tmp, { force: true });
    throw err;
  }
}

/** Snapshot after a successful compile of the state `capture` saw. Never throws. */
export async function snapshotAfterCompile(projectId, capture, result, { kind = 'compile', label = null, actorId = null } = {}) {
  if (!capture || result?.status !== 'success' || !result.hasPdf) return null;
  try {
    return await takeSnapshot(projectId, { kind, label, actorId, capture, force: kind === 'submission' });
  } catch (err) {
    console.error(`[history] ${kind} snapshot of ${projectId} failed`, err);
    return null;
  }
}

// ---------- retention ----------

/**
 * Deletes what the policy no longer keeps: anything older than HISTORY_KEEP_DAYS; past 24 hours only
 * the newest snapshot of each hour, past 7 days of each day; then, above HISTORY_MAX_MB, the oldest
 * snapshots. Submissions and named versions are only subject to HISTORY_KEEP_DAYS; the newest snapshot
 * only to that too. Returns the number of snapshots deleted.
 */
export const applyRetention = (projectId, at = now()) => withLock(projectId, () => retain(projectId, at));

async function retain(projectId, at = now()) {
  const { keepDays, maxBytes } = config.history;
  const rows = all(`SELECT id, created_at, kind, state_hash, state_size, tree FROM snapshots WHERE project_id = ? ORDER BY created_at DESC`, projectId);
  const doomed = new Set();
  const seen = new Set();
  rows.forEach((r, i) => {
    const age = at - r.created_at;
    if (keepDays > 0 && age > keepDays * DAY) return doomed.add(r.id);
    if (i === 0 || PROTECTED_KINDS.has(r.kind) || age <= DAY) return;
    const bucket = age <= 7 * DAY ? `h${Math.floor(r.created_at / HOUR)}` : `d${Math.floor(r.created_at / DAY)}`;
    if (seen.has(bucket)) doomed.add(r.id);
    else seen.add(bucket);
  });

  // Size cap: count each stored object once, however many snapshots share it.
  const kept = rows.filter((r) => !doomed.has(r.id));
  const objectsOf = (r) => {
    const list = new Map([[`s:${r.state_hash}`, r.state_size]]);
    for (const e of JSON.parse(r.tree).entities) if (e.kind === 'file' && e.sha256) list.set(`b:${e.sha256}`, e.size || 0);
    return list;
  };
  const refs = new Map();
  let total = 0;
  for (const r of kept) {
    r.objects = objectsOf(r);
    for (const [key, size] of r.objects) {
      if (!refs.has(key)) {
        refs.set(key, { size, count: 0 });
        total += size;
      }
      refs.get(key).count++;
    }
  }
  for (const r of [...kept].reverse()) {
    if (total <= maxBytes) break;
    if (r === kept[0] || PROTECTED_KINDS.has(r.kind)) continue;
    doomed.add(r.id);
    for (const key of r.objects.keys()) {
      const o = refs.get(key);
      if (--o.count === 0) {
        total -= o.size;
        refs.delete(key);
      }
    }
  }

  if (!doomed.size) return 0;
  tx(() => {
    for (const id of doomed) run(`DELETE FROM snapshots WHERE id = ?`, id);
  });
  await collectGarbage(projectId);
  return doomed.size;
}

/** Removes stored objects no snapshot refers to any more. Call with the project lock held. */
async function collectGarbage(projectId) {
  const wanted = new Set();
  for (const r of all(`SELECT state_hash, tree FROM snapshots WHERE project_id = ?`, projectId)) {
    wanted.add(`states/${r.state_hash}.gz`);
    for (const e of JSON.parse(r.tree).entities) if (e.kind === 'file' && e.sha256) wanted.add(`blobs/${e.sha256}`);
  }
  for (const kind of ['states', 'blobs']) {
    for (const name of await fs.readdir(objectsDir(projectId, kind)).catch(() => [])) {
      if (name.endsWith('.tmp') || wanted.has(`${kind}/${name}`)) continue;
      await fs.rm(path.join(objectsDir(projectId, kind), name), { force: true });
    }
  }
}

/** Hourly retention pass over every project with history (old snapshots of idle projects too). */
export function startHistorySweep() {
  if (!config.history.enabled) return;
  const sweep = async () => {
    for (const { project_id } of all(`SELECT DISTINCT project_id FROM snapshots`)) {
      await applyRetention(project_id).catch((err) => console.error(`[history] retention of ${project_id} failed`, err));
    }
  };
  setInterval(sweep, HOUR).unref();
  setTimeout(sweep, 60_000).unref();
}

// ---------- reading ----------

/** Rows → API objects, with user ids resolved to names. */
function describe(rows) {
  const ids = new Set();
  for (const r of rows) {
    for (const id of JSON.parse(r.authors)) ids.add(id);
    if (r.actor_id) ids.add(r.actor_id);
  }
  const names = new Map();
  if (ids.size) {
    const list = [...ids];
    for (const u of all(`SELECT id, name FROM users WHERE id IN (${list.map(() => '?').join(',')})`, ...list)) names.set(u.id, u.name);
  }
  const user = (id) => ({ id, name: names.get(id) ?? null });
  return rows.map((r) => ({
    id: r.id,
    createdAt: r.created_at,
    kind: r.kind,
    label: r.label,
    meta: r.meta ? JSON.parse(r.meta) : null,
    authors: JSON.parse(r.authors).map(user),
    actor: r.actor_id ? user(r.actor_id) : null,
  }));
}

export function listSnapshots(projectId) {
  return describe(all(`SELECT id, created_at, kind, label, meta, authors, actor_id FROM snapshots WHERE project_id = ? ORDER BY created_at DESC`, projectId));
}

/** A snapshot's content: { row, tree, pathOf (id → path), texts (id → string) }. */
export async function loadSnapshot(projectId, snapshotId) {
  const row = one(`SELECT * FROM snapshots WHERE id = ? AND project_id = ?`, String(snapshotId), projectId);
  if (!row) throw new HttpError(404, 'Version not found');
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, new Uint8Array(await gunzip(await fs.readFile(stateFile(projectId, row.state_hash)))));
    const tree = JSON.parse(row.tree);
    const map = doc.getMap('texts');
    const texts = new Map();
    for (const e of tree.entities) if (e.kind === 'doc') texts.set(e.id, map.get(e.id)?.toString() ?? '');
    return { row, tree, pathOf: entityPaths(tree.entities), texts };
  } finally {
    doc.destroy();
  }
}

/**
 * A snapshot compared with the project now: its files with status 'same' | 'modified' | 'deleted'
 * (not in the project any more), plus files that exist only now ('added'); line counts for documents.
 */
export async function snapshotDetails(projectId, snapshotId) {
  const snap = await loadSnapshot(projectId, snapshotId);
  const shared = await getShared(projectId);
  const live = listEntities(projectId);
  const liveById = new Map(live.map((e) => [e.id, e]));
  const livePath = entityPaths(live);
  const files = [];
  for (const e of snap.tree.entities) {
    if (e.kind === 'folder') continue;
    const item = { id: e.id, path: snap.pathOf.get(e.id), kind: e.kind, size: e.size ?? null, status: 'deleted' };
    const l = liveById.get(e.id);
    if (l && l.kind === e.kind) {
      item.currentPath = livePath.get(e.id);
      if (e.kind === 'doc') {
        const before = snap.texts.get(e.id);
        const nowText = shared.texts.get(e.id)?.toString() ?? '';
        item.status = before === nowText ? 'same' : 'modified';
        if (item.status === 'modified') item.lines = diffStats(before, nowText);
      } else {
        item.status = (await blobHash(projectId, e.id))?.hash === e.sha256 ? 'same' : 'modified';
      }
    } else if (e.kind === 'doc') {
      item.lines = { added: 0, removed: (snap.texts.get(e.id).match(/\n/g) || []).length + 1 };
    }
    files.push(item);
  }
  const inSnapshot = new Set(snap.tree.entities.map((e) => e.id));
  for (const l of live) {
    if (l.kind === 'folder' || inSnapshot.has(l.id)) continue;
    files.push({ id: l.id, path: livePath.get(l.id), currentPath: livePath.get(l.id), kind: l.kind, status: 'added' });
  }
  files.sort((a, b) => a.path.localeCompare(b.path));
  return { snapshot: describe([snap.row])[0], mainFileId: snap.tree.mainFileId, files };
}

/** One file of a snapshot: { entity, path, text } for documents, { entity, path, file } for binaries. */
export async function snapshotFile(projectId, snapshotId, entityId) {
  const snap = await loadSnapshot(projectId, snapshotId);
  const e = snap.tree.entities.find((x) => x.id === entityId && x.kind !== 'folder');
  if (!e) throw new HttpError(404, 'File not found in this version');
  const p = snap.pathOf.get(e.id);
  return e.kind === 'doc' ? { entity: e, path: p, text: snap.texts.get(e.id) } : { entity: e, path: p, file: blobFile(projectId, e.sha256) };
}

/** The whole snapshot as a .zip (Uint8Array). */
export async function snapshotZip(projectId, snapshotId) {
  const snap = await loadSnapshot(projectId, snapshotId);
  const files = {};
  for (const e of snap.tree.entities) {
    const p = snap.pathOf.get(e.id);
    if (e.kind === 'doc') files[p] = new TextEncoder().encode(snap.texts.get(e.id));
    if (e.kind === 'file') {
      const data = await fs.readFile(blobFile(projectId, e.sha256)).catch(() => null);
      if (data) files[p] = new Uint8Array(data);
    }
  }
  return { zip: zipSync(files, { level: 6 }), createdAt: snap.row.created_at };
}

// ---------- restoring ----------

/** Turns ytext into `target` with minimal line edits (collaborators' cursors elsewhere stay put). */
function applyText(ytext, target) {
  for (const e of textEdits(ytext.toString(), target).reverse()) {
    if (e.remove) ytext.delete(e.index, e.remove);
    if (e.insert) ytext.insert(e.index, e.insert);
  }
}

async function writeBlob(projectId, entityId, hash) {
  const source = blobFile(projectId, hash);
  if (!(await exists(source))) throw new Error(`history object blobs/${hash} is missing`);
  await writeObject(paths.blob(projectId, entityId), (tmp) => fs.copyFile(source, tmp));
}

// An entity id free for reuse: snapshot ids are kept, so a restored file is the same file again.
const freeId = (id) => (one(`SELECT 1 FROM entities WHERE id = ?`, id) ? newId() : id);

/**
 * Restores a whole snapshot, or one file of it (`entityId`), as a new change: the current state is
 * snapshotted first ('pre-restore', the undo point), then the difference is applied to the live
 * project, then the result is snapshotted ('restore'). Returns { undoId, snapshotId }.
 */
export async function restoreSnapshot(projectId, snapshotId, { entityId = null, actorId = null } = {}) {
  const snap = await loadSnapshot(projectId, snapshotId);
  let target = null;
  if (entityId) {
    target = snap.tree.entities.find((e) => e.id === entityId && e.kind !== 'folder');
    if (!target) throw new HttpError(404, 'File not found in this version');
  }
  const meta = { from: snap.row.id, fromCreatedAt: snap.row.created_at, path: target ? snap.pathOf.get(target.id) : null };
  const before = await takeSnapshot(projectId, { kind: 'pre-restore', meta, actorId });
  if (target) await restoreFile(projectId, snap, target);
  else await restoreProject(projectId, snap);
  const after = await takeSnapshot(projectId, { kind: 'restore', meta, actorId, force: true });
  return { undoId: before?.snapshot.id ?? null, snapshotId: after?.snapshot.id ?? null };
}

async function restoreFile(projectId, snap, e) {
  const shared = await getShared(projectId);
  let id = one(`SELECT id FROM entities WHERE id = ? AND project_id = ?`, e.id, projectId)?.id;
  if (!id) {
    // Deleted since: put it back where it was (recreating folders), or into a same-named file there.
    let parentId = null;
    for (const dir of snap.pathOf.get(e.id).split('/').slice(0, -1)) parentId = createFolder(projectId, parentId, dir);
    const clash = findChild(projectId, parentId, e.name);
    if (clash?.kind === 'folder') throw new HttpError(409, `A folder named "${e.name}" is in the way`);
    if (clash && clash.kind !== e.kind) await deleteEntity(projectId, clash.id);
    else if (clash) id = clash.id;
    if (!id) {
      id = insertEntity(projectId, parentId, e.name, e.kind, e.size ?? null, freeId(e.id));
      if (e.kind === 'doc') return setTexts(projectId, [[id, snap.texts.get(e.id)]]);
      return writeBlob(projectId, id, e.sha256);
    }
  }
  if (e.kind === 'doc') {
    const target = snap.texts.get(e.id);
    shared.doc.transact(() => {
      const yt = shared.texts.get(id);
      if (yt) applyText(yt, target);
      else shared.texts.set(id, new Y.Text(target));
    }, 'restore');
  } else if ((await blobHash(projectId, id))?.hash !== e.sha256) {
    await writeBlob(projectId, id, e.sha256);
    run(`UPDATE entities SET size = ? WHERE id = ?`, e.size, id);
  }
}

async function restoreProject(projectId, snap) {
  const shared = await getShared(projectId);
  const live = listEntities(projectId);
  const liveById = new Map(live.map((e) => [e.id, e]));
  const idMap = new Map(snap.tree.entities.map((e) => [e.id, liveById.get(e.id)?.kind === e.kind ? e.id : freeId(e.id)]));
  const keep = new Set(idMap.values());
  const doomed = live.filter((e) => !keep.has(e.id));

  // Binary contents first: the tree switches over only once every file it names is in place.
  for (const e of snap.tree.entities) {
    if (e.kind !== 'file') continue;
    const id = idMap.get(e.id);
    if (liveById.has(id) && (await blobHash(projectId, id))?.hash === e.sha256) continue;
    await writeBlob(projectId, id, e.sha256);
  }
  // The snapshot's tree is consistent as a whole, so the rows are rewritten in one go (no name clashes on the way).
  tx(() => {
    for (const d of doomed) run(`DELETE FROM entities WHERE id = ?`, d.id);
    for (const e of snap.tree.entities) {
      const id = idMap.get(e.id);
      const parentId = e.parentId ? idMap.get(e.parentId) : null;
      if (liveById.has(id)) run(`UPDATE entities SET parent_id = ?, name = ?, size = ? WHERE id = ?`, parentId, e.name, e.size ?? null, id);
      else run(`INSERT INTO entities (id, project_id, parent_id, name, kind, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)`, id, projectId, parentId, e.name, e.kind, e.size ?? null, now());
    }
    const main = snap.tree.mainFileId && idMap.get(snap.tree.mainFileId);
    if (main) run(`UPDATE projects SET main_file_id = ? WHERE id = ?`, main, projectId);
    else run(`UPDATE projects SET main_file_id = NULL WHERE id = ? AND main_file_id NOT IN (SELECT id FROM entities WHERE project_id = ?)`, projectId, projectId);
  });
  for (const d of doomed) if (d.kind === 'file') await fs.rm(paths.blob(projectId, d.id), { force: true });
  shared.doc.transact(() => {
    for (const d of doomed) if (d.kind === 'doc') shared.texts.delete(d.id);
    for (const e of snap.tree.entities) {
      if (e.kind !== 'doc') continue;
      const id = idMap.get(e.id);
      const target = snap.texts.get(e.id);
      const yt = shared.texts.get(id);
      if (yt) applyText(yt, target);
      else shared.texts.set(id, new Y.Text(target));
    }
  }, 'restore');
}
