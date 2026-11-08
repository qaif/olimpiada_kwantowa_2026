// Sprawdzanie pisowni przeglądarki w kodzie LaTeX: atrybut lang treści (np. pl, więc słownik polski)
// i spellcheck=false na poleceniach, wzorach i argumentach-nazwach (\label{…}, \usepackage{…}),
// żeby czerwone podkreślenia trafiały tylko w zwykły tekst.
import { Decoration, EditorView, ViewPlugin } from '@codemirror/view';
import { noSpellRanges } from './latex/prose.js';
import { structureOf } from './docStructure.js';

const noSpell = Decoration.mark({ attributes: { spellcheck: 'false' } });

const plugin = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = this.build(view);
    }
    update(u) {
      if (u.docChanged || u.viewportChanged) this.decorations = this.build(u.view);
    }
    build(view) {
      const { math } = structureOf(view.state.doc);
      const ranges = [];
      for (const { from, to } of view.visibleRanges) {
        for (const [a, b] of noSpellRanges(view.state.sliceDoc(from, to), from, math)) if (b > a) ranges.push(noSpell.range(a, b));
      }
      return Decoration.set(ranges, true);
    }
  },
  { decorations: (v) => v.decorations },
);

/** Atrybut lang dla treści edytora (dla każdego pliku). */
export const contentLanguage = (lang) => EditorView.contentAttributes.of(lang ? { lang } : {});

/** Wyłączanie sprawdzania pisowni w kodzie (pliki LaTeX). */
export const latexSpellcheck = plugin;
