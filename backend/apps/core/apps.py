from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "Rdzeń"

    def ready(self) -> None:
        """Podpięcie licznika nieudanych zadań Celery do watchdoga alertów.

        Sygnał, a nie ``try/except`` w każdym zadaniu: ``task_failure`` dostaje **każde** zadanie
        zakończone wyjątkiem, także te dopisane później i te z bibliotek (``django_celery_beat``).
        Licznik w jednym miejscu jest tu jedyną wersją, która nie przestanie być prawdziwa przy
        pierwszym nowym zadaniu w systemie.

        Import jest lokalny, bo ``ready`` biegnie przed pełnym załadowaniem aplikacji, a łańcuch
        importów ``alerts`` sięga modeli.
        """
        from celery.signals import task_failure

        from apps.core.alerts import FAILED_TASKS_KEY, bump

        def _count_failure(**_kwargs) -> None:
            # Bez treści wyjątku i bez argumentów zadania: te bywają danymi osobowymi (adres
            # odbiorcy listu), a licznik ma być liczbą, nie kopią kolejki. Szczegóły są w logu
            # workera, który Celery zapisuje sam.
            bump(FAILED_TASKS_KEY)

        # ``weak=False``: bez tego funkcja lokalna zostałaby zebrana przez GC zaraz po ``ready``
        # i sygnał przestałby cokolwiek liczyć – po cichu, bo brak alertów wygląda jak spokój.
        task_failure.connect(_count_failure, weak=False, dispatch_uid="core.alerts.count_task_failure")
