import { api, session } from '../api.js';
import { h, icon, menu } from '../dom.js';
import { LANGUAGES, currentLang, t } from '../i18n.js';
import { prefs } from '../prefs.js';
import { navigate } from '../main.js';

export const brand = () =>
  h('a.brand', { href: '/', 'data-link': '' }, h('span.brand-mark', 'TEX'), h('span.brand-name', 'web', h('b', 'TEX')));

export function initials(name = '?') {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join('');
}

// Stable, pleasant colour per user id.
export function userColor(id = '') {
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  return { color: `hsl(${hue} 70% 45%)`, light: `hsl(${hue} 70% 45% / 0.25)` };
}

export function avatar(user, { title } = {}) {
  const { color } = userColor(user.id);
  return h('span.avatar', { style: { background: color }, title: title || user.name }, initials(user.name));
}

export function userMenuButton() {
  const user = session.user;
  const btn = h('button.avatar-btn', { 'aria-label': user.name }, avatar(user));
  btn.addEventListener('click', () => {
    const theme = prefs.get('theme');
    menu(
      btn,
      [
        { label: user.email ? `${user.name} · ${user.email}` : user.name, disabled: true },
        '-',
        { label: t('editor.themeSystem'), icon: 'columns', checked: theme === 'system', onclick: () => prefs.set('theme', 'system') },
        { label: t('editor.themeLight'), icon: 'sun', checked: theme === 'light', onclick: () => prefs.set('theme', 'light') },
        { label: t('editor.themeDark'), icon: 'moon', checked: theme === 'dark', onclick: () => prefs.set('theme', 'dark') },
        '-',
        ...Object.entries(LANGUAGES).map(([code, label]) => ({
          label,
          checked: currentLang() === code,
          onclick: () => {
            prefs.set('lang', code);
            location.reload();
          },
        })),
        '-',
        {
          label: t('auth.logout'),
          icon: 'logout',
          onclick: async () => {
            await api.post('/api/auth/logout');
            session.user = null;
            navigate('/login');
          },
        },
      ],
      { align: 'right' },
    );
  });
  return btn;
}
