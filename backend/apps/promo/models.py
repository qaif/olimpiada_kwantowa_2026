"""Plakaty do pobrania i zdarzenia ich pobrania (prośba organizatora z 23.09.2026).

Dwa modele, dwie różne natury danych:

- ``PromoMaterial`` – **treść** serwisu: plik, tytuł, podgląd, kolejność, publikacja. Redaguje ją
  koordynator na ``/coordinator/posters/``, czyta ją każdy na ``/plakaty/``,
- ``PromoDownload`` – **zdarzenie**: „ktoś pobrał ten plik o tej porze”. Z nich powstają
  statystyki na ekranie koordynatora: pobrania łącznie i pobrania z unikalnych adresów IP.
  Zdarzenie nie przechowuje adresu IP, nagłówka przeglądarki ani konta. Jedyne, co zostaje po
  człowieku, to ``ip_hash`` – HMAC-SHA256 adresu IP z kluczem serwera (``apps.promo.tracking``),
  czyli **pseudonim**: te same dwa pobrania z jednego adresu dają ten sam skrót (stąd „unikalne
  IP” w dowolnym okresie), ale bez klucza skrótu nie da się zamienić z powrotem na adres. Po
  ``IP_HASH_RETENTION_MONTHS`` miesiącach skrót jest zerowany (``apps.promo.tasks``), a zdarzenie
  zostaje – do liczby pobrań łącznie.

Plik plakatu leży w storage **prywatnym** (``private_media``), a nie w publicznym buckecie mediów
redakcyjnych – mimo że sam plakat jest jawny. Powód jest jeden i dotyczy statystyk: plik
z publicznym adresem w buckecie dałoby się pobierać z pominięciem licznika (link krąży po grupach
nauczycieli dłużej niż strona), więc liczby na ekranie koordynatora mówiłyby „tyle przez stronę”,
a nie „tyle w ogóle”. Podgląd (miniatura) jest obrazkiem na karcie i leży w storage publicznym,
bo tam leżą wszystkie obrazki serwisu i tak je serwuje przeglądarka.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager
from apps.competitions.storage import private_media_storage

from .validators import FORMAT_CHOICES, FORMATS


def material_upload_to(instance, filename: str) -> str:
    """Klucz pliku w storage: konkurs + losowy identyfikator + rozszerzenie **z treści**.

    Nazwy podanej przez przesyłającego nie ma w kluczu wcale: bywa długa, ma polskie znaki i spacje,
    a przede wszystkim jest wartością spoza naszej kontroli. Nazwę, pod którą plik zapisze się
    u pobierającego, składa widok pobrania z tytułu (``PromoMaterial.download_name``).
    """
    ext = instance.file_format or "bin"
    return f"promo/{instance.competition_id or 'none'}/{uuid.uuid4().hex}.{ext}"


def preview_upload_to(instance, filename: str) -> str:
    """Klucz podglądu – ta sama zasada, co wyżej; rozszerzenie z nazwy nadanej przez nasz kod."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ("jpg", "png"):
        ext = "jpg"
    return f"promo/previews/{instance.competition_id or 'none'}/{uuid.uuid4().hex}.{ext}"


class PromoMaterial(models.Model):
    """Jeden plakat (albo ulotka) do pobrania: plik, podpis i miejsce na liście."""

    #: ``PROTECT``, jak przy ``Category.competition``: skasowanie konkursu razem z plakatami
    #: skasowałoby też historię ich pobrań, czyli statystyki, o które organizator prosił.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="promo_materials",
        verbose_name="konkurs",
    )
    title = models.CharField("tytuł", max_length=150)
    #: Krótki opis pod tytułem: format papieru, orientacja, przeznaczenie („A4 pionowy”,
    #: „A3 do gabloty”, „wersja do druku w kolorze”). Jedna linia, nie akapit.
    description = models.CharField("opis", max_length=200, blank=True)
    #: Nagłówek **wspólnej karty** na ``/plakaty/`` (prośba organizatora z 23.09.2026: „jedna karta
    #: na format papieru, kilka przycisków”). Pliki jednego konkursu z identyczną, niepustą grupą
    #: („A3 · 297×420 mm”) stają na jednej karcie z jednym podglądem i przyciskiem na każdy plik;
    #: pusta grupa = osobna karta jak dotąd. Grupa to napis, a nie osobny model: karta nie ma
    #: własnych danych poza nagłówkiem, a statystyki i pobranie zostają przy pliku.
    group = models.CharField("karta (grupa)", max_length=100, blank=True)
    #: Napis na przycisku pliku we wspólnej karcie („JPG”, „PDF ze spadem 3 mm”). Pusty = sam
    #: format pliku (``variant_name``). Na karcie pojedynczego pliku nieużywany.
    variant_label = models.CharField("wariant (przycisk)", max_length=60, blank=True)
    file = models.FileField(
        "plik",
        upload_to=material_upload_to,
        storage=private_media_storage,
        max_length=255,
    )
    #: Format rozpoznany **po treści** przy zapisie (``apps.promo.validators``), a nie z rozszerzenia.
    #: Trzymany w kolumnie, bo lista publiczna pokazuje go na każdej karcie – czytanie nagłówka
    #: pliku z prywatnego storage przy każdym wyświetleniu strony byłoby żądaniem do S3 na kartę.
    file_format = models.CharField("format", max_length=4, choices=FORMAT_CHOICES)
    #: Rozmiar w bajtach – z tego samego powodu, co format: pokazywany na karcie bez sięgania do S3.
    file_size = models.PositiveBigIntegerField("rozmiar (B)", default=0)
    #: Miniatura na karcie. Dla JPG/PNG powstaje sama (``apps.promo.previews``), dla PDF-a można ją
    #: wgrać; bez niej karta pokazuje ikonę dokumentu. Storage publiczny – patrz docstring modułu.
    preview = models.FileField("podgląd", upload_to=preview_upload_to, blank=True, max_length=255)
    #: Czy podgląd zrobił system (z samego plakatu), czy wgrał go człowiek. Podmiana pliku plakatu
    #: przelicza wyłącznie miniaturę **wygenerowaną** – wgranej ręcznie nie ruszamy, bo to była
    #: czyjaś decyzja („pokaż fragment, nie całość”).
    preview_is_generated = models.BooleanField("podgląd wygenerowany", default=False)
    position = models.PositiveIntegerField("kolejność", default=0)
    is_published = models.BooleanField("opublikowany", default=False)
    #: Plakat zdjęty z listy **razem z historią pobrań**. Skasowanie wiersza, który ma pobrania,
    #: zabrałoby statystyki, więc „Usuń” na takim plakacie archiwizuje (patrz widok koordynatora).
    archived_at = models.DateTimeField("zarchiwizowany", null=True, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dodał",
    )

    #: Domyślna ścieżka queryseta (``competition``) – plakat ma własną kolumnę konkursu.
    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "plakat do pobrania"
        verbose_name_plural = "plakaty do pobrania"
        ordering = ("competition", "position", "id")
        indexes = [
            models.Index(fields=["competition", "is_published", "position"], name="promo_material_listing"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_public(self) -> bool:
        """Czy plakat widać na ``/plakaty/`` – opublikowany i nie w archiwum."""
        return self.is_published and not self.is_archived

    @property
    def format_label(self) -> str:
        return FORMATS.get(self.file_format, (b"", "", self.file_format.upper()))[2]

    @property
    def variant_name(self) -> str:
        """Napis przycisku we wspólnej karcie: ``variant_label`` albo – gdy pusty – sam format."""
        return self.variant_label or self.format_label

    @property
    def content_type(self) -> str:
        return FORMATS.get(self.file_format, (b"", "application/octet-stream", ""))[1]

    @property
    def download_name(self) -> str:
        """Nazwa pliku u pobierającego: tytuł bez polskich znaków i spacji + rozszerzenie z treści.

        ``slugify``, bo nazwa z ogonkami bywa zamieniana przez stary program pocztowy albo szkolny
        komputer w krzaczki – a to jest plik, który ktoś wydrukuje i powiesi. ``ł`` zamieniamy
        ręcznie: ``slugify`` zdejmuje znaki diakrytyczne rozkładem Unicode (NFKD), a „ł” nie ma
        rozkładu i znikałoby z nazwy zamiast stać się „l” („Szkoła” → „szkoa”).
        """
        from django.utils.text import slugify

        title = self.title.replace("ł", "l").replace("Ł", "L")
        base = slugify(title)[:80].strip("-") or f"plakat-{self.pk}"
        return f"{base}.{self.file_format}"


#: Jak długo trzymamy pseudonim adresu IP przy zdarzeniu pobrania. Po tym terminie zadanie
#: ``apps.promo.tasks.clear_expired_ip_hashes`` zeruje skrót, a samo zdarzenie (data, plakat) zostaje
#: w liczbie pobrań łącznie. Dwanaście miesięcy obejmuje pełny rok szkolny i porównanie z tym samym
#: miesiącem poprzedniej edycji – dłużej unikalność adresów niczemu już nie służy.
IP_HASH_RETENTION_MONTHS = 12


class PromoDownload(models.Model):
    """Jedno pobranie plakatu. Bez IP, bez przeglądarki, bez konta – patrz docstring modułu."""

    #: ``CASCADE``: plakat z pobraniami nie jest nigdy kasowany (archiwizacja, patrz wyżej), więc
    #: ta reguła zadziała wyłącznie dla plakatu, którego nikt nie pobrał – czyli bez strat.
    material = models.ForeignKey(
        PromoMaterial,
        on_delete=models.CASCADE,
        related_name="downloads",
        verbose_name="plakat",
    )
    #: Konkurs powtórzony przy zdarzeniu (a nie tylko przez plakat), żeby statystyki konkursu
    #: liczyły się jednym filtrem po indeksie, bez złączenia z tabelą plakatów.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="promo_downloads",
        verbose_name="konkurs",
    )
    downloaded_at = models.DateTimeField("pobrano", default=timezone.now, db_index=True)
    #: Pseudonim adresu IP: HMAC-SHA256 z kluczem wyprowadzonym z ``SECRET_KEY`` (64 znaki
    #: szesnastkowe, ``apps.promo.tracking``). ``NULL`` znaczy „nie ma już czym liczyć unikalności” –
    #: skrót wyzerowany po okresie retencji albo pobranie bez rozpoznanego adresu. ``NULL``, a nie
    #: pusty napis, i to jest powód odstępstwa od zwyczaju Django: ``COUNT(DISTINCT …)`` pomija
    #: ``NULL``, a pusty napis policzyłby jako jeden „unikalny adres” wszystkie wyzerowane wiersze.
    ip_hash = models.CharField("pseudonim adresu IP", max_length=64, null=True, blank=True)  # noqa: DJ001

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "pobranie plakatu"
        verbose_name_plural = "pobrania plakatów"
        ordering = ("-downloaded_at", "-id")
        indexes = [
            models.Index(fields=["competition", "downloaded_at"], name="promo_download_stats"),
            models.Index(fields=["material", "downloaded_at"], name="promo_download_material"),
        ]

    def __str__(self) -> str:
        return f"{self.material_id} @ {self.downloaded_at:%Y-%m-%d %H:%M}"
