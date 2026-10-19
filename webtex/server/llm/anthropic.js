// Anthropic Messages API streaming (POST {base}/v1/messages, SSE with typed events:
// message_start, content_block_start/delta/stop, message_delta, message_stop, ping, error).
import { LlmError } from './errors.js';
import { joinUrl, openStream, parseJson, sse } from './http.js';
import { normalizeTurns, usage } from './turns.js';

export const ANTHROPIC_VERSION = '2023-06-01';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const body = {
    model,
    max_tokens: maxTokens || 4096,
    messages: normalizeTurns(messages),
    stream: true,
  };
  if (system) body.system = system;
  // Current Claude models reject sampling parameters: only sent when explicitly configured.
  if (temperature != null) body.temperature = temperature;
  Object.assign(body, provider.extraBody || {});
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    'anthropic-version': provider.apiVersion || ANTHROPIC_VERSION,
    ...(provider.headers || {}),
  };
  if (provider.apiKey) headers['x-api-key'] = provider.apiKey;
  return { url: joinUrl(provider.baseUrl, 'v1/messages'), headers, body };
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let input = null;
  let output = null;
  let finishReason = null;
  for await (const ev of sse(chunks)) {
    const data = parseJson(ev.data, provider.id);
    const type = data.type || ev.event;
    if (type === 'message_start') {
      input = data.message?.usage?.input_tokens ?? input;
      output = data.message?.usage?.output_tokens ?? output;
    } else if (type === 'content_block_delta') {
      // thinking_delta / signature_delta / input_json_delta are not shown.
      if (data.delta?.type === 'text_delta' && data.delta.text) yield { type: 'text', text: data.delta.text };
    } else if (type === 'message_delta') {
      if (data.delta?.stop_reason) finishReason = data.delta.stop_reason;
      if (data.usage?.output_tokens != null) output = data.usage.output_tokens;
      if (data.usage?.input_tokens != null) input = data.usage.input_tokens;
    } else if (type === 'error') {
      const code = data.error?.type === 'overloaded_error' || data.error?.type === 'api_error' ? 'upstream' : data.error?.type === 'rate_limit_error' ? 'rate_limited' : 'upstream';
      throw new LlmError(code, data.error?.message || 'Provider error', { provider: provider.id });
    } else if (type === 'message_stop') {
      break;
    }
  }
  yield { type: 'done', usage: usage(input, output), finishReason };
}
