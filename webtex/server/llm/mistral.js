// Mistral La Plateforme chat completions: POST {base}/v1/chat/completions (SSE, `data: [DONE]`).
// The wire format resembles OpenAI's, but Mistral rejects unknown fields (no stream_options; usage
// comes in the last chunk anyway) and reasoning models stream `content` as a list of typed chunks.
import { LlmError } from './errors.js';
import { joinUrl, openStream, parseJson, sse } from './http.js';
import { contentText, normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const body = { model, messages: [], stream: true };
  if (system) body.messages.push({ role: 'system', content: system });
  for (const m of normalizeTurns(messages)) body.messages.push(m);
  if (maxTokens) body.max_tokens = maxTokens;
  if (temperature != null) body.temperature = temperature;
  Object.assign(body, provider.extraBody || {});
  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(provider.headers || {}) };
  if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
  return { url: joinUrl(provider.baseUrl, 'v1/chat/completions'), headers, body };
}

function deltaText(content) {
  // Reasoning ("thinking") chunks are not shown; only text chunks.
  if (Array.isArray(content)) return contentText(content.filter((c) => typeof c === 'string' || c?.type === 'text'));
  return contentText(content);
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let u = usage(null, null);
  let finishReason = null;
  for await (const ev of sse(chunks)) {
    if (ev.data === '[DONE]') break;
    const data = parseJson(ev.data, provider.id);
    if (data.object === 'error' || data.error) throw new LlmError('upstream', String(data.message || data.error?.message || 'Provider error'), { provider: provider.id });
    for (const choice of data.choices || []) {
      const text = deltaText(choice.delta?.content);
      if (text) yield { type: 'text', text };
      if (choice.finish_reason) finishReason = choice.finish_reason;
    }
    if (data.usage) u = usage(data.usage.prompt_tokens, data.usage.completion_tokens);
  }
  yield { type: 'done', usage: u, finishReason };
}
