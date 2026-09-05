"""Swagger UI (``/api/docs/``) pod ostrą polityką CSP (przegląd T-08, ustalenie 2).

Regresja: domyślny szablon drf-spectacular wstawia skrypty bez ``nonce`` (w tym jeden inline),
więc przy ``script-src`` bez ``'unsafe-inline'`` strona dokumentacji była pusta. Kuszące
„rozwiązanie” – dopisanie ``/api/docs/`` do ścieżek z luźniejszą polityką – jest tu wprost
zabronione testem: dokumentacja ma zostać pod tą samą polityką, co reszta serwisu.
"""

import re

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db

DOCS_URL = "/api/docs/"
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)


@pytest.fixture
def docs(web_client):
    response = web_client.get(DOCS_URL)
    assert response.status_code == 200
    return response


def test_every_script_tag_carries_a_nonce(docs):
    tags = SCRIPT_TAG.findall(docs.content.decode())

    # Trzy znaczniki oryginału: bundle, standalone preset i inline'owa konfiguracja.
    assert len(tags) >= 3
    for tag in tags:
        assert "nonce=" in tag, tag
    assert 'nonce=""' not in docs.content.decode()


def test_docs_keep_the_strict_policy(docs):
    policy = docs.headers["Content-Security-Policy"]
    script_src = next(part for part in policy.split("; ") if part.startswith("script-src"))

    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert "'nonce-" in script_src
    assert "frame-ancestors 'none'" in policy


def test_swagger_assets_are_pinned_and_verified_by_sri(docs):
    content = docs.content.decode()

    assert f"swagger-ui-dist@{settings.SWAGGER_UI_VERSION}" in content
    assert "swagger-ui-dist@latest" not in content
    for filename, digest in settings.SWAGGER_UI_SRI.items():
        assert filename in content, filename
        assert f'integrity="{digest}"' in content, filename


def test_docs_render_the_openapi_schema(docs):
    """Sanity: to nadal jest Swagger, a nie sama skorupa szablonu."""
    content = docs.content.decode()

    assert 'id="swagger-ui"' in content
    assert "SwaggerUIBundle(" in content
    assert "/api/schema/" in content
