"""Adresy webinarów LiveKit (zadanie WEB-01, ``apps.web.views.webinars``).

Osobny moduł rozwijany na **końcu** ``urlpatterns`` – jak pozostałe ``urls_*``. Dwa **nowe** pierwsze
segmenty, świadomie: ``webinars`` (strona odbiorców i pokój – wspólne dla uczestników, komisji
i prowadzących, więc ani ``me``, ani ``review``) oraz ``integrations`` (webhook serwera LiveKit –
adres dla maszyny, nie dla człowieka). Oba weszły do kontraktu tras djcms
(``manage.py djcms_routes --write``) i do ``RESERVED_SLUGS`` – strona CMS o takim slugu byłaby
martwa. Link gościa stoi pod istniejącym ``/zaproszenie/`` (``zaproszenie/webinar/<klucz>/``), obok
bramki pokoi Jitsi (``zaproszenie/wideo/<klucz>/``) – ten sam rodzaj adresu.

Wzorce stoją w mapie zawsze; bez flagi konkursu ``webinars`` (albo bez LiveKit) widoki odpowiadają 404.
"""

from __future__ import annotations

from django.urls import path

from .views import webinars

urlpatterns = [
    path("coordinator/webinars/", webinars.CoordinatorWebinarsView.as_view(), name="coordinator-webinars"),
    path(
        "coordinator/webinars/<int:pk>/",
        webinars.CoordinatorWebinarDetailView.as_view(),
        name="coordinator-webinar",
    ),
    path(
        "coordinator/webinars/<int:pk>/edit/",
        webinars.CoordinatorWebinarEditView.as_view(),
        name="coordinator-webinar-edit",
    ),
    path(
        "coordinator/webinars/<int:pk>/start/",
        webinars.CoordinatorWebinarStartView.as_view(),
        name="coordinator-webinar-start",
    ),
    path(
        "coordinator/webinars/<int:pk>/end/",
        webinars.CoordinatorWebinarEndView.as_view(),
        name="coordinator-webinar-end",
    ),
    path(
        "coordinator/webinars/<int:pk>/cancel/",
        webinars.CoordinatorWebinarCancelView.as_view(),
        name="coordinator-webinar-cancel",
    ),
    path(
        "coordinator/webinars/<int:pk>/announce/",
        webinars.CoordinatorWebinarAnnounceView.as_view(),
        name="coordinator-webinar-announce",
    ),
    path(
        "coordinator/webinars/<int:pk>/recordings/",
        webinars.CoordinatorWebinarRecordingView.as_view(),
        name="coordinator-webinar-recording",
    ),
    path(
        "coordinator/webinars/<int:pk>/stream/",
        webinars.CoordinatorWebinarStreamView.as_view(),
        name="coordinator-webinar-stream",
    ),
    path(
        "coordinator/webinars/<int:pk>/guest-link/",
        webinars.CoordinatorWebinarGuestLinkView.as_view(),
        name="coordinator-webinar-guest-link",
    ),
    path(
        "coordinator/webinars/<int:pk>/attendees/",
        webinars.CoordinatorWebinarAttendeeView.as_view(),
        name="coordinator-webinar-attendee",
    ),
    path("webinars/", webinars.WebinarsView.as_view(), name="webinars"),
    path(
        "webinars/notifications/", webinars.WebinarNotificationsView.as_view(), name="webinar-notifications"
    ),
    path("webinars/<int:pk>/room/", webinars.WebinarRoomView.as_view(), name="webinar-room"),
    path("webinars/<int:pk>/token/", webinars.WebinarTokenView.as_view(), name="webinar-token"),
    path("webinars/<int:pk>/control/", webinars.WebinarControlView.as_view(), name="webinar-control"),
    path(
        "webinars/<int:pk>/recordings/<int:recording_pk>/",
        webinars.WebinarRecordingView.as_view(),
        name="webinar-recording",
    ),
    path("zaproszenie/webinar/<str:key>/", webinars.WebinarGuestView.as_view(), name="webinar-guest"),
    path(
        "zaproszenie/webinar/<str:key>/room/",
        webinars.WebinarGuestRoomView.as_view(),
        name="webinar-guest-room",
    ),
    path(
        "zaproszenie/webinar/<str:key>/token/",
        webinars.WebinarGuestTokenView.as_view(),
        name="webinar-guest-token",
    ),
    path("integrations/livekit/webhook/", webinars.LiveKitWebhookView.as_view(), name="livekit-webhook"),
]
