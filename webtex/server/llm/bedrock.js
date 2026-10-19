// Amazon Bedrock Converse API streaming: POST https://bedrock-runtime.{region}.amazonaws.com
// /model/{modelId}/converse-stream, signed with SigV4 (service "bedrock") or a Bedrock API key
// (Bearer). The response is an AWS event stream: messageStart, contentBlockDelta, contentBlockStop,
// messageStop, metadata — or an exception message.
import { LlmError } from './errors.js';
import { decodeEventStream } from './eventstream.js';
import { joinUrl, openStream, parseJson } from './http.js';
import { signRequest } from './sigv4.js';
import { normalizeTurns, usage } from './turns.js';

export function buildRequest(provider, { model, system, messages, maxTokens, temperature }, { date } = {}) {
  const body = {
    messages: normalizeTurns(messages).map((m) => ({ role: m.role, content: [{ text: m.content }] })),
    inferenceConfig: {},
  };
  if (system) body.system = [{ text: system }];
  if (maxTokens) body.inferenceConfig.maxTokens = maxTokens;
  if (temperature != null) body.inferenceConfig.temperature = temperature;
  Object.assign(body, provider.extraBody || {});
  const json = JSON.stringify(body);

  const region = provider.region;
  if (!region) throw new LlmError('config', 'Bedrock needs a region (BEDROCK_REGION or AWS_REGION)', { provider: provider.id });
  const base = provider.baseUrl || `https://bedrock-runtime.${region}.amazonaws.com`;
  const url = joinUrl(base, `model/${encodeURIComponent(model)}/converse-stream`);
  const baseHeaders = { 'content-type': 'application/json', ...(provider.headers || {}) };

  let headers;
  if (provider.apiKey) {
    headers = { ...baseHeaders, authorization: `Bearer ${provider.apiKey}` };
  } else {
    const c = provider.credentials || {};
    if (!c.accessKeyId || !c.secretAccessKey) {
      throw new LlmError('config', 'Bedrock needs AWS credentials (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY) or AWS_BEARER_TOKEN_BEDROCK', { provider: provider.id });
    }
    headers = signRequest({
      method: 'POST',
      url,
      headers: baseHeaders,
      body: json,
      region,
      service: 'bedrock',
      accessKeyId: c.accessKeyId,
      secretAccessKey: c.secretAccessKey,
      sessionToken: c.sessionToken,
      date: date || new Date(),
    }).headers;
  }
  return { url, headers, body, json };
}

const EXCEPTION_CODES = {
  throttlingException: 'rate_limited',
  serviceQuotaExceededException: 'rate_limited',
  validationException: 'bad_request',
  resourceNotFoundException: 'bad_request',
  accessDeniedException: 'auth',
  unrecognizedClientException: 'auth',
};

export async function* chat(provider, opts) {
  const { url, headers, json } = buildRequest(provider, opts);
  const { chunks } = await openStream(url, { headers, body: json, signal: opts.signal, timeoutMs: opts.timeoutMs, idleMs: opts.idleMs, provider: provider.id });
  let u = usage(null, null);
  let finishReason = null;
  for await (const { headers: h, payload } of decodeEventStream(chunks, provider.id)) {
    const text = payload.toString('utf8');
    if (h[':message-type'] === 'exception' || h[':message-type'] === 'error') {
      const kind = h[':exception-type'] || h[':error-code'] || 'error';
      let message = h[':error-message'] || '';
      try {
        message = JSON.parse(text).message || message;
      } catch {}
      throw new LlmError(EXCEPTION_CODES[kind] || 'upstream', message || kind, { provider: provider.id });
    }
    const event = h[':event-type'];
    const data = text ? parseJson(text, provider.id) : {};
    if (event === 'contentBlockDelta') {
      // reasoningContent deltas are not shown.
      if (data.delta?.text) yield { type: 'text', text: data.delta.text };
    } else if (event === 'messageStop') {
      finishReason = data.stopReason || null;
    } else if (event === 'metadata') {
      u = usage(data.usage?.inputTokens, data.usage?.outputTokens);
    }
  }
  yield { type: 'done', usage: u, finishReason };
}
