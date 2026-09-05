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

from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.utils.functional import cached_property

#: Alias w ``settings.STORAGES``. Jedno miejsce, w którym nazwa jest zapisana.
PRIVATE_MEDIA_ALIAS = "private_media"


class PrivateMediaFileSystemStorage(FileSystemStorage):
    """Lokalny odpowiednik prywatnego bucketu: **osobny podkatalog**, nie ten sam co ``default``.

    Bez tego rozdziału alias ``private_media`` jest w devie i w testach wyłącznie etykietą:
    plik leży w tym samym drzewie, co media redakcyjne, więc test „``statement_pdf`` nie idzie
    przez ``default``” sprawdzałby nazwę aliasu, a nie skutek. Z osobnym katalogiem da się
    postawić asercję na ścieżce – dokładnie tak, jak w produkcji stawia się ją na buckecie.

    ``base_location`` jest liczone leniwie z ``MEDIA_ROOT``, a nie zapisane w ``STORAGES`` jako
    stała: testy podmieniają ``MEDIA_ROOT`` per test (``conftest.py``), a Django po zmianie tego
    ustawienia czyści cache tej właściwości (``StorageSettingsMixin._clear_cached_properties``).
    """

    #: Podkatalog w ``MEDIA_ROOT``. Nazwa jest częścią kontraktu testów, stąd stała, a nie literał.
    subdirectory = "private"

    @cached_property
    def base_location(self):
        root = self._value_or_setting(self._location, settings.MEDIA_ROOT)
        return Path(root) / self.subdirectory


def private_media_storage() -> Storage:
    """Storage dla plików, których nie wolno wystawić pod publicznym URL-em."""
    return storages[PRIVATE_MEDIA_ALIAS]
