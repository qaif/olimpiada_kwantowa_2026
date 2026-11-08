// Real-time collaboration: one Y.Doc per project, holding a Y.Text per editable document
// (doc.getMap('texts') keyed by entity id). Speaks the standard y-protocols sync + awareness
// messages over binary WebSocket frames; JSON text frames carry project events (tree changes etc).
import fs from 'node:fs/promises';
import { WebSocketServer } from 'ws';
import * as Y from 'yjs';
import * as syncProtocol from 'y-protocols/sync';
import * as awarenessProtocol from 'y-protocols/awareness';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';
import { paths } from './config.js';
import { writeFileAtomic } from './util.js';

const MSG_SYNC = 0;
const MSG_AWARENESS = 1;
const PERSIST_DELAY = 1500;
const UNLOAD_DELAY = 60_000;

class SharedProject {
  constructor(id) {
    this.id = id;
    this.doc = new Y.Doc({ gc: true });
    this.awareness = new awarenessProtocol.Awareness(this.doc);
    this.awareness.setLocalState(null);
    this.conns = new Map(); // ws -> { user, readOnly, clientIds: Set }
    this.persistTimer = null;
    this.unloadTimer = null;
    this.ready = this.load();

    this.doc.on('update', (update, origin) => {
      const enc = encoding.createEncoder();
      encoding.writeVarUint(enc, MSG_SYNC);
      syncProtocol.writeUpdate(enc, update);
      const msg = encoding.toUint8Array(enc);
      for (const ws of this.conns.keys()) if (ws !== origin) send(ws, msg);
      this.schedulePersist();
      if (origin !== 'load') changeListener?.(this.id, this.conns.get(origin)?.user.id ?? null);
    });

    this.awareness.on('update', ({ added, updated, removed }, origin) => {
      const changed = added.concat(updated, removed);
      const meta = this.conns.get(origin);
      if (meta) {
        for (const id of added) meta.clientIds.add(id);
        for (const id of removed) meta.clientIds.delete(id);
      }
      const enc = encoding.createEncoder();
      encoding.writeVarUint(enc, MSG_AWARENESS);
      encoding.writeVarUint8Array(enc, awarenessProtocol.encodeAwarenessUpdate(this.awareness, changed));
      const msg = encoding.toUint8Array(enc);
      for (const ws of this.conns.keys()) send(ws, msg);
    });
  }

  async load() {
    try {
      const data = await fs.readFile(paths.ydoc(this.id));
      Y.applyUpdate(this.doc, new Uint8Array(data), 'load');
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
    if (this.persistTimer) {
      clearTimeout(this.persistTimer);
      this.persistTimer = null;
    }
  }

  get texts() {
    return this.doc.getMap('texts');
  }

  schedulePersist() {
    if (this.persistTimer) return;
    this.persistTimer = setTimeout(() => {
      this.persistTimer = null;
      this.persist().catch((err) => console.error(`[collab] persist ${this.id} failed`, err));
    }, PERSIST_DELAY);
  }

  async persist() {
    if (this.deleted) return;
    await writeFileAtomic(paths.ydoc(this.id), Y.encodeStateAsUpdate(this.doc));
  }

  async flush() {
    if (this.persistTimer) {
      clearTimeout(this.persistTimer);
      this.persistTimer = null;
      await this.persist();
    }
  }

  scheduleUnload() {
    clearTimeout(this.unloadTimer);
    if (this.conns.size > 0) return;
    this.unloadTimer = setTimeout(async () => {
      if (this.conns.size > 0) return;
      await this.flush();
      projects.delete(this.id);
      this.doc.destroy();
    }, UNLOAD_DELAY);
    this.unloadTimer.unref?.();
  }

  broadcast(event) {
    const msg = JSON.stringify(event);
    for (const ws of this.conns.keys()) if (ws.readyState === ws.OPEN) ws.send(msg);
  }
}

function send(ws, msg) {
  if (ws.readyState !== ws.OPEN) return;
  ws.send(msg, (err) => err && ws.terminate());
}

const projects = new Map();

// Told about every change of a project's documents, with the editing user when it came over a
// WebSocket (null for changes made by the server itself). Used by the version history.
let changeListener = null;
export function onDocChange(fn) {
  changeListener = fn;
}

/** Returns the loaded shared project (loading it from disk if needed). */
export async function getShared(projectId) {
  let p = projects.get(projectId);
  if (!p) {
    p = new SharedProject(projectId);
    projects.set(projectId, p);
    p.scheduleUnload();
  }
  await p.ready;
  return p;
}

export async function getText(projectId, entityId) {
  const p = await getShared(projectId);
  const t = p.texts.get(entityId);
  return t ? t.toString() : '';
}

export async function setTexts(projectId, entries) {
  const p = await getShared(projectId);
  p.doc.transact(() => {
    for (const [id, content] of entries) p.texts.set(id, new Y.Text(content));
  }, 'server');
}

export async function replaceText(projectId, entityId, content) {
  const p = await getShared(projectId);
  const existing = p.texts.get(entityId);
  if (!existing) return setTexts(projectId, [[entityId, content]]);
  p.doc.transact(() => {
    existing.delete(0, existing.length);
    existing.insert(0, content);
  }, 'server');
}

export async function deleteTexts(projectId, ids) {
  const p = await getShared(projectId);
  p.doc.transact(() => {
    for (const id of ids) p.texts.delete(id);
  }, 'server');
}

export function broadcast(projectId, event) {
  projects.get(projectId)?.broadcast(event);
}

/** Disconnects everybody (project deleted) or a single user (removed from project). */
export function kick(projectId, userId = null, { deleted = false } = {}) {
  const p = projects.get(projectId);
  if (!p) return;
  if (deleted) p.deleted = true;
  for (const [ws, meta] of p.conns) {
    if (!userId || meta.user.id === userId) ws.close(4403, deleted ? 'Project deleted' : 'Access revoked');
  }
  if (deleted) {
    clearTimeout(p.persistTimer);
    projects.delete(projectId);
  }
}

/** Disconnects everyone except `keepUserId` (the owner), e.g. when sharing gets locked. */
export function kickExcept(projectId, keepUserId) {
  const p = projects.get(projectId);
  if (!p) return;
  for (const [ws, meta] of p.conns) if (meta.user.id !== keepUserId) ws.close(4403, 'Access revoked');
}

export async function flushAll() {
  await Promise.all([...projects.values()].map((p) => p.flush()));
}

/**
 * Attaches the collaboration WebSocket endpoint: /ws/projects/:id
 * `authorize(req, projectId)` must return { user, role } or null.
 */
export function attachCollab(server, authorize) {
  const wss = new WebSocketServer({ noServer: true, maxPayload: 32 * 1024 * 1024 });

  server.on('upgrade', async (req, socket, head) => {
    const match = /^\/ws\/projects\/([\w-]+)$/.exec(new URL(req.url, 'http://x').pathname);
    const reject = (code, text) => {
      socket.write(`HTTP/1.1 ${code} ${text}\r\n\r\n`);
      socket.destroy();
    };
    if (!match) return reject(404, 'Not Found');
    // Protect against cross-site WebSocket hijacking.
    const origin = req.headers.origin;
    if (origin && new URL(origin).host !== req.headers.host) return reject(403, 'Forbidden');
    const access = authorize(req, match[1]);
    if (!access) return reject(403, 'Forbidden');
    wss.handleUpgrade(req, socket, head, (ws) => onConnection(ws, match[1], access));
  });

  return wss;
}

async function onConnection(ws, projectId, { user, role }) {
  ws.binaryType = 'arraybuffer';
  const queue = [];
  let shared = null;
  ws.on('message', (data, isBinary) => (shared ? handle(data, isBinary) : queue.push([data, isBinary])));

  shared = await getShared(projectId);
  clearTimeout(shared.unloadTimer);
  const meta = { user, readOnly: role === 'viewer', clientIds: new Set() };
  shared.conns.set(ws, meta);

  function handle(data, isBinary) {
    if (!isBinary) return; // clients only send binary frames
    try {
      const decoder = decoding.createDecoder(new Uint8Array(data));
      const encoder = encoding.createEncoder();
      const type = decoding.readVarUint(decoder);
      if (type === MSG_SYNC) {
        if (meta.readOnly && decoding.peekVarUint(decoder) !== syncProtocol.messageYjsSyncStep1) return;
        encoding.writeVarUint(encoder, MSG_SYNC);
        syncProtocol.readSyncMessage(decoder, encoder, shared.doc, ws);
        if (encoding.length(encoder) > 1) send(ws, encoding.toUint8Array(encoder));
      } else if (type === MSG_AWARENESS) {
        awarenessProtocol.applyAwarenessUpdate(shared.awareness, decoding.readVarUint8Array(decoder), ws);
      }
    } catch (err) {
      console.error('[collab] bad message', err);
      ws.close(1007, 'Bad message');
    }
  }

  ws.on('close', () => {
    shared.conns.delete(ws);
    awarenessProtocol.removeAwarenessStates(shared.awareness, [...meta.clientIds], null);
    shared.scheduleUnload();
  });

  // Start the sync handshake and share current presence.
  const enc = encoding.createEncoder();
  encoding.writeVarUint(enc, MSG_SYNC);
  syncProtocol.writeSyncStep1(enc, shared.doc);
  send(ws, encoding.toUint8Array(enc));
  const states = [...shared.awareness.getStates().keys()];
  if (states.length) {
    const aenc = encoding.createEncoder();
    encoding.writeVarUint(aenc, MSG_AWARENESS);
    encoding.writeVarUint8Array(aenc, awarenessProtocol.encodeAwarenessUpdate(shared.awareness, states));
    send(ws, encoding.toUint8Array(aenc));
  }
  for (const [data, isBinary] of queue.splice(0)) handle(data, isBinary);

  // Keep connections alive through proxies.
  const ping = setInterval(() => ws.readyState === ws.OPEN && ws.ping(), 25_000);
  ws.on('close', () => clearInterval(ping));
}
