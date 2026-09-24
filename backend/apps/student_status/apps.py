from django.apps import AppConfig


class StudentStatusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.student_status"
    label = "student_status"
    verbose_name = "status ucznia"
