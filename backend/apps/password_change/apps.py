from django.apps import AppConfig


class PasswordChangeConfig(AppConfig):
    """Zmiana hasła w panelu konta (AUTH-01b). Bez modeli – stan żyje w ``accounts.User`` i audycie."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.password_change"
    label = "password_change"
    verbose_name = "zmiana hasła"
