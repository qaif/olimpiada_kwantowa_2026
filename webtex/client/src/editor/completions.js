// LaTeX autocompletion: commands, environments, labels, citation keys and project files.
// Snippet templates use CodeMirror's #{name} / #{} field syntax (not ${}, which String.raw would
// interpolate). `\{` / `\}` are unescaped by the snippet parser, so a literal LaTeX `\{` is written `\\{`.
import { snippet } from '@codemirror/autocomplete';

// [label, snippet template (optional), detail (optional)]
const COMMANDS = [
  // Document structure
  ['\\documentclass', String.raw`\documentclass{#{article}}`, 'document'],
  ['\\usepackage', String.raw`\usepackage{#{package}}`, 'preamble'],
  ['\\usepackage[]', String.raw`\usepackage[#{options}]{#{package}}`, 'preamble'],
  ['\\title', String.raw`\title{#{title}}`, 'preamble'],
  ['\\author', String.raw`\author{#{name}}`, 'preamble'],
  ['\\date', String.raw`\date{#{\today}}`, 'preamble'],
  ['\\maketitle', null, 'title'],
  ['\\tableofcontents', null, 'toc'],
  ['\\listoffigures', null, 'toc'],
  ['\\listoftables', null, 'toc'],
  ['\\part', String.raw`\part{#{title}}`, 'sectioning'],
  ['\\chapter', String.raw`\chapter{#{title}}`, 'sectioning'],
  ['\\chapter*', String.raw`\chapter*{#{title}}`, 'sectioning'],
  ['\\section', String.raw`\section{#{title}}`, 'sectioning'],
  ['\\section*', String.raw`\section*{#{title}}`, 'sectioning'],
  ['\\subsection', String.raw`\subsection{#{title}}`, 'sectioning'],
  ['\\subsection*', String.raw`\subsection*{#{title}}`, 'sectioning'],
  ['\\subsubsection', String.raw`\subsubsection{#{title}}`, 'sectioning'],
  ['\\subsubsection*', String.raw`\subsubsection*{#{title}}`, 'sectioning'],
  ['\\paragraph', String.raw`\paragraph{#{title}}`, 'sectioning'],
  ['\\subparagraph', String.raw`\subparagraph{#{title}}`, 'sectioning'],
  ['\\appendix', null, 'sectioning'],
  ['\\frontmatter', null, 'book'],
  ['\\mainmatter', null, 'book'],
  ['\\backmatter', null, 'book'],
  ['\\input', String.raw`\input{#{file}}`, 'files'],
  ['\\include', String.raw`\include{#{file}}`, 'files'],
  ['\\includeonly', String.raw`\includeonly{#{files}}`, 'files'],
  ['\\begin', String.raw`\begin{#{env}}
	#{}
\end{#{env}}`, 'environment'],
  ['\\end', String.raw`\end{#{env}}`, 'environment'],
  ['\\newcommand', String.raw`\newcommand{\#{name}}[#{nargs}]{#{definition}}`, 'definition'],
  ['\\renewcommand', String.raw`\renewcommand{\#{name}}{#{definition}}`, 'definition'],
  ['\\newenvironment', String.raw`\newenvironment{#{name}}{#{begin}}{#{end}}`, 'definition'],
  ['\\DeclareMathOperator', String.raw`\DeclareMathOperator{\#{name}}{#{text}}`, 'definition'],
  ['\\newtheorem', String.raw`\newtheorem{#{name}}{#{Title}}`, 'definition'],
  ['\\setlength', String.raw`\setlength{\#{length}}{#{value}}`, 'layout'],
  ['\\addbibresource', String.raw`\addbibresource{#{references.bib}}`, 'bibliography'],
  ['\\bibliography', String.raw`\bibliography{#{references}}`, 'bibliography'],
  ['\\bibliographystyle', String.raw`\bibliographystyle{#{plain}}`, 'bibliography'],
  ['\\printbibliography', null, 'bibliography'],
  ['\\bibitem', String.raw`\bibitem{#{key}}`, 'bibliography'],

  // References
  ['\\label', String.raw`\label{#{label}}`, 'reference'],
  ['\\ref', String.raw`\ref{#{label}}`, 'reference'],
  ['\\eqref', String.raw`\eqref{#{label}}`, 'reference'],
  ['\\pageref', String.raw`\pageref{#{label}}`, 'reference'],
  ['\\autoref', String.raw`\autoref{#{label}}`, 'reference'],
  ['\\cref', String.raw`\cref{#{label}}`, 'reference'],
  ['\\Cref', String.raw`\Cref{#{label}}`, 'reference'],
  ['\\nameref', String.raw`\nameref{#{label}}`, 'reference'],
  ['\\cite', String.raw`\cite{#{key}}`, 'citation'],
  ['\\citep', String.raw`\citep{#{key}}`, 'citation'],
  ['\\citet', String.raw`\citet{#{key}}`, 'citation'],
  ['\\parencite', String.raw`\parencite{#{key}}`, 'citation'],
  ['\\textcite', String.raw`\textcite{#{key}}`, 'citation'],
  ['\\autocite', String.raw`\autocite{#{key}}`, 'citation'],
  ['\\nocite', String.raw`\nocite{#{key}}`, 'citation'],
  ['\\footnote', String.raw`\footnote{#{text}}`, 'note'],
  ['\\footnotemark', null, 'note'],
  ['\\footnotetext', String.raw`\footnotetext{#{text}}`, 'note'],
  ['\\url', String.raw`\url{#{url}}`, 'hyperref'],
  ['\\href', String.raw`\href{#{url}}{#{text}}`, 'hyperref'],

  // Text formatting
  ['\\textbf', String.raw`\textbf{#{text}}`, 'bold'],
  ['\\textit', String.raw`\textit{#{text}}`, 'italic'],
  ['\\emph', String.raw`\emph{#{text}}`, 'emphasis'],
  ['\\underline', String.raw`\underline{#{text}}`, 'underline'],
  ['\\texttt', String.raw`\texttt{#{text}}`, 'monospace'],
  ['\\textsc', String.raw`\textsc{#{text}}`, 'small caps'],
  ['\\textsf', String.raw`\textsf{#{text}}`, 'sans serif'],
  ['\\textrm', String.raw`\textrm{#{text}}`, 'roman'],
  ['\\textsl', String.raw`\textsl{#{text}}`, 'slanted'],
  ['\\textsuperscript', String.raw`\textsuperscript{#{text}}`, 'text'],
  ['\\textsubscript', String.raw`\textsubscript{#{text}}`, 'text'],
  ['\\textcolor', String.raw`\textcolor{#{red}}{#{text}}`, 'xcolor'],
  ['\\colorbox', String.raw`\colorbox{#{yellow}}{#{text}}`, 'xcolor'],
  ['\\mbox', String.raw`\mbox{#{text}}`, 'box'],
  ['\\fbox', String.raw`\fbox{#{text}}`, 'box'],
  ['\\parbox', String.raw`\parbox{#{width}}{#{text}}`, 'box'],
  ['\\tiny', null, 'size'],
  ['\\scriptsize', null, 'size'],
  ['\\footnotesize', null, 'size'],
  ['\\small', null, 'size'],
  ['\\normalsize', null, 'size'],
  ['\\large', null, 'size'],
  ['\\Large', null, 'size'],
  ['\\LARGE', null, 'size'],
  ['\\huge', null, 'size'],
  ['\\Huge', null, 'size'],
  ['\\bfseries', null, 'font'],
  ['\\itshape', null, 'font'],
  ['\\ttfamily', null, 'font'],
  ['\\centering', null, 'alignment'],
  ['\\raggedright', null, 'alignment'],
  ['\\raggedleft', null, 'alignment'],
  ['\\noindent', null, 'spacing'],
  ['\\newline', null, 'spacing'],
  ['\\newpage', null, 'spacing'],
  ['\\clearpage', null, 'spacing'],
  ['\\linebreak', null, 'spacing'],
  ['\\pagebreak', null, 'spacing'],
  ['\\vspace', String.raw`\vspace{#{1em}}`, 'spacing'],
  ['\\hspace', String.raw`\hspace{#{1em}}`, 'spacing'],
  ['\\vfill', null, 'spacing'],
  ['\\hfill', null, 'spacing'],
  ['\\medskip', null, 'spacing'],
  ['\\bigskip', null, 'spacing'],
  ['\\today', null, 'date'],
  ['\\LaTeX', null, 'logo'],
  ['\\TeX', null, 'logo'],
  ['\\ldots', null, 'symbol'],
  ['\\item', null, 'list'],
  ['\\item[]', String.raw`\item[#{label}] #{}`, 'list'],

  // Floats, graphics, tables
  ['\\includegraphics', String.raw`\includegraphics[width=#{0.8}\linewidth]{#{file}}`, 'graphicx'],
  ['\\caption', String.raw`\caption{#{text}}`, 'float'],
  ['\\captionof', String.raw`\captionof{#{figure}}{#{text}}`, 'float'],
  ['\\hline', null, 'table'],
  ['\\cline', String.raw`\cline{#{1-2}}`, 'table'],
  ['\\toprule', null, 'booktabs'],
  ['\\midrule', null, 'booktabs'],
  ['\\bottomrule', null, 'booktabs'],
  ['\\multicolumn', String.raw`\multicolumn{#{cols}}{#{c}}{#{text}}`, 'table'],
  ['\\multirow', String.raw`\multirow{#{rows}}{*}{#{text}}`, 'table'],
  ['\\linewidth', null, 'length'],
  ['\\textwidth', null, 'length'],

  // Math
  ['\\frac', String.raw`\frac{#{num}}{#{den}}`, 'math'],
  ['\\dfrac', String.raw`\dfrac{#{num}}{#{den}}`, 'math'],
  ['\\sqrt', String.raw`\sqrt{#{x}}`, 'math'],
  ['\\sqrt[]', String.raw`\sqrt[#{n}]{#{x}}`, 'math'],
  ['\\sum', String.raw`\sum_{#{i=1}}^{#{n}}`, 'math'],
  ['\\prod', String.raw`\prod_{#{i=1}}^{#{n}}`, 'math'],
  ['\\int', String.raw`\int_{#{a}}^{#{b}}`, 'math'],
  ['\\iint', null, 'math'],
  ['\\oint', null, 'math'],
  ['\\lim', String.raw`\lim_{#{x \to \infty}}`, 'math'],
  ['\\binom', String.raw`\binom{#{n}}{#{k}}`, 'math'],
  ['\\left(', String.raw`\left( #{} \right)`, 'math'],
  ['\\left[', String.raw`\left[ #{} \right]`, 'math'],
  ['\\left\\{', String.raw`\left\\{ #{} \right\\}`, 'math'],
  ['\\mathbb', String.raw`\mathbb{#{R}}`, 'math font'],
  ['\\mathcal', String.raw`\mathcal{#{A}}`, 'math font'],
  ['\\mathrm', String.raw`\mathrm{#{text}}`, 'math font'],
  ['\\mathbf', String.raw`\mathbf{#{x}}`, 'math font'],
  ['\\mathit', String.raw`\mathit{#{x}}`, 'math font'],
  ['\\boldsymbol', String.raw`\boldsymbol{#{x}}`, 'math font'],
  ['\\text', String.raw`\text{#{text}}`, 'amsmath'],
  ['\\operatorname', String.raw`\operatorname{#{name}}`, 'amsmath'],
  ['\\overline', String.raw`\overline{#{x}}`, 'math'],
  ['\\underbrace', String.raw`\underbrace{#{x}}_{#{text}}`, 'math'],
  ['\\hat', String.raw`\hat{#{x}}`, 'accent'],
  ['\\bar', String.raw`\bar{#{x}}`, 'accent'],
  ['\\vec', String.raw`\vec{#{x}}`, 'accent'],
  ['\\tilde', String.raw`\tilde{#{x}}`, 'accent'],
  ['\\dot', String.raw`\dot{#{x}}`, 'accent'],
  ['\\partial', null, 'math'],
  ['\\nabla', null, 'math'],
  ['\\infty', null, 'math'],
  ['\\cdot', null, 'math'],
  ['\\cdots', null, 'math'],
  ['\\times', null, 'math'],
  ['\\pm', null, 'math'],
  ['\\leq', null, 'relation'],
  ['\\geq', null, 'relation'],
  ['\\neq', null, 'relation'],
  ['\\approx', null, 'relation'],
  ['\\equiv', null, 'relation'],
  ['\\sim', null, 'relation'],
  ['\\in', null, 'relation'],
  ['\\notin', null, 'relation'],
  ['\\subset', null, 'relation'],
  ['\\subseteq', null, 'relation'],
  ['\\cup', null, 'math'],
  ['\\cap', null, 'math'],
  ['\\emptyset', null, 'math'],
  ['\\forall', null, 'logic'],
  ['\\exists', null, 'logic'],
  ['\\neg', null, 'logic'],
  ['\\land', null, 'logic'],
  ['\\lor', null, 'logic'],
  ['\\to', null, 'arrow'],
  ['\\rightarrow', null, 'arrow'],
  ['\\leftarrow', null, 'arrow'],
  ['\\Rightarrow', null, 'arrow'],
  ['\\Leftrightarrow', null, 'arrow'],
  ['\\iff', null, 'arrow'],
  ['\\implies', null, 'arrow'],
  ['\\mapsto', null, 'arrow'],
  ['\\quad', null, 'spacing'],
  ['\\qquad', null, 'spacing'],
  ['\\sin', null, 'function'],
  ['\\cos', null, 'function'],
  ['\\tan', null, 'function'],
  ['\\log', null, 'function'],
  ['\\ln', null, 'function'],
  ['\\exp', null, 'function'],
  ['\\max', null, 'function'],
  ['\\min', null, 'function'],
  ...['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'varepsilon', 'zeta', 'eta', 'theta', 'vartheta', 'iota', 'kappa',
    'lambda', 'mu', 'nu', 'xi', 'pi', 'rho', 'sigma', 'tau', 'upsilon', 'phi', 'varphi', 'chi', 'psi', 'omega',
    'Gamma', 'Delta', 'Theta', 'Lambda', 'Xi', 'Pi', 'Sigma', 'Phi', 'Psi', 'Omega'].map((g) => [`\\#{g}`, null, 'greek']),

  // Beamer
  ['\\frametitle', String.raw`\frametitle{#{title}}`, 'beamer'],
  ['\\pause', null, 'beamer'],
  ['\\usetheme', String.raw`\usetheme{#{Madrid}}`, 'beamer'],
];

const ENV_BODIES = {
  itemize: String.raw`\item #{}`,
  enumerate: String.raw`\item #{}`,
  description: String.raw`\item[#{label}] #{}`,
  figure: String.raw`\centering
	\includegraphics[width=0.8\linewidth]{#{file}}
	\caption{#{caption}}
	\label{fig:#{label}}`,
  table: String.raw`\centering
	\begin{tabular}{#{c c}}
		#{}
	\end{tabular}
	\caption{#{caption}}
	\label{tab:#{label}}`,
  tabular: null,
  frame: String.raw`\frametitle{#{title}}
	#{}`,
  thebibliography: String.raw`\bibitem{#{key}} #{}`,
};
const ENV_ARGS = {
  figure: '[htbp]',
  table: '[htbp]',
  tabular: String.raw`{#{c c}}`,
  tabularx: String.raw`{\linewidth}{#{X X}}`,
  minipage: String.raw`{#{0.45}\linewidth}`,
  thebibliography: '{9}',
  column: String.raw`{#{0.5}\textwidth}`,
  block: String.raw`{#{title}}`,
  alertblock: String.raw`{#{title}}`,
  exampleblock: String.raw`{#{title}}`,
  subfigure: String.raw`{#{0.45}\linewidth}`,
  array: String.raw`{#{c c}}`,
  multicols: String.raw`{#{cols}}`,
};
const ENVIRONMENTS = [
  'itemize', 'enumerate', 'description', 'equation', 'equation*', 'align', 'align*', 'gather', 'gather*',
  'multline', 'multline*', 'split', 'cases', 'figure', 'figure*', 'table', 'table*', 'tabular', 'tabularx',
  'longtable', 'center', 'flushleft', 'flushright', 'quote', 'quotation', 'verse', 'verbatim', 'lstlisting',
  'minted', 'abstract', 'document', 'titlepage', 'minipage', 'multicols', 'subfigure', 'array', 'matrix',
  'pmatrix', 'bmatrix', 'vmatrix', 'theorem', 'lemma', 'proposition', 'corollary', 'definition', 'remark',
  'example', 'proof', 'thebibliography', 'tikzpicture', 'frame', 'block', 'alertblock', 'exampleblock',
  'columns', 'column', 'appendices', 'landscape',
];

const PACKAGES = [
  'amsmath', 'amssymb', 'amsthm', 'mathtools', 'graphicx', 'xcolor', 'hyperref', 'geometry', 'babel', 'polski',
  'inputenc', 'fontenc', 'lmodern', 'fontspec', 'microtype', 'booktabs', 'tabularx', 'longtable', 'multirow',
  'array', 'float', 'caption', 'subcaption', 'enumitem', 'listings', 'minted', 'tikz', 'pgfplots', 'biblatex',
  'natbib', 'cleveref', 'siunitx', 'csquotes', 'fancyhdr', 'titlesec', 'setspace', 'parskip', 'url', 'xspace',
  'todonotes', 'lipsum', 'multicol', 'wrapfig', 'algorithm', 'algpseudocode', 'physics', 'bm', 'mhchem',
];
const CLASSES = ['article', 'report', 'book', 'letter', 'beamer', 'memoir', 'scrartcl', 'scrreprt', 'scrbook', 'standalone', 'mwart', 'mwrep', 'mwbk'];

const REF_CMDS = new Set(['ref', 'eqref', 'autoref', 'cref', 'Cref', 'pageref', 'nameref', 'vref', 'labelcref']);
const CITE_CMDS = /^(no)?cite[a-zA-Z]*$|^(paren|text|auto|foot|full|super|smart)cite[a-zA-Z]*$/i;
const IMAGE_EXT = /\.(png|jpe?g|pdf|eps|svg|gif)$/i;

const envBeginTemplate = (env) => {
  const args = ENV_ARGS[env] || '';
  const body = env in ENV_BODIES && ENV_BODIES[env] !== null ? ENV_BODIES[env] : '#{}';
  return `${env}}${args}\n\t${body}\n\\end{${env}}`;
};

// Swallow a `}` that closeBrackets already inserted after the cursor.
const swallowBrace = (apply) => (view, completion, from, to) => {
  const end = view.state.sliceDoc(to, to + 1) === '}' ? to + 1 : to;
  apply(view, completion, from, end);
};

const insertWithBrace = (text) => (view, completion, from, to) => {
  const hasBrace = view.state.sliceDoc(to, to + 1) === '}';
  const insert = hasBrace ? text : `${text}}`;
  view.dispatch({
    changes: { from, to, insert },
    selection: { anchor: from + insert.length },
    userEvent: 'input.complete',
  });
};

const commandOptions = COMMANDS.map(([label, template, detail]) => ({
  label,
  detail,
  type: template ? 'function' : 'keyword',
  apply: template ? snippet(template) : label,
  boost: detail === 'sectioning' || detail === 'environment' ? 2 : 0,
}));

const envBeginOptions = ENVIRONMENTS.map((env) => ({
  label: env,
  type: 'type',
  apply: swallowBrace(snippet(envBeginTemplate(env))),
}));
const envEndOptions = ENVIRONMENTS.map((env) => ({ label: env, type: 'type', apply: insertWithBrace(env) }));

/** Returns a CodeMirror CompletionSource for LaTeX. */
export function latexCompletionSource(getSymbols) {
  return (context) => {
    const line = context.state.doc.lineAt(context.pos);
    const before = line.text.slice(0, context.pos - line.from);

    // Inside a command argument: \cmd[opt]{arg
    const arg = /\\([a-zA-Z]+\*?)\s*(?:\[[^\]]*\])*\{([^{}]*)$/.exec(before);
    if (arg) {
      const cmd = arg[1];
      const content = arg[2];
      const symbols = () => getSymbols?.() || { labels: [], citeKeys: [], files: [] };
      let options = null;
      let word = content;

      if (cmd === 'begin') options = envBeginOptions;
      else if (cmd === 'end') options = envEndOptions;
      else if (REF_CMDS.has(cmd.replace(/\*$/, ''))) {
        word = content.split(',').pop().trimStart();
        options = (symbols().labels || []).map((l) => ({ label: l, type: 'variable', apply: insertWithBrace(l) }));
      } else if (CITE_CMDS.test(cmd.replace(/\*$/, ''))) {
        word = content.split(',').pop().trimStart();
        options = (symbols().citeKeys || []).map((k) => ({ label: k, type: 'text', apply: insertWithBrace(k) }));
      } else if (cmd === 'includegraphics') {
        options = (symbols().files || []).filter((f) => IMAGE_EXT.test(f)).map((f) => ({ label: f, type: 'variable', apply: insertWithBrace(f) }));
      } else if (cmd === 'input' || cmd === 'include' || cmd === 'subfile' || cmd === 'includeonly') {
        options = (symbols().files || [])
          .filter((f) => /\.tex$/i.test(f))
          .map((f) => ({ label: f.replace(/\.tex$/i, ''), type: 'variable', detail: f }));
      } else if (cmd === 'addbibresource') {
        options = (symbols().files || []).filter((f) => /\.bib$/i.test(f)).map((f) => ({ label: f, type: 'variable' }));
      } else if (cmd === 'bibliography') {
        word = content.split(',').pop().trimStart();
        options = (symbols().files || [])
          .filter((f) => /\.bib$/i.test(f))
          .map((f) => ({ label: f.replace(/\.bib$/i, ''), type: 'variable' }));
      } else if (cmd === 'usepackage' || cmd === 'RequirePackage') {
        word = content.split(',').pop().trimStart();
        options = PACKAGES.map((p) => ({ label: p, type: 'namespace' }));
      } else if (cmd === 'documentclass') {
        options = CLASSES.map((c) => ({ label: c, type: 'class' }));
      }

      if (!options) return null;
      if (!options.length && !context.explicit) return null;
      return {
        from: context.pos - word.length,
        options,
        validFor: /^[^{},\s]*$/,
      };
    }

    // A command name: \sec|
    const cmd = context.matchBefore(/\\[a-zA-Z@]*\*?/);
    if (cmd && (cmd.text.length > 1 || context.explicit)) {
      // Don't complete escaped backslashes (\\ line breaks).
      if (cmd.from > line.from && context.state.sliceDoc(cmd.from - 1, cmd.from) === '\\') return null;
      return { from: cmd.from, options: commandOptions, validFor: /^\\[a-zA-Z@]*\*?$/ };
    }
    return null;
  };
}

const stripComments = (text) => text.replace(/(^|[^\\])%.*$/gm, '$1');

/** Collects labels (from .tex files) and citation keys (from .bib files and \bibitem). */
export function scanSymbols(entries) {
  const labels = new Set();
  const citeKeys = new Set();
  for (const [path, raw] of entries) {
    const lower = path.toLowerCase();
    if (lower.endsWith('.bib')) {
      for (const m of raw.matchAll(/@([a-zA-Z]+)\s*[{(]\s*([^,\s{}()]+)\s*,/g)) {
        if (!/^(comment|string|preamble)$/i.test(m[1])) citeKeys.add(m[2]);
      }
    } else if (/\.(tex|ltx|sty|cls|tikz)$/.test(lower)) {
      const text = stripComments(raw);
      for (const m of text.matchAll(/\\label\{([^{}]+)\}/g)) labels.add(m[1].trim());
      for (const m of text.matchAll(/\\bibitem(?:\[[^\]]*\])?\{([^{}]+)\}/g)) citeKeys.add(m[1].trim());
    }
  }
  return {
    labels: [...labels].sort((a, b) => a.localeCompare(b)),
    citeKeys: [...citeKeys].sort((a, b) => a.localeCompare(b)),
  };
}
