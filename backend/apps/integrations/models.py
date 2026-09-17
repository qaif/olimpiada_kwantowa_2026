"""Warstwa integracyjna: klucze API organizatora, odbiorcy webhooków i dziennik doręczeń.

Po co osobna aplikacja, skoro API platformy już istnieje. Bo to jest inny **kontrakt** i inny
odbiorca. Dotychczasowe ``/api/…`` obsługuje przeglądarkę i panel: uwierzytelnia się sesją albo
tokenem konta, zwraca to, czego akurat potrzebuje ekran, i wolno mu się zmieniać razem z ekranem.
Tutaj po drugiej stronie stoi **czyjś serwer** – kuratorium, uczelnia, system partnera – który
nie ma konta, nie ma przeglądarki i nie przeżyje zmiany kształtu odpowiedzi w środku edycji.
Stąd osobne poświadczenie (klucz z zakresami zamiast konta z rolą), osobny, wersjonowany prefiks
adresów (``/api/v1/``) i osobny dziennik tego, co wyszło na zewnątrz.

Trzy decyzje, na których stoją te modele:

- **klucza nie da się odczytać po utworzeniu.** W bazie leży wyłącznie ``key_hash`` (SHA-256
  sekretu) i ``prefix`` służący do odnalezienia wiersza. Klucz jest hasłem do danych osobowych
  uczestników – trzymanie go w postaci jawnej znaczyłoby, że jeden zrzut bazy oddaje komplet
  poświadczeń partnerów. Utracony klucz się **unieważnia i wystawia nowy**, a nie odczytuje,
- **zakres jest przy kluczu, nie przy koncie.** Partner dostaje dokładnie tyle, ile wynika
  z umowy (``scopes``), i – gdy klucz jest przypisany do edycji – wyłącznie dane tej edycji.
  Dane osobowe wymagają **dwóch** niezależnych zgód: zakresu ``read:participants_pii``
  **i** flagi ``pii_allowed`` postawionej ręcznie przez koordynatora. Jedno kliknięcie za mało,
  żeby lista imion i nazwisk wyszła z systemu,
- **doręczenie webhooka jest wierszem w bazie, nie efektem ubocznym.** ``WebhookDelivery``
  powstaje w tej samej transakcji, co zdarzenie domenowe, i dopiero po jej zatwierdzeniu idzie
  na kolejkę. Dzięki temu „wysłaliśmy” i „nie wysłaliśmy” są stanem, który da się pokazać
  koordynatorowi i powtórzyć – a nie linijką w logu workera.

Kluczem dziedziny jest **edycja** (``Edition``), a nie „olimpiada”: planowany podział na wiele
konkursów doda ``Competition`` nad edycją, więc klucz API i webhook zapięte na edycji przeżyją
tę zmianę bez migracji danych (dojdzie co najwyżej drugie, szersze dowiązanie).
"""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# --- zakresy uprawnień klucza -------------------------------------------------------------------
# Lista jest **zamknięta**: zakres wpisuje koordynator w panelu, a literówka („read:participant”)
# dawałaby klucz, który nigdy niczego nie otworzy, i nikt by nie wiedział dlaczego.

SCOPE_READ_PARTICIPANTS = "read:participants"
SCOPE_READ_PARTICIPANTS_PII = "read:participants_pii"
SCOPE_READ_RESULTS = "read:results"
SCOPE_READ_SUBMISSIONS_META = "read:submissions_meta"
SCOPE_READ_STATS = "read:stats"
SCOPE_WRITE_EVENTS = "write:events"

#: Zakres → zdanie dla koordynatora. Słownik, a nie ``TextChoices``: wartości niosą dwukropek,
#: więc i tak nie byłyby poprawnymi nazwami atrybutów, a opis jest tu częścią kontraktu – to on
#: stoi przy polu wyboru w panelu i w ``docs/API.md``.
SCOPES: dict[str, str] = {
    SCOPE_READ_PARTICIPANTS: "Lista uczestników etapu bez danych osobowych (kod, szkoła, klasa).",
    SCOPE_READ_PARTICIPANTS_PII: (
        "Imiona, nazwiska i adresy e-mail uczestników. Działa wyłącznie, gdy klucz ma "
        "zaznaczone „dane osobowe dozwolone”."
    ),
    SCOPE_READ_RESULTS: "Ogłoszona tabela wyników etapu (zamrożony snapshot publikacji).",
    SCOPE_READ_SUBMISSIONS_META: "Metadane oddanych prac: kod, zadanie, wersja, stan, data.",
    SCOPE_READ_STATS: "Statystyki zbiorcze ogłoszonych etapów.",
    SCOPE_WRITE_EVENTS: "Dopisywanie wydarzeń do linii czasu edycji.",
}

# --- zdarzenia webhooków ------------------------------------------------------------------------

EVENT_RESULTS_PUBLISHED = "results.published"
EVENT_STAGE_CLOSED = "stage.closed"
EVENT_SUBMISSION_RECEIVED = "submission.received"
EVENT_APPEAL_DECIDED = "appeal.decided"
EVENT_REGISTRATION_CREATED = "registration.created"

#: Zdarzenie → zdanie dla koordynatora. Lista zamknięta z tego samego powodu, co przy zakresach,
#: plus jeden dodatkowy: nazwa zdarzenia jest w kontrakcie po stronie odbiorcy. Dopisanie nowej
#: pozycji jest zmianą API, więc ma być widoczne w jednym miejscu.
WEBHOOK_EVENTS: dict[str, str] = {
    EVENT_RESULTS_PUBLISHED: "Ogłoszono wyniki etapu.",
    EVENT_STAGE_CLOSED: "Etap został zamknięty (upłynął deadline albo zamknął go koordynator).",
    EVENT_SUBMISSION_RECEIVED: "Uczestnik oddał rozwiązanie.",
    EVENT_APPEAL_DECIDED: "Komisja rozstrzygnęła reklamację.",
    EVENT_REGISTRATION_CREATED: "Zarejestrował się nowy uczestnik.",
}

#: Przedrostek klucza w postaci jawnej: ``ok_<prefix>_<sekret>``. „ok” jak Olimpiada Kwantowa –
#: rozpoznawalny początek pozwala skanerom repozytoriów wyłapać klucz wklejony przez pomyłkę
#: do kodu, a człowiekowi odróżnić go od tokenu konta.
TOKEN_PREFIX = "ok"

#: Długość widocznej części klucza. Osiem znaków wystarcza, żeby nazwać klucz w rozmowie
#: („ten zaczynający się na 7f3a…”), a jednocześnie nic nie zdradza: sekret ma własną entropię.
PREFIX_LENGTH = 8

#: Domyślny limit żądań na minutę dla jednego klucza. Sto dwadzieścia to dwa na sekundę – tyle,
#: ile potrzebuje synchronizacja po stronie partnera, i za mało, żeby przepisać bazę pętlą.
DEFAULT_RATE_LIMIT = 120

#: Górna granica limitu, którą wolno wpisać w panelu. Powyżej tej wartości limit przestaje być
#: limitem, a zaczyna być zgodą na wysycenie procesów aplikacji jednym klientem.
MAX_RATE_LIMIT = 6000

#: Po ilu doręczeniach zakończonych porażką **pod rząd** odbiorca jest wygaszany. Dwadzieścia
#: to kilka godzin prób z narastającym odstępem: tyle trwa zwykła awaria po drugiej stronie,
#: a dłuższe dobijanie się do adresu, który nie odpowiada, jest już generowaniem ruchu donikąd.
MAX_CONSECUTIVE_FAILURES = 20


def generate_secret() -> str:
    """Sekret klucza API albo webhooka. 43 znaki base64url z ``secrets`` – 256 bitów entropii."""
    return secrets.token_urlsafe(32)


def generate_prefix() -> str:
    """Widoczna część klucza. Szesnastkowa, żeby dało się ją przeczytać przez telefon."""
    return secrets.token_hex(PREFIX_LENGTH // 2)


def hash_secret(secret: str) -> str:
    """SHA-256 sekretu w zapisie szesnastkowym.

    Bez soli i bez rozciągania klucza – i to jest tu poprawne, a nie oszczędność. Sekret ma 256
    bitów z generatora kryptograficznego, więc nie ma słownika, który by go zgadł; koszt funkcji
    skrótu chroni **hasła ludzi**, czyli sekrety o niskiej entropii. Za to sprawdzenie musi być
    tanie: idzie na każde żądanie API.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def default_scopes() -> list:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return []


def default_events() -> list:
    """To samo dla listy zdarzeń. Osobna nazwa, bo trafia do migracji i ma się w niej czytać."""
    return []


def default_payload() -> dict:
    return {}


def validate_scopes(values) -> list[str]:
    """Sprawdza listę zakresów wobec zamkniętego zbioru i zwraca ją w kolejności kanonicznej.

    Kolejność jest ustalona (jak w ``SCOPES``), a nie „taka, jak przyszła z formularza”: klucz
    z tymi samymi uprawnieniami ma wyglądać tak samo w panelu, w audycie i w eksporcie, inaczej
    porównanie dwóch wpisów audytowych pokazywałoby zmianę tam, gdzie zmieniła się kolejność
    checkboxów.
    """
    if not isinstance(values, list):
        raise ValidationError({"scopes": "Zakresy muszą być listą."})
    unknown = sorted({str(value) for value in values} - set(SCOPES))
    if unknown:
        raise ValidationError({"scopes": f"Nieznane zakresy: {', '.join(unknown)}."})
    chosen = {str(value) for value in values}
    return [scope for scope in SCOPES if scope in chosen]


def validate_events(values) -> list[str]:
    """To samo, co ``validate_scopes``, dla listy zdarzeń webhooka."""
    if not isinstance(values, list):
        raise ValidationError({"events": "Zdarzenia muszą być listą."})
    unknown = sorted({str(value) for value in values} - set(WEBHOOK_EVENTS))
    if unknown:
        raise ValidationError({"events": f"Nieznane zdarzenia: {', '.join(unknown)}."})
    chosen = {str(value) for value in values}
    return [event for event in WEBHOOK_EVENTS if event in chosen]


class ApiKeyQuerySet(models.QuerySet):
    def active(self):
        """Klucze, którymi da się dziś uwierzytelnić."""
        return self.filter(revoked_at__isnull=True)


class ApiKey(models.Model):
    """Poświadczenie serwera partnera: co wolno, w której edycji i do kiedy.

    Klucz **nie jest kontem**: nie ma roli, nie ma sesji i nie zaloguje się do panelu. Dlatego
    ``created_by`` jest wyłącznie odpowiedzią na pytanie „kto go wystawił”, a nie tożsamością,
    w której imieniu działa żądanie – uprawnieniem jest sam zbiór zakresów.
    """

    # Pusta edycja znaczy „wszystkie edycje” – tak wystawia się klucz dla stałego partnera, który
    # ma czytać także przyszłe roczniki. CASCADE, bo klucz zapięty na skasowanej edycji nie ma
    # już czego otwierać, a zostawiony byłby poświadczeniem bez zakresu danych.
    edition = models.ForeignKey(
        "competitions.Edition",
        verbose_name="edycja",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="api_keys",
        help_text="Puste = klucz widzi wszystkie edycje.",
    )
    name = models.CharField("nazwa", max_length=120, help_text="Dla kogo jest ten klucz.")
    prefix = models.CharField("przedrostek", max_length=PREFIX_LENGTH, unique=True, default=generate_prefix)
    key_hash = models.CharField("skrót klucza", max_length=64, editable=False)
    scopes = models.JSONField("zakresy", default=default_scopes, blank=True)
    # Druga, niezależna zgoda na dane osobowe – patrz docstring modułu. Osobne pole, a nie kolejny
    # zakres, właśnie po to, żeby nie dało się jej nadać tym samym ruchem, co reszty uprawnień.
    pii_allowed = models.BooleanField(
        "dane osobowe dozwolone",
        default=False,
        help_text="Warunek konieczny dla zakresu read:participants_pii.",
    )
    rate_limit_per_minute = models.PositiveIntegerField("limit żądań na minutę", default=DEFAULT_RATE_LIMIT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="wystawił",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_api_keys",
    )
    created_at = models.DateTimeField("wystawiony", default=timezone.now)
    # Znacznik ostatniego użycia jest zapisywany **z grubsza** (nie częściej niż raz na minutę,
    # patrz ``apps.integrations.auth``): jego zadaniem jest odpowiedzieć „czy ten klucz jeszcze
    # żyje”, a nie prowadzić dziennik żądań. Zapis przy każdym żądaniu byłby UPDATE-em na gorącym
    # wierszu przy każdym odczycie API.
    last_used_at = models.DateTimeField("ostatnio użyty", null=True, blank=True)
    revoked_at = models.DateTimeField("unieważniony", null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        verbose_name = "klucz API"
        verbose_name_plural = "klucze API"
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rate_limit_per_minute__gte=1, rate_limit_per_minute__lte=MAX_RATE_LIMIT),
                name="integrations_apikey_rate_limit_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def has_scope(self, scope: str) -> bool:
        """Czy klucz ma dany zakres. Zakres PII dodatkowo wymaga zgody ``pii_allowed``.

        Warunek jest **tutaj**, a nie w widoku, bo ma obowiązywać wszędzie tak samo: klucz,
        któremu cofnięto flagę, przestaje widzieć dane osobowe natychmiast, bez odbierania
        zakresu i bez pamiętania o tym w każdym nowym endpoincie.
        """
        if scope == SCOPE_READ_PARTICIPANTS_PII and not self.pii_allowed:
            return False
        return scope in (self.scopes or [])

    def covers_edition(self, edition_id: int | None) -> bool:
        """Czy klucz obejmuje daną edycję. Klucz bez edycji obejmuje wszystkie."""
        return self.edition_id is None or self.edition_id == edition_id

    def clean(self):
        super().clean()
        self.scopes = validate_scopes(self.scopes or [])
        if SCOPE_READ_PARTICIPANTS_PII in self.scopes and not self.pii_allowed:
            raise ValidationError(
                {"pii_allowed": ("Zakres read:participants_pii wymaga zaznaczenia „dane osobowe dozwolone”.")}
            )
        if not (1 <= int(self.rate_limit_per_minute) <= MAX_RATE_LIMIT):
            raise ValidationError(
                {"rate_limit_per_minute": f"Limit musi mieścić się w zakresie 1–{MAX_RATE_LIMIT}."}
            )


class WebhookEndpoint(models.Model):
    """Adres, pod który serwis sam dzwoni, gdy w edycji coś się wydarzy.

    Odwrotność API: tam partner pyta, tutaj my mówimy. Dzięki temu system po drugiej stronie nie
    musi odpytywać nas co minutę, żeby dowiedzieć się o publikacji wyników – a różnica między
    „co minutę” a „gdy się stanie” to przy dwudziestu partnerach dwadzieścia tysięcy żądań dziennie
    zamiast kilkunastu.
    """

    edition = models.ForeignKey(
        "competitions.Edition",
        verbose_name="edycja",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
        help_text="Puste = odbiorca dostaje zdarzenia ze wszystkich edycji.",
    )
    url = models.URLField("adres", max_length=500)
    # Sekret jest **wspólnym** sekretem: my podpisujemy nim ładunek, odbiorca tym samym sprawdza
    # podpis. Nie jest hasłem do niczego u nas, więc – inaczej niż klucz API – musi dać się
    # odczytać w panelu: bez tego odbiorca nie miałby czym zweryfikować podpisu.
    secret = models.CharField("sekret podpisu", max_length=64, default=generate_secret)
    events = models.JSONField("zdarzenia", default=default_events, blank=True)
    is_active = models.BooleanField("aktywny", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="dodał",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_endpoints",
    )
    created_at = models.DateTimeField("dodany", default=timezone.now)
    # Licznik porażek **pod rząd**: udane doręczenie zeruje go. Liczba narastająca od początku
    # świata nie mówiłaby nic o tym, czy odbiorca działa teraz.
    failures = models.PositiveIntegerField("porażki pod rząd", default=0)
    disabled_at = models.DateTimeField("wygaszony", null=True, blank=True)

    class Meta:
        verbose_name = "odbiorca webhooków"
        verbose_name_plural = "odbiorcy webhooków"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.url

    def clean(self):
        super().clean()
        self.events = validate_events(self.events or [])
        scheme = urlsplit(self.url or "").scheme.lower()
        if scheme != "https":
            # Wyłącznie HTTPS, bo ładunek niesie identyfikatory i kody publiczne, a podpis HMAC
            # dowodzi autorstwa, nie poufności: po HTTP każdy po drodze czytałby zdarzenia
            # olimpiady i widział, kto co oddał, zanim wyniki zostaną ogłoszone.
            raise ValidationError({"url": "Adres webhooka musi zaczynać się od „https://”."})


class DeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "w kolejce"
    DELIVERED = "DELIVERED", "doręczone"
    FAILED = "FAILED", "nieudane"


class WebhookDelivery(models.Model):
    """Jedno doręczenie jednego zdarzenia do jednego odbiorcy – razem z tym, czym się skończyło.

    Ładunek jest **kopiowany** do wiersza, a nie odtwarzany z bazy przy ponowieniu. Zdarzenie
    opisuje stan z chwili, w której zaszło; gdyby ponowienie liczyło ładunek od nowa, odbiorca
    dostałby po awarii treść inną niż ta, którą dostali wszyscy pozostali, i nie dałoby się
    później powiedzieć, co właściwie zostało wysłane.
    """

    endpoint = models.ForeignKey(
        WebhookEndpoint, verbose_name="odbiorca", on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField("zdarzenie", max_length=40)
    payload = models.JSONField("ładunek", default=default_payload, blank=True)
    status = models.CharField(
        "stan", max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField("liczba prób", default=0)
    last_error = models.CharField("ostatni błąd", max_length=300, blank=True)
    created_at = models.DateTimeField("utworzone", default=timezone.now, db_index=True)
    delivered_at = models.DateTimeField("doręczone", null=True, blank=True)

    class Meta:
        verbose_name = "doręczenie webhooka"
        verbose_name_plural = "doręczenia webhooków"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["endpoint", "-created_at"], name="integrations_delivery_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event} → {self.endpoint_id} ({self.status})"
