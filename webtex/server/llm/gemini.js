// Google Gemini API streaming: POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse,
// key in the x-goog-api-key header. Each SSE event is a GenerateContentResponse chunk.
import { LlmError } from './errors.js';
import { joinUrl, openStream, parseJson, sse } from './http.js';
import { normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const body = {
    contents: normalizeTurns(messages).map((m) => ({ role: m.role === 'assistant' ? 'model' : 'user', parts: [{ text: m.content }] })),
    generationConfig: {},
  };
  if (system) body.systemInstruction = { parts: [{ text: system }] };
  if (maxTokens) body.generationConfig.maxOutputTokens = maxTokens;
  if (temperature != null) body.generationConfig.temperature = temperature;
  Object.assign(body, provider.extraBody || {});
  const id = String(model).replace(/^models\//, '');
  const version = provider.apiVersion || 'v1beta';
  const url = `${joinUrl(provider.baseUrl, `${version}/models/${encodeURIComponent(id)}:streamGenerateContent`)}?alt=sse`;
  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(provider.headers || {}) };
  if (provider.apiKey) headers['x-goog-api-key'] = provider.apiKey;
  return { url, headers, body };
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let u = usage(null, null);
  let finishReason = null;
  for await (const ev of sse(chunks)) {
    let data = parseJson(ev.data, provider.id);
    if (Array.isArray(data)) data = data[0] || {};
    if (data.error) throw new LlmError('upstream', data.error.message || 'Provider error', { provider: provider.id });
    if (data.promptFeedback?.blockReason) {
      throw new LlmError('bad_request', `Blocked by the provider (${data.promptFeedback.blockReason})`, { provider: provider.id });
    }
    for (const cand of data.candidates || []) {
      for (const part of cand.content?.parts || []) {
        if (part.thought) continue; // thought summaries are not shown
        if (part.text) yield { type: 'text', text: part.text };
      }
      if (cand.finishReason) finishReason = cand.finishReason;
    }
    if (data.usageMetadata) u = usage(data.usageMetadata.promptTokenCount, data.usageMetadata.candidatesTokenCount);
  }
  yield { type: 'done', usage: u, finishReason };
}
