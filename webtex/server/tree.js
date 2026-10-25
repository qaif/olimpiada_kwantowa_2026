// File tree helpers: entities live in SQLite, document text in the project's Y.Doc,
// binary files on disk under data/projects/<id>/blobs/<entityId>.
import fs from 'node:fs/promises';
import path from 'node:path';
import { paths } from './config.js';
import { all, one, run } from './db.js';
import { deleteTexts, setTexts, replaceText } from './collab.js';
import { HttpError, assertValidName, isEditableText, newId, now } from './util.js';

export const listEntities = (projectId) =>
  all(`SELECT id, parent_id AS parentId, name, kind, size FROM entities WHERE project_id = ? ORDER BY kind, name`, projectId);

export function entityPaths(entities) {
  const byId = new Map(entities.map((e) => [e.id, e]));
  const cache = new Map();
  const resolve = (e, depth = 0) => {
    if (cache.has(e.id)) return cache.get(e.id);
    if (depth > 64) throw new Error('Tree too deep or cyclic');
    const parent = e.parentId && byId.get(e.parentId);
    const p = parent ? `${resolve(parent, depth + 1)}/${e.name}` : e.name;
    cache.set(e.id, p);
    return p;
  };
  for (const e of entities) resolve(e);
  return cache; // id -> "folder/sub/name.tex"
}

export function getEntity(projectId, entityId) {
  const e = one(
    `SELECT id, parent_id AS parentId, name, kind, size FROM entities WHERE project_id = ? AND id = ?`,
    projectId,
    entityId,
  );
  if (!e) throw new HttpError(404, 'File not found');
  return e;
}

function assertParent(projectId, parentId) {
  if (!parentId) return null;
  const parent = getEntity(projectId, parentId);
  if (parent.kind !== 'folder') throw new HttpError(400, 'Parent is not a folder');
  return parentId;
}

export function findChild(projectId, parentId, name) {
  return one(
    `SELECT id, kind FROM entities WHERE project_id = ? AND parent_id IS ? AND name = ?`,
    projectId,
    parentId ?? null,
    name,
  );
}

export function insertEntity(projectId, parentId, name, kind, size = null, id = newId()) {
  run(
    `INSERT INTO entities (id, project_id, parent_id, name, kind, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    id,
    projectId,
    parentId ?? null,
    name,
    kind,
    size,
    now(),
  );
  return id;
}

export async function createDoc(projectId, parentId, name, content = '') {
  name = assertValidName(name);
  parentId = assertParent(projectId, parentId);
  if (findChild(projectId, parentId, name)) throw new HttpError(409, `"${name}" already exists`);
  const id = insertEntity(projectId, parentId, name, 'doc');
  await setTexts(projectId, [[id, content]]);
  return id;
}

export function createFolder(projectId, parentId, name) {
  name = assertValidName(name);
  parentId = assertParent(projectId, parentId);
  const existing = findChild(projectId, parentId, name);
  if (existing) {
    if (existing.kind === 'folder') return existing.id;
    throw new HttpError(409, `"${name}" already exists`);
  }
  return insertEntity(projectId, parentId, name, 'folder');
}

/** Creates (or replaces) a file from uploaded bytes. Text files become editable docs. */
export async function putFile(projectId, parentId, name, buffer) {
  name = assertValidName(name);
  parentId = assertParent(projectId, parentId);
  const editable = isEditableText(name, buffer);
  const existing = findChild(projectId, parentId, name);
  if (existing) {
    if (existing.kind === 'folder') throw new HttpError(409, `A folder named "${name}" already exists`);
    if (existing.kind === 'doc' && editable) {
      await replaceText(projectId, existing.id, buffer.toString('utf8'));
      return existing.id;
    }
    await deleteEntity(projectId, existing.id);
  }
  if (editable) {
    const id = insertEntity(projectId, parentId, name, 'doc');
    await setTexts(projectId, [[id, buffer.toString('utf8')]]);
    return id;
  }
  const id = insertEntity(projectId, parentId, name, 'file', buffer.length);
  const file = paths.blob(projectId, id);
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, buffer);
  return id;
}

/** Creates nested folders for a path like "a/b/c.tex" and stores the file. */
export async function putFileAtPath(projectId, rootParentId, relPath, buffer) {
  const parts = relPath.split('/').filter(Boolean);
  let parentId = rootParentId ?? null;
  for (const dir of parts.slice(0, -1)) parentId = createFolder(projectId, parentId, dir);
  return putFile(projectId, parentId, parts.at(-1), buffer);
}

export function moveEntity(projectId, entityId, { name, parentId }) {
  const e = getEntity(projectId, entityId);
  const newName = name !== undefined ? assertValidName(name) : e.name;
  let newParent = parentId !== undefined ? assertParent(projectId, parentId || null) : e.parentId;
  // Refuse moving a folder into itself or its descendants.
  for (let cur = newParent; cur; cur = getEntity(projectId, cur).parentId) {
    if (cur === entityId) throw new HttpError(400, 'Cannot move a folder into itself');
  }
  const clash = findChild(projectId, newParent, newName);
  if (clash && clash.id !== entityId) throw new HttpError(409, `"${newName}" already exists there`);
  run(`UPDATE entities SET name = ?, parent_id = ? WHERE id = ?`, newName, newParent ?? null, entityId);
}

export async function deleteEntity(projectId, entityId) {
  const entities = listEntities(projectId);
  const doomed = new Set([entityId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const e of entities) {
      if (!doomed.has(e.id) && doomed.has(e.parentId)) {
        doomed.add(e.id);
        grew = true;
      }
    }
  }
  const docs = [];
  for (const e of entities) {
    if (!doomed.has(e.id)) continue;
    run(`DELETE FROM entities WHERE id = ?`, e.id);
    if (e.kind === 'doc') docs.push(e.id);
    if (e.kind === 'file') await fs.rm(paths.blob(projectId, e.id), { force: true });
  }
  if (docs.length) await deleteTexts(projectId, docs);
  run(`UPDATE projects SET main_file_id = NULL WHERE id = ? AND main_file_id IN (${[...doomed].map(() => '?').join(',')})`, projectId, ...doomed);
}

/** Picks a sensible main file: a root-level .tex containing \documentclass, preferring main.tex. */
export async function guessMainFile(projectId, getText) {
  const entities = listEntities(projectId);
  const tex = entities.filter((e) => e.kind === 'doc' && e.name.toLowerCase().endsWith('.tex'));
  if (!tex.length) return null;
  const depth = (e) => {
    let d = 0;
    for (let cur = e; cur?.parentId; cur = entities.find((x) => x.id === cur.parentId)) d++;
    return d;
  };
  const scored = [];
  for (const e of tex) {
    const text = await getText(projectId, e.id);
    const hasClass = /^[^%\n]*\\documentclass/m.test(text);
    scored.push({ e, score: (hasClass ? 0 : 100) + depth(e) * 10 + (e.name === 'main.tex' ? 0 : 1) });
  }
  scored.sort((a, b) => a.score - b.score);
  return scored[0].e.id;
}
