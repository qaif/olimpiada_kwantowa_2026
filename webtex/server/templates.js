// Starter projects offered on the dashboard. Each file: [path, content]. First file is the main file.

const article = (title) => String.raw`\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{amsmath, amssymb}
\usepackage{graphicx}
\usepackage[a4paper, margin=2.5cm]{geometry}
\usepackage{hyperref}

\title{${title}}
\author{}
\date{\today}

\begin{document}

\maketitle

\section{Introduction}
Start writing here. Press \textbf{Ctrl+S} (or Ctrl+Enter) to compile.

\begin{equation}
  e^{i\pi} + 1 = 0
  \label{eq:euler}
\end{equation}

Equation~\eqref{eq:euler} is famous.

\end{document}
`;

const articlePl = (title) => String.raw`\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[polish]{babel}
\usepackage{lmodern}
\usepackage{amsmath, amssymb}
\usepackage{graphicx}
\usepackage[a4paper, margin=2.5cm]{geometry}
\usepackage{hyperref}

\title{${title}}
\author{}
\date{\today}

\begin{document}

\maketitle

\section{Wstęp}
Zacznij pisać tutaj. Naciśnij \textbf{Ctrl+S}, aby skompilować dokument.
Zażółć gęślą jaźń.

\begin{equation}
  \int_0^1 x^2 \, dx = \frac{1}{3}
  \label{eq:calka}
\end{equation}

Wzór~\eqref{eq:calka} jest prosty.

\end{document}
`;

const beamer = (title) => String.raw`\documentclass{beamer}
\usetheme{Madrid}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}

\title{${title}}
\author{}
\date{\today}

\begin{document}

\frame{\titlepage}

\begin{frame}{Outline}
  \tableofcontents
\end{frame}

\section{First section}
\begin{frame}{First slide}
  \begin{itemize}
    \item<1-> Point one
    \item<2-> Point two
    \item<3-> Point three
  \end{itemize}
\end{frame}

\end{document}
`;

const report = (title) => String.raw`\documentclass[11pt]{report}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{graphicx}
\usepackage[backend=biber, style=numeric]{biblatex}
\usepackage{hyperref}
\addbibresource{references.bib}

\title{${title}}
\author{}

\begin{document}

\maketitle
\tableofcontents

\input{chapters/introduction}

\printbibliography

\end{document}
`;

const reportChapter = String.raw`\chapter{Introduction}
\label{chap:intro}

As shown by Knuth~\cite{knuth1984}, \TeX{} is a typesetting system.
Lamport later created \LaTeX~\cite{lamport1994}.
`;

const bib = String.raw`@book{knuth1984,
  author    = {Donald E. Knuth},
  title     = {The {\TeX}book},
  publisher = {Addison-Wesley},
  year      = {1984}
}

@book{lamport1994,
  author    = {Leslie Lamport},
  title     = {{\LaTeX}: A Document Preparation System},
  publisher = {Addison-Wesley},
  edition   = {2},
  year      = {1994}
}
`;

export const TEMPLATES = {
  blank: { label: 'Blank article', files: (t) => [['main.tex', article(t)]] },
  'article-pl': { label: 'Artykuł (polski)', files: (t) => [['main.tex', articlePl(t)]] },
  beamer: { label: 'Beamer presentation', files: (t) => [['main.tex', beamer(t)]] },
  report: {
    label: 'Report with bibliography',
    compiler: 'pdflatex',
    files: (t) => [
      ['main.tex', report(t)],
      ['chapters/introduction.tex', reportChapter],
      ['references.bib', bib],
    ],
  },
};

// Solution template for projects opened from the Quantum Olympiad platform (TEX-01).
// `title` and `code` must already be TeX-escaped. No names: the participant is identified by code only.
// braket is loaded after physics on purpose, so \bra, \ket and \braket{\phi|\psi} keep braket's
// one-argument syntax while physics still provides \dv, \pdv, \expval, \comm, \abs, \norm, \tr, ...
export const olympiadSolution = ({ title, code }) => String.raw`\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[polish]{babel}
\usepackage{lmodern}
\usepackage{amsmath, amssymb}
\usepackage{physics}
\usepackage{braket}
\usepackage{graphicx}
\usepackage[a4paper, margin=2.5cm]{geometry}
\usepackage{hyperref}
% Obwody kwantowe: odkomentuj poniższą linię i przykład w treści.
% \usepackage{quantikz}

\title{${title}}
\author{Kod uczestnika: \texttt{${code}}}
\date{}

\begin{document}

\maketitle

\section*{Rozwiązanie}
Tutaj wpisz rozwiązanie. Naciśnij \textbf{Ctrl+S}, aby skompilować dokument.
Nie podpisuj pracy imieniem ani nazwiskiem -- wystarczy kod uczestnika.

Przykład: stan Bella
\begin{equation}
  \ket{\Phi^+} = \frac{1}{\sqrt{2}} \left( \ket{00} + \ket{11} \right),
  \qquad \braket{\Phi^+ | \Phi^+} = 1, \qquad \expval{Z \otimes Z}{\Phi^+} = 1.
  \label{eq:bell}
\end{equation}

% Przykład obwodu (wymaga \usepackage{quantikz} w preambule):
% \begin{center}
% \begin{quantikz}
%   \lstick{$\ket{0}$} & \gate{H} & \ctrl{1} & \qw \\
%   \lstick{$\ket{0}$} & \qw      & \targ{}  & \qw
% \end{quantikz}
% \end{center}

\end{document}
`;
