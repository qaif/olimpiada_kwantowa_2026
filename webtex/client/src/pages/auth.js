import { api, session } from '../api.js';
import { h } from '../dom.js';
import { t } from '../i18n.js';
import { navigate } from '../main.js';

function renderPlatformOnly(root) {
  const url = session.platformUrl;
  root.replaceChildren(
    h(
      'div.auth-page',
      h(
        'div.auth-card',
        h('div.brand.brand-lg', h('span.brand-mark', 'TEX'), h('span.brand-name', 'web', h('b', 'TEX'))),
        h('p.auth-tagline', t('integration.loginMessage')),
        url ? h('a.btn.primary.wide', { href: url }, t('integration.toPlatform')) : null,
      ),
    ),
  );
}

export function renderAuth(root, mode) {
  if (session.integration) return renderPlatformOnly(root);
  const isRegister = mode === 'register';
  const error = h('div.form-error', { role: 'alert' });
  const fields = {
    email: h('input.input', { type: 'email', name: 'email', autocomplete: 'email', required: true }),
    name: h('input.input', { type: 'text', name: 'name', autocomplete: 'name' }),
    password: h('input.input', {
      type: 'password',
      name: 'password',
      required: true,
      minLength: isRegister ? 8 : undefined,
      autocomplete: isRegister ? 'new-password' : 'current-password',
    }),
  };
  const submit = h('button.btn.primary.wide', { type: 'submit' }, isRegister ? t('auth.register') : t('auth.login'));

  const form = h(
    'form.auth-form',
    {
      onsubmit: async (e) => {
        e.preventDefault();
        error.textContent = '';
        submit.disabled = true;
        try {
          const body = { email: fields.email.value, password: fields.password.value };
          if (isRegister) body.name = fields.name.value;
          const r = await api.post(isRegister ? '/api/auth/register' : '/api/auth/login', body);
          session.user = r.user;
          navigate(new URLSearchParams(location.search).get('next') || '/', { replace: true });
        } catch (err) {
          error.textContent = err.message;
          submit.disabled = false;
        }
      },
    },
    h('label.field', h('span.field-label', t('auth.email')), fields.email),
    isRegister ? h('label.field', h('span.field-label', t('auth.name')), fields.name) : null,
    h(
      'label.field',
      h('span.field-label', t('auth.password')),
      fields.password,
      isRegister ? h('span.field-hint', t('auth.passwordHint')) : null,
    ),
    error,
    submit,
  );

  const qs = location.search;
  const switcher =
    isRegister || session.allowRegistration
      ? h(
          'p.auth-switch',
          isRegister ? t('auth.haveAccount') : t('auth.noAccount'),
          ' ',
          h('a', { href: (isRegister ? '/login' : '/register') + qs, 'data-link': '' }, isRegister ? t('auth.signIn') : t('auth.signUp')),
        )
      : null;

  root.replaceChildren(
    h(
      'div.auth-page',
      h(
        'div.auth-card',
        h('div.brand.brand-lg', h('span.brand-mark', 'TEX'), h('span.brand-name', 'web', h('b', 'TEX'))),
        h('p.auth-tagline', t('app.tagline')),
        h('h1.auth-title', isRegister ? t('auth.register') : t('auth.login')),
        form,
        switcher,
      ),
    ),
  );
  fields.email.focus();
}
