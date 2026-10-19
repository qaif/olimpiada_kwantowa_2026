// Every adapter against a mock provider on localhost: the exact request it sends and how it
// parses the provider's stream (split into small pieces to cross line/event/UTF-8 borders).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import * as anthropic from '../llm/anthropic.js';
import * as bedrock from '../llm/bedrock.js';
import * as cohere from '../llm/cohere.js';
import * as gemini from '../llm/gemini.js';
import * as mistral from '../llm/mistral.js';
import * as ollama from '../llm/ollama.js';
import * as openai from '../llm/openai.js';
import { LlmError } from '../llm/errors.js';
import { encodeEventStreamMessage } from '../llm/eventstream.js';
import { signRequest } from '../llm/sigv4.js';
import { CONVERSATION, NORMALIZED, collect, mockServer, sseBody, writeInPieces } from './llm-helpers.js';

const servers = [];
after(async () => Promise.all(servers.map((s) => s.close())));
async function serve(handler) {
  const s = await mockServer(handler);
  servers.push(s);
  return s;
}
const OPTS = { model: 'm-1', system: 'SYSTEM', messages: CONVERSATION, maxTokens: 321 };
const rejectsWith = (promise, code, re) =>
  assert.rejects(promise, (err) => err instanceof LlmError && err.code === code && (!re || re.test(err.message)));

// ---------- OpenAI Chat Completions family ----------

const OPENAI_STREAM = sseBody([
  JSON.stringify({ id: 'c1', choices: [{ index: 0, delta: { role: 'assistant', content: '' } }] }),
  JSON.stringify({ choices: [{ index: 0, delta: { content: 'Zażółć ' } }] }),
  JSON.stringify({ choices: [{ index: 0, delta: { content: '$\\alpha$ ✓' } }] }),
  JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }),
  JSON.stringify({ choices: [], usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 } }),
  '[DONE]',
]);

test('openai: request shape and SSE parsing', async () => {
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, OPENAI_STREAM.replace(/\n/g, '\r\n'), 3);
  });
  const out = await collect(openai.chat({ id: 'openai', type: 'openai', baseUrl: `${s.url}/v1`, apiKey: 'sk-test' }, OPTS));
  assert.equal(out.text, 'Zażółć $\\alpha$ ✓');
  assert.deepEqual(out.done.usage, { inputTokens: 12, outputTokens: 3 });
  assert.equal(out.done.finishReason, 'stop');

  const r = s.requests[0];
  assert.equal(r.method, 'POST');
  assert.equal(r.path, '/v1/chat/completions');
  assert.equal(r.headers.authorization, 'Bearer sk-test');
  assert.equal(r.json.model, 'm-1');
  assert.equal(r.json.stream, true);
  assert.deepEqual(r.json.stream_options, { include_usage: true });
  assert.equal(r.json.max_completion_tokens, 321);
  assert.equal(r.json.max_tokens, undefined);
  assert.equal(r.json.temperature, undefined);
  assert.deepEqual(r.json.messages, [{ role: 'system', content: 'SYSTEM' }, ...NORMALIZED]);
});

test('openai-compatible (vLLM/LiteLLM): max_tokens, optional key, model listing', async () => {
  const s = await serve((req, res) => {
    if (req.url === '/v1/models') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ object: 'list', data: [{ id: 'Qwen/Qwen3-8B' }, { id: 'llama-3.1' }] }));
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, OPENAI_STREAM, 4);
  });
  const provider = { id: 'vllm', type: 'openai-compatible', baseUrl: `${s.url}/v1/`, apiKey: '' };
  assert.deepEqual(await openai.listModels(provider), [
    { id: 'Qwen/Qwen3-8B', label: 'Qwen/Qwen3-8B' },
    { id: 'llama-3.1', label: 'llama-3.1' },
  ]);
  const out = await collect(openai.chat(provider, { ...OPTS, temperature: 0.2 }));
  assert.equal(out.text, 'Zażółć $\\alpha$ ✓');
  const r = s.requests[1];
  assert.equal(r.path, '/v1/chat/completions');
  assert.equal(r.headers.authorization, undefined);
  assert.equal(r.json.max_tokens, 321);
  assert.equal(r.json.max_completion_tokens, undefined);
  assert.equal(r.json.temperature, 0.2);
});

test('azure: deployment URL + api-version + api-key header, and the v1 API', async () => {
  const azureStream = sseBody([
    JSON.stringify({ choices: [], prompt_filter_results: [{ prompt_index: 0, content_filter_results: {} }] }),
    JSON.stringify({ choices: [{ index: 0, delta: { content: 'ok' }, content_filter_results: {} }] }),
    '[DONE]',
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, azureStream, 9);
  });
  const classic = { id: 'azure', type: 'azure', baseUrl: `${s.url}/`, apiKey: 'az-key', apiVersion: '2024-10-21' };
  assert.equal((await collect(openai.chat(classic, { ...OPTS, model: 'my gpt' }))).text, 'ok');
  let r = s.requests[0];
  assert.equal(r.url, '/openai/deployments/my%20gpt/chat/completions?api-version=2024-10-21');
  assert.equal(r.headers['api-key'], 'az-key');
  assert.equal(r.headers.authorization, undefined);
  assert.equal(r.json.model, undefined);
  assert.equal(r.json.max_completion_tokens, 321);

  await collect(openai.chat({ ...classic, apiVersion: '' }, OPTS));
  r = s.requests[1];
  assert.equal(r.url, '/openai/v1/chat/completions');
  assert.equal(r.json.model, 'm-1');
});

// ---------- Anthropic ----------

test('anthropic: Messages API request and typed SSE events', async () => {
  const stream = sseBody([
    { event: 'message_start', data: { type: 'message_start', message: { id: 'msg_1', role: 'assistant', content: [], usage: { input_tokens: 25, output_tokens: 1 } } } },
    { event: 'content_block_start', data: { type: 'content_block_start', index: 0, content_block: { type: 'thinking', thinking: '' } } },
    { event: 'content_block_delta', data: { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 'hidden' } } },
    { event: 'content_block_stop', data: { type: 'content_block_stop', index: 0 } },
    { event: 'ping', data: { type: 'ping' } },
    { event: 'content_block_start', data: { type: 'content_block_start', index: 1, content_block: { type: 'text', text: '' } } },
    { event: 'content_block_delta', data: { type: 'content_block_delta', index: 1, delta: { type: 'text_delta', text: 'Użyj ' } } },
    { event: 'content_block_delta', data: { type: 'content_block_delta', index: 1, delta: { type: 'text_delta', text: '\\frac{a}{b}' } } },
    { event: 'content_block_stop', data: { type: 'content_block_stop', index: 1 } },
    { event: 'message_delta', data: { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 15 } } },
    { event: 'message_stop', data: { type: 'message_stop' } },
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, stream, 6);
  });
  const out = await collect(anthropic.chat({ id: 'anthropic', type: 'anthropic', baseUrl: s.url, apiKey: 'sk-ant' }, { ...OPTS, model: 'claude-opus-5-5' }));
  assert.equal(out.text, 'Użyj \\frac{a}{b}');
  assert.deepEqual(out.done.usage, { inputTokens: 25, outputTokens: 15 });
  assert.equal(out.done.finishReason, 'end_turn');

  const r = s.requests[0];
  assert.equal(r.path, '/v1/messages');
  assert.equal(r.headers['x-api-key'], 'sk-ant');
  assert.equal(r.headers['anthropic-version'], '2023-06-01');
  assert.equal(r.headers.authorization, undefined);
  assert.deepEqual(r.json, { model: 'claude-opus-5-5', max_tokens: 321, messages: NORMALIZED, stream: true, system: 'SYSTEM' });
});

test('anthropic: an error event mid-stream becomes an LlmError', async () => {
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(
      sseBody([
        { event: 'message_start', data: { type: 'message_start', message: { usage: { input_tokens: 1 } } } },
        { event: 'error', data: { type: 'error', error: { type: 'overloaded_error', message: 'Overloaded' } } },
      ]),
    );
  });
  await rejectsWith(collect(anthropic.chat({ id: 'a', type: 'anthropic', baseUrl: s.url, apiKey: 'k' }, OPTS)), 'upstream', /Overloaded/);
});

// ---------- Gemini ----------

test('gemini: streamGenerateContent request and SSE chunks (thoughts skipped)', async () => {
  const stream = sseBody([
    JSON.stringify({ candidates: [{ content: { role: 'model', parts: [{ text: 'secret plan', thought: true }] } }] }),
    JSON.stringify({ candidates: [{ content: { role: 'model', parts: [{ text: 'Wzór: ' }] } }] }),
    JSON.stringify({
      candidates: [{ content: { role: 'model', parts: [{ text: '$E=mc^2$' }] }, finishReason: 'STOP' }],
      usageMetadata: { promptTokenCount: 40, candidatesTokenCount: 7, totalTokenCount: 47 },
    }),
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, stream, 8);
  });
  const out = await collect(gemini.chat({ id: 'gemini', type: 'gemini', baseUrl: s.url, apiKey: 'g-key' }, { ...OPTS, model: 'models/gemini-2.5-flash' }));
  assert.equal(out.text, 'Wzór: $E=mc^2$');
  assert.deepEqual(out.done.usage, { inputTokens: 40, outputTokens: 7 });
  assert.equal(out.done.finishReason, 'STOP');

  const r = s.requests[0];
  assert.equal(r.url, '/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse');
  assert.equal(r.headers['x-goog-api-key'], 'g-key');
  assert.deepEqual(r.json, {
    contents: [
      { role: 'user', parts: [{ text: 'Pierwsze\n\npytanie' }] },
      { role: 'model', parts: [{ text: 'Odpowiedź' }] },
      { role: 'user', parts: [{ text: 'Drugie pytanie' }] },
    ],
    generationConfig: { maxOutputTokens: 321 },
    systemInstruction: { parts: [{ text: 'SYSTEM' }] },
  });
});

test('gemini: HTTP error body is mapped with the provider message', async () => {
  const s = await serve((req, res) => {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify([{ error: { code: 400, message: 'API key not valid. Please pass a valid API key.', status: 'INVALID_ARGUMENT' } }]));
  });
  await rejectsWith(collect(gemini.chat({ id: 'g', type: 'gemini', baseUrl: s.url, apiKey: 'bad' }, OPTS)), 'bad_request', /API key not valid/);
});

// ---------- Mistral ----------

test('mistral: request without OpenAI-only fields, typed content chunks', async () => {
  const stream = sseBody([
    JSON.stringify({ id: 'x', object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content: '' } }] }),
    JSON.stringify({ choices: [{ index: 0, delta: { content: [{ type: 'thinking', thinking: [{ type: 'text', text: 'hmm' }] }] } }] }),
    JSON.stringify({ choices: [{ index: 0, delta: { content: [{ type: 'text', text: '\\begin{table}' }] } }] }),
    JSON.stringify({ choices: [{ index: 0, delta: { content: '…' }, finish_reason: 'stop' }], usage: { prompt_tokens: 9, completion_tokens: 4 } }),
    '[DONE]',
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, stream, 7);
  });
  const out = await collect(mistral.chat({ id: 'mistral', type: 'mistral', baseUrl: s.url, apiKey: 'm-key' }, OPTS));
  assert.equal(out.text, '\\begin{table}…');
  assert.deepEqual(out.done.usage, { inputTokens: 9, outputTokens: 4 });
  const r = s.requests[0];
  assert.equal(r.path, '/v1/chat/completions');
  assert.equal(r.headers.authorization, 'Bearer m-key');
  assert.deepEqual(r.json, { model: 'm-1', messages: [{ role: 'system', content: 'SYSTEM' }, ...NORMALIZED], stream: true, max_tokens: 321 });
});

// ---------- Cohere ----------

test('cohere: v2 chat request and typed stream events', async () => {
  const stream = sseBody([
    { event: 'message-start', data: { id: 'c', type: 'message-start', delta: { message: { role: 'assistant', content: [] } } } },
    { event: 'content-start', data: { type: 'content-start', index: 0, delta: { message: { content: { type: 'text', text: '' } } } } },
    { event: 'content-delta', data: { type: 'content-delta', index: 0, delta: { message: { content: { text: 'Cześć' } } } } },
    { event: 'content-delta', data: { type: 'content-delta', index: 0, delta: { message: { content: { text: '!' } } } } },
    { event: 'content-end', data: { type: 'content-end', index: 0 } },
    {
      event: 'message-end',
      data: { type: 'message-end', delta: { finish_reason: 'COMPLETE', usage: { billed_units: { input_tokens: 5, output_tokens: 2 }, tokens: { input_tokens: 70, output_tokens: 2 } } } },
    },
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    return writeInPieces(res, stream, 11);
  });
  const out = await collect(cohere.chat({ id: 'cohere', type: 'cohere', baseUrl: s.url, apiKey: 'co-key' }, OPTS));
  assert.equal(out.text, 'Cześć!');
  assert.deepEqual(out.done.usage, { inputTokens: 70, outputTokens: 2 });
  assert.equal(out.done.finishReason, 'COMPLETE');
  const r = s.requests[0];
  assert.equal(r.path, '/v2/chat');
  assert.equal(r.headers.authorization, 'Bearer co-key');
  assert.deepEqual(r.json, { model: 'm-1', messages: [{ role: 'system', content: 'SYSTEM' }, ...NORMALIZED], stream: true, max_tokens: 321 });
});

// ---------- Ollama ----------

test('ollama: /api/chat NDJSON stream, thinking ignored, token counts', async () => {
  const lines = [
    { model: 'qwen3', message: { role: 'assistant', content: '', thinking: 'let me think' }, done: false },
    { model: 'qwen3', message: { role: 'assistant', content: 'Wynik: ' }, done: false },
    { model: 'qwen3', message: { role: 'assistant', content: '\\(x^2\\)' }, done: false },
    { model: 'qwen3', message: { role: 'assistant', content: '' }, done: true, done_reason: 'stop', prompt_eval_count: 26, eval_count: 9 },
  ];
  const s = await serve((req, res) => {
    if (req.url === '/api/tags') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ models: [{ name: 'qwen3:8b', model: 'qwen3:8b' }] }));
    }
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
    return writeInPieces(res, lines.map((l) => JSON.stringify(l)).join('\n') + '\n', 10);
  });
  const provider = { id: 'ollama', type: 'ollama', baseUrl: s.url };
  assert.deepEqual(await ollama.listModels(provider), [{ id: 'qwen3:8b', label: 'qwen3:8b' }]);
  const out = await collect(ollama.chat(provider, { ...OPTS, model: 'qwen3:8b' }));
  assert.equal(out.text, 'Wynik: \\(x^2\\)');
  assert.deepEqual(out.done.usage, { inputTokens: 26, outputTokens: 9 });
  const r = s.requests[1];
  assert.equal(r.path, '/api/chat');
  assert.deepEqual(r.json, { model: 'qwen3:8b', messages: [{ role: 'system', content: 'SYSTEM' }, ...NORMALIZED], stream: true, options: { num_predict: 321 } });
});

test('ollama: unknown model (404 with {"error"}) is a bad_request with the message', async () => {
  const s = await serve((req, res) => {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: "model 'nope' not found" }));
  });
  await rejectsWith(collect(ollama.chat({ id: 'o', type: 'ollama', baseUrl: s.url }, OPTS)), 'bad_request', /model 'nope' not found/);
});

// ---------- Bedrock ----------

const frame = (eventType, payload) => encodeEventStreamMessage({ ':event-type': eventType, ':content-type': 'application/json', ':message-type': 'event' }, payload);

test('bedrock: ConverseStream request signed with SigV4, event stream parsed', async () => {
  const body = Buffer.concat([
    frame('messageStart', { role: 'assistant' }),
    frame('contentBlockDelta', { contentBlockIndex: 0, delta: { reasoningContent: { text: 'thinking' } } }),
    frame('contentBlockDelta', { contentBlockIndex: 1, delta: { text: 'Tabela: ' } }),
    frame('contentBlockDelta', { contentBlockIndex: 1, delta: { text: '\\begin{tabular}{cc}' } }),
    frame('contentBlockStop', { contentBlockIndex: 1 }),
    frame('messageStop', { stopReason: 'end_turn' }),
    frame('metadata', { usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 }, metrics: { latencyMs: 100 } }),
  ]);
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/vnd.amazon.eventstream' });
    return writeInPieces(res, body, 13);
  });
  const provider = {
    id: 'bedrock',
    type: 'bedrock',
    region: 'eu-central-1',
    baseUrl: s.url,
    credentials: { accessKeyId: 'AKIDTEST', secretAccessKey: 'secret/key', sessionToken: 'session-token' },
  };
  const out = await collect(bedrock.chat(provider, { ...OPTS, model: 'eu.anthropic.claude-opus-5-5-v1:0' }));
  assert.equal(out.text, 'Tabela: \\begin{tabular}{cc}');
  assert.deepEqual(out.done.usage, { inputTokens: 30, outputTokens: 8 });
  assert.equal(out.done.finishReason, 'end_turn');

  const r = s.requests[0];
  assert.equal(r.path, '/model/eu.anthropic.claude-opus-5-5-v1%3A0/converse-stream');
  assert.deepEqual(r.json, {
    messages: NORMALIZED.map((m) => ({ role: m.role, content: [{ text: m.content }] })),
    inferenceConfig: { maxTokens: 321 },
    system: [{ text: 'SYSTEM' }],
  });
  assert.equal(r.headers['x-amz-security-token'], 'session-token');
  const date = r.headers['x-amz-date'];
  assert.match(date, /^\d{8}T\d{6}Z$/);
  assert.match(
    r.headers.authorization,
    new RegExp(`^AWS4-HMAC-SHA256 Credential=AKIDTEST/${date.slice(0, 8)}/eu-central-1/bedrock/aws4_request, SignedHeaders=content-type;host;x-amz-date;x-amz-security-token, Signature=[0-9a-f]{64}$`),
  );
  // Recompute the signature from what the server received.
  const when = new Date(`${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}T${date.slice(9, 11)}:${date.slice(11, 13)}:${date.slice(13, 15)}Z`);
  const expected = signRequest({
    method: 'POST',
    url: `${s.url}${r.url}`,
    headers: { 'content-type': r.headers['content-type'] },
    body: r.body,
    region: 'eu-central-1',
    service: 'bedrock',
    accessKeyId: 'AKIDTEST',
    secretAccessKey: 'secret/key',
    sessionToken: 'session-token',
    date: when,
  });
  assert.equal(r.headers.authorization, expected.authorization);
  // The canonical URI double-encodes the path segment (non-S3 rule).
  assert.match(expected.canonicalRequest, /^POST\n\/model\/eu\.anthropic\.claude-opus-5-5-v1%253A0\/converse-stream\n/);
});

test('bedrock: Bedrock API key (Bearer) instead of SigV4; exception frames are mapped', async () => {
  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/vnd.amazon.eventstream' });
    res.end(
      Buffer.concat([
        frame('messageStart', { role: 'assistant' }),
        encodeEventStreamMessage({ ':exception-type': 'throttlingException', ':content-type': 'application/json', ':message-type': 'exception' }, { message: 'Too many tokens, please wait.' }),
      ]),
    );
  });
  const provider = { id: 'bedrock', type: 'bedrock', region: 'us-east-1', baseUrl: s.url, apiKey: 'bedrock-api-key' };
  await rejectsWith(collect(bedrock.chat(provider, OPTS)), 'rate_limited', /Too many tokens/);
  assert.equal(s.requests[0].headers.authorization, 'Bearer bedrock-api-key');
  assert.equal(s.requests[0].headers['x-amz-date'], undefined);
});

test('bedrock: missing credentials is a config error, not a request', async () => {
  await rejectsWith(collect(bedrock.chat({ id: 'b', type: 'bedrock', region: 'us-east-1', credentials: {} }, OPTS)), 'config');
  await rejectsWith(collect(bedrock.chat({ id: 'b', type: 'bedrock', region: '', apiKey: 'k' }, OPTS)), 'config');
});

// ---------- transport: errors, timeouts, abort ----------

test('HTTP errors map to stable codes with the provider message', async () => {
  const cases = [
    [401, { error: { message: 'Incorrect API key provided: sk-***', type: 'invalid_request_error' } }, 'auth', /Incorrect API key/],
    [429, { error: { message: 'Rate limit reached' } }, 'rate_limited', /Rate limit/],
    [400, { error: { message: 'max_tokens is too large' } }, 'bad_request', /max_tokens/],
    [503, 'upstream connect error', 'upstream', /upstream connect error/],
  ];
  for (const [status, body, code, re] of cases) {
    const s = await serve((req, res) => {
      res.writeHead(status, { 'Content-Type': typeof body === 'string' ? 'text/plain' : 'application/json' });
      res.end(typeof body === 'string' ? body : JSON.stringify(body));
    });
    await rejectsWith(collect(openai.chat({ id: 'x', type: 'openai', baseUrl: s.url, apiKey: 'k' }, OPTS)), code, re);
  }
});

test('unreachable provider → network; stalled stream → timeout', async () => {
  const dead = await serve(() => {});
  await dead.close();
  servers.splice(servers.indexOf(dead), 1);
  await rejectsWith(collect(openai.chat({ id: 'x', type: 'openai-compatible', baseUrl: dead.url }, OPTS)), 'network');

  const s = await serve((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'a' } }] })}\n\n`);
    // …and then nothing.
  });
  await rejectsWith(collect(openai.chat({ id: 'x', type: 'openai-compatible', baseUrl: s.url }, { ...OPTS, idleMs: 300 })), 'timeout');
});

test('aborting the signal stops the upstream request', async () => {
  let release;
  const s = await serve(async (req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: 'first' } }] })}\n\n`);
    await new Promise((r) => (release = r));
  });
  const controller = new AbortController();
  const seen = [];
  await rejectsWith(
    (async () => {
      for await (const ev of openai.chat({ id: 'x', type: 'openai-compatible', baseUrl: s.url }, { ...OPTS, signal: controller.signal })) {
        seen.push(ev);
        controller.abort();
      }
    })(),
    'aborted',
  );
  assert.deepEqual(seen, [{ type: 'text', text: 'first' }]);
  for (let i = 0; i < 50 && !s.requests[0].closed; i++) await new Promise((r) => setTimeout(r, 20));
  assert.equal(s.requests[0].closed, true, 'the provider connection was closed');
  release();
});
