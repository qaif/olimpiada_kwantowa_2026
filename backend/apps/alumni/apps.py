from django.apps import AppConfig


class AlumniConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.alumni"
    label = "alumni"
    verbose_name = "absolwenci"

    def ready(self):
        # Polityka rozmów mentorskich w Wiadomościach (ALUM-01 § 5.3): czat pyta o nią przy każdej
        # wiadomości P2P, a sam o mentoringu nie wie nic – zależność idzie wyłącznie stąd do czatu.
        from apps.chat.services import register_peer_policy

        from .mentoring import chat_policy

        register_peer_policy(chat_policy)
        # Medale (MED-01) jako źródło osiągnięć – rejestr źródeł czekał na ten moduł od ALUM-01.
        from .achievements import medal_source, register_source

        register_source(medal_source)
        # Zmiana daty urodzenia osoby w otwartej relacji – audyt i zgłoszenie (przegląd, M2).
        from . import signals  # noqa: F401
