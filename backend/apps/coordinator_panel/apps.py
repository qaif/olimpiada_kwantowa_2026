from django.apps import AppConfig


class CoordinatorPanelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.coordinator_panel"
    label = "coordinator_panel"
    verbose_name = "Panel koordynatora (Start, przypięte pozycje, okruszki, paleta poleceń)"
