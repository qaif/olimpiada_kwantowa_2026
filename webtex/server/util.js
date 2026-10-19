import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

export const newId = (bytes = 12) => crypto.randomBytes(bytes).toString('base64url');
export const now = () => Date.now();

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const TEXT_EXTENSIONS = new Set([
  'tex', 'ltx', 'latex', 'bib', 'bbl', 'cls', 'sty', 'bst', 'bbx', 'cbx', 'lbx', 'dbx', 'def', 'cfg', 'clo',
  'dtx', 'ins', 'fd', 'tikz', 'pgf', 'asy', 'mp', 'txt', 'md', 'markdown', 'csv', 'tsv', 'dat', 'json',
  'yaml', 'yml', 'xml', 'r', 'rtex', 'rnw', 'py', 'lua', 'sh', 'gitignore', 'latexmkrc', 'ist', 'gls', 'glo',
  'nlo', 'idx', 'lco', 'inc', 'tab', 'gnuplot', 'plt',
]);

export function extOf(name) {
  const base = name.toLowerCase();
  if (base === 'latexmkrc' || base === '.latexmkrc') return 'latexmkrc';
  const i = base.lastIndexOf('.');
  return i >= 0 ? base.slice(i + 1) : '';
}

/** Decides whether an uploaded file becomes an editable (collaborative) document. */
export function isEditableText(name, buffer) {
  if (!TEXT_EXTENSIONS.has(extOf(name))) return false;
  if (buffer.length > 2 * 1024 * 1024) return false;
  if (buffer.includes(0)) return false;
  try {
    new TextDecoder('utf-8', { fatal: true }).decode(buffer);
    return true;
  } catch {
    return false;
  }
}

const INVALID_NAME = /[\\/:*?"<>|\u0000-\u001f]/;
export function assertValidName(name) {
  if (typeof name !== 'string') throw new HttpError(400, 'Invalid name');
  const n = name.trim();
  if (!n || n === '.' || n === '..' || n.length > 150 || INVALID_NAME.test(n)) {
    throw new HttpError(400, `Invalid file name: "${name}"`);
  }
  return n;
}

export async function writeFileAtomic(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${newId(4)}.tmp`;
  await fs.writeFile(tmp, data);
  await fs.rename(tmp, file);
}

export async function exists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

export function parseCookies(header = '') {
  const out = {};
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    if (!k) continue;
    const raw = part.slice(i + 1).trim();
    try {
      out[k] = decodeURIComponent(raw);
    } catch {
      out[k] = raw; // malformed cookie set by another app on the same host
    }
  }
  return out;
}
