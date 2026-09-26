from django.apps import AppConfig


class LiveConfig(AppConfig):
    """Dane na żywo z aplikacji głównej: klient API, rama serwisu (``chrome``), wtyczki żywe (DJ-01f).

    Etykieta ``dj_live`` – prefiks ``dj_`` z tego samego powodu, co w ``apps.pages``.
    """

    name = "apps.live"
    label = "dj_live"
    verbose_name = "Dane na żywo (djcms)"
