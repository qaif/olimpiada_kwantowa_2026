// Podgląd wzorów w dymku: po najechaniu myszą na $…$, \(…\), \[…\], $$…$$ albo otoczenie
// equation/align/… oraz gdy kursor stoi we wzorze. Składa KaTeX (w paczce aplikacji, bez sieci),
// z makrami z projektu (latex/macros.js). KaTeX ładuje się dopiero przy pierwszym podglądzie.
import { StateField } from '@codemirror/state';
import { EditorView, hoverTooltip, showTooltip } from '@codemirror/view';
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { mathSource } from './latex/macros.js';
import { mathRegionAt } from './latex/structure.js';
import { structureOf } from './docStructure.js';

let katexLib = null;
let katexLoading = null;
function loadKatex() {
  katexLoading ??= Promise.all([import('katex'), import('katex/dist/katex.min.css')]).then(([m]) => {
    katexLib = m.default;
    return katexLib;
  });
  return katexLoading;
}

/** Obszar matematyczny pod pozycją (z ogranicznikami) albo null; pomija puste wzory. */
function regionAt(state, pos) {
  const r = mathRegionAt(structureOf(state.doc).math, pos);
  if (!r || !state.sliceDoc(r.innerFrom, r.innerTo).trim()) return null;
  return r;
}

/** Składa wzór do `el` (od razu, jeśli KaTeX jest już wczytany). */
function renderInto(el, state, region, getMacros) {
  const { source, display } = mathSource(region, state.doc.toString());
  const draw = (katex) => {
    try {
      el.classList.remove('cm-math-preview-error');
      katex.render(source, el, {
        displayMode: display,
        throwOnError: true,
        strict: 'ignore',
        trust: false,
        maxExpand: 500,
        maxSize: 50,
        output: 'html',
        macros: { ...(getMacros?.() || {}) },
      });
    } catch (err) {
      el.classList.add('cm-math-preview-error');
      // KaTeX podkreśla miejsce błędu znakami łączącymi (U+0332) — w dymku tylko przeszkadzają.
      const msg = String(err?.message || err).replace(/^KaTeX parse error:\s*/, '').replace(/̲/g, '');
      el.replaceChildren(h('div.cm-math-preview-title', t('math.previewError')), h('div.cm-math-preview-msg', msg));
    }
  };
  if (katexLib) draw(katexLib);
  else {
    el.replaceChildren(h('span.muted', t('math.loading')));
    loadKatex().then(draw, () => el.replaceChildren(h('span', t('math.previewError'))));
  }
}

function tooltipFor(region, getMacros, live) {
  return {
    pos: region.from,
    end: region.to,
    above: true,
    arrow: false,
    create(view) {
      const body = h('div.cm-math-preview-body');
      const dom = h('div.cm-math-preview', body);
      const draw = () => {
        const r = live ? regionAt(view.state, view.state.selection.main.head) : region;
        if (r) renderInto(body, view.state, r, getMacros);
      };
      draw();
      return {
        dom,
        update(u) {
          if (u.docChanged && live) draw();
        },
      };
    },
  };
}

/** Rozszerzenie podglądu; `getMacros()` zwraca makra KaTeX-a dla projektu. */
export function mathPreviewExtension({ getMacros }) {
  // Dymek przy kursorze: ten sam obiekt, dopóki kursor jest w tym samym wzorze (bez migania przy pisaniu).
  const cursorTooltip = StateField.define({
    create: (state) => compute(state, null),
    update(prev, tr) {
      if (!tr.docChanged && !tr.selection) return prev;
      return compute(tr.state, prev);
    },
    provide: (f) => showTooltip.from(f),
  });
  function compute(state, prev) {
    const sel = state.selection.main;
    if (!sel.empty) return null;
    const r = regionAt(state, sel.head);
    if (!r || sel.head <= r.from) return null;
    if (prev && prev.pos === r.from) return prev;
    return tooltipFor(r, getMacros, true);
  }

  const hover = hoverTooltip(
    (view, pos) => {
      const r = regionAt(view.state, pos);
      if (!r) return null;
      const active = view.state.field(cursorTooltip, false);
      if (active && active.pos === r.from) return null; // ten wzór już ma podgląd przy kursorze
      return tooltipFor(r, getMacros, false);
    },
    { hoverTime: 300 },
  );

  return [
    cursorTooltip,
    hover,
    EditorView.theme({
      '.cm-math-preview': {
        padding: '6px 12px',
        maxWidth: 'min(640px, 80vw)',
        maxHeight: '40vh',
        overflow: 'auto',
        fontSize: '1.1em',
        color: 'var(--cm-text)',
        backgroundColor: 'var(--cm-bg)',
      },
      '.cm-math-preview .katex-display': { margin: '0.3em 0' },
      '.cm-math-preview-error': { fontSize: '0.9em', fontFamily: 'inherit' },
      '.cm-math-preview-title': { fontWeight: '600', color: 'var(--syn-error)' },
      '.cm-math-preview-msg': { fontFamily: 'var(--cm-font, ui-monospace, Consolas, monospace)', whiteSpace: 'pre-wrap', marginTop: '2px' },
    }),
  ];
}

/** Wczytuje KaTeX zawczasu (np. gdy przeglądarka i tak nic nie robi). */
export function preloadMathPreview() {
  loadKatex().catch(() => {});
}
