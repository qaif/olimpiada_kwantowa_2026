// Auto-compile pacing. While the server's compile queue is busy (deadline rush) every automatic
// compile only adds to the line, so the editor waits longer between them:
//   - after a refusal (503 compiler_busy / 429 too_many_compiles) exponentially, at least the server's
//     Retry-After, at most a minute,
//   - after a compile that had to wait in the queue, by half that wait (at most 20 s more).
export const AUTO_DELAY_MS = 2500;
const MAX_DELAY_MS = 60_000;

export function autoCompileDelay({ refusals = 0, retryAfterSec = 0, queuedMs = 0 } = {}) {
  if (refusals > 0) {
    const exp = AUTO_DELAY_MS * 2 ** Math.min(refusals, 5);
    return Math.min(MAX_DELAY_MS, Math.max(retryAfterSec * 1000, exp));
  }
  return AUTO_DELAY_MS + Math.min(20_000, Math.max(0, queuedMs) / 2);
}

/** True for the answers that mean "the compiler is busy, try later" (not a compile failure). */
export function isBusyError(err) {
  return (err?.status === 503 && err.data?.error === 'compiler_busy') || (err?.status === 429 && err.data?.error === 'too_many_compiles');
}
