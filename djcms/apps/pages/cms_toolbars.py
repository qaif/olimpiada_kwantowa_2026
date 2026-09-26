"""Pozycje paska narzędzi django CMS dla rozszerzeń stron djcms.

„Ustawienia menu” w menu **Strona** – okno modalne z formularzem ``MenuExtension``. Wzorzec
z dokumentacji django CMS (``ExtensionToolbar``): pozycja istnieje tylko dla redaktora, który może
zmieniać bieżącą stronę, i jest aktywna w trybie edycji.
"""

from cms.extensions.toolbar import ExtensionToolbar
from cms.toolbar_pool import toolbar_pool

from .models import MenuExtension


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
