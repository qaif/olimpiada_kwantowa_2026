// LLM assistant configuration: server-side only, never sent to the browser (the browser gets
// provider ids, labels and model lists). Sources, merged in this order:
//   1. env shortcuts (OPENAI_API_KEY, ANTHROPIC_API_KEY, OLLAMA_BASE_URL, …) register providers
//   2. the optional JSON file LLM_CONFIG overrides/extends them by provider id
// Pure: takes the environment and a file reader, so tests can load any configuration.
import fs from 'node:fs';

export const PROVIDER_TYPES = ['openai', 'anthropic', 'gemini', 'mistral', 'cohere', 'azure', 'bedrock', 'ollama', 'openai-compatible'];

// Presets for OpenAI-compatible servers: type alias → defaults.
export const COMPATIBLE_PRESETS = {
  vllm: { label: 'vLLM', baseUrl: 'http://localhost:8000/v1' },
  litellm: { label: 'LiteLLM', baseUrl: 'http://localhost:4000/v1' },
  lmstudio: { label: 'LM Studio', baseUrl: 'http://localhost:1234/v1' },
  openrouter: { label: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1' },
  groq: { label: 'Groq', baseUrl: 'https://api.groq.com/openai/v1' },
  deepseek: { label: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', models: 'deepseek-chat=DeepSeek Chat,deepseek-reasoner=DeepSeek Reasoner' },
  xai: { label: 'xAI', baseUrl: 'https://api.x.ai/v1' },
  together: { label: 'Together AI', baseUrl: 'https://api.together.xyz/v1' },
};

const DEFAULT_BASE = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  gemini: 'https://generativelanguage.googleapis.com',
  mistral: 'https://api.mistral.ai',
  cohere: 'https://api.cohere.com',
  ollama: 'http://localhost:11434',
};

const DEFAULT_LABEL = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  gemini: 'Google Gemini',
  mistral: 'Mistral AI',
  cohere: 'Cohere',
  azure: 'Azure OpenAI',
  bedrock: 'Amazon Bedrock',
  ollama: 'Ollama',
  'openai-compatible': 'OpenAI-compatible',
};

// Default model lists for the env shortcuts; override with <PREFIX>_MODELS="id=Label,id2".
const DEFAULT_MODELS = {
  openai: 'gpt-5=GPT-5,gpt-5-mini=GPT-5 mini',
  anthropic: 'claude-opus-5-5=Claude Opus 5.5,claude-sonnet-5-5=Claude Sonnet 5.5,claude-haiku-5-5=Claude Haiku 5.5',
  gemini: 'gemini-2.5-pro=Gemini 2.5 Pro,gemini-2.5-flash=Gemini 2.5 Flash',
  mistral: 'mistral-large-latest=Mistral Large,mistral-small-latest=Mistral Small,codestral-latest=Codestral',
  cohere: 'command-a-03-2025=Command A',
  bedrock: 'anthropic.claude-opus-5-5=Claude Opus 5.5 (Bedrock),anthropic.claude-sonnet-5-5=Claude Sonnet 5.5 (Bedrock)',
};

const num = (v, def, { min = 0, max = Infinity } = {}) => {
  const n = Number(v);
  return v === undefined || v === '' || !Number.isFinite(n) ? def : Math.min(max, Math.max(min, n));
};
const bool = (v, def = false) => (v === undefined || v === '' ? def : /^(1|true|yes|on)$/i.test(String(v).trim()));

/** "id=Label,id2" or ["id", {id, label}] → [{ id, label }] */
export function parseModels(value) {
  if (!value) return [];
  const items = Array.isArray(value) ? value : String(value).split(',');
  const out = [];
  for (const item of items) {
    let id;
    let label;
    let extra = {};
    if (typeof item === 'string') {
      const [rawId, ...rest] = item.split('=');
      id = rawId.trim();
      label = rest.join('=').trim();
    } else if (item && typeof item === 'object') {
      id = String(item.id || '').trim();
      label = String(item.label || '').trim();
      if (Number.isFinite(item.maxTokens)) extra.maxTokens = item.maxTokens;
      if (item.default === true) extra.default = true;
    }
    if (id && !out.some((m) => m.id === id)) out.push({ id, label: label || id, ...extra });
  }
  return out;
}

function envProviders(env) {
  const list = [];
  const add = (p) => list.push(p);
  const models = (prefix, type) => parseModels(env[`${prefix}_MODELS`] || DEFAULT_MODELS[type]);

  if (env.OPENAI_API_KEY) {
    add({ id: 'openai', type: 'openai', apiKey: env.OPENAI_API_KEY, baseUrl: env.OPENAI_BASE_URL, models: models('OPENAI', 'openai') });
  }
  if (env.ANTHROPIC_API_KEY) {
    add({ id: 'anthropic', type: 'anthropic', apiKey: env.ANTHROPIC_API_KEY, baseUrl: env.ANTHROPIC_BASE_URL, models: models('ANTHROPIC', 'anthropic') });
  }
  if (env.GEMINI_API_KEY || env.GOOGLE_API_KEY) {
    add({ id: 'gemini', type: 'gemini', apiKey: env.GEMINI_API_KEY || env.GOOGLE_API_KEY, baseUrl: env.GEMINI_BASE_URL, models: models('GEMINI', 'gemini') });
  }
  if (env.MISTRAL_API_KEY) {
    add({ id: 'mistral', type: 'mistral', apiKey: env.MISTRAL_API_KEY, baseUrl: env.MISTRAL_BASE_URL, models: models('MISTRAL', 'mistral') });
  }
  if (env.COHERE_API_KEY || env.CO_API_KEY) {
    add({ id: 'cohere', type: 'cohere', apiKey: env.COHERE_API_KEY || env.CO_API_KEY, baseUrl: env.COHERE_BASE_URL, models: models('COHERE', 'cohere') });
  }
  if (env.AZURE_OPENAI_ENDPOINT && env.AZURE_OPENAI_API_KEY) {
    add({
      id: 'azure',
      type: 'azure',
      baseUrl: env.AZURE_OPENAI_ENDPOINT,
      apiKey: env.AZURE_OPENAI_API_KEY,
      apiVersion: env.AZURE_OPENAI_API_VERSION === 'v1' ? '' : env.AZURE_OPENAI_API_VERSION || '2024-10-21',
      models: parseModels(env.AZURE_OPENAI_DEPLOYMENTS || env.AZURE_OPENAI_MODELS),
    });
  }
  const bedrockRegion = env.BEDROCK_REGION || env.AWS_REGION || env.AWS_DEFAULT_REGION;
  const bedrockCreds = env.AWS_ACCESS_KEY_ID && env.AWS_SECRET_ACCESS_KEY;
  if ((env.BEDROCK_REGION || env.BEDROCK_MODELS || env.AWS_BEARER_TOKEN_BEDROCK) && bedrockRegion && (bedrockCreds || env.AWS_BEARER_TOKEN_BEDROCK)) {
    add({
      id: 'bedrock',
      type: 'bedrock',
      region: bedrockRegion,
      apiKey: env.AWS_BEARER_TOKEN_BEDROCK || '',
      baseUrl: env.BEDROCK_BASE_URL,
      models: models('BEDROCK', 'bedrock'),
    });
  }
  if (env.OLLAMA_BASE_URL || env.OLLAMA_HOST) {
    let base = env.OLLAMA_BASE_URL || env.OLLAMA_HOST;
    if (!/^https?:\/\//.test(base)) base = `http://${base}`;
    add({ id: 'ollama', type: 'ollama', baseUrl: base, apiKey: env.OLLAMA_API_KEY, models: parseModels(env.OLLAMA_MODELS) });
  }
  for (const [alias, preset] of Object.entries(COMPATIBLE_PRESETS)) {
    const P = alias.toUpperCase();
    const base = env[`${P}_BASE_URL`];
    const key = env[`${P}_API_KEY`];
    // Local servers register by URL; hosted ones by key (their URL is known).
    const hosted = /^https:/.test(preset.baseUrl);
    if (!(base || (hosted && key))) continue;
    add({
      id: alias,
      type: 'openai-compatible',
      label: preset.label,
      baseUrl: base || preset.baseUrl,
      apiKey: key,
      models: parseModels(env[`${P}_MODELS`] || preset.models),
    });
  }
  return list;
}

function readJsonFile(file, readFile) {
  let text;
  try {
    text = readFile(file);
  } catch (err) {
    throw new Error(`LLM_CONFIG: cannot read ${file} (${err.code || err.message})`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`LLM_CONFIG: ${file} is not valid JSON (${err.message})`);
  }
}

/** Fills defaults and resolves secrets of one provider entry. Returns { provider } or { error }. */
function resolveProvider(raw, env) {
  const id = String(raw.id || '').trim();
  if (!/^[\w.-]{1,40}$/.test(id)) return { error: `provider id "${raw.id}" is invalid (letters, digits, _ . - only)` };
  let type = String(raw.type || '').trim();
  const preset = COMPATIBLE_PRESETS[type];
  if (preset) type = 'openai-compatible';
  if (!PROVIDER_TYPES.includes(type)) return { error: `provider "${id}": unknown type "${raw.type}"` };

  const apiKey = raw.apiKey || (raw.apiKeyEnv ? env[raw.apiKeyEnv] : '') || '';
  if (raw.apiKeyEnv && !apiKey) return { error: `provider "${id}": environment variable ${raw.apiKeyEnv} is empty` };
  const baseUrl = raw.baseUrl || preset?.baseUrl || DEFAULT_BASE[type] || '';
  if (!baseUrl && type !== 'bedrock') return { error: `provider "${id}": baseUrl is required for type ${type}` };
  if (['openai', 'anthropic', 'gemini', 'mistral', 'cohere', 'azure'].includes(type) && !apiKey) {
    return { error: `provider "${id}": an API key is required (apiKey or apiKeyEnv)` };
  }

  const p = {
    id,
    type,
    label: String(raw.label || preset?.label || DEFAULT_LABEL[type]).slice(0, 60),
    baseUrl,
    apiKey,
    models: parseModels(raw.models || preset?.models),
    defaultModel: typeof raw.default === 'string' ? raw.default : typeof raw.defaultModel === 'string' ? raw.defaultModel : null,
    isDefault: raw.default === true,
    headers: raw.headers && typeof raw.headers === 'object' ? { ...raw.headers } : undefined,
    extraBody: raw.extraBody && typeof raw.extraBody === 'object' ? { ...raw.extraBody } : undefined,
    maxTokens: Number.isFinite(raw.maxTokens) ? raw.maxTokens : null,
    maxTokensParam: raw.maxTokensParam || undefined,
    streamUsage: raw.streamUsage,
    temperature: Number.isFinite(raw.temperature) ? raw.temperature : null,
    apiVersion: raw.apiVersion ?? undefined,
    keepAlive: raw.keepAlive,
  };
  if (type === 'azure' && p.apiVersion === undefined) p.apiVersion = '2024-10-21';
  if (type === 'bedrock') {
    p.region = raw.region || env.BEDROCK_REGION || env.AWS_REGION || env.AWS_DEFAULT_REGION || '';
    if (!p.region) return { error: `provider "${id}": Bedrock needs a region` };
    p.baseUrl = raw.baseUrl || '';
    p.apiKey = apiKey || (raw.useBearerToken === false ? '' : env.AWS_BEARER_TOKEN_BEDROCK || '');
    p.credentials = {
      accessKeyId: raw.accessKeyId || env[raw.accessKeyIdEnv || 'AWS_ACCESS_KEY_ID'] || '',
      secretAccessKey: raw.secretAccessKey || env[raw.secretAccessKeyEnv || 'AWS_SECRET_ACCESS_KEY'] || '',
      sessionToken: raw.sessionToken || env[raw.sessionTokenEnv || 'AWS_SESSION_TOKEN'] || '',
    };
    if (!p.apiKey && !(p.credentials.accessKeyId && p.credentials.secretAccessKey)) {
      return { error: `provider "${id}": Bedrock needs AWS credentials or a Bedrock API key` };
    }
  }
  // Without a model list only servers that can list their models are usable.
  p.discover = !p.models.length && (type === 'ollama' || type === 'openai-compatible');
  if (!p.models.length && !p.discover) return { error: `provider "${id}": no models configured` };
  const marked = p.models.find((m) => m.default);
  if (!p.defaultModel && marked) p.defaultModel = marked.id;
  return { provider: p };
}

/**
 * Loads the assistant configuration.
 * Returns { enabled, providers, defaultProvider, limits, errors } — `errors` are human readable
 * problems (invalid entries are skipped, never fatal).
 */
export function loadLlmConfig(env = process.env, { readFile = (f) => fs.readFileSync(f, 'utf8') } = {}) {
  const errors = [];
  let file = {};
  if (env.LLM_CONFIG) {
    try {
      file = readJsonFile(env.LLM_CONFIG, readFile);
      if (!file || typeof file !== 'object' || Array.isArray(file)) throw new Error('LLM_CONFIG: the top level must be an object');
    } catch (err) {
      errors.push(err.message);
      file = {};
    }
  }

  const entries = new Map();
  if (bool(env.LLM_ENV_PROVIDERS, file.envProviders ?? true)) for (const p of envProviders(env)) entries.set(p.id, p);
  for (const raw of Array.isArray(file.providers) ? file.providers : []) {
    if (!raw || typeof raw !== 'object') continue;
    if (raw.enabled === false) {
      entries.delete(String(raw.id));
      continue;
    }
    entries.set(String(raw.id), raw);
  }

  const providers = [];
  for (const raw of entries.values()) {
    const { provider, error } = resolveProvider(raw, env);
    if (error) errors.push(error);
    else providers.push(provider);
  }

  const wanted = env.LLM_DEFAULT_PROVIDER || file.defaultProvider;
  const defaultProvider = (providers.find((p) => p.id === wanted) || providers.find((p) => p.isDefault) || providers[0])?.id || null;
  const limits = file.limits && typeof file.limits === 'object' ? file.limits : {};

  return {
    enabled: bool(env.LLM_ENABLED, file.enabled === true),
    providers,
    defaultProvider,
    errors,
    limits: {
      maxTokens: num(env.LLM_MAX_TOKENS, limits.maxTokens ?? 4096, { min: 16, max: 128000 }),
      rateLimit: num(env.LLM_RATE_LIMIT, limits.rateLimit ?? 30, { min: 0 }),
      rateWindowSec: num(env.LLM_RATE_WINDOW, limits.rateWindowSec ?? 600, { min: 1 }),
      maxConcurrent: num(env.LLM_MAX_CONCURRENT, limits.maxConcurrent ?? 2, { min: 1 }),
      timeoutSec: num(env.LLM_TIMEOUT, limits.timeoutSec ?? 180, { min: 5 }),
      idleTimeoutSec: num(env.LLM_IDLE_TIMEOUT, limits.idleTimeoutSec ?? 90, { min: 5 }),
      contextChars: num(env.LLM_CONTEXT_CHARS, limits.contextChars ?? 24000, { min: 1000 }),
      maxInputChars: num(env.LLM_MAX_INPUT_CHARS, limits.maxInputChars ?? 40000, { min: 1000 }),
      temperature: env.LLM_TEMPERATURE !== undefined && env.LLM_TEMPERATURE !== '' ? num(env.LLM_TEMPERATURE, null, { min: 0, max: 2 }) : null,
    },
    logPrompts: bool(env.LLM_LOG_PROMPTS, false),
  };
}
