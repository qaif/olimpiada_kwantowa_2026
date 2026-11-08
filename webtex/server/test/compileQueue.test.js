import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setTimeout as sleep } from 'node:timers/promises';
import { CompileQueue, QueueBusyError } from '../compileQueue.js';

// A job whose end the test controls: `started` tells whether run() was called.
function gate(value) {
  let release;
  const done = new Promise((resolve) => (release = resolve));
  const job = { started: false, waitMs: null };
  job.run = async ({ waitMs }) => {
    job.started = true;
    job.waitMs = waitMs;
    await done;
    return value;
  };
  job.release = () => release();
  return job;
}

const quiet = { warn() {} };
const tick = () => sleep(0);

test('runs at most `concurrency` compiles at once and starts the next when one ends', async () => {
  const q = new CompileQueue({ concurrency: 2, log: quiet });
  const jobs = [1, 2, 3].map((n) => gate(n));
  const results = jobs.map((j, i) => q.submit({ project: `p${i}`, user: `u${i}`, run: j.run }));
  await tick();
  assert.deepEqual(jobs.map((j) => j.started), [true, true, false]);
  assert.equal(q.stats().running, 2);
  assert.equal(q.stats().queued, 1);
  jobs[0].release();
  assert.equal(await results[0], 1);
  await tick();
  assert.equal(jobs[2].started, true);
  jobs[1].release();
  jobs[2].release();
  assert.deepEqual(await Promise.all(results), [1, 2, 3]);
  assert.equal(q.stats().completed, 3);
});

test('never compiles the same project twice at the same time', async () => {
  const q = new CompileQueue({ concurrency: 4, log: quiet });
  const a = gate('a');
  const b = gate('b');
  const ra = q.submit({ project: 'p', user: 'u1', run: a.run });
  const rb = q.submit({ project: 'p', user: 'u2', run: b.run });
  await tick();
  assert.equal(a.started, true);
  assert.equal(b.started, false);
  assert.deepEqual(q.status('p'), { state: 'queued', position: 1, queued: 1 });
  a.release();
  await ra;
  await tick();
  assert.equal(b.started, true);
  assert.deepEqual(q.status('p'), { state: 'running' });
  b.release();
  assert.equal(await rb, 'b');
  assert.equal(q.status('p'), null);
});

test('submission builds start before interactive compiles that waited longer', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  const order = [];
  const blocker = gate('x');
  q.submit({ project: 'busy', user: 'u0', run: blocker.run });
  const later = (name) => async () => {
    order.push(name);
    return name;
  };
  const r1 = q.submit({ project: 'p1', user: 'u1', run: later('interactive-1') });
  const r2 = q.submit({ project: 'p2', user: 'u2', run: later('interactive-2') });
  const r3 = q.submit({ project: 'p3', user: 'u3', kind: 'build', run: later('build') });
  assert.equal(q.status('p3').position, 1);
  assert.equal(q.status('p1').position, 2);
  blocker.release();
  await Promise.all([r1, r2, r3]);
  assert.deepEqual(order, ['build', 'interactive-1', 'interactive-2']);
});

test('coalesces repeated compiles of a project: only the newest queued one runs, every waiter gets it', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  const blocker = gate('x');
  q.submit({ project: 'other', user: 'u0', run: blocker.run });
  const ran = [];
  const make = (n) => async () => {
    ran.push(n);
    return `result ${n}`;
  };
  const waits = [1, 2, 3].map((n) => q.submit({ project: 'p', user: 'u1', run: make(n) }));
  assert.equal(q.stats().queued, 1);
  assert.equal(q.stats().coalesced, 2);
  blocker.release();
  assert.deepEqual(await Promise.all(waits), ['result 3', 'result 3', 'result 3']);
  assert.deepEqual(ran, [3]);
});

test('a build does not swallow an interactive compile of the same project (different kinds)', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  const blocker = gate('x');
  q.submit({ project: 'other', user: 'u0', run: blocker.run });
  const ri = q.submit({ project: 'p', user: 'u1', run: async () => 'interactive' });
  const rb = q.submit({ project: 'p', user: 'u1', kind: 'build', run: async () => 'build' });
  assert.equal(q.stats().queued, 2);
  blocker.release();
  assert.deepEqual(await Promise.all([ri, rb]), ['interactive', 'build']);
});

test('per-user cap: one running and one queued interactive compile', async () => {
  const q = new CompileQueue({ concurrency: 4, log: quiet });
  const running = gate('a');
  const ra = q.submit({ project: 'pa', user: 'u', run: running.run });
  await tick();
  assert.equal(running.started, true);
  // Second project of the same user: queued, and not started although slots are free.
  const queued = gate('b');
  const rb = q.submit({ project: 'pb', user: 'u', run: queued.run });
  await tick();
  assert.equal(queued.started, false);
  // A third one is refused with 429...
  assert.throws(
    () => q.submit({ project: 'pc', user: 'u', run: async () => 'c' }),
    (err) => err instanceof QueueBusyError && err.status === 429 && err.code === 'too_many_compiles',
  );
  // ...but repeating the queued project coalesces, and other users are not affected.
  const rb2 = q.submit({ project: 'pb', user: 'u', run: queued.run });
  assert.equal(await q.submit({ project: 'pd', user: 'v', run: async () => 'd' }), 'd');
  // Builds skip the per-user cap.
  assert.equal(await q.submit({ project: 'pe', user: 'u', kind: 'build', run: async () => 'e' }), 'e');
  running.release();
  await ra;
  await tick();
  assert.equal(queued.started, true);
  queued.release();
  assert.deepEqual(await Promise.all([rb, rb2]), ['b', 'b']);
  assert.equal(q.stats().rejected, 1);
});

test('full queue: interactive compiles get 503 compiler_busy, builds have their own room', async () => {
  const q = new CompileQueue({ concurrency: 1, maxQueue: 2, log: quiet });
  const blocker = gate('x');
  q.submit({ project: 'p0', user: 'u0', run: blocker.run });
  const waiting = [1, 2].map((n) => q.submit({ project: `p${n}`, user: `u${n}`, run: async () => n }));
  assert.throws(
    () => q.submit({ project: 'p3', user: 'u3', run: async () => 3 }),
    (err) => err instanceof QueueBusyError && err.status === 503 && err.code === 'compiler_busy' && /przeciążony/.test(err.message) && err.retryAfterSec >= 5,
  );
  const build = q.submit({ project: 'p4', user: 'u4', kind: 'build', run: async () => 'build' });
  assert.equal(q.stats().queued, 3);
  blocker.release();
  assert.deepEqual(await Promise.all([...waiting, build]), [1, 2, 'build']);
  assert.equal(q.stats().rejected, 1);
});

test('a compile that waited longer than its kind allows is dropped with compiler_busy', async () => {
  const warnings = [];
  const q = new CompileQueue({ concurrency: 1, maxWaitMs: { build: 20, interactive: 1000 }, log: { warn: (m) => warnings.push(m) } });
  const blocker = gate('x');
  q.submit({ project: 'p0', user: 'u0', run: blocker.run });
  const late = q.submit({ project: 'p1', user: 'u1', kind: 'build', run: async () => 'never' });
  await assert.rejects(late, (err) => err instanceof QueueBusyError && err.code === 'compiler_busy');
  assert.equal(q.stats().expired, 1);
  assert.equal(q.stats().queued, 0);
  assert.match(warnings[0], /dropped after/);
  blocker.release();
});

test('a caller that goes away drops its queued compile', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  const blocker = gate('x');
  q.submit({ project: 'p0', user: 'u0', run: blocker.run });
  const controller = new AbortController();
  let ran = false;
  const gone = q.submit({ project: 'p1', user: 'u1', signal: controller.signal, run: async () => (ran = true) });
  controller.abort(new Error('closed'));
  await assert.rejects(gone, /closed/);
  assert.equal(q.stats().queued, 0);
  assert.equal(q.stats().abandoned, 1);
  blocker.release();
  await tick();
  assert.equal(ran, false);
});

test('a coalesced compile still runs while one of its waiters remains', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  const blocker = gate('x');
  q.submit({ project: 'p0', user: 'u0', run: blocker.run });
  const controller = new AbortController();
  const first = q.submit({ project: 'p1', user: 'u1', signal: controller.signal, run: async () => 'old' });
  const second = q.submit({ project: 'p1', user: 'u1', run: async () => 'new' });
  controller.abort(new Error('closed'));
  await assert.rejects(first, /closed/);
  blocker.release();
  assert.equal(await second, 'new');
});

test('errors of a compile reach its waiters and free the slot', async () => {
  const q = new CompileQueue({ concurrency: 1, log: quiet });
  await assert.rejects(q.submit({ project: 'p', user: 'u', run: async () => { throw new Error('boom'); } }), /boom/);
  assert.equal(await q.submit({ project: 'p', user: 'u', run: async () => 'ok' }), 'ok');
  assert.equal(q.stats().failed, 1);
});

test('reports queue waits and warns (throttled) when a compile waited too long', async () => {
  let clock = 0;
  const warnings = [];
  const q = new CompileQueue({ concurrency: 1, warnWaitMs: 30_000, maxWaitMs: {}, now: () => clock, log: { warn: (m) => warnings.push(m) } });
  const blocker = gate('x');
  q.submit({ project: 'p0', user: 'u0', run: blocker.run });
  const jobs = [1, 2].map((n) => gate(n));
  const waits = jobs.map((j, i) => q.submit({ project: `p${i + 1}`, user: `u${i + 1}`, run: j.run }));
  clock = 40_000;
  assert.equal(q.stats().oldestQueuedMs, 40_000);
  blocker.release();
  await tick();
  await tick();
  assert.equal(jobs[0].waitMs, 40_000);
  clock = 45_000;
  jobs[0].release();
  await waits[0];
  await tick();
  assert.equal(jobs[1].waitMs, 45_000);
  jobs[1].release();
  await waits[1];
  const s = q.stats();
  assert.equal(s.running, 0);
  assert.equal(s.queued, 0);
  assert.equal(s.recentStarted, 3);
  assert.equal(s.maxWaitMs, 45_000);
  assert.equal(s.avgWaitMs, Math.round((0 + 40_000 + 45_000) / 3));
  // Two slow starts within 30 s: one warning line, the second suppressed.
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /compile queue wait 40 s/);
});
