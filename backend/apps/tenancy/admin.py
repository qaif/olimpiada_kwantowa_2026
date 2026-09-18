"""Konkursy w panelu operatora platformy (``/admin/``).

Dlaczego ``/admin/``, a nie ``/cms/``: adresowanie konkursu (witryna, identyfikator, tryb, prefiks
ścieżki) wymaga **zgodnej zmiany poza aplikacją** – domeny w Caddy, ``DJANGO_ALLOWED_HOSTS``
i ``DJANGO_CSRF_TRUSTED_ORIGINS``. To jest praca administratora serwera, a nie koordynatora
konkursu (``docs/UNIWERSALNY-ETAP-1.md`` § 8, D7). Koordynator dostanie w panelu ekran „Ustawienia
konkursu” z marką, danymi organizatora i kontaktem – bez tych czterech pól (T5).

Stąd podział praw na tym ekranie: staff widzi i edytuje markę, superużytkownik – także adresowanie.
"""

from django.contrib import admin

from .aliases import CompetitionSiteAlias
from .documents import DocumentTemplate
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


@admin.register(CompetitionSiteAlias)
class CompetitionSiteAliasAdmin(admin.ModelAdmin):
    """Druga domena konkursu w drugim języku treści – ekran wyłącznie dla operatora platformy.

    Ta sama reguła, co przy adresowaniu konkursu (patrz docstring modułu): alias działa dopiero
    razem z blokiem serwerowym w Caddym, wpisem w ``DJANGO_ALLOWED_HOSTS`` i w
    ``DJANGO_CSRF_TRUSTED_ORIGINS``. Koordynator konkursu nie ma jak tych trzech miejsc zmienić,
    więc nie ma tu czego robić – a wiersz założony bez nich znaczyłby domenę, która odpowiada 400.
    """

    list_display = ("site", "competition", "locale", "created_at")
    list_filter = ("competition", "locale")
    search_fields = ("site__hostname", "competition__name", "competition__slug")
    ordering = ("competition", "id")
    autocomplete_fields = ("competition",)

    def has_module_permission(self, request) -> bool:
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser

    def has_add_permission(self, request) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser


@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    """Teksty dokumentów konkursu – **podgląd i awaryjna edycja**, a nie ekran pracy koordynatora.

    Ekranem pracy jest panel konkursu (T13, § 2.2): tam stoi lista znaczników, podgląd dokumentu
    i wymuszone podniesienie wersji (``apps.tenancy.documents.set_current_template``). Tutaj widać
    to samo bez tamtej obudowy, bo operator platformy musi mieć wgląd w treść dokumentów także
    wtedy, gdy panel konkursu jest jeszcze niepodłączony albo gdy trzeba obejrzeć wersję
    historyczną.

    Kasowanie wyłącznie dla superużytkownika i z tego samego powodu, co przy konkursie: wiersz
    historyczny jest jedynym śladem, z czego powstał dokument, który ktoś trzyma w ręku.
    """

    list_display = ("competition", "kind", "version", "is_current", "created_at")
    list_filter = ("competition", "kind", "is_current")
    search_fields = ("version", "title", "statement", "competition__name", "competition__slug")
    ordering = ("competition", "kind", "-created_at")
    date_hierarchy = "created_at"
    autocomplete_fields = ("competition",)
    readonly_fields = ("created_at",)

    def has_delete_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser
