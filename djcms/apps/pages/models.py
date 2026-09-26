"""Rozszerzenia stron djcms (§ 6.1 i 6.3 docs/tasks/DJ-01.md).

Na etapie ramy (DJ-01d) jest tu wyłącznie ``MenuExtension`` – ustawienia menu serwisu, które
w Wagtailu są stałymi w kodzie (``apps/cms/context_processors.py``: ``PRIMARY_MENU_SLUGS``,
``DocumentIndexPage`` jako lista rozwijana, ``PROMOTED_DOCUMENT_SLUGS``). Na ``dj.`` są polami
strony, bo redaktor ma móc je zmienić bez wydania – to jest część porównania.

``PageExtension`` (a nie ``PageContentExtension``): miejsce w menu jest cechą strony w drzewie,
a nie jednej wersji jej treści – zmiana nie ma czekać na publikację wersji roboczej, tak samo jak
przeciągnięcie strony w drzewie (kolejność menu) nie czeka.
"""

from __future__ import annotations

from cms.extensions import PageExtension
from cms.extensions.extension_pool import extension_pool
from django.db import models


@extension_pool.register
class MenuExtension(PageExtension):
    """Rola strony w menu serwisu. Brak rekordu = zwykła pozycja (wszystko ``False``)."""

    primary = models.BooleanField(
        "w przyklejonym pasku",
        default=False,
        help_text="Pozycja pojawia się także w pasku u góry okna po przewinięciu strony "
        "(jak „Zadania”, „Harmonogram”, „Warsztaty”, „Kontakt”).",
    )
    expand = models.BooleanField(
        "lista rozwijana",
        default=False,
        help_text="Podstrony tej strony pokazują się w menu jako lista rozwijana (jak „Dokumenty”).",
    )
    promote = models.BooleanField(
        "wyniesiona do menu głównego",
        default=False,
        help_text="Podstrona listy rozwijanej, która stoi w menu jako osobna pozycja – zaraz po stronie "
        "głównej – i znika z listy (jak „Komitety”).",
    )

    class Meta:
        verbose_name = "ustawienia menu"
        verbose_name_plural = "ustawienia menu"

    def __str__(self) -> str:
        return f"Menu: strona {self.extended_object_id}"
