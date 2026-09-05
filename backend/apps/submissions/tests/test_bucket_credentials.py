"""Rozdział poświadczeń MinIO per bucket (przegląd Critica T-09, finding 2).

Sam podział aliasów ``STORAGES`` nie jest zabezpieczeniem, dopóki wszystkie storage'e chodzą na
jednym koncie administracyjnym: pomyłka w ścieżce redakcyjnej (a tam pliki przychodzą od człowieka)
sięgałaby wtedy także prac uczestników i treści zadań przed otwarciem etapu. Konta serwisowe
tworzy ``minio-init``: ``S3_PUBLIC_*`` widzi wyłącznie ``public-media``, ``S3_PRIVATE_*`` wyłącznie
``submissions``.

Testy są **offline** – nie tworzą klienta boto3 i nie dotykają sieci. Sprawdzamy jedno: który
klucz trafia do którego storage'a. Że polityki po stronie MinIO faktycznie odcinają obcy bucket,
dowodzi smoke w kontenerze (raport T-09), nie test jednostkowy.
"""

import importlib

import pytest
from django.test import override_settings

from apps.submissions import storage as storage_module

PUBLIC = ("klucz-publiczny", "sekret-publiczny")
PRIVATE = ("klucz-prywatny", "sekret-prywatny")
ROOT = ("klucz-root", "sekret-root")


@pytest.fixture
def recorded_clients(monkeypatch) -> list[tuple]:
    """Podmienia fabrykę klientów boto3 na rejestrator argumentów (żadnego I/O)."""
    calls: list[tuple] = []

    def fake_client(endpoint_url, access_key, secret_key, region):
        calls.append((endpoint_url, access_key, secret_key, region))
        return object()

    monkeypatch.setattr(storage_module, "_s3_client", fake_client)
    return calls


@override_settings(
    S3_ENDPOINT_URL="http://minio:9000",
    S3_PUBLIC_ENDPOINT_URL="http://minio:9000",
    S3_PRIVATE_ACCESS_KEY=PRIVATE[0],
    S3_PRIVATE_SECRET_KEY=PRIVATE[1],
    S3_PUBLIC_ACCESS_KEY=PUBLIC[0],
    S3_PUBLIC_SECRET_KEY=PUBLIC[1],
    S3_ACCESS_KEY=ROOT[0],
    S3_SECRET_KEY=ROOT[1],
)
def test_submission_storage_client_uses_the_private_service_account(recorded_clients):
    storage_module.S3SubmissionStorage().client

    assert recorded_clients == [("http://minio:9000", *PRIVATE, "us-east-1")]


@override_settings(
    S3_ENDPOINT_URL="http://minio:9000",
    # Host publiczny to inny adres tego samego MinIO – klient podpisujący jest osobny…
    S3_PUBLIC_ENDPOINT_URL="https://s3.example.test",
    S3_PRIVATE_ACCESS_KEY=PRIVATE[0],
    S3_PRIVATE_SECRET_KEY=PRIVATE[1],
    S3_PUBLIC_ACCESS_KEY=PUBLIC[0],
    S3_PUBLIC_SECRET_KEY=PUBLIC[1],
)
def test_presign_client_signs_with_the_private_account_not_the_public_one(recorded_clients):
    """…ale podpisuje kluczem prywatnym. Presigned URL dziedziczy uprawnienia klucza."""
    storage_module.S3SubmissionStorage().presign_client

    assert recorded_clients == [("https://s3.example.test", *PRIVATE, "us-east-1")]
    assert PUBLIC[0] not in [call[1] for call in recorded_clients]


def reload_settings(monkeypatch, **environment) -> object:
    """Przeładowuje moduły ustawień pod podanym środowiskiem i zwraca ``config.settings.production``.

    Ustawienia czytają ``os.environ`` w chwili importu, więc wyboru kluczy nie da się sprawdzić
    przez ``override_settings`` – trzeba przejść tę samą drogę, co start procesu. Przeładowanie
    nie rusza aktywnej konfiguracji Django: ``django.conf.settings`` trzyma własną kopię wartości.
    """
    for name, value in environment.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    base = importlib.reload(importlib.import_module("config.settings.base"))
    production = importlib.reload(importlib.import_module("config.settings.production"))
    return base, production


def test_production_storages_use_separate_accounts_per_bucket(monkeypatch):
    _, production = reload_settings(
        monkeypatch,
        MINIO_ROOT_USER=ROOT[0],
        MINIO_ROOT_PASSWORD=ROOT[1],
        S3_PUBLIC_ACCESS_KEY=PUBLIC[0],
        S3_PUBLIC_SECRET_KEY=PUBLIC[1],
        S3_PRIVATE_ACCESS_KEY=PRIVATE[0],
        S3_PRIVATE_SECRET_KEY=PRIVATE[1],
    )

    default = production.STORAGES["default"]["OPTIONS"]
    private = production.STORAGES["private_media"]["OPTIONS"]

    # Wagtail (alias ``default``) chodzi wyłącznie na koncie bucketu publicznego…
    assert (default["access_key"], default["secret_key"]) == PUBLIC
    # …a treści zadań na koncie bucketu prywatnego. Żadne z nich nie jest kontem root.
    assert (private["access_key"], private["secret_key"]) == PRIVATE
    assert ROOT[0] not in (default["access_key"], private["access_key"])


def test_missing_service_accounts_fall_back_to_root_credentials(monkeypatch, caplog):
    """Zgodność wsteczna z instalacjami sprzed T-09 – ale głośna, nie po cichu."""
    base, production = reload_settings(
        monkeypatch,
        MINIO_ROOT_USER=ROOT[0],
        MINIO_ROOT_PASSWORD=ROOT[1],
        S3_PUBLIC_ACCESS_KEY="",
        S3_PUBLIC_SECRET_KEY="",
        S3_PRIVATE_ACCESS_KEY="",
        S3_PRIVATE_SECRET_KEY="",
    )

    assert (base.S3_PUBLIC_ACCESS_KEY, base.S3_PUBLIC_SECRET_KEY) == ROOT
    assert (base.S3_PRIVATE_ACCESS_KEY, base.S3_PRIVATE_SECRET_KEY) == ROOT
    assert production.STORAGES["default"]["OPTIONS"]["access_key"] == ROOT[0]
    # Cichy fallback byłby gorszy od jego braku: instalacja wyglądałaby na rozdzieloną, a nie była.
    assert "S3_PUBLIC_ACCESS_KEY" in caplog.text
    assert "S3_PRIVATE_ACCESS_KEY" in caplog.text


@pytest.fixture(autouse=True)
def _restore_settings_modules():
    """Po testach reloadujących wracamy do modułów zbudowanych z prawdziwego środowiska.

    Bez tego kolejny test w sesji zobaczyłby ``config.settings.base`` z kluczami z monkeypatcha.
    """
    yield
    importlib.reload(importlib.import_module("config.settings.base"))
    importlib.reload(importlib.import_module("config.settings.production"))
