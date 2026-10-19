import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { forwardSearch, inverseSearch, loadSynctex, parseSynctex } from '../synctex.js';

const text = fs.readFileSync(new URL('./fixtures/sample.synctex', import.meta.url), 'utf8');
const data = parseSynctex(text, ['/compile']);
const SP = 65781.76;

const near = (actual, expected, msg) => assert.ok(Math.abs(actual - expected) < 0.01, `${msg}: ${actual} !~ ${expected}`);

test('inputs are normalized relative to the compile root (also inputs declared inside Content)', () => {
  assert.equal(data.inputs.get(1), 'main.tex');
  assert.equal(data.inputs.get(3), 'chapters/intro.tex');
  assert.equal(data.inputs.get(2), '/usr/share/texlive/texmf-dist/tex/latex/base/article.cls');
});

test('records are converted to PDF points with a top-left origin', () => {
  const box = data.boxes.find((b) => b.kind === 'hbox' && b.line === 12);
  assert.equal(box.page, 1);
  near(box.left, 4736286 / SP, 'left');
  near(box.top, (6578178 - 655360) / SP, 'top');
  near(box.width, 25510605 / SP, 'width');
  near(box.height, (655360 + 196608) / SP, 'height');
  // x / k / g / $ records are points; [ / v are vboxes.
  assert.deepEqual(
    [...new Set(data.boxes.map((b) => b.kind))].sort(),
    ['hbox', 'point', 'vbox'],
  );
  // The optional column field ("1,14,3:") is accepted.
  assert.ok(data.boxes.some((b) => b.kind === 'hbox' && b.line === 14));
  // Parsing stops at the postamble.
  assert.ok(data.boxes.every((b) => b.page >= 1 && b.page <= 3));
});

test('magnification, unit and offsets are applied', () => {
  const h = Math.round(SP * 10);
  const v = Math.round(SP * 20);
  const custom = parseSynctex(
    ['Input:1:./a.tex', 'Magnification:2000', 'Unit:1', 'X Offset:65782', 'Y Offset:0', 'Content:', '{1', `(1,1:${h},${v}:${Math.round(SP)},0,0`, ')', '}1'].join('\n'),
  );
  const [b] = custom.boxes;
  near(b.left, ((h + 65782) * 2) / SP, 'left with offset and magnification');
  near(b.top, 40, 'top with magnification');
  assert.equal(custom.inputs.get(1), 'a.tex');
});

test('inverse search: the smallest containing hbox wins', () => {
  assert.deepEqual(inverseSearch(data, 1, 100, 95), { file: 'main.tex', line: 12 });
  assert.deepEqual(inverseSearch(data, 1, 100, 145), { file: 'main.tex', line: 14 });
  assert.deepEqual(inverseSearch(data, 2, 300, 45), { file: 'main.tex', line: 20 });
  assert.deepEqual(inverseSearch(data, 3, 100, 45), { file: 'chapters/intro.tex', line: 2 });
});

test('inverse search falls back to the nearest box and returns null on empty pages', () => {
  // Right of every box on the line-14 baseline → still line 14 (closest).
  assert.deepEqual(inverseSearch(data, 1, 300, 147), { file: 'main.tex', line: 14 });
  assert.equal(inverseSearch(data, 9, 10, 10), null);
});

test('inverse search can be restricted to project files', () => {
  // Without a filter the nearest record wins, even one from a class file.
  assert.equal(inverseSearch(data, 1, 80, 798).file, '/usr/share/texlive/texmf-dist/tex/latex/base/article.cls');
  const projectFiles = new Set(['main.tex', 'chapters/intro.tex']);
  const hit = inverseSearch(data, 1, 80, 798, (f) => projectFiles.has(f));
  assert.equal(hit.file, 'main.tex');
  assert.equal(inverseSearch(data, 1, 80, 798, () => false), null);
});

test('forward search: exact line, previous line, first line fallback', () => {
  const [exact] = forwardSearch(data, 'main.tex', 14);
  assert.equal(exact.page, 1);
  near(exact.top, (9867267 - 655360) / SP, 'top');
  near(exact.width, 12755302 / SP, 'width');

  const [previous] = forwardSearch(data, 'main.tex', 13);
  near(previous.top, (6578178 - 655360) / SP, 'line 13 → line 12');

  const [first] = forwardSearch(data, 'main.tex', 1);
  near(first.top, (6578178 - 655360) / SP, 'before any record → first recorded line');

  const [last] = forwardSearch(data, 'main.tex', 500);
  assert.equal(last.page, 2);
});

test('forward search normalizes the requested path and handles unknown files', () => {
  assert.equal(forwardSearch(data, './main.tex', 12)[0].page, 1);
  assert.equal(forwardSearch(data, 'chapters/intro.tex', 2)[0].page, 3);
  assert.deepEqual(forwardSearch(data, 'nope.tex', 1), []);
});

test('loadSynctex reads gzipped files and caches by mtime', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'webtex-synctex-'));
  try {
    const file = path.join(dir, 'output.synctex.gz');
    fs.writeFileSync(file, zlib.gzipSync(text));
    const a = await loadSynctex(file, ['/compile']);
    const b = await loadSynctex(file, ['/compile']);
    assert.equal(a, b, 'second load is served from cache');
    assert.equal(a.inputs.get(1), 'main.tex');
    assert.equal(a.boxes.length, data.boxes.length);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
