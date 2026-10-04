from django.apps import AppConfig


class AccessibilityConfig(AppConfig):
    """Dostępność (A11Y-01): deklaracja dostępności i napisy stopki. Bez modeli – deklaracja jest
    stroną CMS (``cms.DocumentPage``), a aplikacja niesie komendę, która ją zakłada, i katalog
    tłumaczeń odnośnika w stopce obu motywów."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accessibility"
    label = "accessibility"
    verbose_name = "dostępność"
