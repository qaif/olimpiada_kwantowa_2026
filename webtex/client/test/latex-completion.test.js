// Autocompletion logic of the LaTeX editor (client/src/editor/latex/*): context, project scan,
// option building and ranking. Pure modules, no DOM or CodeMirror needed.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { CATEGORIES, COMMANDS, ENVIRONMENTS, PACKAGES } from '../src/editor/latex/catalog.js';
import {
  analyzeContext,
  beginBeforeCursor,
  isEnvironmentClosed,
  openEnvironments,
  paragraphStart,
} from '../src/editor/latex/context.js';
import {
  ENV_CONTEXT_BOOST,
  MISSING_PACKAGE_BOOST,
  OFF_CONTEXT_BOOST,
  PROJECT_BOOST,
  argumentContext,
  commandOptions,
  envBeginTemplate,
  environmentOptions,
  labelOptions,
  labelPrefix,
  packageOptions,
  renderTemplate,
  userCommandTemplate,
} from '../src/editor/latex/options.js';
import { MAX_USAGE_BOOST, createUsageStore, usageBoost } from '../src/editor/latex/rank.js';
import { mergeSymbols, scanFile, scanSymbols, stripComments } from '../src/editor/latex/scan.js';

const mode = (text) => analyzeContext(text.slice(paragraphStart(text))).mode;
const labels = (opts) => opts.map((o) => o.label);
const find = (opts, label) => opts.find((o) => o.label === label);

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
  };
}

describe('analyzeContext: math or text mode', () => {
  it('detects inline and display math', () => {
    assert.equal(mode(String.raw`Energia $E = \al`), 'math');
    assert.equal(mode(String.raw`Energia $E$ oraz `), 'text');
    assert.equal(mode(String.raw`\( x + `), 'math');
    assert.equal(mode(String.raw`\( x \) i `), 'text');
    assert.equal(mode(String.raw`\[ x = `), 'math');
    assert.equal(mode(String.raw`\[ x \] dalej`), 'text');
    assert.equal(mode('$$ x = '), 'math');
    assert.equal(mode('$$ x $$ dalej'), 'text');
  });

  it('treats math environments as math and \\text{} as text', () => {
    assert.equal(mode('\\begin{equation}\n  \\ket{\\psi} = '), 'math');
    assert.equal(mode('\\begin{align*}\n  a &= b \\\\\n  c &= '), 'math');
    assert.equal(mode('\\begin{equation}\n  x \\quad \\text{dla '), 'text');
    assert.equal(mode('\\begin{equation}\n  x \\text{dla $y$} + '), 'math');
    assert.equal(mode('\\begin{equation} x \\end{equation} Tekst '), 'text');
    assert.equal(mode('\\begin{itemize}\n  \\item '), 'text');
    assert.equal(mode('$\\text{a $b'), 'math');
  });

  it('ignores escaped dollars, comments and verbatim', () => {
    assert.equal(mode(String.raw`Cena 5\$ i `), 'text');
    assert.equal(mode('% $ tu komentarz\nTekst '), 'text');
    assert.equal(analyzeContext('Tekst % uwaga ').inComment, true);
    assert.equal(analyzeContext(String.raw`100\% pewności `).inComment, false);
    assert.equal(analyzeContext('\\begin{verbatim}\n $x').inVerbatim, true);
    assert.equal(mode('\\begin{verbatim} $ \\end{verbatim} tekst'), 'text');
    assert.equal(mode('\\verb|$| tekst'), 'text');
  });

  it('does not carry math mode across a blank line', () => {
    assert.equal(mode('Niedomknięte $x\n\nNowy akapit '), 'text');
  });

  it('reports open environments, innermost last', () => {
    assert.deepEqual(analyzeContext('\\begin{equation}\\begin{pmatrix} a').envs, ['equation', 'pmatrix']);
  });
});

describe('paragraphStart', () => {
  it('starts after the last blank line', () => {
    const text = 'a\n\nb\n  \nc';
    assert.equal(text.slice(paragraphStart(text)), 'c');
    assert.equal(paragraphStart('bez pustych linii'), 0);
  });
  it('looks back at most MODE_WINDOW characters', () => {
    const text = 'x'.repeat(50000);
    assert.equal(paragraphStart(text), 50000 - 20000);
  });
});

describe('openEnvironments / isEnvironmentClosed / beginBeforeCursor', () => {
  it('tracks begin/end pairs and skips comments', () => {
    const text = '\\begin{document}\n\\begin{itemize}\n\\item a\n\\end{itemize}\n% \\begin{figure}\n\\begin{enumerate}\n\\item ';
    assert.deepEqual(openEnvironments(text), ['document', 'enumerate']);
  });
  it('knows whether an environment is already closed after the cursor', () => {
    assert.equal(isEnvironmentClosed('itemize', '\n  \\item a\n\\end{itemize}'), true);
    assert.equal(isEnvironmentClosed('itemize', '\n\\begin{itemize}\\end{itemize}\n'), false);
    assert.equal(isEnvironmentClosed('itemize', '\n% \\end{itemize}\n'), false);
    assert.equal(isEnvironmentClosed('align*', '\n a \\\\ b\n\\end{align*}'), true);
  });
  it('finds a hand-typed \\begin{…} at the end of the line', () => {
    assert.equal(beginBeforeCursor('  \\begin{itemize}'), 'itemize');
    assert.equal(beginBeforeCursor('\\begin{tabular}{c c}'), 'tabular');
    assert.equal(beginBeforeCursor('\\begin{figure}[htbp] '), 'figure');
    assert.equal(beginBeforeCursor('% \\begin{itemize}'), null);
    assert.equal(beginBeforeCursor('\\begin{itemize} tekst'), null);
  });
});

describe('scanFile / mergeSymbols', () => {
  const tex = [
    '\\documentclass{article}',
    '\\usepackage{amsmath, physics}',
    '\\usepackage[polish]{babel}',
    '\\newcommand{\\R}{\\mathbb{R}}',
    '\\newcommand\\avg[1]{\\langle #1 \\rangle}',
    '\\renewcommand{\\vec}[2][x]{\\mathbf{#2}}',
    '\\def\\pair#1#2{(#1,#2)}',
    '\\DeclareMathOperator{\\Tr}{Tr}',
    '\\DeclareMathOperator*{\\argmax}{arg\\,max}',
    '\\DeclarePairedDelimiter\\abs{\\lvert}{\\rvert}',
    '\\NewDocumentCommand{\\op}{m O{1} m}{#1}',
    '\\newenvironment{zadanie}[1]{}{}',
    '\\newtheorem{twierdzenie}{Twierdzenie}',
    '% \\newcommand{\\ukryta}{x}  \\label{eq:ukryta}',
    '\\begin{equation}',
    '  \\ket{\\Phi^+} = \\frac{1}{\\sqrt{2}}',
    '  \\label{eq:bell}',
    '\\end{equation}',
    '\\section{Wstęp}\\label{sec:wstep}',
  ].join('\n');
  const r = scanFile('main.tex', tex);

  it('collects labels with file, line and a readable preview', () => {
    assert.deepEqual(r.labels.map((l) => [l.name, l.line]), [['eq:bell', 17], ['sec:wstep', 19]]);
    assert.equal(r.labels[0].file, 'main.tex');
    assert.match(r.labels[0].preview, /\\ket\{\\Phi\^\+\}/);
    assert.match(r.labels[1].preview, /\\section\{Wstęp\}/);
  });

  it('collects project commands with argument counts', () => {
    const byName = Object.fromEntries(r.commands.map((c) => [c.name, c]));
    assert.deepEqual(Object.keys(byName).sort(), ['R', 'Tr', 'abs', 'argmax', 'avg', 'op', 'pair', 'vec']);
    assert.equal(byName.R.nargs, 0);
    assert.equal(byName.avg.nargs, 1);
    assert.deepEqual([byName.vec.nargs, byName.vec.optional], [1, 1]);
    assert.equal(byName.pair.nargs, 2);
    assert.equal(byName.Tr.math, true);
    assert.equal(byName.abs.math, true);
    assert.equal(byName.abs.nargs, 1);
    assert.deepEqual([byName.op.nargs, byName.op.optional], [2, 1]);
    assert.equal(byName.R.line, 4);
  });

  it('collects environments and packages, ignoring comments', () => {
    assert.deepEqual(r.environments.map((e) => [e.name, e.nargs]), [['zadanie', 1], ['twierdzenie', 0]]);
    assert.deepEqual(r.packages, ['amsmath', 'physics', 'babel']);
    assert.ok(!r.commands.some((c) => c.name === 'ukryta'));
    assert.ok(!r.labels.some((l) => l.name === 'eq:ukryta'));
  });

  it('reads citation keys from .bib files and \\bibitem', () => {
    const bib = scanFile('refs.bib', '@article{nielsen2010,\n title={QC}}\n@string{x = "y"}\n@book{ Preskill ,}');
    assert.deepEqual(bib.citeKeys, ['nielsen2010', 'Preskill']);
    assert.deepEqual(scanFile('a.tex', '\\bibitem[NC]{nc} x').citeKeys, ['nc']);
    assert.deepEqual(scanFile('image.png', '\\label{x}').labels, []);
  });

  it('merges files: first definition wins, lists sorted', () => {
    const merged = mergeSymbols([
      scanFile('b.tex', '\\label{b}\\newcommand{\\x}{1}'),
      scanFile('a.tex', '\\label{a}\\newcommand{\\x}[2]{2}\\usepackage{braket}'),
    ]);
    assert.deepEqual(merged.labels.map((l) => l.name), ['a', 'b']);
    assert.equal(merged.commands.length, 1);
    assert.equal(merged.commands[0].file, 'b.tex');
    assert.deepEqual(merged.packages, ['braket']);
    assert.deepEqual(scanSymbols([['m.tex', '\\label{z}']]).labels.map((l) => l.name), ['z']);
  });

  it('keeps offsets when stripping comments', () => {
    const src = 'a % b\n100\\% c';
    assert.equal(stripComments(src).length, src.length);
    assert.equal(stripComments(src), 'a    \n100\\% c');
  });

  it('scans a large file quickly', () => {
    const chunk = '\\section{Część}\\label{sec:%n}\nTekst $\\ket{\\psi} = \\alpha\\ket{0}$ i \\newcommand{\\c%n}{x}.\n\n';
    const big = Array.from({ length: 20000 }, (_, k) => chunk.replaceAll('%n', String(k).replace(/\d/g, (d) => 'abcdefghij'[d]))).join('');
    const t0 = performance.now();
    const res = scanFile('big.tex', big);
    const ms = performance.now() - t0;
    assert.equal(res.labels.length, 20000);
    assert.equal(res.commands.length, 20000);
    assert.ok(ms < 3000, `scan of ${big.length} chars took ${ms.toFixed(0)} ms`);
  });
});

describe('catalog', () => {
  it('has unique command and environment names with Polish and English descriptions', () => {
    const names = COMMANDS.map((c) => c.name);
    assert.equal(new Set(names).size, names.length, 'duplicate command');
    const envs = ENVIRONMENTS.map((e) => e.name);
    assert.equal(new Set(envs).size, envs.length, 'duplicate environment');
    for (const item of [...COMMANDS, ...ENVIRONMENTS, ...PACKAGES]) {
      assert.ok(item.pl && item.en, `missing description: ${item.name}`);
    }
    for (const c of COMMANDS) {
      assert.ok(CATEGORIES[c.cat], `unknown category ${c.cat}`);
      assert.ok(['math', 'text', 'any'].includes(c.mode), `bad mode for ${c.name}`);
    }
  });

  it('has snippets whose braces balance once placeholders are filled', () => {
    const balance = (s) => {
      let depth = 0;
      for (let k = 0; k < s.length; k++) {
        if (s[k] === '\\') k++;
        else if (s[k] === '{') depth++;
        else if (s[k] === '}' && --depth < 0) return false;
      }
      return depth === 0;
    };
    const fill = (tpl) => tpl.replace(/#\{((?:\\[{}]|[^{}])*)\}/g, (_, n) => n.replace(/\\([{}])/g, '$1') || 'x').replace(/\\([{}])/g, '$1');
    for (const c of COMMANDS) if (c.snippet) assert.ok(balance(fill(c.snippet)), `unbalanced snippet ${c.name}`);
    for (const e of ENVIRONMENTS) assert.ok(balance(fill(`\\begin{${envBeginTemplate(e)}`)), `unbalanced env ${e.name}`);
    assert.ok(!COMMANDS.some((c) => c.snippet?.includes('${')), 'use #{} fields, not ${}');
  });

  it('covers quantum notation', () => {
    for (const name of ['\\ket', '\\bra', '\\braket', '\\expval', '\\mel', '\\comm', '\\dyad', '\\hbar', '\\dagger', '\\otimes', '\\gate']) {
      assert.ok(COMMANDS.some((c) => c.name === name), name);
    }
    assert.equal(COMMANDS.find((c) => c.name === '\\braket').snippet, String.raw`\braket{#{\phi} | #{\psi}}`);
  });
});

describe('commandOptions: context', () => {
  const math = { mode: 'math', envs: ['equation'] };
  const text = { mode: 'text', envs: ['document'] };

  it('offers math symbols in math mode and text commands outside', () => {
    const inMath = labels(commandOptions({ ctx: math }));
    assert.ok(inMath.includes('\\alpha') && inMath.includes('\\ket') && inMath.includes('\\frac'));
    assert.ok(!inMath.includes('\\section') && !inMath.includes('\\textbf'));
    const inText = labels(commandOptions({ ctx: text }));
    assert.ok(inText.includes('\\section') && inText.includes('\\textbf'));
    assert.ok(!inText.includes('\\alpha') && !inText.includes('\\frac'));
    assert.ok(inText.includes('\\label') && inMath.includes('\\label'), 'any-mode commands in both');
  });

  it('adds the other mode with a penalty when asked', () => {
    const opts = commandOptions({ ctx: text, includeOffContext: true });
    const alpha = find(opts, '\\alpha');
    assert.equal(alpha.boost, OFF_CONTEXT_BOOST);
    assert.equal(alpha.info.offContext, true);
    assert.equal(find(opts, '\\section').info.offContext, false);
  });

  it('offers \\item and circuit commands inside their environments only', () => {
    assert.ok(!labels(commandOptions({ ctx: text })).includes('\\item'));
    const inList = commandOptions({ ctx: { mode: 'text', envs: ['document', 'itemize'] } });
    assert.ok(find(inList, '\\item').boost >= ENV_CONTEXT_BOOST);
    assert.ok(labels(commandOptions({ ctx: { mode: 'text', envs: ['quantikz'] } })).includes('\\gate'));
    assert.ok(!labels(commandOptions({ ctx: math })).includes('\\gate'));
  });

  it('describes in Polish by default and in English on request', () => {
    assert.equal(find(commandOptions({ ctx: math }), '\\frac').detail, 'ułamek a/b');
    assert.equal(find(commandOptions({ ctx: math, lang: 'en' }), '\\frac').detail, 'fraction a/b');
    const ket = find(commandOptions({ ctx: math }), '\\ket');
    assert.equal(ket.info.syntax, '\\ket{⟨\\psi⟩}');
    assert.equal(ket.info.category, 'fizyka kwantowa');
  });

  it('includes project commands as snippets and lets them override the catalog', () => {
    const symbols = scanSymbols([['m.tex', '\\newcommand{\\R}{\\mathbb{R}}\\newcommand{\\vec}[1]{\\mathbf{#1}}\\DeclareMathOperator{\\Tr}{Tr}']]);
    const inMath = commandOptions({ ctx: math, symbols });
    const vec = inMath.filter((o) => o.label === '\\vec');
    assert.equal(vec.length, 1);
    assert.equal(vec[0].template, '\\vec{#{}}');
    assert.equal(vec[0].info.definedAt.file, 'm.tex');
    assert.equal(find(inMath, '\\R').boost, PROJECT_BOOST);
    assert.equal(find(inMath, '\\R').template, null);
    assert.ok(find(inMath, '\\Tr'));
    // \DeclareMathOperator commands belong to math mode.
    assert.ok(!labels(commandOptions({ ctx: text, symbols })).includes('\\Tr'));
    assert.ok(labels(commandOptions({ ctx: text, symbols })).includes('\\R'));
  });

  it('ranks by usage and lowers commands from packages the project does not load', () => {
    const usage = { boost: (id) => (id === 'cmd:\\beta' ? 30 : 0) };
    assert.equal(find(commandOptions({ ctx: math, usage }), '\\beta').boost, 30);
    const symbols = { packages: ['amsmath'] };
    assert.equal(find(commandOptions({ ctx: math, symbols }), '\\dv').boost, MISSING_PACKAGE_BOOST);
    assert.equal(find(commandOptions({ ctx: math, symbols }), '\\dv').info.pkgLoaded, false);
    assert.equal(find(commandOptions({ ctx: math, symbols: { packages: ['physics'] } }), '\\dv').boost, 0);
    assert.equal(find(commandOptions({ ctx: math, symbols: { packages: ['braket'] } }), '\\ket').info.pkgLoaded, true);
    assert.equal(find(commandOptions({ ctx: math }), '\\dv').info.pkgLoaded, null, 'unknown when no preamble is seen');
  });
});

describe('environmentOptions', () => {
  it('closes \\begin snippets with the matching \\end', () => {
    const opts = environmentOptions({ ctx: { mode: 'text', envs: [] } });
    const itemize = find(opts, 'itemize');
    assert.equal(itemize.template, 'itemize}\n\t\\item #{}\n\\end{itemize}');
    assert.equal(find(opts, 'figure').template.split('\n')[0], 'figure}[htbp]');
    assert.equal(find(opts, 'pmatrix').boost, OFF_CONTEXT_BOOST);
    assert.equal(find(opts, 'equation').boost, 0);
  });

  it('prefers matrices and cases inside math', () => {
    const opts = environmentOptions({ ctx: { mode: 'math', envs: ['equation'] } });
    assert.equal(find(opts, 'pmatrix').boost, 0);
    assert.equal(find(opts, 'align').boost, OFF_CONTEXT_BOOST);
  });

  it('suggests closing the innermost open environment after \\end{', () => {
    const opts = environmentOptions({ kind: 'end', ctx: { mode: 'text' }, open: ['document', 'zadanie', 'itemize'] });
    assert.equal(find(opts, 'itemize').boost, 99);
    assert.equal(find(opts, 'zadanie').boost, 89);
    assert.ok(find(opts, 'document').boost < find(opts, 'zadanie').boost);
    assert.equal(find(opts, 'itemize').template, null);
  });

  it('includes project environments with argument fields', () => {
    const symbols = scanSymbols([['m.tex', '\\newenvironment{zadanie}[1]{}{}']]);
    const z = find(environmentOptions({ ctx: { mode: 'text' }, symbols }), 'zadanie');
    assert.equal(z.template, 'zadanie}{#{}}\n\t#{}\n\\end{zadanie}');
    assert.equal(z.boost, PROJECT_BOOST);
  });
});

describe('labels, packages, arguments', () => {
  const symbols = scanSymbols([['m.tex', '\\label{eq:bell}\n\\label{fig:obwod}\n\\label{sec:wstep}']]);

  it('prefers equation labels for \\eqref', () => {
    const eq = labelOptions({ cmd: 'eqref', symbols });
    assert.ok(find(eq, 'eq:bell').boost > find(eq, 'fig:obwod').boost);
    assert.equal(find(eq, 'eq:bell').detail, 'm.tex:1');
    assert.equal(find(labelOptions({ cmd: 'ref', symbols }), 'fig:obwod').boost, 0);
  });

  it('suggests a label prefix from the environment', () => {
    assert.equal(labelPrefix(['document', 'equation']), 'eq:');
    assert.equal(labelPrefix(['figure', 'center']), 'fig:');
    assert.equal(labelPrefix(['document', 'table', 'tabular']), 'tab:');
    assert.equal(labelPrefix(['document']), 'sec:');
  });

  it('lists packages with descriptions, loaded ones last', () => {
    const opts = packageOptions({ symbols: { packages: ['physics'] } });
    assert.equal(find(opts, 'braket').detail, 'notacja Diraca: \\bra, \\ket, \\braket');
    assert.ok(find(opts, 'physics').boost < find(opts, 'braket').boost);
    assert.equal(find(opts, 'physics').loaded, true);
  });

  it('parses the argument being typed', () => {
    assert.deepEqual(argumentContext('Zob. \\eqref{eq:a, eq:b'), { cmd: 'eqref', content: 'eq:a, eq:b', word: 'eq:b' });
    assert.deepEqual(argumentContext('\\usepackage[utf8]{amsmath,phy'), { cmd: 'usepackage', content: 'amsmath,phy', word: 'phy' });
    assert.equal(argumentContext('\\begin{ali').cmd, 'begin');
    assert.equal(argumentContext('\\section*{Tytuł').cmd, 'section');
    assert.equal(argumentContext('zwykły tekst'), null);
  });

  it('renders snippet templates for the info panel', () => {
    assert.equal(renderTemplate('\\frac{#{a}}{#{b}}'), '\\frac{⟨a⟩}{⟨b⟩}');
    assert.equal(renderTemplate(String.raw`\left\\{ #{} \right\\}`), '\\left\\{ ⟨…⟩ \\right\\}');
    assert.equal(userCommandTemplate({ name: 'avg', nargs: 1 }), '\\avg{#{}}');
    assert.equal(userCommandTemplate({ name: 'vec', nargs: 1, optional: 1 }), '\\vec[#{}]{#{}}');
    assert.equal(userCommandTemplate({ name: 'R', nargs: 0 }), null);
  });
});

describe('usage ranking', () => {
  const day = 24 * 3600 * 1000;

  it('grows with use, fades with time and is capped', () => {
    const now = 1e12;
    assert.equal(usageBoost(undefined, now), 0);
    const once = usageBoost({ n: 1, t: now }, now);
    const often = usageBoost({ n: 20, t: now }, now);
    assert.ok(once > 0 && often > once);
    assert.ok(usageBoost({ n: 20, t: now - 14 * day }, now) < often / 2);
    assert.ok(usageBoost({ n: 20, t: now - 90 * day }, now) <= 1);
    assert.equal(usageBoost({ n: 1e6, t: now }, now), MAX_USAGE_BOOST);
  });

  it('persists picks in storage and survives broken storage', () => {
    let now = 1e12;
    const storage = memoryStorage();
    const store = createUsageStore({ storage, now: () => now });
    store.record('cmd:\\ket');
    store.record('cmd:\\ket');
    assert.equal(store.get('cmd:\\ket').n, 2);
    const reloaded = createUsageStore({ storage, now: () => now });
    assert.ok(reloaded.boost('cmd:\\ket') > reloaded.boost('cmd:\\bra'));

    const broken = createUsageStore({ storage: memoryStorage({ 'webtex:completion-usage': '{oops' }) });
    broken.record('x');
    assert.equal(broken.get('x').n, 1);
    const throwing = {
      getItem() {
        throw new Error('blocked');
      },
      setItem() {
        throw new Error('blocked');
      },
    };
    assert.doesNotThrow(() => createUsageStore({ storage: throwing }).record('x'));
  });

  it('keeps the stored history bounded', () => {
    const storage = memoryStorage();
    let now = 1e12;
    const store = createUsageStore({ storage, now: () => now });
    for (let k = 0; k < 450; k++) {
      now += 1000;
      store.record(`cmd:\\c${k}`);
    }
    assert.equal(Object.keys(JSON.parse(storage.data['webtex:completion-usage'])).length, 400);
    assert.ok(store.get('cmd:\\c449'), 'newest entry kept');
  });
});
