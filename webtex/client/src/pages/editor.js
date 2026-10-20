import * as Y from 'yjs';
import { api, session } from '../api.js';
import { confirmDialog, h, icon, menu, modal, prompt, toast } from '../dom.js';
import { LANGUAGES, currentLang, t } from '../i18n.js';
import { prefs } from '../prefs.js';
import { navigate } from '../main.js';
import { ProjectConnection } from '../editor/provider.js';
import { FileTree, buildPaths } from '../editor/filetree.js';
import { LogPanel } from '../editor/logs.js';
import { Outline } from '../editor/outline.js';
import { createEditor, mergeSymbols, scanFile } from '../editor/codemirror.js';
import { PdfViewer } from '../editor/pdfviewer.js';
import { AiPanel } from '../editor/ai.js';
import { avatar, userColor, userMenuButton } from './common.js';

const IMAGE_PREVIEW = /\.(png|jpe?g|gif|svg|webp|bmp)$/i;
const COMPILERS = { pdflatex: 'pdfLaTeX', xelatex: 'XeLaTeX', lualatex: 'LuaLaTeX' };

export async function renderEditor(root, projectId) {
  root.replaceChildren(h('div.page-loading', t('common.loading')));
  let data;
  try {
    data = await api.get(`/api/projects/${projectId}`);
  } catch (err) {
    toast(err.message, 'error');
    navigate('/', { replace: true });
    return null;
  }

  // ---------- state ----------
  let project = data.project;
  let entities = data.entities;
  let paths = buildPaths(entities);
  const readOnly = project.role === 'viewer';
  const ydoc = new Y.Doc();
  const texts = ydoc.getMap('texts');
  const undoManagers = new Map();
  let currentId = null;
  let editor = null;
  let lastResult = null;
  let compiling = false;
  let compileQueued = false;
  let autoTimer = null;
  const disposers = [];
  const lastFileKey = `webtex:last-file:${projectId}`;

  const byId = (id) => entities.find((e) => e.id === id);
  const byPath = (p) => {
    const want = String(p).replace(/^\.\//, '');
    for (const [id, path] of paths) if (path === want) return byId(id);
    return null;
  };

  // ---------- layout ----------
  const statusEl = h('span.conn-status');
  const onlineEl = h('div.online');
  const nameEl = h('button.project-name', { title: readOnly ? project.name : t('common.rename') }, project.name);
  const layoutBtn = h('button.icon-btn', { title: t('editor.layoutBoth') }, icon('columns'));
  const shareBtn = h('button.btn.small', icon('share'), h('span.hide-sm', t('editor.share')));
  const settingsBtn = h('button.icon-btn', { title: t('editor.settings'), 'aria-label': t('editor.settings') }, icon('settings'));
  const backBtn = project.returnUrl
    ? h('a.btn.small', { href: project.returnUrl, title: t('editor.backToOlympiad') }, icon('arrowLeft'), h('span.hide-sm', t('editor.backToOlympiad')))
    : null;
  const zipBtn = h('a.icon-btn', { href: `/api/projects/${projectId}/download`, title: t('editor.downloadZip') }, icon('download'));

  const header = h(
    'header.topbar.editor-top',
    h('a.icon-btn', { href: '/', 'data-link': '', title: t('dash.title'), 'aria-label': t('dash.title') }, icon('home')),
    h('span.brand-mark.small', 'TEX'),
    nameEl,
    readOnly ? h('span.badge', t('editor.readOnly')) : null,
    statusEl,
    h('div.spacer'),
    backBtn,
    onlineEl,
    layoutBtn,
    zipBtn,
    shareBtn,
    settingsBtn,
    userMenuButton(),
  );

  // Sidebar
  const treeHost = h('div.tree-host');
  const outlineHost = h('div.outline-host');
  const sidebarTools = readOnly
    ? []
    : [
        h('button.icon-btn', { title: t('editor.newFile'), onclick: () => newFile(tree.currentFolder()) }, icon('filePlus')),
        h('button.icon-btn', { title: t('editor.newFolder'), onclick: () => newFolder(tree.currentFolder()) }, icon('folderPlus')),
        h('button.icon-btn', { title: t('editor.upload'), onclick: () => upload(tree.currentFolder()) }, icon('upload')),
      ];
  const outlineToggle = h('button.section-title', { 'aria-expanded': 'true' }, icon('chevronDown', 14), t('editor.outline'));
  const sidebar = h(
    'aside.sidebar',
    h('div.section-head', h('span.section-title', t('editor.files')), h('div.spacer'), sidebarTools),
    treeHost,
    h('div.section-head.outline-head', outlineToggle),
    outlineHost,
  );
  outlineToggle.addEventListener('click', () => {
    const open = outlineHost.hidden;
    outlineHost.hidden = !open;
    outlineToggle.setAttribute('aria-expanded', String(open));
    outlineToggle.querySelector('.icon').replaceWith(icon(open ? 'chevronDown' : 'chevronRight', 14));
  });

  // Editor pane
  const filePathEl = h('span.file-path');
  const fmt = (title, ic, fn) => h('button.icon-btn', { title, 'aria-label': title, disabled: readOnly, onclick: fn }, ic);
  const formatBar = h(
    'div.format-bar',
    fmt(t('editor.wordBold'), icon('bold'), () => editor?.wrapSelection('\\textbf{', '}')),
    fmt(t('editor.wordItalic'), icon('italic'), () => editor?.wrapSelection('\\textit{', '}')),
    fmt(t('editor.insertSection'), h('span.txt-icon', '§'), () => editor?.wrapSelection('\\section{', '}')),
    fmt(t('editor.insertMath'), icon('sigma'), () => editor?.wrapSelection('$', '$')),
    fmt(t('editor.insertList'), icon('list'), () => editor?.insertText('\\begin{itemize}\n  \\item \n\\end{itemize}\n')),
    fmt(t('editor.insertFigure'), icon('image'), () =>
      editor?.insertText('\\begin{figure}[h]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{}\n  \\caption{}\n  \\label{fig:}\n\\end{figure}\n'),
    ),
  );
  const forwardBtn = h('button.icon-btn', { title: `${t('editor.goToPdf')} (Ctrl+Alt+→)`, onclick: () => forwardSearch() }, icon('arrowRight'));
  // AI assistant (TEX-02): only when the server says this user may use it on this project.
  const aiBtn = h('button.icon-btn.ai-toggle', { title: t('ai.title'), 'aria-label': t('ai.title'), hidden: !project.ai, onclick: () => toggleAi() }, icon('sparkles'));
  const editorHost = h('div.editor-host');
  const aiHost = h('div.ai-host', { hidden: true });
  const editorPane = h(
    'section.pane.pane-editor',
    h('div.pane-toolbar', filePathEl, h('div.spacer'), formatBar, h('span.toolbar-sep'), forwardBtn, aiBtn),
    h('div.editor-body', editorHost, aiHost),
  );

  // PDF pane
  const compileBtn = h('button.btn.primary.compile-btn', icon('refresh'), h('span', t('editor.recompile')));
  const compileMore = h('button.btn.primary.compile-more', { 'aria-label': t('editor.settings') }, icon('chevronDown', 14));
  const logsBtn = h('button.btn.small.logs-btn', { title: t('editor.logs') }, icon('log'), h('span.log-counts'));
  const pageLabel = h('span.page-label.muted');
  const zoomLabel = h('button.btn.small.zoom-label', '100%');
  const downloadPdf = h('a.icon-btn', { title: t('editor.downloadPdf'), 'aria-disabled': 'true' }, icon('download'));
  const pdfHost = h('div.pdf-host');
  const logsHost = h('div.logs-host');
  const pdfPane = h(
    'section.pane.pane-pdf',
    h(
      'div.pane-toolbar',
      h('div.split-btn', compileBtn, compileMore),
      logsBtn,
      h('div.spacer'),
      pageLabel,
      h('button.icon-btn', { title: '−', onclick: () => pdf.zoomOut() }, icon('minus')),
      zoomLabel,
      h('button.icon-btn', { title: '+', onclick: () => pdf.zoomIn() }, icon('plus')),
      downloadPdf,
    ),
    h('div.pdf-stack', pdfHost, logsHost),
  );

  const split1 = h('div.splitter', { role: 'separator', 'aria-orientation': 'vertical' });
  const split2 = h('div.splitter', { role: 'separator', 'aria-orientation': 'vertical' });
  const workspace = h('div.workspace', sidebar, split1, editorPane, split2, pdfPane);
  const overlay = h('div.page-overlay', { hidden: true });
  const page = h('div.editor-page', header, workspace, overlay);
  root.replaceChildren(page);

  // ---------- components ----------
  const tree = new FileTree(treeHost, {
    readOnly,
    actions: {
      open: (e) => openEntity(e.id),
      newFile,
      newFolder,
      upload,
      rename: renameEntity,
      remove: removeEntity,
      setMain: (e) => patchProject({ mainFileId: e.id }),
      move: (e, parentId) => api.patch(`/api/projects/${projectId}/entities/${e.id}`, { parentId }).catch((err) => toast(err.message, 'error')),
      download: (e) => window.open(`/api/projects/${projectId}/files/${e.id}`, '_blank', 'noopener'),
    },
  });
  tree.setEntities(entities);
  tree.setMain(project.mainFileId);

  const outline = new Outline(outlineHost, { onJump: (line) => editor?.goToLine(line) });

  const logs = new LogPanel(logsHost, {
    onJump: (file, line) => jumpTo(file, line),
    onClose: () => showLogs(false),
  });

  const pdf = new PdfViewer(pdfHost, {
    onInverseSearch: (pageNo, x, y) => inverseSearch(pageNo, x, y),
    onPageChange: (p, n) => (pageLabel.textContent = n ? `${p} / ${n}` : ''),
    onScaleChange: (label) => (zoomLabel.textContent = label),
  });
  pdf.clear(t('editor.noPdf'));
  zoomLabel.addEventListener('click', () =>
    menu(zoomLabel, [
      { label: 'Fit width', checked: pdf.zoomMode === 'fit-width', onclick: () => pdf.setZoom('fit-width') },
      { label: 'Fit page', checked: pdf.zoomMode === 'fit-page', onclick: () => pdf.setZoom('fit-page') },
      '-',
      ...[0.5, 0.75, 1, 1.25, 1.5, 2, 3].map((s) => ({ label: `${s * 100}%`, checked: pdf.zoomMode === s, onclick: () => pdf.setZoom(s) })),
    ]),
  );

  // ---------- collaboration ----------
  const me = session.user;
  const { color, light } = userColor(me.id);
  const conn = new ProjectConnection(projectId, ydoc, {
    onStatus: (s) => {
      statusEl.className = `conn-status conn-${s}`;
      statusEl.textContent = s === 'connected' ? '' : s === 'connecting' ? t('editor.connecting') : t('editor.reconnecting');
    },
    onSynced: () => {
      const last = localStorage.getItem(lastFileKey);
      const initial = (last && byId(last) && last) || project.mainFileId || entities.find((e) => e.kind === 'doc')?.id;
      if (initial) openEntity(initial);
      else showPlaceholder(t('editor.selectFile'));
      // First compile on open, like Overleaf.
      if (project.mainFileId) compile();
    },
    onEvent: handleEvent,
    onClose: () => {
      overlay.hidden = false;
      overlay.replaceChildren(h('div.overlay-card', h('p', t('editor.kicked')), h('a.btn.primary', { href: '/', 'data-link': '' }, t('dash.title'))));
    },
  });
  const awareness = conn.awareness;
  awareness.setLocalStateField('user', { id: me.id, name: me.name, color, colorLight: light });
  awareness.on('change', renderOnline);
  showPlaceholder(t('editor.connecting'));

  function renderOnline() {
    const seen = new Map();
    for (const [clientId, state] of awareness.getStates()) {
      if (clientId === ydoc.clientID || !state.user || state.user.id === me.id) continue;
      seen.set(state.user.id, state);
    }
    onlineEl.replaceChildren(
      ...[...seen.values()].slice(0, 6).map((s) => {
        const file = s.fileId && paths.get(s.fileId);
        const a = avatar(s.user, { title: file ? `${s.user.name} — ${file}` : s.user.name });
        a.classList.add('clickable');
        a.addEventListener('click', () => s.fileId && byId(s.fileId) && openEntity(s.fileId));
        return a;
      }),
    );
  }

  function handleEvent(ev) {
    if (ev.type === 'tree') {
      entities = ev.entities;
      paths = buildPaths(entities);
      tree.setEntities(entities);
      if (currentId && !byId(currentId)) {
        currentId = null;
        const fallback = project.mainFileId && byId(project.mainFileId) ? project.mainFileId : null;
        if (fallback) openEntity(fallback);
        else showPlaceholder(t('editor.selectFile'));
      } else if (currentId) {
        filePathEl.textContent = paths.get(currentId) || '';
      }
      renderOnline();
    } else if (ev.type === 'ai') {
      setAiEnabled(!!ev.enabled, true);
    } else if (ev.type === 'project') {
      Object.assign(project, ev.project);
      nameEl.textContent = project.name;
      document.title = `${project.name} — webTEX`;
      tree.setMain(project.mainFileId);
    }
  }

  // Auto-compile after local edits.
  const onLocalUpdate = (_u, origin) => {
    if (origin === conn || !prefs.get('autoCompile') || readOnly) return;
    clearTimeout(autoTimer);
    autoTimer = setTimeout(() => compile(), 2500);
  };
  ydoc.on('update', onLocalUpdate);

  // A document's text can arrive after we tried to open it (or be replaced by the server).
  texts.observe((ev) => {
    if (!currentId || !ev.keysChanged.has(currentId)) return;
    const id = currentId;
    currentId = null;
    if (texts.get(id)) openEntity(id);
  });

  // ---------- files ----------
  function showPlaceholder(text) {
    editor?.destroy();
    editor = null;
    editorHost.replaceChildren(h('div.empty-state', text));
    filePathEl.textContent = '';
    outline.update(null);
  }

  function undoManagerFor(id, ytext) {
    let um = undoManagers.get(id);
    if (!um || um.scope[0] !== ytext) {
      um = new Y.UndoManager(ytext);
      undoManagers.set(id, um);
    }
    return um;
  }

  // Completion symbols (labels, citation keys, \newcommand…) per file; a file is rescanned only after
  // it changes, so completing in a large project does not re-read every file on each keystroke.
  const scanned = new Map(); // id -> { ytext, path, result }
  const observed = new WeakSet();
  function symbols() {
    const results = [];
    for (const e of entities) {
      if (e.kind !== 'doc' || !/\.(tex|bib|ltx|sty|cls)$/i.test(e.name)) continue;
      const yt = texts.get(e.id);
      if (!yt) continue;
      const path = paths.get(e.id);
      let c = scanned.get(e.id);
      if (!c || c.ytext !== yt || c.path !== path) {
        if (!observed.has(yt)) {
          observed.add(yt);
          yt.observe(() => scanned.delete(e.id));
        }
        c = { ytext: yt, path, result: scanFile(path, yt.toString()) };
        scanned.set(e.id, c);
      }
      results.push(c.result);
    }
    return { ...mergeSymbols(results), files: [...paths.entries()].filter(([id]) => byId(id)?.kind !== 'folder').map(([, p]) => p) };
  }

  let outlineTimer = null;
  function openEntity(id, line) {
    const e = byId(id);
    if (!e || e.kind === 'folder') return;
    if (id === currentId && editor) {
      if (line) editor.goToLine(line);
      return;
    }
    currentId = id;
    tree.select(id);
    filePathEl.textContent = paths.get(id);
    localStorage.setItem(lastFileKey, id);
    awareness.setLocalStateField('fileId', id);
    editor?.destroy();
    editor = null;

    if (e.kind === 'file') {
      outline.update(null);
      const url = `/api/projects/${projectId}/files/${id}`;
      editorHost.replaceChildren(
        IMAGE_PREVIEW.test(e.name)
          ? h('div.file-preview', h('img', { src: url, alt: e.name }))
          : /\.pdf$/i.test(e.name)
            ? h('div.file-preview', h('iframe', { src: url, title: e.name }))
            : h('div.empty-state', h('p', t('editor.binary')), h('a.btn', { href: url, download: e.name }, icon('download'), t('dash.download'))),
      );
      return;
    }

    const ytext = texts.get(id);
    if (!ytext) {
      showPlaceholder(t('common.loading'));
      return;
    }
    editorHost.replaceChildren();
    editor = createEditor({
      parent: editorHost,
      ytext,
      awareness,
      undoManager: undoManagerFor(id, ytext),
      fileName: e.name,
      readOnly,
      fontSize: prefs.get('fontSize'),
      getSymbols: symbols,
      onCompile: () => compile(),
      onForwardSearch: () => forwardSearch(),
      onCursorLine: (l) => outline.setCursorLine(l),
      onDocChanged: () => {
        clearTimeout(outlineTimer);
        outlineTimer = setTimeout(() => outline.update(ytext.toString()), 400);
      },
    });
    outline.update(/\.(tex|ltx)$/i.test(e.name) ? ytext.toString() : null);
    if (line) editor.goToLine(line);
    editor.focus();
  }

  function jumpTo(file, line) {
    const e = byPath(file) || (file && byPath(`${file}.tex`));
    if (!e) return toast(file, 'error');
    openEntity(e.id, line);
    if (line && editor && currentId === e.id) editor.goToLine(line);
  }

  async function newFile(parentId) {
    const name = await prompt({ title: t('editor.newFile'), label: t('editor.fileName'), value: 'chapter.tex', okLabel: t('dash.create'), cancelLabel: t('common.cancel'), select: 7 });
    if (!name) return;
    try {
      const { id } = await api.post(`/api/projects/${projectId}/entities`, { parentId, name, kind: 'doc' });
      // The tree event arrives over the socket; open once it's there.
      waitFor(() => byId(id) && texts.get(id)).then(() => openEntity(id));
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function newFolder(parentId) {
    const name = await prompt({ title: t('editor.newFolder'), label: t('editor.folderName'), okLabel: t('dash.create'), cancelLabel: t('common.cancel') });
    if (!name) return;
    await api.post(`/api/projects/${projectId}/entities`, { parentId, name, kind: 'folder' }).catch((err) => toast(err.message, 'error'));
  }

  function upload(parentId, files) {
    if (!files) {
      const input = h('input', { type: 'file', multiple: true });
      input.addEventListener('change', () => upload(parentId, [...input.files]));
      input.click();
      return;
    }
    if (!files.length) return;
    const form = new FormData();
    if (parentId) form.append('parentId', parentId);
    form.append('paths', JSON.stringify(files.map((f) => f.webkitRelativePath || f.name)));
    for (const f of files) form.append('files', f, f.name);
    toast(t('editor.uploading', { n: files.length }));
    api.upload(`/api/projects/${projectId}/upload`, form).catch((err) => toast(err.message, 'error'));
  }

  async function renameEntity(e) {
    const dot = e.name.lastIndexOf('.');
    const name = await prompt({ title: t('common.rename'), value: e.name, okLabel: t('common.save'), cancelLabel: t('common.cancel'), select: dot > 0 ? dot : true });
    if (!name || name === e.name) return;
    api.patch(`/api/projects/${projectId}/entities/${e.id}`, { name }).catch((err) => toast(err.message, 'error'));
  }

  async function removeEntity(e) {
    const ok = await confirmDialog({
      title: t('common.delete'),
      message: t(e.kind === 'folder' ? 'editor.deleteFolderConfirm' : 'editor.deleteConfirm', { name: e.name }),
      okLabel: t('common.delete'),
      cancelLabel: t('common.cancel'),
      danger: true,
    });
    if (ok) api.del(`/api/projects/${projectId}/entities/${e.id}`).catch((err) => toast(err.message, 'error'));
  }

  async function patchProject(patch) {
    try {
      await api.patch(`/api/projects/${projectId}`, patch);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  nameEl.addEventListener('click', async () => {
    if (readOnly) return;
    const name = await prompt({ title: t('dash.rename'), value: project.name, okLabel: t('common.save'), cancelLabel: t('common.cancel'), select: true });
    if (name) patchProject({ name });
  });

  // ---------- compile ----------
  function setCompiling(on) {
    compiling = on;
    compileBtn.disabled = on;
    compileBtn.classList.toggle('busy', on);
    compileBtn.querySelector('span:last-child').textContent = on ? t('editor.compiling') : t('editor.recompile');
  }

  async function compile() {
    clearTimeout(autoTimer);
    if (compiling) {
      compileQueued = true;
      return;
    }
    setCompiling(true);
    try {
      const stateVector = btoa(String.fromCharCode(...Y.encodeStateVector(ydoc)));
      const r = await api.post(`/api/projects/${projectId}/compile`, { stateVector, stopOnFirstError: !!prefs.get('stopOnFirstError') });
      lastResult = r;
      logs.setResult(r);
      updateLogBadge();
      aiPanel?.setErrorCount(logs.counts().error);
      if (r.hasPdf && r.buildId) {
        const url = `/api/projects/${projectId}/builds/${r.buildId}/output.pdf`;
        downloadPdf.href = `${url}?download=1`;
        downloadPdf.removeAttribute('aria-disabled');
        await pdf.load(url);
        if (!logsHost.hidden && !logs.counts().error) showLogs(false);
      } else {
        const msg = r.status === 'unavailable' ? `${t('editor.unavailable')}: ${r.message}` : r.status === 'timedout' ? t('editor.compileTimeout') : t('editor.compileFailed');
        pdf.clear(msg);
        if (r.status !== 'unavailable' || r.issues?.length) showLogs(true);
      }
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setCompiling(false);
      if (compileQueued) {
        compileQueued = false;
        compile();
      }
    }
  }

  function updateLogBadge() {
    const c = logs.counts();
    const el = logsBtn.querySelector('.log-counts');
    el.replaceChildren(
      ...[
        c.error ? h('span.count.count-error', c.error) : null,
        c.warning ? h('span.count.count-warning', c.warning) : null,
        c.typesetting ? h('span.count.count-info', c.typesetting) : null,
      ].filter(Boolean),
    );
  }

  function showLogs(on) {
    logsHost.hidden = !on;
    logs.show(on);
    logsBtn.classList.toggle('active', on);
  }
  logs.setResult(null);
  showLogs(false);
  logsBtn.addEventListener('click', () => showLogs(logsHost.hidden));
  compileBtn.addEventListener('click', () => compile());
  compileMore.addEventListener('click', () =>
    menu(compileMore, [
      { label: t('editor.autoCompile'), checked: prefs.get('autoCompile'), onclick: () => prefs.set('autoCompile', !prefs.get('autoCompile')) },
      { label: t('editor.stopOnError'), checked: !!prefs.get('stopOnFirstError'), onclick: () => prefs.set('stopOnFirstError', !prefs.get('stopOnFirstError')) },
      '-',
      ...Object.entries(COMPILERS).map(([id, label]) => ({
        label: `${t('editor.compiler')}: ${label}`,
        checked: project.compiler === id,
        disabled: readOnly,
        onclick: () => patchProject({ compiler: id }).then(() => compile()),
      })),
      '-',
      {
        label: t('editor.clearCache'),
        icon: 'trash',
        disabled: readOnly,
        onclick: () => api.post(`/api/projects/${projectId}/clear-cache`).then(() => compile()),
      },
    ]),
  );

  // ---------- SyncTeX ----------
  async function forwardSearch() {
    if (!editor || !lastResult?.buildId || !currentId) return;
    const line = editor.getCursorLine();
    try {
      const r = await api.get(
        `/api/projects/${projectId}/synctex/code?buildId=${lastResult.buildId}&file=${encodeURIComponent(paths.get(currentId))}&line=${line}`,
      );
      if (r.boxes.length) pdf.highlight(r.boxes);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function inverseSearch(pageNo, x, y) {
    if (!lastResult?.buildId) return;
    try {
      const r = await api.get(`/api/projects/${projectId}/synctex/pdf?buildId=${lastResult.buildId}&page=${pageNo}&x=${x}&y=${y}`);
      if (r.hit) jumpTo(r.hit.file, r.hit.line);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ---------- AI assistant ----------
  let aiPanel = null;
  function toggleAi(open = aiHost.hidden) {
    if (open && !project.ai) return;
    aiHost.hidden = !open;
    aiBtn.classList.toggle('active', open);
    prefs.set('aiOpen', open);
    if (!open) return;
    if (!aiPanel) {
      aiPanel = new AiPanel(aiHost, {
        projectId,
        getEditor: () => editor,
        getFileId: () => currentId,
        getStateVector: () => btoa(String.fromCharCode(...Y.encodeStateVector(ydoc))),
        onDisabled: () => setAiEnabled(false, true),
        onClose: () => toggleAi(false),
      });
      aiPanel.setErrorCount(logs.counts().error);
      aiPanel.loadProviders();
    }
    aiPanel.focus();
  }
  function setAiEnabled(enabled, notify = false) {
    // The event goes to every open editor of the project; read-only members never get the assistant.
    if (enabled && readOnly) return;
    if (project.ai === enabled) return;
    project.ai = enabled;
    aiBtn.hidden = !enabled;
    if (enabled) {
      aiPanel?.loadProviders();
      if (notify) toast(t('ai.enabled'));
      return;
    }
    aiPanel?.destroy();
    aiPanel = null;
    aiHost.hidden = true;
    aiBtn.classList.remove('active');
    if (notify) toast(t('ai.disabled'), 'error');
  }
  if (project.ai && prefs.get('aiOpen')) toggleAi(true);

  // ---------- dialogs ----------
  shareBtn.addEventListener('click', () => openShare());
  function openShare() {
    if (project.sharingLocked) {
      modal({ title: t('share.title'), body: h('p', t('share.locked')) });
      return;
    }
    if (project.role !== 'owner') {
      modal({ title: t('share.title'), body: h('p', t('share.ownerOnly')) });
      return;
    }
    const body = h('div.stack');
    const draw = () => {
      const link = (token) => `${location.origin}/join/${token}`;
      const linkRow = (label, token) => {
        const input = h('input.input.mono', { value: link(token), readOnly: true, onfocus: (e) => e.target.select() });
        return h(
          'div.field',
          h('span.field-label', label),
          h('div.input-row', input, h('button.btn', { onclick: () => navigator.clipboard.writeText(input.value).then(() => toast(t('common.copied'))) }, icon('copy'), t('common.copy'))),
        );
      };
      body.replaceChildren(
        linkRow(t('share.editLink'), project.editToken),
        linkRow(t('share.viewLink'), project.viewToken),
        h('h3.subhead', t('share.members')),
        project.members?.length
          ? h(
              'div.members',
              project.members.map((m) => {
                const roleSel = h(
                  'select.input.small',
                  { onchange: () => api.patch(`/api/projects/${projectId}/members/${m.id}`, { role: roleSel.value }).then(reloadInfo) },
                  ['editor', 'viewer'].map((r) => h('option', { value: r, selected: m.role === r }, t(`role.${r}`))),
                );
                return h(
                  'div.member',
                  avatar(m),
                  h('div.member-name', h('div', m.name), h('div.muted.small', m.email)),
                  roleSel,
                  h('button.btn.small.danger', { onclick: () => api.del(`/api/projects/${projectId}/members/${m.id}`).then(reloadInfo) }, t('share.remove')),
                );
              }),
            )
          : h('p.muted', t('share.noMembers')),
        h(
          'div.row-end',
          h(
            'button.btn.small',
            {
              onclick: async () => {
                if (!(await confirmDialog({ title: t('share.regenerate'), message: t('share.regenerateConfirm'), cancelLabel: t('common.cancel') }))) return;
                const r = await api.post(`/api/projects/${projectId}/tokens`);
                project = { ...project, ...r.project };
                draw();
              },
            },
            icon('refresh'),
            t('share.regenerate'),
          ),
        ),
      );
    };
    const reloadInfo = async () => {
      const r = await api.get(`/api/projects/${projectId}`);
      project = { ...project, ...r.project };
      draw();
    };
    draw();
    modal({ title: t('share.title'), body, wide: true });
  }

  settingsBtn.addEventListener('click', () => {
    const docs = entities.filter((e) => e.kind === 'doc' && /\.tex$/i.test(e.name));
    const sel = (options, value, onchange, disabled = false) =>
      h('select.input', { disabled, onchange: (e) => onchange(e.target.value) }, options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value) }, l)));
    const row = (label, control) => h('label.field.field-inline', h('span.field-label', label), control);
    modal({
      title: t('editor.settings'),
      body: h(
        'div.stack',
        row(t('editor.compiler'), sel(Object.entries(COMPILERS).filter(([v]) => !(session.integration && v === 'lualatex' && project.compiler !== v)), project.compiler, (v) => patchProject({ compiler: v }), readOnly)),
        row(t('editor.mainFile'), sel(docs.map((d) => [d.id, paths.get(d.id)]), project.mainFileId, (v) => patchProject({ mainFileId: v }), readOnly)),
        h('hr'),
        row(t('editor.fontSize'), sel([11, 12, 13, 14, 15, 16, 18, 20, 24].map((n) => [n, `${n}px`]), prefs.get('fontSize'), (v) => {
          prefs.set('fontSize', Number(v));
          editor?.setFontSize(Number(v));
        })),
        row(t('editor.theme'), sel([['system', t('editor.themeSystem')], ['light', t('editor.themeLight')], ['dark', t('editor.themeDark')]], prefs.get('theme'), (v) => prefs.set('theme', v))),
        row(t('editor.language'), sel(Object.entries(LANGUAGES), currentLang(), (v) => {
          prefs.set('lang', v);
          location.reload();
        })),
        row(t('editor.autoCompile'), h('input', { type: 'checkbox', checked: prefs.get('autoCompile'), onchange: (e) => prefs.set('autoCompile', e.target.checked) })),
      ),
    });
  });

  // ---------- layout: splitters & modes ----------
  function applyLayout() {
    const mode = prefs.get('layout');
    workspace.dataset.layout = mode;
    workspace.style.setProperty('--sidebar-w', `${prefs.get('sidebarWidth')}px`);
    workspace.style.setProperty('--split', String(prefs.get('split')));
  }
  applyLayout();
  layoutBtn.addEventListener('click', () =>
    menu(layoutBtn, [
      { label: t('editor.layoutBoth'), checked: prefs.get('layout') === 'both', onclick: () => (prefs.set('layout', 'both'), applyLayout()) },
      { label: t('editor.layoutEditor'), checked: prefs.get('layout') === 'editor', onclick: () => (prefs.set('layout', 'editor'), applyLayout()) },
      { label: t('editor.layoutPdf'), checked: prefs.get('layout') === 'pdf', onclick: () => (prefs.set('layout', 'pdf'), applyLayout()) },
    ], { align: 'right' }),
  );

  function dragSplitter(el, onMove) {
    el.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      page.classList.add('resizing');
      const move = (ev) => onMove(ev);
      const up = () => {
        page.classList.remove('resizing');
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', up);
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', up);
    });
  }
  dragSplitter(split1, (e) => {
    const x = e.clientX - workspace.getBoundingClientRect().left;
    prefs.set('sidebarWidth', Math.round(Math.min(480, Math.max(160, x))));
    applyLayout();
  });
  dragSplitter(split2, (e) => {
    const rect = editorPane.getBoundingClientRect();
    const total = rect.width + pdfPane.getBoundingClientRect().width;
    const ratio = (e.clientX - rect.left) / total;
    prefs.set('split', Math.min(0.85, Math.max(0.15, ratio)));
    applyLayout();
  });

  // Global shortcuts (when focus is outside the editor).
  const onKey = (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === 's' || e.key === 'Enter')) {
      e.preventDefault();
      compile();
    }
  };
  document.addEventListener('keydown', onKey);
  disposers.push(() => document.removeEventListener('keydown', onKey));

  // Drag files from the OS anywhere on the sidebar to upload into the project root.
  sidebar.addEventListener('dragover', (e) => {
    if (!readOnly && e.dataTransfer.types.includes('Files')) e.preventDefault();
  });
  sidebar.addEventListener('drop', (e) => {
    if (readOnly || !e.dataTransfer.files.length) return;
    e.preventDefault();
    upload(null, [...e.dataTransfer.files]);
  });

  const unsubPrefs = prefs.subscribe((k) => k === 'theme' && editor?.view?.requestMeasure());
  disposers.push(unsubPrefs);
  document.title = `${project.name} — webTEX`;

  return () => {
    clearTimeout(autoTimer);
    for (const d of disposers) d();
    ydoc.off('update', onLocalUpdate);
    editor?.destroy();
    pdf.destroy();
    aiPanel?.destroy();
    conn.destroy();
    for (const um of undoManagers.values()) um.destroy();
    ydoc.destroy();
    document.title = 'webTEX';
  };
}

function waitFor(cond, timeout = 5000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      if (cond()) return resolve();
      if (Date.now() - start > timeout) return reject(new Error('timeout'));
      setTimeout(tick, 50);
    };
    tick();
  });
}
