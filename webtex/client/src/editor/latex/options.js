// Builds completion options from the catalog, the project's own definitions, the cursor context and
// usage history. Returns plain objects ({ label, detail, boost, template, info, id, ... }); completions.js
// turns them into CodeMirror completions (snippets, info panels). Pure, so it is unit tested in node.
import { CATEGORIES, CLASSES, COMMANDS, ENVIRONMENTS, PACKAGES } from './catalog.js';

export const OFF_CONTEXT_BOOST = -60;
export const PROJECT_BOOST = 15;
export const ENV_CONTEXT_BOOST = 20;
export const MISSING_PACKAGE_BOOST = -10;
/** Command names typed this long (with the backslash) also list commands of the other mode. */
export const OFF_CONTEXT_MIN_TYPED = 3;

const clamp = (b) => Math.max(-99, Math.min(99, Math.round(b)));
const noUsage = { boost: () => 0 };

/** Placeholder-annotated form of a snippet template for the info panel: \frac{⟨a⟩}{⟨b⟩}. */
export function renderTemplate(template) {
  return template
    .replace(/#\{((?:\\[{}]|[^{}])*)\}/g, (_, name) => `⟨${name.replace(/\\([{}])/g, '$1') || '…'}⟩`)
    .replace(/\\([{}])/g, '$1')
    .replace(/\t/g, '  ');
}

/** The argument being typed, e.g. `\eqref{eq:a` → { cmd: 'eqref', content: 'eq:a', word: 'eq:a' }. */
export function argumentContext(lineBefore) {
  const m = /\\([a-zA-Z]+\*?)\s*(?:\[[^\]]*\])*\{([^{}]*)$/.exec(lineBefore);
  if (!m) return null;
  const cmd = m[1].replace(/\*$/, '');
  const content = m[2];
  const listy = /^(ref|eqref|autoref|cref|Cref|pageref|nameref|vref|labelcref|usepackage|RequirePackage|bibliography|includeonly)$/.test(cmd)
    || CITE_CMDS.test(cmd);
  return { cmd, content, word: listy ? content.split(',').pop().trimStart() : content };
}

export const REF_CMDS = new Set(['ref', 'eqref', 'autoref', 'cref', 'Cref', 'pageref', 'nameref', 'vref', 'labelcref']);
export const CITE_CMDS = /^(no)?cite[a-zA-Z]*$|^(paren|text|auto|foot|full|super|smart)cite[a-zA-Z]*$/i;

const pkgLoaded = (pkg, packages) => !pkg || pkg.split('|').some((p) => packages.includes(p));

/** In-context check for a catalog command or environment. */
function fits(entry, mode, envs) {
  if (entry.env) return entry.env.some((e) => envs.includes(e));
  return entry.mode === 'any' || entry.mode === mode;
}

/** Snippet template for a project command: \foo{#{}}{#{}} (one optional argument as [#{}]). */
export function userCommandTemplate({ name, nargs = 0, optional = 0 }) {
  if (!nargs && !optional) return null;
  return `\\${name}${optional ? '[#{}]' : ''}${'{#{}}'.repeat(Math.max(0, nargs))}`;
}

/**
 * Options after a backslash.
 * ctx: { mode, envs }, symbols: merged project symbols, usage: { boost(id) }, includeOffContext: bool.
 */
export function commandOptions({ ctx, symbols = {}, lang = 'pl', usage = noUsage, includeOffContext = false }) {
  const mode = ctx?.mode || 'text';
  const envs = ctx?.envs || [];
  const packages = symbols.packages || [];
  const knowPackages = packages.length > 0;
  const userCmds = symbols.commands || [];
  const userNames = new Set(userCmds.map((c) => `\\${c.name}`));
  const out = [];

  for (const c of COMMANDS) {
    if (userNames.has(c.name)) continue;
    const inContext = fits(c, mode, envs);
    if (!inContext && !includeOffContext) continue;
    const id = `cmd:${c.name}`;
    let boost = (c.boost || 0) + usage.boost(id);
    if (!inContext) boost += OFF_CONTEXT_BOOST;
    else if (c.env) boost += ENV_CONTEXT_BOOST;
    const loaded = pkgLoaded(c.pkg, packages);
    if (knowPackages && !loaded) boost += MISSING_PACKAGE_BOOST;
    out.push({
      label: c.name,
      detail: c[lang] || c.pl,
      boost: clamp(boost),
      template: c.snippet,
      id,
      kind: c.snippet ? 'function' : 'keyword',
      info: {
        description: c[lang] || c.pl,
        category: CATEGORIES[c.cat]?.[lang] || c.cat,
        syntax: c.snippet ? renderTemplate(c.snippet) : c.name,
        pkg: c.pkg || null,
        pkgLoaded: knowPackages ? loaded : null,
        offContext: !inContext,
        mode: c.mode,
        envs: c.env || null,
      },
    });
  }

  for (const u of userCmds) {
    if (!includeOffContext && u.math && mode !== 'math') continue;
    const id = `cmd:\\${u.name}`;
    let boost = PROJECT_BOOST + usage.boost(id);
    if (u.math && mode !== 'math') boost += OFF_CONTEXT_BOOST;
    const template = userCommandTemplate(u);
    out.push({
      label: `\\${u.name}`,
      detail: CATEGORIES.user[lang] || CATEGORIES.user.pl,
      boost: clamp(boost),
      template,
      id,
      kind: template ? 'function' : 'keyword',
      info: {
        description: null,
        category: CATEGORIES.user[lang] || CATEGORIES.user.pl,
        syntax: template ? renderTemplate(template) : `\\${u.name}`,
        definedAt: { file: u.file, line: u.line },
        nargs: u.nargs,
        offContext: Boolean(u.math && mode !== 'math'),
        mode: u.math ? 'math' : 'any',
      },
    });
  }
  return out;
}

/** Text that follows `\begin{` for an environment, as a snippet template (closes with \end{…}). */
export function envBeginTemplate(env) {
  const args = env.args || (env.nargs ? '{#{}}'.repeat(env.nargs) : '');
  const body = env.body || '#{}';
  return `${env.name}}${args}\n\t${body}\n\\end{${env.name}}`;
}

/** Options after `\begin{` (kind 'begin') or `\end{` (kind 'end', `open` = open environments, innermost last). */
export function environmentOptions({ kind = 'begin', ctx, symbols = {}, lang = 'pl', usage = noUsage, open = [] }) {
  const mode = ctx?.mode || 'text';
  const packages = symbols.packages || [];
  const knowPackages = packages.length > 0;
  const seen = new Set();
  const out = [];
  const add = (env, { project = false } = {}) => {
    if (seen.has(env.name)) return;
    seen.add(env.name);
    const id = `env:${env.name}`;
    let boost = usage.boost(id) + (project ? PROJECT_BOOST : 0);
    const inContext = project || env.mode === 'any' || env.mode === mode;
    if (kind === 'begin' && !inContext) boost += OFF_CONTEXT_BOOST;
    const loaded = pkgLoaded(env.pkg, packages);
    if (kind === 'begin' && knowPackages && !loaded) boost += MISSING_PACKAGE_BOOST;
    const depth = open.lastIndexOf(env.name);
    if (kind === 'end' && depth >= 0) boost = 99 - (open.length - 1 - depth) * 10;
    const description = project ? null : env[lang] || env.pl;
    out.push({
      label: env.name,
      detail: description || CATEGORIES.user[lang] || CATEGORIES.user.pl,
      boost: clamp(boost),
      template: kind === 'begin' ? envBeginTemplate(env) : null,
      id,
      kind: 'type',
      info: {
        description,
        category: project ? CATEGORIES.user[lang] || CATEGORIES.user.pl : CATEGORIES.environment[lang],
        syntax: `\\begin{${env.name}}${env.args ? renderTemplate(env.args) : ''} … \\end{${env.name}}`,
        pkg: env.pkg || null,
        pkgLoaded: knowPackages ? loaded : null,
        definedAt: project ? { file: env.file, line: env.line } : null,
        offContext: kind === 'begin' && !inContext,
        mode: env.mode,
      },
    });
  };
  for (const e of symbols.environments || []) add(e, { project: true });
  for (const e of ENVIRONMENTS) add(e);
  // Environments open in the document but unknown to the catalog, so \end{ can still close them.
  if (kind === 'end') for (const name of open) add({ name, mode: 'any' }, { project: true });
  return out;
}

/** Labels for \ref-like commands; \eqref prefers eq: labels. */
export function labelOptions({ cmd = 'ref', symbols = {}, usage = noUsage }) {
  return (symbols.labels || []).map((l) => {
    const name = typeof l === 'string' ? l : l.name;
    const isEq = /^eq/i.test(name);
    let boost = usage.boost(`ref:${name}`);
    if (cmd === 'eqref') boost += isEq ? 20 : -20;
    return {
      label: name,
      detail: l.file ? `${l.file}:${l.line}` : '',
      boost: clamp(boost),
      id: `ref:${name}`,
      kind: 'variable',
      info: l.preview ? { preview: l.preview, definedAt: { file: l.file, line: l.line } } : null,
    };
  });
}

const LABEL_PREFIXES = [
  [/^(equation|align|gather|multline|flalign|alignat|eqnarray)\*?$/, 'eq:'],
  [/^(figure|subfigure|wrapfigure)\*?$/, 'fig:'],
  [/^(table|tabular|longtable)\*?$/, 'tab:'],
  [/^(theorem|lemma|proposition|corollary)$/, 'thm:'],
  [/^definition$/, 'def:'],
  [/^(quantikz|tikzpicture)$/, 'fig:'],
];

/** Suggested label prefix inside \label{ from the innermost environment (eq:, fig:, tab:, …, else sec:). */
export function labelPrefix(envs = []) {
  for (let k = envs.length - 1; k >= 0; k--) {
    for (const [re, prefix] of LABEL_PREFIXES) if (re.test(envs[k])) return prefix;
  }
  return 'sec:';
}

/** Packages after \usepackage{; already loaded ones go to the end. */
export function packageOptions({ symbols = {}, lang = 'pl', usage = noUsage }) {
  const loaded = new Set(symbols.packages || []);
  return PACKAGES.map((p) => ({
    label: p.name,
    detail: p[lang] || p.pl,
    boost: clamp(usage.boost(`pkg:${p.name}`) + (loaded.has(p.name) ? -30 : 0)),
    id: `pkg:${p.name}`,
    kind: 'namespace',
    loaded: loaded.has(p.name),
    info: { description: p[lang] || p.pl, category: CATEGORIES.package[lang] },
  }));
}

export function classOptions({ lang = 'pl' } = {}) {
  return CLASSES.map((c) => ({ label: c.name, detail: c[lang] || c.pl, boost: 0, id: `cls:${c.name}`, kind: 'class' }));
}
