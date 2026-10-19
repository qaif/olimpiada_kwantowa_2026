// Parses a TeX log (ideally produced with max_print_line=10000 and -file-line-error)
// into a list of issues: { level: 'error'|'warning'|'typesetting', file, line, message, content }.

const FILE_LINE_ERROR = /^((?:\.{0,2}\/|[A-Za-z]:[\\/])?[^:\s][^:]*?\.\w+):(\d+): (.*)$/;
const LATEX_WARNING = /^((?:La|pdf|Xe|Lua)?TeX|LaTeX\s\w+|Class \S+|Package \S+) Warning: (.*)$/;
const BAD_BOX = /^(Over|Under)full \\[hv]box \((.*?)\) (?:in paragraph |in alignment |detected |has occurred while \\output is active)?(?:at lines? (\d+)(?:--(\d+))?)?/;
const LINE_REF = /on input line (\d+)/;
const FILE_TOKEN = /^(?:\.{0,2}\/|\/|[A-Za-z]:[\\/])?[^\s()"]*\.[A-Za-z0-9]{1,8}/;

export function normalizePath(p, roots = []) {
  let s = p.replace(/\\/g, '/');
  for (const root of roots) {
    const r = root.replace(/\\/g, '/').replace(/\/?$/, '/');
    if (s.toLowerCase().startsWith(r.toLowerCase())) s = s.slice(r.length);
  }
  s = s.replace(/^(\.\/)+/, '').replace(/\/\.\//g, '/');
  return s;
}

export function parseLog(log, { roots = [] } = {}) {
  const lines = log.replace(/\r\n?/g, '\n').split('\n');
  const issues = [];
  const stack = []; // file names or null for non-file parentheses
  const currentFile = () => {
    for (let i = stack.length - 1; i >= 0; i--) if (stack[i]) return stack[i];
    return null;
  };
  const isProjectFile = (f) => f && !f.startsWith('/') && !/^[A-Za-z]:\//.test(f);

  const updateStack = (line) => {
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '(') {
        const m = FILE_TOKEN.exec(line.slice(i + 1));
        if (m) {
          stack.push(normalizePath(m[0], roots));
          i += m[0].length;
        } else {
          stack.push(null);
        }
      } else if (ch === ')') {
        stack.pop();
      }
    }
  };

  // Collect continuation lines of a message (until a blank line or a new message).
  const collect = (start, max = 12) => {
    const out = [];
    for (let j = start; j < lines.length && out.length < max; j++) {
      const l = lines[j];
      if (!l.trim()) break;
      out.push(l);
    }
    return out;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;

    if ((m = FILE_LINE_ERROR.exec(line)) && !line.startsWith('l.')) {
      const context = collect(i + 1);
      issues.push({
        level: 'error',
        file: normalizePath(m[1], roots),
        line: Number(m[2]),
        message: m[3].replace(/^LaTeX Error: /, ''),
        content: context.join('\n'),
      });
      // Skip the "l.<n> ..." context lines so stray parentheses in source don't confuse the stack.
      let j = i + 1;
      while (j < lines.length && j < i + 12 && !/^l\.\d+/.test(lines[j]) && lines[j].trim()) j++;
      if (j < lines.length && /^l\.\d+/.test(lines[j])) i = j + 1;
      continue;
    }

    if (line.startsWith('! ')) {
      const context = collect(i + 1);
      const lineMatch = context.map((l) => /^l\.(\d+)/.exec(l)).find(Boolean);
      const file = currentFile();
      issues.push({
        level: 'error',
        file: isProjectFile(file) ? file : null,
        line: lineMatch ? Number(lineMatch[1]) : null,
        message: line.slice(2).replace(/^LaTeX Error: /, ''),
        content: context.join('\n'),
      });
      const idx = context.findIndex((l) => /^l\.\d+/.test(l));
      if (idx >= 0) i += idx + 2;
      continue;
    }

    if ((m = LATEX_WARNING.exec(line))) {
      const source = m[1];
      // Continuation lines are prefixed with "(<package>)", "(<class>)" or "(Font)".
      const prefix = (/^(?:Package|Class) (\S+)/.exec(source) || /^LaTeX (Font)$/.exec(source))?.[1];
      const cont = [];
      for (let j = i + 1; j < lines.length; j++) {
        const l = lines[j];
        if (!l.trim()) break;
        if (prefix && l.startsWith(`(${prefix})`)) cont.push(l.slice(prefix.length + 2).trim());
        else if (/^\s{4,}/.test(l)) cont.push(l.trim());
        else break;
      }
      const message = [m[2], ...cont].join(' ').replace(/\s+/g, ' ').trim();
      const lineNo = LINE_REF.exec(message);
      const file = currentFile();
      const isFont = /Font/.test(source);
      issues.push({
        level: isFont ? 'typesetting' : 'warning',
        file: isProjectFile(file) ? file : null,
        line: lineNo ? Number(lineNo[1]) : null,
        message: source.startsWith('Package') || source.startsWith('Class') ? `${source}: ${message}` : message,
        content: '',
      });
      i += cont.length;
      updateStack(line);
      continue;
    }

    if ((m = BAD_BOX.exec(line))) {
      const file = currentFile();
      issues.push({
        level: 'typesetting',
        file: isProjectFile(file) ? file : null,
        line: m[3] ? Number(m[3]) : null,
        message: line.trim(),
        content: '',
      });
      // The following lines show the offending text (may contain stray parentheses): skip
      // everything up to the blank line that ends the message.
      const stop = Math.min(lines.length, i + 8);
      while (i + 1 < stop && lines[i + 1].trim()) i++;
      continue;
    }

    if (/^No file .*\.(bbl|ind|gls)\./.test(line)) {
      issues.push({ level: 'warning', file: null, line: null, message: line.trim(), content: '' });
    }

    updateStack(line);
  }

  // Deduplicate identical messages (LaTeX repeats some warnings on every run).
  const seen = new Set();
  return issues.filter((it) => {
    const key = `${it.level}|${it.file}|${it.line}|${it.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Extracts errors from latexmk's own output (bibtex/biber failures, missing tools). */
export function parseLatexmkOutput(out) {
  const issues = [];
  for (const line of out.split(/\r?\n/)) {
    if (/^(ERROR|FATAL) - /.test(line) || /^Biber error:/.test(line)) {
      issues.push({ level: 'error', file: null, line: null, message: line.trim(), content: '' });
    } else if (/^I couldn't open (database|style) file/.test(line) || /^Latexmk: (Missing input|Summary of warnings)/.test(line)) {
      issues.push({ level: 'warning', file: null, line: null, message: line.trim(), content: '' });
    }
  }
  return issues;
}
