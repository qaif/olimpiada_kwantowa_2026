"""Adresy funkcji „Status ucznia” (prośba organizatora z 24.09.2026) – panel uczestnika i koordynatora.

Osobny moduł i rozwinięcie na **końcu** ``urlpatterns`` z tego samego powodu, co wzorce wydań E–K
(``apps/web/urls.py``): kolejność wzorców jest umową i dopisujemy, a nie przestawiamy. Wzorce stoją
w mapie zawsze – o tym, czy ekran istnieje w tym konkursie, rozstrzyga widok (404 przy wyłączonej
fladze ``student_status_certificate``), bo mapa adresów zależna od konkursu znaczyłaby ``reverse()``
dający raz adres, a raz ``NoReverseMatch``.

Adresy uczestnika są po polsku (``/me/status-ucznia/``), jak reszta tego, co uczestnik przepisuje
albo przesyła dalej (``/dyplomy/<kod>/``, ``/plakaty/``); adresy koordynatora – po angielsku, jak
cały panel ``/coordinator/``. Identyfikatora w adresach uczestnika nie ma wcale: uczestnik ma
w edycji dokładnie jedno bieżące zaświadczenie, więc nie ma tu czego podmienić na cudze.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_student_status, participant_student_status

urlpatterns = [
    path(
        "me/status-ucznia/",
        participant_student_status.StudentStatusView.as_view(),
        name="student-status",
    ),
    path(
        "me/status-ucznia/wzor.pdf",
        participant_student_status.StudentStatusTemplateView.as_view(),
        name="student-status-template",
    ),
    path(
        "me/status-ucznia/plik/",
        participant_student_status.StudentStatusOwnFileView.as_view(),
        name="student-status-file",
    ),
    path(
        "coordinator/student-status/",
        coordinator_student_status.CoordinatorStudentStatusView.as_view(),
        name="coordinator-student-status",
    ),
    path(
        "coordinator/student-status/<int:pk>/file/",
        coordinator_student_status.CoordinatorStudentStatusFileView.as_view(),
        name="coordinator-student-status-file",
    ),
    path(
        "coordinator/student-status/<int:pk>/accept/",
        coordinator_student_status.CoordinatorStudentStatusAcceptView.as_view(),
        name="coordinator-student-status-accept",
    ),
    path(
        "coordinator/student-status/<int:pk>/reject/",
        coordinator_student_status.CoordinatorStudentStatusRejectView.as_view(),
        name="coordinator-student-status-reject",
    ),
]
