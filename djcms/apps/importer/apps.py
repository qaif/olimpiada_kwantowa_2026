from django.apps import AppConfig


class ImporterConfig(AppConfig):
    """Import treści z Wagtaila (paczka ``olimpiada-cms-bundle``, § 5.3 docs/tasks/DJ-01.md).

    Etykieta ``dj_importer`` – prefiks ``dj_`` z tego samego powodu, co w ``apps.pages``. Aplikacja
    nie ma modeli: tworzy strony, wtyczki i obrazy wyłącznie przez publiczne API django CMS i filera.
    """

    name = "apps.importer"
    label = "dj_importer"
    verbose_name = "Import treści z Wagtaila"
