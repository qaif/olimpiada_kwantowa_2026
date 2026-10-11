from django.apps import AppConfig


class TranslationReviewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.translation_review"
    label = "translation_review"
    verbose_name = "Przegląd tłumaczeń"

    def ready(self) -> None:
        # Nakładka zatwierdzonych poprawek na katalogi gettext (L10N-01 § 6). W ``ready()``, bo
        # musi stać, zanim pierwsze żądanie aktywuje język – i dokładnie raz na proces.
        from .runtime import install

        install()
