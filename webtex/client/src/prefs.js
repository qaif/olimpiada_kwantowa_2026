// Per-browser UI preferences (theme, language, font size, layout…).
const KEY = 'webtex:prefs';
const defaults = {
  theme: 'system',
  lang: null,
  fontSize: 14,
  autoCompile: false,
  layout: 'both',
  sidebarWidth: 240,
  split: 0.5,
  mathPreview: true,
  quickLint: true,
};

let state = { ...defaults };
try {
  state = { ...defaults, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
} catch {}

const listeners = new Set();

export const prefs = {
  get: (k) => state[k],
  set(k, v) {
    state[k] = v;
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {}
    for (const l of listeners) l(k, v);
  },
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

export function applyTheme() {
  const theme = prefs.get('theme');
  const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
prefs.subscribe((k) => k === 'theme' && applyTheme());
