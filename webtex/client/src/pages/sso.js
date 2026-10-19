// /sso#token=… — entry point from the olympiad platform (TEX-01). The one-time token travels in the
// URL fragment (never sent to servers or in Referer); we remove it from the address bar at once.
import { api, session } from '../api.js';
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { prefs } from '../prefs.js';
import { navigate } from '../main.js';

const MESSAGES = { 400: 'sso.invalid', 409: 'sso.used', 410: 'sso.expired' };

export function renderSso(root) {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  history.replaceState(null, '', '/sso');

  const msg = h('p', t('sso.opening'));
  root.replaceChildren(h('div.auth-page', h('div.auth-card', msg)));

  const fail = (key, returnUrl) => {
    const back = returnUrl || session.platformUrl;
    msg.replaceChildren(
      h('strong', t('sso.failed')),
      h('br'),
      t(key),
      back ? h('p', h('a.btn.primary', { href: back }, t('sso.back'))) : null,
    );
  };
  if (!token) {
    fail('sso.noToken');
    return;
  }

  let cancelled = false;
  api
    .post('/api/integration/sso', { token })
    .then(async ({ projectId, lang }) => {
      if (cancelled) return;
      if (lang) {
        prefs.set('lang', lang);
        document.documentElement.lang = lang;
      }
      await session.refresh();
      navigate(`/project/${projectId}`, { replace: true });
    })
    .catch((err) => {
      if (!cancelled) fail(MESSAGES[err.status] || 'sso.error', err.data?.returnUrl);
    });
  return () => {
    cancelled = true;
  };
}
