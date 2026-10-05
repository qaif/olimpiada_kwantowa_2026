from django.apps import AppConfig


class MailDomainsConfig(AppConfig):
    """Domeny nadawców poczty konkursów: sprawdzenie SPF/DKIM/DMARC i ostrzeżenia (MAIL-01)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mail_domains"
    label = "mail_domains"
    verbose_name = "domeny nadawców poczty"
