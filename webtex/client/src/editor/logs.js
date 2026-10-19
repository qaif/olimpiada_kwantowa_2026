// Compile log panel: grouped issues (click to jump to source), raw log and latexmk output.
import { h, icon } from '../dom.js';
import { t } from '../i18n.js';

export class LogPanel {
  constructor(container, { onJump, onClose }) {
    this.el = h('div.logs', { hidden: true });
    container.append(this.el);
    this.onJump = onJump;
    this.onClose = onClose;
    this.result = null;
  }

  counts(result = this.result) {
    const c = { error: 0, warning: 0, typesetting: 0 };
    for (const i of result?.issues || []) c[i.level]++;
    return c;
  }

  show(visible) {
    this.el.hidden = !visible;
  }

  setResult(result) {
    this.result = result;
    this.render();
  }

  render() {
    const r = this.result;
    if (!r) {
      this.el.replaceChildren(h('div.empty-state', t('editor.noPdf')));
      return;
    }
    const head = h(
      'div.logs-head',
      h('h3', t('editor.logs')),
      h('div.spacer'),
      h('button.btn.small', { onclick: () => this.onClose?.() }, icon('arrowLeft'), t('editor.backToPdf')),
    );
    const items = [];
    if (r.message) items.push(issueCard({ level: 'error', message: r.message, content: '' }));
    const order = { error: 0, warning: 1, typesetting: 2 };
    const sorted = [...(r.issues || [])].sort((a, b) => order[a.level] - order[b.level]);
    for (const issue of sorted) items.push(issueCard(issue, this.onJump));
    if (!items.length) items.push(h('div.log-ok', t('editor.noIssues')));

    const raw = (title, text) =>
      text ? h('details.log-raw', h('summary', title), h('pre', text)) : null;

    this.el.replaceChildren(
      head,
      h('div.logs-body', items, raw(t('editor.rawLog'), r.log), raw(t('editor.latexmkOutput'), r.output)),
    );
  }
}

function issueCard(issue, onJump) {
  const where = issue.file ? `${issue.file}${issue.line ? `:${issue.line}` : ''}` : '';
  const card = h(
    'div.log-item',
    { class: `log-${issue.level}` },
    h(
      'div.log-item-head',
      h('span.log-level', t(issue.level === 'error' ? 'editor.errors' : issue.level === 'warning' ? 'editor.warnings' : 'editor.typesetting')),
      h('span.log-message', issue.message),
      where ? h('button.log-where', { title: where, onclick: () => onJump?.(issue.file, issue.line) }, where) : null,
    ),
    issue.content ? h('pre.log-context', issue.content) : null,
  );
  return card;
}
