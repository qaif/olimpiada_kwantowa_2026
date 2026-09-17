"""Konkursy w panelu operatora platformy (``/admin/``).

Dlaczego ``/admin/``, a nie ``/cms/``: adresowanie konkursu (witryna, identyfikator, tryb, prefiks
ścieżki) wymaga **zgodnej zmiany poza aplikacją** – domeny w Caddy, ``DJANGO_ALLOWED_HOSTS``
i ``DJANGO_CSRF_TRUSTED_ORIGINS``. To jest praca administratora serwera, a nie koordynatora
konkursu (``docs/UNIWERSALNY-ETAP-1.md`` § 8, D7). Koordynator dostanie w panelu ekran „Ustawienia
konkursu” z marką, danymi organizatora i kontaktem – bez tych czterech pól (T5).

Stąd podział praw na tym ekranie: staff widzi i edytuje markę, superużytkownik – także adresowanie.
"""

from django.contrib import admin

from .models import Competition

#: Pola, które przestawiają **adres** konkursu. Zmiana którejkolwiek z nich bez zgodnej zmiany
#: w konfiguracji serwera kończy się stroną nie do otwarcia albo błędem CSRF przy każdym POST-cie,
#: więc należą do operatora platformy.
OPERATOR_FIELDS = ("site", "slug", "routing_mode", "path_prefix")


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "primary_domain", "routing_mode", "is_active", "created_at")
    list_filter = ("is_active", "routing_mode")
    search_fields = ("name", "short_name", "slug", "primary_domain", "organizer_name")
    ordering = ("name", "id")
    date_hierarchy = "created_at"

    def get_readonly_fields(self, request, obj=None):
        """Adresowanie tylko dla superużytkownika; przy zakładaniu konkursu – dla nikogo.

        Rozróżnienie „obiekt istnieje” jest istotne: pola tylko do odczytu na formularzu
        **dodawania** oznaczałyby konkurs bez witryny, czyli wiersz, którego nie da się zapisać.
        """
        if obj is None or request.user.is_superuser:
            return ()
        return OPERATOR_FIELDS

    def has_delete_permission(self, request, obj=None) -> bool:
        """Konkurs kasuje wyłącznie operator platformy.

        Za konkursem stoją edycje, uczestnicy, prace i wyniki (``PROTECT``), więc kasowanie i tak
        nie przejdzie, dopóki cokolwiek z tego istnieje – ale przycisk, który zawsze kończy się
        błędem bazy, jest gorszy niż jego brak.
        """
        return request.user.is_superuser
