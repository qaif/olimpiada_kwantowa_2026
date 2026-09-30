"""Adresy Wiadomości (zadanie CZ-01) – panel uczestnika, ustawienia konta i panel koordynatora.

Osobny moduł i rozwinięcie na **końcu** ``urlpatterns`` z tego samego powodu, co pozostałe moduły
``urls_*``: kolejność wzorców jest umową i dopisujemy, a nie przestawiamy. Wzorce stoją w mapie
zawsze – o tym, czy moduł działa w tym konkursie, rozstrzyga widok (404 u uczestnika, przekierowanie
na ustawienia u koordynatora).

W adresach stoi identyfikator **rozmowy** albo losowy token osoby z katalogu – nigdy ``pk``
uczestnika ani jego kod publiczny (§ 3 zadania). Wyjątkiem jest „Napisz wiadomość” z karty
uczestnika w panelu koordynatora: tam ``pk`` uczestnika jest już w adresie samej karty, a adres
odpowiada wyłącznie koordynatorowi tego konkursu.
"""

from __future__ import annotations

from django.urls import path

from .views import chat, coordinator_chat

urlpatterns = [
    path("me/messages/", chat.ChatInboxView.as_view(), name="chat"),
    path("me/messages/organizer/", chat.ChatOrganizerView.as_view(), name="chat-organizer"),
    path("me/messages/new/", chat.ChatDirectoryView.as_view(), name="chat-directory"),
    path("me/messages/key/", chat.ChatKeyView.as_view(), name="chat-key"),
    path("me/messages/new/<str:token>/", chat.ChatStartView.as_view(), name="chat-start"),
    path("me/messages/<int:pk>/", chat.ChatThreadView.as_view(), name="chat-thread"),
    path("me/messages/<int:pk>/report/", chat.ChatReportView.as_view(), name="chat-report"),
    path("me/messages/<int:pk>/block/", chat.ChatBlockView.as_view(), name="chat-block"),
    path("account/chat-settings/", chat.ChatPreferencesView.as_view(), name="chat-preferences"),
    path("coordinator/chat/", coordinator_chat.CoordinatorChatView.as_view(), name="coordinator-chat"),
    path(
        "coordinator/chat/moderation/",
        coordinator_chat.CoordinatorChatModerationView.as_view(),
        name="coordinator-chat-moderation",
    ),
    path(
        "coordinator/chat/settings/",
        coordinator_chat.CoordinatorChatSettingsView.as_view(),
        name="coordinator-chat-settings",
    ),
    path(
        "coordinator/chat/new/<int:participant_pk>/",
        coordinator_chat.CoordinatorChatNewView.as_view(),
        name="coordinator-chat-new",
    ),
    path(
        "coordinator/chat/<int:pk>/",
        coordinator_chat.CoordinatorChatThreadView.as_view(),
        name="coordinator-chat-thread",
    ),
]
