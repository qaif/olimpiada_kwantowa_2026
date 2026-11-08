// Błędy i ostrzeżenia z kompilacji (server/logParser.js: { level, file, line, message, content })
// jako zakresy w pliku otwartym w edytorze. Kompilacja widzi tekst z chwili zlecenia, a plik mógł się
// od tamtej pory zmienić: numer linii „kotwiczymy” w treści linii z chwili kompilacji. Czyste funkcje.

/** Poziom z logu -> ważność diagnostyki CodeMirror. */
export const SEVERITY = { error: 'error', warning: 'warning', typesetting: 'info' };

/** Jak daleko (w liniach) szukać linii, która przesunęła się po edycji. */
export const ANCHOR_WINDOW = 40;

/** Czy problem z logu dotyczy pliku `path` (log podaje ścieżkę względem projektu, czasem bez .tex). */
export function issueMatchesFile(issue, path) {
  if (!issue?.file || !path) return false;
  const f = issue.file.replace(/^\.\//, '');
  return f === path || `${f}.tex` === path;
}

/** Liczba błędów i ostrzeżeń w ostatniej kompilacji dla każdej ścieżki: Map(ścieżka -> { error, warning, info }). */
export function countIssuesByFile(issues, paths) {
  const out = new Map();
  for (const issue of issues || []) {
    const path = paths.find((p) => issueMatchesFile(issue, p));
    if (!path) continue;
    const c = out.get(path) || { error: 0, warning: 0, info: 0 };
    c[SEVERITY[issue.level] || 'info']++;
    out.set(path, c);
  }
  return out;
}

const splitLines = (text) => text.split('\n');

/**
 * Numer linii w `current` odpowiadający linii `line` w `compiled` albo null, gdy tej linii już nie ma.
 * Ta sama treść pod tym samym numerem wygrywa; potem najbliższa linia o tej samej treści w oknie.
 */
export function anchorLine(line, compiledLines, currentLines) {
  if (!line || line < 1) return null;
  if (!compiledLines || compiledLines === currentLines) return Math.min(line, currentLines.length);
  const want = compiledLines[line - 1];
  if (want === undefined) return Math.min(line, currentLines.length); // linia za końcem (np. brak \end{document})
  if (currentLines[line - 1] === want) return line;
  if (!want.trim()) return null; // pustej linii nie da się pewnie odnaleźć
  for (let d = 1; d <= ANCHOR_WINDOW; d++) {
    if (currentLines[line - 1 - d] === want) return line - d;
    if (currentLines[line - 1 + d] === want) return line + d;
  }
  return null;
}

/**
 * Kolumny [od, do) do podkreślenia w linii. TeX pokazuje w kontekście błędu linię „l.12 tekst przed
 * błędem”, więc koniec tego fragmentu to miejsce błędu: podkreślamy polecenie (albo znak) tuż przed
 * nim. Dla ostrzeżeń z nazwą w cudzysłowie (`nazwa') — tę nazwę, jeśli jest w linii. Inaczej całą linię
 * bez wcięcia.
 */
export function issueColumns(issue, lineText) {
  const whole = () => {
    const start = lineText.length - lineText.trimStart().length;
    return [start, Math.max(start, lineText.trimEnd().length)];
  };
  const ctx = /^l\.\d+ (.*)$/m.exec(issue.content || '');
  if (ctx && issue.level === 'error') {
    let before = ctx[1];
    let col = -1;
    if (before.startsWith('...')) {
      before = before.slice(3);
      col = before ? lineText.lastIndexOf(before) : -1;
      if (col >= 0) col += before.length;
    } else if (lineText.startsWith(before)) {
      col = before.length;
    } else {
      const indent = lineText.length - lineText.trimStart().length;
      if (lineText.slice(indent).startsWith(before.trimStart())) col = indent + before.trimStart().length;
    }
    if (col > 0) {
      const head = lineText.slice(0, col);
      // Polecenie razem z prostym argumentem (\end{align}), samo polecenie albo ostatni znak.
      const m = /\\[a-zA-Z@]+\*?(?:\{[^{}\n]*\})?\s*$|\\.$|\S$/.exec(head);
      if (m) return [m.index, m.index + m[0].trimEnd().length];
    }
  }
  const quoted = /[`‘']([^`'’\s]{1,80})['’]/.exec(issue.message || '');
  if (quoted) {
    const at = lineText.indexOf(quoted[1]);
    if (at >= 0) return [at, at + quoted[1].length];
  }
  return whole();
}

/**
 * Diagnostyki z kompilacji dla pliku `path`: [{ from, to, severity, message, context, line }].
 * `compiledText` — treść pliku wysłana do kompilacji (albo null, gdy jej nie znamy), `currentText` —
 * obecna. Problemy bez numeru linii albo z linii, której już nie ma, są pomijane (zostają w logach).
 */
export function compileDiagnostics(issues, path, compiledText, currentText) {
  const current = splitLines(currentText);
  const compiled = compiledText == null || compiledText === currentText ? current : splitLines(compiledText);
  const starts = [0];
  for (let i = 0; i < current.length - 1; i++) starts.push(starts[i] + current[i].length + 1);
  const out = [];
  const seen = new Set();
  for (const issue of issues || []) {
    if (!issueMatchesFile(issue, path)) continue;
    const line = anchorLine(issue.line, compiled, current);
    if (!line) continue;
    const text = current[line - 1] ?? '';
    const [a, b] = issueColumns(issue, text);
    const from = starts[line - 1] + a;
    const to = starts[line - 1] + b;
    const severity = SEVERITY[issue.level] || 'info';
    const key = `${from}|${severity}|${issue.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ from, to, severity, message: issue.message, context: issue.content || '', line });
  }
  return out.sort((x, y) => x.from - y.from);
}
