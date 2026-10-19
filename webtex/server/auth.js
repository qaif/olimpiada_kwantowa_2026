import crypto from 'node:crypto';
import express from 'express';
import { config } from './config.js';
import { one, run } from './db.js';
import { HttpError, newId, now, parseCookies } from './util.js';

const COOKIE = 'webtex_sid';
const SESSION_TTL = 30 * 24 * 3600 * 1000;

function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
  return `scrypt$${salt.toString('base64')}$${hash.toString('base64')}`;
}

function verifyPassword(password, stored) {
  const [scheme, saltB64, hashB64] = stored.split('$');
  if (scheme !== 'scrypt') return false;
  const expected = Buffer.from(hashB64, 'base64');
  const actual = crypto.scryptSync(password, Buffer.from(saltB64, 'base64'), expected.length, { N: 16384, r: 8, p: 1 });
  return crypto.timingSafeEqual(expected, actual);
}

// External (platform SSO) users have no real email: theirs is a synthetic placeholder.
export const publicUser = (u) =>
  u && { id: u.id, email: u.external_id ? null : u.email, name: u.name, external: !!u.external_id };

export function userFromCookieHeader(header) {
  const sid = parseCookies(header)[COOKIE];
  if (!sid) return null;
  const row = one(
    `SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ? AND s.expires_at > ?`,
    sid,
    now(),
  );
  return row || null;
}

/** Express middleware: sets req.user (or null). */
export function loadUser(req, _res, next) {
  req.user = userFromCookieHeader(req.headers.cookie);
  next();
}

export function requireUser(req, _res, next) {
  if (!req.user) throw new HttpError(401, 'Not signed in');
  next();
}

export function startSession(res, userId) {
  const sid = newId(32);
  run(`INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)`, sid, userId, now() + SESSION_TTL);
  res.cookie(COOKIE, sid, {
    httpOnly: true,
    sameSite: 'lax',
    secure: config.secureCookies,
    maxAge: SESSION_TTL,
    path: '/',
  });
}

// Very small in-memory brute-force guard for login attempts.
const attempts = new Map();
function throttle(key) {
  const t = now();
  const entry = attempts.get(key) || { count: 0, reset: t + 15 * 60 * 1000 };
  if (t > entry.reset) Object.assign(entry, { count: 0, reset: t + 15 * 60 * 1000 });
  entry.count++;
  attempts.set(key, entry);
  if (entry.count > 20) throw new HttpError(429, 'Too many attempts, try again later');
}

export const authRouter = express.Router();

authRouter.get('/me', (req, res) => {
  const { enabled, platformUrl } = config.integration;
  res.json({
    user: publicUser(req.user),
    allowRegistration: config.allowRegistration,
    ...(enabled ? { integration: true, platformUrl } : {}),
  });
});

// In integration mode accounts come only from the platform's SSO.
const PLATFORM_ONLY = 'Accounts are managed by the olympiad platform: open the editor from the platform';
function noPasswordAuth(_req, _res, next) {
  if (config.integration.enabled) throw new HttpError(403, PLATFORM_ONLY);
  next();
}

authRouter.post('/register', noPasswordAuth, (req, res) => {
  if (!config.allowRegistration) throw new HttpError(403, 'Registration is disabled');
  const email = String(req.body?.email || '').trim().toLowerCase();
  const name = String(req.body?.name || '').trim() || email.split('@')[0];
  const password = String(req.body?.password || '');
  if (!/^[^\s@]+@[^\s@]+$/.test(email)) throw new HttpError(400, 'Invalid email address');
  if (password.length < 8) throw new HttpError(400, 'Password must have at least 8 characters');
  if (one(`SELECT 1 FROM users WHERE email = ?`, email)) throw new HttpError(409, 'This email is already registered');
  const id = newId();
  run(
    `INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)`,
    id,
    email,
    name.slice(0, 80),
    hashPassword(password),
    now(),
  );
  startSession(res, id);
  res.json({ user: publicUser(one(`SELECT * FROM users WHERE id = ?`, id)) });
});

authRouter.post('/login', noPasswordAuth, (req, res) => {
  const email = String(req.body?.email || '').trim().toLowerCase();
  const password = String(req.body?.password || '');
  throttle(`${req.ip}|${email}`);
  const user = one(`SELECT * FROM users WHERE email = ?`, email);
  if (!user || !verifyPassword(password, user.password_hash)) throw new HttpError(401, 'Wrong email or password');
  startSession(res, user.id);
  res.json({ user: publicUser(user) });
});

authRouter.post('/logout', (req, res) => {
  const sid = parseCookies(req.headers.cookie)[COOKIE];
  if (sid) run(`DELETE FROM sessions WHERE id = ?`, sid);
  res.clearCookie(COOKIE, { path: '/' });
  res.json({ ok: true });
});

authRouter.patch('/me', requireUser, (req, res) => {
  if (req.user.external_id) throw new HttpError(403, PLATFORM_ONLY);
  const name = String(req.body?.name || '').trim();
  if (name) run(`UPDATE users SET name = ? WHERE id = ?`, name.slice(0, 80), req.user.id);
  if (req.body?.newPassword) {
    if (!verifyPassword(String(req.body.currentPassword || ''), req.user.password_hash)) {
      throw new HttpError(401, 'Current password is wrong');
    }
    if (String(req.body.newPassword).length < 8) throw new HttpError(400, 'Password must have at least 8 characters');
    run(`UPDATE users SET password_hash = ? WHERE id = ?`, hashPassword(String(req.body.newPassword)), req.user.id);
  }
  res.json({ user: publicUser(one(`SELECT * FROM users WHERE id = ?`, req.user.id)) });
});

setInterval(() => run(`DELETE FROM sessions WHERE expires_at < ?`, now()), 3600 * 1000).unref();
