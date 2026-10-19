import './styles.css';
import { session } from './api.js';
import { applyTheme } from './prefs.js';
import { currentLang } from './i18n.js';
import { renderAuth } from './pages/auth.js';
import { renderDashboard } from './pages/dashboard.js';
import { renderJoin } from './pages/join.js';
import { renderSso } from './pages/sso.js';

applyTheme();
document.documentElement.lang = currentLang();

const app = document.getElementById('app');
let cleanup = null;

export function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState(null, '', path);
  else history.pushState(null, '', path);
  route();
}

async function route() {
  cleanup?.();
  cleanup = null;
  const path = location.pathname;
  if (session.user === null) {
    try {
      await session.refresh();
    } catch {}
  }
  const user = session.user;
  let m;

  if (path === '/sso') {
    cleanup = renderSso(app);
    return;
  }

  if (path === '/login' || path === '/register') {
    if (user) return navigate(new URLSearchParams(location.search).get('next') || '/', { replace: true });
    cleanup = renderAuth(app, path === '/register' ? 'register' : 'login');
    return;
  }
  if (!user) {
    const next = path !== '/' ? `?next=${encodeURIComponent(path)}` : '';
    return navigate(`/login${next}`, { replace: true });
  }
  if ((m = /^\/project\/([\w-]+)$/.exec(path))) {
    // The editor (CodeMirror, pdf.js, Yjs) is loaded on demand.
    const { renderEditor } = await import('./pages/editor.js');
    cleanup = await renderEditor(app, m[1]);
    return;
  }
  if ((m = /^\/join\/([\w-]+)$/.exec(path))) {
    cleanup = renderJoin(app, m[1]);
    return;
  }
  cleanup = renderDashboard(app);
}

// Intercept same-origin links marked with data-link.
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-link]');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  navigate(a.getAttribute('href'));
});
window.addEventListener('popstate', route);
route();
