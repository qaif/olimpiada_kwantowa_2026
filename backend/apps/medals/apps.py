from django.apps import AppConfig


class MedalsConfig(AppConfig):
    """Medale olimpiady międzynarodowej, dyplomy w języku ucznia i ranking krajów (MED-01)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.medals"
    verbose_name = "Medale i ranking krajów"

    def ready(self) -> None:
        # Skład dokumentów medalowych i zaświadczeń w języku ucznia wpina się w ``render_pdf``
        # przez rejestr rodzajów w ``apps.results.certificates`` – zależność idzie od medali do
        # wyników, a nie odwrotnie (wyniki nie importują tej aplikacji).
        from apps.results.certificates import register_composer

        from .documents import compose_certificate, handles

        register_composer(handles, compose_certificate)
