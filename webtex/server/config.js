import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = process.env;

export const config = {
  port: Number(env.PORT || 3000),
  host: env.HOST || '0.0.0.0',
  dataDir: path.resolve(env.DATA_DIR || path.join(root, 'data')),
  clientDist: path.join(root, 'client', 'dist'),
  allowRegistration: env.ALLOW_REGISTRATION !== 'false' && env.WEBTEX_INTEGRATION !== 'true',
  secureCookies: env.SECURE_COOKIES === 'true',
  maxUploadBytes: Number(env.MAX_UPLOAD_MB || 50) * 1024 * 1024,
  compiler: {
    // auto | local | docker
    backend: env.COMPILE_BACKEND || 'auto',
    image: env.COMPILE_IMAGE || 'webtex-texlive',
    timeoutSec: Number(env.COMPILE_TIMEOUT || 90),
    memory: env.COMPILE_MEMORY || '1g',
    cpus: env.COMPILE_CPUS || '2',
    keepBuilds: 3,
    // Local backend: rlimits of every TeX process (server/tex/limits.pl). COMPILE_MEMORY is then the
    // address space per process, not per compile.
    cpuSec: Number(env.COMPILE_CPU_SECONDS || env.COMPILE_TIMEOUT || 90),
    maxFileMb: Number(env.COMPILE_MAX_FILE_MB || 256),
    // Global queue (server/compileQueue.js, docs/OPERACJE.md § 55).
    concurrency: Number(env.COMPILE_CONCURRENCY) || os.availableParallelism(),
    queueMax: Number(env.COMPILE_QUEUE_MAX || 100),
    buildMaxWaitSec: Number(env.COMPILE_BUILD_MAX_WAIT || 60),
    queueMaxWaitSec: Number(env.COMPILE_QUEUE_MAX_WAIT || 120),
    queueWarnSec: Number(env.COMPILE_QUEUE_WARN || 30),
  },
  // Quantum Olympiad platform integration (docs/tasks/TEX-01.md): SSO + signed server-to-server API.
  integration: {
    enabled: env.WEBTEX_INTEGRATION === 'true',
    sharedKey: env.WEBTEX_SHARED_KEY || '',
    issuer: env.WEBTEX_ISSUER || 'olimpiada',
    platformUrl: env.PLATFORM_URL || '',
  },
};

/** Returns a list of fatal configuration problems (empty when the server may start). */
export function configErrors() {
  const errors = [];
  if (config.integration.enabled && config.integration.sharedKey.length < 32) {
    errors.push('WEBTEX_INTEGRATION=true requires WEBTEX_SHARED_KEY with at least 32 characters');
  }
  return errors;
}

export const paths = {
  db: path.join(config.dataDir, 'webtex.db'),
  project: (id) => path.join(config.dataDir, 'projects', id),
  ydoc: (id) => path.join(config.dataDir, 'projects', id, 'ydoc.bin'),
  blob: (id, entityId) => path.join(config.dataDir, 'projects', id, 'blobs', entityId),
  compileDir: (id) => path.join(config.dataDir, 'compiles', id),
  outputs: (id) => path.join(config.dataDir, 'outputs', id),
  build: (id, buildId) => path.join(config.dataDir, 'outputs', id, buildId),
};
