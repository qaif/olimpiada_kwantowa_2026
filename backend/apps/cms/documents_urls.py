"""Adresy dokumentów Wagtaila z podmienionym widokiem serwującym (``apps.cms.views.serve``).

Wzorce są dokładnie te z ``wagtail.documents.urls`` – razem z nazwami, bo ``wagtaildocs_serve``
używa ``Document.url``, a ``wagtaildocs_authenticate_with_password`` – formularz kolekcji
chronionej hasłem. Zmienia się wyłącznie widok pierwszego wzorca: dokłada ``Cache-Control``
dla plików z kolekcji bez ograniczeń widoczności.
"""

from django.urls import path, re_path
from wagtail.documents.views import serve as wagtail_serve_views

from apps.cms.views import serve

urlpatterns = [
    re_path(r"^(\d+)/(.*)$", serve, name="wagtaildocs_serve"),
    path(
        "authenticate_with_password/<int:restriction_id>/",
        wagtail_serve_views.authenticate_with_password,
        name="wagtaildocs_authenticate_with_password",
    ),
]
