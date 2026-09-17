"""Rejestracja modeli testów w ``/admin/`` – wyłącznie do podglądu i awaryjnej interwencji.

Codzienna praca z testem odbywa się w panelu koordynatora (``/coordinator/stages/<id>/quiz/``),
bo tam stoją reguły, których surowy formularz admina nie zna: walidacja klucza odpowiedzi per
rodzaj pytania, blokada zmian w teście z podejściami i przeliczenie punktów z wpisem audytowym.
Admin zostaje dla sytuacji, w których trzeba **zobaczyć** stan danych (spór o przebieg podejścia,
zgłoszenie „nie zapisała się odpowiedź”), i dlatego podejścia oraz odpowiedzi są tu tylko
do odczytu – edycja cudzej odpowiedzi po zawodach nie jest czynnością, którą wolno wykonać
jednym kliknięciem i bez śladu.
"""

from django.contrib import admin

from .models import Quiz, QuizAnswer, QuizAttempt, QuizOption, QuizQuestion


class QuizOptionInline(admin.TabularInline):
    model = QuizOption
    extra = 0


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "stage", "duration_minutes", "attempts_allowed", "show_results_after")
    list_filter = ("show_results_after", "negative_floor")
    search_fields = ("title", "stage__name")
    raw_id_fields = ("stage",)


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "quiz", "pool", "kind", "points", "negative_points")
    list_filter = ("kind", "pool")
    search_fields = ("text",)
    raw_id_fields = ("quiz",)
    inlines = [QuizOptionInline]


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("__str__", "quiz", "status", "score", "max_points", "started_at", "submitted_at")
    list_filter = ("status", "quiz")
    raw_id_fields = ("quiz", "entry")
    readonly_fields = tuple(field.name for field in QuizAttempt._meta.fields)


@admin.register(QuizAnswer)
class QuizAnswerAdmin(admin.ModelAdmin):
    list_display = ("__str__", "is_correct", "points_awarded", "updated_at")
    list_filter = ("is_correct",)
    raw_id_fields = ("attempt", "question")
    readonly_fields = tuple(field.name for field in QuizAnswer._meta.fields)
