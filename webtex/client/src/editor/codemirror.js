// Source editor factory: CodeMirror 6 bound to a Y.Text (real-time collaboration via y-codemirror.next).
import './codemirror.css';
import { Compartment, EditorSelection, EditorState } from '@codemirror/state';
import {
  EditorView,
  crosshairCursor,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from '@codemirror/view';
import {
  HighlightStyle,
  StreamLanguage,
  bracketMatching,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
} from '@codemirror/language';
import { defaultKeymap, indentWithTab, toggleComment } from '@codemirror/commands';
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search';
import { acceptCompletion, autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete';
import { stex } from '@codemirror/legacy-modes/mode/stex';
import { tags as t } from '@lezer/highlight';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import { bibtexLanguage } from './bibtex.js';
import {
  completionPhrases,
  completionUsageTracker,
  insertEnvironmentEnd,
  latexCompletionSource,
  mergeSymbols,
  scanFile,
  scanSymbols,
} from './completions.js';

export { mergeSymbols, scanFile, scanSymbols };

const latexLanguage = StreamLanguage.define(stex);

function languageFor(fileName = '') {
  const ext = fileName.toLowerCase().split('.').pop();
  if (['tex', 'sty', 'cls', 'ltx', 'dtx', 'tikz', 'latex', 'bbl'].includes(ext)) return 'latex';
  if (ext === 'bib') return 'bibtex';
  return 'plain';
}

// Colours come from CSS variables (see codemirror.css), so the app's light/dark switch just works.
const highlightStyle = HighlightStyle.define([
  { tag: [t.keyword, t.tagName], color: 'var(--syn-command)' },
  { tag: [t.atom, t.bool, t.special(t.variableName)], color: 'var(--syn-atom)' },
  { tag: [t.number], color: 'var(--syn-number)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--syn-string)' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: 'var(--syn-comment)', fontStyle: 'italic' },
  { tag: [t.bracket, t.paren, t.squareBracket, t.brace], color: 'var(--syn-bracket)' },
  { tag: [t.variableName, t.labelName], color: 'var(--syn-variable)' },
  { tag: [t.propertyName], color: 'var(--syn-property)' },
  { tag: [t.typeName, t.className], color: 'var(--syn-type)' },
  { tag: [t.operator, t.punctuation], color: 'var(--syn-operator)' },
  { tag: [t.heading], color: 'var(--syn-command)', fontWeight: 'bold' },
  { tag: [t.invalid], color: 'var(--syn-error)' },
  { tag: [t.meta], color: 'var(--syn-meta)' },
]);

const baseTheme = EditorView.theme({
  '&': {
    height: '100%',
    color: 'var(--cm-text)',
    backgroundColor: 'var(--cm-bg)',
  },
  '.cm-scroller': {
    fontFamily: 'var(--cm-font, ui-monospace, "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace)',
    lineHeight: '1.55',
  },
  '.cm-content': { caretColor: 'var(--cm-caret)', padding: '6px 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--cm-caret)', borderLeftWidth: '2px' },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection':
    { backgroundColor: 'var(--cm-selection)' },
  '.cm-activeLine': { backgroundColor: 'var(--cm-active-line)' },
  '.cm-gutters': {
    backgroundColor: 'var(--cm-gutter-bg)',
    color: 'var(--cm-gutter-text)',
    borderRight: '1px solid var(--cm-border)',
  },
  '.cm-activeLineGutter': { backgroundColor: 'var(--cm-active-line)', color: 'var(--cm-text)' },
  '.cm-lineNumbers .cm-gutterElement': { padding: '0 8px 0 12px', minWidth: '32px' },
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--cm-active-line)',
    border: '1px solid var(--cm-border)',
    color: 'var(--cm-gutter-text)',
  },
  '&.cm-focused .cm-matchingBracket': {
    backgroundColor: 'var(--cm-bracket-match)',
    outline: '1px solid var(--cm-bracket-outline)',
  },
  '&.cm-focused .cm-nonmatchingBracket': { backgroundColor: 'var(--cm-bracket-bad)' },
  '.cm-selectionMatch': { backgroundColor: 'var(--cm-selection-match)' },
  '.cm-searchMatch': { backgroundColor: 'var(--cm-search-match)', outline: '1px solid var(--cm-search-outline)' },
  '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'var(--cm-search-selected)' },
  '.cm-panels': { backgroundColor: 'var(--cm-panel-bg)', color: 'var(--cm-text)' },
  '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--cm-border)' },
  '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--cm-border)' },
  '.cm-tooltip': {
    backgroundColor: 'var(--cm-panel-bg)',
    color: 'var(--cm-text)',
    border: '1px solid var(--cm-border)',
    borderRadius: '6px',
    boxShadow: '0 6px 20px rgba(0,0,0,.18)',
    overflow: 'hidden',
  },
  '.cm-tooltip-autocomplete > ul': { fontFamily: 'var(--cm-font, ui-monospace, Consolas, monospace)', maxHeight: '18em' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': {
    backgroundColor: 'var(--cm-accent)',
    color: 'var(--cm-accent-contrast)',
  },
  '.cm-completionDetail': { color: 'var(--cm-gutter-text)', fontStyle: 'normal', marginLeft: '1em' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected] .cm-completionDetail': { color: 'inherit', opacity: 0.8 },
  '.cm-completionMatchedText': { textDecoration: 'none', fontWeight: '700' },
  '.cm-snippetField': { backgroundColor: 'var(--cm-selection-match)' },
  // The info panel is a child of the list's tooltip, placed beside it: it must not be clipped.
  '.cm-tooltip.cm-tooltip-autocomplete': { overflow: 'visible' },
  '.cm-tooltip.cm-tooltip-autocomplete > ul': { borderRadius: '6px' },
  '.cm-latex-completions .cm-completionDetail': {
    display: 'inline-block',
    maxWidth: '24em',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    verticalAlign: 'bottom',
  },
  '.cm-tooltip.cm-completionInfo': { padding: '6px 10px', maxWidth: '360px' },
  '.cm-latex-info': { display: 'grid', gap: '4px', fontSize: '0.92em', lineHeight: '1.4' },
  '.cm-latex-info-syntax': {
    fontFamily: 'var(--cm-font, ui-monospace, Consolas, monospace)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  '.cm-latex-field': {
    backgroundColor: 'var(--cm-selection-match)',
    borderRadius: '3px',
    padding: '0 2px',
    fontStyle: 'italic',
  },
  '.cm-latex-info-meta': { color: 'var(--cm-gutter-text)' },
  '.cm-latex-info-warn': { color: 'var(--syn-error)' },
});

const fontTheme = (px) =>
  EditorView.theme({
    '&': { fontSize: `${px}px` },
  });

const readOnlyExt = (ro) => [EditorState.readOnly.of(ro), EditorView.editable.of(!ro)];

/** Wraps every selection range in before/after (with an empty range, the cursor lands in between). */
function wrapSelectionCommand(view, before, after) {
  if (view.state.readOnly) return false;
  const tr = view.state.changeByRange((range) => {
    const text = view.state.sliceDoc(range.from, range.to);
    // Toggle off if the selection is already wrapped exactly.
    const outerFrom = range.from - before.length;
    const outerTo = range.to + after.length;
    if (
      !range.empty &&
      outerFrom >= 0 &&
      view.state.sliceDoc(outerFrom, range.from) === before &&
      view.state.sliceDoc(range.to, outerTo) === after
    ) {
      return {
        changes: [
          { from: outerFrom, to: range.from, insert: '' },
          { from: range.to, to: outerTo, insert: '' },
        ],
        range: EditorSelection.range(outerFrom, range.to - before.length),
      };
    }
    return {
      changes: [
        { from: range.from, insert: before },
        { from: range.to, insert: after },
      ],
      range: range.empty
        ? EditorSelection.cursor(range.from + before.length)
        : EditorSelection.range(range.from + before.length, range.to + before.length),
    };
  });
  view.dispatch(view.state.update(tr, { scrollIntoView: true, userEvent: 'input' }));
  view.focus();
  return true;
}

export function createEditor({
  parent,
  ytext,
  awareness,
  undoManager,
  fileName = '',
  readOnly = false,
  fontSize = 14,
  getSymbols,
  onCompile,
  onForwardSearch,
  onCursorLine,
  onDocChanged,
}) {
  const readOnlyComp = new Compartment();
  const fontComp = new Compartment();
  const lang = languageFor(fileName);

  const cursorLine = (state) => state.doc.lineAt(state.selection.main.head).number;
  const run = (fn) => () => {
    fn?.();
    return true;
  };

  const appKeymap = keymap.of([
    { key: 'Mod-s', run: run(() => onCompile?.()), preventDefault: true },
    { key: 'Mod-Enter', run: run(() => onCompile?.()), preventDefault: true },
    { key: 'Ctrl-Alt-ArrowRight', run: (v) => (onForwardSearch?.(cursorLine(v.state)), true), preventDefault: true },
    { key: 'Mod-b', run: (v) => wrapSelectionCommand(v, '\\textbf{', '}'), preventDefault: true },
    { key: 'Mod-i', run: (v) => wrapSelectionCommand(v, '\\textit{', '}'), preventDefault: true },
    { key: 'Mod-/', run: toggleComment, preventDefault: true },
  ]);

  let cursorTimer = null;
  let lastLine = null;
  const listeners = EditorView.updateListener.of((update) => {
    if (update.docChanged) onDocChanged?.();
    if ((update.selectionSet || update.docChanged) && onCursorLine) {
      clearTimeout(cursorTimer);
      cursorTimer = setTimeout(() => {
        const line = cursorLine(view.state);
        if (line !== lastLine) {
          lastLine = line;
          onCursorLine(line);
        }
      }, 150);
    }
  });

  const languageExt = [];
  if (lang === 'latex') {
    languageExt.push(
      latexLanguage,
      autocompletion({
        override: [latexCompletionSource(getSymbols)],
        icons: false,
        activateOnTyping: true,
        tooltipClass: () => 'cm-latex-completions',
      }),
      completionUsageTracker,
      completionPhrases(),
    );
  } else if (lang === 'bibtex') {
    languageExt.push(bibtexLanguage, autocompletion());
  }

  const state = EditorState.create({
    doc: ytext.toString(),
    extensions: [
      appKeymap,
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightSpecialChars(),
      drawSelection(),
      dropCursor(),
      EditorState.allowMultipleSelections.of(true),
      indentOnInput(),
      indentUnit.of('  '),
      syntaxHighlighting(highlightStyle),
      bracketMatching(),
      closeBrackets(),
      rectangularSelection(),
      crosshairCursor(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      search({ top: true }),
      EditorView.lineWrapping,
      EditorView.contentAttributes.of({ spellcheck: 'true', autocorrect: 'off', autocapitalize: 'off' }),
      keymap.of([
        ...closeBracketsKeymap,
        ...yUndoManagerKeymap,
        ...searchKeymap,
        ...completionKeymap,
        // Tab accepts the selected completion (Enter works too); otherwise it indents.
        { key: 'Tab', run: acceptCompletion },
        indentWithTab,
        ...(lang === 'latex' ? [{ key: 'Enter', run: insertEnvironmentEnd }] : []),
        ...defaultKeymap,
      ]),
      ...languageExt,
      baseTheme,
      fontComp.of(fontTheme(fontSize)),
      readOnlyComp.of(readOnlyExt(readOnly)),
      yCollab(ytext, awareness, { undoManager }),
      listeners,
    ],
  });

  const view = new EditorView({ state, parent });

  return {
    view,
    destroy() {
      clearTimeout(cursorTimer);
      view.destroy();
    },
    focus() {
      view.focus();
    },
    setFontSize(px) {
      view.dispatch({ effects: fontComp.reconfigure(fontTheme(px)) });
    },
    setReadOnly(ro) {
      view.dispatch({ effects: readOnlyComp.reconfigure(readOnlyExt(ro)) });
    },
    goToLine(line, col = 0) {
      const doc = view.state.doc;
      const l = doc.line(Math.min(Math.max(1, line | 0), doc.lines));
      const pos = Math.min(l.from + Math.max(0, col | 0), l.to);
      view.dispatch({
        selection: { anchor: pos },
        effects: EditorView.scrollIntoView(pos, { y: 'center' }),
      });
      view.focus();
    },
    wrapSelection(before, after) {
      return wrapSelectionCommand(view, before, after);
    },
    insertText(text) {
      if (view.state.readOnly) return;
      view.dispatch(view.state.replaceSelection(text), { scrollIntoView: true, userEvent: 'input' });
      view.focus();
    },
    getCursorLine() {
      return cursorLine(view.state);
    },
    /** Main selection as character offsets (same offsets as the shared Y.Text) and its text. */
    getSelection() {
      const { from, to } = view.state.selection.main;
      return { from, to, text: view.state.sliceDoc(from, to) };
    },
    sliceDoc(from, to) {
      return view.state.sliceDoc(from, to);
    },
    /** Replaces [from, to) with text as a normal (collaborative, undoable) edit and selects it. */
    replaceRange(from, to, text, { select = true } = {}) {
      if (view.state.readOnly) return false;
      const len = view.state.doc.length;
      from = Math.max(0, Math.min(from, len));
      to = Math.max(from, Math.min(to, len));
      view.dispatch({
        changes: { from, to, insert: text },
        selection: select ? EditorSelection.range(from, from + text.length) : EditorSelection.cursor(from + text.length),
        scrollIntoView: true,
        userEvent: 'input',
      });
      view.focus();
      return true;
    },
    /** Inserts text at the cursor (end of the selection) without replacing the selection. */
    insertAtCursor(text) {
      const pos = view.state.selection.main.to;
      return this.replaceRange(pos, pos, text, { select: false });
    },
  };
}
