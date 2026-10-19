// AI assistant panel (TEX-02): provider/model picker, streamed answers with insertable code
// blocks, quick actions. The server builds the prompt context from the shared document; this
// panel only sends which file, the selection range and the conversation. Conversations live in
// memory per project (gone on reload).
import { h, icon, toast } from '../dom.js';
import { currentLang, t } from '../i18n.js';
import { prefs } from '../prefs.js';

const conversations = new Map(); // projectId -> { messages: [{ role, content, label?, error?, stopped?, selection? }] }

const ERRORS = {
  rate_limited: 'ai.err.rateLimited',
  busy: 'ai.err.busy',
  auth: 'ai.err.auth',
  timeout: 'ai.err.timeout',
  network: 'ai.err.network',
  bad_request: 'ai.err.badRequest',
  upstream: 'ai.err.upstream',
  config: 'ai.err.config',
  ai_disabled: 'ai.disabled',
  ai_not_configured: 'ai.err.notConfigured',
  forbidden: 'ai.err.forbidden',
};

const errorText = (code, message) => t(ERRORS[code] || 'ai.err.generic', { message: message || '' });

/** Minimal SSE reader over a fetch Response: yields { event, data }. */
async function* readSse(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = 'message';
      const data = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (data.length) yield { event, data: data.join('\n') };
    }
  }
}

// ---------- markdown-ish rendering (DOM only, never innerHTML) ----------

function inline(text) {
  const out = [];
  const re = /(`+)([\s\S]+?)\1|\*\*([^*\n]+)\*\*/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(m[2] !== undefined ? h('code', m[2]) : h('strong', m[3]));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Renders assistant text: paragraphs, headings, lists, inline code/bold and fenced code blocks. */
export function renderMarkdown(text, codeBlock) {
  const nodes = [];
  const lines = text.split('\n');
  let para = [];
  const flush = () => {
    if (para.length) nodes.push(h('p', inline(para.join('\n'))));
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = /^\s*(`{3,}|~{3,})\s*([\w+-]*)/.exec(line);
    if (fence) {
      flush();
      const marker = fence[1];
      const body = [];
      let closed = false;
      for (i++; i < lines.length; i++) {
        const tline = lines[i].trim();
        if (tline.startsWith(marker[0].repeat(marker.length)) && /^(`+|~+)$/.test(tline)) {
          closed = true;
          break;
        }
        body.push(lines[i]);
      }
      nodes.push(codeBlock(body.join('\n'), fence[2] || '', closed));
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      nodes.push(h('p.ai-heading', inline(heading[2])));
      continue;
    }
    const bullet = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      flush();
      const ordered = /\d/.test(bullet[1]);
      const items = [h('li', inline(bullet[2]))];
      while (i + 1 < lines.length) {
        const next = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i + 1]);
        if (!next || /\d/.test(next[1]) !== ordered) break;
        items.push(h('li', inline(next[2])));
        i++;
      }
      nodes.push(h(ordered ? 'ol' : 'ul', items));
      continue;
    }
    if (!line.trim()) flush();
    else para.push(line);
  }
  flush();
  return nodes;
}

// ---------- panel ----------

export class AiPanel {
  /**
   * opts: { projectId, getEditor, getFileId, getStateVector, getErrorCount, onDisabled, onClose }
   */
  constructor(container, opts) {
    this.opts = opts;
    this.projectId = opts.projectId;
    this.conv = conversations.get(this.projectId) || { messages: [] };
    conversations.set(this.projectId, this.conv);
    this.providers = [];
    this.controller = null;
    this.renderQueued = false;

    this.modelSel = h('select.input.small.ai-model', { 'aria-label': t('ai.model'), onchange: () => prefs.set('aiModel', this.modelSel.value) });
    this.logEl = h('div.ai-log', { role: 'log', 'aria-live': 'polite' });
    this.input = h('textarea.input.ai-input', {
      rows: 3,
      placeholder: t('ai.placeholder'),
      'aria-label': t('ai.placeholder'),
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          this.submit();
        }
      },
    });
    this.sendBtn = h('button.btn.primary.small.ai-send', { title: t('ai.send'), onclick: () => (this.busy ? this.stop() : this.submit()) }, icon('send'), h('span', t('ai.send')));
    this.includeFile = h('input', { type: 'checkbox', checked: prefs.get('aiIncludeFile') !== false, onchange: (e) => prefs.set('aiIncludeFile', e.target.checked) });
    this.includeErrors = h('input', { type: 'checkbox', checked: !!prefs.get('aiIncludeErrors'), onchange: (e) => prefs.set('aiIncludeErrors', e.target.checked) });

    const action = (label, title, fn) => h('button.chip', { title, onclick: fn }, label);
    this.fixBtn = action(t('ai.fixErrors'), t('ai.fixErrors'), () =>
      this.send(t('ai.fixErrorsPrompt'), { label: t('ai.fixErrors'), includeErrors: true, includeFile: true }),
    );
    this.actions = h(
      'div.ai-actions',
      this.fixBtn,
      action(t('ai.explain'), t('ai.explain'), () => this.send(t('ai.explainPrompt'), { label: t('ai.explain'), needsSelection: true })),
      action(t('ai.improve'), t('ai.improve'), () => this.send(t('ai.improvePrompt'), { label: t('ai.improve'), needsSelection: true })),
      action(t('ai.toLatex'), t('ai.toLatexTitle'), () => this.toLatex()),
    );

    this.el = h(
      'aside.ai-panel',
      { 'aria-label': t('ai.title') },
      h(
        'div.ai-head',
        icon('sparkles'),
        h('span.ai-title', t('ai.title')),
        h('div.spacer'),
        h('button.icon-btn', { title: t('ai.clear'), 'aria-label': t('ai.clear'), onclick: () => this.clear() }, icon('trash')),
        h('button.icon-btn', { title: t('common.close'), 'aria-label': t('common.close'), onclick: () => opts.onClose?.() }, icon('x')),
      ),
      h('div.ai-model-row', this.modelSel),
      this.actions,
      this.logEl,
      h(
        'div.ai-foot',
        h(
          'div.ai-ctx',
          h('label.ai-check', this.includeFile, t('ai.includeFile')),
          h('label.ai-check', this.includeErrors, t('ai.includeErrors')),
        ),
        h('div.ai-compose', this.input, this.sendBtn),
      ),
    );
    container.append(this.el);
    this.render();
  }

  get busy() {
    return !!this.controller;
  }

  async loadProviders() {
    try {
      const res = await fetch(`/api/ai/providers?projectId=${encodeURIComponent(this.projectId)}`, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.enabled) {
        if (data.reason === 'ai_disabled') this.opts.onDisabled?.();
        this.providers = [];
      } else {
        this.providers = data.providers;
      }
      this.defaultProvider = data.defaultProvider;
    } catch {
      this.providers = [];
    }
    this.renderModels();
    this.render();
  }

  renderModels() {
    const saved = prefs.get('aiModel');
    const options = [];
    for (const p of this.providers) {
      options.push(h('optgroup', { label: p.label }, p.models.map((m) => h('option', { value: `${p.id}|${m.id}` }, m.label))));
    }
    this.modelSel.replaceChildren(...options);
    const values = this.providers.flatMap((p) => p.models.map((m) => `${p.id}|${m.id}`));
    const def = this.providers.find((p) => p.id === this.defaultProvider) || this.providers[0];
    this.modelSel.value = values.includes(saved) ? saved : def ? `${def.id}|${def.defaultModel}` : '';
    this.modelSel.disabled = !values.length;
  }

  selectedModel() {
    const [providerId, ...rest] = (this.modelSel.value || '').split('|');
    const model = rest.join('|');
    const provider = this.providers.find((p) => p.id === providerId);
    return provider && model ? { providerId, model, label: provider.label } : null;
  }

  setErrorCount(n) {
    this.fixBtn.textContent = n ? `${t('ai.fixErrors')} (${n})` : t('ai.fixErrors');
    this.fixBtn.classList.toggle('chip-alert', !!n);
  }

  focus() {
    this.input.focus();
  }

  submit() {
    const text = this.input.value.trim();
    if (!text || this.busy) return;
    this.input.value = '';
    this.send(text, { includeErrors: this.includeErrors.checked });
  }

  toLatex() {
    const sel = this.opts.getEditor()?.getSelection();
    const typed = this.input.value.trim();
    if (sel && sel.to > sel.from) return this.send(t('ai.toLatexPrompt'), { label: t('ai.toLatex'), needsSelection: true });
    if (typed) {
      this.input.value = '';
      return this.send(`${t('ai.toLatexPromptTyped')}\n\n${typed}`, { label: t('ai.toLatex') });
    }
    toast(t('ai.toLatexHint'));
  }

  clear() {
    this.stop();
    this.conv.messages = [];
    this.render();
  }

  stop() {
    this.controller?.abort();
  }

  async send(text, { label = null, needsSelection = false, includeErrors = false, includeFile } = {}) {
    if (this.busy) return;
    const chosen = this.selectedModel();
    if (!chosen) return toast(t('ai.noProviders'), 'error');
    const editor = this.opts.getEditor();
    const fileId = this.opts.getFileId();
    const sel = editor?.getSelection() || null;
    const hasSel = !!sel && sel.to > sel.from;
    if (needsSelection && !hasSel) return toast(t('ai.needSelection'));

    const history = this.conv.messages.filter((m) => !m.error && m.content).map((m) => ({ role: m.role, content: m.content }));
    this.conv.messages.push({ role: 'user', content: text, label });
    const answer = { role: 'assistant', content: '', pending: true, selection: hasSel ? { fileId, from: sel.from, to: sel.to, text: sel.text } : null };
    this.conv.messages.push(answer);
    this.controller = new AbortController();
    this.forceScroll = true;
    this.render();

    const body = {
      projectId: this.projectId,
      providerId: chosen.providerId,
      model: chosen.model,
      lang: currentLang(),
      stateVector: this.opts.getStateVector?.(),
      messages: [...history, { role: 'user', content: text }],
      context: {
        fileId: editor ? fileId : null,
        selection: sel ? { from: sel.from, to: sel.to } : null,
        includeFile: includeFile ?? this.includeFile.checked,
        includeErrors,
      },
    };
    try {
      const res = await fetch('/api/ai/chat', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: this.controller.signal,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        answer.error = errorText(data.error, data.message);
        if (data.error === 'ai_disabled') this.opts.onDisabled?.();
        return;
      }
      for await (const ev of readSse(res)) {
        const data = JSON.parse(ev.data);
        if (ev.event === 'delta') {
          answer.content += data.text;
          this.scheduleRender();
        } else if (ev.event === 'error') {
          answer.error = errorText(data.error, data.message);
          if (data.error === 'ai_disabled') this.opts.onDisabled?.();
        } else if (ev.event === 'done' && data.finishReason && /length|max_tokens|MAX_TOKENS/i.test(data.finishReason)) {
          answer.truncated = true;
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') answer.stopped = true;
      else answer.error = errorText('network');
    } finally {
      answer.pending = false;
      this.controller = null;
      this.render();
    }
  }

  scheduleRender() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => {
      this.renderQueued = false;
      this.render();
    });
  }

  /** Inserts at the cursor; a multi-line block gets its own lines instead of gluing to text around it. */
  insertBlock(code) {
    const editor = this.opts.getEditor();
    if (!editor) return toast(t('editor.selectFile'));
    let text = code;
    if (code.includes('\n')) {
      const { to } = editor.getSelection();
      const before = editor.sliceDoc(Math.max(0, to - 1), to);
      const after = editor.sliceDoc(to, to + 1);
      if (before && before !== '\n') text = `\n${text}`;
      if (after && after !== '\n') text = `${text}\n`;
    }
    editor.insertAtCursor(text);
  }

  replaceSelection(code, msg) {
    const editor = this.opts.getEditor();
    if (!editor) return toast(t('ai.selectToReplace'));
    const sel = editor.getSelection();
    if (sel.to > sel.from) return editor.replaceRange(sel.from, sel.to, code);
    // The selection the question was about, if it is still there unchanged.
    const s = msg.selection;
    if (s && s.fileId === this.opts.getFileId() && editor.sliceDoc(s.from, s.to) === s.text) return editor.replaceRange(s.from, s.to, code);
    toast(t('ai.selectToReplace'));
  }

  codeBlock(msg) {
    return (code, lang, closed) => {
      const btn = (label, ic, fn) =>
        h('button.btn.small', { title: label, 'aria-label': label, disabled: !closed && msg.pending, onclick: fn }, icon(ic, 14), h('span.ai-btn-label', label));
      return h(
        'div.ai-code',
        h(
          'div.ai-code-head',
          h('span.ai-code-lang', lang || 'text'),
          h('div.spacer'),
          btn(t('ai.insert'), 'plus', () => this.insertBlock(code)),
          btn(t('ai.replace'), 'edit', () => this.replaceSelection(code, msg)),
          btn(t('ai.copy'), 'copy', () => navigator.clipboard.writeText(code).then(() => toast(t('common.copied')))),
        ),
        h('pre', h('code', code)),
      );
    };
  }

  render() {
    const stick = this.logEl.scrollHeight - this.logEl.scrollTop - this.logEl.clientHeight < 40;
    const items = this.conv.messages.map((m) => {
      if (m.role === 'user') return h('div.ai-msg.ai-user', m.label ? h('span.ai-tag', m.label) : m.content);
      const body = [];
      if (m.content) body.push(...renderMarkdown(m.content, this.codeBlock(m)));
      else if (m.pending) body.push(h('p.ai-thinking', t('ai.thinking')));
      if (m.stopped) body.push(h('p.muted.small', t('ai.stopped')));
      if (m.truncated) body.push(h('p.muted.small', t('ai.truncated')));
      if (m.error) body.push(h('p.ai-error', icon('alert', 14), m.error));
      return h('div.ai-msg.ai-assistant', { class: m.pending ? 'pending' : '' }, body);
    });
    if (!items.length) {
      const chosen = this.selectedModel();
      items.push(
        h(
          'div.ai-empty',
          h('p', t(this.providers.length ? 'ai.empty' : 'ai.noProviders')),
          chosen ? h('p.muted.small', t('ai.privacy', { provider: chosen.label })) : null,
        ),
      );
    }
    this.logEl.replaceChildren(...items);
    if (stick || this.forceScroll) this.logEl.scrollTop = this.logEl.scrollHeight;
    this.forceScroll = false;
    this.sendBtn.replaceChildren(icon(this.busy ? 'stop' : 'send'), h('span', t(this.busy ? 'ai.stop' : 'ai.send')));
    this.sendBtn.title = t(this.busy ? 'ai.stop' : 'ai.send');
    this.sendBtn.classList.toggle('danger', this.busy);
  }

  destroy() {
    this.stop();
    this.el.remove();
  }
}
