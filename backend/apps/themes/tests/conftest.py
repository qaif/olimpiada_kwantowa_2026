"""Pamięci procesu motywów (wersje, silniki slotów, menu, arkusze dostosowania) – od zera w każdym teście.

Klucze tych pamięci (identyfikator wersji, konkursu, numer rewizji menu) powtarzają się między
testami, bo każdy test zaczyna od tej samej bazy – bez czyszczenia wynik zależałby od kolejności.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _theme_process_caches():
    from apps.themes import customize, menu
    from apps.themes.rendering import forget_engines
    from apps.themes.runtime import forget_runtime

    def clear():
        forget_runtime()
        forget_engines()
        menu.forget()
        customize._CSS_CACHE.clear()

    clear()
    yield
    clear()
