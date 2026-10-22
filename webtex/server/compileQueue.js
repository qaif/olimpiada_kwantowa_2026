// Global compile queue. Before a submission deadline hundreds of participants compile at once, and
// every latexmk run in the single webtex container costs memory, PIDs and CPU: without a global
// limit they all start together and the container runs out of memory, killing everyone's compiles,
// including the platform's submission builds. The queue therefore
//   - runs at most `concurrency` compiles at a time, never two of the same project (shared directory),
//   - starts submission builds (POST /api/integration/build) before interactive compiles,
//   - coalesces: a new request for a project whose compile of the same kind is still queued replaces
//     it, and every waiter gets the newest result (auto-compiles pile up while typing),
//   - caps each user at one running and one queued interactive compile,
//   - refuses new work with QueueBusyError when `maxQueue` jobs wait, or when a job waited too long.
// Pure (no TeX, no HTTP): the clock and the logger are injectable for tests.

export const BUSY_MESSAGE =
  'Kompilator jest teraz przeciążony – bardzo dużo osób kompiluje jednocześnie. Spróbuj ponownie za chwilę, Twoja praca jest zapisana.';
export const PER_USER_MESSAGE =
  'Masz już kompilację w kolejce. Poczekaj, aż się zakończy, i spróbuj ponownie.';

export class QueueBusyError extends Error {
  constructor(code, message, { status = 503, retryAfterSec = 10 } = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryAfterSec = retryAfterSec;
  }
}

const KINDS = ['build', 'interactive'];
const WAIT_WINDOW_MS = 5 * 60 * 1000;
const WAIT_SAMPLES = 500;

export class CompileQueue {
  constructor({
    concurrency = 1,
    maxQueue = 100,
    maxWaitMs = { build: 60_000, interactive: 120_000 },
    warnWaitMs = 30_000,
    now = Date.now,
    log = console,
  } = {}) {
    this.concurrency = Math.max(1, Math.floor(concurrency));
    this.maxQueue = Math.max(0, Math.floor(maxQueue));
    this.maxWaitMs = maxWaitMs;
    this.warnWaitMs = warnWaitMs;
    this.now = now;
    this.log = log;
    this.queues = { build: [], interactive: [] };
    this.running = new Map(); // project -> job
    this.waits = []; // { at, ms, kind } of recently started jobs
    this.totals = { started: 0, completed: 0, failed: 0, coalesced: 0, rejected: 0, expired: 0, abandoned: 0 };
    this.lastWarnAt = -Infinity;
    this.suppressedWarnings = 0;
  }

  /**
   * Runs `run()` when a slot is free; resolves with its result (shared by coalesced waiters).
   * Rejects with QueueBusyError when refused, or with the signal's reason when the caller gave up.
   */
  submit({ project, user = null, kind = 'interactive', run, signal } = {}) {
    if (!KINDS.includes(kind)) throw new TypeError(`unknown compile kind ${kind}`);
    if (signal?.aborted) return Promise.reject(signal.reason ?? new Error('aborted'));
    const queue = this.queues[kind];

    const queued = queue.find((j) => j.project === project);
    if (queued) {
      // Keep the older job's place in the line, compile the newest request.
      queued.run = run;
      queued.user = user;
      this.totals.coalesced++;
      return this.#wait(queued, signal);
    }

    if (kind === 'interactive' && user != null && this.queues.interactive.some((j) => j.user === user)) {
      this.totals.rejected++;
      throw new QueueBusyError('too_many_compiles', PER_USER_MESSAGE, { status: 429, retryAfterSec: 5 });
    }
    if (queue.length >= this.maxQueue && !this.#canStartNow(project, user, kind)) {
      this.totals.rejected++;
      throw new QueueBusyError('compiler_busy', BUSY_MESSAGE, { retryAfterSec: this.retryAfterSec() });
    }

    const job = { project, user, kind, run, waiters: [], enqueuedAt: this.now(), timer: null };
    const limit = this.maxWaitMs?.[kind];
    if (limit > 0) {
      job.timer = setTimeout(() => this.#expire(job), limit);
      job.timer.unref?.();
    }
    queue.push(job);
    const promise = this.#wait(job, signal);
    this.#pump();
    return promise;
  }

  /** Where a project's compile is: { state: 'running' } | { state: 'queued', position, queued } | null. */
  status(project) {
    if (this.running.has(project)) {
      const queuedToo = KINDS.some((k) => this.queues[k].some((j) => j.project === project));
      if (!queuedToo) return { state: 'running' };
    }
    let position = 0;
    for (const kind of KINDS) {
      for (const job of this.queues[kind]) {
        position++;
        if (job.project === project) return { state: 'queued', position, queued: this.queuedCount() };
      }
    }
    return this.running.has(project) ? { state: 'running' } : null;
  }

  queuedCount() {
    return this.queues.build.length + this.queues.interactive.length;
  }

  /** Seconds a refused client should wait before retrying, from the current backlog. */
  retryAfterSec() {
    const avg = this.#recentWaits().avg;
    return Math.min(120, Math.max(5, Math.round(avg / 1000) || 10));
  }

  /** Counters for /api/health and the operators (docs/OPERACJE.md § 55). */
  stats() {
    const at = this.now();
    const recent = this.#recentWaits();
    const oldest = (kind) => (this.queues[kind].length ? at - this.queues[kind][0].enqueuedAt : 0);
    return {
      concurrency: this.concurrency,
      maxQueue: this.maxQueue,
      running: this.running.size,
      queued: this.queuedCount(),
      queuedBuilds: this.queues.build.length,
      oldestQueuedMs: Math.max(oldest('build'), oldest('interactive')),
      // Over the jobs started in the last five minutes.
      recentStarted: recent.count,
      avgWaitMs: recent.avg,
      p95WaitMs: recent.p95,
      maxWaitMs: recent.max,
      ...this.totals,
    };
  }

  #wait(job, signal) {
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, signal, onAbort: null };
      if (signal) {
        waiter.onAbort = () => this.#abandon(job, waiter);
        signal.addEventListener('abort', waiter.onAbort, { once: true });
      }
      job.waiters.push(waiter);
    });
  }

  #settle(job, fn) {
    for (const w of job.waiters) {
      w.signal?.removeEventListener('abort', w.onAbort);
      fn(w);
    }
    job.waiters = [];
  }

  #remove(job) {
    const queue = this.queues[job.kind];
    const i = queue.indexOf(job);
    if (i < 0) return false;
    queue.splice(i, 1);
    clearTimeout(job.timer);
    return true;
  }

  #abandon(job, waiter) {
    job.waiters = job.waiters.filter((w) => w !== waiter);
    waiter.reject(waiter.signal.reason ?? new Error('aborted'));
    // Nobody waits for a queued compile any more (browser closed, platform timed out): drop it.
    if (!job.waiters.length && this.#remove(job)) this.totals.abandoned++;
  }

  #expire(job) {
    if (!this.#remove(job)) return;
    this.totals.expired++;
    const waited = Math.round((this.now() - job.enqueuedAt) / 1000);
    this.log.warn?.(`webTEX: ${job.kind} compile of project ${job.project} dropped after ${waited} s in the queue (${this.queuedCount()} queued)`);
    const err = new QueueBusyError('compiler_busy', BUSY_MESSAGE, { retryAfterSec: this.retryAfterSec() });
    this.#settle(job, (w) => w.reject(err));
  }

  #runningInteractive(user) {
    for (const job of this.running.values()) if (job.kind === 'interactive' && job.user === user) return true;
    return false;
  }

  #eligible(job) {
    if (this.running.has(job.project)) return false;
    return job.kind === 'build' || job.user == null || !this.#runningInteractive(job.user);
  }

  #canStartNow(project, user, kind) {
    return this.running.size < this.concurrency && this.#eligible({ project, user, kind });
  }

  #pump() {
    while (this.running.size < this.concurrency) {
      let next = null;
      for (const kind of KINDS) {
        next = this.queues[kind].find((j) => this.#eligible(j));
        if (next) break;
      }
      if (!next) return;
      this.#remove(next);
      this.#start(next);
    }
  }

  #start(job) {
    const at = this.now();
    const waitMs = at - job.enqueuedAt;
    this.running.set(job.project, job);
    this.totals.started++;
    this.waits.push({ at, ms: waitMs, kind: job.kind });
    if (this.waits.length > WAIT_SAMPLES) this.waits.shift();
    if (waitMs > this.warnWaitMs) this.#warnSlow(job, waitMs);

    // The slot is free before the waiters hear the result: status() and stats() are then exact.
    const finish = (counter, settle) => {
      this.running.delete(job.project);
      this.totals[counter]++;
      this.#settle(job, settle);
      this.#pump();
    };
    Promise.resolve()
      .then(() => job.run({ waitMs }))
      .then(
        (result) => finish('completed', (w) => w.resolve(result)),
        (err) => finish('failed', (w) => w.reject(err)),
      );
  }

  // At most one line per 30 s, however many compiles are late: the rush must not flood the log.
  #warnSlow(job, waitMs) {
    const at = this.now();
    if (at - this.lastWarnAt < 30_000) {
      this.suppressedWarnings++;
      return;
    }
    const more = this.suppressedWarnings ? ` (+${this.suppressedWarnings} more since the last warning)` : '';
    this.log.warn?.(
      `webTEX: compile queue wait ${Math.round(waitMs / 1000)} s for a ${job.kind} compile` +
        ` (running ${this.running.size}/${this.concurrency}, queued ${this.queuedCount()})${more}`,
    );
    this.lastWarnAt = at;
    this.suppressedWarnings = 0;
  }

  #recentWaits() {
    const since = this.now() - WAIT_WINDOW_MS;
    const ms = this.waits.filter((w) => w.at >= since).map((w) => w.ms).sort((a, b) => a - b);
    if (!ms.length) return { count: 0, avg: 0, p95: 0, max: 0 };
    const avg = Math.round(ms.reduce((s, v) => s + v, 0) / ms.length);
    return { count: ms.length, avg, p95: ms[Math.min(ms.length - 1, Math.floor(ms.length * 0.95))], max: ms[ms.length - 1] };
  }
}

/** Writes a QueueBusyError as the HTTP answer (503 compiler_busy / 429 too_many_compiles). */
export function sendQueueError(res, err) {
  res.set('Retry-After', String(err.retryAfterSec));
  res.status(err.status).json({ error: err.code, message: err.message, retryAfter: err.retryAfterSec });
}

/** An AbortSignal that fires when the client goes away before the response was sent. */
export function closeSignal(res) {
  const controller = new AbortController();
  // Already gone (e.g. while the route waited for the client's edits to arrive).
  if (res.destroyed) controller.abort(new Error('client closed the connection'));
  res.on('close', () => {
    if (!res.writableFinished) controller.abort(new Error('client closed the connection'));
  });
  return controller.signal;
}
