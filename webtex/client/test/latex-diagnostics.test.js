// Problemy w kodzie i pisanie (client/src/editor/latex/*): struktura dokumentu, szybkie sprawdzanie,
// mapowanie problemów z kompilacji na linie, licznik słów, język dokumentu, makra podglądu wzorów.
// Czyste moduły, bez DOM i CodeMirror.
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import katex from 'katex';
import { analyzeStructure, blankNonCode, mathRegionAt } from '../src/editor/latex/structure.js';
import { lintLatex, packageRuleApplies, scanLintInfo, usepackageInsertion, withImplied } from '../src/editor/latex/lint.js';
import {
  anchorLine,
  compileDiagnostics,
  countIssuesByFile,
  issueColumns,
  issueMatchesFile,
} from '../src/editor/latex/diagnostics.js';
import { countWords, documentLanguage, noSpellRanges, readableText } from '../src/editor/latex/prose.js';
import { collectMacros, mathSource, previewMacros } from '../src/editor/latex/macros.js';

const r = String.raw;
const codes = (problems) => problems.map((p) => p.code);
const at = (text, problems) => problems.map((p) => [p.code, text.slice(p.from, p.to)]);

const doc = (body, preamble = r`\usepackage{amsmath}`) => r`\documentclass{article}
${preamble}
\begin{document}
${body}
\end{document}
`;

describe('analyzeStructure: klamry, otoczenia, wzory', () => {
  it('poprawny dokument nie ma problemów', () => {
    const text = doc(r`Tekst $x^2$ i \(y\), \[ \frac{a}{b} \]
\begin{equation} E = mc^2 \label{eq:e} \end{equation}
\begin{itemize} \item {\bfseries a} \item b \end{itemize}
Koszt: 5\$ i 10\% oraz \{ \} \verb|{$| \url{http://a.pl/%7Ex#y}`);
    assert.deepEqual(analyzeStructure(text).problems, []);
  });

  it('nadmiarowa i niezamknięta klamra', () => {
    const text = doc('a } b\n\\textbf{c');
    assert.deepEqual(at(text, analyzeStructure(text).problems), [
      ['brace-close', '}'],
      ['brace-open', '{'],
    ]);
  });

  it('otoczenia: niezamknięte, złe \\end, \\end bez \\begin — bez zgłoszeń kaskadowych', () => {
    const t1 = doc(r`\begin{itemize} \item a \end{enumerate}`);
    assert.deepEqual(at(t1, analyzeStructure(t1).problems), [['env-mismatch', r`\end{enumerate}`]]);
    assert.deepEqual(analyzeStructure(t1).problems[0].params, { name: 'enumerate', open: 'itemize' });

    const t2 = doc(r`\begin{center} \begin{tabular}{cc} a & b \end{center}`);
    const p2 = analyzeStructure(t2).problems;
    assert.deepEqual(at(t2, p2), [['env-unclosed', r`\begin{tabular}`]]);
    assert.equal(p2[0].params.before, 'center');

    const t3 = r`\end{itemize}`;
    assert.deepEqual(codes(analyzeStructure(t3).problems), ['env-end-unmatched']);

    const t4 = r`\documentclass{article}\begin{document} tekst`;
    assert.deepEqual(at(t4, analyzeStructure(t4).problems), [['env-unclosed', r`\begin{document}`]]);
  });

  it('wzory: niezamknięty $, pusta linia, $ we wzorze wyświetlanym, \\] bez \\[', () => {
    const t1 = doc('Tekst $x^2 bez końca\n\nNowy akapit.');
    assert.deepEqual(at(t1, analyzeStructure(t1).problems), [['math-unclosed', '$']]);

    const t2 = doc(r`\begin{equation}
a = b

c = d
\end{equation}`);
    assert.deepEqual(codes(analyzeStructure(t2).problems), ['math-blank-line']);

    const t3 = doc(r`\[ a $ b \]`);
    assert.deepEqual(at(t3, analyzeStructure(t3).problems), [['dollar-in-math', '$']]);

    const t4 = doc(r`a \] b`);
    assert.deepEqual(codes(analyzeStructure(t4).problems), ['math-close-unmatched']);

    const t5 = doc(r`$\begin{equation} x \end{equation}$`);
    assert.deepEqual(codes(analyzeStructure(t5).problems), ['math-nested']);

    const t6 = doc(r`\begin{align} a &= b \end{align*}`);
    assert.ok(codes(analyzeStructure(t6).problems).includes('env-mismatch'));
  });

  it('$$…$$ daje tylko podpowiedź; $x$$y$ to dwa wzory w tekście', () => {
    const t1 = doc('$$ x $$');
    const p1 = analyzeStructure(t1).problems;
    assert.deepEqual(p1.map((p) => [p.code, p.severity]), [['dollar-display', 'info']]);
    const t2 = doc('$x$$y$');
    const s2 = analyzeStructure(t2);
    assert.deepEqual(s2.problems, []);
    assert.deepEqual(s2.math.map((m) => t2.slice(m.from, m.to)), ['$x$', '$y$']);
  });

  it('tekst we wzorze (\\text{… $x$ …}) i >{$}c<{$} w tabeli nie są błędem', () => {
    const text = doc(r`\[ a = 1 \text{ dla $b > 0$ } \]
\begin{tabular}{>{$}c<{$}} x \end{tabular}`, r`\usepackage{amsmath,array}`);
    assert.deepEqual(analyzeStructure(text).problems, []);
  });

  it('definicje makr nie liczą się do otoczeń i wzorów, ale ich polecenia tak', () => {
    const text = doc('Tekst', r`\newcommand{\R}{\ensuremath{\mathbb{R}}}
\newenvironment{ramka}{\begin{center}\begin{minipage}{5cm}}{\end{minipage}\end{center}}
\newcommand{\pol}[2][x]{$#1^{#2}$}
\def\half#1{\frac{#1}{2}}`);
    const s = analyzeStructure(text);
    assert.deepEqual(s.problems, []);
    assert.equal(s.definitions.length, 4);
    assert.ok(s.commands.some((c) => c.name === 'mathbb'));
  });

  it('komentarze, \\verb i otoczenia dosłowne są pomijane', () => {
    const text = doc(r`% \begin{itemize} { $
\verb+\end{x}+ i \begin{verbatim}
\begin{x} { $ \end{y}
\end{verbatim}`);
    assert.deepEqual(analyzeStructure(text).problems, []);
    // Linia z samym komentarzem nie jest pustą linią (nie kończy akapitu we wzorze).
    const t2 = doc('$a\n% komentarz\nb$');
    assert.deepEqual(analyzeStructure(t2).problems, []);
    assert.equal(blankNonCode('a % b\nc').clean, 'a %  \nc');
  });

  it('tekst za \\end{document} jest ignorowany', () => {
    const text = `${doc('ok')}\n$ { \\begin{x}`;
    assert.deepEqual(analyzeStructure(text).problems, []);
  });

  it('etykiety i odsyłacze (także listy w \\cref) z pozycjami', () => {
    const text = r`\label{eq:a} \ref{eq:a} \cref{fig:1, tab:2} \eqref{ eq:b }`;
    const s = analyzeStructure(text);
    assert.deepEqual(s.labels.map((l) => [l.name, text.slice(l.from, l.to)]), [['eq:a', 'eq:a']]);
    assert.deepEqual(
      s.refs.map((x) => [x.name, text.slice(x.from, x.to), x.cmd]),
      [['eq:a', 'eq:a', 'ref'], ['fig:1', 'fig:1', 'cref'], ['tab:2', 'tab:2', 'cref'], ['eq:b', 'eq:b', 'eqref']],
    );
  });

  it('obszary matematyczne: rodzaj, wnętrze, tryb wyświetlany; mathRegionAt', () => {
    const text = r`a $x$ b \[y\] \begin{align*} z \end{align*} \begin{math} w \end{math}`;
    const { math } = analyzeStructure(text);
    assert.deepEqual(
      math.map((m) => [m.kind, m.env, text.slice(m.innerFrom, m.innerTo).trim(), m.display]),
      [['$', null, 'x', false], ['\\[', null, 'y', true], ['env', 'align*', 'z', true], ['env', 'math', 'w', false]],
    );
    assert.equal(mathRegionAt(math, text.indexOf('x')).kind, '$');
    assert.equal(mathRegionAt(math, text.indexOf('b')), null);
    assert.equal(mathRegionAt(math, text.indexOf('z')).env, 'align*');
  });
});

describe('lintLatex: odsyłacze, etykiety, pakiety', () => {
  const other = (path, text) => ({ path, info: scanLintInfo(path, text) });

  it('odsyłacz do etykiety, której nie ma w projekcie; etykiety z innych plików się liczą', () => {
    const text = doc(r`\label{eq:a} \ref{eq:a} \ref{fig:1} \eqref{eq:x}`);
    const p = lintLatex(text, { path: 'main.tex', project: { others: [other('ch.tex', r`\label{fig:1}`)] } });
    assert.deepEqual(at(text, p.filter((x) => x.code === 'ref-unknown')), [['ref-unknown', 'eq:x']]);
  });

  it('etykiety z makr (#1) albo pakiet xr wyłączają regułę odsyłaczy', () => {
    const text = doc(r`\ref{eq:x}`, r`\usepackage{amsmath}
\newcommand{\eqlab}[1]{\label{eq:#1}}`);
    assert.deepEqual(lintLatex(text).filter((x) => x.code === 'ref-unknown'), []);
    const t2 = doc(r`\ref{eq:x}`, r`\usepackage{xr}`);
    assert.deepEqual(lintLatex(t2).filter((x) => x.code === 'ref-unknown'), []);
  });

  it('powtórzona etykieta: w tym samym pliku i w innym', () => {
    const text = doc(r`\label{a}
\label{a} \label{b}`);
    const p = lintLatex(text, { path: 'main.tex', project: { others: [other('ch.tex', '\n\\label{b}')] } }).filter((x) => x.code === 'label-duplicate');
    assert.deepEqual(p.map((x) => [x.params.name, x.params.where]), [['a', 4], ['b', 'ch.tex:2']]);
  });

  it('polecenia i otoczenia z niedołączonych pakietów — po jednym zgłoszeniu', () => {
    const text = doc(r`\ket{0} \ket{1} \includegraphics{x} \mathbb{R}
\begin{tikzpicture}\end{tikzpicture} \text{ok} \begin{align} a \end{align}`);
    const p = lintLatex(text).filter((x) => x.code === 'package-missing');
    assert.deepEqual(
      p.map((x) => [text.slice(x.from, x.to), x.params.pkg, x.params.alternatives.join('|')]),
      [
        [r`\ket`, 'braket', 'braket|physics'],
        [r`\includegraphics`, 'graphicx', 'graphicx'],
        [r`\mathbb`, 'amssymb', 'amssymb'],
        [r`\begin{tikzpicture}`, 'tikz', 'tikz'],
      ],
    );
    assert.equal(p[0].severity, 'warning');
  });

  it('szablon olimpiady i pakiety ładowane pośrednio nie dają ostrzeżeń', () => {
    const text = doc(r`\ket{\Phi^+} \braket{a|b} \expval{Z}{\psi} \abs{x} \mathbb{R} \includegraphics{a}
\href{http://x}{y} \begin{align} a \end{align}`, r`\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[polish]{babel}
\usepackage{lmodern}
\usepackage{amsmath, amssymb}
\usepackage{physics}
\usepackage{braket}
\usepackage{graphicx}
\usepackage[a4paper, margin=2.5cm]{geometry}
\usepackage{hyperref}`);
    assert.deepEqual(lintLatex(text), []);
    const t2 = doc(r`\begin{align} \text{a} \end{align} \textcolor{red}{x}`, r`\usepackage{mathtools}\usepackage{tikz}`);
    assert.deepEqual(lintLatex(t2).filter((x) => x.code === 'package-missing'), []);
    assert.ok(withImplied(['pgfplots']).has('xcolor'));
  });

  it('reguła pakietów milczy przy nieznanych pakietach, własnych .sty, innych klasach i własnych makrach', () => {
    const body = r`\ket{0}`;
    assert.deepEqual(lintLatex(doc(body, r`\usepackage{mojpakiet}`)).filter((x) => x.code === 'package-missing'), []);
    assert.deepEqual(lintLatex(doc(body, '')).filter((x) => x.code === 'package-missing').length, 1);
    assert.deepEqual(lintLatex(doc(body, ''), { project: { files: ['main.tex', 'styl.sty'] } }).filter((x) => x.code === 'package-missing'), []);
    assert.deepEqual(lintLatex(doc(body, r`\newcommand{\ket}[1]{|#1\rangle}`)).filter((x) => x.code === 'package-missing'), []);
    assert.equal(packageRuleApplies({ documentClass: 'beamer', packages: [] }), false);
    assert.equal(packageRuleApplies({ documentClass: 'article', packages: ['siunitx'] }), false);
    // Klasa z pliku głównego, sprawdzany jest rozdział.
    const chapter = r`\section{A} \ket{0}`;
    const main = { path: 'main.tex', info: scanLintInfo('main.tex', doc(r`\input{ch}`, '')) };
    assert.equal(lintLatex(chapter, { path: 'ch.tex', project: { others: [main] } }).filter((x) => x.code === 'package-missing').length, 1);
  });

  it('usepackageInsertion: za ostatnim \\usepackage w preambule', () => {
    const text = doc('x', r`\usepackage{amsmath}
\usepackage{graphicx}`);
    const ins = usepackageInsertion(text, 'braket');
    const out = text.slice(0, ins.pos) + ins.insert + text.slice(ins.pos);
    assert.match(out, /\\usepackage\{graphicx\}\n\\usepackage\{braket\}\n\\begin\{document\}/);
    assert.equal(usepackageInsertion(text, 'graphicx'), null);
    assert.equal(usepackageInsertion(r`\section{a}`, 'x'), null);
  });
});

describe('problemy z kompilacji jako diagnostyki w pliku', () => {
  const issue = (over) => ({ level: 'error', file: 'main.tex', line: 2, message: 'Undefined control sequence.', content: '', ...over });

  it('dopasowanie pliku (także bez .tex) i liczby na plik', () => {
    assert.equal(issueMatchesFile({ file: 'chapters/intro' }, 'chapters/intro.tex'), true);
    assert.equal(issueMatchesFile({ file: './main.tex' }, 'main.tex'), true);
    assert.equal(issueMatchesFile({ file: null }, 'main.tex'), false);
    const counts = countIssuesByFile(
      [issue(), issue({ level: 'warning' }), issue({ level: 'typesetting' }), issue({ file: 'a.tex' }), issue({ file: null })],
      ['main.tex', 'a.tex', 'b.tex'],
    );
    assert.deepEqual(Object.fromEntries(counts), { 'main.tex': { error: 1, warning: 1, info: 1 }, 'a.tex': { error: 1, warning: 0, info: 0 } });
  });

  it('kolumny: polecenie przed miejscem błędu z kontekstu l.N, nazwa w cudzysłowie, inaczej cała linia', () => {
    assert.deepEqual(issueColumns(issue({ content: 'l.2 Tekst \\foo\n         reszta' }), '  Tekst \\foo reszta'), [8, 12]);
    assert.deepEqual(issueColumns(issue({ content: 'l.2 ...dalszy \\bar' }), 'bardzo dlugi wstep, dalszy \\bar x'), [27, 31]);
    assert.deepEqual(
      issueColumns({ level: 'warning', message: "Reference `eq:x' on page 1 undefined on input line 2.", content: '' }, 'Wzór \\ref{eq:x}.'),
      [10, 14],
    );
    assert.deepEqual(issueColumns(issue({ content: 'l.23 \\end{align}' }), '\\end{align}'), [0, 11]);
    assert.deepEqual(issueColumns(issue({ content: '' }), '   cała linia  '), [3, 13]);
  });

  it('kotwiczenie linii po edycjach: przesunięta, zmieniona, za końcem', () => {
    const compiled = ['a', 'b \\foo', 'c'];
    assert.equal(anchorLine(2, compiled, compiled), 2);
    assert.equal(anchorLine(2, compiled, ['nowa', 'a', 'b \\foo', 'c']), 3);
    assert.equal(anchorLine(2, compiled, ['a', 'b \\poprawione', 'c']), null);
    assert.equal(anchorLine(9, compiled, ['a']), 1);
    assert.equal(anchorLine(null, compiled, compiled), null);
  });

  it('compileDiagnostics: offsety w obecnym tekście, ważność, bez duplikatów i bez cudzych plików', () => {
    const compiled = 'a\nb \\foo\nc\n';
    const current = 'nowa linia\na\nb \\foo\nc\n';
    const issues = [
      issue({ content: 'l.2 b \\foo' }),
      issue({ content: 'l.2 b \\foo' }),
      issue({ level: 'warning', line: 3, message: 'Something', file: 'main' }),
      issue({ file: 'other.tex' }),
      issue({ line: null }),
    ];
    const d = compileDiagnostics(issues, 'main.tex', compiled, current);
    assert.deepEqual(
      d.map((x) => [x.severity, current.slice(x.from, x.to), x.line]),
      [['error', '\\foo', 3], ['warning', 'c', 4]],
    );
    assert.equal(d[0].context, 'l.2 b \\foo');
    assert.deepEqual(compileDiagnostics(issues, 'main.tex', null, compiled).map((x) => x.line), [2, 3]);
  });
});

describe('tekst dokumentu: słowa, język, pisownia', () => {
  it('liczy słowa bez poleceń, wzorów, komentarzy i preambuły', () => {
    const text = doc(r`\section{Wstęp}
Zażółć gęślą jaźń, czyli $E = mc^2$ i \textbf{pogrubienie}~tekstu.% komentarz nie
\begin{equation} a + b \label{eq:x} \end{equation}
Zob.~\ref{eq:x} oraz \cite{knuth}. \includegraphics[width=3cm]{rys.png}
\begin{tabular}{|c|c|} pole & drugie \\ \end{tabular}`);
    const c = countWords(text);
    assert.deepEqual(readableText(text).replace(/\s+/g, ' ').trim(), 'Wstęp Zażółć gęślą jaźń, czyli i pogrubienie tekstu. Zob. oraz . pole drugie');
    assert.equal(c.words, 12);
    assert.equal(c.characters, 'Wstęp Zażółć gęślą jaźń, czyli i pogrubienie tekstu. Zob. oraz . pole drugie'.length);
    assert.equal(countWords('').words, 0);
  });

  it('fragment (zaznaczenie) i rozdział bez \\begin{document}', () => {
    assert.equal(countWords(r`jeden \emph{dwa} trzy`, { fragment: true }).words, 3);
    assert.equal(countWords(r`\chapter{Tytuł rozdziału} Treść`).words, 3);
    assert.equal(countWords(r`10\% i 5\$`).words, 3);
  });

  it('język dokumentu z babel, polyglossia, opcji klasy, pakietu polski', () => {
    assert.equal(documentLanguage(r`\usepackage[polish]{babel}`), 'pl');
    assert.equal(documentLanguage(r`\usepackage[english,polish]{babel}`), 'pl');
    assert.equal(documentLanguage(r`\usepackage[polish,main=english]{babel}`), 'en');
    assert.equal(documentLanguage(r`\documentclass[11pt,polish]{article}\usepackage{babel}`), 'pl');
    assert.equal(documentLanguage(r`\setmainlanguage{german}`), 'de');
    assert.equal(documentLanguage(r`\usepackage{polski}`), 'pl');
    assert.equal(documentLanguage(r`% \usepackage[polish]{babel}`), null);
    assert.equal(documentLanguage(''), null);
  });

  it('bez sprawdzania pisowni: polecenia, argumenty-nazwy i wzory', () => {
    const text = r`Tekst \label{eq:moja} i \textbf{słowo} $x$`;
    const { math } = analyzeStructure(text);
    const ranges = noSpellRanges(text, 0, math).map(([a, b]) => text.slice(a, b));
    assert.ok(ranges.includes(r`\label{eq:moja}`));
    assert.ok(ranges.includes(r`\textbf`));
    assert.ok(!ranges.some((s) => s.includes('słowo')));
    assert.ok(ranges.includes('$x$'));
    // Fragment z przesunięciem (widoczny zakres edytora).
    assert.deepEqual(noSpellRanges(r`a \b`, 100), [[102, 104]]);
  });
});

describe('podgląd wzorów: źródło i makra (KaTeX)', () => {
  const render = (src, display, macros = {}) => katex.renderToString(src, { displayMode: display, throwOnError: true, strict: 'ignore', macros: { ...macros } });

  it('źródło z otoczeń: bez \\label i \\nonumber, align -> aligned, alignat -> alignedat', () => {
    const text = r`\begin{align} a &= b \label{x}\\ c &= d \nonumber \end{align} \begin{alignat}{2} a&=b & c&=d \end{alignat} \begin{eqnarray} a&=&b \end{eqnarray} $x % k
$`;
    const { math } = analyzeStructure(text);
    const out = math.map((m) => mathSource(m, text));
    assert.deepEqual(out.map((o) => o.display), [true, true, true, false]);
    assert.match(out[0].source, /^\\begin\{aligned\} a &= b \\\\ c &= d\s+\\end\{aligned\}$/);
    assert.match(out[1].source, /^\\begin\{alignedat\}\{2\}/);
    assert.match(out[2].source, /^\\begin\{array\}\{rcl\}/);
    assert.equal(out[3].source, 'x');
    for (const o of out.slice(0, 3)) assert.doesNotThrow(() => render(o.source, o.display));
  });

  it('makra z projektu: \\newcommand, \\def, \\DeclareMathOperator, \\DeclarePairedDelimiter; bez argumentów opcjonalnych', () => {
    const m = collectMacros([r`\newcommand{\R}{\mathbb{R}}
\newcommand\vx[1]{\mathbf{#1}}
\renewcommand{\R}{\mathbb{R}}
\providecommand{\R}{X}
\def\half{\frac12}
\DeclareMathOperator*{\argmax}{arg\,max}
\DeclarePairedDelimiter\absx{\lvert}{\rvert}
\newcommand{\opt}[2][x]{#1#2}
\NewDocumentCommand{\pair}{m m}{(#1,#2)}
% \newcommand{\ukryte}{x}`]);
    assert.deepEqual(Object.keys(m).sort(), ['\\R', '\\absx', '\\argmax', '\\half', '\\pair', '\\vx']);
    assert.equal(m['\\R'], '\\mathbb{R}');
    assert.equal(m['\\argmax'], '\\operatorname*{arg\\,max}');
    assert.doesNotThrow(() => render(r`\R \vx{v} \half \argmax_x \absx{y} \pair{a}{b}`, true, m));
  });

  it('physics (gdy załadowany): \\expval z jednym i dwoma argumentami, pochodne, macierze', () => {
    const macros = previewMacros({ packages: ['physics'] });
    for (const src of [
      r`\expval{A}`,
      r`\expval{Z \otimes Z}{\Phi^+}`,
      r`\dv{f}{x} + \dv[2]{f}{x} + \pdv{f}{x}{y} + \dv{x}`,
      r`\abs{x} + \norm{v} + \qty(\frac12) + \dd{x} + \comm{A}{B} + \dyad{\psi} + \Tr\rho`,
      r`\mqty(a & b \\ c & d) \bmqty{1 & 0 \\ 0 & 1}`,
      r`\ket{\Phi^+} = \frac{1}{\sqrt{2}} \left( \ket{00} + \ket{11} \right), \braket{\Phi^+ | \Phi^+} = 1`,
    ]) {
      assert.doesNotThrow(() => render(src, true, macros), src);
    }
    assert.match(render(r`\expval{A}{\psi}`, false, macros), /ψ/);
    assert.equal('\\abs' in previewMacros({ packages: [] }), false);
  });
});
