import { api } from '../api.js';
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { navigate } from '../main.js';

export function renderJoin(root, token) {
  const msg = h('p', t('join.title'));
  root.replaceChildren(h('div.auth-page', h('div.auth-card', msg)));
  api
    .post(`/api/join/${token}`)
    .then(({ id }) => navigate(`/project/${id}`, { replace: true }))
    .catch((err) => {
      msg.replaceChildren(
        h('strong', t('join.failed')),
        h('br'),
        err.message,
        h('br'),
        h('a', { href: '/', 'data-link': '' }, t('dash.title')),
      );
    });
}
