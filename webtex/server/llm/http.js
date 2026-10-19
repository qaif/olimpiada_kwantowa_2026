// HTTP + stream plumbing shared by the adapters: one fetch with a total deadline, an idle timeout
// between chunks and the caller's abort signal, plus parsers for the three streaming formats
// providers use (Server-Sent Events, NDJSON, AWS eventstream lives in eventstream.js).
import { LlmError, codeForStatus, providerMessage } from './errors.js';

export const DEFAULT_TIMEOUT_MS = 120_000;
export const DEFAULT_IDLE_MS = 60_000;

/**
 * Starts a request and returns { response, chunks } where `chunks` is an async iterable of
 * Uint8Array that enforces the idle timeout. Non-2xx responses throw an LlmError with the
 * provider's own message. All failures are LlmError.
 */
export async function openStream(url, { method = 'POST', headers = {}, body, signal, timeoutMs = DEFAULT_TIMEOUT_MS, idleMs = DEFAULT_IDLE_MS, provider = null }) {
  const total = AbortSignal.timeout(timeoutMs);
  const idle = new AbortController();
  const combined = AbortSignal.any([signal, total, idle.signal].filter(Boolean));
  let idleTimer = null;
  const armIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => idle.abort(new DOMException('idle', 'TimeoutError')), idleMs);
    idleTimer.unref?.();
  };
  const fail = (err) => {
    clearTimeout(idleTimer);
    if (err instanceof LlmError) return err;
    if (signal?.aborted) return new LlmError('aborted', 'Request cancelled', { provider });
    if (total.aborted || idle.signal.aborted || err?.name === 'TimeoutError') {
      return new LlmError('timeout', 'The provider did not answer in time', { provider });
    }
    return new LlmError('network', `Cannot reach the provider (${err?.cause?.code || err?.message || 'network error'})`, { provider, cause: err });
  };

  armIdle();
  let response;
  try {
    response = await fetch(url, { method, headers, body, signal: combined });
  } catch (err) {
    throw fail(err);
  }
  if (!response.ok) {
    let text = '';
    try {
      text = await response.text();
    } catch {}
    clearTimeout(idleTimer);
    const message = providerMessage(text) || `HTTP ${response.status}`;
    throw new LlmError(codeForStatus(response.status), message, { status: response.status, provider });
  }
  if (!response.body) {
    clearTimeout(idleTimer);
    throw new LlmError('upstream', 'Empty response from the provider', { provider });
  }

  async function* chunks() {
    try {
      armIdle();
      for await (const chunk of response.body) {
        armIdle();
        yield chunk;
      }
    } catch (err) {
      throw fail(err);
    } finally {
      clearTimeout(idleTimer);
      // Stops the download when the consumer breaks out early (client disconnect).
      response.body.cancel?.().catch?.(() => {});
    }
  }
  return { response, chunks: chunks() };
}

/** Splits a byte stream into text lines (\n, \r\n or \r), decoding UTF-8 across chunk borders. */
export async function* lines(chunks) {
  const decoder = new TextDecoder();
  let buf = '';
  for await (const chunk of chunks) {
    buf += decoder.decode(chunk, { stream: true });
    let i;
    while ((i = buf.search(/\r\n|\r|\n/)) >= 0) {
      // A lone \r at the very end might be the first half of \r\n: wait for more data.
      if (buf[i] === '\r' && i === buf.length - 1) break;
      const line = buf.slice(0, i);
      buf = buf.slice(i + (buf[i] === '\r' && buf[i + 1] === '\n' ? 2 : 1));
      yield line;
    }
  }
  buf += decoder.decode();
  if (buf) yield buf.replace(/\r$/, '');
}

/** Server-Sent Events (WHATWG): yields { event, data, id } per dispatched event. */
export async function* sse(chunks) {
  let event = '';
  let data = [];
  let id = '';
  for await (const line of lines(chunks)) {
    if (line === '') {
      if (data.length) yield { event: event || 'message', data: data.join('\n'), id };
      event = '';
      data = [];
      continue;
    }
    if (line.startsWith(':')) continue; // comment / keep-alive
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') data.push(value);
    else if (field === 'event') event = value;
    else if (field === 'id') id = value;
  }
  if (data.length) yield { event: event || 'message', data: data.join('\n'), id };
}

/** Newline-delimited JSON (Ollama): yields parsed objects, skipping blank lines. */
export async function* ndjson(chunks, provider = null) {
  for await (const line of lines(chunks)) {
    if (!line.trim()) continue;
    yield parseJson(line, provider);
  }
}

export function parseJson(text, provider = null) {
  try {
    return JSON.parse(text);
  } catch {
    throw new LlmError('upstream', 'Malformed data in the provider stream', { provider });
  }
}

/** Joins a base URL and a path without doubling or dropping slashes. */
export function joinUrl(base, path) {
  return `${String(base).replace(/\/+$/, '')}/${String(path).replace(/^\/+/, '')}`;
}
