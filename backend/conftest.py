# ruff: noqa: F401
# rest_framework.throttling przypisuje ``SimpleRateThrottle.timer = time.time`` w czasie importu.
# Gdyby ten import wypadł po raz pierwszy wewnątrz ``freeze_time``, klasa zapamiętałaby na stałe
# freezegunowy ``fake_time`` i kolejne testy przewracałyby się na TypeError – zależnie od kolejności
# uruchomienia. Import na starcie sesji ustala prawdziwy zegar raz na zawsze.
import rest_framework.throttling  # isort: skip

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
