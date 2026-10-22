// Version history for operators (appeals): list a participant's versions, export one as .zip.
// Runs next to the server (same DATA_DIR), e.g. in the compose service:
//   docker compose exec -T webtex node server/history-cli.js list <platform user id>
//   docker compose exec -T webtex node server/history-cli.js zip <version id> > version.zip
// `list` takes the platform's user id (the SSO `sub`); in standalone mode, a webTEX project id.
import { config } from './config.js';
import { all, one } from './db.js';
import { snapshotZip } from './history.js';

const [command, arg] = process.argv.slice(2);
const iso = (ms) => new Date(ms).toISOString().replace('T', ' ').slice(0, 19);

function usage() {
  console.error('usage: node server/history-cli.js list <platform user id | project id>\n       node server/history-cli.js zip <version id> > version.zip');
  process.exit(2);
}

if (command === 'list' && arg) {
  const rows = all(
    `SELECT s.id, s.created_at, s.kind, s.label, p.name AS project, COALESCE(l.project_key, p.id) AS project_key
       FROM snapshots s
       JOIN projects p ON p.id = s.project_id
       LEFT JOIN linked_projects l ON l.project_id = p.id
       LEFT JOIN users u ON u.id = l.user_id
      WHERE u.external_id = ? OR p.id = ?
      ORDER BY project_key, s.created_at`,
    `${config.integration.issuer}:${arg}`,
    arg,
  );
  if (!rows.length) console.error('no versions found');
  for (const r of rows) console.log([r.id, `${iso(r.created_at)} UTC`, r.kind, r.project_key, r.label || ''].join('\t'));
} else if (command === 'zip' && arg) {
  const row = one(`SELECT project_id FROM snapshots WHERE id = ?`, arg);
  if (!row) {
    console.error('no such version');
    process.exit(1);
  }
  const { zip } = await snapshotZip(row.project_id, arg);
  process.stdout.write(Buffer.from(zip));
} else {
  usage();
}
