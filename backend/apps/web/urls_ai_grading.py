"""Adresy oceny AI w panelu koordynatora – wzorce wpięte na **końcu** ``apps/web/urls.py``.

Osobny moduł z tego samego powodu co ``urls_fees``: ``urls.py`` zmienia kilka równoległych zadań
naraz, a jedna linijka rozwinięcia listy jest jedynym miejscem wspólnym. Bramki flagi tu nie ma –
o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404 przy wyłączonej ``ai_grading``),
a mapa adresów zależna od konkursu dawałaby ``reverse()`` raz adres, raz ``NoReverseMatch``.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_ai_grading

urlpatterns = [
    path(
        "coordinator/ai-grading/",
        coordinator_ai_grading.AiGradingSettingsView.as_view(),
        name="coordinator-ai-grading",
    ),
    path(
        "coordinator/problems/<int:pk>/ai/",
        coordinator_ai_grading.AiProblemProgressView.as_view(),
        name="coordinator-ai-problem",
    ),
    path(
        "coordinator/problems/<int:pk>/ai/generate/",
        coordinator_ai_grading.AiGenerateView.as_view(),
        name="coordinator-ai-generate",
    ),
    path(
        "coordinator/problems/<int:pk>/ai/test/",
        coordinator_ai_grading.AiTestWorkView.as_view(),
        name="coordinator-ai-test",
    ),
]
