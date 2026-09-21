"""Forum w panelu operatora platformy (``/admin/``) – minimum, i to jest decyzja.

Moderacja mieszka w panelu koordynatora (``/coordinator/forum/``), bo tam jest kolejka, notatka
dla autora i wpis audytowy przy każdej decyzji. Ten ekran istnieje wyłącznie po to, żeby operator
platformy mógł **zobaczyć** wiersze przy diagnozie zgłoszenia – dlatego treść wpisu jest tu tylko
do odczytu, a stan zmienia się przez panel, nie przez formularz administracyjny obchodzący audyt.
"""

from django.contrib import admin

from .models import ForumCategory, ForumPost, ForumReport, ForumSettings, ForumThread


@admin.register(ForumSettings)
class ForumSettingsAdmin(admin.ModelAdmin):
    list_display = ("competition", "mode", "is_read_only", "updated_at")
    list_filter = ("mode", "is_read_only")


@admin.register(ForumCategory)
class ForumCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "competition", "slug", "ordering", "is_open")
    list_filter = ("competition", "is_open")
    search_fields = ("name", "slug")


@admin.register(ForumThread)
class ForumThreadAdmin(admin.ModelAdmin):
    list_display = ("title", "competition", "category", "status", "is_pinned", "is_locked", "created_at")
    list_filter = ("competition", "status", "is_pinned", "is_locked")
    search_fields = ("title",)
    date_hierarchy = "created_at"
    raw_id_fields = ("author", "moderated_by", "category")


@admin.register(ForumPost)
class ForumPostAdmin(admin.ModelAdmin):
    list_display = ("__str__", "competition", "thread", "status", "created_at", "edited_at")
    list_filter = ("competition", "status")
    date_hierarchy = "created_at"
    raw_id_fields = ("thread", "author", "moderated_by")
    #: Treść wpisu jest wypowiedzią uczestnika, a nie polem konfiguracji: poprawianie jej
    #: z ``/admin/`` byłoby podmianą cudzego zdania bez śladu w audycie.
    readonly_fields = ("body", "created_at", "edited_at")


@admin.register(ForumReport)
class ForumReportAdmin(admin.ModelAdmin):
    list_display = ("__str__", "competition", "created_at", "resolved_at")
    list_filter = ("competition",)
    date_hierarchy = "created_at"
    raw_id_fields = ("post", "reporter", "resolved_by")
    readonly_fields = ("reason", "created_at")
