import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HttpError, assertValidName, extOf, isEditableText, newId, parseCookies } from '../util.js';

test('assertValidName trims and accepts normal names', () => {
  assert.equal(assertValidName('main.tex'), 'main.tex');
  assert.equal(assertValidName('  Rozdział 1.tex '), 'Rozdział 1.tex');
  assert.equal(assertValidName('.latexmkrc'), '.latexmkrc');
});

test('assertValidName rejects dangerous or empty names with HTTP 400', () => {
  for (const bad of ['', '   ', '.', '..', 'a/b.tex', 'a\\b.tex', 'c:x', 'what?.tex', 'x\u0000y', 'x'.repeat(151), 42, null]) {
    assert.throws(
      () => assertValidName(bad),
      (err) => err instanceof HttpError && err.status === 400,
      `should reject ${JSON.stringify(bad)}`,
    );
  }
});

test('extOf handles case and latexmkrc', () => {
  assert.equal(extOf('Main.TEX'), 'tex');
  assert.equal(extOf('archive.tar.gz'), 'gz');
  assert.equal(extOf('latexmkrc'), 'latexmkrc');
  assert.equal(extOf('.latexmkrc'), 'latexmkrc');
  assert.equal(extOf('Makefile'), '');
});

test('isEditableText: text extensions with valid UTF-8 become docs', () => {
  const utf8 = Buffer.from('\\section{Zażółć gęślą jaźń}', 'utf8');
  assert.equal(isEditableText('main.tex', utf8), true);
  assert.equal(isEditableText('refs.BIB', utf8), true);
  assert.equal(isEditableText('latexmkrc', Buffer.from('$pdf_mode = 1;')), true);
});

test('isEditableText: binaries, NUL bytes, invalid UTF-8 and huge files stay blobs', () => {
  assert.equal(isEditableText('figure.png', Buffer.from('not really a png')), false);
  assert.equal(isEditableText('main.tex', Buffer.from([0x61, 0x00, 0x62])), false);
  assert.equal(isEditableText('main.tex', Buffer.from([0xff, 0xfe, 0x41])), false);
  assert.equal(isEditableText('big.tex', Buffer.alloc(2 * 1024 * 1024 + 1, 0x61)), false);
});

test('parseCookies parses and decodes values', () => {
  assert.deepEqual(parseCookies('a=1; b=hello%20world; junk; c=x=y'), { a: '1', b: 'hello world', c: 'x=y' });
  assert.deepEqual(parseCookies(''), {});
  assert.deepEqual(parseCookies(), {});
});

test(
  'parseCookies tolerates malformed percent-encoding from other apps on the same host',
  () => {
    assert.doesNotThrow(() => parseCookies('other=%E0%A4%A; webtex_sid=abc'));
  },
);

test('newId produces url-safe random ids', () => {
  const ids = new Set(Array.from({ length: 200 }, () => newId()));
  assert.equal(ids.size, 200);
  for (const id of ids) assert.match(id, /^[A-Za-z0-9_-]{16}$/);
  assert.equal(newId(32).length, 43);
});
