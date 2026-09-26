"""Pozycje paska narzędzi django CMS dla rozszerzeń stron djcms.

W menu **Strona**: „Ustawienia menu” (każda strona, ``MenuExtension``) i metryki typów stron
(aktualność, dokument, archiwum) – okna modalne z formularzem rozszerzenia. Wzorzec z dokumentacji
django CMS (``ExtensionToolbar``): pozycja istnieje tylko dla redaktora, który może zmieniać bieżącą
stronę, i jest aktywna w trybie edycji.
"""

from cms.extensions.toolbar import ExtensionToolbar
from cms.toolbar_pool import toolbar_pool

from .models import ArchiveMeta, DocumentMeta, MenuExtension, NewsMeta


@toolbar_pool.register
class MenuExtensionToolbar(ExtensionToolbar):
    model = MenuExtension

    def populate(self):
        page_menu = self._setup_extension_toolbar()
        if not page_menu:
            return
        _extension, url = self.get_page_extension_admin()
        if url:
            page_menu.add_modal_item(
                "Ustawienia menu (dj.)…", url=url, disabled=not self.toolbar.edit_mode_active
            )


class PageContentMetaToolbar(ExtensionToolbar):
    """Pozycja „Metryka …” w menu **Strona** – tylko na stronach typu, do którego metryka należy.

    Typ strony to szablon (``PageContent.template``, tabela 6.1): aktualność ma datę i lead, dokument
    metrykę wersji, strona archiwum edycję. Na stronie innego typu pozycja byłaby polem bez skutku.
    Aktywna wyłącznie w trybie edycji, czyli na wersji roboczej – panel i tak odmawia zapisu do
    wersji opublikowanej (``admin.DraftOnlyContentExtensionAdmin``).
    """

    #: Szablon strony, na której pozycja się pojawia, i jej etykieta – ustawiane w podklasach.
    template: str = ""
    label: str = ""

    def populate(self):
        page_menu = self._setup_extension_toolbar()
        if not page_menu or self.page_content is None:
            return
        if getattr(self.page_content, "template", "") != self.template:
            return
        _extension, url = self.get_page_content_extension_admin()
        if url:
            page_menu.add_modal_item(self.label, url=url, disabled=not self.toolbar.edit_mode_active)


@toolbar_pool.register
class NewsMetaToolbar(PageContentMetaToolbar):
    model = NewsMeta
    template = "dj/pages/news.html"
    label = "Data i lead aktualności…"


@toolbar_pool.register
class DocumentMetaToolbar(PageContentMetaToolbar):
    model = DocumentMeta
    template = "dj/pages/document.html"
    label = "Metryka dokumentu…"


@toolbar_pool.register
class ArchiveMetaToolbar(PageContentMetaToolbar):
    model = ArchiveMeta
    template = "dj/pages/archive_edition.html"
    label = "Edycja w systemie zawodów…"
