// Szybkie sprawdzanie pliku .tex bez kompilacji: błędy struktury (z structure.js), odsyłacze do
// nieistniejących etykiet, powtórzone \label, polecenia z pakietów, których nie dołączono, oraz
// podpowiedź o $$…$$. Zasada: mało szumu — ostrzeżenie tylko wtedy, gdy problem jest pewny; przy
// czymkolwiek nietypowym (własne pakiety .sty, nieznane pakiety, klasa ładująca dużo) reguła milczy.
// Czyste funkcje, bez CodeMirror (testy w node).
import { COMMANDS, ENVIRONMENTS, PACKAGES } from './catalog.js';
import { scanFile, stripComments } from './scan.js';
import { analyzeStructure } from './structure.js';

/** Najwięcej zgłoszeń z jednego pliku (reszta i tak wynika zwykle z pierwszych). */
export const MAX_PROBLEMS = 100;

const DOCUMENT_CLASS = /\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}/;

/** scanFile rozszerzone o klasę dokumentu: to, co reguły wiedzą o pliku, którego nie widać w edytorze. */
export function scanLintInfo(path, raw) {
  const info = scanFile(path, raw);
  const m = /\.(tex|ltx)$/i.test(path) ? DOCUMENT_CLASS.exec(stripComments(raw)) : null;
  info.documentClass = m ? m[1].trim() : null;
  return info;
}

// ---------- brakujące pakiety ----------

// Klasy, które same nie ładują pakietów z katalogu (amsart ładuje amsmath, beamer — hyperref, xcolor…).
const PLAIN_CLASSES = new Set([
  'article', 'report', 'book', 'letter', 'scrartcl', 'scrreprt', 'scrbook', 'extarticle', 'extreport', 'extbook',
  'mwart', 'mwrep', 'mwbk',
]);

// Pakiety, które ładują inne (tylko to, co ma znaczenie dla poleceń z katalogu; w razie wątpliwości
// raczej za dużo niż za mało — brak ostrzeżenia jest lepszy niż fałszywe).
const IMPLIES = {
  mathtools: ['amsmath'],
  physics: ['amsmath'],
  mhchem: ['amsmath'],
  amssymb: ['amsfonts'],
  amsmath: ['amstext'],
  tikz: ['pgf', 'xcolor', 'graphicx'],
  pgfplots: ['tikz'],
  quantikz: ['tikz', 'amsmath'],
  todonotes: ['tikz', 'xcolor'],
  minted: ['xcolor'],
  hyperref: ['url', 'nameref'],
  biblatex: ['url'],
  subcaption: ['caption'],
  tabularx: ['array'],
  graphicx: ['graphics'],
  xcolor: ['color'],
};

// Inne pakiety, które też dają dane polecenie.
const EXTRA_PROVIDERS = {
  url: ['url'],
  includegraphics: ['graphics'],
  textcolor: ['color'],
  colorbox: ['color'],
  mathbb: ['amsfonts'],
  mathfrak: ['amsfonts'],
  text: ['amstext'],
};

// Pakiety, o których wiadomo, że nie definiują poleceń z katalogu inaczej niż katalog zakłada.
// Załadowanie czegokolwiek spoza tej listy wyłącza regułę (siunitx ma własne \qty, itd.).
const KNOWN_PACKAGES = new Set([
  ...PACKAGES.map((p) => p.name).filter((p) => p !== 'siunitx'),
  'url', 'graphics', 'color', 'amsfonts', 'amstext', 'xparse', 'etoolbox', 'ifthen', 'calc', 'textcomp', 'bookmark',
  'nameref', 'lscape', 'verbatim', 'fancyvrb', 'upquote', 'ragged2e', 'indentfirst', 'titling', 'polyglossia',
  'icomma', 'fontawesome5', 'xurl', 'placeins', 'afterpage', 'changepage', 'footmisc', 'hyphenat', 'datetime',
]);

function packageIndex(rows) {
  const map = new Map(); // nazwa bazowa -> 'pkg' albo 'pkg|pkg2'
  for (const { base, pkg = null } of rows) {
    if (!base) continue;
    if (!map.has(base)) map.set(base, pkg);
    else if (pkg === null) map.set(base, null); // wariant bez pakietu: polecenie jądra LaTeX-a
  }
  // Polecenia klasy beamer to nie pakiet; null — jądro.
  for (const [k, v] of map) if (v === null || v === 'beamer') map.delete(k);
  return map;
}
const COMMAND_PKG = packageIndex(COMMANDS.map((c) => ({ base: /^\\([a-zA-Z]+)/.exec(c.name)?.[1], pkg: c.pkg })));
const ENV_PKG = packageIndex(ENVIRONMENTS.map((e) => ({ base: e.name, pkg: e.pkg })));

/** Zbiór pakietów razem z tymi, które ładują pośrednio. */
export function withImplied(packages) {
  const out = new Set();
  const add = (p) => {
    if (out.has(p)) return;
    out.add(p);
    for (const q of IMPLIES[p] || []) add(q);
  };
  for (const p of packages) add(p);
  return out;
}

/**
 * Czy regułę o brakujących pakietach można stosować: znana „goła” klasa, tylko znane pakiety
 * i żadnych własnych plików .sty/.cls w projekcie.
 */
export function packageRuleApplies({ documentClass, packages, files = [] }) {
  if (!documentClass || !PLAIN_CLASSES.has(documentClass)) return false;
  if (files.some((f) => /\.(sty|cls)$/i.test(f))) return false;
  for (const p of packages) if (!KNOWN_PACKAGES.has(p)) return false;
  return true;
}

// ---------- reguły ----------

/**
 * Sprawdza plik. `project`: { files: [ścieżki], others: [{ path, info: wynik scanLintInfo }] } —
 * pozostałe pliki projektu (bieżący, jeśli jest na liście, zostaje pominięty: liczy się tekst `text`).
 * Zwraca [{ from, to, severity: 'error'|'warning'|'info', code, params }] posortowane po pozycji.
 */
export function lintLatex(text, { path = 'main.tex', project = {}, structure } = {}) {
  const s = structure || analyzeStructure(text);
  const others = (project.others || []).filter((o) => o.path !== path).map((o) => o.info);
  const self = scanLintInfo(path, text);
  const out = s.problems.map((p) => ({ ...p }));

  // Odsyłacze i etykiety.
  const otherLabels = new Map();
  for (const o of others) for (const l of o.labels) if (!otherLabels.has(l.name)) otherLabels.set(l.name, l);
  const ownLabels = new Map();
  for (const l of s.labels) {
    if (/[#\\]/.test(l.name)) continue;
    const first = ownLabels.get(l.name);
    if (first) out.push({ from: l.from, to: l.to, severity: 'warning', code: 'label-duplicate', params: { name: l.name, where: lineOf(text, first.from) } });
    else {
      ownLabels.set(l.name, l);
      const other = otherLabels.get(l.name);
      if (other) out.push({ from: l.from, to: l.to, severity: 'warning', code: 'label-duplicate', params: { name: l.name, where: `${other.file}:${other.line}` } });
    }
  }
  const allPackages = new Set([...self.packages, ...others.flatMap((o) => o.packages || [])]);
  // Etykiety tworzone makrami (\label{#1}) albo z innych dokumentów (xr): nie wiadomo, jakie istnieją.
  const dynamicLabels =
    self.labels.some((l) => /[#\\]/.test(l.name)) ||
    others.some((o) => o.labels.some((l) => /[#\\]/.test(l.name))) ||
    allPackages.has('xr') ||
    allPackages.has('xr-hyper');
  if (!dynamicLabels) {
    for (const r of s.refs) {
      if (/[#\\]/.test(r.name) || ownLabels.has(r.name) || otherLabels.has(r.name)) continue;
      out.push({ from: r.from, to: r.to, severity: 'warning', code: 'ref-unknown', params: { name: r.name } });
    }
  }

  // Polecenia i otoczenia z pakietów, których nie dołączono.
  const documentClass = self.documentClass || others.find((o) => o.documentClass)?.documentClass || null;
  if (packageRuleApplies({ documentClass, packages: allPackages, files: project.files || [] })) {
    const loaded = withImplied(allPackages);
    const userCommands = new Set([...self.commands, ...others.flatMap((o) => o.commands || [])].map((c) => c.name));
    const userEnvs = new Set([...self.environments, ...others.flatMap((o) => o.environments || [])].map((e) => e.name));
    const missing = (pkg, base) => {
      const alts = [...pkg.split('|'), ...(EXTRA_PROVIDERS[base] || [])];
      return alts.some((p) => loaded.has(p)) ? null : pkg.split('|');
    };
    const seen = new Set();
    for (const c of s.commands) {
      const pkg = COMMAND_PKG.get(c.name);
      if (!pkg || seen.has(c.name) || userCommands.has(c.name)) continue;
      const need = missing(pkg, c.name);
      seen.add(c.name);
      if (need) out.push({ from: c.from, to: c.to, severity: 'warning', code: 'package-missing', params: { name: `\\${c.name}`, pkg: need[0], alternatives: need } });
    }
    for (const e of s.envs) {
      const pkg = ENV_PKG.get(e.name);
      if (!pkg || seen.has(`env:${e.name}`) || userEnvs.has(e.name)) continue;
      const need = missing(pkg, e.name);
      seen.add(`env:${e.name}`);
      if (need) out.push({ from: e.from, to: e.to, severity: 'warning', code: 'package-missing', params: { name: `\\begin{${e.name}}`, pkg: need[0], alternatives: need } });
    }
  }

  out.sort((a, b) => a.from - b.from || a.to - b.to);
  return out.slice(0, MAX_PROBLEMS);
}

/**
 * Gdzie wstawić \usepackage{pkg} w preambule tego pliku: za ostatnim \usepackage (albo za
 * \documentclass). Zwraca { pos, insert } albo null, gdy plik nie ma preambuły albo już ładuje pakiet.
 */
export function usepackageInsertion(text, pkg) {
  const clean = stripComments(text);
  const cls = DOCUMENT_CLASS.exec(clean);
  if (!cls) return null;
  const begin = clean.indexOf('\\begin{document}');
  const preamble = begin >= 0 ? clean.slice(0, begin) : clean;
  let after = cls.index + cls[0].length;
  for (const m of preamble.matchAll(/\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}/g)) {
    if (m[1].split(',').some((p) => p.trim() === pkg)) return null;
    after = m.index + m[0].length;
  }
  const eol = text.indexOf('\n', after);
  const pos = eol < 0 ? text.length : eol;
  return { pos, insert: `\n\\usepackage{${pkg}}` };
}

/** Numer linii (od 1) offsetu. */
export function lineOf(text, offset) {
  let line = 1;
  for (let i = text.indexOf('\n'); i !== -1 && i < offset; i = text.indexOf('\n', i + 1)) line++;
  return line;
}
