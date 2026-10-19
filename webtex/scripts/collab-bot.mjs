// Dev helper: joins a project as a second (bot) user over the real collaboration protocol,
// shows a remote cursor and types a line into a document. Useful for testing collaboration.
//   node scripts/collab-bot.mjs <baseUrl> <joinToken> <entityId> [text]
import WebSocket from 'ws';
import * as Y from 'yjs';
import * as syncProtocol from 'y-protocols/sync';
import * as awarenessProtocol from 'y-protocols/awareness';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';

const [base = 'http://localhost:3000', token, entityId, text = '% Hello from the collaboration bot!\n'] = process.argv.slice(2);
if (!token || !entityId) {
  console.error('usage: node scripts/collab-bot.mjs <baseUrl> <joinToken> <entityId> [text]');
  process.exit(1);
}

let cookie = '';
async function api(method, url, body) {
  const res = await fetch(base + url, { method, headers: { 'content-type': 'application/json', cookie }, body: body && JSON.stringify(body) });
  const sc = res.headers.get('set-cookie');
  if (sc) cookie = sc.split(';')[0];
  const data = await res.json();
  if (!res.ok) throw new Error(`${url}: ${data.error}`);
  return data;
}

const email = `bot-${Date.now()}@webtex.test`;
await api('POST', '/api/auth/register', { email, name: 'Collab Bot', password: `bot-${Math.random()}` });
const { id: projectId } = await api('POST', `/api/join/${token}`);

const doc = new Y.Doc();
const awareness = new awarenessProtocol.Awareness(doc);
const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws/projects/${projectId}`, { headers: { cookie } });
const send = (fn) => {
  const enc = encoding.createEncoder();
  fn(enc);
  ws.send(encoding.toUint8Array(enc));
};

doc.on('update', (u, origin) => origin !== ws && send((e) => (encoding.writeVarUint(e, 0), syncProtocol.writeUpdate(e, u))));
awareness.on('update', ({ added, updated, removed }, origin) => {
  if (origin === 'local') send((e) => (encoding.writeVarUint(e, 1), encoding.writeVarUint8Array(e, awarenessProtocol.encodeAwarenessUpdate(awareness, added.concat(updated, removed)))));
});

let synced = false;
ws.on('open', () => send((e) => (encoding.writeVarUint(e, 0), syncProtocol.writeSyncStep1(e, doc))));
ws.on('message', async (data, isBinary) => {
  if (!isBinary) return console.log('event', JSON.parse(data.toString()).type);
  const dec = decoding.createDecoder(new Uint8Array(data));
  const type = decoding.readVarUint(dec);
  if (type === 1) return awarenessProtocol.applyAwarenessUpdate(awareness, decoding.readVarUint8Array(dec), ws);
  const enc = encoding.createEncoder();
  encoding.writeVarUint(enc, 0);
  const st = syncProtocol.readSyncMessage(dec, enc, doc, ws);
  if (encoding.length(enc) > 1) ws.send(encoding.toUint8Array(enc));
  if (st === 1 && !synced) {
    synced = true;
    const ytext = doc.getMap('texts').get(entityId);
    if (!ytext) throw new Error('document not found');
    console.log('synced; document has', ytext.length, 'chars');
    awareness.setLocalStateField('user', { id: 'bot', name: 'Collab Bot', color: '#d946ef', colorLight: '#d946ef33' });
    awareness.setLocalStateField('fileId', entityId);
    // Type after the first line, character by character, moving our cursor along.
    const start = ytext.toString().indexOf('\n') + 1;
    for (const [i, ch] of [...text].entries()) {
      ytext.insert(start + i, ch);
      const pos = Y.createRelativePositionFromTypeIndex(ytext, start + i + 1);
      awareness.setLocalStateField('cursor', { anchor: pos, head: pos });
      await new Promise((r) => setTimeout(r, 40));
    }
    console.log('typed; waiting 20s so you can see the cursor…');
    setTimeout(() => {
      ws.close();
      process.exit(0);
    }, 20000);
  }
});
