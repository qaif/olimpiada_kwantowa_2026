"""Materiały z warsztatów: nagrania, pliki i odnośniki (prośba organizatora z 24.09.2026).

„Koordynator dostaje możliwość wgrywania materiałów z warsztatów, w tym filmów. Filmy powinny być
możliwe do obejrzenia tylko na stronie po zalogowaniu.” Z tego zdania biorą się dwa modele:

- ``WorkshopMaterial`` – **treść**: film, plik albo odnośnik przypięty do jednego wiersza
  harmonogramu warsztatów, z tytułem, opisem, kolejnością i publikacją,
- ``WorkshopMaterialViewer`` – **pseudonim widza**: „to konto oglądało ten materiał”, wyłącznie
  do liczby unikalnych widzów na ekranie koordynatora. Konta w nim nie ma – patrz niżej.

**Przypięcie do warsztatu idzie kluczem, nie kluczem obcym.** Warsztat nie jest wierszem tabeli,
tylko wierszem bloku ``schedule`` w treści strony „Warsztaty” (``apps.cms.workshops``), a jego
trwałym identyfikatorem jest ``workshop_key`` – ten sam, przy którym zapisuje się obecność
(``cms.WorkshopAttendance``). Cena jest ta sama i opisana przy ``workshop_key``: zmiana tematu albo
daty w harmonogramie tworzy nowy klucz. Obecność wtedy po prostu „odpada” (koordynator odhacza
kolumnę jeszcze raz), ale materiał nie może zniknąć po cichu – ktoś wgrał dwugodzinne nagranie.
Dlatego materiał trzyma obok klucza **migawkę** wiersza (``workshop_topic``, ``workshop_date``)
z chwili przypięcia:

- uczestnik widzi materiał „osierocony” pod tematem i datą z migawki – nagranie nie znika ze
  strony tylko dlatego, że ktoś poprawił literówkę w temacie,
- koordynator widzi na swoim ekranie osobną sekcję „bez warsztatu w harmonogramie” i przepina
  materiał jednym kliknięciem do bieżącego wiersza (z podpowiedzią wiersza o tej samej dacie).

Automatycznego przepinania nie ma świadomie: „ten sam dzień” nie znaczy „te same zajęcia”, gdy
jednego dnia są dwa warsztaty, a zmiana daty bywa przeniesieniem zajęć, które się jeszcze nie
odbyły – nagranie z nich nie może samo przeskoczyć na nowy termin.

**Edycji w modelu nie ma** i to jest decyzja, a nie przeoczenie: klucz zawiera datę zajęć, więc
jest jednoznaczny między rocznikami, a harmonogram (strona „Warsztaty”) należy do konkursu, nie do
edycji. Ten sam wybór zrobiła obecność na warsztatach.

**Plik leży w prywatnym buckecie** (``submissions``, prefiks ``workshop-materials/``) i nigdy nie
ma adresu bez podpisu. Przeglądarka dostaje wyłącznie krótkotrwały adres podpisany przez widok,
który wcześniej sprawdził, czy konto należy do tego konkursu (``apps.workshop_materials.access``).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.cms.workshops import WORKSHOP_KEY_LENGTH
from apps.competitions.scoping import competition_scoped_manager

from .formats import ALL_FORMATS

#: Przełącznik konkursu, za którym stoi cała funkcja (``apps.tenancy.models.FEATURE_DEFAULTS``).
#: Stała, a nie napis powtórzony w każdym widoku – ta sama zasada, co ``apps.forum.models.FORUM_FLAG``.
FEATURE_FLAG = "workshop_materials"


class MaterialKind(models.TextChoices):
    """Trzy rodzaje materiału – po jednym na każdą drogę, którą materiał dociera do widza."""

    VIDEO = "video", "film"
    FILE = "file", "plik"
    LINK = "link", "odnośnik"


class MaterialStatus(models.TextChoices):
    """Cykl życia pliku. Widz widzi wyłącznie ``READY`` – i tylko opublikowane.

    - ``UPLOADING`` – wiersz założony, przeglądarka koordynatora wysyła części do MinIO. Wiersz
      porzucony w tym stanie sprząta zadanie ``cleanup_stale_uploads`` (razem z częściami),
    - ``SCANNING`` – plik (nie film) czeka na werdykt ClamAV-a,
    - ``READY`` – sprawdzony: sygnatura zgodna, a plik także przeskanowany,
    - ``REJECTED`` – skaner znalazł zagrożenie albo plik przekroczył limit skanera; obiektu już
      nie ma w magazynie, wiersz zostaje, żeby koordynator zobaczył, co się stało.
    """

    UPLOADING = "uploading", "wgrywanie"
    SCANNING = "scanning", "sprawdzanie antywirusowe"
    READY = "ready", "gotowy"
    REJECTED = "rejected", "odrzucony"


class WorkshopMaterial(models.Model):
    """Jeden materiał jednego warsztatu: film, plik albo odnośnik."""

    #: ``PROTECT``: skasowanie konkursu razem z materiałami zostawiłoby obiekty w magazynie bez
    #: wiersza, który by o nich pamiętał (ta sama reguła, co ``promo.PromoMaterial``).
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="workshop_materials",
        verbose_name="konkurs",
    )
    workshop_key = models.CharField("warsztat (klucz)", max_length=WORKSHOP_KEY_LENGTH)
    #: Migawka wiersza harmonogramu z chwili przypięcia – patrz docstring modułu.
    workshop_topic = models.CharField("temat warsztatu (migawka)", max_length=300, blank=True)
    workshop_date = models.DateField("data warsztatu (migawka)", null=True, blank=True)
    kind = models.CharField("rodzaj", max_length=10, choices=MaterialKind.choices)
    title = models.CharField("tytuł", max_length=200)
    description = models.TextField("opis", max_length=1000, blank=True)
    #: Wyłącznie dla odnośnika (np. nagranie niepubliczne w serwisie wideo). Tylko ``https``.
    url = models.URLField("adres", max_length=500, blank=True)
    #: Klucz obiektu w prywatnym buckecie. Składa go serwis z konkursu i losowego identyfikatora –
    #: nazwa pliku od przesyłającego nie ma do klucza wstępu (``services.build_object_key``).
    object_key = models.CharField("klucz obiektu", max_length=255, blank=True)
    #: Identyfikator wgrywania wieloczęściowego w MinIO – potrzebny do podpisania kolejnych części,
    #: do złożenia pliku i do porzucenia wgrywania. Pusty po zakończeniu.
    upload_id = models.CharField("identyfikator wgrywania", max_length=255, blank=True)
    #: Format rozpoznany **po treści** w kroku „zakończ wgrywanie” (``apps.workshop_materials.formats``);
    #: przed nim – deklaracja z rozszerzenia, do nadania ``Content-Type`` obiektowi.
    file_format = models.CharField("format", max_length=10, blank=True)
    size_bytes = models.PositiveBigIntegerField("rozmiar (B)", default=0)
    status = models.CharField(
        "stan", max_length=12, choices=MaterialStatus.choices, default=MaterialStatus.UPLOADING
    )
    #: Zdanie dla koordynatora, gdy stan tego wymaga (sygnatura z ClamAV-a, powód odrzucenia).
    status_note = models.CharField("uwaga", max_length=300, blank=True)
    position = models.PositiveIntegerField("kolejność", default=0)
    is_published = models.BooleanField("opublikowany", default=False)
    #: Liczba wyświetleń (otwarcie odtwarzacza, pobranie pliku, przejście pod odnośnik). Zwykły
    #: licznik, bez żadnej informacji o tym, kto – do tego jest ``WorkshopMaterialViewer``.
    view_count = models.PositiveIntegerField("wyświetlenia", default=0)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", auto_now=True)
    ready_at = models.DateTimeField("gotowy od", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dodał",
    )

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "materiał z warsztatów"
        verbose_name_plural = "materiały z warsztatów"
        ordering = ("competition", "workshop_key", "position", "id")
        indexes = [
            models.Index(
                fields=["competition", "workshop_key", "position"], name="workshop_material_listing"
            ),
            models.Index(fields=["status", "created_at"], name="workshop_material_status"),
        ]

    def __str__(self) -> str:
        return self.title

    # --- stan -------------------------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self.status == MaterialStatus.READY

    @property
    def is_visible(self) -> bool:
        """Czy materiał widzi zalogowany czytelnik – opublikowany **i** sprawdzony."""
        return self.is_published and self.is_ready

    @property
    def has_object(self) -> bool:
        return self.kind != MaterialKind.LINK and bool(self.object_key)

    # --- plik ------------------------------------------------------------------------------------

    @property
    def format(self):
        return ALL_FORMATS.get(self.file_format)

    @property
    def format_label(self) -> str:
        fmt = self.format
        return fmt.label if fmt is not None else ""

    @property
    def content_type(self) -> str:
        fmt = self.format
        return fmt.content_type if fmt is not None else "application/octet-stream"

    @property
    def download_name(self) -> str:
        """Nazwa pliku u pobierającego: tytuł bez polskich znaków + rozszerzenie **z treści**.

        Ta sama reguła, co ``PromoMaterial.download_name`` (łącznie z ręczną zamianą „ł”, której
        ``slugify`` nie rozkłada).
        """
        title = self.title.replace("ł", "l").replace("Ł", "L")
        base = slugify(title)[:80].strip("-") or f"material-{self.pk}"
        return f"{base}.{self.file_format or 'bin'}"


class WorkshopMaterialViewer(models.Model):
    """Pseudonim konta, które otworzyło materiał – do liczby **unikalnych** widzów.

    W wierszu nie ma konta ani adresu IP. Jest ``viewer_hash`` = HMAC-SHA256 z kluczem
    wyprowadzonym z ``SECRET_KEY`` nad parą (materiał, konto) – patrz ``apps.workshop_materials.stats``.
    Materiał wchodzi do skrótu celowo: ta sama osoba przy dwóch materiałach ma dwa **różne**
    pseudonimy, więc tabela nie składa się w „historię oglądania” jednej osoby nawet dla kogoś, kto
    ma zrzut bazy. Z kluczem da się sprawdzić „czy konto X oglądało materiał Y” – dlatego to nadal
    jest dana osobowa (pseudonim, art. 4 pkt 5 RODO), z wpisem w rejestrze czynności
    (``apps.accounts.processing_register``) i terminem usunięcia (``VIEWER_RETENTION_MONTHS``).
    """

    material = models.ForeignKey(
        WorkshopMaterial,
        on_delete=models.CASCADE,
        related_name="viewers",
        verbose_name="materiał",
    )
    viewer_hash = models.CharField("pseudonim widza", max_length=64)
    first_seen_at = models.DateTimeField("pierwsze wyświetlenie", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "widz materiału"
        verbose_name_plural = "widzowie materiałów"
        constraints = [
            models.UniqueConstraint(
                fields=["material", "viewer_hash"], name="workshop_material_viewer_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.material_id}:{self.viewer_hash[:8]}"


#: Jak długo trzymamy pseudonim widza. Rok obejmuje cały cykl warsztatów jednej edycji i porównanie
#: z poprzednią; dłużej liczba unikalnych widzów niczemu nie służy. Po tym terminie wiersz jest
#: kasowany (``apps.workshop_materials.tasks.cleanup``), a licznik wyświetleń zostaje.
VIEWER_RETENTION_MONTHS = 12
