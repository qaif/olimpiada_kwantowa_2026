from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    verbose_name = "Konta i role"

    def ready(self) -> None:
        """Rejestruje zadania Celery, które nie leżą w ``tasks.py``, i sygnały modułów peryferyjnych.

        ``app.autodiscover_tasks()`` zagląda wyłącznie do modułów o nazwie ``tasks`` w każdej
        zainstalowanej aplikacji. Zadanie wysyłki komunikatów mieszka razem z resztą swojej
        logiki (``apps.accounts.messaging``), bo rozdzielenie „kto jest odbiorcą” od „jak to
        wysłać” na dwa pliki znaczyłoby czytanie jednej funkcji w dwóch miejscach. Cena jest
        ta: moduł trzeba zaimportować ręcznie, inaczej worker nie zna nazwy zadania i odrzuca je
        jako ``NotRegistered`` – a wygląda to wtedy jak „komunikat wyszedł, tylko nie doszedł”.

        Import ``supervisors`` ma ten sam powód, co import ``analytics`` w ``apps.cms.apps``:
        podpina od razu (a nie dopiero przy pierwszym użyciu modułu w danym procesie) odbiornik,
        który czyści pamięć podręczną przełącznika ``supervisor_registration_enabled`` po zapisie
        ``SiteSettings`` w ``/cms/``. Bez wczesnego importu organizator widziałby własną zmianę
        z opóźnieniem do ``_REGISTRATION_CACHE_TTL_SECONDS`` w procesach, które modułu jeszcze
        nie dotknęły, zamiast od razu.
        """
        from django.db.models.signals import post_delete, post_save

        from . import messaging, request_memo, supervisors  # noqa: F401  (import dla efektu ubocznego)
        from .models import Participant

        # Pamięć profilu uczestnika na czas żądania (``apps.accounts.request_memo``) gubi wszystko
        # przy zapisie albo skasowaniu profilu – inaczej żądanie, które zmienia profil, czytałoby
        # dalej stan sprzed zmiany.
        post_save.connect(
            request_memo.forget_all, sender=Participant, dispatch_uid="accounts.participant_memo.save"
        )
        post_delete.connect(
            request_memo.forget_all, sender=Participant, dispatch_uid="accounts.participant_memo.delete"
        )
