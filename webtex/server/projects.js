import fs from 'node:fs/promises';
import path from 'node:path';
import express from 'express';
import multer from 'multer';
import { unzipSync, zipSync } from 'fflate';
import * as Y from 'yjs';
import { config, paths } from './config.js';
import { all, one, run } from './db.js';
import { requireUser } from './auth.js';
import { broadcast, getShared, getText, kick, setTexts } from './collab.js';
import { ENGINES, clearCache, compileProject, compileQueue, detectBackend, engineRefusal } from './compiler.js';
import { QueueBusyError, closeSignal, sendQueueError } from './compileQueue.js';
import { forwardSearch, inverseSearch, loadSynctex } from './synctex.js';
import { canUseAi } from './aiPolicy.js';
import { TEMPLATES } from './templates.js';
import {
  createDoc,
  createFolder,
  deleteEntity,
  entityPaths,
  getEntity,
  guessMainFile,
  listEntities,
  moveEntity,
  putFile,
  putFileAtPath,
} from './tree.js';
import { HttpError, newId, now } from './util.js';

const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: config.maxUploadBytes, files: 200 } });
export const projectsRouter = express.Router();
projectsRouter.use(requireUser);

// ---------- access control ----------

const ROLE_RANK = { viewer: 1, editor: 2, owner: 3 };

export const SHARING_LOCKED = 'Udostępnianie wyłączone przez organizatora';

export function roleFor(userId, projectId) {
  const p = one(`SELECT owner_id, sharing_locked FROM projects WHERE id = ?`, projectId);
  if (!p) return null;
  if (p.owner_id === userId) return 'owner';
  // Locked by the olympiad organiser: memberships stay in the database but grant nothing.
  if (p.sharing_locked) return null;
  return one(`SELECT role FROM members WHERE project_id = ? AND user_id = ?`, projectId, userId)?.role || null;
}

function access(req, minRole) {
  const project = one(`SELECT * FROM projects WHERE id = ?`, req.params.id);
  const role = project && roleFor(req.user.id, project.id);
  if (!role) throw new HttpError(404, 'Project not found');
  if (ROLE_RANK[role] < ROLE_RANK[minRole]) throw new HttpError(403, 'You do not have permission to do that');
  return { project, role };
}

// Platform (SSO) users only get projects from the platform: no creating, importing, copying, deleting.
function assertNotExternal(req) {
  if (req.user.external_id) throw new HttpError(403, 'Projects are created from the olympiad platform');
}

function assertSharingAllowed(project) {
  if (project.sharing_locked) throw new HttpError(403, SHARING_LOCKED);
}

const touch = (projectId) => run(`UPDATE projects SET updated_at = ? WHERE id = ?`, now(), projectId);

// External users' emails are synthetic placeholders; never show them.
const EMAIL = `CASE WHEN u.external_id IS NULL THEN u.email END`;

function projectInfo(project, role) {
  const owner = one(`SELECT id, name, ${EMAIL} AS email FROM users u WHERE id = ?`, project.owner_id);
  const linked = one(`SELECT return_url FROM linked_projects WHERE project_id = ?`, project.id);
  const info = {
    id: project.id,
    name: project.name,
    owner,
    role,
    compiler: project.compiler,
    mainFileId: project.main_file_id,
    updatedAt: project.updated_at,
    createdAt: project.created_at,
    sharingLocked: !!project.sharing_locked,
    returnUrl: linked?.return_url || null,
    // AI assistant available to this user on this project (TEX-02); the panel is hidden otherwise.
    ai: canUseAi(project, role),
  };
  if (role === 'owner') {
    if (!project.sharing_locked) {
      info.editToken = project.edit_token;
      info.viewToken = project.view_token;
    }
    info.members = all(
      `SELECT u.id, u.name, ${EMAIL} AS email, m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.project_id = ? ORDER BY u.name`,
      project.id,
    );
  }
  return info;
}

const treeEvent = (projectId) => broadcast(projectId, { type: 'tree', entities: listEntities(projectId) });
const projectEvent = (projectId) => {
  const p = one(`SELECT * FROM projects WHERE id = ?`, projectId);
  broadcast(projectId, { type: 'project', project: { name: p.name, compiler: p.compiler, mainFileId: p.main_file_id } });
};

export const escapeTex = (s) =>
  s.replace(/[\\{}#$%&_^~]/g, (c) => ({ '\\': '\\textbackslash{}', '^': '\\^{}', '~': '\\~{}' })[c] || `\\${c}`);

function insertProject(ownerId, name, compiler = 'pdflatex', { sharingLocked = false, aiLocked = false } = {}) {
  const id = newId();
  const t = now();
  run(
    `INSERT INTO projects (id, name, owner_id, compiler, edit_token, view_token, sharing_locked, ai_locked, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    id,
    name,
    ownerId,
    compiler,
    newId(18),
    newId(18),
    sharingLocked ? 1 : 0,
    aiLocked ? 1 : 0,
    t,
    t,
  );
  return id;
}

/** Creates a project from [path, content] pairs; the first .tex file becomes the main file. */
export async function createProjectWithFiles(ownerId, name, files, { compiler = 'pdflatex', sharingLocked = false, aiLocked = false } = {}) {
  const id = insertProject(ownerId, cleanName(name), compiler, { sharingLocked, aiLocked });
  for (const [p, content] of files) await putFileAtPath(id, null, p, Buffer.from(content, 'utf8'));
  await finalizeProject(id);
  return id;
}

async function finalizeProject(projectId) {
  const main = await guessMainFile(projectId, getText);
  run(`UPDATE projects SET main_file_id = ? WHERE id = ?`, main, projectId);
  const shared = await getShared(projectId);
  await shared.flush();
}

async function removeProjectData(projectId) {
  kick(projectId, null, { deleted: true });
  for (const p of [paths.project(projectId), paths.compileDir(projectId), paths.outputs(projectId)]) {
    await fs.rm(p, { recursive: true, force: true });
  }
}

export const cleanName = (n, fallback = 'Untitled project') => String(n || '').trim().slice(0, 120) || fallback;

// ---------- projects ----------

projectsRouter.get('/', (req, res) => {
  const rows = all(
    `SELECT p.*, u.name AS owner_name,
            CASE WHEN p.owner_id = ? THEN 'owner' ELSE m.role END AS role
       FROM projects p
       JOIN users u ON u.id = p.owner_id
       LEFT JOIN members m ON m.project_id = p.id AND m.user_id = ?
      WHERE p.owner_id = ? OR (m.user_id = ? AND p.sharing_locked = 0)
      ORDER BY p.updated_at DESC`,
    req.user.id,
    req.user.id,
    req.user.id,
    req.user.id,
  );
  res.json({
    projects: rows.map((p) => ({ id: p.id, name: p.name, ownerName: p.owner_name, role: p.role, updatedAt: p.updated_at })),
    templates: Object.entries(TEMPLATES).map(([id, t]) => ({ id, label: t.label })),
  });
});

projectsRouter.post('/', async (req, res) => {
  assertNotExternal(req);
  const name = cleanName(req.body?.name);
  const template = TEMPLATES[req.body?.template] || TEMPLATES.blank;
  const id = await createProjectWithFiles(req.user.id, name, template.files(escapeTex(name)), { compiler: template.compiler });
  res.json({ id });
});

projectsRouter.post('/import', (req, _res, next) => (assertNotExternal(req), next()), upload.single('file'), async (req, res) => {
  if (!req.file) throw new HttpError(400, 'No file uploaded');
  let files;
  try {
    files = unzipSync(new Uint8Array(req.file.buffer));
  } catch {
    throw new HttpError(400, 'This is not a valid .zip archive');
  }
  let entries = Object.entries(files).filter(
    ([p]) => !p.endsWith('/') && !p.startsWith('__MACOSX/') && !p.split('/').some((s) => s === '.DS_Store' || s === '..'),
  );
  if (!entries.length) throw new HttpError(400, 'The archive is empty');
  // Strip a single common top-level folder.
  const first = entries[0][0].split('/')[0];
  if (entries.every(([p]) => p.includes('/') && p.split('/')[0] === first)) {
    entries = entries.map(([p, d]) => [p.slice(first.length + 1), d]);
  }
  const name = cleanName(req.body?.name || req.file.originalname.replace(/\.zip$/i, ''));
  const id = insertProject(req.user.id, name);
  try {
    for (const [p, data] of entries) await putFileAtPath(id, null, p, Buffer.from(data));
  } catch (err) {
    run(`DELETE FROM projects WHERE id = ?`, id);
    await removeProjectData(id);
    throw err;
  }
  await finalizeProject(id);
  res.json({ id });
});

projectsRouter.get('/:id', (req, res) => {
  const { project, role } = access(req, 'viewer');
  res.json({ project: projectInfo(project, role), entities: listEntities(project.id) });
});

projectsRouter.patch('/:id', (req, res) => {
  const { project } = access(req, 'editor');
  const b = req.body || {};
  if (b.name !== undefined) run(`UPDATE projects SET name = ? WHERE id = ?`, cleanName(b.name), project.id);
  if (b.compiler !== undefined) {
    if (!ENGINES[b.compiler]) throw new HttpError(400, 'Unknown compiler');
    const refused = engineRefusal(b.compiler);
    if (refused) throw new HttpError(400, refused);
    run(`UPDATE projects SET compiler = ? WHERE id = ?`, b.compiler, project.id);
  }
  if (b.mainFileId !== undefined) {
    const e = getEntity(project.id, b.mainFileId);
    if (e.kind !== 'doc' || !e.name.toLowerCase().endsWith('.tex')) throw new HttpError(400, 'Main file must be a .tex document');
    run(`UPDATE projects SET main_file_id = ? WHERE id = ?`, e.id, project.id);
  }
  touch(project.id);
  projectEvent(project.id);
  res.json({ ok: true });
});

projectsRouter.delete('/:id', async (req, res) => {
  const { project, role } = access(req, 'viewer');
  if (role === 'owner') {
    assertNotExternal(req);
    run(`DELETE FROM projects WHERE id = ?`, project.id);
    await removeProjectData(project.id);
  } else {
    run(`DELETE FROM members WHERE project_id = ? AND user_id = ?`, project.id, req.user.id);
    kick(project.id, req.user.id);
  }
  res.json({ ok: true });
});

projectsRouter.post('/:id/copy', async (req, res) => {
  assertNotExternal(req);
  const { project } = access(req, 'viewer');
  const id = insertProject(req.user.id, cleanName(req.body?.name, `${project.name} (copy)`), project.compiler);
  const entities = listEntities(project.id);
  const idMap = new Map();
  const texts = [];
  // Parents first.
  const ordered = [];
  const visit = (parentId) => {
    for (const e of entities.filter((x) => (x.parentId ?? null) === parentId)) {
      ordered.push(e);
      if (e.kind === 'folder') visit(e.id);
    }
  };
  visit(null);
  for (const e of ordered) {
    const nid = newId();
    idMap.set(e.id, nid);
    run(
      `INSERT INTO entities (id, project_id, parent_id, name, kind, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
      nid,
      id,
      e.parentId ? idMap.get(e.parentId) : null,
      e.name,
      e.kind,
      e.size,
      now(),
    );
    if (e.kind === 'doc') texts.push([nid, await getText(project.id, e.id)]);
    if (e.kind === 'file') {
      await fs.mkdir(path.dirname(paths.blob(id, nid)), { recursive: true });
      await fs.copyFile(paths.blob(project.id, e.id), paths.blob(id, nid)).catch(() => {});
    }
  }
  await setTexts(id, texts);
  run(`UPDATE projects SET main_file_id = ? WHERE id = ?`, idMap.get(project.main_file_id) || null, id);
  await (await getShared(id)).flush();
  res.json({ id });
});

projectsRouter.get('/:id/download', async (req, res) => {
  const { project } = access(req, 'viewer');
  const entities = listEntities(project.id);
  const pathOf = entityPaths(entities);
  const files = {};
  for (const e of entities) {
    if (e.kind === 'doc') files[pathOf.get(e.id)] = new TextEncoder().encode(await getText(project.id, e.id));
    if (e.kind === 'file') {
      const data = await fs.readFile(paths.blob(project.id, e.id)).catch(() => null);
      if (data) files[pathOf.get(e.id)] = new Uint8Array(data);
    }
  }
  const zip = zipSync(files, { level: 6 });
  const safe = project.name.replace(/[^\w\- ]+/g, '_').trim() || 'project';
  res.setHeader('Content-Type', 'application/zip');
  res.setHeader('Content-Disposition', `attachment; filename="${safe}.zip"; filename*=UTF-8''${encodeURIComponent(project.name)}.zip`);
  res.end(Buffer.from(zip));
});

// ---------- sharing ----------

projectsRouter.post('/:id/tokens', (req, res) => {
  const { project } = access(req, 'owner');
  assertSharingAllowed(project);
  run(`UPDATE projects SET edit_token = ?, view_token = ? WHERE id = ?`, newId(18), newId(18), project.id);
  const updated = one(`SELECT * FROM projects WHERE id = ?`, project.id);
  res.json({ project: projectInfo(updated, 'owner') });
});

projectsRouter.patch('/:id/members/:userId', (req, res) => {
  const { project } = access(req, 'owner');
  assertSharingAllowed(project);
  const role = req.body?.role;
  if (!['editor', 'viewer'].includes(role)) throw new HttpError(400, 'Invalid role');
  run(`UPDATE members SET role = ? WHERE project_id = ? AND user_id = ?`, role, project.id, req.params.userId);
  kick(project.id, req.params.userId); // reconnect with the new permissions
  res.json({ ok: true });
});

projectsRouter.delete('/:id/members/:userId', (req, res) => {
  const { project } = access(req, 'owner');
  assertSharingAllowed(project);
  run(`DELETE FROM members WHERE project_id = ? AND user_id = ?`, project.id, req.params.userId);
  kick(project.id, req.params.userId);
  res.json({ ok: true });
});

export const joinRouter = express.Router();
joinRouter.post('/:token', requireUser, (req, res) => {
  const token = req.params.token;
  const project = one(`SELECT * FROM projects WHERE edit_token = ? OR view_token = ?`, token, token);
  if (!project) throw new HttpError(404, 'This link is invalid or has been revoked');
  if (project.sharing_locked && project.owner_id !== req.user.id) throw new HttpError(403, SHARING_LOCKED);
  const role = project.edit_token === token ? 'editor' : 'viewer';
  const current = roleFor(req.user.id, project.id);
  if (!current) {
    run(`INSERT INTO members (project_id, user_id, role) VALUES (?, ?, ?)`, project.id, req.user.id, role);
  } else if (current === 'viewer' && role === 'editor') {
    run(`UPDATE members SET role = 'editor' WHERE project_id = ? AND user_id = ?`, project.id, req.user.id);
  }
  res.json({ id: project.id });
});

// ---------- files ----------

projectsRouter.post('/:id/entities', async (req, res) => {
  const { project } = access(req, 'editor');
  const { parentId = null, name, kind } = req.body || {};
  let id;
  if (kind === 'folder') id = createFolder(project.id, parentId, name);
  else if (kind === 'doc') id = await createDoc(project.id, parentId, name, '');
  else throw new HttpError(400, 'Invalid kind');
  touch(project.id);
  treeEvent(project.id);
  res.json({ id });
});

projectsRouter.post('/:id/upload', upload.array('files'), async (req, res) => {
  const { project } = access(req, 'editor');
  const parentId = req.body?.parentId || null;
  // Optional relative paths (folder uploads) arrive as a JSON array aligned with the files.
  let relPaths = [];
  try {
    relPaths = JSON.parse(req.body?.paths || '[]');
  } catch {}
  const ids = [];
  for (const [i, f] of (req.files || []).entries()) {
    const name = /[^\x00-\xff]/.test(f.originalname) ? f.originalname : Buffer.from(f.originalname, 'latin1').toString('utf8');
    const rel = typeof relPaths[i] === 'string' && relPaths[i] ? relPaths[i] : name;
    ids.push(await putFileAtPath(project.id, parentId, rel, f.buffer));
  }
  if (!project.main_file_id) await finalizeProject(project.id);
  touch(project.id);
  treeEvent(project.id);
  projectEvent(project.id);
  res.json({ ids });
});

projectsRouter.patch('/:id/entities/:entityId', (req, res) => {
  const { project } = access(req, 'editor');
  moveEntity(project.id, req.params.entityId, { name: req.body?.name, parentId: req.body?.parentId });
  touch(project.id);
  treeEvent(project.id);
  res.json({ ok: true });
});

projectsRouter.delete('/:id/entities/:entityId', async (req, res) => {
  const { project } = access(req, 'editor');
  getEntity(project.id, req.params.entityId);
  await deleteEntity(project.id, req.params.entityId);
  touch(project.id);
  treeEvent(project.id);
  projectEvent(project.id);
  res.json({ ok: true });
});

projectsRouter.get('/:id/files/:entityId', async (req, res) => {
  const { project } = access(req, 'viewer');
  const e = getEntity(project.id, req.params.entityId);
  if (e.kind === 'folder') throw new HttpError(400, 'Is a folder');
  res.setHeader('Content-Disposition', `inline; filename*=UTF-8''${encodeURIComponent(e.name)}`);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; sandbox");
  if (e.kind === 'doc') {
    res.type('text/plain; charset=utf-8').send(await getText(project.id, e.id));
  } else {
    res.type(path.extname(e.name) || 'application/octet-stream');
    res.sendFile(paths.blob(project.id, e.id));
  }
});

// ---------- compile ----------

export async function waitForState(projectId, stateVectorB64, timeoutMs = 3000) {
  let wanted;
  try {
    wanted = Y.decodeStateVector(new Uint8Array(Buffer.from(stateVectorB64, 'base64')));
  } catch {
    return;
  }
  const shared = await getShared(projectId);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const have = Y.decodeStateVector(Y.encodeStateVector(shared.doc));
    if ([...wanted].every(([client, clock]) => (have.get(client) || 0) >= clock)) return;
    await new Promise((r) => setTimeout(r, 50));
  }
}

projectsRouter.post('/:id/compile', async (req, res) => {
  const { project } = access(req, 'viewer');
  // The client sends its Yjs state vector so we compile only after its latest edits
  // (which travel over the WebSocket) have reached the server.
  if (typeof req.body?.stateVector === 'string') await waitForState(project.id, req.body.stateVector);
  let result;
  try {
    result = await compileProject(project, {
      stopOnFirstError: !!req.body?.stopOnFirstError,
      user: req.user.id,
      signal: closeSignal(res),
    });
  } catch (err) {
    if (err instanceof QueueBusyError) return sendQueueError(res, err);
    if (res.destroyed) return; // the browser gave up while the compile was queued
    throw err;
  }
  res.json(result);
});

// Polled by the editor while its compile request is pending: queue position for the button.
projectsRouter.get('/:id/compile/status', (req, res) => {
  const { project } = access(req, 'viewer');
  res.set('Cache-Control', 'no-store');
  res.json(compileQueue.status(project.id) || { state: 'idle' });
});

projectsRouter.post('/:id/clear-cache', async (req, res) => {
  const { project } = access(req, 'editor');
  await clearCache(project.id);
  res.json({ ok: true });
});

projectsRouter.get('/:id/builds/:buildId/:file', (req, res) => {
  const { project } = access(req, 'viewer');
  const allowed = { 'output.pdf': 'application/pdf', 'output.log': 'text/plain; charset=utf-8', 'latexmk.txt': 'text/plain; charset=utf-8' };
  const type = allowed[req.params.file];
  if (!type || !/^[\w-]+$/.test(req.params.buildId)) throw new HttpError(404, 'Not found');
  res.type(type);
  res.setHeader('Cache-Control', 'private, max-age=3600');
  if (req.query.download) {
    const safe = project.name.replace(/[^\w\- ]+/g, '_').trim() || 'output';
    res.setHeader('Content-Disposition', `attachment; filename="${safe}.pdf"`);
  }
  res.sendFile(path.join(paths.build(project.id, req.params.buildId), req.params.file), (err) => {
    if (err && !res.headersSent) res.status(404).json({ error: 'Build output not found' });
  });
});

async function synctexFor(req) {
  const { project } = access(req, 'viewer');
  const buildId = String(req.query.buildId || '');
  if (!/^[\w-]+$/.test(buildId)) throw new HttpError(400, 'Missing build id');
  const file = path.join(paths.build(project.id, buildId), 'output.synctex.gz');
  try {
    return await loadSynctex(file, [paths.compileDir(project.id), '/compile']);
  } catch {
    throw new HttpError(404, 'No SyncTeX data for this build');
  }
}

projectsRouter.get('/:id/synctex/pdf', async (req, res) => {
  const data = await synctexFor(req);
  const files = new Set(entityPaths(listEntities(req.params.id)).values());
  const hit = inverseSearch(data, Number(req.query.page), Number(req.query.x), Number(req.query.y), (f) => files.has(f));
  res.json({ hit });
});

projectsRouter.get('/:id/synctex/code', async (req, res) => {
  const data = await synctexFor(req);
  res.json({ boxes: forwardSearch(data, String(req.query.file || ''), Number(req.query.line)) });
});

export async function compilerStatus() {
  return { backend: await detectBackend(), queue: compileQueue.stats() };
}

