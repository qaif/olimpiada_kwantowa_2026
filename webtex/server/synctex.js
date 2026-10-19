// Minimal SyncTeX parser for forward (source -> PDF) and inverse (PDF -> source) search.
// Coordinates returned are PDF points (bp) measured from the top-left corner of the page.
import fs from 'node:fs/promises';
import zlib from 'node:zlib';
import { normalizePath } from './logParser.js';

const SP_PER_BP = 65781.76;
const cache = new Map(); // file -> { mtimeMs, data }

export async function loadSynctex(file, roots) {
  const stat = await fs.stat(file);
  const hit = cache.get(file);
  if (hit && hit.mtimeMs === stat.mtimeMs) return hit.data;
  const raw = await fs.readFile(file);
  const text = (file.endsWith('.gz') ? zlib.gunzipSync(raw) : raw).toString('utf8');
  const data = parseSynctex(text, roots);
  cache.set(file, { mtimeMs: stat.mtimeMs, data });
  if (cache.size > 50) cache.delete(cache.keys().next().value);
  return data;
}

export function parseSynctex(text, roots = []) {
  const inputs = new Map(); // tag -> normalized path
  const boxes = []; // { page, tag, line, left, top, width, height, kind }
  let unit = 1;
  let xOffset = 0;
  let yOffset = 0;
  let mag = 1000;
  let page = 0;
  let inContent = false;

  const RECORD = /^([[(hvxkg$])(\d+),(\d+)(?:,-?\d+)?:(-?\d+),(-?\d+)(?::(-?\d+)(?:,(-?\d+),(-?\d+))?)?/;

  for (const line of text.split('\n')) {
    if (!inContent) {
      let m;
      if ((m = /^Input:(\d+):(.*)$/.exec(line))) inputs.set(Number(m[1]), normalizePath(m[2].trim(), roots));
      else if ((m = /^Unit:(\d+)/.exec(line))) unit = Number(m[1]);
      else if ((m = /^X Offset:(-?\d+)/.exec(line))) xOffset = Number(m[1]);
      else if ((m = /^Y Offset:(-?\d+)/.exec(line))) yOffset = Number(m[1]);
      else if ((m = /^Magnification:(\d+)/.exec(line))) mag = Number(m[1]);
      else if (line.startsWith('Content:')) inContent = true;
      continue;
    }
    const c = line[0];
    if (c === '{') {
      page = Number(line.slice(1));
      continue;
    }
    if (c === 'I') {
      // Inputs may also appear inside the content section.
      const m = /^Input:(\d+):(.*)$/.exec(line);
      if (m) inputs.set(Number(m[1]), normalizePath(m[2].trim(), roots));
      continue;
    }
    if (c === 'P') break; // Postamble
    const m = RECORD.exec(line);
    if (!m) continue;
    const scale = (unit * (mag / 1000)) / SP_PER_BP;
    const h = (Number(m[4]) + xOffset) * scale;
    const v = (Number(m[5]) + yOffset) * scale;
    const w = m[6] !== undefined ? Number(m[6]) * scale : 0;
    const height = m[7] !== undefined ? Number(m[7]) * scale : 0;
    const depth = m[8] !== undefined ? Number(m[8]) * scale : 0;
    const kind = c === '(' || c === 'h' ? 'hbox' : c === '[' || c === 'v' ? 'vbox' : 'point';
    boxes.push({
      page,
      tag: Number(m[2]),
      line: Number(m[3]),
      left: h,
      top: v - height,
      width: w,
      height: height + depth,
      kind,
    });
  }
  return { inputs, boxes };
}

/** PDF position -> { file, line } */
export function inverseSearch(data, page, x, y, isProjectFile = () => true) {
  const onPage = data.boxes.filter(
    (b) => b.page === page && b.line > 0 && data.inputs.has(b.tag) && isProjectFile(data.inputs.get(b.tag)),
  );
  let best = null;
  let bestScore = Infinity;
  for (const b of onPage) {
    let score;
    if (b.kind === 'hbox' && x >= b.left && x <= b.left + b.width && y >= b.top && y <= b.top + b.height) {
      score = b.width * b.height * 1e-6; // smallest containing box wins
    } else {
      const dx = Math.max(b.left - x, 0, x - (b.left + b.width));
      const dy = Math.max(b.top - y, 0, y - (b.top + b.height));
      score = 1 + dx * dx + dy * dy * 4;
    }
    if (score < bestScore) {
      bestScore = score;
      best = b;
    }
  }
  return best ? { file: data.inputs.get(best.tag), line: best.line } : null;
}

/** Source position -> [{ page, left, top, width, height }] */
export function forwardSearch(data, file, line) {
  const want = normalizePath(file);
  const tags = [...data.inputs].filter(([, f]) => f === want).map(([t]) => t);
  if (!tags.length) return [];
  const records = data.boxes.filter((b) => tags.includes(b.tag) && b.kind === 'hbox' && b.width > 0);
  if (!records.length) return [];
  // Exact line if present, otherwise the closest line before it (or after it).
  const lines = [...new Set(records.map((r) => r.line))].sort((a, b) => a - b);
  let target = lines.filter((l) => l <= line).at(-1);
  if (target === undefined) target = lines[0];
  const hits = records.filter((r) => r.line === target);
  const page = hits[0].page;
  const onPage = hits.filter((h) => h.page === page);
  // Merge into one rectangle per page for a clean highlight.
  const left = Math.min(...onPage.map((h) => h.left));
  const top = Math.min(...onPage.map((h) => h.top));
  const right = Math.max(...onPage.map((h) => h.left + h.width));
  const bottom = Math.max(...onPage.map((h) => h.top + h.height));
  return [{ page, left, top, width: right - left, height: bottom - top }];
}
