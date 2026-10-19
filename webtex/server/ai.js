// AI assistant API (TEX-02). The browser never talks to a provider and never sees a key.
//   GET  /api/ai/providers[?projectId=]  provider ids, labels and models (+ whether AI is usable)
//   POST /api/ai/chat                    SSE stream: meta, delta*, done | error
// The prompt context (file text, selection, compile issues) is built here from server state;
// the client only says which file and which character range.
import express from 'express';
import { requireUser } from './auth.js';
import { getText } from './collab.js';
import { lastCompileIssues } from './compiler.js';
import { config } from './config.js';
import { one } from './db.js';
import { aiBlockReason, trackAiRequest } from './aiPolicy.js';
import { roleFor, waitForState } from './projects.js';
import { entityPaths, listEntities } from './tree.js';
import { LlmError, chat, llmConfig, llmEnabled, publicProviders, resolveModel } from './llm/index.js';
import { buildSystemPrompt, excerpt, lineOf, numberedSnippet } from './llm/prompt.js';

export const aiRouter = express.Router();
aiRouter.use(requireUser);

const MAX_MESSAGES = 60;
const MAX_ISSUES = 12;

function projectAccess(req, res, projectId) {
  const project = typeof projectId === 'string' && one(`SELECT * FROM projects WHERE id = ?`, projectId);
  const role = project && roleFor(req.user.id, project.id);
  if (!role) {
    res.status(404).json({ error: 'not_found', message: 'Project not found' });
    return null;
  }
  return { project, role };
}

aiRouter.get('/providers', async (req, res) => {
  if (!llmEnabled()) return res.json({ enabled: false, reason: 'not_configured', providers: [], defaultProvider: null });
  if (req.query.projectId) {
    const a = projectAccess(req, res, String(req.query.projectId));
    if (!a) return;
    const reason = a.role === 'viewer' ? 'forbidden' : aiBlockReason(a.project);
    if (reason) return res.json({ enabled: false, reason, providers: [], defaultProvider: null });
  }
  const { providers, defaultProvider } = await publicProviders();
  res.json({ enabled: providers.length > 0, providers, defaultProvider });
});

// ---------- per-user limits ----------

const usage = new Map(); // userId -> { times: number[], active: number }

function admit(userId) {
  const { rateLimit, rateWindowSec, maxConcurrent } = llmConfig().limits;
  const u = usage.get(userId) || { times: [], active: 0 };
  usage.set(userId, u);
  const now = Date.now();
  u.times = u.times.filter((t) => now - t < rateWindowSec * 1000);
  if (u.active >= maxConcurrent) return { error: 'busy' };
  if (rateLimit > 0 && u.times.length >= rateLimit) {
    return { error: 'rate_limited', retryAfter: Math.ceil((u.times[0] + rateWindowSec * 1000 - now) / 1000) };
  }
  u.times.push(now);
  u.active++;
  let released = false;
  return {
    release: () => {
      if (!released) u.active--;
      released = true;
    },
  };
}
setInterval(() => {
  const { rateWindowSec } = llmConfig().limits;
  for (const [id, u] of usage) if (!u.active && u.times.every((t) => Date.now() - t > rateWindowSec * 1000)) usage.delete(id);
}, 10 * 60 * 1000).unref();

// ---------- request validation ----------

/** Validated conversation, oldest turns dropped to fit the input budget. */
function cleanMessages(raw, maxChars) {
  if (!Array.isArray(raw) || !raw.length) return null;
  const msgs = [];
  for (const m of raw.slice(-MAX_MESSAGES)) {
    if (!m || (m.role !== 'user' && m.role !== 'assistant') || typeof m.content !== 'string') return null;
    msgs.push({ role: m.role, content: m.content.slice(0, maxChars) });
  }
  if (msgs[msgs.length - 1].role !== 'user' || !msgs[msgs.length - 1].content.trim()) return null;
  const kept = [];
  let total = 0;
  for (let i = msgs.length - 1; i >= 0; i--) {
    total += msgs[i].content.length;
    if (total > maxChars && kept.length) break;
    kept.unshift(msgs[i]);
  }
  return kept;
}

const int = (v) => (Number.isInteger(v) && v >= 0 ? v : null);

async function buildContext(project, ctx, lang) {
  const { contextChars } = llmConfig().limits;
  const entities = listEntities(project.id);
  const pathOf = entityPaths(entities);
  const out = {
    lang,
    projectName: project.name,
    compiler: project.compiler,
    mainFile: project.main_file_id ? pathOf.get(project.main_file_id) : null,
  };
  const texts = new Map();
  const textOf = async (id) => {
    if (!texts.has(id)) texts.set(id, await getText(project.id, id));
    return texts.get(id);
  };

  const file = typeof ctx?.fileId === 'string' && entities.find((e) => e.id === ctx.fileId && e.kind === 'doc');
  if (file) {
    const path = pathOf.get(file.id);
    const text = await textOf(file.id);
    let from = int(ctx.selection?.from);
    let to = int(ctx.selection?.to);
    if (from !== null) {
      from = Math.min(from, text.length);
      to = to === null ? from : Math.min(Math.max(to, from), text.length);
      if (to > from) {
        const selected = text.slice(from, Math.min(to, from + contextChars));
        out.selection = { path, fromLine: lineOf(text, from), toLine: lineOf(text, to), text: selected };
      } else {
        out.cursor = { path, line: lineOf(text, from) };
      }
    }
    if (ctx.includeFile) out.file = { path, ...excerpt(text, { maxChars: contextChars, focusFrom: from ?? 0, focusTo: to ?? from ?? 0 }) };
  }

  if (ctx?.includeErrors) {
    const last = await lastCompileIssues(project.id);
    if (!last) {
      out.issues = [];
      out.issuesNote = 'The project has not been compiled yet.';
    } else {
      const errors = last.issues.filter((i) => i.level === 'error');
      const picked = (errors.length ? errors : last.issues.filter((i) => i.level === 'warning')).slice(0, MAX_ISSUES);
      const idOf = new Map([...pathOf].map(([id, p]) => [p, id]));
      out.issues = [];
      for (const i of picked) {
        const rel = i.file ? String(i.file).replace(/^\.\//, '') : null;
        const id = rel && (idOf.get(rel) || idOf.get(`${rel}.tex`));
        const doc = id && entities.find((e) => e.id === id && e.kind === 'doc');
        out.issues.push({
          level: i.level,
          file: rel,
          line: i.line ?? null,
          message: String(i.message || '').slice(0, 500),
          snippet: doc && i.line ? numberedSnippet(await textOf(doc.id), i.line) : '',
        });
      }
      if (!out.issues.length && last.message) out.issues.push({ level: 'error', file: null, line: null, message: String(last.message).slice(0, 500), snippet: '' });
      out.issuesNote = last.status === 'timedout' ? 'the compilation timed out' : !errors.length && out.issues.length ? 'no errors, only warnings' : '';
    }
  }
  return out;
}

// ---------- chat ----------

aiRouter.post('/chat', async (req, res) => {
  const b = req.body || {};
  const a = projectAccess(req, res, b.projectId);
  if (!a) return;
  const { project, role } = a;
  if (role === 'viewer') return res.status(403).json({ error: 'forbidden', message: 'Read-only members cannot use the assistant' });
  const blocked = aiBlockReason(project);
  if (blocked === 'not_configured') return res.status(404).json({ error: 'ai_not_configured', message: 'The AI assistant is not enabled on this server' });
  if (blocked) return res.status(403).json({ error: 'ai_disabled', message: 'The AI assistant is disabled for this project' });

  const cfg = llmConfig();
  const messages = cleanMessages(b.messages, cfg.limits.maxInputChars);
  if (!messages) return res.status(400).json({ error: 'bad_request', message: 'Invalid messages' });
  let resolved;
  try {
    resolved = await resolveModel(String(b.providerId || ''), String(b.model || ''));
  } catch (err) {
    return res.status(400).json({ error: 'bad_request', message: err.message });
  }

  const slot = admit(req.user.id);
  if (slot.error) {
    if (slot.retryAfter) res.setHeader('Retry-After', String(slot.retryAfter));
    return res.status(429).json({ error: slot.error, retryAfter: slot.retryAfter ?? null, message: slot.error === 'busy' ? 'Another answer is still running' : 'Too many requests, try again later' });
  }

  const started = Date.now();
  const controller = new AbortController();
  const untrack = trackAiRequest(project.id, controller);
  let heartbeat = null;
  let status = 'ok';
  let usageOut = null;
  res.on('close', () => {
    if (!res.writableFinished) controller.abort(new Error('client_closed'));
  });

  try {
    // Compile-like consistency: wait until the client's latest edits reached the server.
    if (typeof b.stateVector === 'string') await waitForState(project.id, b.stateVector, 2000);
    const lang = b.lang === 'pl' || b.lang === 'en' ? b.lang : config.integration.enabled ? 'pl' : 'en';
    const system = buildSystemPrompt(await buildContext(project, b.context || {}, lang));
    if (cfg.logPrompts) console.log(`[ai] prompt project=${project.id}\n${system}\n---\n${JSON.stringify(messages)}`);

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Accel-Buffering': 'no',
      Connection: 'keep-alive',
    });
    const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    heartbeat = setInterval(() => res.write(': ping\n\n'), 15_000);
    send('meta', { provider: resolved.provider.id, model: resolved.model.id });

    try {
      for await (const ev of chat({ providerId: resolved.provider.id, model: resolved.model.id, system, messages, signal: controller.signal })) {
        if (ev.type === 'text') send('delta', { text: ev.text });
        else if (ev.type === 'done') {
          usageOut = ev.usage;
          send('done', { usage: ev.usage, finishReason: ev.finishReason });
        }
      }
    } catch (err) {
      const reason = controller.signal.reason?.message;
      const code = reason === 'ai_disabled' ? 'ai_disabled' : err instanceof LlmError ? err.code : 'internal';
      status = `error:${code}`;
      if (code === 'internal') console.error('[ai] unexpected error', err);
      if (reason !== 'client_closed') send('error', { error: code, message: code === 'internal' ? 'Internal error' : err.message });
    }
    res.end();
  } catch (err) {
    status = 'error:internal';
    console.error('[ai] request failed', err);
    if (!res.headersSent) res.status(500).json({ error: 'internal', message: 'Internal server error' });
    else res.end();
  } finally {
    clearInterval(heartbeat);
    untrack();
    slot.release();
    // Metadata only: no prompts or answers unless LLM_LOG_PROMPTS=true.
    console.log(
      `[ai] user=${req.user.id} project=${project.id} provider=${resolved.provider.id} model=${resolved.model.id} ` +
        `status=${controller.signal.reason?.message === 'client_closed' ? 'cancelled' : status} in=${usageOut?.inputTokens ?? '-'} out=${usageOut?.outputTokens ?? '-'} ms=${Date.now() - started}`,
    );
  }
});
