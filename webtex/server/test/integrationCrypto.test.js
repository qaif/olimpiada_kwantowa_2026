import { test } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {
  TokenError,
  consumeJti,
  deriveKeys,
  memoryJtiStore,
  signJwt,
  signRequest,
  verifyJwt,
  verifyRequestSignature,
} from '../integrationCrypto.js';

// Vectors computed independently with Python's hmac/hashlib/base64 (what Django uses):
//   K = b'webtex-test-shared-key-0123456789abcdef'
//   hmac.new(K, b'webtex-sso-v1', hashlib.sha256).hexdigest()
const SHARED = 'webtex-test-shared-key-0123456789abcdef';
const SSO_HEX = '96d10fa26a580558ec522f4e0f0effd61afb7dc88597cd40c44d66de52e7bbde';
const API_HEX = 'f02ee0bafb4c6ad7807443e069a15a351e185edb7245f1762dcc2cb6546bf2cd';
const { ssoKey, apiKey } = deriveKeys(SHARED);

const T0 = 1760000000;
const claims = (over = {}) => ({
  iss: 'olimpiada',
  aud: 'webtex',
  iat: T0,
  exp: T0 + 120,
  jti: crypto.randomBytes(16).toString('base64url'),
  sub: '42',
  project: 'oq-2026/s7/p3',
  ...over,
});
const opts = { issuer: 'olimpiada', now: T0 + 5 };
const rejects = (fn, code) => assert.throws(fn, (err) => err instanceof TokenError && err.code === code);

test('key derivation matches the Python reference vectors', () => {
  assert.equal(ssoKey.toString('hex'), SSO_HEX);
  assert.equal(apiKey.toString('hex'), API_HEX);
  assert.equal(ssoKey.length, 32);
  assert.notEqual(SSO_HEX, API_HEX);
});

test('a JWT signed in Python (compact JSON, no padding) verifies', () => {
  const py =
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJvbGltcGlhZGEiLCJhdWQiOiJ3ZWJ0ZXgiLCJpYXQiOjE3NjAwMDAwMDAsImV4cCI6MTc2MDAwMDEyMCwianRpIjoiQUFBQUFBQUFBQUFBQUFBQUFBQUFBQSIsInN1YiI6IjQyIiwicHJvamVjdCI6Im9xLTIwMjYvczcvcDMifQ.GAXRn-izN9962ocdIxGeg-YabaGj5XvONUZyGIlgBsM';
  const c = verifyJwt(py, ssoKey, opts);
  assert.equal(c.sub, '42');
  assert.equal(c.project, 'oq-2026/s7/p3');
});

test('valid token round-trips', () => {
  const c = claims({ name: 'Ala', share: false });
  assert.deepEqual(verifyJwt(signJwt(c, ssoKey), ssoKey, opts), c);
});

test('bad signature, wrong key and tampered payload are rejected as invalid', () => {
  const token = signJwt(claims(), ssoKey);
  const [h, p, s] = token.split('.');
  rejects(() => verifyJwt(`${h}.${p}.${s.slice(0, -2)}AA`, ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(token, apiKey, opts), 'invalid'); // api key must not sign SSO tokens
  const forged = Buffer.from(JSON.stringify(claims({ sub: '1' }))).toString('base64url');
  rejects(() => verifyJwt(`${h}.${forged}.${s}`, ssoKey, opts), 'invalid');
  rejects(() => verifyJwt('not-a-jwt', ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(undefined, ssoKey, opts), 'invalid');
});

test('alg none and other algorithms are rejected', () => {
  const body = Buffer.from(JSON.stringify(claims())).toString('base64url');
  for (const alg of ['none', 'None', 'HS512', 'RS256']) {
    const head = Buffer.from(JSON.stringify({ alg, typ: 'JWT' })).toString('base64url');
    rejects(() => verifyJwt(`${head}.${body}.`, ssoKey, opts), 'invalid');
    const sig = crypto.createHmac('sha256', ssoKey).update(`${head}.${body}`).digest('base64url');
    rejects(() => verifyJwt(`${head}.${body}.${sig}`, ssoKey, opts), 'invalid');
  }
});

test('expired tokens are rejected (with 30 s skew) and carry their verified claims', () => {
  const token = signJwt(claims({ return_url: 'https://olimpiada.example/me/' }), ssoKey);
  assert.ok(verifyJwt(token, ssoKey, { ...opts, now: T0 + 150 })); // exp + 30
  assert.throws(
    () => verifyJwt(token, ssoKey, { ...opts, now: T0 + 151 }),
    (err) => err.code === 'expired' && err.claims.return_url === 'https://olimpiada.example/me/',
  );
});

test('iat in the future beyond skew, missing exp and overlong lifetime are rejected', () => {
  assert.ok(verifyJwt(signJwt(claims({ iat: T0 + 35, exp: T0 + 155 }), ssoKey), ssoKey, opts));
  rejects(() => verifyJwt(signJwt(claims({ iat: T0 + 40, exp: T0 + 160 }), ssoKey), ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(signJwt(claims({ exp: undefined }), ssoKey), ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(signJwt(claims({ exp: T0 + 3600 }), ssoKey), ssoKey, opts), 'invalid');
});

test('wrong audience or issuer is rejected', () => {
  rejects(() => verifyJwt(signJwt(claims({ aud: 'jitsi' }), ssoKey), ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(signJwt(claims({ aud: undefined }), ssoKey), ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(signJwt(claims({ iss: 'evil' }), ssoKey), ssoKey, opts), 'invalid');
  assert.ok(verifyJwt(signJwt(claims({ aud: ['webtex', 'x'] }), ssoKey), ssoKey, opts));
});

test('jti must be present and is accepted only once', () => {
  rejects(() => verifyJwt(signJwt(claims({ jti: undefined }), ssoKey), ssoKey, opts), 'invalid');
  rejects(() => verifyJwt(signJwt(claims({ jti: 'short' }), ssoKey), ssoKey, opts), 'invalid');
  const store = memoryJtiStore({ now: () => T0 + 5 });
  const c = verifyJwt(signJwt(claims(), ssoKey), ssoKey, opts);
  consumeJti(store, c);
  rejects(() => consumeJti(store, c), 'used');
  consumeJti(store, claims()); // a different token is fine
});

// ---------- request signatures ----------

const BODY = '{"sub":"42","project":"oq-2026/s7/p3"}';
const req = (over = {}) => ({ method: 'POST', path: '/api/integration/build', body: BODY, key: apiKey, now: T0, ...over });

test('request signature matches the Python reference vector', () => {
  assert.equal(signRequest(req()), 't=1760000000,v1=a0f8c8d39b5f2523934c399bb987afa0b959f007ea93cc9170cc2c01ede7be62');
  assert.equal(
    signRequest(req({ method: 'GET', path: '/api/integration/health', body: '' })),
    't=1760000000,v1=4204d14a7ec72ed92aec6361d4c73357c384423bd295880ee82977226ca247c1',
  );
});

test('valid signature verifies, with Buffer or string body', () => {
  const header = signRequest(req());
  assert.equal(verifyRequestSignature(header, req()), true);
  assert.equal(verifyRequestSignature(header, req({ body: Buffer.from(BODY) })), true);
  assert.equal(verifyRequestSignature(header, req({ method: 'post' })), true);
  assert.equal(verifyRequestSignature(` ${header.replace(',', ', ')} `, req()), true);
});

test('stale or future timestamps (> 300 s) are rejected', () => {
  const header = signRequest(req());
  assert.equal(verifyRequestSignature(header, req({ now: T0 + 300 })), true);
  assert.equal(verifyRequestSignature(header, req({ now: T0 + 301 })), false);
  assert.equal(verifyRequestSignature(header, req({ now: T0 - 301 })), false);
});

test('tampered body, path, method or key is rejected', () => {
  const header = signRequest(req());
  assert.equal(verifyRequestSignature(header, req({ body: BODY.replace('42', '43') })), false);
  assert.equal(verifyRequestSignature(header, req({ body: `${BODY} ` })), false);
  assert.equal(verifyRequestSignature(header, req({ path: '/api/integration/sharing' })), false);
  assert.equal(verifyRequestSignature(header, req({ method: 'PUT' })), false);
  assert.equal(verifyRequestSignature(header, req({ key: ssoKey })), false);
  assert.equal(verifyRequestSignature(header.replace(/t=\d+/, `t=${T0 + 1}`), req()), false);
});

test('missing or malformed signature headers are rejected', () => {
  for (const h of [undefined, '', 't=1760000000', 'v1=00', `t=abc,v1=${'0'.repeat(64)}`, `t=${T0},v1=${'z'.repeat(64)}`]) {
    assert.equal(verifyRequestSignature(h, req()), false, String(h));
  }
});
