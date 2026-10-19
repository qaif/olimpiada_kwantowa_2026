// Document outline built from sectioning commands of the current file.
import { h } from '../dom.js';
import { t } from '../i18n.js';

const LEVELS = { part: 0, chapter: 1, section: 2, subsection: 3, subsubsection: 4, paragraph: 5, frametitle: 3 };
const RE = /^[^%\n]*?\\(part|chapter|section|subsection|subsubsection|paragraph|frametitle)\*?\s*(?:\[[^\]]*\])?\s*\{([^\n]*)/gm;

export function parseOutline(text) {
  const out = [];
  for (const m of text.matchAll(RE)) {
    // Read the balanced title.
    let depth = 1;
    let title = '';
    for (const ch of m[2]) {
      if (ch === '{') depth++;
      else if (ch === '}' && --depth === 0) break;
      title += ch;
    }
    const line = text.slice(0, m.index).split('\n').length;
    out.push({ level: LEVELS[m[1]], title: title.replace(/\\\w+\s*/g, '').replace(/[{}]/g, '').trim() || m[1], line });
  }
  return out;
}

export class Outline {
  constructor(container, { onJump }) {
    this.el = h('div.outline');
    container.append(this.el);
    this.onJump = onJump;
    this.items = [];
    this.activeLine = 0;
  }

  update(text) {
    this.items = text == null ? [] : parseOutline(text);
    this.render();
  }

  setCursorLine(line) {
    this.activeLine = line;
    const prevActive = this.el.querySelector('.active');
    const idx = this.items.findLastIndex((i) => i.line <= line);
    const next = this.el.children[idx];
    if (prevActive !== next) {
      prevActive?.classList.remove('active');
      next?.classList?.add('active');
    }
  }

  render() {
    if (!this.items.length) {
      this.el.replaceChildren(h('div.outline-empty', t('editor.noOutline')));
      return;
    }
    const min = Math.min(...this.items.map((i) => i.level));
    this.el.replaceChildren(
      ...this.items.map((i) =>
        h(
          'button.outline-item',
          { style: { paddingLeft: `${10 + (i.level - min) * 12}px` }, title: i.title, onclick: () => this.onJump(i.line) },
          i.title,
        ),
      ),
    );
    this.setCursorLine(this.activeLine);
  }
}
