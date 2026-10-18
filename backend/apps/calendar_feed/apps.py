from django.apps import AppConfig


class CalendarFeedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.calendar_feed"
    label = "calendar_feed"
    verbose_name = "publiczny kalendarz ICS"
