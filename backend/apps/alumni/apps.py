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
