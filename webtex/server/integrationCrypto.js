// Pure crypto helpers for the Quantum Olympiad platform integration (docs/tasks/TEX-01.md).
// No imports from config/db, so tests and scripts/integration-smoke.mjs can use them directly.
import crypto from 'node:crypto';

export const AUDIENCE = 'webtex';
export const CLOCK_SKEW_SEC = 30;
export const SIGNATURE_TOLERANCE_SEC = 300;
// The platform issues tokens with exp = iat + 120. Anything much longer is not one of ours.
export const MAX_TOKEN_TTL_SEC = 300;

const hmac = (key, data) => crypto.createHmac('sha256', key).update(data).digest();

/**
 * Domain-separated keys derived from WEBTEX_SHARED_KEY (same as Django):
 *   sso_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-sso-v1")
 *   api_key = HMAC-SHA256(key=WEBTEX_SHARED_KEY, msg="webtex-api-v1")
 * The shared key is used as its UTF-8 bytes.
 */
export function deriveKeys(sharedKey) {
  const key = Buffer.from(String(sharedKey), 'utf8');
  return { ssoKey: hmac(key, 'webtex-sso-v1'), apiKey: hmac(key, 'webtex-api-v1') };
}

export class TokenError extends Error {
  /** code: 'invalid' (400) | 'expired' (410) | 'used' (409) */
  constructor(code, message, claims = null) {
    super(message);
    this.code = code;
    // Set only when the signature was valid (expired / used tokens), so the claims can be trusted.
    this.claims = claims;
  }
}

const b64url = (buf) => Buffer.from(buf).toString('base64url');
const B64URL = /^[A-Za-z0-9_-]*$/;

function decodeJsonPart(part) {
  if (!B64URL.test(part)) throw new TokenError('invalid', 'Malformed token');
  try {
    const value = JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    throw new TokenError('invalid', 'Malformed token');
  }
}

/** Signs an HS256 JWT (used by tests and the smoke script; the platform signs in Django). */
export function signJwt(claims, key) {
  const head = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64url(JSON.stringify(claims));
  return `${head}.${body}.${b64url(hmac(key, `${head}.${body}`))}`;
}

const isInt = (v) => Number.isInteger(v);

/**
 * Verifies an HS256 JWT and its standard claims. Returns the claims or throws TokenError.
 * The signature is checked before anything in the payload is trusted. One-time use of `jti`
 * is NOT checked here (see consumeJti), but its presence and shape are.
 */
export function verifyJwt(token, key, { issuer, audience = AUDIENCE, now = Math.floor(Date.now() / 1000), skew = CLOCK_SKEW_SEC } = {}) {
  if (typeof token !== 'string' || token.length > 8192) throw new TokenError('invalid', 'Malformed token');
  const parts = token.split('.');
  if (parts.length !== 3) throw new TokenError('invalid', 'Malformed token');
  const [head, body, sig] = parts;
  const header = decodeJsonPart(head);
  if (header.alg !== 'HS256') throw new TokenError('invalid', 'Unsupported token algorithm');
  if (header.typ !== undefined && header.typ !== 'JWT') throw new TokenError('invalid', 'Unsupported token type');
  if (!B64URL.test(sig)) throw new TokenError('invalid', 'Malformed token');
  const expected = hmac(key, `${head}.${body}`);
  const actual = Buffer.from(sig, 'base64url');
  if (actual.length !== expected.length || !crypto.timingSafeEqual(actual, expected)) {
    throw new TokenError('invalid', 'Bad token signature');
  }
  const claims = decodeJsonPart(body);
  if (issuer !== undefined && claims.iss !== issuer) throw new TokenError('invalid', 'Wrong token issuer');
  const aud = claims.aud;
  if (!(aud === audience || (Array.isArray(aud) && aud.includes(audience)))) throw new TokenError('invalid', 'Wrong token audience');
  if (!isInt(claims.iat) || !isInt(claims.exp)) throw new TokenError('invalid', 'Token without iat/exp');
  if (claims.iat > now + skew) throw new TokenError('invalid', 'Token issued in the future');
  if (claims.exp <= claims.iat || claims.exp - claims.iat > MAX_TOKEN_TTL_SEC) throw new TokenError('invalid', 'Token lifetime out of range');
  if (now > claims.exp + skew) throw new TokenError('expired', 'Token expired', claims);
  if (typeof claims.jti !== 'string' || !/^[\w-]{16,256}$/.test(claims.jti)) throw new TokenError('invalid', 'Token without a valid jti');
  return claims;
}

/**
 * Marks a token id as used. `store.add(jti, expiresAtSec)` must return false when the id was
 * already present. Throws TokenError('used') on replay.
 */
export function consumeJti(store, claims, { skew = CLOCK_SKEW_SEC } = {}) {
  if (!store.add(claims.jti, claims.exp + skew)) throw new TokenError('used', 'Token already used', claims);
}

/** In-memory jti store (tests; the server uses the used_jti table with the same interface). */
export function memoryJtiStore({ now = () => Math.floor(Date.now() / 1000) } = {}) {
  const seen = new Map();
  return {
    add(jti, expiresAt) {
      const t = now();
      for (const [k, exp] of seen) if (exp < t) seen.delete(k);
      if (seen.has(jti)) return false;
      seen.set(jti, expiresAt);
      return true;
    },
  };
}

// ---------- server-to-server request signatures ----------

const signedPayload = (t, method, path, body) =>
  Buffer.concat([Buffer.from(`${t}.${method.toUpperCase()}.${path}.`, 'utf8'), Buffer.isBuffer(body) ? body : Buffer.from(body ?? '', 'utf8')]);

/** Returns the X-Webtex-Signature header value: `t=<unix>,v1=<hex HMAC-SHA256(api_key, "{t}.{METHOD}.{path}.{raw body}")>`. */
export function signRequest({ method, path, body = '', key, now = Math.floor(Date.now() / 1000) }) {
  return `t=${now},v1=${hmac(key, signedPayload(now, method, path, body)).toString('hex')}`;
}

/**
 * Verifies X-Webtex-Signature. `path` must exclude the query string; `body` is the raw body
 * (Buffer or string, '' for none). Returns true/false.
 */
export function verifyRequestSignature(header, { method, path, body = '', key, now = Math.floor(Date.now() / 1000), tolerance = SIGNATURE_TOLERANCE_SEC }) {
  if (typeof header !== 'string' || header.length > 1024) return false;
  let t = null;
  const sigs = [];
  for (const part of header.split(',')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k === 't' && /^\d{1,12}$/.test(v)) t = Number(v);
    else if (k === 'v1' && /^[0-9a-f]{64}$/i.test(v)) sigs.push(Buffer.from(v, 'hex'));
  }
  if (t === null || !sigs.length || Math.abs(now - t) > tolerance) return false;
  const expected = hmac(key, signedPayload(t, method, path, body));
  // Check every candidate without short-circuiting on the first match.
  let ok = false;
  for (const s of sigs) ok = crypto.timingSafeEqual(s, expected) || ok;
  return ok;
}
