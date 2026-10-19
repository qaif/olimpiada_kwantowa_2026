// Client side of the collaboration protocol (see server/collab.js): one WebSocket per project
// carrying y-protocols sync/awareness messages (binary) and JSON project events (text).
import * as syncProtocol from 'y-protocols/sync';
import * as awarenessProtocol from 'y-protocols/awareness';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';

const MSG_SYNC = 0;
const MSG_AWARENESS = 1;

export class ProjectConnection {
  constructor(projectId, doc, { onEvent, onStatus, onSynced, onClose } = {}) {
    this.projectId = projectId;
    this.doc = doc;
    this.awareness = new awarenessProtocol.Awareness(doc);
    this.handlers = { onEvent, onStatus, onSynced, onClose };
    this.synced = false;
    this.ws = null;
    this.retries = 0;
    this.stopped = false;

    this.onDocUpdate = (update, origin) => {
      if (origin === this) return;
      const enc = encoding.createEncoder();
      encoding.writeVarUint(enc, MSG_SYNC);
      syncProtocol.writeUpdate(enc, update);
      this.send(encoding.toUint8Array(enc));
    };
    this.onAwarenessUpdate = ({ added, updated, removed }, origin) => {
      if (origin !== 'local') return;
      const enc = encoding.createEncoder();
      encoding.writeVarUint(enc, MSG_AWARENESS);
      encoding.writeVarUint8Array(enc, awarenessProtocol.encodeAwarenessUpdate(this.awareness, added.concat(updated, removed)));
      this.send(encoding.toUint8Array(enc));
    };
    doc.on('update', this.onDocUpdate);
    this.awareness.on('update', this.onAwarenessUpdate);
    this.onUnload = () => awarenessProtocol.removeAwarenessStates(this.awareness, [doc.clientID], 'window unload');
    window.addEventListener('beforeunload', this.onUnload);
    this.connect();
  }

  setStatus(status) {
    this.status = status;
    this.handlers.onStatus?.(status);
  }

  connect() {
    if (this.stopped) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws/projects/${this.projectId}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    this.setStatus('connecting');

    ws.onopen = () => {
      this.retries = 0;
      this.setStatus('connected');
      const enc = encoding.createEncoder();
      encoding.writeVarUint(enc, MSG_SYNC);
      syncProtocol.writeSyncStep1(enc, this.doc);
      this.send(encoding.toUint8Array(enc));
      if (this.awareness.getLocalState() !== null) {
        const aenc = encoding.createEncoder();
        encoding.writeVarUint(aenc, MSG_AWARENESS);
        encoding.writeVarUint8Array(aenc, awarenessProtocol.encodeAwarenessUpdate(this.awareness, [this.doc.clientID]));
        this.send(encoding.toUint8Array(aenc));
      }
    };

    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          this.handlers.onEvent?.(JSON.parse(event.data));
        } catch (err) {
          console.error(err);
        }
        return;
      }
      const decoder = decoding.createDecoder(new Uint8Array(event.data));
      const encoder = encoding.createEncoder();
      const type = decoding.readVarUint(decoder);
      if (type === MSG_SYNC) {
        encoding.writeVarUint(encoder, MSG_SYNC);
        const syncType = syncProtocol.readSyncMessage(decoder, encoder, this.doc, this);
        if (encoding.length(encoder) > 1) this.send(encoding.toUint8Array(encoder));
        if (syncType === syncProtocol.messageYjsSyncStep2 && !this.synced) {
          this.synced = true;
          this.handlers.onSynced?.();
        }
      } else if (type === MSG_AWARENESS) {
        awarenessProtocol.applyAwarenessUpdate(this.awareness, decoding.readVarUint8Array(decoder), this);
      }
    };

    ws.onclose = (event) => {
      this.ws = null;
      // Forget remote cursors; they are re-sent on reconnect.
      const others = [...this.awareness.getStates().keys()].filter((id) => id !== this.doc.clientID);
      awarenessProtocol.removeAwarenessStates(this.awareness, others, this);
      if (this.stopped) return;
      if (event.code === 4403) {
        this.stopped = true;
        this.setStatus('closed');
        this.handlers.onClose?.(event.reason);
        return;
      }
      this.setStatus('disconnected');
      const delay = Math.min(10000, 500 * 2 ** this.retries++);
      setTimeout(() => this.connect(), delay);
    };
  }

  send(msg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(msg);
  }

  destroy() {
    this.stopped = true;
    window.removeEventListener('beforeunload', this.onUnload);
    this.doc.off('update', this.onDocUpdate);
    this.awareness.off('update', this.onAwarenessUpdate);
    awarenessProtocol.removeAwarenessStates(this.awareness, [this.doc.clientID], 'destroy');
    this.ws?.close();
    this.awareness.destroy();
  }
}
