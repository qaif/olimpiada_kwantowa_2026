// Ollama native chat API: POST {base}/api/chat with stream: true → NDJSON, one object per line,
// the last one with done: true and token counts. Models are discovered with GET /api/tags.
import { LlmError } from './errors.js';
import { joinUrl, ndjson, openStream } from './http.js';
import { normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const body = { model, messages: [], stream: true, options: {} };
  if (system) body.messages.push({ role: 'system', content: system });
  for (const m of normalizeTurns(messages)) body.messages.push(m);
  if (maxTokens) body.options.num_predict = maxTokens;
  if (temperature != null) body.options.temperature = temperature;
  if (provider.keepAlive) body.keep_alive = provider.keepAlive;
  Object.assign(body, provider.extraBody || {});
  const headers = { 'Content-Type': 'application/json', Accept: 'application/x-ndjson', ...(provider.headers || {}) };
  if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
  return { url: joinUrl(provider.baseUrl, 'api/chat'), headers, body };
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let u = usage(null, null);
  let finishReason = null;
  for await (const data of ndjson(chunks, provider.id)) {
    if (data.error) throw new LlmError('upstream', String(data.error), { provider: provider.id });
    // message.thinking (reasoning models) is not shown.
    if (data.message?.content) yield { type: 'text', text: data.message.content };
    if (data.done) {
      finishReason = data.done_reason || 'stop';
      u = usage(data.prompt_eval_count, data.eval_count);
      break;
    }
  }
  yield { type: 'done', usage: u, finishReason };
}

export async function listModels(provider, { signal } = {}) {
  const res = await fetch(joinUrl(provider.baseUrl, 'api/tags'), {
    headers: provider.apiKey ? { Authorization: `Bearer ${provider.apiKey}` } : {},
    signal: AbortSignal.any([AbortSignal.timeout(3000), signal].filter(Boolean)),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return (data.models || []).map((m) => ({ id: m.name || m.model, label: m.name || m.model })).filter((m) => m.id);
}
