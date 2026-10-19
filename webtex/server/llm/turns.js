// Conversation normalisation shared by the adapters. Input: [{ role: 'user'|'assistant', content }].
// Output alternates strictly, starts and ends with 'user' (what Anthropic, Gemini, Bedrock and
// Cohere require; harmless for the rest).
export function normalizeTurns(messages) {
  const out = [];
  for (const m of messages || []) {
    const role = m?.role === 'assistant' ? 'assistant' : m?.role === 'user' ? 'user' : null;
    const content = typeof m?.content === 'string' ? m.content : '';
    if (!role || !content.trim()) continue;
    const last = out[out.length - 1];
    if (last && last.role === role) last.content += `\n\n${content}`;
    else out.push({ role, content });
  }
  while (out.length && out[0].role !== 'user') out.shift();
  while (out.length && out[out.length - 1].role !== 'user') out.pop();
  return out;
}

/** Text of an OpenAI-style `content`, which may be a string or a list of parts (Mistral, vLLM). */
export function contentText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((p) => (typeof p === 'string' ? p : p?.type === 'text' || p?.type === undefined ? p?.text || '' : '')).join('');
  }
  return '';
}

export const usage = (input, output) => ({
  inputTokens: Number.isFinite(input) ? input : null,
  outputTokens: Number.isFinite(output) ? output : null,
});
