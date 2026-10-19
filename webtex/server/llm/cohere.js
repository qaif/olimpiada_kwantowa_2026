// Cohere Chat API v2 streaming: POST {base}/v2/chat with stream: true. SSE events carry a JSON
// object whose `type` is message-start, content-start, content-delta, content-end, message-end…
import { LlmError } from './errors.js';
import { joinUrl, openStream, parseJson, sse } from './http.js';
import { normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const body = { model, messages: [], stream: true };
  if (system) body.messages.push({ role: 'system', content: system });
  for (const m of normalizeTurns(messages)) body.messages.push(m);
  if (maxTokens) body.max_tokens = maxTokens;
  if (temperature != null) body.temperature = temperature;
  Object.assign(body, provider.extraBody || {});
  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(provider.headers || {}) };
  if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
  return { url: joinUrl(provider.baseUrl, 'v2/chat'), headers, body };
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let u = usage(null, null);
  let finishReason = null;
  for await (const ev of sse(chunks)) {
    if (ev.data === '[DONE]') break;
    const data = parseJson(ev.data, provider.id);
    const type = data.type || ev.event;
    if (type === 'content-delta') {
      const text = data.delta?.message?.content?.text;
      if (text) yield { type: 'text', text };
    } else if (type === 'message-end') {
      finishReason = data.delta?.finish_reason || null;
      const tokens = data.delta?.usage?.tokens || data.delta?.usage?.billed_units || {};
      u = usage(tokens.input_tokens, tokens.output_tokens);
      if (finishReason === 'ERROR') throw new LlmError('upstream', data.delta?.error || 'Provider error', { provider: provider.id });
    } else if (type === 'error' || (data.message && !data.type)) {
      throw new LlmError('upstream', String(data.message || 'Provider error'), { provider: provider.id });
    }
  }
  yield { type: 'done', usage: u, finishReason };
}
