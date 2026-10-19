// Project file tree with selection, expand/collapse, context menu and drag & drop (move + upload).
import { h, icon, menu } from '../dom.js';
import { t } from '../i18n.js';

const IMAGE_EXT = /\.(png|jpe?g|gif|svg|webp|bmp|pdf|eps)$/i;

export class FileTree {
  /**
   * actions: { open(entity), newFile(parentId), newFolder(parentId), upload(parentId, files?),
   *            rename(entity), remove(entity), setMain(entity), move(entity, parentId) }
   */
  constructor(container, { actions, readOnly = false }) {
    this.el = h('div.filetree', { role: 'tree' });
    container.append(this.el);
    this.actions = actions;
    this.readOnly = readOnly;
    this.entities = [];
    this.selectedId = null;
    this.mainId = null;
    this.expanded = new Set();
    this.dragId = null;

    // Dropping on empty space = project root.
    this.el.addEventListener('dragover', (e) => this.onDragOver(e, null));
    this.el.addEventListener('dragleave', () => this.el.classList.remove('drop-target'));
    this.el.addEventListener('drop', (e) => this.onDrop(e, null));
  }

  setEntities(entities) {
    this.entities = entities;
    this.render();
  }

  setMain(id) {
    this.mainId = id;
    this.render();
  }

  setReadOnly(ro) {
    this.readOnly = ro;
    this.render();
  }

  select(id) {
    this.selectedId = id;
    // Make sure the selected file is visible.
    let e = this.entities.find((x) => x.id === id);
    while (e?.parentId) {
      this.expanded.add(e.parentId);
      e = this.entities.find((x) => x.id === e.parentId);
    }
    this.render();
  }

  /** Folder that "new file" should go into: the selected folder or the selected file's folder. */
  currentFolder() {
    const sel = this.entities.find((x) => x.id === this.selectedId);
    if (!sel) return null;
    return sel.kind === 'folder' ? sel.id : sel.parentId || null;
  }

  children(parentId) {
    return this.entities
      .filter((e) => (e.parentId || null) === parentId)
      .sort((a, b) => (b.kind === 'folder') - (a.kind === 'folder') || a.name.localeCompare(b.name));
  }

  render() {
    const build = (parentId, depth) =>
      this.children(parentId).map((e) => {
        const isFolder = e.kind === 'folder';
        const open = this.expanded.has(e.id);
        const row = h(
          'div.tree-row',
          {
            role: 'treeitem',
            tabIndex: 0,
            'aria-selected': String(e.id === this.selectedId),
            'aria-expanded': isFolder ? String(open) : undefined,
            class: e.id === this.selectedId ? 'selected' : '',
            style: { paddingLeft: `${8 + depth * 14}px` },
            draggable: !this.readOnly,
            title: e.name,
          },
          isFolder ? icon(open ? 'chevronDown' : 'chevronRight', 14) : h('span.tree-indent'),
          icon(isFolder ? (open ? 'folderOpen' : 'folder') : e.kind === 'doc' ? 'fileText' : IMAGE_EXT.test(e.name) ? 'image' : 'file'),
          h('span.tree-name', e.name),
          e.id === this.mainId ? h('span.tree-main', { title: t('editor.mainFile') }, t('editor.main')) : null,
        );
        row.addEventListener('click', () => {
          if (isFolder) {
            if (open) this.expanded.delete(e.id);
            else this.expanded.add(e.id);
            this.selectedId = e.id;
            this.render();
          } else {
            this.actions.open(e);
          }
        });
        row.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            row.click();
          } else if (ev.key === 'F2' && !this.readOnly) this.actions.rename(e);
          else if (ev.key === 'Delete' && !this.readOnly) this.actions.remove(e);
        });
        row.addEventListener('contextmenu', (ev) => {
          ev.preventDefault();
          if (!this.readOnly) this.contextMenu(row, e);
        });
        row.addEventListener('dragstart', (ev) => {
          this.dragId = e.id;
          ev.dataTransfer.effectAllowed = 'move';
          ev.dataTransfer.setData('text/x-webtex-entity', e.id);
        });
        row.addEventListener('dragend', () => (this.dragId = null));
        if (isFolder) {
          row.addEventListener('dragover', (ev) => this.onDragOver(ev, e.id, row));
          row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
          row.addEventListener('drop', (ev) => this.onDrop(ev, e.id, row));
        }
        return [row, isFolder && open ? build(e.id, depth + 1) : null];
      });
    const scroll = this.el.scrollTop;
    this.el.replaceChildren(...build(null, 0).flat(Infinity).filter(Boolean));
    this.el.scrollTop = scroll;
  }

  contextMenu(anchor, e) {
    const isTex = e.kind === 'doc' && e.name.toLowerCase().endsWith('.tex');
    const folderId = e.kind === 'folder' ? e.id : e.parentId || null;
    menu(anchor, [
      { label: t('editor.newFile'), icon: 'filePlus', onclick: () => this.actions.newFile(folderId) },
      { label: t('editor.newFolder'), icon: 'folderPlus', onclick: () => this.actions.newFolder(folderId) },
      { label: t('editor.upload'), icon: 'upload', onclick: () => this.actions.upload(folderId) },
      '-',
      isTex && e.id !== this.mainId ? { label: t('editor.setMain'), icon: 'star', onclick: () => this.actions.setMain(e) } : null,
      e.kind !== 'folder' ? { label: t('dash.download'), icon: 'download', onclick: () => this.actions.download(e) } : null,
      { label: t('common.rename'), icon: 'edit', onclick: () => this.actions.rename(e) },
      { label: t('common.delete'), icon: 'trash', danger: true, onclick: () => this.actions.remove(e) },
    ].filter(Boolean));
  }

  onDragOver(ev, folderId, row) {
    if (this.readOnly) return;
    const types = ev.dataTransfer.types;
    if (!types.includes('Files') && !types.includes('text/x-webtex-entity')) return;
    ev.preventDefault();
    ev.stopPropagation();
    (row || this.el).classList.add('drop-target');
  }

  onDrop(ev, folderId, row) {
    if (this.readOnly) return;
    ev.preventDefault();
    ev.stopPropagation();
    (row || this.el).classList.remove('drop-target');
    const id = ev.dataTransfer.getData('text/x-webtex-entity');
    if (id) {
      const e = this.entities.find((x) => x.id === id);
      if (e && (e.parentId || null) !== folderId && e.id !== folderId) this.actions.move(e, folderId);
    } else if (ev.dataTransfer.files.length) {
      this.actions.upload(folderId, [...ev.dataTransfer.files]);
    }
  }
}

export function buildPaths(entities) {
  const byId = new Map(entities.map((e) => [e.id, e]));
  const out = new Map();
  const path = (e) => {
    if (out.has(e.id)) return out.get(e.id);
    const parent = e.parentId && byId.get(e.parentId);
    const p = parent ? `${path(parent)}/${e.name}` : e.name;
    out.set(e.id, p);
    return p;
  };
  entities.forEach(path);
  return out;
}
