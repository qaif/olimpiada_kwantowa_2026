// Provider-independent error for the LLM layer. `code` is stable and safe to show to clients:
//   auth            the provider rejected the credentials (401/403)
//   rate_limited    the provider throttled us (429)
//   bad_request     the provider refused the request (400/404/413/422: unknown model, too long…)
//   upstream        the provider failed (5xx, malformed stream, error event mid-stream)
//   timeout         no answer / no new data in time
//   network         the provider could not be reached
//   aborted         the client went away (or the assistant was disabled mid-answer)
//   config          the provider is configured incompletely (missing key, region…)
export class LlmError extends Error {
  constructor(code, message, { status = null, provider = null, cause } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = 'LlmError';
    this.code = code;
    this.status = status;
    this.provider = provider;
  }
}

export function codeForStatus(status) {
  if (status === 401 || status === 403) return 'auth';
  if (status === 429) return 'rate_limited';
  if (status >= 500) return 'upstream';
  return 'bad_request';
}

const MAX_MESSAGE = 300;

/** Pulls a human readable message out of the various provider error bodies. */
export function providerMessage(body) {
  if (body == null) return '';
  let value = body;
  if (typeof body === 'string') {
    const text = body.trim();
    try {
      value = JSON.parse(text);
    } catch {
      return text.slice(0, MAX_MESSAGE);
    }
  }
  if (Array.isArray(value)) value = value[0];
  const pick = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null);
  const msg =
    pick(value?.error?.message) || // OpenAI, Anthropic, Gemini, Azure
    pick(value?.error) || // Ollama
    pick(value?.message) || // Cohere, Mistral, Bedrock
    pick(value?.Message) ||
    pick(typeof value?.detail === 'string' ? value.detail : value?.detail?.[0]?.msg) || // Mistral 422, FastAPI (vLLM)
    '';
  return msg.slice(0, MAX_MESSAGE);
}
