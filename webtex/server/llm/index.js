// LLM provider layer: one interface for every provider.
//   chat({ providerId, model, system, messages, maxTokens, temperature, signal })
//     → async iterable of { type: 'text', text } … then { type: 'done', usage, finishReason }
// Errors are LlmError (see errors.js). Configuration: see config.js.
import * as anthropic from './anthropic.js';
import * as bedrock from './bedrock.js';
import * as cohere from './cohere.js';
import * as gemini from './gemini.js';
import * as mistral from './mistral.js';
import * as ollama from './ollama.js';
import * as openai from './openai.js';
import { loadLlmConfig } from './config.js';
import { LlmError } from './errors.js';

export { LlmError } from './errors.js';

const ADAPTERS = {
  openai,
  azure: openai,
  'openai-compatible': openai,
  anthropic,
  gemini,
  mistral,
  cohere,
  bedrock,
  ollama,
};

let current = null;
const discovered = new Map(); // providerId -> { at, models }
const DISCOVERY_TTL = 60_000;

/** (Re)loads the configuration; tests pass their own environment. */
export function configureLlm(env = process.env, options) {
  current = loadLlmConfig(env, options);
  discovered.clear();
  return current;
}

export function llmConfig() {
  return current || configureLlm();
}

export const llmEnabled = () => llmConfig().enabled && llmConfig().providers.length > 0;

export function getProvider(id) {
  return llmConfig().providers.find((p) => p.id === id) || null;
}

/** Models of a provider: configured, or discovered from the server (cached briefly). */
export async function modelsFor(provider, { refresh = false } = {}) {
  if (!provider.discover) return provider.models;
  const hit = discovered.get(provider.id);
  if (hit && !refresh && Date.now() - hit.at < DISCOVERY_TTL) return hit.models;
  let models = [];
  try {
    models = await ADAPTERS[provider.type].listModels(provider);
  } catch {
    models = hit?.models || [];
  }
  discovered.set(provider.id, { at: Date.now(), models });
  return models;
}

/** Public description of the providers: ids, labels and models only. */
export async function publicProviders() {
  const cfg = llmConfig();
  const out = [];
  await Promise.all(
    cfg.providers.map(async (p, i) => {
      const models = await modelsFor(p);
      if (!models.length) return;
      const defaultModel = models.some((m) => m.id === p.defaultModel) ? p.defaultModel : models[0].id;
      out[i] = { id: p.id, label: p.label, models: models.map((m) => ({ id: m.id, label: m.label })), defaultModel };
    }),
  );
  const providers = out.filter(Boolean);
  const defaultProvider = providers.some((p) => p.id === cfg.defaultProvider) ? cfg.defaultProvider : providers[0]?.id || null;
  return { providers, defaultProvider };
}

/** Validates provider + model; returns { provider, model } or throws LlmError('bad_request'). */
export async function resolveModel(providerId, modelId) {
  const provider = getProvider(providerId);
  if (!provider) throw new LlmError('bad_request', 'Unknown provider');
  let models = await modelsFor(provider);
  if (!models.some((m) => m.id === modelId) && provider.discover) models = await modelsFor(provider, { refresh: true });
  const model = models.find((m) => m.id === modelId);
  if (!model) throw new LlmError('bad_request', 'Unknown model');
  return { provider, model };
}

export async function* chat({ providerId, model, system, messages, maxTokens, temperature, signal }) {
  const cfg = llmConfig();
  const { provider, model: m } = await resolveModel(providerId, model);
  const cap = Math.min(cfg.limits.maxTokens, m.maxTokens || Infinity, provider.maxTokens || Infinity);
  const adapter = ADAPTERS[provider.type];
  yield* adapter.chat(provider, {
    model: m.id,
    system,
    messages,
    maxTokens: Math.min(maxTokens || cap, cap),
    temperature: temperature ?? provider.temperature ?? cfg.limits.temperature,
    signal,
    timeoutMs: cfg.limits.timeoutSec * 1000,
    idleMs: cfg.limits.idleTimeoutSec * 1000,
  });
}
