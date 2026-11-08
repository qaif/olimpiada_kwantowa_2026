// Struktura źródła LaTeX w jednym przebiegu: klamry, środowiska, obszary matematyczne, definicje
// makr, etykiety i odsyłacze, użyte polecenia. Czyste funkcje na zwykłych napisach, offsety odnoszą
// się do oryginalnego tekstu. Korzystają z niej szybkie sprawdzanie (lint.js), podgląd wzorów,
// wyłączanie sprawdzania pisowni w poleceniach i licznik słów.
import { MATH_ENVIRONMENTS } from './catalog.js';

/** Środowiska, których treść TeX czyta dosłownie (nie ma w niej poleceń, klamer ani $). */
export const VERBATIM_ENVIRONMENTS = new Set([
  'verbatim', 'verbatim*', 'Verbatim', 'BVerbatim', 'lstlisting', 'minted', 'comment', 'filecontents', 'filecontents*',
]);

/** Polecenia, których argument w klamrach jest tekstem także wewnątrz wzoru. */
const TEXT_IN_MATH = new Set([
  'text', 'textrm', 'textit', 'textbf', 'textsf', 'texttt', 'textnormal', 'textup', 'textmd', 'textsc', 'emph',
  'mbox', 'hbox', 'intertext', 'shortintertext',
]);

/** Polecenia definiujące makra: ich treść nie jest składana w miejscu definicji. */
const DEFINERS = {
  newcommand: 'cmd', renewcommand: 'cmd', providecommand: 'cmd', DeclareRobustCommand: 'cmd',
  newenvironment: 'env', renewenvironment: 'env',
  NewDocumentCommand: 'xcmd', RenewDocumentCommand: 'xcmd', ProvideDocumentCommand: 'xcmd', DeclareDocumentCommand: 'xcmd',
  NewDocumentEnvironment: 'xenv', RenewDocumentEnvironment: 'xenv', ProvideDocumentEnvironment: 'xenv', DeclareDocumentEnvironment: 'xenv',
  def: 'def', gdef: 'def', edef: 'def', xdef: 'def',
};

/** Polecenia odsyłające do etykiet (\label); argument może być listą po przecinku. */
export const REF_COMMANDS = new Set(['ref', 'eqref', 'pageref', 'autoref', 'nameref', 'cref', 'Cref', 'cpageref', 'Cpageref', 'vref', 'Vref', 'labelcref']);

const isLetter = (ch) => (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch === '@';

/**
 * Zastępuje spacjami komentarze (bez samego znaku %, żeby linia z komentarzem nie udawała pustej),
 * treść \verb, \lstinline, adresów w \url/\href i środowisk dosłownych. Długość i podział na linie
 * zostają, więc offsety pozostają ważne. Zwraca { clean, comments: [[from, to]] }.
 */
export function blankNonCode(text) {
  const out = text.split('');
  const comments = [];
  const n = text.length;
  const blank = (from, to) => {
    for (let k = from; k < to; k++) if (out[k] !== '\n') out[k] = ' ';
  };
  let i = 0;
  while (i < n) {
    const ch = text[i];
    if (ch === '%') {
      let end = text.indexOf('\n', i);
      if (end < 0) end = n;
      comments.push([i, end]);
      blank(i + 1, end);
      i = end;
      continue;
    }
    if (ch !== '\\') {
      i++;
      continue;
    }
    let j = i + 1;
    while (j < n && isLetter(text[j])) j++;
    if (j === i + 1) {
      i += 2; // \%, \{, \\ …
      continue;
    }
    const name = text.slice(i + 1, j);
    if (name === 'verb' || name === 'lstinline') {
      let k = j;
      if (text[k] === '*') k++;
      const delim = text[k];
      if (delim === '{' && name === 'lstinline') {
        const close = text.indexOf('}', k + 1);
        const stop = close < 0 ? n : close;
        blank(k + 1, stop);
        i = stop;
        continue;
      }
      if (delim && delim !== ' ' && delim !== '\n' && !isLetter(delim)) {
        let close = text.indexOf(delim, k + 1);
        const eol = text.indexOf('\n', k + 1);
        if (close < 0 || (eol >= 0 && eol < close)) close = eol < 0 ? n : eol;
        blank(k, Math.min(n, close + 1));
        i = close + 1;
        continue;
      }
    } else if (name === 'url' || name === 'href') {
      let k = j;
      while (text[k] === ' ') k++;
      if (text[k] === '{') {
        const close = text.indexOf('}', k + 1);
        const eol = text.indexOf('\n\n', k + 1);
        if (close >= 0 && (eol < 0 || close < eol)) {
          blank(k + 1, close);
          i = close;
          continue;
        }
      }
    } else if (name === 'begin') {
      const m = /^\s*\{([^{}\n]*)\}/.exec(text.slice(j, j + 80));
      if (m && VERBATIM_ENVIRONMENTS.has(m[1].trim())) {
        const env = m[1].trim();
        const start = j + m[0].length;
        const endTag = new RegExp(`\\\\end\\s*\\{${env.replace(/[*]/g, '\\*')}\\}`, 'g');
        endTag.lastIndex = start;
        const em = endTag.exec(text);
        const stop = em ? em.index : n;
        blank(start, stop);
        i = stop;
        continue;
      }
    }
    i = j;
  }
  return { clean: out.join(''), comments };
}

/** Indeks tuż za klamrą zamykającą grupę otwartą w `open` (tekst bez komentarzy) albo -1. */
export function matchGroup(text, open, limit = text.length) {
  let depth = 0;
  for (let i = open; i < limit; i++) {
    const ch = text[i];
    if (ch === '\\') i++;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) return i + 1;
  }
  return -1;
}

const skipSpaces = (text, i) => {
  while (i < text.length && (text[i] === ' ' || text[i] === '\t' || text[i] === '\n')) i++;
  return i;
};

/** Indeks za [opcjonalnym] argumentem w nawiasach kwadratowych (z zagnieżdżonymi klamrami) albo -1. */
function skipBracket(text, i) {
  let depth = 0;
  for (let k = i + 1; k < text.length && k < i + 2000; k++) {
    const ch = text[k];
    if (ch === '\\') k++;
    else if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === ']' && depth === 0) return k + 1;
  }
  return -1;
}

/** Jedna grupa {…} albo jedno polecenie \nazwa; zwraca indeks za nim albo -1. */
function skipArgument(text, i) {
  i = skipSpaces(text, i);
  if (text[i] === '{') return matchGroup(text, i);
  if (text[i] === '\\') {
    let j = i + 1;
    while (j < text.length && isLetter(text[j])) j++;
    return j === i + 1 ? i + 2 : j;
  }
  return -1;
}

/** Koniec definicji (\newcommand…, \def…) zaczynającej się za nazwą polecenia w `i` albo -1. */
function skipDefinition(text, i, kind) {
  if (text[i] === '*') i++;
  let k = skipArgument(text, i); // nazwa: {\foo}, \foo albo {otoczenie}
  if (k < 0) return -1;
  if (kind === 'def') {
    const brace = text.indexOf('{', k);
    if (brace < 0 || brace - k > 120) return -1;
    return matchGroup(text, brace);
  }
  const groups = kind === 'cmd' ? 1 : kind === 'env' ? 2 : kind === 'xcmd' ? 2 : 3;
  if (kind === 'cmd' || kind === 'env') {
    for (let b = 0; b < 2; b++) {
      const at = skipSpaces(text, k);
      if (text[at] !== '[') break;
      k = skipBracket(text, at);
      if (k < 0) return -1;
    }
  }
  for (let g = 0; g < groups; g++) {
    k = skipArgument(text, k);
    if (k < 0) return -1;
  }
  return k;
}

/** {nazwa} za poleceniem (np. \begin): [nazwa, indeks otwarcia, indeks za klamrą] albo null. */
function readName(text, i) {
  const at = skipSpaces(text, i);
  if (text[at] !== '{') return null;
  const close = text.indexOf('}', at + 1);
  if (close < 0 || close - at > 200 || text.slice(at + 1, close).includes('{')) return null;
  return [text.slice(at + 1, close), at, close + 1];
}

const INLINE_KINDS = new Set(['$', '\\(']);

/**
 * Analizuje plik .tex. Zwraca:
 * - problems: [{ from, to, code, severity, params }] — błędy struktury (klamry, środowiska, wzory),
 * - math: [{ from, to, innerFrom, innerTo, kind, env, display }] — zamknięte obszary matematyczne,
 * - labels / refs: [{ name, from, to, cmd? }] — argumenty \label i odsyłaczy (poza definicjami),
 * - commands: [{ name, from, to }] — użyte polecenia (także w treści definicji), envs: [{ name, from, to }],
 * - definitions: [[from, to]], documentBegin / documentEnd (offsety albo -1), clean (tekst bez komentarzy).
 */
export function analyzeStructure(text) {
  const { clean, comments } = blankNonCode(text);
  const n = clean.length;
  const problems = [];
  const math = [];
  const labels = [];
  const refs = [];
  const commands = [];
  const envUses = [];
  const definitions = [];
  let documentBegin = -1;
  let documentEnd = -1;

  const braces = []; // offsety otwartych {
  const envs = []; // { name, from, to }
  const frames = []; // { kind: '$'|'$$'|'\\('|'\\['|'env'|'text', from, to, env, envDepth, braceDepth, blankReported }
  const report = (from, to, code, severity = 'error', params = {}) => problems.push({ from, to, code, severity, params });
  const top = () => frames[frames.length - 1];
  const inMath = () => !!frames.length && top().kind !== 'text';
  const push = (kind, from, to, extra = {}) => frames.push({ kind, from, to, envDepth: envs.length, braceDepth: braces.length, ...extra });
  const closeRegion = (f, closeFrom, closeTo) =>
    math.push({
      from: f.from,
      to: closeTo,
      innerFrom: f.to,
      innerTo: closeFrom,
      kind: f.kind,
      env: f.env || null,
      display: !(INLINE_KINDS.has(f.kind) || (f.kind === 'env' && f.env === 'math')),
    });
  const unclosed = (f) => report(f.from, f.to, 'math-unclosed', 'error', { open: f.kind });
  // Po zamknięciu środowiska: wzory otwarte w nim (a niezamknięte) są błędem.
  const dropFramesAbove = (depth, closedEnv, closeFrom, closeTo) => {
    while (frames.length && top().envDepth > depth) {
      const f = frames.pop();
      if (f.kind === 'env' && f.env === closedEnv) closeRegion(f, closeFrom, closeTo);
      else if (f.kind !== 'text' && f.kind !== 'env') unclosed(f);
    }
  };

  let i = 0;
  scan: while (i < n) {
    const ch = clean[i];

    if (ch === '\n') {
      // Pusta linia = koniec akapitu: wzór w tekście nie może go przekroczyć.
      let j = i + 1;
      while (j < n && (clean[j] === ' ' || clean[j] === '\t' || clean[j] === '\r')) j++;
      if (j < n && clean[j] === '\n') {
        for (let k = frames.length - 1; k >= 0; k--) {
          const f = frames[k];
          if (f.kind === 'text') break;
          if (INLINE_KINDS.has(f.kind)) {
            unclosed(f);
            frames.length = k;
          } else if (!f.blankReported) {
            f.blankReported = true;
            report(j, j, 'math-blank-line', 'error', { open: f.kind === 'env' ? f.env : f.kind });
          }
        }
        i = j;
        continue;
      }
      i++;
      continue;
    }

    if (ch === '{') {
      braces.push(i);
      i++;
      continue;
    }
    if (ch === '}') {
      if (!braces.length) report(i, i + 1, 'brace-close');
      else braces.pop();
      while (frames.length && top().braceDepth > braces.length) {
        const f = frames.pop();
        if (INLINE_KINDS.has(f.kind)) unclosed(f);
        else if (f.kind !== 'text') {
          frames.push(f); // otoczenie/wzór wyświetlany w grupie: zostawiamy, rozliczy je \end
          break;
        }
      }
      i++;
      continue;
    }

    if (ch === '$') {
      const double = clean[i + 1] === '$';
      // >{$}c<{$} w specyfikacji kolumn tabeli
      if (!double && clean[i - 1] === '{' && clean[i + 1] === '}') {
        i++;
        continue;
      }
      const f = top();
      if (f && f.kind === '$') {
        frames.pop();
        closeRegion(f, i, i + 1);
        i++;
        continue;
      }
      if (double && f && f.kind === '$$') {
        frames.pop();
        closeRegion(f, i, i + 2);
        i += 2;
        continue;
      }
      if (inMath()) {
        report(i, i + (double ? 2 : 1), 'dollar-in-math', 'error', { open: f.kind === 'env' ? f.env : f.kind });
        i += double ? 2 : 1;
        continue;
      }
      if (double) {
        push('$$', i, i + 2);
        report(i, i + 2, 'dollar-display', 'info');
        i += 2;
      } else {
        push('$', i, i + 1);
        i++;
      }
      continue;
    }

    if (ch !== '\\') {
      i++;
      continue;
    }

    // ---------- polecenia ----------
    const next = clean[i + 1];
    if (next === '(' || next === '[') {
      const kind = next === '(' ? '\\(' : '\\[';
      if (inMath()) report(i, i + 2, 'math-nested', 'error', { open: kind });
      else push(kind, i, i + 2);
      i += 2;
      continue;
    }
    if (next === ')' || next === ']') {
      const kind = next === ')' ? '\\(' : '\\[';
      const f = top();
      if (f && f.kind === kind) {
        frames.pop();
        closeRegion(f, i, i + 2);
      } else {
        report(i, i + 2, 'math-close-unmatched', 'error', { close: `\\${next}` });
      }
      i += 2;
      continue;
    }
    let j = i + 1;
    while (j < n && isLetter(clean[j])) j++;
    if (j === i + 1) {
      i += 2;
      continue;
    }
    const name = clean.slice(i + 1, j);

    const defKind = DEFINERS[name];
    if (defKind) {
      const end = skipDefinition(clean, j, defKind);
      if (end > 0) {
        definitions.push([i, end]);
        for (const m of clean.slice(j, end).matchAll(/\\([a-zA-Z@]+)/g)) {
          commands.push({ name: m[1], from: j + m.index, to: j + m.index + m[0].length });
        }
        i = end;
        continue;
      }
    }
    commands.push({ name, from: i, to: j });

    if (name === 'begin' || name === 'end') {
      const arg = readName(clean, j);
      if (!arg) {
        i = j;
        continue;
      }
      const env = arg[0].trim();
      const end = arg[2];
      if (name === 'begin') {
        envs.push({ name: env, from: i, to: end });
        envUses.push({ name: env, from: i, to: end });
        if (env === 'document') documentBegin = end;
        if (MATH_ENVIRONMENTS.has(env)) {
          if (inMath()) report(i, end, 'math-nested', 'error', { open: env });
          else push('env', i, end, { env });
        }
      } else {
        const at = envs.map((e) => e.name).lastIndexOf(env);
        if (at === envs.length - 1 && at >= 0) {
          envs.pop();
          dropFramesAbove(envs.length, env, i, end);
        } else if (at >= 0) {
          for (const open of envs.slice(at + 1).reverse()) report(open.from, open.to, 'env-unclosed', 'error', { name: open.name, before: env });
          envs.length = at;
          dropFramesAbove(envs.length, env, i, end);
        } else if (envs.length) {
          // Najpewniej literówka w nazwie: traktujemy to jako zamknięcie bieżącego otoczenia,
          // żeby jeden błąd nie dawał drugiego zgłoszenia przy \end{document}.
          const open = envs.pop();
          report(i, end, 'env-mismatch', 'error', { name: env, open: open.name });
          dropFramesAbove(envs.length, open.name, i, end);
        } else {
          report(i, end, 'env-end-unmatched', 'error', { name: env });
        }
        if (env === 'document') {
          documentEnd = i;
          i = end;
          break scan; // TeX nie czyta dalej
        }
      }
      i = end;
      continue;
    }

    if (name === 'label' || REF_COMMANDS.has(name)) {
      let k = j;
      if (clean[k] === '*') k++;
      const arg = readName(clean, k);
      if (arg) {
        const inner = arg[1] + 1;
        const list = name === 'label' ? [[arg[0], 0]] : splitList(arg[0]);
        for (const [raw, off] of list) {
          const nm = raw.trim();
          if (!nm) continue;
          const from = inner + off + raw.indexOf(nm);
          const item = { name: nm, from, to: from + nm.length, cmd: name };
          (name === 'label' ? labels : refs).push(item);
        }
        i = arg[2];
        continue;
      }
    }

    if (inMath() && TEXT_IN_MATH.has(name)) {
      const at = skipSpaces(clean, j);
      if (clean[at] === '{') {
        braces.push(at);
        push('text', at, at + 1);
        i = at + 1;
        continue;
      }
    }
    i = j;
  }

  for (const f of frames) if (f.kind !== 'env' && f.kind !== 'text') unclosed(f);
  for (const e of envs) report(e.from, e.to, 'env-unclosed', 'error', { name: e.name });
  for (const b of braces) report(b, b + 1, 'brace-open');

  problems.sort((a, b) => a.from - b.from);
  math.sort((a, b) => a.from - b.from);
  return { clean, comments, problems, math, labels, refs, commands, envs: envUses, definitions, documentBegin, documentEnd };
}

/** "a, b,c" -> [["a", 0], [" b", 2], ["c", 5]] (fragment i jego offset). */
function splitList(s) {
  const out = [];
  let off = 0;
  for (const part of s.split(',')) {
    out.push([part, off]);
    off += part.length + 1;
  }
  return out;
}

/** Obszar matematyczny zawierający pozycję `pos` (na ogranicznikach też) albo null. */
export function mathRegionAt(regions, pos) {
  let lo = 0;
  let hi = regions.length - 1;
  let found = null;
  // Obszary się nie przeplatają, ale wewnętrzne (\text{$…$}) leżą w zewnętrznych: bierzemy najgłębszy.
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (regions[mid].from <= pos) lo = mid + 1;
    else hi = mid - 1;
  }
  for (let k = hi; k >= 0 && k > hi - 50; k--) {
    const r = regions[k];
    if (r.from <= pos && pos <= r.to && (!found || r.from >= found.from)) found = r;
  }
  return found;
}
