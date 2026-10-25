// Problemy w kodzie prosto w edytorze: błędy i ostrzeżenia z ostatniej kompilacji (zmapowane na
// linie przez latex/diagnostics.js) razem z szybkim sprawdzaniem bez kompilacji (latex/lint.js).
// Podkreślenie, znaczniki na marginesie, podświetlenie linii, dymek z treścią i kontekstem z logu,
// F8 / Shift+F8 — następny / poprzedni problem.
import { StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, ViewPlugin, keymap } from '@codemirror/view';
import {
  forEachDiagnostic,
  lintGutter,
  linter,
  nextDiagnostic,
  previousDiagnostic,
  setDiagnosticsEffect,
} from '@codemirror/lint';
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { lintLatex, usepackageInsertion } from './latex/lint.js';
import { structureOf } from './docStructure.js';

/** Efekt: nowe diagnostyki z kompilacji (lista z compileDiagnostics). */
export const setCompileDiagnosticsEffect = StateEffect.define();
const refreshEffect = StateEffect.define();

/**
 * Diagnostyki z kompilacji w bieżącym dokumencie. Edycja przesuwa je razem z tekstem, a te z linii,
 * które ktoś zmienił, znikają (to zwykle poprawka) — reszta czeka na następną kompilację.
 */
export const compileDiagnosticsField = StateField.define({
  create: () => [],
  update(list, tr) {
    for (const e of tr.effects) if (e.is(setCompileDiagnosticsEffect)) return e.value;
    if (!tr.docChanged || !list.length) return list;
    const old = tr.startState.doc;
    const touched = [];
    tr.changes.iterChangedRanges((fromA, toA) => touched.push([old.lineAt(fromA).from, old.lineAt(toA).to]));
    const out = [];
    for (const d of list) {
      const a = old.lineAt(d.from).from;
      const b = old.lineAt(d.to).to;
      if (touched.some(([x, y]) => a <= y && b >= x)) continue;
      const from = tr.changes.mapPos(d.from, 1);
      out.push({ ...d, from, to: Math.max(from, tr.changes.mapPos(d.to, -1)) });
    }
    return out;
  },
});

// Linia, w której użytkownik właśnie pisze: szybkie sprawdzanie nie krzyczy o niedokończonym kodzie
// (otwarty $, \ref{eq: w trakcie pisania), dopóki kursor jej nie opuści.
const editingLine = StateField.define({
  create: () => null,
  update(line, tr) {
    const head = tr.state.selection.main.head;
    const now = tr.state.doc.lineAt(head).number;
    if (tr.docChanged && (tr.isUserEvent('input') || tr.isUserEvent('delete'))) return now;
    if (line !== null && tr.selection && now !== line) return null;
    return line;
  },
});

const CLOSE = { $: '$', $$: '$$', '\\(': '\\)', '\\[': '\\]' };
const opener = (open) => (open in CLOSE ? open : `\\begin{${open}}`);

/** Treść komunikatu szybkiego sprawdzania w języku interfejsu. */
export function lintMessage(p) {
  const q = p.params || {};
  switch (p.code) {
    case 'math-unclosed':
      return t('lint.math-unclosed', { open: q.open, close: CLOSE[q.open] || '' });
    case 'math-close-unmatched':
      return t('lint.math-close-unmatched', { close: q.close, open: q.close === '\\)' ? '\\(' : '\\[' });
    case 'math-blank-line':
    case 'math-nested':
    case 'dollar-in-math':
      return t(`lint.${p.code}`, { open: opener(q.open) });
    case 'env-unclosed':
      return t(q.before ? 'lint.env-unclosed-before' : 'lint.env-unclosed', q);
    case 'label-duplicate':
      return t('lint.label-duplicate', { name: q.name, where: typeof q.where === 'number' ? t('lint.line', { n: q.where }) : q.where });
    case 'package-missing':
      return t('lint.package-missing', { name: q.name, pkg: q.pkg, pkgs: q.alternatives.join(t('lint.or')) });
    default:
      return t(`lint.${p.code}`, q);
  }
}

const fromCompile = (d) => ({
  from: d.from,
  to: d.to,
  severity: d.severity,
  source: t('lint.sourceCompile'),
  message: d.message,
  markClass: 'cm-lint-compile',
  renderMessage: () =>
    h('div.cm-diag-body', h('div.cm-diag-message', d.message), d.context ? h('pre.cm-diag-context', d.context) : null),
});

function fromLocal(p, { readOnly }) {
  const message = lintMessage(p);
  const actions = [];
  if (p.code === 'package-missing' && !readOnly) {
    const pkg = p.params.pkg;
    actions.push({
      name: t('lint.addPackage', { pkg }),
      apply(view) {
        const ins = usepackageInsertion(view.state.doc.toString(), pkg);
        if (ins) view.dispatch({ changes: { from: ins.pos, insert: ins.insert }, userEvent: 'input' });
      },
    });
  }
  return {
    from: p.from,
    to: p.to,
    severity: p.severity === 'info' ? 'hint' : p.severity,
    source: t('lint.sourceQuick'),
    message,
    actions,
  };
}

/**
 * `getContext()` -> { path, lint (czy szybkie sprawdzanie), project, readOnly } albo null.
 * Szybkie sprawdzanie tylko w plikach .tex/.ltx; diagnostyki z kompilacji — w każdym pliku.
 */
function source(getContext) {
  return (view) => {
    const { state } = view;
    const compile = state.field(compileDiagnosticsField);
    const out = compile.filter((d) => d.to <= state.doc.length).map(fromCompile);
    const ctx = getContext?.();
    if (!ctx?.lint || !/\.(tex|ltx)$/i.test(ctx.path || '')) return out;
    const errorLines = new Set(compile.filter((d) => d.severity === 'error').map((d) => state.doc.lineAt(d.from).number));
    const editing = state.field(editingLine, false);
    let problems;
    try {
      problems = lintLatex(state.doc.toString(), { path: ctx.path, project: ctx.project, structure: structureOf(state.doc) });
    } catch (err) {
      console.error('webtex lint', err);
      return out;
    }
    for (const p of problems) {
      const line = state.doc.lineAt(p.from).number;
      if (line === editing) continue;
      // Ten sam błąd LaTeX już zgłosił w tej linii — jedno podkreślenie wystarczy.
      if (p.severity === 'error' && errorLines.has(line)) continue;
      out.push(fromLocal(p, ctx));
    }
    return out;
  };
}

// Tło linii z błędem albo ostrzeżeniem (błąd wygrywa).
const problemLines = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = this.build(view.state);
    }
    update(u) {
      if (u.docChanged || u.transactions.some((tr) => tr.effects.some((e) => e.is(setDiagnosticsEffect)))) {
        this.decorations = this.build(u.state);
      }
    }
    build(state) {
      const lines = new Map();
      forEachDiagnostic(state, (d, from) => {
        if (d.severity !== 'error' && d.severity !== 'warning') return;
        const at = state.doc.lineAt(Math.min(from, state.doc.length)).from;
        if (lines.get(at) !== 'error') lines.set(at, d.severity);
      });
      return Decoration.set(
        [...lines].map(([at, sev]) => Decoration.line({ class: `cm-problemLine cm-problemLine-${sev}` }).range(at)),
        true,
      );
    }
  },
  { decorations: (v) => v.decorations },
);

const theme = EditorView.theme({
  '.cm-lintRange-error': { backgroundImage: 'none', textDecoration: 'underline wavy var(--syn-error)', textDecorationSkipInk: 'none', textUnderlineOffset: '3px' },
  '.cm-lintRange-warning': { backgroundImage: 'none', textDecoration: 'underline wavy var(--warning, #c98a00)', textDecorationSkipInk: 'none', textUnderlineOffset: '3px' },
  '.cm-lintRange-info': { backgroundImage: 'none', textDecoration: 'underline dotted var(--info, #3b82f6)', textUnderlineOffset: '3px' },
  '.cm-lintRange-hint': { backgroundImage: 'none', textDecoration: 'underline dotted var(--cm-gutter-text)', textUnderlineOffset: '3px' },
  '.cm-problemLine-error': { backgroundColor: 'var(--cm-problem-error-bg)' },
  '.cm-problemLine-warning': { backgroundColor: 'var(--cm-problem-warning-bg)' },
  '.cm-tooltip-lint': { padding: '0', maxWidth: '520px' },
  '.cm-diagnostic': { padding: '6px 10px', whiteSpace: 'normal' },
  '.cm-diagnostic-error': { borderLeft: '4px solid var(--syn-error)' },
  '.cm-diagnostic-warning': { borderLeft: '4px solid var(--warning, #c98a00)' },
  '.cm-diagnostic-info': { borderLeft: '4px solid var(--info, #3b82f6)' },
  '.cm-diagnostic-hint': { borderLeft: '4px solid var(--cm-gutter-text)' },
  '.cm-diagnosticSource': { fontSize: '0.8em', opacity: 0.7, marginTop: '2px' },
  '.cm-diagnosticAction': {
    backgroundColor: 'var(--cm-accent)',
    color: 'var(--cm-accent-contrast)',
    borderRadius: '4px',
    padding: '1px 8px',
    marginLeft: '0',
    marginTop: '4px',
  },
  '.cm-diag-context': {
    margin: '4px 0 0',
    padding: '4px 6px',
    maxHeight: '9em',
    overflow: 'auto',
    fontSize: '0.85em',
    background: 'var(--cm-active-line)',
    borderRadius: '4px',
    whiteSpace: 'pre-wrap',
  },
  '.cm-gutter-lint': { width: '1.1em' },
  '.cm-gutter-lint .cm-gutterElement': { padding: '0 0 0 2px' },
});

/** Rozszerzenie diagnostyk dla edytora. */
export function diagnosticsExtension({ getContext }) {
  return [
    compileDiagnosticsField,
    editingLine,
    linter(source(getContext), {
      delay: 500,
      needsRefresh: (u) =>
        u.transactions.some((tr) => tr.effects.some((e) => e.is(setCompileDiagnosticsEffect) || e.is(refreshEffect))) ||
        u.startState.field(editingLine, false) !== u.state.field(editingLine, false),
    }),
    lintGutter({ hoverTime: 200 }),
    problemLines,
    keymap.of([
      { key: 'F8', run: nextDiagnostic, preventDefault: true },
      { key: 'Shift-F8', run: previousDiagnostic, preventDefault: true },
    ]),
    theme,
  ];
}

/** Wymusza ponowne sprawdzenie (np. po zmianie ustawień albo innego pliku projektu). */
export function refreshDiagnostics(view) {
  view.dispatch({ effects: refreshEffect.of(null) });
}

/** Liczba wyświetlanych problemów: { error, warning, info }. */
export function diagnosticCounts(state) {
  const c = { error: 0, warning: 0, info: 0 };
  forEachDiagnostic(state, (d) => {
    c[d.severity === 'hint' ? 'info' : d.severity]++;
  });
  return c;
}

export { nextDiagnostic, previousDiagnostic };
