from django.apps import AppConfig


class SeoConfig(AppConfig):
    """Przekierowania starych adresów witryn konkursów (DJ-02 § 8, odpowiednik ``wagtail.contrib.redirects``).

    Etykieta ``dj_seo`` – importer (``apps.importer.services.redirect_model``) szuka modelu
    ``dj_seo.Redirect`` po niej. ``ready`` podpina automatyczne przekierowania przy zmianie adresu
    opublikowanej strony (``apps.seo.auto``) i regułę celów przekierowania w formularzu strony
    django CMS (``apps.seo.targets``).
    """

    name = "apps.seo"
    label = "dj_seo"
    verbose_name = "Przekierowania (djcms)"

    def ready(self) -> None:
        from . import auto, targets

        auto.install()
        targets.install_page_redirect_validation()
