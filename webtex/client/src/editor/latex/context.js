// Where is the cursor, LaTeX-wise? Math or text mode, inside a comment or verbatim, which environments
// are open. Pure functions over plain strings; the editor passes a bounded slice before the cursor,
// so the cost does not grow with the file size.
import { MATH_ENVIRONMENTS } from './catalog.js';

// Commands whose braced argument is typeset as text even inside math.
const TEXT_ARG_COMMANDS = new Set([
  'text', 'textrm', 'textit', 'textbf', 'textsf', 'texttt', 'textnormal', 'textup', 'emph', 'mbox', 'hbox',
  'intertext', 'shortintertext', 'label', 'tag', 'operatorname', 'eqref', 'ref',
]);
const VERBATIM_ENVIRONMENTS = new Set(['verbatim', 'verbatim*', 'lstlisting', 'minted', 'comment', 'Verbatim']);

/** Maximum number of characters before the cursor that the mode scan looks at. */
export const MODE_WINDOW = 20000;
/** Maximum number of characters before the cursor scanned for open environments. */
export const ENV_WINDOW = 100000;

/**
 * Offset in `text` where the mode scan should start: just after the last blank line (math cannot
 * span a paragraph break), but not more than `MODE_WINDOW` characters back.
 */
export function paragraphStart(text, pos = text.length) {
  const from = Math.max(0, pos - MODE_WINDOW);
  const slice = text.slice(from, pos);
  let start = 0;
  const re = /\n[ \t]*\r?\n/g;
  let m;
  while ((m = re.exec(slice))) start = m.index + m[0].length;
  return from + start;
}

/**
 * Scans `text` (from a paragraph start up to the cursor) and returns
 * { mode: 'math'|'text', inComment, inVerbatim, envs: [innermost last] }.
 */
export function analyzeContext(text) {
  // Frames: { math: bool, close: '$' | '$$' | ')' | ']' | 'env' | '}', env? }
  const stack = [];
  const isMath = () => (stack.length ? stack[stack.length - 1].math : false);
  const n = text.length;
  let i = 0;

  const popTo = (pred) => {
    for (let k = stack.length - 1; k >= 0; k--) {
      if (pred(stack[k])) {
        stack.length = k;
        return true;
      }
    }
    return false;
  };
  const readGroup = (at) => {
    // {name} after optional spaces; returns [name, endIndex] or null.
    let j = at;
    while (j < n && (text[j] === ' ' || text[j] === '\t')) j++;
    if (text[j] !== '{') return null;
    const close = text.indexOf('}', j + 1);
    if (close < 0) return null;
    return [text.slice(j + 1, close).trim(), close + 1];
  };

  while (i < n) {
    const ch = text[i];
    if (ch === '%') {
      const nl = text.indexOf('\n', i);
      if (nl < 0) return { mode: isMath() ? 'math' : 'text', inComment: true, inVerbatim: false, envs: envNames(stack) };
      i = nl + 1;
      continue;
    }
    if (ch === '\\') {
      const next = text[i + 1];
      if (next === undefined) break;
      if (!/[a-zA-Z@]/.test(next)) {
        if (next === '(' || next === '[') stack.push({ math: true, close: next === '(' ? ')' : ']' });
        else if (next === ')' || next === ']') popTo((f) => f.close === next);
        i += 2;
        continue;
      }
      let j = i + 1;
      while (j < n && /[a-zA-Z@]/.test(text[j])) j++;
      const name = text.slice(i + 1, j);
      i = j;
      if (name === 'begin' || name === 'end') {
        const g = readGroup(j);
        if (!g) continue;
        const [env, end] = g;
        i = end;
        if (name === 'begin') {
          if (VERBATIM_ENVIRONMENTS.has(env)) {
            const close = text.indexOf(`\\end{${env}}`, i);
            if (close < 0) return { mode: 'text', inComment: false, inVerbatim: true, envs: [...envNames(stack), env] };
            i = close + env.length + 6;
            continue;
          }
          stack.push({ math: MATH_ENVIRONMENTS.has(env) || isMath(), close: 'env', env });
        } else {
          popTo((f) => f.close === 'env' && f.env === env);
        }
        continue;
      }
      if (name === 'verb') {
        const delim = text[i];
        if (delim === undefined) break;
        const close = text.indexOf(delim, i + 1);
        if (close < 0) return { mode: 'text', inComment: false, inVerbatim: true, envs: envNames(stack) };
        i = close + 1;
        continue;
      }
      if (TEXT_ARG_COMMANDS.has(name)) {
        let k = i;
        while (k < n && (text[k] === ' ' || text[k] === '*')) k++;
        if (text[k] === '{') {
          stack.push({ math: false, close: '}' });
          i = k + 1;
        }
      }
      continue;
    }
    if (ch === '{') stack.push({ math: isMath(), close: '}' });
    else if (ch === '}') {
      if (stack.length && stack[stack.length - 1].close === '}') stack.pop();
    } else if (ch === '$') {
      const dbl = text[i + 1] === '$';
      const tok = dbl ? '$$' : '$';
      const top = stack[stack.length - 1];
      if (top && top.close === tok) stack.pop();
      else if (!(isMath() && stack.some((f) => f.close === tok) && popTo((f) => f.close === tok))) {
        stack.push({ math: true, close: tok });
      }
      i += dbl ? 2 : 1;
      continue;
    }
    i++;
  }
  return { mode: isMath() ? 'math' : 'text', inComment: false, inVerbatim: false, envs: envNames(stack) };
}

const envNames = (stack) => stack.filter((f) => f.close === 'env').map((f) => f.env);

/**
 * Environments still open at the end of `text` (innermost last), from \begin/\end pairs.
 * Comments are ignored. Used to suggest the right name after \end{.
 */
export function openEnvironments(text) {
  const stack = [];
  const re = /(^|[^\\])%[^\n]*|\\(begin|end)\s*\{([^{}]+)\}/g;
  let m;
  while ((m = re.exec(text))) {
    if (!m[2]) continue;
    const env = m[3].trim();
    if (m[2] === 'begin') stack.push(env);
    else {
      const k = stack.lastIndexOf(env);
      if (k >= 0) stack.length = k;
    }
  }
  return stack;
}

/**
 * True when the environment opened just before `after` is already closed in `after` (the text after
 * the cursor), counting nested environments of the same name. Looks at most `limit` characters ahead.
 */
export function isEnvironmentClosed(env, after, limit = 200000) {
  const text = after.length > limit ? after.slice(0, limit) : after;
  const esc = env.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(String.raw`(^|[^\\])%[^\n]*|\\(begin|end)\s*\{\s*${esc}\s*\}`, 'g');
  let depth = 1;
  let m;
  while ((m = re.exec(text))) {
    if (!m[2]) continue;
    depth += m[2] === 'begin' ? 1 : -1;
    if (depth === 0) return true;
  }
  return false;
}

/**
 * The environment name when `lineBefore` (the line up to the cursor) ends with `\begin{name}` plus
 * optional [..]/{..} arguments, outside a comment; otherwise null.
 */
export function beginBeforeCursor(lineBefore) {
  const m = /\\begin\s*\{([^{}]+)\}(?:\s*(?:\[[^\]]*\]|\{[^{}]*\}))*\s*$/.exec(lineBefore);
  if (!m) return null;
  if (/(^|[^\\])%/.test(lineBefore.slice(0, m.index))) return null;
  return m[1].trim();
}
