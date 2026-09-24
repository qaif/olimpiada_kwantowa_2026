import pytest


@pytest.mark.django_db
def test_healthz_returns_ok_with_db_and_cache(client, monkeypatch):
    """T-01: GET /healthz/ zwraca 200 i {"status":"ok","db":true,"redis":true,…}.

    ``db_connections`` to poziom zajętości połączeń – odczyt podmieniony, bo testowy
    Postgres jest wspólny z innymi przebiegami i jego prawdziwa zajętość nie jest tu przedmiotem.
    """
    from apps.core import dbconnections

    monkeypatch.setattr(dbconnections, "_fetch", lambda: [(100, 3, "olimpiada-web", "idle", 5)])
    resp = client.get("/healthz/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True, "redis": True, "db_connections": "ok"}


def test_healthz_does_not_require_auth(client, db):
    resp = client.get("/healthz/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_openapi_schema_is_generated(admin_client):
    resp = admin_client.get("/api/schema/")
    assert resp.status_code == 200
    assert b"openapi" in resp.content
