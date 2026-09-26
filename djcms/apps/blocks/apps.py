from django.apps import AppConfig


class BlocksConfig(AppConfig):
    """Wtyczki redakcyjne djcms – odpowiedniki bloków StreamField Wagtaila (§ 6.2 docs/tasks/DJ-01.md).

    Etykieta ``dj_blocks`` z tego samego powodu co ``dj_pages``: krótkie nazwy aplikacji ekosystemu
    django CMS (``blocks`` to częsta nazwa) nie mogą zderzyć się z naszymi.
    """

    name = "apps.blocks"
    label = "dj_blocks"
    verbose_name = "Wtyczki treści (djcms)"

    def ready(self) -> None:
        from . import checks, files  # noqa: F401 - system checki i pilnowanie publicznych plików filera
