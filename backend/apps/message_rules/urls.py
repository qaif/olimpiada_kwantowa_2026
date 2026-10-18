"""Adresy automatycznych przypomnień (MSG-AUTO-01) – rozwijane w ``apps/web/urls.py`` (przestrzeń ``web:``).

Przestrzeń ``web:``, bo menu koordynatora (``apps.web.coordinator_nav``) podświetla pozycję po nazwie
adresu w tej przestrzeni. Przedrostek ``coordinator/messages/rules/`` stoi obok ekranu komunikatów,
do którego trafiają wygenerowane przypomnienia.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("coordinator/messages/rules/", views.RuleListView.as_view(), name="coordinator-message-rules"),
    path(
        "coordinator/messages/rules/new/",
        views.RuleCreateView.as_view(),
        name="coordinator-message-rule-create",
    ),
    path(
        "coordinator/messages/rules/<int:pk>/",
        views.RuleEditView.as_view(),
        name="coordinator-message-rule-edit",
    ),
    path(
        "coordinator/messages/rules/<int:pk>/toggle/",
        views.RuleToggleView.as_view(),
        name="coordinator-message-rule-toggle",
    ),
    path(
        "coordinator/messages/rules/<int:pk>/delete/",
        views.RuleDeleteView.as_view(),
        name="coordinator-message-rule-delete",
    ),
]
