"""Storage treści zadań (``Problem.statement_pdf``).

Po wprowadzeniu Wagtaila (T-09) ``default`` storage jest w produkcji publicznym bucketem
``public-media`` (polityka MinIO ``download``), bo tam mają trafiać media redakcyjne. Treść zadania
publiczna być nie może: staje się jawna dopiero po ``Stage.opens_at`` i wyłącznie przez
``ProblemStatementView``. Dlatego pole ma **własny** storage – alias ``private_media`` z
``settings.STORAGES`` (produkcyjnie prywatny bucket ``submissions`` pod prefiksem
``problem-statements/``, lokalnie i w testach zwykły ``FileSystemStorage``).

Storage podajemy jako *callable*: Django zapisuje w migracji referencję do funkcji, a nie instancję,
więc podmiana backendu w ustawieniach nie generuje migracji ani nie wymaga restartu deployu.
"""

from __future__ import annotations

from django.core.files.storage import Storage, storages

#: Alias w ``settings.STORAGES``. Jedno miejsce, w którym nazwa jest zapisana.
PRIVATE_MEDIA_ALIAS = "private_media"


def private_media_storage() -> Storage:
    """Storage dla plików, których nie wolno wystawić pod publicznym URL-em."""
    return storages[PRIVATE_MEDIA_ALIAS]
