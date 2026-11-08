// Podgląd wzorów (KaTeX): źródło wzoru z obszaru matematycznego dokumentu i makra — proste
// \newcommand/\def/\DeclareMathOperator z projektu oraz przybliżenia poleceń pakietu physics,
// których KaTeX nie zna. Czyste funkcje (makra-funkcje dostają kontekst KaTeX-a dopiero przy składaniu).
import { stripComments } from './scan.js';
import { matchGroup } from './structure.js';

// Otoczenia amsmath, których KaTeX nie składa samodzielnie: odpowiednik wewnątrz wzoru.
const ENV_WRAP = {
  align: 'aligned', 'align*': 'aligned', flalign: 'aligned', 'flalign*': 'aligned',
  gather: 'gathered', 'gather*': 'gathered', multline: 'gathered', 'multline*': 'gathered',
  alignat: 'alignedat', 'alignat*': 'alignedat',
};

/**
 * Źródło do złożenia dla obszaru z analyzeStructure: { source, display }. Bez etykiet, \nonumber
 * i komentarzy; otoczenia align/gather/… zamienione na aligned/gathered (KaTeX).
 */
export function mathSource(region, text) {
  let src = stripComments(text.slice(region.innerFrom, region.innerTo));
  src = src.replace(/\\label\s*\{[^{}]*\}/g, '').replace(/\\(?:nonumber|notag)\b/g, '');
  const env = region.env;
  if (env && ENV_WRAP[env]) {
    const inner = ENV_WRAP[env];
    if (inner === 'alignedat') {
      const m = /^\s*\{(\d+)\}/.exec(src);
      src = `\\begin{alignedat}{${m ? m[1] : 1}}${m ? src.slice(m[0].length) : src}\\end{alignedat}`;
    } else {
      src = `\\begin{${inner}}${src}\\end{${inner}}`;
    }
  } else if (env === 'eqnarray' || env === 'eqnarray*') {
    src = `\\begin{array}{rcl}${src}\\end{array}`;
  }
  return { source: src.trim(), display: region.display };
}

// ---------- makra z projektu ----------

const NAME = String.raw`(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))`;

/**
 * Makra z tekstów plików projektu: { '\\R': '\\mathbb{R}', … } w formacie opcji `macros` KaTeX-a.
 * Pomija definicje z argumentem opcjonalnym (KaTeX ich nie obsługuje) i zbyt długie.
 */
export function collectMacros(texts) {
  const out = {};
  const set = (name, body, provide = false) => {
    if (!name || body == null || body.length > 2000) return;
    const key = `\\${name}`;
    if (provide && key in out) return;
    out[key] = body;
  };
  for (const raw of texts) {
    const text = stripComments(raw);
    const body = (at) => {
      let k = at;
      while (k < text.length && /\s/.test(text[k])) k++;
      if (text[k] !== '{') return null;
      const end = matchGroup(text, k);
      return end < 0 ? null : text.slice(k + 1, end - 1);
    };
    for (const m of text.matchAll(new RegExp(String.raw`\\(newcommand|renewcommand|providecommand)\*?\s*${NAME}\s*(?:\[\s*(\d)\s*\])?\s*(\[)?`, 'g'))) {
      if (m[5]) continue; // argument opcjonalny
      set(m[2] || m[3], body(m.index + m[0].length), m[1] === 'providecommand');
    }
    for (const m of text.matchAll(/\\[gex]?def\s*\\([a-zA-Z@]+)((?:\s*#\d)*)\s*(?=\{)/g)) set(m[1], body(m.index + m[0].length));
    for (const m of text.matchAll(new RegExp(String.raw`\\DeclareMathOperator(\*?)\s*${NAME}\s*`, 'g'))) {
      const b = body(m.index + m[0].length);
      if (b != null) set(m[2] || m[3], `\\operatorname${m[1]}{${b}}`);
    }
    for (const m of text.matchAll(new RegExp(String.raw`\\DeclarePairedDelimiter\s*${NAME}\s*`, 'g'))) {
      const at = m.index + m[0].length;
      const left = body(at);
      if (left == null) continue;
      const leftEnd = matchGroup(text, text.indexOf('{', at));
      const right = body(leftEnd);
      if (right != null) set(m[1] || m[2], `\\left${left.trim()} #1 \\right${right.trim()}`);
    }
    for (const m of text.matchAll(new RegExp(String.raw`\\(?:New|Renew|Provide|Declare)DocumentCommand\s*${NAME}\s*\{([^{}]*)\}\s*`, 'g'))) {
      if (!/^\s*(?:m\s*)*$/.test(m[3])) continue; // tylko argumenty obowiązkowe
      set(m[1] || m[2], body(m.index + m[0].length));
    }
  }
  return out;
}

// ---------- physics ----------

/** Tokeny argumentu KaTeX-a (odwrócone, jak na stosie) z powrotem jako tekst. */
export function tokensToString(tokens) {
  return [...tokens]
    .reverse()
    .map((t) => (/^\\[a-zA-Z@]+$/.test(t.text) ? `${t.text} ` : t.text))
    .join('');
}

/** Treść w nawiasach otwartych tokenem `open` (już na wejściu) do pasującego `close`. */
function gatherUntil(ctx, open, close) {
  ctx.popToken(); // sam nawias otwierający
  const out = [];
  let depth = 0;
  for (let k = 0; k < 5000; k++) {
    const tok = ctx.popToken();
    if (!tok || tok.text === 'EOF') break;
    if (tok.text === open && open !== close) depth++;
    else if (tok.text === close) {
      if (depth === 0) break;
      depth--;
    }
    out.push(tok);
  }
  return tokensToString(out.reverse());
}

const next = (ctx) => {
  ctx.consumeSpaces();
  return ctx.future().text;
};
const arg = (ctx) => tokensToString(ctx.consumeArgs(1)[0]);
const optionalOrder = (ctx) => (next(ctx) === '[' ? gatherUntil(ctx, '[', ']') : '');

// Pochodne: \dv{x}, \dv{f}{x}, \dv[n]{f}{x}; \pdv{f}{x}{y} — mieszana.
const derivative = (d) => (ctx) => {
  const order = optionalOrder(ctx);
  const sup = order ? `^{${order}}` : '';
  const a = arg(ctx);
  if (next(ctx) !== '{') return `\\frac{${d}${sup}}{${d} ${a}${sup}}`;
  const b = arg(ctx);
  if (d === '\\partial' && next(ctx) === '{') {
    const c = arg(ctx);
    return `\\frac{${d}^{2} ${a}}{${d} ${b}\\, ${d} ${c}}`;
  }
  return `\\frac{${d}${sup} ${a}}{${d} ${b}${sup}}`;
};

// ⟨A⟩ albo ⟨ψ|A|ψ⟩
const expval = (ctx) => {
  const a = arg(ctx);
  if (next(ctx) !== '{') return `\\left\\langle ${a} \\right\\rangle`;
  const psi = arg(ctx);
  return `\\left\\langle ${psi} \\middle| ${a} \\middle| ${psi} \\right\\rangle`;
};

// |a⟩⟨a| albo |a⟩⟨b|
const outer = (ctx) => {
  const a = arg(ctx);
  const b = next(ctx) === '{' ? arg(ctx) : a;
  return `\\left| ${a} \\right\\rangle\\!\\left\\langle ${b} \\right|`;
};

const MATRIX = { '(': ['pmatrix', ')'], '[': ['bmatrix', ']'], '|': ['vmatrix', '|'], '{': ['matrix', '}'] };
const mqty = (env) => (ctx) => {
  const open = next(ctx);
  const spec = MATRIX[open];
  if (!spec) return '';
  const inner = open === '{' ? arg(ctx) : gatherUntil(ctx, open, spec[1]);
  return `\\begin{${env || spec[0]}}${inner}\\end{${env || spec[0]}}`;
};

/** Przybliżenia poleceń pakietu physics (KaTeX ich nie ma). */
export const PHYSICS_MACROS = {
  '\\abs': '\\left\\lvert #1 \\right\\rvert',
  '\\norm': '\\left\\lVert #1 \\right\\rVert',
  '\\expval': expval,
  '\\ev': expval,
  '\\mel': '\\left\\langle #1 \\middle| #2 \\middle| #3 \\right\\rangle',
  '\\matrixel': '\\left\\langle #1 \\middle| #2 \\middle| #3 \\right\\rangle',
  '\\dyad': outer,
  '\\op': outer,
  '\\outerproduct': outer,
  '\\ketbra': '\\left| #1 \\right\\rangle\\!\\left\\langle #2 \\right|',
  '\\comm': '\\left[ #1, #2 \\right]',
  '\\commutator': '\\left[ #1, #2 \\right]',
  '\\acomm': '\\left\\{ #1, #2 \\right\\}',
  '\\anticommutator': '\\left\\{ #1, #2 \\right\\}',
  '\\pb': '\\left\\{ #1, #2 \\right\\}',
  '\\dv': derivative('\\mathrm{d}'),
  '\\pdv': derivative('\\partial'),
  '\\fdv': derivative('\\delta'),
  '\\dd': (ctx) => (next(ctx) === '{' ? `\\mathrm{d}${arg(ctx)}` : '\\mathrm{d}'),
  '\\qty': (ctx) => (next(ctx) === '{' ? `\\left\\{ ${arg(ctx)} \\right\\}` : ''),
  '\\order': '\\mathcal{O}\\left( #1 \\right)',
  '\\tr': '\\operatorname{tr}',
  '\\Tr': '\\operatorname{Tr}',
  '\\rank': '\\operatorname{rank}',
  '\\vb': '\\mathbf{#1}',
  '\\va': '\\vec{#1}',
  '\\vu': '\\hat{\\mathbf{#1}}',
  '\\grad': '\\nabla',
  '\\curl': '\\nabla\\times',
  '\\divergence': '\\nabla\\cdot',
  '\\laplacian': '\\nabla^2',
  '\\mqty': mqty(null),
  '\\pmqty': mqty('pmatrix'),
  '\\bmqty': mqty('bmatrix'),
  '\\vmqty': mqty('vmatrix'),
  '\\dmqty': mqty('vmatrix'),
  '\\xspace': '',
};

/** Makra dla KaTeX-a: physics (gdy projekt go ładuje), potem definicje z projektu (mają pierwszeństwo). */
export function previewMacros({ texts = [], packages = [] } = {}) {
  return { ...(packages.includes('physics') ? PHYSICS_MACROS : { '\\xspace': '' }), ...collectMacros(texts) };
}
