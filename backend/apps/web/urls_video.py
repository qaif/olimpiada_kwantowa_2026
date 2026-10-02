"""Adresy wejścia do pokoi wideo przez platformę i pokoi bez terminu (v0.39.0, ``apps.web.views.video``).

Osobny moduł i rozwinięcie na **końcu** ``urlpatterns`` – jak pozostałe moduły ``urls_*``:
kolejność wzorców jest umową, więc dopisujemy, a nie przestawiamy.

Wyłącznie **istniejące** pierwsze segmenty (``me``, ``coordinator``, ``review``, ``zaproszenie``):
nowy segment zmieniłby kontrakt tras z djcms (``djcms_contract/app_routes.json``) i listę
zastrzeżonych slugów stron. Bramka linku-zaproszenia stoi pod ``/zaproszenie/`` obok zaproszeń
ucznia – to ten sam rodzaj adresu: publiczny, z kluczem, przysłany komuś bez konta. Dwa segmenty
(``wideo/<klucz>``) nie kolidują ze wzorcem ``zaproszenie/<token>/`` (konwerter ``str`` nie
przechodzi przez ukośnik). Adres nie pasuje do listy stron publicznych trzymanych w pamięci
podręcznej (``apps.web.page_cache.ALLOWED_PATHS``/``ALLOWED_PREFIXES``) i tak ma zostać.

Wzorce stoją w mapie zawsze; bez sekretu przepustek każdy z widoków odpowiada 404.
"""

from __future__ import annotations

from django.urls import path

from .views import video

urlpatterns = [
    # Uczestnik: rozmowa i próba sprzętu – po identyfikatorze **etapu**, jak rezygnacja z terminu
    # (``interview-cancel``): w etapie jest dokładnie jeden zapis tej osoby, więc w adresie nie ma
    # identyfikatora, który dałoby się podmienić na cudzy.
    path(
        "me/stages/<int:stage_id>/interview/join/", video.InterviewJoinView.as_view(), name="interview-join"
    ),
    path(
        "me/stages/<int:stage_id>/interview/precheck/",
        video.InterviewPrecheckView.as_view(),
        name="interview-precheck",
    ),
    # Koordynator: pokój terminu rozmowy.
    path(
        "coordinator/interview-slots/<int:pk>/join/",
        video.SlotJoinView.as_view(),
        name="coordinator-interview-slot-join",
    ),
    path(
        "coordinator/interview-slots/<int:pk>/precheck/",
        video.SlotPrecheckView.as_view(),
        name="coordinator-interview-slot-precheck",
    ),
    # Koordynator: pokoje bez terminu i uprawnienia komisji.
    path(
        "coordinator/video-rooms/", video.CoordinatorVideoRoomsView.as_view(), name="coordinator-video-rooms"
    ),
    path(
        "coordinator/video-rooms/<int:pk>/links/",
        video.CoordinatorVideoRoomLinksView.as_view(),
        name="coordinator-video-room-links",
    ),
    path(
        "coordinator/video-rooms/<int:pk>/rotate/",
        video.CoordinatorVideoRoomRotateView.as_view(),
        name="coordinator-video-room-rotate",
    ),
    path(
        "coordinator/video-rooms/<int:pk>/close/",
        video.CoordinatorVideoRoomCloseView.as_view(),
        name="coordinator-video-room-close",
    ),
    path(
        "coordinator/video-rooms/<int:pk>/join/",
        video.CoordinatorVideoRoomJoinView.as_view(),
        name="coordinator-video-room-join",
    ),
    path(
        "coordinator/video-rooms/issuers/<int:pk>/",
        video.CoordinatorVideoIssuerView.as_view(),
        name="coordinator-video-issuer",
    ),
    path(
        "coordinator/video-rooms/issuers/<int:pk>/close-rooms/",
        video.CoordinatorVideoIssuerCloseRoomsView.as_view(),
        name="coordinator-video-issuer-close-rooms",
    ),
    # Komisja (recenzent albo komisja odwoławcza tego konkursu). Pod ``/review/``, bo tam jest
    # panel komisji; członek samej komisji odwoławczej przechodzi tę samą bramkę
    # (``CommitteeRequiredMixin``), a wraca do ``/appeals/``.
    # Komisja prowadzi rozmowy kwalifikacyjne: wejście do pokoju terminu jako gospodarz.
    path(
        "review/interview-slots/<int:pk>/join/",
        video.CommitteeSlotJoinView.as_view(),
        name="committee-interview-slot-join",
    ),
    path(
        "review/interview-slots/<int:pk>/precheck/",
        video.CommitteeSlotPrecheckView.as_view(),
        name="committee-interview-slot-precheck",
    ),
    path("review/video-rooms/", video.CommitteeVideoRoomsView.as_view(), name="committee-video-rooms"),
    path(
        "review/video-rooms/<int:pk>/links/",
        video.CommitteeVideoRoomLinksView.as_view(),
        name="committee-video-room-links",
    ),
    path(
        "review/video-rooms/<int:pk>/rotate/",
        video.CommitteeVideoRoomRotateView.as_view(),
        name="committee-video-room-rotate",
    ),
    path(
        "review/video-rooms/<int:pk>/close/",
        video.CommitteeVideoRoomCloseView.as_view(),
        name="committee-video-room-close",
    ),
    path(
        "review/video-rooms/<int:pk>/join/",
        video.CommitteeVideoRoomJoinView.as_view(),
        name="committee-video-room-join",
    ),
    # Bramka linku-zaproszenia – bez konta.
    path("zaproszenie/wideo/<str:key>/", video.VideoGatewayView.as_view(), name="video-gateway"),
]
