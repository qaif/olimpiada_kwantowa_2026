import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { normalizePath, parseLatexmkOutput, parseLog } from '../logParser.js';

const fixture = (name) => fs.readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8');
const pick = (issues) => issues.map(({ level, file, line, message }) => ({ level, file, line, message }));

test('normalizePath strips compile roots, ./ prefixes and Windows separators', () => {
  assert.equal(normalizePath('/compile/./main.tex', ['/compile']), 'main.tex');
  assert.equal(normalizePath('./chapters/./intro.tex'), 'chapters/intro.tex');
  assert.equal(normalizePath('C:\\Data\\compiles\\abc\\sub\\a.tex', ['c:\\data\\compiles\\abc']), 'sub/a.tex');
  assert.equal(normalizePath('/usr/share/texlive/article.cls', ['/compile']), '/usr/share/texlive/article.cls');
});

test('file-line-error errors carry file, line and context; LaTeX Error prefix is removed', () => {
  const log = [
    '(./main.tex',
    'LaTeX2e <2022-11-01> patch level 1',
    '(/usr/share/texlive/texmf-dist/tex/latex/base/article.cls',
    'Document Class: article 2022/07/02 v1.4n Standard LaTeX document class',
    '(/usr/share/texlive/texmf-dist/tex/latex/base/size10.clo))',
    './main.tex:12: Undefined control sequence.',
    'l.12 \\foo',
    '          (unbalanced paren in source',
    '',
    './main.tex:20: LaTeX Error: File `missing.png\' not found.',
    '',
    'See the LaTeX manual or LaTeX Companion for explanation.',
    '',
    '/compile/chapters/intro.tex:3: Missing $ inserted.',
    'l.3 a^',
    '      b',
    ')',
  ].join('\n');
  const issues = parseLog(log, { roots: ['/compile'] });
  assert.deepEqual(pick(issues), [
    { level: 'error', file: 'main.tex', line: 12, message: 'Undefined control sequence.' },
    { level: 'error', file: 'main.tex', line: 20, message: "File `missing.png' not found." },
    { level: 'error', file: 'chapters/intro.tex', line: 3, message: 'Missing $ inserted.' },
  ]);
  assert.match(issues[0].content, /^l\.12 \\foo/);
});

test('"!" errors use the current file from the stack and the l.N line', () => {
  const log = [
    '(./main.tex (./main.aux)',
    '! Missing $ inserted.',
    '<inserted text> ',
    '                $',
    'l.7 a^',
    '      b)',
    '',
    'LaTeX Warning: Reference `x\' on page 1 undefined on input line 9.',
    ')',
  ].join('\n');
  const issues = pick(parseLog(log));
  assert.deepEqual(issues[0], { level: 'error', file: 'main.tex', line: 7, message: 'Missing $ inserted.' });
  // The ")" in the skipped source-context line must not pop main.tex off the stack.
  assert.deepEqual(issues[1], {
    level: 'warning',
    file: 'main.tex',
    line: 9,
    message: "Reference `x' on page 1 undefined on input line 9.",
  });
});

test('"!" errors inside system files have no project file', () => {
  const log = [
    '(./main.tex',
    '(/usr/share/texlive/texmf-dist/tex/latex/pgf/frontendlayer/tikz.sty',
    '! Package pgfkeys Error: I do not know the key \'/tikz/foo\'.',
    '',
    '))',
  ].join('\n');
  const [issue] = parseLog(log);
  assert.equal(issue.level, 'error');
  assert.equal(issue.file, null);
  assert.equal(issue.line, null);
  assert.match(issue.message, /pgfkeys Error/);
});

test('LaTeX and Package warnings, including (pkg) continuation lines', () => {
  const log = [
    '(./main.tex',
    'Package hyperref Warning: Token not allowed in a PDF string (Unicode):',
    "(hyperref)                removing `math shift' on input line 20.",
    '',
    'LaTeX Warning: Citation `knuth\' on page 1 undefined on input line 25.',
    '',
    'LaTeX Warning: There were undefined references.',
    ')',
  ].join('\n');
  assert.deepEqual(pick(parseLog(log)), [
    {
      level: 'warning',
      file: 'main.tex',
      line: 20,
      message: "Package hyperref: Token not allowed in a PDF string (Unicode): removing `math shift' on input line 20.",
    },
    { level: 'warning', file: 'main.tex', line: 25, message: "Citation `knuth' on page 1 undefined on input line 25." },
    { level: 'warning', file: 'main.tex', line: null, message: 'There were undefined references.' },
  ]);
});

test('warnings raised while a system file is open have no project file', () => {
  const log = [
    '(./main.tex',
    '(/usr/share/texlive/texmf-dist/tex/latex/hyperref/hyperref.sty',
    'Package hyperref Warning: Option `pdfborder\' has already been used.',
    '',
    ')',
    ')',
  ].join('\n');
  const [issue] = parseLog(log);
  assert.equal(issue.file, null);
  assert.equal(issue.level, 'warning');
});

test('LaTeX Font warnings are classified as typesetting issues', () => {
  const log = ['(./main.tex', "LaTeX Font Warning: Font shape `OT1/cmr/bx/sc' undefined", ')'].join('\n');
  const [issue] = parseLog(log);
  assert.equal(issue.level, 'typesetting');
  assert.equal(issue.file, 'main.tex');
});

test('over/underfull boxes become typesetting issues with the first line', () => {
  const log = [
    '(./main.tex',
    'Overfull \\hbox (11.39728pt too wide) in paragraph at lines 21--23',
    '[]\\T1/lmr/m/n/10.95 Some long text',
    '',
    'Underfull \\hbox (badness 10000) in paragraph at lines 30--30',
    '',
    'Underfull \\vbox (badness 10000) has occurred while \\output is active []',
    '',
    'Overfull \\hbox (3.0pt too wide) detected at line 44',
    ')',
  ].join('\n');
  assert.deepEqual(
    parseLog(log).map((i) => [i.level, i.file, i.line]),
    [
      ['typesetting', 'main.tex', 21],
      ['typesetting', 'main.tex', 30],
      ['typesetting', 'main.tex', null],
      ['typesetting', 'main.tex', 44],
    ],
  );
});

test('file stack tracks nested project files and returns to the parent', () => {
  const issues = parseLog(fixture('nested.log'), { roots: ['/compile'] });
  assert.deepEqual(
    issues.map((i) => [i.level, i.file, i.line]),
    [
      ['warning', null, null], // "No file output.bbl."
      ['warning', 'chapters/introduction.tex', 3],
      ['typesetting', 'chapters/introduction.tex', 7],
      ['warning', 'main.tex', 40],
    ],
  );
});

test('duplicate messages are reported once', () => {
  const w = "LaTeX Warning: Reference `a' on page 1 undefined on input line 5.";
  const log = ['(./main.tex', w, '', w, '', ')'].join('\n');
  assert.equal(parseLog(log).length, 1);
});

test('missing .bbl/.ind files are reported as warnings', () => {
  const [issue] = parseLog('No file output.bbl.\n');
  assert.deepEqual(pick([issue]), [{ level: 'warning', file: null, line: null, message: 'No file output.bbl.' }]);
});

test('CRLF logs are handled', () => {
  const log = "(./main.tex\r\n./main.tex:3: Undefined control sequence.\r\nl.3 \\x\r\n\r\n)\r\n";
  assert.deepEqual(pick(parseLog(log)), [{ level: 'error', file: 'main.tex', line: 3, message: 'Undefined control sequence.' }]);
});

test('parseLatexmkOutput picks up biber/bibtex problems', () => {
  const out = [
    'Latexmk: applying rule \'biber output\'...',
    'ERROR - Cannot find \'output.bcf\'!',
    'Biber error: something went wrong',
    "I couldn't open database file refs.bib",
    "Latexmk: Missing input file 'output.toc' (or dependence on it) from following:",
    'Latexmk: All targets (output.pdf) are up-to-date',
  ].join('\n');
  assert.deepEqual(
    parseLatexmkOutput(out).map((i) => i.level),
    ['error', 'error', 'warning', 'warning'],
  );
});

// ---- Regression tests for previously reported parser bugs ----

test(
  'text of an overfull box line ("[]..." with a stray ")") must not pop the file stack',
  () => {
    const log = [
      '(./main.tex',
      'Overfull \\hbox (5.0pt too wide) in paragraph at lines 10--10',
      '[]\\T1/cmr/m/n/10 1) first item',
      '',
      "LaTeX Warning: Reference `a' on page 1 undefined on input line 30.",
      ')',
    ].join('\n');
    const warning = parseLog(log).find((i) => i.level === 'warning');
    assert.equal(warning.file, 'main.tex');
  },
);

test(
  'Class/Font warning continuation lines "(article)" / "(Font)" are joined and give the line number',
  () => {
    const log = [
      '(./main.tex',
      "LaTeX Font Warning: Font shape `OT1/cmr/bx/sc' undefined",
      "(Font)              using `OT1/cmr/bx/n' instead on input line 9.",
      '',
      ')',
    ].join('\n');
    const [issue] = parseLog(log);
    assert.equal(issue.line, 9);
  },
);
