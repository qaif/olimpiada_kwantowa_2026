"""Filer bez plików „prywatnych” (reguła 12, ``apps/blocks/files.py``) i konfiguracja slotów (tabela 6.1)."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.template.loader import get_template

from apps.blocks import checks
from apps.blocks.plugin_sets import ART_PLUGINS, DOC_PLUGINS
from apps.blocks.tests import factories as f


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.mark.django_db
def test_new_private_file_is_saved_as_public(media_root):
    from filer.models import File

    item = f.filer_file()
    item.refresh_from_db()
    assert item.is_public
    private = File(original_filename="tajne.pdf", is_public=False)
    private.file = f.filer_file("tajne.pdf").file
    private.save()
    private.refresh_from_db()
    assert private.is_public


@pytest.mark.django_db
def test_flipping_existing_file_to_private_keeps_it_public_and_readable(media_root):
    item = f.filer_file("zgoda.pdf", b"tresc-zgody")
    item.is_public = False
    item.save()
    item.refresh_from_db()
    assert item.is_public
    assert item.file.storage.exists(item.file.name)  # plik wrócił do magazynu publicznego
    with item.file.open("rb") as handle:
        assert handle.read() == b"tresc-zgody"


@pytest.mark.django_db
def test_admin_file_form_has_no_private_flag(client, superuser, media_root):
    item = f.filer_file()
    client.force_login(superuser)
    response = client.get(f"/djcms/admin/filer/file/{item.pk}/change/")
    assert response.status_code == 200
    assert 'name="is_public"' not in response.content.decode()


def test_check_requires_public_only_filer(settings):
    assert checks.check_filer_is_public_only() == []
    settings.FILER_IS_PUBLIC_DEFAULT = False
    assert [error.id for error in checks.check_filer_is_public_only()] == ["dj_blocks.E001"]


def test_check_flags_a_private_toggle_left_in_the_admin():
    from django.contrib.admin import AdminSite
    from filer.admin.fileadmin import FileAdmin
    from filer.admin.folderadmin import FolderAdmin
    from filer.models import File, Folder

    from apps.blocks.files import hide_private_toggle, private_toggle_visible

    site = AdminSite(name="probe")
    site.register(File, FileAdmin)
    site.register(Folder, FolderAdmin)
    assert private_toggle_visible(site) != []  # filer przy włączonych uprawnieniach pokazuje pole i akcje
    hide_private_toggle(site)
    assert private_toggle_visible(site) == []


def test_check_requires_folder_permissions(settings):
    assert checks.check_filer_folder_permissions() == []
    settings.FILER_ENABLE_PERMISSIONS = False
    assert [error.id for error in checks.check_filer_folder_permissions()] == ["dj_blocks.E002"]


# --- szablony i sloty (tabela 6.1) ----------------------------------------------------------------


def _declared_slots(template: str) -> list[str]:
    from cms.utils.placeholder import get_placeholders

    return [placeholder.slot for placeholder in get_placeholders(template)]


@pytest.mark.parametrize(("template", "_label"), settings.CMS_TEMPLATES)
def test_every_page_template_exists_and_every_slot_is_configured(template, _label):
    get_template(template)
    for slot in _declared_slots(template):
        conf = settings.CMS_PLACEHOLDER_CONF.get(f"{template} {slot}")
        assert conf and conf["plugins"], f"{template}: slot {slot} bez CMS_PLACEHOLDER_CONF"


def test_every_configured_plugin_is_registered():
    """Nazwy w ``CMS_PLACEHOLDER_CONF`` to klasy zarejestrowane (także wtyczki żywe z ``apps.live``)."""
    from cms.plugin_pool import plugin_pool

    registered = {plugin.__name__ for plugin in plugin_pool.get_all_plugins()}
    configured = {name for conf in settings.CMS_PLACEHOLDER_CONF.values() for name in conf["plugins"]}
    assert configured - registered == set()


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("dj/pages/news.html body", ART_PLUGINS),
        ("dj/pages/problems.html body", ART_PLUGINS),
        # Strona „Warsztaty” (DJ-02 D9): tabele na żywo z Wagtaila obok zestawu DOC.
        ("dj/pages/content.html body", [*DOC_PLUGINS, "WorkshopSchedulePlugin"]),
        ("dj/live/partners_page.html partners", ["PartnersLivePlugin"]),
        ("dj/pages/document.html body", DOC_PLUGINS),
        ("dj/pages/partners.html partners", ["PartnerPlugin"]),
        ("dj/pages/faq.html faq", ["FAQEntryPlugin"]),
        ("dj/pages/archive_edition.html documents", ["ArchiveDocumentPlugin"]),
    ],
)
def test_slot_plugin_sets(key, expected):
    assert settings.CMS_PLACEHOLDER_CONF[key]["plugins"] == expected


def test_doc_set_includes_live_stage_timeline_and_about_section_accepts_doc():
    from cms.plugin_pool import plugin_pool

    assert "StageTimelinePlugin" in DOC_PLUGINS
    assert plugin_pool.get_plugin("AboutSectionPlugin").child_classes == DOC_PLUGINS
