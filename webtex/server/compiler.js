// Compiles a project with latexmk, either with a local TeX installation or inside a
// throw-away, network-less Docker container (recommended: see docker/texlive/Dockerfile).
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config, paths } from './config.js';
import { getText } from './collab.js';
import { entityPaths, listEntities } from './tree.js';
import { parseLatexmkOutput, parseLog } from './logParser.js';
import { exists, newId } from './util.js';

export const ENGINES = { pdflatex: '-pdf', xelatex: '-xelatex', lualatex: '-lualatex' };
const MANIFEST = '.webtex-manifest.json';
const DOCKER_ROOT = '/compile';
const GUARD_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'tex');
const DOCKER_GUARD_DIR = '/opt/webtex';

// TeX hardening for every compile (kpathsea): no writing outside the project directory (no absolute
// paths, no ..), no shell escape at all. openin_any=p restricts reads the same way only up to TeX Live
// 2025 (a no-op since 2026), and LuaTeX's Lua io library ignores both: hence the Docker sandbox or
// Landlock (localCompileIsolation) and engineRefusal().
export const TEX_ENV = {
  openin_any: 'p',
  openout_any: 'p',
  shell_escape: 'f',
  max_print_line: '10000',
  error_line: '254',
  half_error_line: '238',
};

/** Returns why an engine may not be used on this server, or null. */
export function engineRefusal(compiler) {
  if (config.integration.enabled && compiler === 'lualatex') {
    return 'LuaLaTeX jest wyłączony w edytorze olimpiady (kod Lua może czytać i zapisywać pliki poza projektem). Wybierz pdfLaTeX lub XeLaTeX w ustawieniach projektu.';
  }
  return null;
}

function runProcess(cmd, args, { cwd, timeoutMs, onTimeout, env = { ...process.env, ...TEX_ENV } } = {}) {
  return new Promise((resolve) => {
    let out = '';
    let timedOut = false;
    let child;
    try {
      child = spawn(cmd, args, {
        cwd,
        env,
        windowsHide: true,
      });
    } catch (err) {
      return resolve({ code: -1, out: String(err), timedOut, spawnError: err });
    }
    const timer = timeoutMs
      ? setTimeout(() => {
          timedOut = true;
          onTimeout?.();
          child.kill('SIGKILL');
        }, timeoutMs)
      : null;
    const append = (d) => {
      if (out.length < 2_000_000) out += d.toString();
    };
    child.stdout?.on('data', append);
    child.stderr?.on('data', append);
    child.on('error', (err) => {
      clearTimeout(timer);
      resolve({ code: -1, out: out + String(err), timedOut, spawnError: err });
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code, out, timedOut });
    });
  });
}

let detected = null;
export async function detectBackend() {
  if (detected) return detected;
  const want = config.compiler.backend;
  if (want === 'local' || want === 'auto') {
    const r = await runProcess('latexmk', ['-v'], { timeoutMs: 15000 });
    if (r.code === 0) return (detected = 'local');
    if (want === 'local') return 'none';
  }
  if (want === 'docker' || want === 'auto') {
    const r = await runProcess('docker', ['image', 'inspect', '--format', '{{.Id}}', config.compiler.image], { timeoutMs: 20000 });
    if (r.code === 0) return (detected = 'docker');
  }
  return 'none'; // not cached: the admin may install TeX / build the image later
}

// ---------- isolation of local compiles ----------
// Since TeX Live 2026 kpathsea ignores openin_any (see its texmf.cnf), and LuaTeX's io library and
// biber never honoured it. Outside the per-compile Docker sandbox, compiles therefore run under
// server/tex/sandbox.pl (Linux Landlock): the kernel confines each compile to its own directory plus
// read-only system and TeX paths. In integration mode the local backend refuses to compile when
// neither Landlock nor an enforcing openin_any (TeX Live <= 2025) keeps TeX inside the project.
const SANDBOX = path.join(GUARD_DIR, 'sandbox.pl');
const SANDBOX_RO = ['/usr', '/bin', '/sbin', '/lib', '/lib32', '/lib64', '/etc', '/opt', '/var/lib/texmf', '/var/cache/fontconfig'];
const SANDBOX_DEVICES = ['/dev/null', '/dev/zero', '/dev/random', '/dev/urandom'];

let localIsolation = null;
export async function localCompileIsolation() {
  if (localIsolation) return localIsolation;
  let landlock = false;
  const texRoots = [];
  if (process.platform === 'linux') {
    landlock = (await runProcess('perl', [SANDBOX, '--check'], { timeoutMs: 15000 })).code === 0;
    const root = await runProcess('kpsewhich', ['-var-value=TEXMFROOT'], { timeoutMs: 15000 });
    if (root.code === 0 && root.out.trim().startsWith('/')) texRoots.push(root.out.trim());
  }
  const probe = await runProcess('kpsewhich', ['-safe-in-name=/etc/passwd'], { timeoutMs: 15000 });
  const openinEnforced = !probe.spawnError && probe.code !== 0;
  localIsolation = { landlock, openinEnforced, texRoots };
  console.log(
    `webTEX: local compiles ${landlock ? 'sandboxed with Landlock' : 'NOT sandboxed (Landlock unavailable)'}, ` +
      `openin_any ${openinEnforced ? 'enforced' : 'not enforced by this TeX'}`,
  );
  return localIsolation;
}

/** Environment for local TeX runs: only what TeX needs, never the server's secrets. */
function localTexEnv(dir) {
  if (process.platform === 'win32') return { ...process.env, ...TEX_ENV };
  const env = {
    PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
    LANG: 'C.UTF-8',
    // Per project, inside the only writable directory: no cache shared (and poisonable) between projects.
    HOME: path.join(dir, '.webtex-home'),
    TEXMFVAR: path.join(dir, '.webtex-var'),
    TMPDIR: path.join(dir, '.webtex-tmp'),
    ...TEX_ENV,
  };
  // Read-only, pre-extracted cache of the self-extracting biber binary (see Dockerfile).
  if (process.env.PAR_GLOBAL_TMPDIR) env.PAR_GLOBAL_TMPDIR = process.env.PAR_GLOBAL_TMPDIR;
  return env;
}

/** Writes the project's current files into its compile directory (incrementally). */
async function materialize(projectId) {
  const dir = paths.compileDir(projectId);
  await fs.mkdir(dir, { recursive: true });
  const entities = listEntities(projectId);
  const pathOf = entityPaths(entities);
  let previous = {};
  try {
    previous = JSON.parse(await fs.readFile(path.join(dir, MANIFEST), 'utf8'));
  } catch {}
  const manifest = {};

  for (const e of entities) {
    const rel = pathOf.get(e.id);
    const target = path.join(dir, ...rel.split('/'));
    if (e.kind === 'folder') {
      await fs.mkdir(target, { recursive: true });
      continue;
    }
    await fs.mkdir(path.dirname(target), { recursive: true });
    if (e.kind === 'doc') {
      const content = await getText(projectId, e.id);
      let current = null;
      try {
        current = await fs.readFile(target, 'utf8');
      } catch {}
      if (current !== content) await fs.writeFile(target, content);
      manifest[rel] = 'doc';
    } else {
      const blob = paths.blob(projectId, e.id);
      const stat = await fs.stat(blob).catch(() => null);
      if (!stat) continue;
      const stamp = `${e.id}:${stat.size}:${stat.mtimeMs}`;
      if (previous[rel] !== stamp || !(await exists(target))) await fs.copyFile(blob, target);
      manifest[rel] = stamp;
    }
  }
  // Remove files that were deleted/renamed in the project since the last compile.
  for (const rel of Object.keys(previous)) {
    if (!(rel in manifest)) await fs.rm(path.join(dir, ...rel.split('/')), { force: true });
  }
  await fs.writeFile(path.join(dir, MANIFEST), JSON.stringify(manifest));
  return { dir, pathOf };
}

const locks = new Map();

export async function compileProject(project, { stopOnFirstError = false } = {}) {
  const previous = locks.get(project.id) || Promise.resolve();
  const job = previous.catch(() => {}).then(() => doCompile(project, { stopOnFirstError }));
  locks.set(project.id, job);
  try {
    const result = await job;
    if (result.status !== 'unavailable') {
      lastResults.set(project.id, { status: result.status, message: result.message || null, issues: result.issues || [], at: Date.now() });
    }
    return result;
  } finally {
    if (locks.get(project.id) === job) locks.delete(project.id);
  }
}

// Last compile outcome per project (whoever compiled it), for the AI assistant's "fix errors".
const lastResults = new Map();

/**
 * Issues of the project's most recent compilation: from memory, or after a restart from the
 * newest build's log on disk. Returns { status, message, issues, at } or null if never compiled.
 */
export async function lastCompileIssues(projectId) {
  const hit = lastResults.get(projectId);
  if (hit) return hit;
  const builds = (await fs.readdir(paths.outputs(projectId)).catch(() => [])).sort();
  const newest = builds[builds.length - 1];
  if (!newest) return null;
  const dir = paths.build(projectId, newest);
  const log = await fs.readFile(path.join(dir, 'output.log'), 'utf8').catch(() => '');
  const out = await fs.readFile(path.join(dir, 'latexmk.txt'), 'utf8').catch(() => '');
  const hasPdf = await exists(path.join(dir, 'output.pdf'));
  const issues = [...parseLog(log, { roots: [paths.compileDir(projectId), DOCKER_ROOT] }), ...parseLatexmkOutput(out)];
  return { status: hasPdf ? 'success' : 'failure', message: null, issues, at: null };
}


async function doCompile(project, { stopOnFirstError }) {
  const started = Date.now();
  const backend = await detectBackend();
  if (backend === 'none') {
    return {
      status: 'unavailable',
      message:
        'No TeX installation found. Install TeX Live (latexmk) on the server, or build the sandbox image: ' +
        `docker build -t ${config.compiler.image} docker/texlive`,
      issues: [],
      log: '',
      output: '',
    };
  }
  const isolation = backend === 'local' ? await localCompileIsolation() : null;
  if (isolation && config.integration.enabled && !isolation.landlock && !isolation.openinEnforced) {
    return {
      status: 'unavailable',
      message:
        'Compilation disabled: nothing keeps TeX inside the project here. The kernel has no Landlock (needs Linux >= 5.13 ' +
        'with Landlock enabled) and this TeX ignores openin_any (TeX Live 2026+). Use such a kernel, or COMPILE_BACKEND=docker.',
      issues: [],
      log: '',
      output: '',
    };
  }

  const { dir, pathOf } = await materialize(project.id);
  const mainPath = project.main_file_id && pathOf.get(project.main_file_id);
  if (!mainPath || !mainPath.toLowerCase().endsWith('.tex')) {
    return { status: 'failure', message: 'No main .tex file selected', issues: [], log: '', output: '' };
  }

  const refused = engineRefusal(project.compiler);
  if (refused) {
    return {
      status: 'failure',
      backend,
      message: refused,
      issues: [{ level: 'error', file: null, line: null, message: refused, content: '' }],
      log: '',
      output: '',
    };
  }

  const jobname = 'output';
  const engine = ENGINES[project.compiler] || ENGINES.pdflatex;
  // Remember which engine produced the cached aux files; switching engines needs a clean build.
  const engineStamp = path.join(dir, '.webtex-engine');
  const previousEngine = await fs.readFile(engineStamp, 'utf8').catch(() => null);
  if (previousEngine && previousEngine !== engine) {
    for (const f of await fs.readdir(dir)) if (f.startsWith(`${jobname}.`)) await fs.rm(path.join(dir, f), { force: true });
  }
  await fs.writeFile(engineStamp, engine);

  // A project's own latexmkrc is Perl code. Outside the per-compile Docker sandbox (and always in
  // integration mode) it must not run: it would be shell escape in disguise.
  const allowRc = backend === 'docker' && !config.integration.enabled;
  const guard = backend === 'docker' ? `${DOCKER_GUARD_DIR}/biber-guard.pl` : path.join(GUARD_DIR, 'biber-guard.pl');
  const latexmkArgs = [
    ...(allowRc ? [] : ['-norc']),
    // Applied after any rc file, so a project latexmkrc cannot replace the guarded biber either.
    '-e', `$biber = q{perl "${guard}" %O %S}`,
    engine,
    '-g', // always run at least once: an explicit compile must produce fresh output and logs
    stopOnFirstError ? '-halt-on-error' : '-f',
    '-interaction=nonstopmode',
    '-synctex=1',
    '-file-line-error',
    `-jobname=${jobname}`,
    mainPath,
  ];
  const timeoutMs = config.compiler.timeoutSec * 1000;

  let result;
  if (backend === 'local') {
    const env = localTexEnv(dir);
    if (process.platform !== 'win32') {
      for (const d of [env.HOME, env.TEXMFVAR, env.TMPDIR]) await fs.mkdir(d, { recursive: true });
    }
    if (isolation.landlock) {
      const ro = [...SANDBOX_RO, ...isolation.texRoots, GUARD_DIR, ...(env.PAR_GLOBAL_TMPDIR ? [env.PAR_GLOBAL_TMPDIR] : [])];
      const sandboxArgs = [
        SANDBOX,
        '--rw', dir,
        ...ro.flatMap((p) => ['--ro', p]),
        ...SANDBOX_DEVICES.flatMap((p) => ['--dev', p]),
        '--', 'latexmk', ...latexmkArgs,
      ];
      result = await runProcess('perl', sandboxArgs, { cwd: dir, timeoutMs, env });
    } else {
      result = await runProcess('latexmk', latexmkArgs, { cwd: dir, timeoutMs, env });
    }
  } else {
    const name = `webtex-${project.id.replace(/[^\w-]/g, '')}-${newId(4).replace(/[^\w]/g, '')}`;
    const args = [
      'run', '--rm', '--name', name,
      // A real PID 1 (tini): GNU timeout 9.x fails with 125 when it is PID 1 itself; also reaps zombies.
      '--init',
      '--network', 'none',
      '--memory', config.compiler.memory,
      '--cpus', String(config.compiler.cpus),
      '--pids-limit', '512',
      '--security-opt', 'no-new-privileges',
      ...Object.entries(TEX_ENV).flatMap(([k, v]) => ['-e', `${k}=${v}`]),
      '-v', `${dir}:${DOCKER_ROOT}`,
      '-v', `${GUARD_DIR}:${DOCKER_GUARD_DIR}:ro`,
      '-w', DOCKER_ROOT,
    ];
    if (typeof process.getuid === 'function') args.push('--user', `${process.getuid()}:${process.getgid()}`);
    args.push(config.compiler.image, 'timeout', '-s', 'KILL', String(config.compiler.timeoutSec), 'latexmk', ...latexmkArgs);
    result = await runProcess('docker', args, {
      timeoutMs: timeoutMs + 15000,
      onTimeout: () => runProcess('docker', ['kill', name], { timeoutMs: 10000 }),
    });
    if (result.code === 137) result.timedOut = true;
  }

  const roots = [dir, DOCKER_ROOT];
  const log = await fs.readFile(path.join(dir, `${jobname}.log`), 'utf8').catch(() => '');
  const issues = [...parseLog(log, { roots }), ...parseLatexmkOutput(result.out)];
  // Only a PDF written by this run counts (small slack for filesystem timestamp granularity).
  const pdfStat = await fs.stat(path.join(dir, 'output.pdf')).catch(() => null);
  const hasPdf = !!pdfStat && pdfStat.mtimeMs >= started - 2000;

  let buildId = null;
  if (hasPdf || log) {
    buildId = `${Date.now().toString(36)}-${newId(4)}`;
    const out = paths.build(project.id, buildId);
    await fs.mkdir(out, { recursive: true });
    for (const f of hasPdf ? ['output.pdf', 'output.synctex.gz', 'output.log'] : ['output.log']) {
      await fs.copyFile(path.join(dir, f), path.join(out, f)).catch(() => {});
    }
    await fs.writeFile(path.join(out, 'latexmk.txt'), result.out);
    await pruneBuilds(project.id);
  }

  let status = hasPdf ? 'success' : 'failure';
  if (result.timedOut) status = 'timedout';
  return {
    status,
    backend,
    buildId,
    hasPdf,
    issues,
    durationMs: Date.now() - started,
    log,
    output: result.out,
  };
}

async function pruneBuilds(projectId) {
  const dir = paths.outputs(projectId);
  const builds = (await fs.readdir(dir).catch(() => [])).sort();
  for (const b of builds.slice(0, Math.max(0, builds.length - config.compiler.keepBuilds))) {
    await fs.rm(path.join(dir, b), { recursive: true, force: true });
  }
}

export async function clearCache(projectId) {
  await fs.rm(paths.compileDir(projectId), { recursive: true, force: true });
}
