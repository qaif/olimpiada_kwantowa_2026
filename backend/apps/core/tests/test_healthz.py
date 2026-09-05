import pytest


@pytest.mark.django_db
def test_healthz_returns_ok_with_db_and_cache(client):
    """T-01: GET /healthz/ zwraca 200 i {"status":"ok","db":true,"redis":true}."""
    resp = client.get("/healthz/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True, "redis": True}


def test_healthz_does_not_require_auth(client, db):
    resp = client.get("/healthz/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_openapi_schema_is_generated(admin_client):
    resp = admin_client.get("/api/schema/")
    assert resp.status_code == 200
    assert b"openapi" in resp.content
