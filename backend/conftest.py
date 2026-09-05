import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Izolacja testów: cache trzyma m.in. liczniki throttlingu, które przeciekałyby między testami."""
    cache.clear()
    yield
    cache.clear()
