// "History" panel: the project's versions (server/history.js), each compared with the project now
// (per-file unified diff), downloadable as .zip, and restorable whole or file by file as a new change.
import { api } from '../api.js';
import { confirmDialog, h, icon, prompt, toast } from '../dom.js';
import { currentLang, t } from '../i18n.js';
import { unifiedDiff } from '../../../server/linediff.js';

const IMAGE = /\.(png|jpe?g|gif|svg|webp|bmp)$/i;
const MAX_DIFF_LINES = 4000;

export class HistoryPanel {
  /**
   * opts: { projectId, getText(entityId) → string|null (live document text), onClose }
   */
  constructor(host, opts) {
    this.host = host;
    this.opts = opts;
    this.snapshots = [];
    this.canRestore = false;
    this.selectedId = null;
    this.details = null;
    this.fileId = null;
    this.showSame = false;
    this.notice = null; // { text, undoId }

    this.listEl = h('div.history-list', { role: 'listbox', 'aria-label': t('history.versions') });
    this.detailEl = h('div.history-detail');
    this.saveBtn = h('button.btn.small', { hidden: true, onclick: () => this.saveVersion() }, icon('bookmark'), h('span.hide-sm', t('history.saveVersion')));
    this.el = h(
      'section.history-panel',
      { 'aria-label': t('history.title') },
      h(
        'div.history-head',
        icon('history'),
        h('h2.history-title', t('history.title')),
        h('div.spacer'),
        this.saveBtn,
        h('button.icon-btn', { title: t('common.close'), 'aria-label': t('common.close'), onclick: () => opts.onClose?.() }, icon('x')),
      ),
      h('div.history-body', this.listEl, this.detailEl),
    );
    host.replaceChildren(this.el);
    this.refresh();
  }

  url(path = '') {
    return `/api/projects/${this.opts.projectId}/history${path}`;
  }

  async refresh() {
    let r;
    try {
      r = await api.get(this.url());
    } catch (err) {
      this.listEl.replaceChildren(h('div.empty-state', err.message));
      return;
    }
    this.snapshots = r.snapshots;
    this.canRestore = r.canRestore;
    this.keepDays = r.keepDays;
    this.saveBtn.hidden = !this.canRestore;
    if (!this.snapshots.some((s) => s.id === this.selectedId)) this.selectedId = this.snapshots[0]?.id ?? null;
    this.renderList();
    await this.loadDetails();
  }

  // The project changed: the comparison with "now" is stale.
  projectChanged() {
    clearTimeout(this.changeTimer);
    this.changeTimer = setTimeout(() => this.selectedId && this.loadDetails({ keepFile: true }), 800);
  }

  renderList() {
    if (!this.snapshots.length) {
      this.listEl.replaceChildren(h('div.empty-state', t('history.empty')));
      return;
    }
    const items = [];
    let day = null;
    for (const s of this.snapshots) {
      const d = new Date(s.createdAt).toLocaleDateString(currentLang(), { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' });
      if (d !== day) {
        day = d;
        items.push(h('div.history-day', d));
      }
      const who = names(s);
      items.push(
        h(
          'button.history-item',
          {
            role: 'option',
            'aria-selected': String(s.id === this.selectedId),
            class: `kind-${s.kind}${s.id === this.selectedId ? ' selected' : ''}`,
            onclick: () => this.select(s.id),
          },
          h('span.history-time', formatTime(s.createdAt)),
          h('span.history-label', snapshotTitle(s)),
          who ? h('span.history-who.muted.small', who) : null,
        ),
      );
    }
    this.listEl.replaceChildren(...items);
  }

  select(id) {
    if (id === this.selectedId) return;
    this.selectedId = id;
    this.fileId = null;
    this.renderList();
    this.loadDetails();
  }

  async loadDetails({ keepFile = false } = {}) {
    const id = this.selectedId;
    if (!id) {
      this.details = null;
      this.detailEl.replaceChildren(h('div.empty-state', t('history.emptyHint', { days: this.keepDays })));
      return;
    }
    if (!keepFile) this.detailEl.replaceChildren(h('div.empty-state', t('common.loading')));
    try {
      const d = await api.get(this.url(`/${id}`));
      if (id !== this.selectedId) return;
      this.details = d;
    } catch (err) {
      this.detailEl.replaceChildren(h('div.empty-state', err.message));
      return;
    }
    const files = this.details.files;
    if (!keepFile || !files.some((f) => f.id === this.fileId)) {
      // Open the first changed file; the main file when nothing changed.
      this.fileId = (files.find((f) => f.status !== 'same') || files.find((f) => f.id === this.details.mainFileId) || files[0])?.id ?? null;
    }
    this.renderDetails();
  }

  renderDetails() {
    const { snapshot: s, files } = this.details;
    const changed = files.filter((f) => f.status !== 'same');
    const same = files.filter((f) => f.status === 'same');
    const authors = names(s);
    const head = h(
      'div.history-detail-head',
      h(
        'div.history-detail-title',
        h('strong', snapshotTitle(s)),
        h('div.muted.small', formatDateTime(s.createdAt), authors ? ` · ${authors}` : ''),
      ),
      h('div.spacer'),
      h('a.btn.small', { href: this.url(`/${s.id}/download`), download: '' }, icon('download'), h('span.hide-sm', t('history.downloadZip'))),
      this.canRestore ? h('button.btn.small.primary', { onclick: () => this.restore() }, icon('rotateCcw'), t('history.restoreAll')) : null,
    );
    const notice = this.notice
      ? h(
          'div.history-notice',
          h('span', this.notice.text),
          this.notice.undoId && this.canRestore ? h('button.btn.small', { onclick: () => this.undo() }, t('history.undoRestore')) : null,
          h('button.icon-btn', { 'aria-label': t('common.close'), onclick: () => ((this.notice = null), this.renderDetails()) }, icon('x', 14)),
        )
      : null;
    const fileRow = (f) =>
      h(
        'button.history-file',
        { class: f.id === this.fileId ? 'selected' : '', onclick: () => ((this.fileId = f.id), this.renderDetails()) },
        icon(f.kind === 'doc' ? 'fileText' : IMAGE.test(f.path) ? 'image' : 'file', 14),
        h('span.history-file-path', f.path, f.currentPath && f.currentPath !== f.path ? h('span.muted', ` → ${f.currentPath}`) : null),
        f.lines ? h('span.history-lines', f.lines.removed ? h('span.del', `−${f.lines.removed}`) : null, f.lines.added ? h('span.add', `+${f.lines.added}`) : null) : null,
        h('span.history-status', { class: `status-${f.status}` }, t(`history.status.${f.status}`)),
      );
    const fileList = h(
      'div.history-files',
      changed.length ? changed.map(fileRow) : h('div.muted.small.history-same-note', t('history.noChanges')),
      same.length
        ? h(
            'button.history-toggle-same',
            { onclick: () => ((this.showSame = !this.showSame), this.renderDetails()) },
            icon(this.showSame ? 'chevronDown' : 'chevronRight', 14),
            t('history.unchanged', { n: same.length }),
          )
        : null,
      this.showSame ? same.map(fileRow) : null,
    );
    const file = files.find((f) => f.id === this.fileId);
    this.detailEl.replaceChildren(...[head, notice, h('div.history-split', fileList, h('div.history-diff', file ? this.renderFile(file) : null))].filter(Boolean));
    if (file?.kind === 'doc') this.loadDiff(file);
  }

  renderFile(f) {
    const tools = h(
      'div.history-diff-head',
      h('span.mono', f.path),
      h('span.muted.small', t('history.legend')),
      h('div.spacer'),
      f.status !== 'added'
        ? h('a.icon-btn', { href: this.url(`/${this.selectedId}/files/${f.id}`), target: '_blank', rel: 'noopener', title: t('history.openVersionFile') }, icon('download'))
        : null,
      this.canRestore && f.status !== 'same' && f.status !== 'added'
        ? h('button.btn.small', { onclick: () => this.restore(f) }, icon('rotateCcw', 14), t('history.restoreFile'))
        : null,
    );
    if (f.kind === 'doc') return [tools, h('div.history-diff-body', h('div.muted.small', t('common.loading')))];
    const version = f.status === 'added' ? null : this.url(`/${this.selectedId}/files/${f.id}`);
    const current = f.status === 'deleted' ? null : `/api/projects/${this.opts.projectId}/files/${f.id}`;
    const pane = (label, src) =>
      h('figure.history-binary', h('figcaption.muted.small', label), src ? (IMAGE.test(f.path) ? h('img', { src, alt: f.path }) : h('a', { href: src, target: '_blank', rel: 'noopener' }, t('dash.download'))) : h('span.muted', '—'));
    return [
      tools,
      h(
        'div.history-diff-body',
        h('p.muted.small', t(f.status === 'same' ? 'history.binarySame' : 'history.binaryChanged')),
        h('div.history-binaries', pane(t('history.thisVersion'), version), pane(t('history.now'), current)),
      ),
    ];
  }

  async loadDiff(f) {
    const body = this.detailEl.querySelector('.history-diff-body');
    let before = '';
    if (f.status !== 'added') {
      try {
        const res = await fetch(this.url(`/${this.selectedId}/files/${f.id}`), { credentials: 'same-origin' });
        if (!res.ok) throw new Error(res.statusText);
        before = await res.text();
      } catch (err) {
        body.replaceChildren(h('div.muted', err.message));
        return;
      }
    }
    if (f.id !== this.fileId || !body.isConnected) return;
    const now = f.status === 'deleted' ? '' : (this.opts.getText(f.id) ?? '');
    body.replaceChildren(renderDiff(before, now));
  }

  async restore(file = null) {
    const s = this.details.snapshot;
    const when = formatDateTime(s.createdAt);
    const ok = await confirmDialog({
      title: file ? t('history.restoreFile') : t('history.restoreAll'),
      message: file ? t('history.restoreFileConfirm', { path: file.path, when }) : t('history.restoreAllConfirm', { when }),
      okLabel: t('history.restore'),
      cancelLabel: t('common.cancel'),
    });
    if (!ok) return;
    try {
      const r = await api.post(this.url(`/${s.id}/restore`), file ? { entityId: file.id } : {});
      this.notice = { text: file ? t('history.restoredFile', { path: file.path, when }) : t('history.restoredAll', { when }), undoId: r.undoId };
      toast(this.notice.text);
      this.selectedId = r.snapshotId || this.selectedId;
      await this.refresh();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async undo() {
    const id = this.notice?.undoId;
    if (!id) return;
    try {
      const r = await api.post(this.url(`/${id}/restore`), {});
      this.notice = { text: t('history.undone'), undoId: r.undoId };
      this.selectedId = r.snapshotId || this.selectedId;
      await this.refresh();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async saveVersion() {
    const label = await prompt({ title: t('history.saveVersion'), label: t('history.versionName'), okLabel: t('common.save'), cancelLabel: t('common.cancel') });
    if (!label) return;
    try {
      const r = await api.post(this.url(), { label });
      this.selectedId = r.snapshot.id;
      await this.refresh();
      toast(t('history.saved'));
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  destroy() {
    clearTimeout(this.changeTimer);
    this.el.remove();
  }
}

function renderDiff(before, now) {
  if (before === now) return h('div.muted.small.history-same-note', t('history.fileSame'));
  const groups = unifiedDiff(before, now, 3);
  const rows = [];
  let shown = 0;
  for (const g of groups) {
    rows.push(h('div.diff-hunk', `@@ −${g.aStart + 1} +${g.bStart + 1} @@`));
    for (const l of g.lines) {
      if (shown++ > MAX_DIFF_LINES) break;
      rows.push(
        h(
          'div.diff-line',
          { class: l.type === '-' ? 'del' : l.type === '+' ? 'add' : '' },
          h('span.diff-no', l.a ?? ''),
          h('span.diff-no', l.b ?? ''),
          h('span.diff-sign', l.type === ' ' ? '' : l.type === '-' ? '−' : '+'),
          h('span.diff-text', l.text.replace(/\n$/, '')),
        ),
      );
    }
    if (shown > MAX_DIFF_LINES) {
      rows.push(h('div.diff-hunk', t('history.diffTruncated')));
      break;
    }
  }
  return h('div.diff', { role: 'table' }, rows);
}

const formatTime = (ts) => new Date(ts).toLocaleTimeString(currentLang(), { hour: '2-digit', minute: '2-digit' });
const formatDateTime = (ts) => new Date(ts).toLocaleString(currentLang(), { dateStyle: 'medium', timeStyle: 'short' });

function names(s) {
  const list = [...s.authors];
  if (s.actor && !list.some((a) => a.id === s.actor.id)) list.push(s.actor);
  return list.map((u) => u.name || t('history.unknownUser')).join(', ');
}

export function snapshotTitle(s) {
  const when = s.meta?.fromCreatedAt ? formatDateTime(s.meta.fromCreatedAt) : '';
  switch (s.kind) {
    case 'submission':
      return s.label || t('history.kind.submission');
    case 'manual':
      return s.label || t('history.kind.manual');
    case 'compile':
      return t('history.kind.compile');
    case 'pre-restore':
      return t('history.kind.preRestore', { when });
    case 'restore':
      return s.meta?.path ? t('history.kind.restoreFile', { path: s.meta.path, when }) : t('history.kind.restore', { when });
    default:
      return t('history.kind.auto');
  }
}
