// Ranking help for completions: how often and how recently this browser picked each completion.
// Stored only in localStorage (nothing leaves the browser). CodeMirror's fuzzy matcher still decides
// whether an option matches and how well; the boost from here orders options of similar match quality.

export const USAGE_KEY = 'webtex:completion-usage';
export const HALF_LIFE_MS = 7 * 24 * 3600 * 1000;
export const MAX_USAGE_BOOST = 40;
const MAX_ENTRIES = 400;

/** Frecency boost (0..MAX_USAGE_BOOST) for an entry { n: times picked, t: last pick (ms) }. */
export function usageBoost(entry, now = Date.now()) {
  if (!entry || !entry.n) return 0;
  const age = Math.max(0, now - (entry.t || 0));
  const decay = 0.5 ** (age / HALF_LIFE_MS);
  return Math.min(MAX_USAGE_BOOST, Math.round(12 * Math.log2(1 + entry.n) * decay + 8 * decay));
}

/**
 * Usage statistics backed by a Storage-like object (localStorage in the browser, a stub in tests).
 * Every read and write is guarded: private windows and blocked storage just mean no history.
 */
export function createUsageStore({ storage = globalThis.localStorage, key = USAGE_KEY, now = () => Date.now() } = {}) {
  let data = {};
  try {
    const parsed = JSON.parse(storage?.getItem(key) || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) data = parsed;
  } catch {}

  const save = () => {
    const ids = Object.keys(data);
    if (ids.length > MAX_ENTRIES) {
      // Keep the most valuable entries.
      const t = now();
      ids.sort((a, b) => usageBoost(data[b], t) - usageBoost(data[a], t) || (data[b].t || 0) - (data[a].t || 0));
      for (const id of ids.slice(MAX_ENTRIES)) delete data[id];
    }
    try {
      storage?.setItem(key, JSON.stringify(data));
    } catch {}
  };

  return {
    /** Records that the completion `id` was picked. */
    record(id) {
      if (!id) return;
      const e = data[id] || { n: 0, t: 0 };
      data[id] = { n: Math.min(e.n + 1, 1e6), t: now() };
      save();
    },
    boost(id) {
      return usageBoost(data[id], now());
    },
    get(id) {
      return data[id];
    },
  };
}
