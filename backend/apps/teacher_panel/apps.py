from django.apps import AppConfig


class TeacherPanelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teacher_panel"
    label = "teacher_panel"
    verbose_name = "Panel nauczyciela (menu, Start, postęp klasy, eksport)"
