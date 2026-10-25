// Line diff shared by the server (restoring a version as a minimal edit of the live Y.Text) and the
// client (the "History" panel's unified diff). Myers' O(ND) algorithm on whole lines, after trimming
// the common prefix and suffix; very different texts (D above MAX_D) fall back to one replaced block.
// No dependencies: imported by Node and bundled by Vite.

const MAX_D = 2000;

/** Splits text into lines that keep their "\n", so lines.join('') === text. */
export function splitLines(text) {
  if (!text) return [];
  const lines = text.split('\n');
  const out = lines.slice(0, -1).map((l) => `${l}\n`);
  if (lines[lines.length - 1] !== '') out.push(lines[lines.length - 1]);
  return out;
}

/**
 * Hunks turning a into b: [{ aStart, aEnd, bStart, bEnd }] (line ranges, end exclusive), meaning
 * a.slice(aStart, aEnd) is replaced by b.slice(bStart, bEnd). Sorted, non-overlapping.
 */
export function diffLines(a, b) {
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const A = a.slice(pre, a.length - suf);
  const B = b.slice(pre, b.length - suf);
  if (!A.length && !B.length) return [];
  const edits = myers(A, B);
  const whole = [{ aStart: pre, aEnd: pre + A.length, bStart: pre, bEnd: pre + B.length }];
  if (!edits) return whole;
  // edits: list of [type, aIndex, bIndex] with type '=' | '-' | '+', in order; group the runs.
  const hunks = [];
  let cur = null;
  for (const [type, i, j] of edits) {
    if (type === '=') {
      if (cur) hunks.push(cur);
      cur = null;
      continue;
    }
    if (!cur) cur = { aStart: pre + i, aEnd: pre + i, bStart: pre + j, bEnd: pre + j };
    if (type === '-') cur.aEnd = pre + i + 1;
    else cur.bEnd = pre + j + 1;
  }
  if (cur) hunks.push(cur);
  return hunks;
}

function myers(a, b) {
  // Lines as small integers: comparisons in the hot loop are then cheap.
  const ids = new Map();
  const id = (s) => {
    let v = ids.get(s);
    if (v === undefined) ids.set(s, (v = ids.size));
    return v;
  };
  const A = Int32Array.from(a, id);
  const B = Int32Array.from(b, id);
  const n = A.length;
  const m = B.length;
  const offset = n + m + 1;
  const v = new Int32Array(2 * offset + 1);
  // trace[d] = the k-range [-d-1, d+1] of v before step d (memory O(D²), hence MAX_D).
  const trace = [];
  for (let d = 0; d <= n + m; d++) {
    if (d > MAX_D) return null;
    trace.push(v.slice(offset - d - 1, offset + d + 2));
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]) ? v[offset + k + 1] : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && A[x] === B[y]) {
        x++;
        y++;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) return backtrack(trace, n, m);
    }
  }
  return null;
}

function backtrack(trace, n, m) {
  const edits = [];
  let x = n;
  let y = m;
  for (let d = trace.length - 1; d > 0; d--) {
    const row = trace[d];
    const at = (k) => row[k + d + 1];
    const k = x - y;
    const prevK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const prevX = at(prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) edits.push(['=', --x, --y]);
    if (x === prevX) edits.push(['+', x, --y]);
    else edits.push(['-', --x, y]);
  }
  while (x > 0 && y > 0) edits.push(['=', --x, --y]);
  return edits.reverse();
}

/**
 * Character-level edits turning text a into text b, ready to apply back to front:
 * [{ index, remove, insert }] with ascending index (positions in a).
 */
export function textEdits(a, b) {
  const la = splitLines(a);
  const lb = splitLines(b);
  const starts = [0];
  for (const l of la) starts.push(starts[starts.length - 1] + l.length);
  return diffLines(la, lb).map((h) => ({
    index: starts[h.aStart],
    remove: starts[h.aEnd] - starts[h.aStart],
    insert: lb.slice(h.bStart, h.bEnd).join(''),
  }));
}

/**
 * Unified diff for display: [{ aStart, bStart, lines: [{ type: ' ' | '-' | '+', text, a, b }] }],
 * line numbers 1-based (a/b null where the line does not exist on that side).
 */
export function unifiedDiff(a, b, context = 3) {
  const la = splitLines(a);
  const lb = splitLines(b);
  const hunks = diffLines(la, lb);
  const groups = [];
  let g = null;
  for (const h of hunks) {
    const from = Math.max(0, h.aStart - context);
    if (!g || from > g.aEnd + context) {
      if (g) groups.push(g);
      g = { aStart: from, bStart: h.bStart - (h.aStart - from), aEnd: from, bEnd: h.bStart - (h.aStart - from), lines: [] };
    }
    // Context between the previous hunk (or group start) and this one.
    for (let i = g.aEnd, j = g.bEnd; i < h.aStart; i++, j++) g.lines.push({ type: ' ', text: la[i], a: i + 1, b: j + 1 });
    for (let i = h.aStart; i < h.aEnd; i++) g.lines.push({ type: '-', text: la[i], a: i + 1, b: null });
    for (let j = h.bStart; j < h.bEnd; j++) g.lines.push({ type: '+', text: lb[j], a: null, b: j + 1 });
    g.aEnd = h.aEnd;
    g.bEnd = h.bEnd;
  }
  if (g) groups.push(g);
  for (const gr of groups) {
    const stop = Math.min(la.length, gr.aEnd + context);
    for (let i = gr.aEnd, j = gr.bEnd; i < stop; i++, j++) gr.lines.push({ type: ' ', text: la[i], a: i + 1, b: j + 1 });
    delete gr.aEnd;
    delete gr.bEnd;
  }
  return groups;
}

/** { added, removed } line counts between two texts. */
export function diffStats(a, b) {
  let added = 0;
  let removed = 0;
  for (const h of diffLines(splitLines(a), splitLines(b))) {
    added += h.bEnd - h.bStart;
    removed += h.aEnd - h.aStart;
  }
  return { added, removed };
}
