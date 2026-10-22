import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diffLines, diffStats, splitLines, textEdits, unifiedDiff } from '../linediff.js';

const apply = (a, edits) => {
  let s = a;
  for (const e of [...edits].reverse()) s = s.slice(0, e.index) + e.insert + s.slice(e.index + e.remove);
  return s;
};

test('splitLines keeps line endings, so joining gives the text back', () => {
  assert.deepEqual(splitLines(''), []);
  assert.deepEqual(splitLines('a'), ['a']);
  assert.deepEqual(splitLines('a\n'), ['a\n']);
  assert.deepEqual(splitLines('a\n\nb'), ['a\n', '\n', 'b']);
  const text = 'x\r\ny\n\nz';
  assert.equal(splitLines(text).join(''), text);
});

test('textEdits turns any text into any other (random round trips)', () => {
  let seed = 7;
  const rnd = (n) => ((seed = (seed * 1103515245 + 12345) % 2 ** 31), seed % n);
  const piece = ['a\n', 'b\n', 'c\n', 'ł\n', '\n', 'end', 'x\r\n'];
  const text = () => Array.from({ length: rnd(40) }, () => piece[rnd(piece.length)]).join('');
  for (let i = 0; i < 2000; i++) {
    const a = text();
    const b = text();
    assert.equal(apply(a, textEdits(a, b)), b, JSON.stringify([a, b]));
  }
});

test('edits stay minimal: only the changed lines are touched', () => {
  const a = 'one\ntwo\nthree\nfour\n';
  const b = 'one\nTWO\nthree\nfour\nfive\n';
  assert.deepEqual(textEdits(a, b), [
    { index: 4, remove: 4, insert: 'TWO\n' },
    { index: 19, remove: 0, insert: 'five\n' },
  ]);
  assert.deepEqual(diffStats(a, b), { added: 2, removed: 1 });
  assert.deepEqual(textEdits(a, a), []);
});

test('very different long texts fall back to one replaced block', () => {
  const a = Array.from({ length: 5000 }, (_, i) => `a${i}\n`);
  const b = Array.from({ length: 5000 }, (_, i) => `b${i}\n`);
  assert.deepEqual(diffLines(a, b), [{ aStart: 0, aEnd: 5000, bStart: 0, bEnd: 5000 }]);
});

test('unifiedDiff groups hunks with context and line numbers', () => {
  const a = '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n';
  const b = '1\n2\nX\n4\n5\n6\n7\n8\n9\n10\nY\n';
  const groups = unifiedDiff(a, b, 1);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    groups[0].lines.map((l) => [l.type, l.text, l.a, l.b]),
    [
      [' ', '2\n', 2, 2],
      ['-', '3\n', 3, null],
      ['+', 'X\n', null, 3],
      [' ', '4\n', 4, 4],
    ],
  );
  assert.deepEqual(
    groups[1].lines.map((l) => [l.type, l.text]),
    [
      [' ', '10\n'],
      ['+', 'Y\n'],
    ],
  );
  assert.deepEqual(unifiedDiff(a, a), []);
});
