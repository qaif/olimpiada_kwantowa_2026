// Shared helpers for the LLM tests: a recording mock HTTP server on localhost and stream utilities.
import http from 'node:http';

/**
 * Starts a server on 127.0.0.1 with a random port. `handler(req, res, record)` answers; every
 * request is recorded as { method, url, path, headers, body, json }.
 */
export async function mockServer(handler) {
  const requests = [];
  const sockets = new Set();
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = Buffer.concat(chunks).toString('utf8');
    let json = null;
    try {
      json = JSON.parse(body);
    } catch {}
    const record = { method: req.method, url: req.url, path: req.url.split('?')[0], headers: req.headers, body, json, closed: false };
    res.on('close', () => (record.closed = true));
    requests.push(record);
    try {
      await handler(req, res, record);
    } catch (err) {
      if (!res.headersSent) res.writeHead(500);
      res.end(String(err));
    }
  });
  server.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}`,
    port,
    requests,
    close: () =>
      new Promise((resolve) => {
        for (const s of sockets) s.destroy();
        server.close(() => resolve());
      }),
  };
}

const tick = () => new Promise((r) => setImmediate(r));

/** Writes data in small pieces (splitting lines, events and multi-byte characters), then ends. */
export async function writeInPieces(res, data, size = 5) {
  const buf = Buffer.isBuffer(data) ? data : Buffer.from(data, 'utf8');
  for (let i = 0; i < buf.length; i += size) {
    res.write(buf.subarray(i, i + size));
    await tick();
  }
  res.end();
}

export const sseBody = (events) =>
  events.map((e) => (typeof e === 'string' ? `data: ${e}\n\n` : `${e.event ? `event: ${e.event}\n` : ''}data: ${JSON.stringify(e.data)}\n\n`)).join('');

/** Drains an adapter stream: { text, deltas, done }. */
export async function collect(iterable) {
  let text = '';
  let deltas = 0;
  let done = null;
  for await (const ev of iterable) {
    if (ev.type === 'text') {
      text += ev.text;
      deltas++;
    } else if (ev.type === 'done') done = ev;
  }
  return { text, deltas, done };
}

export const CONVERSATION = [
  { role: 'assistant', content: 'dropped: conversations must start with the user' },
  { role: 'user', content: 'Pierwsze' },
  { role: 'user', content: 'pytanie' },
  { role: 'assistant', content: 'Odpowiedź' },
  { role: 'user', content: 'Drugie pytanie' },
];
export const NORMALIZED = [
  { role: 'user', content: 'Pierwsze\n\npytanie' },
  { role: 'assistant', content: 'Odpowiedź' },
  { role: 'user', content: 'Drugie pytanie' },
];
