import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { paths } from './config.js';

fs.mkdirSync(path.dirname(paths.db), { recursive: true });

export const db = new DatabaseSync(paths.db);

db.exec(`
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  owner_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  main_file_id TEXT,
  compiler     TEXT NOT NULL DEFAULT 'pdflatex',
  edit_token   TEXT NOT NULL UNIQUE,
  view_token   TEXT NOT NULL UNIQUE,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       TEXT NOT NULL CHECK (role IN ('editor', 'viewer')),
  PRIMARY KEY (project_id, user_id)
);

-- File tree. kind: 'folder' | 'doc' (collaborative text, stored in the Y.Doc) | 'file' (binary blob)
CREATE TABLE IF NOT EXISTS entities (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id  TEXT,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL CHECK (kind IN ('folder', 'doc', 'file')),
  size       INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS entities_by_project ON entities(project_id);
`);

// ---------- migrations (idempotent) ----------

const hasColumn = (table, column) => db.prepare(`PRAGMA table_info(${table})`).all().some((c) => c.name === column);

// Platform integration (TEX-01). External users have external_id = "{iss}:{sub}", password_hash = '!'
// (never matches) and a synthetic, non-deliverable email ("ext:{external_id}") because email is NOT NULL UNIQUE.
if (!hasColumn('users', 'external_id')) db.exec(`ALTER TABLE users ADD COLUMN external_id TEXT`);
if (!hasColumn('projects', 'sharing_locked')) db.exec(`ALTER TABLE projects ADD COLUMN sharing_locked INTEGER NOT NULL DEFAULT 0`);
// AI assistant (TEX-02): ai_locked = 1 blocks every AI request for the project. Normal projects
// start unlocked; projects created by the platform's SSO start locked (D8) and follow the `ai` claim.
const addAiLocked = !hasColumn('projects', 'ai_locked');
if (addAiLocked) db.exec(`ALTER TABLE projects ADD COLUMN ai_locked INTEGER NOT NULL DEFAULT 0`);
db.exec(`
CREATE UNIQUE INDEX IF NOT EXISTS users_by_external_id ON users(external_id);

-- A project created by SSO, unique per (user, platform project key).
CREATE TABLE IF NOT EXISTS linked_projects (
  project_id  TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_key TEXT NOT NULL,
  scope       TEXT NOT NULL,
  return_url  TEXT,
  UNIQUE (user_id, project_key)
);
CREATE INDEX IF NOT EXISTS linked_projects_by_scope ON linked_projects(scope);

-- One-time SSO token ids, kept until the token would have expired anyway.
CREATE TABLE IF NOT EXISTS used_jti (
  jti        TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);
`);
// Platform projects that existed before the column: locked until the platform says otherwise.
if (addAiLocked) db.exec(`UPDATE projects SET ai_locked = 1 WHERE id IN (SELECT project_id FROM linked_projects)`);

// Version history (server/history.js). The content lives in files under projects/<id>/history/,
// shared between snapshots by hash; a row is a point in time pointing at them.
db.exec(`
CREATE TABLE IF NOT EXISTS snapshots (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  -- auto | compile | submission | manual | pre-restore | restore
  kind       TEXT NOT NULL,
  label      TEXT,
  meta       TEXT,                       -- JSON, e.g. what a restore restored
  authors    TEXT NOT NULL DEFAULT '[]', -- JSON array of user ids who changed the project since the previous snapshot
  actor_id   TEXT,                       -- who triggered it (compile, restore, named version)
  state_hash TEXT NOT NULL,              -- states/<hash>.gz: Y.encodeStateAsUpdate of the project's Y.Doc
  state_size INTEGER NOT NULL,
  tree       TEXT NOT NULL,              -- JSON { entities: [{ id, parentId, name, kind, size, sha256 }], mainFileId }
  tree_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_by_project ON snapshots(project_id, created_at);
`);

export const one = (sql, ...args) => db.prepare(sql).get(...args);
export const all = (sql, ...args) => db.prepare(sql).all(...args);
export const run = (sql, ...args) => db.prepare(sql).run(...args);

export function tx(fn) {
  db.exec('BEGIN');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}
