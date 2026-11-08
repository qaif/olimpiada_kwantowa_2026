import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AUTO_DELAY_MS, autoCompileDelay, isBusyError } from '../src/editor/compileBackoff.js';

test('auto-compile waits the usual debounce when the server is not busy', () => {
  assert.equal(autoCompileDelay(), AUTO_DELAY_MS);
  assert.equal(autoCompileDelay({ queuedMs: 0 }), AUTO_DELAY_MS);
});

test('auto-compile waits longer after a compile that sat in the queue', () => {
  assert.equal(autoCompileDelay({ queuedMs: 10_000 }), AUTO_DELAY_MS + 5_000);
  assert.equal(autoCompileDelay({ queuedMs: 600_000 }), AUTO_DELAY_MS + 20_000);
});

test('auto-compile backs off exponentially after refusals, honouring Retry-After, capped at a minute', () => {
  assert.equal(autoCompileDelay({ refusals: 1 }), AUTO_DELAY_MS * 2);
  assert.equal(autoCompileDelay({ refusals: 2 }), AUTO_DELAY_MS * 4);
  assert.equal(autoCompileDelay({ refusals: 1, retryAfterSec: 30 }), 30_000);
  assert.equal(autoCompileDelay({ refusals: 20 }), 60_000);
  assert.equal(autoCompileDelay({ refusals: 1, retryAfterSec: 600 }), 60_000);
});

test('isBusyError recognises only the queue refusals', () => {
  assert.equal(isBusyError({ status: 503, data: { error: 'compiler_busy' } }), true);
  assert.equal(isBusyError({ status: 429, data: { error: 'too_many_compiles' } }), true);
  assert.equal(isBusyError({ status: 503, data: { error: 'compiler_unavailable' } }), false);
  assert.equal(isBusyError({ status: 500, data: {} }), false);
  assert.equal(isBusyError(new Error('x')), false);
});
