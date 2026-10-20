// Collects what the project itself defines: labels, citation keys, \newcommand & co., new environments
// and loaded packages. Pure functions; the editor caches the result per file and rescans a file only
// after it changes, so a keystroke rescans one file, not the whole project.

const TEX_FILE = /\.(tex|ltx|sty|cls|tikz)$/i;

/** Blanks out comments but keeps the text length and line structure, so offsets stay valid. */
export function stripComments(text) {
  return text.replace(/(^|[^\\])(%[^\n]*)/gm, (_, pre, comment) => pre + ' '.repeat(comment.length));
}

/** Line number (1-based) for each offset in a sorted list, in one pass. */
function lineNumbers(text, offsets) {
  const out = [];
  let line = 1;
  let pos = 0;
  for (const off of offsets) {
    for (let nl = text.indexOf('\n', pos); nl !== -1 && nl < off; nl = text.indexOf('\n', nl + 1)) {
      line++;
      pos = nl + 1;
    }
    out.push(line);
  }
  return out;
}

function lineAround(text, offset) {
  const from = text.lastIndexOf('\n', offset - 1) + 1;
  let to = text.indexOf('\n', offset);
  if (to < 0) to = text.length;
  return [from, to];
}

/** A short, readable piece of source near a label: its own line, or the previous one if it is bare. */
function labelPreview(text, offset) {
  let [from, to] = lineAround(text, offset);
  let line = text.slice(from, to);
  if (line.replace(/\\label\{[^{}]*\}/g, '').trim().length < 3 && from > 0) {
    let [pf, pt] = lineAround(text, from - 1);
    for (let k = 0; k < 3 && !text.slice(pf, pt).trim() && pf > 0; k++) [pf, pt] = lineAround(text, pf - 1);
    if (text.slice(pf, pt).trim()) line = text.slice(pf, pt);
  }
  line = line.replace(/\s+/g, ' ').trim();
  return line.length > 90 ? `${line.slice(0, 87)}…` : line;
}

/** Number of mandatory and optional arguments from an xparse spec such as "m O{x} m". */
function xparseArgs(spec) {
  let nargs = 0;
  let optional = 0;
  const re = /\s*(?:([mrRvb])|([oOdDsteE])(?:\{[^{}]*\})?)/g;
  let m;
  while ((m = re.exec(spec)) && m[0]) {
    if (m[1]) nargs++;
    else if (m[2] !== 's' && m[2] !== 't') optional++;
  }
  return { nargs, optional };
}

/**
 * Scans one file. Returns { labels: [{name, file, line, preview}], citeKeys: [string],
 * commands: [{name, nargs, optional, math, file, line}], environments: [{name, nargs, file, line}],
 * packages: [string] }.
 */
export function scanFile(path, raw) {
  const out = { labels: [], citeKeys: [], commands: [], environments: [], packages: [] };
  if (/\.bib$/i.test(path)) {
    for (const m of raw.matchAll(/@([a-zA-Z]+)\s*[{(]\s*([^,\s{}()]+)\s*,/g)) {
      if (!/^(comment|string|preamble)$/i.test(m[1])) out.citeKeys.push(m[2]);
    }
    return out;
  }
  if (!TEX_FILE.test(path)) return out;
  const text = stripComments(raw);

  const found = []; // [offset, kind, data]
  for (const m of text.matchAll(/\\label\s*\{([^{}]+)\}/g)) found.push([m.index, 'label', m[1].trim()]);
  for (const m of text.matchAll(/\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}/g)) out.citeKeys.push(m[1].trim());

  // \newcommand{\foo}[2][default]{…}, \newcommand\foo{…}, \renewcommand, \providecommand
  for (const m of text.matchAll(/\\(?:re)?newcommand\*?|\\providecommand\*?/g)) {
    const rest = text.slice(m.index + m[0].length, m.index + m[0].length + 200);
    const d = /^\s*(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))\s*(?:\[\s*(\d)\s*\])?\s*(\[)?/.exec(rest);
    if (!d) continue;
    const total = d[3] ? Number(d[3]) : 0;
    const optional = d[4] && total > 0 ? 1 : 0;
    found.push([m.index, 'command', { name: d[1] || d[2], nargs: total - optional, optional, math: false }]);
  }
  // \def\foo#1#2{…}
  for (const m of text.matchAll(/\\(?:g|e|x)?def\s*\\([a-zA-Z@]+)((?:\s*#\d)*)/g)) {
    found.push([m.index, 'command', { name: m[1], nargs: (m[2].match(/#/g) || []).length, optional: 0, math: false }]);
  }
  // \DeclareMathOperator{\Tr}{Tr}
  for (const m of text.matchAll(/\\DeclareMathOperator\*?\s*(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))/g)) {
    found.push([m.index, 'command', { name: m[1] || m[2], nargs: 0, optional: 0, math: true }]);
  }
  // \DeclarePairedDelimiter\abs{\lvert}{\rvert} (mathtools): one argument, math.
  for (const m of text.matchAll(/\\DeclarePairedDelimiter(X?)\s*(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))\s*(?:\[\s*(\d)\s*\])?/g)) {
    found.push([m.index, 'command', { name: m[2] || m[3], nargs: m[1] && m[4] ? Number(m[4]) : 1, optional: 0, math: true }]);
  }
  // \NewDocumentCommand{\foo}{m O{x}}
  for (const m of text.matchAll(/\\(?:New|Renew|Provide|Declare)DocumentCommand\s*(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g)) {
    const { nargs, optional } = xparseArgs(m[3]);
    found.push([m.index, 'command', { name: m[1] || m[2], nargs, optional: optional ? 1 : 0, math: false }]);
  }
  // \newenvironment{name}[n], \newtheorem{name}{Title}, \NewDocumentEnvironment{name}{spec}
  for (const m of text.matchAll(/\\(?:re)?newenvironment\*?\s*\{([^{}]+)\}\s*(?:\[\s*(\d)\s*\])?/g)) {
    found.push([m.index, 'environment', { name: m[1].trim(), nargs: m[2] ? Number(m[2]) : 0 }]);
  }
  for (const m of text.matchAll(/\\newtheorem\*?\s*\{([^{}]+)\}/g)) {
    found.push([m.index, 'environment', { name: m[1].trim(), nargs: 0, theorem: true }]);
  }
  for (const m of text.matchAll(/\\(?:New|Renew)DocumentEnvironment\s*\{([^{}]+)\}\s*\{([^{}]*)\}/g)) {
    found.push([m.index, 'environment', { name: m[1].trim(), nargs: xparseArgs(m[2]).nargs }]);
  }
  for (const m of text.matchAll(/\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}/g)) {
    for (const p of m[1].split(',')) if (p.trim()) out.packages.push(p.trim());
  }

  found.sort((a, b) => a[0] - b[0]);
  const lines = lineNumbers(text, found.map((f) => f[0]));
  found.forEach(([offset, kind, data], k) => {
    const line = lines[k];
    if (kind === 'label') out.labels.push({ name: data, file: path, line, preview: labelPreview(raw, offset) });
    else if (kind === 'command') out.commands.push({ ...data, file: path, line });
    else out.environments.push({ ...data, file: path, line });
  });
  return out;
}

/** Merges per-file results; first definition wins, lists are sorted by name. */
export function mergeSymbols(results) {
  const labels = new Map();
  const citeKeys = new Set();
  const commands = new Map();
  const environments = new Map();
  const packages = new Set();
  for (const r of results) {
    for (const l of r.labels) if (!labels.has(l.name)) labels.set(l.name, l);
    for (const k of r.citeKeys) citeKeys.add(k);
    for (const c of r.commands) if (!commands.has(c.name)) commands.set(c.name, c);
    for (const e of r.environments) if (!environments.has(e.name)) environments.set(e.name, e);
    for (const p of r.packages) packages.add(p);
  }
  const byName = (a, b) => a.name.localeCompare(b.name);
  return {
    labels: [...labels.values()].sort(byName),
    citeKeys: [...citeKeys].sort((a, b) => a.localeCompare(b)),
    commands: [...commands.values()].sort(byName),
    environments: [...environments.values()].sort(byName),
    packages: [...packages].sort(),
  };
}

/** Scans [path, text] pairs (labels from .tex files, citation keys from .bib files and \bibitem). */
export function scanSymbols(entries) {
  return mergeSymbols(entries.map(([path, raw]) => scanFile(path, raw)));
}
