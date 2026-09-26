from django.apps import AppConfig


class SitesConfig(AppConfig):
    """Wiele witryn djcms – po jednej na konkurs platformy (docs/tasks/DJ-02.md § 2.1, § 5, D4).

    Etykieta ``dj_sites`` (a nie ``sites``): ``django.contrib.sites`` ma już etykietę ``sites``,
    a prefiks ``dj_`` jest konwencją wszystkich aplikacji djcms.

    ``ready`` instaluje łatkę ``SiteManager.get_current`` (``apps.sites.patches``) – musi stać
    przed pierwszym żądaniem i przed pierwszym renderowaniem, więc właśnie tutaj, a nie w module
    importowanym leniwie.
    """

    name = "apps.sites"
    label = "dj_sites"
    verbose_name = "Witryny konkursów (djcms)"

    def ready(self) -> None:
        from . import patches

        patches.install()
