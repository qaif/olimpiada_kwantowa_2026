from django.apps import AppConfig


class PagesConfig(AppConfig):
    """Rama serwisu djcms: middleware (CSP, noindex), healthz/robots, blokada logowania, checki.

    Etykieta ``dj_pages`` (a nie ``pages``), bo nazwy aplikacji django CMS i jego ekosystemu są
    krótkie i ogólne – prefiks ``dj_`` wyklucza kolizję etykiet teraz i przy następnym pakiecie.
    """

    name = "apps.pages"
    label = "dj_pages"
    verbose_name = "Strony (djcms)"

    def ready(self) -> None:
        from django.contrib import admin

        from . import auth, checks, validation  # noqa: F401 - rejestracja sygnałów i system checków

        # Strona pod adresem aplikacji głównej odrzucona już w formularzu (S5, ``validation``).
        validation.install_form_validation()

        # Formularz logowania panelu z czytelnym komunikatem o blokadzie. Sama blokada działa
        # niżej, w backendzie uwierzytelnienia (``auth.ThrottledModelBackend``), więc obejmuje też
        # logowanie z paska narzędzi django CMS (``cms_login``) – formularz tylko ją tłumaczy.
        admin.site.login_form = auth.ThrottledAdminAuthenticationForm
