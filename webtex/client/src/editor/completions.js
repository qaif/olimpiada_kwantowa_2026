// LaTeX autocompletion for CodeMirror: commands with snippets and descriptions, environments, labels,
// citation keys, packages and project files, chosen by context (math or text mode, open environments)
// and ranked by match quality plus local usage history. Everything runs in the browser, offline.
// The pure logic lives in ./latex/ (catalog, context, scan, options, rank) and is unit tested.
import { pickedCompletion, snippet } from '@codemirror/autocomplete';
import { indentUnit } from '@codemirror/language';
import { EditorState } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { h } from '../dom.js';
import { currentLang, t } from '../i18n.js';
import {
  ENV_WINDOW,
  analyzeContext,
  beginBeforeCursor,
  isEnvironmentClosed,
  openEnvironments,
  paragraphStart,
} from './latex/context.js';
import {
  CITE_CMDS,
  OFF_CONTEXT_MIN_TYPED,
  REF_CMDS,
  argumentContext,
  classOptions,
  commandOptions,
  environmentOptions,
  labelOptions,
  labelPrefix,
  packageOptions,
} from './latex/options.js';
import { createUsageStore } from './latex/rank.js';

export { scanFile, mergeSymbols, scanSymbols } from './latex/scan.js';

const IMAGE_EXT = /\.(png|jpe?g|pdf|eps|svg|gif)$/i;
const NO_SYMBOLS = { labels: [], citeKeys: [], files: [], commands: [], environments: [], packages: [] };

let usageStore = null;
const usage = () => (usageStore ??= createUsageStore());

// Snippet parsing is done once per template, not on every keystroke.
const snippets = new Map();
const cachedSnippet = (template) => {
  let s = snippets.get(template);
  if (!s) snippets.set(template, (s = snippet(template)));
  return s;
};

// Swallow a `}` that closeBrackets already inserted after the cursor.
const swallowBrace = (apply) => (view, completion, from, to) => {
  const end = view.state.sliceDoc(to, to + 1) === '}' ? to + 1 : to;
  apply(view, completion, from, end);
};

const insertWithBrace = (text) => (view, completion, from, to) => {
  const hasBrace = view.state.sliceDoc(to, to + 1) === '}';
  const insert = hasBrace ? text : `${text}}`;
  view.dispatch({
    changes: { from, to, insert },
    selection: { anchor: from + insert.length },
    userEvent: 'input.complete',
    annotations: pickedCompletion.of(completion),
  });
};

function offContextNote(info) {
  if (!info.offContext) return null;
  if (info.envs) return t('cmp.offContextEnv', { envs: info.envs.join(', ') });
  return info.mode === 'math' ? t('cmp.offContextMath') : t('cmp.offContextText');
}

// Snippet fields (⟨name⟩ from renderTemplate) as highlighted spans: in monospace fonts the angle
// brackets look too much like ( and }.
const syntaxNode = (syntax) =>
  h(
    'code.cm-latex-info-syntax',
    syntax.split(/(⟨[^⟨⟩]*⟩)/).map((part) => (/^⟨.*⟩$/.test(part) ? h('span.cm-latex-field', part.slice(1, -1)) : part)),
  );

/** Info panel next to the selected completion: syntax, description, package, where it is defined. */
const renderInfo = (info) => () => {
  const meta = [];
  if (info.category) meta.push(info.category);
  if (info.pkg) {
    const pkgs = info.pkg.split('|');
    meta.push(
      info.pkgLoaded === false
        ? h('span.cm-latex-info-warn', t('cmp.requires', { pkg: pkgs[0] }))
        : `${t('cmp.package', { pkg: pkgs.join(' / ') })}${info.pkgLoaded ? ` (${t('cmp.loaded')})` : ''}`,
    );
  }
  if (info.nargs) meta.push(t('cmp.args', { n: info.nargs }));
  const where = info.definedAt?.file ? t(info.preview ? 'cmp.labelAt' : 'cmp.definedAt', info.definedAt) : null;
  return h(
    'div.cm-latex-info',
    info.syntax ? syntaxNode(info.syntax) : null,
    info.preview ? h('code.cm-latex-info-syntax', info.preview) : null,
    info.description ? h('div', info.description) : null,
    meta.length ? h('div.cm-latex-info-meta', meta.flatMap((m, k) => (k ? [' · ', m] : [m]))) : null,
    where ? h('div.cm-latex-info-meta', where) : null,
    offContextNote(info) ? h('div.cm-latex-info-warn', offContextNote(info)) : null,
  );
};

const toCompletion = (o, apply) => ({
  label: o.label,
  detail: o.detail || undefined,
  boost: o.boost,
  type: o.kind,
  apply,
  info: o.info ? renderInfo(o.info) : undefined,
  usageId: o.id,
});

/** Mode and open environments at `pos`; only bounded slices before the cursor are scanned. */
export function cursorContext(state, pos) {
  const envFrom = Math.max(0, pos - ENV_WINDOW);
  const before = state.sliceDoc(envFrom, pos);
  const modeText = before.slice(paragraphStart(before));
  const ctx = analyzeContext(modeText);
  return { ...ctx, envs: openEnvironments(before) };
}

/** Returns a CodeMirror CompletionSource for LaTeX. */
export function latexCompletionSource(getSymbols) {
  return (context) => {
    const { state, pos } = context;
    const line = state.doc.lineAt(pos);
    const before = line.text.slice(0, pos - line.from);
    const lang = currentLang();
    let symbolCache = null;
    const symbols = () => (symbolCache ??= { ...NO_SYMBOLS, ...(getSymbols?.() || {}) });

    // Inside a command argument: \cmd[opt]{arg
    const arg = argumentContext(before);
    if (arg) {
      const { cmd, word } = arg;
      let options = null;
      let validFor = /^[^{},\s]*$/;
      const needsCtx = cmd === 'begin' || cmd === 'end' || cmd === 'label';
      const ctx = needsCtx ? cursorContext(state, pos - arg.content.length) : null;
      if (ctx && (ctx.inComment || ctx.inVerbatim)) return null;

      if (cmd === 'begin') {
        options = environmentOptions({ kind: 'begin', ctx, symbols: symbols(), lang, usage: usage() }).map((o) =>
          toCompletion(o, swallowBrace(cachedSnippet(o.template))),
        );
      } else if (cmd === 'end') {
        options = environmentOptions({ kind: 'end', ctx, symbols: symbols(), lang, usage: usage(), open: ctx.envs }).map((o) =>
          toCompletion(o, insertWithBrace(o.label)),
        );
      } else if (cmd === 'label') {
        if (arg.content.includes(':') && !context.explicit) return null;
        const prefix = labelPrefix(ctx.envs);
        options = [{ label: prefix, detail: t('cmp.labelPrefix'), type: 'text' }];
        validFor = /^[^{},\s:]*$/;
      } else if (REF_CMDS.has(cmd)) {
        options = labelOptions({ cmd, symbols: symbols(), usage: usage() }).map((o) => toCompletion(o, insertWithBrace(o.label)));
      } else if (CITE_CMDS.test(cmd)) {
        options = symbols().citeKeys.map((k) => ({ label: k, type: 'text', apply: insertWithBrace(k) }));
      } else if (cmd === 'includegraphics') {
        options = symbols().files.filter((f) => IMAGE_EXT.test(f)).map((f) => ({ label: f, type: 'variable', apply: insertWithBrace(f) }));
      } else if (cmd === 'input' || cmd === 'include' || cmd === 'subfile' || cmd === 'includeonly') {
        options = symbols()
          .files.filter((f) => /\.tex$/i.test(f))
          .map((f) => ({ label: f.replace(/\.tex$/i, ''), type: 'variable', detail: f }));
      } else if (cmd === 'addbibresource') {
        options = symbols().files.filter((f) => /\.bib$/i.test(f)).map((f) => ({ label: f, type: 'variable' }));
      } else if (cmd === 'bibliography') {
        options = symbols()
          .files.filter((f) => /\.bib$/i.test(f))
          .map((f) => ({ label: f.replace(/\.bib$/i, ''), type: 'variable' }));
      } else if (cmd === 'usepackage' || cmd === 'RequirePackage') {
        options = packageOptions({ symbols: symbols(), lang, usage: usage() }).map((o) => toCompletion(o, o.label));
      } else if (cmd === 'documentclass') {
        options = classOptions({ lang }).map((o) => toCompletion(o, o.label));
      }

      if (!options) return null;
      if (!options.length && !context.explicit) return null;
      return { from: pos - word.length, options, validFor };
    }

    // A command name: \sec|
    const cmd = context.matchBefore(/\\[a-zA-Z@]*\*?/);
    if (cmd && (cmd.text.length > 1 || context.explicit)) {
      // Don't complete escaped backslashes (\\ line breaks).
      if (cmd.from > line.from && state.sliceDoc(cmd.from - 1, cmd.from) === '\\') return null;
      const ctx = cursorContext(state, cmd.from);
      if (ctx.inComment || ctx.inVerbatim) return null;
      // Commands of the other mode (e.g. \alpha in text) only once the name is long enough to be deliberate.
      const includeOffContext = context.explicit || cmd.text.length >= OFF_CONTEXT_MIN_TYPED;
      const options = commandOptions({ ctx, symbols: symbols(), lang, usage: usage(), includeOffContext }).map((o) =>
        toCompletion(o, o.template ? cachedSnippet(o.template) : o.label),
      );
      return {
        from: cmd.from,
        options,
        validFor: (text) => /^\\[a-zA-Z@]*\*?$/.test(text) && (includeOffContext || text.length < OFF_CONTEXT_MIN_TYPED),
      };
    }
    return null;
  };
}

/** Remembers picked completions (locally) so frequent and recent ones rank higher. */
export const completionUsageTracker = EditorView.updateListener.of((update) => {
  for (const tr of update.transactions) {
    const picked = tr.annotation(pickedCompletion);
    if (picked?.usageId) usage().record(picked.usageId);
  }
});

/** Enter right after a hand-typed `\begin{name}`: adds the matching `\end{name}` unless it already exists. */
export function insertEnvironmentEnd(view) {
  const { state } = view;
  if (state.readOnly || state.selection.ranges.length > 1) return false;
  const sel = state.selection.main;
  if (!sel.empty) return false;
  const line = state.doc.lineAt(sel.head);
  if (line.text.slice(sel.head - line.from).trim()) return false;
  const env = beginBeforeCursor(line.text.slice(0, sel.head - line.from));
  if (!env) return false;
  if (isEnvironmentClosed(env, state.sliceDoc(sel.head, Math.min(state.doc.length, sel.head + 200000)))) return false;
  const indent = /^\s*/.exec(line.text)[0];
  const unit = state.facet(indentUnit);
  const insert = `\n${indent}${unit}\n${indent}\\end{${env}}`;
  view.dispatch({
    changes: { from: sel.head, to: line.to, insert },
    selection: { anchor: sel.head + 1 + indent.length + unit.length },
    scrollIntoView: true,
    userEvent: 'input',
  });
  return true;
}

/** Screen-reader name of the completion list in the UI language. */
export const completionPhrases = () => EditorState.phrases.of({ Completions: t('cmp.completions') });
