from django.apps import AppConfig


class SitesConfig(AppConfig):
    """Wiele witryn djcms – po jednej na konkurs platformy (docs/tasks/DJ-02.md § 2.1, § 5, D4).

    Etykieta ``dj_sites`` (a nie ``sites``): ``django.contrib.sites`` ma już etykietę ``sites``,
    a prefiks ``dj_`` jest konwencją wszystkich aplikacji djcms.

    Tu też uprawnienia redaktorów per konkurs i logowanie z ``/cms/`` (SSO, DJ-02g):
    ``permissions``, ``sso``, ``views``, ``EditorAccessMiddleware``.

    ``ready`` instaluje łatkę ``SiteManager.get_current`` (``apps.sites.patches``) – musi stać
    przed pierwszym żądaniem i przed pierwszym renderowaniem, więc właśnie tutaj, a nie w module
    importowanym leniwie.
    """

    name = "apps.sites"
    label = "dj_sites"
    verbose_name = "Witryny konkursów (djcms)"

    def ready(self) -> None:
        from django.contrib import admin

        from . import checks, patches  # noqa: F401 - rejestracja system checków

        patches.install()
        # Logowanie hasłem – wyłącznie konto techniczne; strona mówi redaktorom, że wchodzą z /cms/ (SSO).
        admin.site.login_template = "dj_sites/admin_login.html"
