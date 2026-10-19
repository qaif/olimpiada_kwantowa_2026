import { api, session } from '../api.js';
import { confirmDialog, h, icon, menu, modal, prompt, timeAgo, toast } from '../dom.js';
import { t } from '../i18n.js';
import { navigate } from '../main.js';
import { brand, userMenuButton } from './common.js';

export function renderDashboard(root) {
  // Platform (SSO) users get their projects only from the olympiad platform.
  const external = !!session.user?.external;
  let projects = [];
  let templates = [];
  let filter = '';

  const list = h('div.project-list');
  const search = h('input.input.search', {
    type: 'search',
    placeholder: t('dash.search'),
    'aria-label': t('dash.search'),
    oninput: () => {
      filter = search.value.trim().toLowerCase();
      draw();
    },
  });
  const fileInput = h('input', { type: 'file', accept: '.zip,application/zip', hidden: true, onchange: () => importZip() });

  async function load() {
    const r = await api.get('/api/projects');
    projects = r.projects;
    templates = r.templates;
    draw();
  }

  function draw() {
    const shown = projects.filter((p) => !filter || p.name.toLowerCase().includes(filter));
    if (!shown.length) {
      list.replaceChildren(
        h(
          'div.empty-state',
          icon('fileText', 40),
          h('p', projects.length ? t('dash.noMatch') : external ? t('dash.emptyExternal') : t('dash.empty')),
          !projects.length && external && session.platformUrl
            ? h('a.btn.primary', { href: session.platformUrl }, t('integration.toPlatform'))
            : null,
        ),
      );
      return;
    }
    list.replaceChildren(
      h(
        'table.projects',
        h('thead', h('tr', h('th', t('dash.colName')), h('th', t('dash.colOwner')), h('th', t('dash.colModified')), h('th'))),
        h(
          'tbody',
          shown.map((p) => {
            const more = h('button.icon-btn', { 'aria-label': '…', title: '…' }, '⋯');
            more.addEventListener('click', () => projectMenu(more, p));
            return h(
              'tr',
              h(
                'td.name',
                h('a', { href: `/project/${p.id}`, 'data-link': '' }, p.name),
                p.role !== 'owner' ? h('span.badge', t(`role.${p.role}`)) : null,
              ),
              h('td.muted', p.role === 'owner' ? t('dash.you') : p.ownerName),
              h('td.muted', timeAgo(p.updatedAt, t)),
              h('td.actions', more),
            );
          }),
        ),
      ),
    );
  }

  function projectMenu(anchor, p) {
    const isOwner = p.role === 'owner';
    const canDelete = !(external && isOwner); // platform users may still leave others' projects
    menu(
      anchor,
      [
        {
          label: t('dash.rename'),
          icon: 'edit',
          disabled: p.role === 'viewer',
          onclick: async () => {
            const name = await prompt({ title: t('dash.rename'), value: p.name, okLabel: t('common.save'), cancelLabel: t('common.cancel'), select: true });
            if (!name) return;
            await api.patch(`/api/projects/${p.id}`, { name }).catch((e) => toast(e.message, 'error'));
            load();
          },
        },
        external
          ? null
          : {
              label: t('dash.copy'),
              icon: 'copy',
              onclick: async () => {
                const { id } = await api.post(`/api/projects/${p.id}/copy`);
                navigate(`/project/${id}`);
              },
            },
        { label: t('dash.download'), icon: 'download', onclick: () => (location.href = `/api/projects/${p.id}/download`) },
        canDelete ? '-' : null,
        canDelete
          ? {
              label: isOwner ? t('dash.delete') : t('dash.leave'),
              icon: 'trash',
              danger: true,
              onclick: async () => {
                const ok = await confirmDialog({
                  title: isOwner ? t('dash.delete') : t('dash.leave'),
                  message: t(isOwner ? 'dash.deleteConfirm' : 'dash.leaveConfirm', { name: p.name }),
                  okLabel: isOwner ? t('common.delete') : t('dash.leave'),
                  cancelLabel: t('common.cancel'),
                  danger: true,
                });
                if (!ok) return;
                await api.del(`/api/projects/${p.id}`);
                load();
              },
            }
          : null,
      ].filter(Boolean),
      { align: 'right' },
    );
  }

  function newProjectDialog(templateId) {
    const name = h('input.input', { value: '', placeholder: t('dash.projectName'), required: true });
    const select = h(
      'select.input',
      templates.map((tpl) => h('option', { value: tpl.id, selected: tpl.id === templateId }, tpl.label)),
    );
    const create = async () => {
      const value = name.value.trim();
      if (!value) return name.focus();
      dlg.close();
      try {
        const { id } = await api.post('/api/projects', { name: value, template: select.value });
        navigate(`/project/${id}`);
      } catch (err) {
        toast(err.message, 'error');
      }
    };
    const dlg = modal({
      title: t('dash.new'),
      body: h(
        'form.stack',
        { onsubmit: (e) => (e.preventDefault(), create()) },
        h('label.field', h('span.field-label', t('dash.projectName')), name),
        h('label.field', h('span.field-label', t('dash.template')), select),
      ),
      actions: [
        h('button.btn', { onclick: () => dlg.close() }, t('common.cancel')),
        h('button.btn.primary', { onclick: create }, t('dash.create')),
      ],
    });
    setTimeout(() => name.focus(), 0);
  }

  async function importZip() {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    form.append('name', file.name.replace(/\.zip$/i, ''));
    try {
      const { id } = await api.upload('/api/projects/import', form);
      navigate(`/project/${id}`);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  const newBtn = h('button.btn.primary', icon('plus'), t('dash.new'));
  newBtn.addEventListener('click', () =>
    menu(newBtn, [
      ...templates.map((tpl) => ({ label: tpl.label, icon: 'fileText', onclick: () => newProjectDialog(tpl.id) })),
      '-',
      { label: t('dash.upload'), icon: 'upload', onclick: () => fileInput.click() },
    ]),
  );

  root.replaceChildren(
    h(
      'div.dashboard',
      h('header.topbar', brand(), h('div.spacer'), userMenuButton()),
      h(
        'main.dash-main',
        h('div.dash-head', h('h1', t('dash.title')), h('div.spacer'), search, external ? null : [newBtn, fileInput]),
        list,
      ),
    ),
  );
  list.append(h('div.empty-state', t('common.loading')));
  load().catch((err) => toast(err.message, 'error'));
}
