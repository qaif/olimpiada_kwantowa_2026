from django.apps import AppConfig


class CommitteePanelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.committee_panel"
    label = "committee_panel"
    verbose_name = "Rama panelu komisji (menu, Start, materiały, powiadomienia komisji)"
