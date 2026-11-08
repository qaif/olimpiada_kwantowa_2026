// Tekst dokumentu dla człowieka: liczba słów i znaków bez poleceń, wzorów i komentarzy (limity
// w esejach), język dokumentu z babel/polyglossia (atrybut lang dla sprawdzania pisowni) i zakresy,
// w których sprawdzanie pisowni nie ma sensu (polecenia, wzory, etykiety). Czyste funkcje.
import { stripComments } from './scan.js';
import { analyzeStructure } from './structure.js';

// Polecenia, których argumenty nie są tekstem: ile grup {…} pominąć (opcje […] zawsze), Infinity — wszystkie kolejne.
const SKIP_ARGS = {
  label: 1, ref: 1, eqref: 1, pageref: 1, autoref: 1, cref: 1, Cref: 1, nameref: 1, vref: 1,
  cite: 1, citep: 1, citet: 1, parencite: 1, textcite: 1, autocite: 1, footcite: 1, nocite: 1,
  usepackage: 1, RequirePackage: 1, documentclass: 1, begin: Infinity, end: 1,
  includegraphics: 1, input: 1, include: 1, includeonly: 1, bibliography: 1, bibliographystyle: 1, addbibresource: 1,
  vspace: 1, hspace: 1, setlength: 2, addtolength: 2, setcounter: 2, addtocounter: 2, thispagestyle: 1, pagestyle: 1,
  pagenumbering: 1, url: 1, href: 1, hypersetup: 1, includepdf: 1, color: 1, textcolor: 1, colorbox: 1,
  multicolumn: 2, multirow: 2, fontsize: 2, newcommand: 2, renewcommand: 2, providecommand: 2, newenvironment: 3,
  renewenvironment: 3, newtheorem: 2, usetikzlibrary: 1, geometry: 1, captionsetup: 1, setlist: 1, rule: 2,
  lstinputlisting: 1, inputminted: 2, graphicspath: 1, DeclareMathOperator: 2,
};

// Przerwy w tekście zapisane poleceniami (\\, \, \; \quad…) — zamieniane na spację.
const SPACE_ESCAPES = new Set(['\\', ',', ';', ':', '!', ' ', '>', '\n']);

/** Zakres do liczenia: treść między \begin{document} a \end{document} albo cały plik (rozdział w \input). */
function bodyRange(s, length) {
  const from = s.documentBegin >= 0 ? s.documentBegin : 0;
  const to = s.documentEnd >= 0 ? s.documentEnd : length;
  return [from, to];
}

/** Tekst „do czytania”: bez komentarzy, wzorów, poleceń i argumentów technicznych. */
export function readableText(text, { fragment = false, structure } = {}) {
  const s = structure || analyzeStructure(text);
  const [from, to] = fragment ? [0, text.length] : bodyRange(s, text.length);
  const chars = s.clean.split('');
  for (const m of s.math) for (let k = Math.max(m.from, from); k < Math.min(m.to, to); k++) if (chars[k] !== '\n') chars[k] = ' ';
  const src = chars.slice(from, to).join('');
  let out = '';
  let i = 0;
  const n = src.length;
  const skipSpaces = (k) => {
    while (k < n && (src[k] === ' ' || src[k] === '\t')) k++;
    return k;
  };
  const skipGroup = (k, open, close) => {
    let depth = 0;
    for (let j = k; j < n; j++) {
      if (src[j] === '\\') j++;
      else if (src[j] === open) depth++;
      else if (src[j] === close && --depth === 0) return j + 1;
    }
    return n;
  };
  while (i < n) {
    const ch = src[i];
    if (ch === '\\') {
      let j = i + 1;
      while (j < n && /[a-zA-Z@]/.test(src[j])) j++;
      if (j === i + 1) {
        const esc = src[i + 1];
        out += esc === undefined || SPACE_ESCAPES.has(esc) ? ' ' : esc;
        i += 2;
        continue;
      }
      const name = src.slice(i + 1, j);
      if (src[j] === '*') j++;
      let groups = SKIP_ARGS[name] || 0;
      if (groups) {
        for (;;) {
          const k = skipSpaces(j);
          if (src[k] === '[') j = skipGroup(k, '[', ']');
          else if (groups > 0 && src[k] === '{' && (k === j || groups !== Infinity)) {
            j = skipGroup(k, '{', '}');
            groups--;
          } else break;
        }
      }
      out += ' ';
      i = j;
      continue;
    }
    out += ch === '{' || ch === '}' || ch === '&' || ch === '~' || ch === '%' || ch === '$' ? ' ' : ch;
    i++;
  }
  return out;
}

/** { words, characters (ze spacjami, białe znaki zwinięte), charactersNoSpaces } dla tekstu pliku. */
export function countWords(text, options = {}) {
  const readable = readableText(text, options).replace(/\s+/g, ' ').trim();
  if (!readable) return { words: 0, characters: 0, charactersNoSpaces: 0 };
  const tokens = readable.split(' ');
  return {
    words: tokens.filter((w) => /[\p{L}\p{N}]/u.test(w)).length,
    characters: readable.length,
    charactersNoSpaces: readable.replace(/ /g, '').length,
  };
}

// ---------- język dokumentu ----------

const LANGUAGE_CODES = {
  polish: 'pl', english: 'en', american: 'en', british: 'en', usenglish: 'en', ukenglish: 'en', canadian: 'en',
  australian: 'en', newzealand: 'en', german: 'de', ngerman: 'de', austrian: 'de', naustrian: 'de', french: 'fr',
  francais: 'fr', spanish: 'es', italian: 'it', portuguese: 'pt', brazil: 'pt', brazilian: 'pt', dutch: 'nl',
  czech: 'cs', slovak: 'sk', ukrainian: 'uk', russian: 'ru', lithuanian: 'lt', latvian: 'lv', hungarian: 'hu',
  magyar: 'hu', swedish: 'sv', danish: 'da', norsk: 'nb', finnish: 'fi', greek: 'el', turkish: 'tr', croatian: 'hr',
  slovene: 'sl', romanian: 'ro', estonian: 'et', catalan: 'ca', serbian: 'sr', bulgarian: 'bg',
};

const lastLanguage = (options) => {
  const opts = options.split(',').map((o) => o.trim().toLowerCase()).filter(Boolean);
  const main = opts.find((o) => o.startsWith('main='));
  if (main && LANGUAGE_CODES[main.slice(5)]) return LANGUAGE_CODES[main.slice(5)];
  for (let k = opts.length - 1; k >= 0; k--) if (LANGUAGE_CODES[opts[k]]) return LANGUAGE_CODES[opts[k]];
  return null;
};

/** Kod języka (np. 'pl') głównego języka dokumentu albo null, gdy preambuła go nie określa. */
export function documentLanguage(text) {
  const src = stripComments(text || '');
  const poly = /\\setmainlanguage\s*(?:\[[^\]]*\])?\s*\{\s*([a-zA-Z]+)\s*\}/.exec(src);
  if (poly) return LANGUAGE_CODES[poly[1].toLowerCase()] || null;
  const babel = /\\usepackage\s*(?:\[([^\]]*)\])?\s*\{[^{}]*\bbabel\b[^{}]*\}/.exec(src);
  if (babel) {
    const fromBabel = babel[1] ? lastLanguage(babel[1]) : null;
    if (fromBabel) return fromBabel;
    const cls = /\\documentclass\s*\[([^\]]*)\]/.exec(src);
    if (cls) {
      const fromClass = lastLanguage(cls[1]);
      if (fromClass) return fromClass;
    }
  }
  if (/\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^{}]*\bpolski\b[^{}]*\}/.test(src)) return 'pl';
  return null;
}

// ---------- zakresy bez sprawdzania pisowni ----------

// Polecenia, których argument to nazwa techniczna (etykieta, plik, pakiet), a nie tekst.
const NAME_ARG = /\\(?:label|ref|eqref|pageref|autoref|cref|Cref|nameref|cite[a-zA-Z]*|parencite|textcite|autocite|nocite|usepackage|RequirePackage|documentclass|begin|end|input|include|includegraphics|bibliography|bibliographystyle|addbibresource|url|href|includepdf|usetikzlibrary|hypersetup|setlength|color|textcolor|colorbox|pagestyle|thispagestyle|newcommand|renewcommand|newenvironment|newtheorem|lstinputlisting|graphicspath|geometry)\*?\s*(?:\[[^\]\n]*\]\s*)*\{[^{}\n]*\}/g;

/**
 * Zakresy [from, to) w tekście `slice` (fragmencie dokumentu zaczynającym się w `offset`), w których
 * pisownia nie powinna być sprawdzana: polecenia z argumentami-nazwami, same nazwy poleceń
 * i obszary matematyczne `math` (z analyzeStructure całego dokumentu).
 */
export function noSpellRanges(slice, offset = 0, math = []) {
  const out = [];
  for (const m of slice.matchAll(NAME_ARG)) out.push([offset + m.index, offset + m.index + m[0].length]);
  for (const m of slice.matchAll(/\\[a-zA-Z@]+/g)) out.push([offset + m.index, offset + m.index + m[0].length]);
  const end = offset + slice.length;
  for (const r of math) if (r.to > offset && r.from < end) out.push([Math.max(r.from, offset), Math.min(r.to, end)]);
  return out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}
