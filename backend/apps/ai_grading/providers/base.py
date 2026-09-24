"""Wspólny kontrakt dostawców modelu: jedno wejście, jeden wynik, jedna rodzina błędów.

Prośba organizatora z 24.09.2026 („pozwól też na użycie innych dostawców AI”) dokłada do Claude'a
trzech kolejnych dostawców, a reguły serwisu – kolejka, ponowienia, koszt, walidacja odpowiedzi,
wymazywanie danych osobowych – mają zostać **jedne**. Dlatego każdy dostawca robi dokładnie trzy
rzeczy i nic więcej:

- z neutralnego :class:`GradingInput` (prompt systemowy, materiały zadania, praca uczestnika,
  limit odpowiedzi) buduje żądanie w kształcie **swojego** SDK (:meth:`Provider.build_request`
  – czysta funkcja, sprawdzana testem bez sieci),
- wysyła je i sprowadza odpowiedź do :class:`CallResult` w słowniku Anthropic (``end_turn``,
  ``max_tokens``, ``refusal``) – zadanie Celery nie musi wiedzieć, że Gemini mówi ``SAFETY``,
  a OpenAI ``incomplete``,
- tłumaczy wyjątki swojego SDK na :class:`ApiFailure` z **rodzajem** (:class:`FailureKind`), od
  którego zależy ponowienie. Rodzaj, a nie kod HTTP, bo ten sam kod znaczy u różnych dostawców co
  innego: 429 u OpenAI bywa wyczerpanym budżetem (ponawianie nic nie da), a u Anthropic –
  chwilowym limitem.

Walidacja JSON-a odpowiedzi (``prompt.validate_output``) **nie** jest częścią dostawcy, choć
schemat jest wspólny: odpowiedź, która nie pasuje do schematu, i tak została zapłacona, więc
serwis najpierw zapisuje zużycie, a dopiero potem ją sprawdza – w jednym miejscu dla wszystkich.

Import SDK jest wszędzie leniwy (wewnątrz metod): brak pakietu wyłącza jednego dostawcę
(:meth:`Provider.is_available`), a nie cały serwis.
"""

from __future__ import annotations

import enum
import importlib.util
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Usage:
    """Zużycie jednego wywołania. ``input_tokens`` **nie** obejmuje tokenów z cache.

    Dostawcy liczą to różnie (OpenAI i Gemini podają wejście łącznie z tokenami z cache, Anthropic
    osobno) – każdy dostawca sprowadza swoje liczby do tego jednego układu, żeby rachunek kosztu
    w ``services.cost_of`` był jeden.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens


@dataclass(frozen=True)
class CallResult:
    """Odpowiedź modelu. ``text`` ma sens wyłącznie przy ``end_turn``.

    ``stop_reason`` jest w słowniku Anthropic niezależnie od dostawcy: ``end_turn`` (pełna
    odpowiedź), ``max_tokens`` (ucięta na limicie), ``refusal`` (odmowa albo blokada filtra
    bezpieczeństwa – przyczyna w ``refusal_category``).
    """

    stop_reason: str
    text: str
    model: str
    request_id: str
    usage: Usage
    refusal_category: str | None = None


class FailureKind(enum.StrEnum):
    """Rodzaj błędu – od niego, a nie od kodu HTTP, zależy, czy zadanie spróbuje jeszcze raz."""

    AUTH = "auth"  # zły, unieważniony albo pozbawiony uprawnień klucz – bez ponowień
    RATE_LIMIT = "rate_limit"  # chwilowy limit zapytań – ponowienie z opóźnieniem
    TRANSIENT = "transient"  # 5xx, sieć, przekroczony czas – ponowienie
    PERMANENT = "permanent"  # błąd żądania, wyczerpany budżet u dostawcy – bez ponowień
    REFUSAL = "refusal"  # dostawca odrzucił treść (filtr bezpieczeństwa) – bez ponowień
    TOO_LARGE = "too_large"  # żądanie ponad limit dostawcy – bez ponowień

    @property
    def retryable(self) -> bool:
        return self in (FailureKind.RATE_LIMIT, FailureKind.TRANSIENT)


#: Rodzaj błędu wyprowadzany z kodu, gdy dostawca go nie podał (zgodność ze starszymi wywołaniami
#: i testami, które budują ``ApiFailure("rate_limit", …, retryable=True)``).
_KIND_BY_CODE = {
    "auth": FailureKind.AUTH,
    "permission": FailureKind.AUTH,
    "rate_limit": FailureKind.RATE_LIMIT,
    "server": FailureKind.TRANSIENT,
    "connection": FailureKind.TRANSIENT,
    "refusal": FailureKind.REFUSAL,
    "too_large": FailureKind.TOO_LARGE,
}


class ApiFailure(Exception):
    """Wywołanie się nie udało. ``retryable`` rozstrzyga, czy zadanie Celery spróbuje jeszcze raz.

    ``message`` jest **naszym** zdaniem po polsku – nigdy treścią wyjątku SDK, bo ta bywa długa,
    angielska i potrafi zacytować nagłówki albo adres żądania (a w nim – u niektórych dostawców –
    klucz). Nic z wyjątku SDK nie trafia do bazy ani na ekran.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after: int | None = None,
        request_id: str = "",
        kind: FailureKind | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if kind is None:
            kind = _KIND_BY_CODE.get(code)
            if kind is None:
                kind = FailureKind.TRANSIENT if retryable else FailureKind.PERMANENT
        self.kind = kind
        self.retryable = kind.retryable if retryable is None else bool(retryable)
        self.retry_after = retry_after
        self.request_id = request_id or ""


def retry_after_seconds(headers) -> int | None:
    """``retry-after`` z odpowiedzi 429/503, przycięty do godziny. Brak albo śmieci → ``None``."""
    try:
        value = int(float((headers or {}).get("retry-after", "")))
    except (TypeError, ValueError, AttributeError):
        return None
    return max(1, min(value, 3600))


# --- wejście ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GradingInput:
    """Wszystko, czego dostawca potrzebuje do jednej oceny – bez niczego, co identyfikuje autora.

    ``submission`` to surowe bajty pliku, ``submission_mime`` – typ ustalony przez **serwer** przy
    przyjęciu pracy (a nie nazwa pliku od uczestnika, której tu w ogóle nie ma).
    """

    competition_name: str
    materials: object  # prompt.ProblemMaterials – bez importu, żeby base nie zależał od prompt
    submission: bytes
    submission_mime: str
    submission_pages: int | None
    max_tokens: int


class ProviderRequest(dict):
    """Argumenty wywołania SDK (gotowe do ``**``) z doklejoną nazwą dostawcy.

    Słownik, a nie osobna klasa z polem ``payload``: żądanie Anthropic jest dokładnie tym samym
    słownikiem, co przed dodaniem innych dostawców (testy kształtu i fałszywe modele w testach
    czytają ``request["model"]``), a dyspozytor (``client.call_model``) odczytuje dostawcę
    z atrybutu, którego SDK nigdy nie zobaczy.
    """

    provider: str = "anthropic"

    def __init__(self, provider: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.provider = provider


# --- katalog ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """Model z kuratorowanej listy dostawcy. Poza listą koordynator wpisuje „inny identyfikator”."""

    id: str
    label: str
    default: bool = False


@dataclass(frozen=True)
class Limits:
    """Limity jednego żądania u dostawcy, sprawdzane **przed** wysyłką (zamiast obcinania pracy).

    ``max_request_bytes`` liczymy po zakodowaniu base64 (tyle naprawdę jedzie w żądaniu),
    ``max_file_bytes`` – surowy rozmiar jednego pliku, ``max_pdf_pages`` – suma stron wszystkich
    PDF-ów żądania (``None`` = dostawca nie podaje limitu stron). Wszystkie limity są **nasze
    zachowawcze** tam, gdzie dokumentacja dostawcy mówi o plikach, a nie o całym żądaniu.
    """

    max_request_bytes: int
    max_image_bytes: int
    max_pdf_pages: int | None = None
    max_file_bytes: int | None = None
    #: Strony jednego PDF-a, które dostawca naprawdę czyta (Meta: obrazy pierwszych 50 stron).
    max_pages_per_pdf: int | None = None


@dataclass
class Provider:
    """Jeden dostawca. Podklasy wypełniają pola i nadpisują cztery metody sieciowe/budujące."""

    name: str = ""
    label: str = ""
    #: Odbiorca danych w eksporcie uczestnika i w rejestrze czynności – pełna nazwa podmiotu.
    processor: str = ""
    #: Moduł SDK sprawdzany przez :meth:`is_available` (bez importowania go).
    sdk_module: str = ""
    sdk_package: str = ""
    models: tuple[ModelInfo, ...] = ()
    limits: Limits = field(default_factory=lambda: Limits(32 * 1024 * 1024, 5 * 1024 * 1024))
    #: Mnożniki stawki wejścia dla zapisu i odczytu cache promptu (cennik dostawcy).
    cache_write_multiplier: str = "1"
    cache_read_multiplier: str = "1"
    #: Uwaga pokazywana przy dostawcy w ustawieniach (np. ograniczenia formatu plików).
    note: str = ""

    # -- katalog --

    @property
    def default_model(self) -> str:
        for item in self.models:
            if item.default:
                return item.id
        return self.models[0].id if self.models else ""

    def model_label(self, model_id: str) -> str:
        for item in self.models:
            if item.id == model_id:
                return item.label
        return model_id

    def is_available(self) -> bool:
        """Czy SDK jest zainstalowane. ``find_spec`` nie wykonuje modułu – sprawdzenie jest tanie."""
        try:
            return importlib.util.find_spec(self.sdk_module) is not None
        except (ImportError, ValueError):
            return False

    # -- klucz --

    def normalise_key(self, raw: str) -> str:
        """Klucz po zdjęciu białych znaków albo ``crypto.InvalidApiKey`` (komunikat bez cytatu)."""
        from ..crypto import generic_key

        return generic_key(raw, self.label)

    # -- żądanie i wywołanie (podklasy) --

    def build_request(self, model: str, grading: GradingInput) -> ProviderRequest:  # pragma: no cover
        raise NotImplementedError

    def call(self, api_key, request: ProviderRequest) -> CallResult:  # pragma: no cover
        raise NotImplementedError

    def check_key(self, api_key, model: str) -> tuple[bool, str]:  # pragma: no cover
        raise NotImplementedError

    def translate_error(self, exc: Exception) -> ApiFailure:  # pragma: no cover
        raise NotImplementedError

    # -- wspólne --

    def unexpected(self) -> ApiFailure:
        """Wyjątek spoza hierarchii SDK. Do bazy idzie zdanie ogólne, do logu – sama nazwa klasy."""
        return ApiFailure("api", f"Nieoczekiwany błąd klienta API {self.label}.", kind=FailureKind.PERMANENT)
