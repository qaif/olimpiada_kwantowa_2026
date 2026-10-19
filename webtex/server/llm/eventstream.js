// AWS event stream framing (application/vnd.amazon.eventstream), used by Bedrock ConverseStream.
// Message: [total length u32][headers length u32][prelude CRC32][headers][payload][message CRC32],
// big-endian, CRC32 = zlib's (IEEE). Header: [name len u8][name][type u8][value].
import zlib from 'node:zlib';
import { LlmError } from './errors.js';

const PRELUDE = 12;
const MAX_MESSAGE = 16 * 1024 * 1024;

function readHeaders(buf) {
  const headers = {};
  let o = 0;
  while (o < buf.length) {
    const nameLen = buf.readUInt8(o);
    o += 1;
    const name = buf.toString('utf8', o, o + nameLen);
    o += nameLen;
    const type = buf.readUInt8(o);
    o += 1;
    let value;
    switch (type) {
      case 0: value = true; break;
      case 1: value = false; break;
      case 2: value = buf.readInt8(o); o += 1; break;
      case 3: value = buf.readInt16BE(o); o += 2; break;
      case 4: value = buf.readInt32BE(o); o += 4; break;
      case 5: value = buf.readBigInt64BE(o); o += 8; break;
      case 6:
      case 7: {
        const len = buf.readUInt16BE(o);
        o += 2;
        value = type === 7 ? buf.toString('utf8', o, o + len) : buf.subarray(o, o + len);
        o += len;
        break;
      }
      case 8: value = new Date(Number(buf.readBigInt64BE(o))); o += 8; break;
      case 9: value = buf.subarray(o, o + 16).toString('hex'); o += 16; break;
      default:
        throw new Error(`unknown header type ${type}`);
    }
    headers[name] = value;
  }
  return headers;
}

/** Decodes a byte stream into { headers, payload } messages, verifying both CRCs. */
export async function* decodeEventStream(chunks, provider = null) {
  let buf = Buffer.alloc(0);
  const bad = (why) => new LlmError('upstream', `Malformed event stream from the provider (${why})`, { provider });
  for await (const chunk of chunks) {
    buf = buf.length ? Buffer.concat([buf, Buffer.from(chunk)]) : Buffer.from(chunk);
    while (buf.length >= PRELUDE) {
      const total = buf.readUInt32BE(0);
      const headersLen = buf.readUInt32BE(4);
      if (zlib.crc32(buf.subarray(0, 8)) !== buf.readUInt32BE(8)) throw bad('prelude checksum');
      if (total < PRELUDE + 4 + headersLen || total > MAX_MESSAGE) throw bad('length');
      if (buf.length < total) break;
      const message = buf.subarray(0, total);
      buf = buf.subarray(total);
      if (zlib.crc32(message.subarray(0, total - 4)) !== message.readUInt32BE(total - 4)) throw bad('message checksum');
      let headers;
      try {
        headers = readHeaders(message.subarray(PRELUDE, PRELUDE + headersLen));
      } catch (err) {
        throw bad(err.message);
      }
      yield { headers, payload: message.subarray(PRELUDE + headersLen, total - 4) };
    }
  }
  if (buf.length) throw bad('truncated');
}

/** Encodes one message with string headers (tests and fake servers). */
export function encodeEventStreamMessage(headers, payload) {
  const parts = [];
  for (const [name, value] of Object.entries(headers)) {
    const n = Buffer.from(name, 'utf8');
    const v = Buffer.from(String(value), 'utf8');
    const h = Buffer.alloc(1 + n.length + 1 + 2 + v.length);
    h.writeUInt8(n.length, 0);
    n.copy(h, 1);
    h.writeUInt8(7, 1 + n.length);
    h.writeUInt16BE(v.length, 2 + n.length);
    v.copy(h, 4 + n.length);
    parts.push(h);
  }
  const headerBuf = Buffer.concat(parts);
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(typeof payload === 'string' ? payload : JSON.stringify(payload), 'utf8');
  const total = PRELUDE + headerBuf.length + body.length + 4;
  const out = Buffer.alloc(total);
  out.writeUInt32BE(total, 0);
  out.writeUInt32BE(headerBuf.length, 4);
  out.writeUInt32BE(zlib.crc32(out.subarray(0, 8)), 8);
  headerBuf.copy(out, PRELUDE);
  body.copy(out, PRELUDE + headerBuf.length);
  out.writeUInt32BE(zlib.crc32(out.subarray(0, total - 4)), total - 4);
  return out;
}
