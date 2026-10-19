#!/usr/bin/env node
// A fake OpenAI-compatible LLM server for trying the AI assistant without any API key.
// It streams a canned answer word by word (Chat Completions SSE) and lists two models.
//
//   node scripts/fake-llm.mjs [port=3999]
//   LLM_ENABLED=true VLLM_BASE_URL=http://localhost:3999/v1 npm start
import http from 'node:http';

const port = Number(process.argv[2] || process.env.PORT || 3999);
const MODELS = ['fake-small', 'fake-large'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function selectedText(system) {
  const m = /## Selected text[^\n]*\n(`{3,})latex\n([\s\S]*?)\n\1/.exec(system || '');
  return m ? m[2] : null;
}

function answerFor(model, system, question) {
  const sel = selectedText(system);
  const q = question.toLowerCase();
  if (/napraw|fix/.test(q)) {
    const issue = /\d+\. \[error\] ([^\n]+)/.exec(system || '');
    return [
      issue ? `Błąd: **${issue[1]}**.` : 'Ostatnia kompilacja nie zgłosiła błędów.',
      'Najczęstsza przyczyna to literówka w nazwie polecenia. Poprawiony fragment:',
      '```latex\n\\textbf{poprawione}\n```',
    ].join('\n\n');
  }
  if (/opis|description/.test(q)) {
    return 'Proponowana tabela:\n\n```latex\n\\begin{tabular}{|c|c|}\n\\hline\n$x$ & $x^2$ \\\\\n\\hline\n1 & 1 \\\\\n2 & 4 \\\\\n\\hline\n\\end{tabular}\n```';
  }
  if (sel && /popraw|improve|rewrite/.test(q)) {
    return `Poprawiona wersja zaznaczenia:\n\n\`\`\`latex\n${sel.replace(/\s+/g, ' ').trim()} % (poprawione przez ${model})\n\`\`\``;
  }
  if (sel) return `Zaznaczony fragment (\`${sel.slice(0, 40)}\`) to polecenie LaTeX-a. To odpowiedź testowa modelu **${model}**.`;
  return `To odpowiedź testowa modelu **${model}** na: „${question.slice(0, 80)}”.\n\n- kontekst systemowy: ${system?.length || 0} znaków\n- wzór poniżej\n\n\`\`\`latex\n\\begin{equation}\n  e^{i\\pi} + 1 = 0\n\\end{equation}\n\`\`\``;
}

http
  .createServer(async (req, res) => {
    if (req.method === 'GET' && req.url.endsWith('/models')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ object: 'list', data: MODELS.map((id) => ({ id, object: 'model' })) }));
    }
    if (req.method !== 'POST' || !req.url.endsWith('/chat/completions')) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: { message: 'not found' } }));
    }
    let body = '';
    for await (const c of req) body += c;
    const json = JSON.parse(body || '{}');
    if (!MODELS.includes(json.model)) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: { message: `model ${json.model} not found` } }));
    }
    const system = json.messages?.find((m) => m.role === 'system')?.content || '';
    const question = json.messages?.filter((m) => m.role === 'user').at(-1)?.content || '';
    console.log(`[fake-llm] ${json.model} system=${system.length} chars, question=${JSON.stringify(question.slice(0, 60))}`);
    const text = answerFor(json.model, system, question);
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    let closed = false;
    res.on('close', () => (closed = true));
    for (const piece of text.match(/\S+\s*|\s+/g) || []) {
      if (closed) return console.log('[fake-llm] client went away');
      res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: piece } }] })}\n\n`);
      await sleep(25);
    }
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: Math.round(system.length / 4), completion_tokens: text.length >> 2 } })}\n\n`);
    res.end('data: [DONE]\n\n');
  })
  .listen(port, () => console.log(`fake LLM (OpenAI-compatible) on http://localhost:${port}/v1`));
