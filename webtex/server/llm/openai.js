// OpenAI Chat Completions streaming (POST {base}/chat/completions, SSE, `data: [DONE]`).
// Used by three provider types:
//   openai             api.openai.com, Bearer key, `max_completion_tokens`, usage via stream_options
//   azure              Azure OpenAI: deployment URL + api-version + `api-key` header, or the v1 API
//   openai-compatible  vLLM, LiteLLM proxy, LM Studio, OpenRouter, Groq, DeepSeek, xAI, Together…
import { LlmError } from './errors.js';
import { joinUrl, openStream, parseJson, sse } from './http.js';
import { contentText, normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }) {
  const type = provider.type;
  const body = { model, messages: [], stream: true };
  if (system) body.messages.push({ role: 'system', content: system });
  for (const m of normalizeTurns(messages)) body.messages.push(m);

  // OpenAI's reasoning models reject max_tokens; most compatible servers only know max_tokens.
  const tokensParam = provider.maxTokensParam || (type === 'openai' || type === 'azure' ? 'max_completion_tokens' : 'max_tokens');
  if (maxTokens) body[tokensParam] = maxTokens;
  if (temperature != null) body.temperature = temperature;
  if (provider.streamUsage !== false) body.stream_options = { include_usage: true };
  Object.assign(body, provider.extraBody || {});

  const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(provider.headers || {}) };
  let url;
  if (type === 'azure') {
    if (provider.apiKey) headers['api-key'] = provider.apiKey;
    if (provider.apiVersion) {
      // Classic data plane: the deployment name is the model.
      url = `${joinUrl(provider.baseUrl, `openai/deployments/${encodeURIComponent(model)}/chat/completions`)}?api-version=${encodeURIComponent(provider.apiVersion)}`;
      delete body.model;
    } else {
      url = joinUrl(provider.baseUrl, 'openai/v1/chat/completions');
    }
  } else {
    if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
    url = joinUrl(provider.baseUrl, 'chat/completions');
  }
  return { url, headers, body };
}

export async function* chat(provider, opts) {
  const { url, headers, body } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: JSON.stringify(body), signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let finishReason = null;
  let u = usage(null, null);
  for await (const ev of sse(chunks)) {
    if (ev.data === '[DONE]') break;
    const data = parseJson(ev.data, provider.id);
    if (data.error) throw new LlmError('upstream', String(data.error.message || data.error), { provider: provider.id });
    for (const choice of data.choices || []) {
      const text = contentText(choice.delta?.content);
      if (text) yield { type: 'text', text };
      if (choice.finish_reason) finishReason = choice.finish_reason;
    }
    if (data.usage) u = usage(data.usage.prompt_tokens, data.usage.completion_tokens);
  }
  yield { type: 'done', usage: u, finishReason };
}

/** GET {base}/models (vLLM, LiteLLM, LM Studio…): used when no models are configured. */
export async function listModels(provider, { signal } = {}) {
  const headers = { ...(provider.headers || {}) };
  if (provider.apiKey) headers.Authorization = `Bearer ${provider.apiKey}`;
  const res = await fetch(joinUrl(provider.baseUrl, 'models'), {
    headers,
    signal: AbortSignal.any([AbortSignal.timeout(3000), signal].filter(Boolean)),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return (data.data || []).map((m) => ({ id: m.id, label: m.id })).filter((m) => m.id);
}
