"""CSP i storage po wprowadzeniu Wagtaila (T-09).

Dwie reguły, które muszą zostać domknięte testem:

1. luźniejsza polityka CSP obowiązuje **wyłącznie** w ``/cms/`` i ``/admin/`` – strony publiczne
   nadal nie mają ``'unsafe-inline'`` w ``script-src``,
2. ``Problem.statement_pdf`` nie leży na ``default`` storage, bo ten jest w produkcji publicznym
   bucketem Wagtaila; treść zadania serwuje widok aplikacji i dopiero po ``opens_at``.
"""

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.tests.factories import DEFAULT_PASSWORD
from apps.competitions.models import Problem
from apps.competitions.storage import PRIVATE_MEDIA_ALIAS

pytestmark = pytest.mark.django_db


def script_src(policy: str) -> str:
    return next(part for part in policy.split("; ") if part.startswith("script-src"))


def test_public_pages_keep_nonce_only_script_policy(web_client):
    for url in ("/", "/aktualnosci/", "/zadania/", "/wyniki/"):
        policy = web_client.get(url).headers["Content-Security-Policy"]

        assert "'unsafe-inline'" not in script_src(policy), url
        assert "'unsafe-eval'" not in script_src(policy), url
        assert "'nonce-" in script_src(policy), url
        assert "frame-ancestors 'none'" in policy, url


def test_admin_paths_get_the_relaxed_policy(web_client, coordinator):
    web_client.login(email=coordinator.email, password=DEFAULT_PASSWORD)

    for url in ("/cms/", "/admin/"):
        policy = web_client.get(url).headers["Content-Security-Policy"]

        assert "'unsafe-inline'" in script_src(policy), url
        # Nonce i 'unsafe-inline' wzajemnie się znoszą – w polityce panelu nonce'a być nie może.
        assert "'nonce-" not in policy, url
        assert "frame-ancestors 'self'" in policy, url


def test_statement_pdf_uses_the_private_storage_not_wagtail_default():
    field_storage = Problem._meta.get_field("statement_pdf").storage

    assert field_storage is storages[PRIVATE_MEDIA_ALIAS]
    assert field_storage is not storages["default"]


def test_statement_is_served_by_the_view_only_after_opens_at(web_client, open_stage):
    problem = Problem.objects.create(
        stage=open_stage,
        number=1,
        title="Zadanie 1",
        statement_pdf=SimpleUploadedFile("z1.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )

    response = web_client.get(f"/api/competitions/problems/{problem.pk}/statement/")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/pdf"
    # Treść leci strumieniem z aplikacji; nie ma przekierowania na URL bucketu.
    assert b"".join(response.streaming_content) == b"%PDF-1.4 tresc"
