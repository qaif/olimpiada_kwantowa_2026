from django.apps import AppConfig


class PromoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.promo"
    label = "promo"
    verbose_name = "materiały promocyjne"

    def ready(self):
        # Sygnały unieważniające pamięć „czy konkurs ma plakaty” (odnośnik w stopce) – ten sam
        # wzorzec, co ``apps.cms.apps.CmsConfig.ready`` dla komunikatów i slidera sponsorów.
        from . import availability  # noqa: F401
