"""Rozszerzenia stron djcms (§ 6.1 i 6.3 docs/tasks/DJ-01.md).

- ``MenuExtension`` (DJ-01d) – ustawienia menu serwisu, które w Wagtailu są stałymi w kodzie
  (``apps/cms/context_processors.py``: ``PRIMARY_MENU_SLUGS``, ``DocumentIndexPage`` jako lista
  rozwijana, ``PROMOTED_DOCUMENT_SLUGS``). Na ``dj.`` są polami strony, bo redaktor ma móc je
  zmienić bez wydania – to jest część porównania. ``PageExtension`` (a nie
  ``PageContentExtension``): miejsce w menu jest cechą strony w drzewie, a nie jednej wersji jej
  treści – zmiana nie ma czekać na publikację wersji roboczej, tak samo jak przeciągnięcie strony
  w drzewie (kolejność menu) nie czeka.
- ``NewsMeta``, ``DocumentMeta``, ``ArchiveMeta`` (DJ-01e) – pola typów stron Wagtaila, które nie są
  treścią w slotach: data i lead aktualności, metryka dokumentu, powiązanie z edycją w archiwum.
  ``PageContentExtension``: należą do **wersji** treści – poprawka metryki regulaminu czeka na
  publikację razem z poprawką tekstu, a historia wersji pokazuje obie. djangocms-versioning kopiuje
  je do nowej wersji roboczej (``default_copy`` → ``BaseExtension.copy``; pilnuje tego test
  ``apps/pages/tests/test_page_types.py``).
- ``LoginAttempt`` – licznik blokady logowania (``apps.pages.auth``).
"""

from __future__ import annotations

from cms.extensions import PageContentExtension, PageExtension
from cms.extensions.extension_pool import extension_pool
from django.db import models
from django.utils import timezone


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


# --- rozszerzenia treści typów stron (tabela 6.1) ------------------------------------------------


@extension_pool.register
class NewsMeta(PageContentExtension):
    """Data i lead aktualności (``NewsPage.date``/``lead``).

    Data jest polem, a nie datą utworzenia strony: aktualność bywa przenoszona z innego kanału
    z datą pierwszej publikacji. Po niej sortuje się newsroom (malejąco) i pas na stronie głównej.
    """

    date = models.DateField("data publikacji", default=timezone.localdate)
    lead = models.TextField("lead", max_length=500, blank=True)

    class Meta:
        verbose_name = "metryka aktualności"
        verbose_name_plural = "metryki aktualności"

    def __str__(self) -> str:
        return f"Aktualność z {self.date:%Y-%m-%d}"


@extension_pool.register
class DocumentMeta(PageContentExtension):
    """Metryka dokumentu urzędowego (``DocumentPage.version_label``/``document_date``/``status_label``).

    Osobne pola, a nie akapit treści: przy dokumencie prawnym pierwsze pytanie czytelnika brzmi
    „czy to obowiązująca wersja” – odpowiedź nie może zależeć od tego, czy redaktor pamiętał
    o poprawieniu zdania w środku tekstu.
    """

    version_label = models.CharField("wersja", max_length=50, blank=True)
    document_date = models.DateField("data dokumentu", null=True, blank=True)
    status_label = models.CharField(
        "status", max_length=200, blank=True, help_text="Np. „Projekt do zatwierdzenia uchwałą Zarządu”."
    )

    class Meta:
        verbose_name = "metryka dokumentu"
        verbose_name_plural = "metryki dokumentów"

    def __str__(self) -> str:
        return f"Dokument, wersja {self.version_label or '–'}"

    @property
    def is_filled(self) -> bool:
        return bool(self.version_label or self.document_date or self.status_label)


@extension_pool.register
class ArchiveMeta(PageContentExtension):
    """Powiązanie strony archiwum z edycją w bazie zawodów (``ArchiveEditionPage.edition``).

    Sam identyfikator, a nie klucz obcy: edycje żyją w bazie aplikacji głównej, do której djcms nie
    ma dostępu. Listę do wyboru daje API (``GET editions`` – ``admin.ArchiveMetaForm``), a z tego
    identyfikatora wtyczka „Wyniki edycji archiwalnej” (DJ-01f) pyta
    ``GET editions/<id>/results``. Pusto = strona bez linków do wyników, jak w Wagtailu.
    """

    edition_id = models.PositiveIntegerField(
        "edycja",
        null=True,
        blank=True,
        help_text="Edycja w systemie zawodów: stąd biorą się linki do tabel wyników.",
    )

    class Meta:
        verbose_name = "edycja w archiwum"
        verbose_name_plural = "edycje w archiwum"

    def __str__(self) -> str:
        return f"Edycja #{self.edition_id}" if self.edition_id else "Edycja: brak"


# --- blokada logowania ---------------------------------------------------------------------------


class LoginAttempt(models.Model):
    """Jedna nieudana (albo właśnie sprawdzana) próba logowania – licznik blokady z ``apps.pages.auth``.

    Tabela, a nie bufor: bufor plikowy wyrzucał wpisy po przekroczeniu limitu i pozwalał skasować
    licznik ofiary zalewem prób na wymyślone loginy (uzasadnienie w docstringu ``auth``). Wiersze
    starsze niż dłuższe z okien kasuje sama blokada przy kolejnej próbie – tabela ma rozmiar
    „porażki z ostatniej godziny”, a nie historię.

    ``at`` to znacznik ``time.time()`` (liczba), a nie ``DateTimeField``: okno liczymy
    arytmetyką na sekundach, a testy podmieniają zegar w jednym miejscu.
    """

    pair_key = models.CharField("skrót pary (IP, login)", max_length=64, db_index=True)
    user_key = models.CharField("skrót loginu", max_length=64, db_index=True)
    at = models.FloatField("chwila próby (sekundy od epoki)", db_index=True)

    class Meta:
        verbose_name = "nieudana próba logowania"
        verbose_name_plural = "nieudane próby logowania"

    def __str__(self) -> str:
        return f"Próba logowania {self.pk}"
