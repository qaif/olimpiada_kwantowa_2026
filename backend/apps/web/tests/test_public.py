"""Kryterium 8 z T-08 oraz nagłówek CSP: strony publiczne bez logowania."""

import pytest
from django.utils import timezone

from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

SNAPSHOT = [
    {
        "rank": 1,
        "display": "OLM-AAAAAA",
        "district": "mazowieckie",
        "points": {"1": 6, "2": 5},
        "total": 11,
        "qualified": True,
    },
    {
        "rank": 2,
        "display": "OLM-BBBBBB",
        "district": "małopolskie",
        "points": {"1": 2, "2": 0},
        "total": 2,
        "qualified": False,
    },
]


@pytest.fixture
def publication(elim_stage):
    elim_stage.results_published_at = timezone.now()
    elim_stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(
        stage=elim_stage, anonymization=Anonymization.CODE, snapshot=SNAPSHOT
    )


def test_public_results_render_snapshot_without_login(web_client, publication):
    response = web_client.get(f"/results/{publication.stage_id}/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "OLM-AAAAAA" in content
    assert "OLM-BBBBBB" in content
    assert ">11<" in content


def test_results_of_unpublished_stage_are_404(web_client, elim_stage):
    assert web_client.get(f"/results/{elim_stage.pk}/").status_code == 404


def test_csp_header_is_present_and_has_no_unsafe_inline_scripts(web_client, elim_stage):
    response = web_client.get("/")
    policy = response.headers["Content-Security-Policy"]

    assert "script-src" in policy
    script_src = next(part for part in policy.split("; ") if part.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert "https://cdnjs.cloudflare.com" in script_src
    assert "https://cdn.jsdelivr.net" in script_src
    assert "'nonce-" in script_src
    # Style mają świadomy wyjątek – patrz apps/web/middleware.py.
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_home_page_loads_pinned_cdn_scripts_with_sri(web_client, elim_stage):
    content = web_client.get("/").content.decode()

    assert "cdnjs.cloudflare.com/ajax/libs/htmx/2.0.4/htmx.min.js" in content
    assert "cdn.jsdelivr.net/npm/@alpinejs/csp@3.17.1/dist/cdn.min.js" in content
    assert content.count('integrity="sha384-') >= 2
    assert "<script>" not in content  # żadnego skryptu inline


def test_login_page_is_public(web_client):
    assert web_client.get("/login/").status_code == 200


def test_registration_creates_participant(web_client):
    response = web_client.post(
        "/register/",
        {
            "email": "nowy@example.test",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Nowy",
            "last_name": "Uczestnik",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "birth_year": 2008,
            "gdpr_consent": "on",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login/"


def test_registration_without_gdpr_consent_shows_domain_error(web_client):
    response = web_client.post(
        "/register/",
        {
            "email": "brak@example.test",
            "password": "Poprawne-Haslo-2026",
            "first_name": "Brak",
            "last_name": "Zgody",
            "school": "LO nr 7",
            "district": "mazowieckie",
            "birth_year": 2008,
        },
    )

    assert response.status_code == 200
    assert "Zgoda na przetwarzanie danych osobowych jest wymagana." in response.content.decode()
