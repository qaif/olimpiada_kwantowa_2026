import { test } from 'node:test';
import assert from 'node:assert/strict';
import zlib from 'node:zlib';
import { sha256Hex, signRequest, signingKey } from '../llm/sigv4.js';
import { decodeEventStream, encodeEventStreamMessage } from '../llm/eventstream.js';

// Values from the AWS documentation (Signature Version 4 examples and the aws-sig-v4-test-suite),
// all with the documented example credentials and 2015-08-30T12:36:00Z.
const SECRET = 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY';
const DATE = new Date('2015-08-30T12:36:00Z');

test('signing key derivation matches the AWS documentation example', () => {
  assert.equal(signingKey(SECRET, '20150830', 'us-east-1', 'iam').toString('hex'), 'c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9');
});

test('IAM ListUsers example: canonical request hash and signature', () => {
  const r = signRequest({
    method: 'GET',
    url: 'https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8' },
    body: '',
    region: 'us-east-1',
    service: 'iam',
    accessKeyId: 'AKIDEXAMPLE',
    secretAccessKey: SECRET,
    date: DATE,
  });
  assert.equal(
    r.canonicalRequest,
    [
      'GET',
      '/',
      'Action=ListUsers&Version=2010-05-08',
      'content-type:application/x-www-form-urlencoded; charset=utf-8',
      'host:iam.amazonaws.com',
      'x-amz-date:20150830T123600Z',
      '',
      'content-type;host;x-amz-date',
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    ].join('\n'),
  );
  assert.equal(sha256Hex(r.canonicalRequest), 'f536975d06c0309214f805bb90ccff089219ecd68b2577efef23edd43b7e1a59');
  assert.equal(r.signature, '5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7');
  assert.equal(
    r.authorization,
    'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/iam/aws4_request, SignedHeaders=content-type;host;x-amz-date, Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7',
  );
  assert.equal(r.headers.host, undefined, 'host is left to fetch');
  assert.equal(r.headers['x-amz-date'], '20150830T123600Z');
});

test('aws-sig-v4-test-suite get-vanilla and post-vanilla', () => {
  const base = { url: 'https://example.amazonaws.com/', region: 'us-east-1', service: 'service', accessKeyId: 'AKIDEXAMPLE', secretAccessKey: SECRET, date: DATE };
  assert.equal(signRequest({ ...base, method: 'GET' }).signature, '5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31');
  assert.equal(signRequest({ ...base, method: 'POST' }).signature, '5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b');
});

test('session token is signed; query strings are sorted and RFC 3986 encoded', () => {
  const r = signRequest({
    method: 'GET',
    url: "https://example.amazonaws.com/a%20b/c?b=2&a=x y&a=1&e=(!)",
    region: 'eu-west-1',
    service: 'bedrock',
    accessKeyId: 'AK',
    secretAccessKey: 'SK',
    sessionToken: 'TOKEN',
    date: DATE,
  });
  const lines = r.canonicalRequest.split('\n');
  assert.equal(lines[1], '/a%2520b/c');
  assert.equal(lines[2], 'a=1&a=x%20y&b=2&e=%28%21%29');
  assert.ok(r.authorization.includes('SignedHeaders=host;x-amz-date;x-amz-security-token,'));
  assert.equal(r.headers['x-amz-security-token'], 'TOKEN');
});

// ---------- event stream framing ----------

test('event stream: hand-built frame per the AWS spec decodes, including split chunks', async () => {
  // Build one message byte by byte (not with our encoder): header ":event-type" (string) = "x".
  const name = Buffer.from(':event-type');
  const value = Buffer.from('contentBlockDelta');
  const header = Buffer.concat([Buffer.from([name.length]), name, Buffer.from([7]), Buffer.from([0, value.length]), value]);
  const payload = Buffer.from('{"delta":{"text":"ż"}}');
  const total = 12 + header.length + payload.length + 4;
  const prelude = Buffer.alloc(8);
  prelude.writeUInt32BE(total, 0);
  prelude.writeUInt32BE(header.length, 4);
  const preludeCrc = Buffer.alloc(4);
  preludeCrc.writeUInt32BE(zlib.crc32(prelude));
  const withoutCrc = Buffer.concat([prelude, preludeCrc, header, payload]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(withoutCrc));
  const message = Buffer.concat([withoutCrc, crc]);
  assert.deepEqual(message, encodeEventStreamMessage({ ':event-type': 'contentBlockDelta' }, payload));

  async function* oneByteAtATime() {
    for (const b of Buffer.concat([message, message])) yield Uint8Array.of(b);
  }
  const out = [];
  for await (const m of decodeEventStream(oneByteAtATime())) out.push([m.headers[':event-type'], m.payload.toString()]);
  assert.deepEqual(out, [
    ['contentBlockDelta', '{"delta":{"text":"ż"}}'],
    ['contentBlockDelta', '{"delta":{"text":"ż"}}'],
  ]);
});

test('event stream: corrupted or truncated data is rejected', async () => {
  const good = encodeEventStreamMessage({ ':event-type': 'metadata' }, { usage: {} });
  const drain = async (buf) => {
    for await (const _ of decodeEventStream([buf]));
  };
  const flipped = Buffer.from(good);
  flipped[flipped.length - 6] ^= 0xff;
  await assert.rejects(drain(flipped), /message checksum/);
  const badPrelude = Buffer.from(good);
  badPrelude[2] ^= 0x01;
  await assert.rejects(drain(badPrelude), /prelude checksum|length/);
  await assert.rejects(drain(good.subarray(0, good.length - 3)), /truncated/);
});
