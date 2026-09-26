"""Panel rozszerzeń stron. ``PageExtensionAdmin`` ukrywa model z indeksu admina – redaktor otwiera
formularz z paska narzędzi strony (``cms_toolbars.py``), a nie z listy wszystkich rekordów."""

from cms.extensions import PageExtensionAdmin
from django.contrib import admin

from .models import MenuExtension


@admin.register(MenuExtension)
class MenuExtensionAdmin(PageExtensionAdmin):
    pass
