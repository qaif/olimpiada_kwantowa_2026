import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import express from 'express';
import { config, configErrors } from './config.js';
import { authRouter, loadUser, userFromCookieHeader } from './auth.js';
import { attachCollab, flushAll } from './collab.js';
import { compilerStatus, joinRouter, projectsRouter, roleFor } from './projects.js';
import { integrationRouter } from './integration.js';
import { aiRouter } from './ai.js';
import { llmConfig } from './llm/index.js';

for (const problem of configErrors()) {
  console.error(`webTEX: configuration error: ${problem}`);
  process.exitCode = 1;
}
if (process.exitCode) process.exit();

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', 'loopback');

app.use((req, res, next) => {
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');
  res.setHeader('Referrer-Policy', 'same-origin');
  next();
});
// Before express.json: signed integration requests need the raw body.
app.use('/api/integration', integrationRouter);
app.use(express.json({ limit: '5mb' }));
app.use(loadUser);

app.get('/api/health', async (_req, res) => res.json({ ok: true, ...(await compilerStatus()) }));
app.use('/api/auth', authRouter);
app.use('/api/projects', projectsRouter);
app.use('/api/join', joinRouter);
app.use('/api/ai', aiRouter);
app.use('/api', (_req, res) => res.status(404).json({ error: 'Not found' }));

// Static client (production build). In development, Vite serves the client and proxies /api + /ws.
if (fs.existsSync(config.clientDist)) {
  app.use(express.static(config.clientDist, { index: false, maxAge: '1h' }));
  app.get('/{*splat}', (_req, res) => res.sendFile(path.join(config.clientDist, 'index.html')));
} else {
  app.get('/', (_req, res) =>
    res.type('text').send('webTEX API is running. Build the client with `npm run build` or use `npm run dev`.'),
  );
}

app.use((err, _req, res, _next) => {
  const status = err.status || (err.code === 'LIMIT_FILE_SIZE' ? 413 : 500);
  if (status >= 500) console.error(err);
  res.status(status).json({ error: status >= 500 ? 'Internal server error' : err.message });
});

const server = http.createServer(app);

attachCollab(server, (req, projectId) => {
  const user = userFromCookieHeader(req.headers.cookie);
  if (!user) return null;
  const role = roleFor(user.id, projectId);
  return role ? { user, role } : null;
});

server.listen(config.port, config.host, async () => {
  const { backend } = await compilerStatus();
  console.log(`webTEX listening on http://localhost:${config.port}  (data: ${config.dataDir}, compiler: ${backend})`);
  if (config.integration.enabled) console.log(`Olympiad platform integration enabled (issuer: ${config.integration.issuer})`);
  const llm = llmConfig();
  for (const problem of llm.errors) console.warn(`webTEX: AI assistant configuration: ${problem}`);
  if (llm.enabled) {
    const list = llm.providers.map((p) => `${p.id} (${p.type})`).join(', ') || 'none';
    console.log(`AI assistant enabled, providers: ${list}${config.integration.enabled ? ' — off per project unless the platform enables it' : ''}`);
  }
});

async function shutdown() {
  console.log('Shutting down, saving documents…');
  await flushAll().catch((err) => console.error(err));
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
