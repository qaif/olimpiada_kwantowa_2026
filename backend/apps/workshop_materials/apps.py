from django.apps import AppConfig


class WorkshopMaterialsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workshop_materials"
    label = "workshop_materials"
    verbose_name = "materiały z warsztatów"

    def ready(self):
        # Sygnały kasujące pamięć „czy konkurs ma materiały” (odnośnik w pasku konta i na pulpicie
        # uczestnika) – ten sam wzorzec, co ``apps.promo.apps.PromoConfig.ready``.
        from . import availability  # noqa: F401
