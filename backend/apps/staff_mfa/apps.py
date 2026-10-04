from django.apps import AppConfig


class StaffMfaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.staff_mfa"
    label = "staff_mfa"
    verbose_name = "logowanie dwuskładnikowe personelu"
