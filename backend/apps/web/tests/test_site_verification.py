"""Plik potwierdzający własność domeny dla Google Search Console (``/google….html``)."""

import pytest

pytestmark = pytest.mark.django_db


def test_google_verification_file_has_the_exact_content(web_client):
    response = web_client.get("/google13608a204a115889.html")

    assert response.status_code == 200
    assert response.content.decode() == "google-site-verification: google13608a204a115889.html"


def test_unknown_token_is_not_served(web_client):
    assert web_client.get("/google0000000000000000.html").status_code == 404
