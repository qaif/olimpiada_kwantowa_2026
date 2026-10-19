// Quantum Olympiad platform integration (docs/tasks/TEX-01.md), active with WEBTEX_INTEGRATION=true.
//   POST /api/integration/sso      browser, body {token}: one-time JWT from the platform → session + project
//   POST /api/integration/build    signed: fresh compile, responds with the PDF
//   POST /api/integration/sharing  signed: lock/unlock link sharing for every project in a scope
//   POST /api/integration/ai       signed: enable/disable the AI assistant for a scope (TEX-02)
//   GET  /api/integration/health   signed
// Mounted before the global JSON parser: signatures cover the raw request body.
import fs from 'node:fs/promises';
import path from 'node:path';
import express from 'express';
import { config, paths } from './config.js';
import { all, one, run } from './db.js';
import { startSession } from './auth.js';
import { kickExcept } from './collab.js';
import { setAiLocked, setAiLockedForScope } from './aiPolicy.js';
import { compileProject, detectBackend } from './compiler.js';
import { cleanName, createProjectWithFiles, escapeTex } from './projects.js';
import { olympiadSolution } from './templates.js';
import { TokenError, consumeJti, deriveKeys, verifyJwt, verifyRequestSignature } from './integrationCrypto.js';
import { newId, now } from './util.js';

let keys = null;
const getKeys = () => (keys ||= deriveKeys(config.integration.sharedKey));

const nowSec = () => Math.floor(Date.now() / 1000);

// Used SSO token ids live in the database, so a restart does not reopen the replay window.
const jtiStore = {
  add: (jti, expiresAt) => Number(run(`INSERT OR IGNORE INTO used_jti (jti, expires_at) VALUES (?, ?)`, jti, expiresAt).changes) === 1,
};
setInterval(() => run(`DELETE FROM used_jti WHERE expires_at < ?`, nowSec()), 10 * 60 * 1000).unref();

const rawBody = (req) => (Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0));

function jsonBody(req) {
  try {
    const value = JSON.parse(rawBody(req).toString('utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

const str = (v, max) => (typeof v === 'string' ? v.trim().slice(0, max) : '');

function safeUrl(v) {
  try {
    const u = new URL(String(v));
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch {
    return null;
  }
}

// One SSO at a time per (user, project key), so a double click cannot create two projects.
const locks = new Map();
async function withLock(key, fn) {
  const previous = locks.get(key) || Promise.resolve();
  const job = previous.catch(() => {}).then(fn);
  locks.set(key, job);
  try {
    return await job;
  } finally {
    if (locks.get(key) === job) locks.delete(key);
  }
}

function signed(req, res, next) {
  const ok = verifyRequestSignature(req.get('X-Webtex-Signature'), {
    method: req.method,
    path: req.originalUrl.split('?')[0],
    body: rawBody(req),
    key: getKeys().apiKey,
  });
  if (!ok) return res.status(401).json({ error: 'bad_signature' });
  next();
}

export const integrationRouter = express.Router();

integrationRouter.use((_req, res, next) => (config.integration.enabled ? next() : res.status(404).json({ error: 'Not found' })));
integrationRouter.use(express.raw({ type: () => true, limit: '1mb' }));

// ---------- SSO (from the browser) ----------

function sameOrigin(req) {
  const origin = req.get('Origin');
  if (!origin) return true;
  try {
    return new URL(origin).host === req.get('Host');
  } catch {
    return false;
  }
}

const TOKEN_ERRORS = { invalid: [400, 'invalid_token'], expired: [410, 'token_expired'], used: [409, 'token_used'] };

integrationRouter.post('/sso', async (req, res) => {
  // Same-origin JSON only: a cross-site form cannot log a victim into someone else's account.
  if (!sameOrigin(req)) return res.status(403).json({ error: 'bad_origin' });
  if (!req.is('application/json')) return res.status(415).json({ error: 'json_required' });

  let claims;
  try {
    claims = verifyJwt(jsonBody(req)?.token, getKeys().ssoKey, { issuer: config.integration.issuer });
    consumeJti(jtiStore, claims);
  } catch (err) {
    if (!(err instanceof TokenError)) throw err;
    const [status, error] = TOKEN_ERRORS[err.code];
    return res.status(status).json({ error, message: err.message, returnUrl: err.claims ? safeUrl(err.claims.return_url) : null });
  }

  const sub = str(claims.sub, 200);
  const projectKey = str(claims.project, 300);
  const scope = str(claims.scope, 300);
  if (!sub || !projectKey || !scope) return res.status(400).json({ error: 'invalid_token', message: 'Token without sub/project/scope' });
  const returnUrl = safeUrl(claims.return_url);
  const sharingLocked = claims.share === true ? 0 : 1;
  // TEX-02 D9: only a JSON `true` claim enables the AI assistant.
  const aiLocked = claims.ai === true ? 0 : 1;

  const externalId = `${claims.iss}:${sub}`;
  const name = str(claims.name, 80) || 'Uczestnik';
  run(
    `INSERT OR IGNORE INTO users (id, email, name, password_hash, external_id, created_at) VALUES (?, ?, ?, '!', ?, ?)`,
    newId(),
    `ext:${externalId}`,
    name,
    externalId,
    now(),
  );
  const user = one(`SELECT * FROM users WHERE external_id = ?`, externalId);
  if (user.name !== name) run(`UPDATE users SET name = ? WHERE id = ?`, name, user.id);

  const projectId = await withLock(`${user.id}|${projectKey}`, async () => {
    const link = one(`SELECT project_id FROM linked_projects WHERE user_id = ? AND project_key = ?`, user.id, projectKey);
    if (link) {
      const before = one(`SELECT sharing_locked FROM projects WHERE id = ?`, link.project_id);
      run(`UPDATE linked_projects SET scope = ?, return_url = ? WHERE project_id = ?`, scope, returnUrl, link.project_id);
      run(`UPDATE projects SET sharing_locked = ? WHERE id = ?`, sharingLocked, link.project_id);
      if (sharingLocked && !before.sharing_locked) kickExcept(link.project_id, user.id);
      setAiLocked([link.project_id], aiLocked);
      return link.project_id;
    }
    const title = cleanName(str(claims.title, 120), projectKey);
    const tex = olympiadSolution({ title: escapeTex(title), code: escapeTex(str(claims.code, 60)) });
    const id = await createProjectWithFiles(user.id, title, [['main.tex', tex]], { sharingLocked, aiLocked: !!aiLocked });
    run(
      `INSERT INTO linked_projects (project_id, user_id, project_key, scope, return_url) VALUES (?, ?, ?, ?, ?)`,
      id,
      user.id,
      projectKey,
      scope,
      returnUrl,
    );
    return id;
  });

  startSession(res, user.id);
  res.json({ projectId, lang: claims.lang === 'en' ? 'en' : 'pl' });
});

// ---------- server to server (signed) ----------

const MAX_ISSUES = 20;
const issueOut = (i) => ({ file: i.file ?? null, line: i.line ?? null, message: i.message });

integrationRouter.post('/build', signed, async (req, res) => {
  const body = jsonBody(req);
  const sub = typeof body?.sub === 'string' ? body.sub.trim() : '';
  const projectKey = typeof body?.project === 'string' ? body.project.trim() : '';
  if (!sub || !projectKey) return res.status(400).json({ error: 'bad_request' });

  const project = one(
    `SELECT p.* FROM linked_projects l
       JOIN users u ON u.id = l.user_id
       JOIN projects p ON p.id = l.project_id
      WHERE u.external_id = ? AND l.project_key = ?`,
    `${config.integration.issuer}:${sub}`,
    projectKey,
  );
  if (!project) return res.status(404).json({ error: 'no_project' });
  if ((await detectBackend()) === 'none') return res.status(503).json({ error: 'compiler_unavailable' });

  const result = await compileProject(project);
  if (result.status === 'unavailable') return res.status(503).json({ error: 'compiler_unavailable' });
  const errors = result.issues.filter((i) => i.level === 'error');

  if (result.status === 'success' && result.hasPdf) {
    // Read now: a later compile of the same project prunes old build directories.
    const pdf = await fs.readFile(path.join(paths.build(project.id, result.buildId), 'output.pdf')).catch(() => null);
    if (pdf) {
      res.set({
        'Content-Type': 'application/pdf',
        'Cache-Control': 'no-store',
        'X-Webtex-Errors': String(errors.length),
        'X-Webtex-Build': result.buildId,
      });
      return res.end(pdf);
    }
  }

  let issues = errors.slice(0, MAX_ISSUES).map(issueOut);
  if (!issues.length) {
    const message =
      result.status === 'timedout'
        ? 'Kompilacja przekroczyła limit czasu'
        : result.message || 'Kompilacja nie utworzyła pliku PDF';
    issues = [{ file: null, line: null, message }];
  }
  res.status(422).json({ error: 'compile_failed', issues });
});

integrationRouter.post('/sharing', signed, (req, res) => {
  const body = jsonBody(req);
  const scope = typeof body?.scope === 'string' ? body.scope.trim() : '';
  if (!scope || typeof body?.enabled !== 'boolean') return res.status(400).json({ error: 'bad_request' });
  const locked = body.enabled ? 0 : 1;
  const changed = run(
    `UPDATE projects SET sharing_locked = ?
      WHERE sharing_locked != ? AND id IN (SELECT project_id FROM linked_projects WHERE scope = ?)`,
    locked,
    locked,
    scope,
  ).changes;
  if (locked) {
    const rows = all(`SELECT p.id, p.owner_id FROM linked_projects l JOIN projects p ON p.id = l.project_id WHERE l.scope = ?`, scope);
    for (const r of rows) kickExcept(r.id, r.owner_id);
  }
  res.json({ projects: Number(changed) });
});

// TEX-02 D10: same contract as /sharing. Disabling aborts running answers and hides open panels.
integrationRouter.post('/ai', signed, (req, res) => {
  const body = jsonBody(req);
  const scope = typeof body?.scope === 'string' ? body.scope.trim() : '';
  if (!scope || typeof body?.enabled !== 'boolean') return res.status(400).json({ error: 'bad_request' });
  res.json({ projects: setAiLockedForScope(scope, !body.enabled) });
});

integrationRouter.get('/health', signed, async (_req, res) => {
  res.json({ ok: true, backend: await detectBackend() });
});
