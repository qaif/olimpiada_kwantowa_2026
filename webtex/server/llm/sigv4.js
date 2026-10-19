// AWS Signature Version 4 (header-based), as documented in "Create a signed AWS API request".
// Pure: no clock or environment access unless the caller omits `date`.
import crypto from 'node:crypto';

const hmac = (key, data) => crypto.createHmac('sha256', key).update(data, 'utf8').digest();
export const sha256Hex = (data) => crypto.createHash('sha256').update(data ?? '').digest('hex');

/** RFC 3986 encoding as SigV4 wants it: everything but A-Z a-z 0-9 - _ . ~ */
export const uriEncode = (s) =>
  encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

export const amzDate = (date) => date.toISOString().replace(/[:-]|\.\d{3}/g, '');

export function signingKey(secretAccessKey, dateStamp, region, service) {
  const kDate = hmac(`AWS4${secretAccessKey}`, dateStamp);
  const kRegion = hmac(kDate, region);
  const kService = hmac(kRegion, service);
  return hmac(kService, 'aws4_request');
}

function canonicalUri(pathname, service) {
  if (!pathname || pathname === '/') return '/';
  // Every service except S3 encodes each (already URL-encoded) path segment once more.
  return pathname
    .split('/')
    .map((seg) => (service === 's3' ? uriEncode(decodeURIComponent(seg)) : uriEncode(seg)))
    .join('/');
}

function canonicalQuery(searchParams) {
  return [...searchParams.entries()]
    .map(([k, v]) => [uriEncode(k), uriEncode(v)])
    .sort(([ak, av], [bk, bv]) => (ak < bk ? -1 : ak > bk ? 1 : av < bv ? -1 : av > bv ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');
}

/**
 * Signs a request. Every header passed in (plus host and x-amz-date, and x-amz-security-token
 * when a session token is given) is signed. Returns the headers to send (without `host`, which
 * fetch derives from the URL) and the intermediate values for debugging/tests.
 */
export function signRequest({ method, url, headers = {}, body = '', region, service, accessKeyId, secretAccessKey, sessionToken, date = new Date() }) {
  const u = new URL(url);
  const stamp = amzDate(date);
  const dateStamp = stamp.slice(0, 8);
  const all = {};
  for (const [k, v] of Object.entries(headers)) all[k.toLowerCase()] = String(v);
  all.host = u.host;
  all['x-amz-date'] = stamp;
  if (sessionToken) all['x-amz-security-token'] = sessionToken;

  const names = Object.keys(all).sort();
  const canonicalHeaders = names.map((n) => `${n}:${all[n].trim().replace(/\s+/g, ' ')}\n`).join('');
  const signedHeaders = names.join(';');
  const payloadHash = sha256Hex(body);
  const canonicalRequest = [method.toUpperCase(), canonicalUri(u.pathname, service), canonicalQuery(u.searchParams), canonicalHeaders, signedHeaders, payloadHash].join('\n');

  const scope = `${dateStamp}/${region}/${service}/aws4_request`;
  const stringToSign = ['AWS4-HMAC-SHA256', stamp, scope, sha256Hex(canonicalRequest)].join('\n');
  const signature = crypto.createHmac('sha256', signingKey(secretAccessKey, dateStamp, region, service)).update(stringToSign, 'utf8').digest('hex');
  const authorization = `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;

  const out = {};
  for (const n of names) if (n !== 'host') out[n] = all[n];
  out.authorization = authorization;
  return { headers: out, canonicalRequest, stringToSign, signature, authorization };
}
