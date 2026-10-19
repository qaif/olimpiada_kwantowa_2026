export class ApiError extends Error {
  constructor(status, message, data = {}) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request(method, url, body) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.message || data.error || res.statusText, data);
  return data;
}

export const api = {
  get: (url) => request('GET', url),
  post: (url, body = {}) => request('POST', url, body),
  patch: (url, body) => request('PATCH', url, body),
  del: (url) => request('DELETE', url),
  upload: (url, form) => request('POST', url, form),
};

export const session = {
  user: null,
  allowRegistration: true,
  // Olympiad platform integration (TEX-01): accounts come only from the platform's SSO.
  integration: false,
  platformUrl: '',
  async refresh() {
    const r = await api.get('/api/auth/me');
    this.user = r.user;
    this.allowRegistration = r.allowRegistration;
    this.integration = !!r.integration;
    this.platformUrl = r.platformUrl || '';
    return r.user;
  },
};
