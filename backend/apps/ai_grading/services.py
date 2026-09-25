"""Reguły oceny AI: klucze i umowy, zlecenia, kolejka z ogranicznikiem, przebieg oceny i odczyty.

Widoki i zadania Celery wyłącznie orkiestrują – każda decyzja („czy wolno zlecić”, „czy tę pracę
pominąć”, „czy ponowić”, „co zobaczy uczestnik”, „czy ten dostawca może dostać pracę”) stoi tutaj,
w jednym miejscu dla panelu, kolejki i eksportu danych.

**Dostawcy** (prośba organizatora z 24.09.2026: „pozwól też na użycie innych dostawców AI”).
Każda reguła tego modułu jest **niezależna od dostawcy**: żądanie buduje i wysyła dostawca
(``apps.ai_grading.providers``), a on oddaje wynik i błędy w jednym, wspólnym kształcie. Dwie
bramki są za to **per dostawca**: klucz API i potwierdzenie umowy powierzenia (DPA). Bez
potwierdzonej umowy dostawca nie dostaje żadnej pracy uczestnika – dostaje co najwyżej **pracę
testową** koordynatora (``apps.ai_grading.sandbox``), która danych uczestnika nie niesie.

**Kolejka z ogranicznikiem** (:func:`pump`). Prośba mówi „jedno zadanie na pracę”, a serwer
produkcyjny jest słaby: worker Celery ma dwa miejsca i dzieli je ze skanem antywirusowym i pocztą.
Wrzucenie czterystu zadań naraz zajęłoby oba miejsca na godziny (jedno wywołanie modelu trwa
minuty), a skan nowo oddanych prac stałby w kolejce. Dlatego zlecenie **nie** wrzuca zadań do
Celery: oznacza oceny jako ``PENDING``, a :func:`pump` wypuszcza ich tyle, ile mieści
``AI_GRADING_MAX_CONCURRENCY`` (domyślnie jedna). Każda zakończona ocena woła :func:`pump`
jeszcze raz, więc kolejka opróżnia się sama, bez odpytywania i bez zadań krążących w kółko.
Okresowy przebieg beatu (``tasks.pump_ai_assessments``) jest tylko siatką asekuracyjną na
restart workera.

**Idempotencja i koszt.** Kluczem oceny jest trójka (wersja pracy, dostawca, zamówiony model):
podwójne kliknięcie nie płaci dwa razy, bo zlecenie pomija oceny ``PENDING`` i ``RUNNING`` tej
trójki (także przy „wygeneruj ponownie”), a przejście ``PENDING → RUNNING`` jest pojedynczym
``UPDATE … WHERE status = PENDING`` – drugie dostarczenie tego samego zadania nie znajdzie już
czego przejąć. Ocena **innym** modelem to osobny wiersz, a nie nadpisanie – po to, żeby dało się
porównać. Ponowienie po błędzie przejściowym następuje wyłącznie wtedy, gdy API **nie oddało**
odpowiedzi, czyli gdy nie było za co zapłacić; odpowiedź oddana (także odmowa i ucięcie na limicie)
jest liczona i nie jest ponawiana automatycznie.

**Model bez ceny.** Koordynator może wpisać dowolny identyfikator modelu, a cena nieznanego modelu
jest nieznana – szacunek byłby zgadywaniem. Taki przebieg liczy się w tokenach, a koszt jest
„nieznany” (``cost_known = False``). Limit wydatków w USD takich wywołań nie widzi, więc przy
**ustawionym** limicie model bez ceny jest odrzucany (``AI_PRICE_UNKNOWN``) – bezpiecznik, który
da się obejść wpisaniem identyfikatora spoza cennika, nie byłby bezpiecznikiem. Bez limitu model
bez ceny działa, a ekran ostrzega, że liczone są wyłącznie tokeny.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit
from apps.core.points import points_json
from apps.submissions.models import AvStatus, Submission, SubmissionFile, SubmissionStatus

from . import prompt, providers
from .catalog import DEFAULT_PRICES, Price
from .crypto import InvalidApiKey, decrypt_key, encrypt_key, last4
from .models import (
    AI_GRADING_FLAG,
    MODEL_ID_MAX_LENGTH,
    AiAssessment,
    AiAssessmentStatus,
    AiGradingSettings,
    AiProvider,
    AiProviderAccount,
    AiStageVisibility,
)
from .providers import call_model, check_key
from .providers.base import ApiFailure, CallResult, GradingInput, Usage

logger = logging.getLogger(__name__)

#: Przestrzeń blokad doradczych Postgresa dla tej aplikacji (1005 – przydział recenzentów,
#: 1007 – zakładanie konkursu). Numer z zapasem, żeby nie zderzyć się z przestrzeniami
#: dokładanymi równolegle w innych modułach.
ADVISORY_LOCK_NAMESPACE_AI = 1042
#: Druga część klucza blokady kolejki – jedna kolejka na instalację (worker jest jeden).
PUMP_LOCK_KEY = 0

#: Odbiorca danych z v0.34.0 (Anthropic). Pozostali dostawcy mają własne brzmienie
#: w ``providers.<nazwa>.processor`` – eksport i rejestr czytają je przez :func:`processor_name`.
PROCESSOR_NAME = "Anthropic PBC (dostawca modelu Claude) – podmiot przetwarzający"

#: Szacunek tokenów na stronę PDF-a (tekst strony plus jej obraz) i na jedno zdjęcie. Rząd
#: wielkości z dokumentacji API; na ekranie stoi wprost, że to szacunek.
TOKENS_PER_PDF_PAGE = 2500
TOKENS_PER_IMAGE = 1600
BYTES_PER_PDF_PAGE_GUESS = 60_000
#: Stała część żądania poza plikami: prompt systemowy, skala, rubryka, znaczniki.
TOKENS_FIXED_OVERHEAD = 2000
#: Myślenie adaptacyjne na ``effort: high`` plus JSON z uzasadnieniem – typowo kilka tysięcy.
TOKENS_OUTPUT_ESTIMATE = 8000

#: Dopuszczalny identyfikator modelu: litery, cyfry i znaki spotykane w identyfikatorach
#: dostawców (``gpt-6-sol``, ``gemini-3.1-pro-preview``, ``ft:gpt-…:org``). Bez spacji i ukośnika
#: wstecznego – identyfikator trafia do adresu żądania u części dostawców.
MODEL_ID_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._:@/-]{{0,{MODEL_ID_MAX_LENGTH - 1}}}$")
#: Przyrostek warstwy Meta Model API, w której Meta może uczyć modele na przesłanych danych.
META_CONTRIBUTOR_SUFFIX = "-contributor"


def _conflict(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_409_CONFLICT)


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def _lock(key: int) -> None:
    """Blokada doradcza na czas transakcji. Na bazie innej niż Postgres – nic (testy jednostkowe)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [ADVISORY_LOCK_NAMESPACE_AI, int(key)])


def _user(actor):
    return actor if getattr(actor, "is_authenticated", False) else None


# --- przełącznik, dostawcy i ustawienia -----------------------------------------------------------


def is_enabled(competition) -> bool:
    """Czy konkurs ma włączoną ocenę AI. Bez zapytania – ``has_feature`` czyta pole wiersza."""
    return competition is not None and competition.has_feature(AI_GRADING_FLAG)


def settings_for(competition) -> AiGradingSettings:
    row, _ = AiGradingSettings.objects.get_or_create(competition=competition)
    return row


def provider_label(name: str) -> str:
    return AiProvider(name).label if name in AiProvider.values else name


def provider_short(name: str) -> str:
    """Krótka nazwa dostawcy („Anthropic”, „OpenAI”…) – do nagłówka panelu obok identyfikatora modelu."""
    try:
        return providers.get(name).label
    except KeyError:
        return name


def processor_name(name: str) -> str:
    """Pełna nazwa podmiotu przetwarzającego – do eksportu danych uczestnika i do rejestru."""
    try:
        return providers.get(name).processor
    except KeyError:
        return f"{name} – podmiot przetwarzający"


def _provider(name: str):
    if name not in AiProvider.values:
        raise _bad_request("Nieznany dostawca modelu.", "AI_PROVIDER_INVALID")
    return providers.get(name)


def clean_model_id(provider_name: str, model: str) -> str:
    """Identyfikator modelu po sprawdzeniu kształtu – albo ``DomainError``.

    Sprawdzamy **kształt**, nie istnienie: o tym, czy model jest dostępny dla klucza, rozstrzyga
    dostawca („Sprawdź klucz” albo błąd przy ocenie). Warstwa ``-contributor`` Mety jest
    odrzucana wprost – pozwala dostawcy uczyć modele na danych, a to wyklucza prace uczestników.
    """
    value = (model or "").strip()
    if not MODEL_ID_RE.fullmatch(value):
        raise _bad_request(
            "Identyfikator modelu może zawierać wyłącznie litery, cyfry i znaki . _ : @ / - "
            f"(najwyżej {MODEL_ID_MAX_LENGTH} znaków).",
            "AI_MODEL_INVALID",
        )
    if provider_name == AiProvider.META and value.endswith(META_CONTRIBUTOR_SUFFIX):
        raise _bad_request(
            "Modele Meta z przyrostkiem „-contributor” pozwalają Mecie używać przesłanych danych do "
            "ulepszania produktów – do prac uczestników nie wolno ich używać.",
            "AI_MODEL_FORBIDDEN",
        )
    return value


def provider_for_model(model: str) -> str | None:
    """Dostawca modelu z kuratorowanej listy (identyfikatory są rozłączne) albo ``None``."""
    for provider in providers.all_providers():
        if any(item.id == model for item in provider.models):
            return provider.name
    return None


def account_for(competition, provider_name: str) -> AiProviderAccount:
    _provider(provider_name)
    row, _ = AiProviderAccount.objects.get_or_create(competition=competition, provider=provider_name)
    return row


def accounts_for(competition) -> dict[str, AiProviderAccount]:
    """Konta wszystkich dostawców – brakujące jako niezapisane wiersze (jedno zapytanie)."""
    existing = {
        row.provider: row
        for row in AiProviderAccount.objects.filter(competition=competition).select_related(
            "api_key_set_by", "dpa_confirmed_by"
        )
    }
    return {
        name: existing.get(name) or AiProviderAccount(competition=competition, provider=name)
        for name in providers.PROVIDER_NAMES
    }


@dataclass
class ProviderState:
    """Stan jednego dostawcy na ekranie ustawień i w bramkach zlecenia."""

    provider: object
    account: AiProviderAccount
    available: bool

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def label(self) -> str:
        return provider_label(self.provider.name)

    @property
    def usable_for_tests(self) -> bool:
        return self.available and self.account.has_key

    @property
    def usable_for_submissions(self) -> bool:
        return self.usable_for_tests and self.account.dpa_confirmed


def provider_states(competition) -> list[ProviderState]:
    accounts = accounts_for(competition)
    return [
        ProviderState(provider=item, account=accounts[item.name], available=item.is_available())
        for item in providers.all_providers()
    ]


@sensitive_variables("raw", "value")
def set_api_key(
    competition, raw: str, *, actor, request=None, provider: str = "anthropic"
) -> AiProviderAccount:
    """Zapisuje (albo zastępuje) klucz dostawcy. W audycie – wyłącznie fakt, nigdy wartość ani końcówka.

    ``sensitive_variables``: gdyby zapis padł (baza, blokada), raport błędu Django – strona
    ``DEBUG`` albo list do ``ADMINS`` – wypisuje zmienne lokalne ramek stosu. Tutaj jedyne dwie
    z jawnym kluczem są zasłonięte gwiazdkami.
    """
    item = _provider(provider)
    try:
        value = item.normalise_key(raw)
    except InvalidApiKey as exc:
        raise _bad_request(str(exc), "AI_KEY_INVALID") from None
    row = account_for(competition, provider)
    replaced = row.has_key
    row.api_key_encrypted = encrypt_key(value)
    row.api_key_last4 = last4(value)
    row.api_key_set_at = timezone.now()
    row.api_key_set_by = _user(actor)
    row.api_key_checked_at = None
    row.api_key_check_ok = None
    row.api_key_check_message = ""
    row.save()
    audit(actor, "ai_grading.key_set", row, {"replaced": replaced, "provider": provider}, request=request)
    return row


def remove_api_key(competition, *, actor, request=None, provider: str = "anthropic") -> AiProviderAccount:
    row = account_for(competition, provider)
    if not row.has_key:
        raise _conflict("Klucz API nie jest ustawiony.", "AI_KEY_MISSING")
    row.api_key_encrypted = ""
    row.api_key_last4 = ""
    row.api_key_set_at = None
    row.api_key_set_by = None
    row.api_key_checked_at = None
    row.api_key_check_ok = None
    row.api_key_check_message = ""
    row.save()
    audit(actor, "ai_grading.key_removed", row, {"provider": provider}, request=request)
    return row


def check_api_key(competition, *, actor, request=None, provider: str = "anthropic") -> tuple[bool, str]:
    """„Sprawdź klucz” – wynik zapisany przy koncie dostawcy i pokazany na ekranie.

    Sprawdzamy model **domyślny**, gdy dostawca jest domyślny, a inaczej domyślny model dostawcy:
    pytanie „czy klucz widzi model, którego użyję” ma sens tylko dla modelu, którego się użyje.
    """
    item = _provider(provider)
    row = account_for(competition, provider)
    options = settings_for(competition)
    model = options.model if options.provider == provider else item.default_model
    key = decrypt_key(row.api_key_encrypted)
    if not item.is_available():
        ok, message = False, f"Pakiet SDK „{item.sdk_package}” nie jest zainstalowany na serwerze."
    elif key is None:
        ok, message = (
            False,
            (
                "Klucz nie jest ustawiony."
                if not row.has_key
                else "Klucza nie da się odczytać – wpisz go ponownie."
            ),
        )
    else:
        ok, message = check_key(key, model, provider)
    row.api_key_checked_at = timezone.now()
    row.api_key_check_ok = ok
    row.api_key_check_message = message[:300]
    row.save(update_fields=["api_key_checked_at", "api_key_check_ok", "api_key_check_message"])
    audit(actor, "ai_grading.key_checked", row, {"ok": ok, "provider": provider}, request=request)
    return ok, message


def set_dpa_confirmation(
    competition,
    provider: str,
    confirmed: bool,
    *,
    actor,
    note: str = "",
    via: str = "panel",
    info_version: str = "",
    request=None,
) -> tuple[AiProviderAccount, bool]:
    """Potwierdza (albo wycofuje) zawarcie umowy powierzenia z dostawcą. Zwraca (konto, czy zmiana).

    Zwykła droga to panel: koordynator potwierdza osobiście, po zapoznaniu się z informacją
    o dostawcy (``disclosures``), a ``info_version`` – skrót tej informacji – idzie do wiersza i do
    dziennika zdarzeń. Komenda operatora (``confirm_ai_provider_dpa``, ``via="command"``) zostaje
    na sytuacje wyjątkowe i zapisuje pustą wersję: operator informacji nie widział.

    Idempotentne: ponowne potwierdzenie już potwierdzonej umowy nie zmienia daty ani osoby
    i nie dokłada wpisu w dzienniku – data potwierdzenia ma mówić, **kiedy** organizator to
    oświadczył po raz pierwszy, a nie kiedy ktoś ostatnio kliknął.
    """
    _provider(provider)
    row = account_for(competition, provider)
    note = (note or "").strip()[:300]
    if confirmed == row.dpa_confirmed:
        return row, False
    if confirmed:
        row.dpa_confirmed_at = timezone.now()
        row.dpa_confirmed_by = _user(actor)
        row.dpa_note = note
        row.dpa_info_version = info_version[:40]
        action = "ai_grading.dpa_confirmed"
    else:
        row.dpa_confirmed_at = None
        row.dpa_confirmed_by = None
        row.dpa_note = ""
        row.dpa_info_version = ""
        action = "ai_grading.dpa_revoked"
    row.save(update_fields=["dpa_confirmed_at", "dpa_confirmed_by", "dpa_note", "dpa_info_version"])
    diff = {"provider": provider, "note": note, "via": via}
    if confirmed:
        diff["info_version"] = info_version or None
    audit(actor, action, row, diff, request=request)
    return row, True


def update_options(
    competition, *, model: str, spending_limit_usd, actor, request=None, provider: str | None = None
) -> AiGradingSettings:
    """Dostawca i model domyślne oraz limit wydatków.

    Bez ``provider`` dostawcę bierzemy z kuratorowanej listy (identyfikatory się nie powtarzają),
    a model spoza listy zostaje przy dotychczasowym dostawcy domyślnym.
    """
    row = settings_for(competition)
    provider = provider or provider_for_model(model) or row.provider
    _provider(provider)
    model = clean_model_id(provider, model)
    if spending_limit_usd is not None and spending_limit_usd < 0:
        raise _bad_request("Limit wydatków nie może być ujemny.", "AI_LIMIT_INVALID")
    diff = {}
    if row.provider != provider:
        diff["provider"] = [row.provider, provider]
    if row.model != model:
        diff["model"] = [row.model, model]
    if row.spending_limit_usd != spending_limit_usd:
        diff["spending_limit_usd"] = [
            str(row.spending_limit_usd) if row.spending_limit_usd is not None else None,
            str(spending_limit_usd) if spending_limit_usd is not None else None,
        ]
    row.provider = provider
    row.model = model
    row.spending_limit_usd = spending_limit_usd
    row.updated_at = timezone.now()
    row.save()
    if diff:
        audit(actor, "ai_grading.options_updated", row, diff, request=request)
    return row


def set_stage_visibility(stage, show: bool, *, actor, request=None) -> AiStageVisibility:
    """„Pokaż uczestnikom ocenę AI” dla jednego etapu. Ślad w audycie przy każdej zmianie."""
    row, _ = AiStageVisibility.objects.get_or_create(stage=stage)
    if row.show_to_participants != show:
        row.show_to_participants = show
        row.changed_at = timezone.now()
        row.changed_by = _user(actor)
        row.save()
        audit(actor, "ai_grading.visibility_changed", stage, {"show_to_participants": show}, request=request)
    return row


def visible_stage_ids(stages) -> set[int]:
    return set(
        AiStageVisibility.objects.filter(stage__in=stages, show_to_participants=True).values_list(
            "stage_id", flat=True
        )
    )


def register_recipients(competition) -> list[str]:
    """Odbiorcy do wiersza „ocena AI” rejestru czynności: dostawcy z kluczem **i** potwierdzoną umową.

    Rejestr opisuje przetwarzanie, które naprawdę może zajść: dostawca bez umowy nie dostaje prac
    uczestników (bramka w :func:`assert_can_request`), więc nie jest ich odbiorcą.
    """
    rows = AiProviderAccount.objects.filter(competition=competition, dpa_confirmed_at__isnull=False).exclude(
        api_key_encrypted=""
    )
    confirmed = {row.provider for row in rows}
    return [processor_name(name) for name in providers.PROVIDER_NAMES if name in confirmed]


# --- ceny i koszt ---------------------------------------------------------------------------------


def _price_key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def price_for(provider: str, model: str, row: AiGradingSettings | None = None) -> Price | None:
    """Stawka modelu: nadpisana przez koordynatora, a bez tego – z cennika domyślnego."""
    if row is not None:
        override = (row.price_overrides or {}).get(_price_key(provider, model))
        if isinstance(override, list | tuple) and len(override) == 2:
            try:
                return Price(Decimal(str(override[0])), Decimal(str(override[1])))
            except InvalidOperation, ValueError:
                pass
    return DEFAULT_PRICES.get((provider, model))


def price_table(row: AiGradingSettings) -> list[dict]:
    """Tabela cen na ekran: modele z listy każdego dostawcy plus modele spoza listy z własną ceną."""
    rows = []
    seen = set()
    for provider in providers.all_providers():
        for item in provider.models:
            seen.add((provider.name, item.id))
            rows.append(_price_row(row, provider.name, item.id, custom=False))
        for key in sorted(row.price_overrides or {}):
            name, _, model = key.partition("/")
            if name == provider.name and (name, model) not in seen:
                seen.add((name, model))
                rows.append(_price_row(row, name, model, custom=True))
    return rows


def _price_row(row, provider: str, model: str, *, custom: bool) -> dict:
    price = price_for(provider, model, row)
    default = DEFAULT_PRICES.get((provider, model))
    return {
        "provider": provider,
        "provider_label": provider_label(provider),
        "model": model,
        "field": _price_key(provider, model),
        "input": price.input if price else None,
        "output": price.output if price else None,
        "is_default": price is not None and price == default,
        "custom": custom,
    }


def set_prices(competition, entries: dict[str, tuple | None], *, actor, request=None) -> AiGradingSettings:
    """Zapisuje ceny: ``{"dostawca/model": (wejście, wyjście)}``; ``None`` zdejmuje nadpisanie.

    Cena równa domyślnej nie jest zapisywana jako nadpisanie – inaczej poprawka cennika
    w następnym wydaniu nie dotarłaby do konkursu, który „zapisał” stare ceny jednym kliknięciem.
    """
    row = settings_for(competition)
    overrides = dict(row.price_overrides or {})
    changed = []
    for key, value in entries.items():
        provider, _, model = key.partition("/")
        _provider(provider)
        clean_model_id(provider, model)
        default = DEFAULT_PRICES.get((provider, model))
        if value is None:
            if key in overrides:
                overrides.pop(key)
                changed.append(key)
            continue
        price_in, price_out = (Decimal(str(value[0])), Decimal(str(value[1])))
        if price_in < 0 or price_out < 0:
            raise _bad_request("Cena nie może być ujemna.", "AI_PRICE_INVALID")
        if default is not None and default == Price(price_in, price_out):
            if key in overrides:
                overrides.pop(key)
                changed.append(key)
            continue
        new = [str(price_in), str(price_out)]
        if overrides.get(key) != new:
            overrides[key] = new
            changed.append(key)
    if changed:
        row.price_overrides = overrides
        row.updated_at = timezone.now()
        row.save(update_fields=["price_overrides", "updated_at"])
        audit(actor, "ai_grading.prices_updated", row, {"models": sorted(changed)}, request=request)
    return row


def cost_of(
    usage: Usage,
    *,
    served_model: str,
    requested_model: str,
    provider: str = "anthropic",
    prices: AiGradingSettings | None = None,
) -> Decimal | None:
    """Szacowany koszt jednego wywołania w USD albo ``None``, gdy cena modelu jest nieznana.

    ``input_tokens`` nie obejmuje tokenów z cache (tak normalizuje go każdy dostawca). Model,
    który **odpowiedział**, bywa inny niż zamówiony (przełączenie ``fallbacks`` u Anthropic, wersja
    modelu u Google) – bez ceny dla niego liczymy stawkami modelu zamówionego: szacunek ma być
    zachowawczy, a nie pusty.
    """
    price = price_for(provider, served_model, prices) or price_for(provider, requested_model, prices)
    if price is None:
        return None
    try:
        item = providers.get(provider)
        write, read = Decimal(item.cache_write_multiplier), Decimal(item.cache_read_multiplier)
    except KeyError:
        write, read = Decimal("1"), Decimal("1")
    million = Decimal(1_000_000)
    total = (
        Decimal(usage.input_tokens) * price.input
        + Decimal(usage.cache_write_tokens) * price.input * write
        + Decimal(usage.cache_read_tokens) * price.input * read
        + Decimal(usage.output_tokens) * price.output
    ) / million
    return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _record_usage(
    assessment_id: int, competition, result: CallResult, requested_model: str, provider: str = "anthropic"
) -> Decimal | None:
    """Dopisuje zużycie do oceny i do liczników konkursu – wyrażeniami ``F``, bez wyścigu.

    Tokeny liczą się **zawsze**; koszt – wtedy, gdy model ma cenę. Wywołanie bez ceny podbija
    licznik ``total_unpriced_calls`` i zdejmuje z oceny ``cost_known`` – ekran pisze wtedy
    „koszt nieznany” zamiast udawać, że było za darmo.
    """
    usage = result.usage
    row = settings_for(competition)
    cost = cost_of(
        usage, served_model=result.model, requested_model=requested_model, provider=provider, prices=row
    )
    assessment_fields = {
        "input_tokens": F("input_tokens") + usage.input_tokens,
        "output_tokens": F("output_tokens") + usage.output_tokens,
        "cache_write_tokens": F("cache_write_tokens") + usage.cache_write_tokens,
        "cache_read_tokens": F("cache_read_tokens") + usage.cache_read_tokens,
    }
    settings_fields = {
        "total_calls": F("total_calls") + 1,
        "total_input_tokens": F("total_input_tokens") + usage.input_tokens,
        "total_output_tokens": F("total_output_tokens") + usage.output_tokens,
        "total_cache_write_tokens": F("total_cache_write_tokens") + usage.cache_write_tokens,
        "total_cache_read_tokens": F("total_cache_read_tokens") + usage.cache_read_tokens,
    }
    if cost is None:
        assessment_fields["cost_known"] = False
        settings_fields["total_unpriced_calls"] = F("total_unpriced_calls") + 1
    else:
        assessment_fields["cost_usd"] = F("cost_usd") + cost
        settings_fields["total_cost_usd"] = F("total_cost_usd") + cost
    AiAssessment.objects.filter(pk=assessment_id).update(**assessment_fields)
    AiGradingSettings.objects.filter(competition=competition).update(**settings_fields)
    return cost


# --- materiały ----------------------------------------------------------------------------------


def scale_values(problem) -> list[int]:
    """Wartości skali w postaci **do pokazania** (z minusem, gdy skala go ma) – jak u recenzenta."""
    from apps.grading.services import scale_items

    return sorted(item["value"] for item in scale_items(problem.stage, problem))


def max_points_for(problem) -> Decimal | None:
    """Maksimum zadania w postaci do pokazania – z reguły oceny (``competitions.scoring``).

    Ta sama liczba, którą recenzent widzi przy polu punktów: najwyższa wartość skali zadania albo
    etapu, a w etapie z dowolnymi wartościami także samo maksimum zadania (12,5). Wcześniej
    funkcja składała ją sama z pól skali – i zadanie z samym maksimum dostałoby maksimum etapu.
    """
    from apps.competitions.scoring import problem_maximum

    return problem_maximum(problem.stage, problem)


def _rule(problem):
    from apps.competitions.scoring import safe_score_rule

    return safe_score_rule(problem.stage, problem)


def _read_field(field_file) -> bytes:
    """Plik zadania z prywatnego storage'u. Brak pliku = puste bajty (materiał opcjonalny)."""
    if not field_file:
        return b""
    try:
        field_file.open("rb")
        try:
            return field_file.read()
        finally:
            field_file.close()
    except Exception as exc:  # noqa: BLE001 - storage niedostępny to błąd materiałów, nie 500
        raise prompt.MaterialError(
            "storage", "Nie udało się odczytać materiałów zadania ze storage'u – spróbuj ponownie."
        ) from exc


def problem_materials(problem) -> prompt.ProblemMaterials:
    from apps.grading.rubric import criteria_for
    from apps.grading.services import scale_items

    maximum = max_points_for(problem)
    if maximum is None or maximum <= 0:
        raise prompt.MaterialError("no_scale", "Zadanie nie ma skali punktacji – nie ma czego proponować.")
    rule = _rule(problem)
    return prompt.ProblemMaterials(
        number=problem.number,
        title=problem.title,
        statement_pdf=_read_field(problem.statement_pdf),
        model_solution_pdf=_read_field(problem.model_solution_pdf),
        reviewer_notes=problem.reviewer_notes or "",
        scale_items=scale_items(problem.stage, problem),
        max_points=maximum,
        rubric=[
            {"title": item.title, "description": item.description, "max_points": item.max_points}
            for item in criteria_for(problem)
        ],
        free_values=bool(rule is not None and rule.free),
    )


def _clean_file(submission: Submission) -> SubmissionFile | None:
    submission_file = submission.latest_file
    if submission_file is None or submission_file.av_status != AvStatus.CLEAN:
        return None
    return submission_file


def read_object(object_key: str) -> bytes:
    from apps.submissions.storage import get_submission_storage

    try:
        stream = get_submission_storage().open(object_key)
    except Exception as exc:  # noqa: BLE001 - brak obiektu / storage niedostępny
        raise prompt.MaterialError("storage", "Nie udało się odczytać pliku pracy ze storage'u.") from exc
    try:
        return stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def _read_submission(submission_file: SubmissionFile) -> bytes:
    return read_object(submission_file.object_key)


# --- cel zlecenia: dostawca i model ---------------------------------------------------------------


def target_value(provider: str, model: str) -> str:
    """Wartość pola wyboru „dostawca i model” – ``dostawca:model`` (dostawca nie ma dwukropka)."""
    return f"{provider}:{model}"


def parse_target(value: str, custom_model: str = "") -> tuple[str, str]:
    """``dostawca:model`` z formularza, z opcjonalnym „innym identyfikatorem” dla tego dostawcy."""
    provider, _, model = (value or "").partition(":")
    _provider(provider)
    if (custom_model or "").strip():
        model = custom_model
    return provider, clean_model_id(provider, model)


def resolve_target(row: AiGradingSettings, provider: str | None, model: str | None) -> tuple[str, str]:
    """Dostawca i model zlecenia: podane wprost albo domyślne z ustawień."""
    provider = provider or row.provider
    item = _provider(provider)
    if not model:
        model = row.model if provider == row.provider else item.default_model
    return provider, clean_model_id(provider, model)


def targets(competition, *, for_tests: bool) -> list[dict]:
    """Pozycje wyboru „dostawca i model”, pogrupowane po dostawcy.

    Do prac uczestników – wyłącznie dostawcy z kluczem **i** potwierdzoną umową powierzenia; do
    pracy testowej – każdy dostawca z kluczem (bramka DPA prac testowych nie dotyczy).
    """
    row = settings_for(competition)
    groups = []
    for state in provider_states(competition):
        usable = state.usable_for_tests if for_tests else state.usable_for_submissions
        if not usable:
            continue
        models = [(item.id, item.label) for item in state.provider.models]
        if row.provider == state.name and row.model not in {model for model, _ in models}:
            models.insert(0, (row.model, f"{row.model} (inny identyfikator)"))
        groups.append(
            {
                "provider": state.name,
                "label": state.label,
                "options": [
                    {
                        "value": target_value(state.name, model),
                        "label": label,
                        "selected": state.name == row.provider and model == row.model,
                        "price_known": price_for(state.name, model, row) is not None,
                    }
                    for model, label in models
                ],
            }
        )
    return groups


def default_target(competition, *, for_tests: bool = False) -> tuple[str | None, str | None]:
    """Cel wstępnie zaznaczony: domyślny z ustawień, a gdy ten jest niedostępny – pierwszy dostępny.

    Dostawca domyślny bez potwierdzonej umowy nie może zablokować zleceń innym, gotowym dostawcą;
    brak jakiegokolwiek dostępnego celu to ``(None, None)`` – bramka zlecenia powie dlaczego.
    """
    groups = targets(competition, for_tests=for_tests)
    options = [option for group in groups for option in group["options"]]
    chosen = next((option for option in options if option["selected"]), options[0] if options else None)
    if chosen is None:
        return None, None
    provider, _, model = chosen["value"].partition(":")
    return provider, model


# --- plan i szacunek ----------------------------------------------------------------------------


def latest_versions(problem) -> list[Submission]:
    """Najnowsza nieodrzucona wersja pracy każdego uczestnika w zadaniu (ta, którą się ocenia)."""
    rows = (
        Submission.objects.filter(problem=problem)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("entry", "entry__participant")
        .prefetch_related(Prefetch("files", queryset=SubmissionFile.objects.order_by("-id")))
        .order_by("entry_id", "-version", "-id")
    )
    latest: dict[int, Submission] = {}
    for submission in rows:
        latest.setdefault(submission.entry_id, submission)
    return sorted(latest.values(), key=lambda item: item.pk)


@dataclass
class Plan:
    """Co zrobi zlecenie – pokazywane koordynatorowi **przed** potwierdzeniem."""

    problem: object
    model: str
    regenerate: bool
    provider: str = "anthropic"
    to_generate: list[Submission] = field(default_factory=list)
    skipped_done: int = 0
    skipped_in_progress: int = 0
    without_file: int = 0
    #: ``None`` – model bez ceny, szacunku w USD nie ma (ekran mówi „koszt nieznany”).
    estimate_usd: Decimal | None = Decimal("0")
    estimate_tokens: int = 0
    has_model_solution: bool = False
    has_statement: bool = False

    @property
    def count(self) -> int:
        return len(self.to_generate)

    @property
    def provider_label(self) -> str:
        return provider_label(self.provider)

    @property
    def target(self) -> str:
        return target_value(self.provider, self.model)


def _file_tokens(submission_file) -> int:
    mime = (submission_file.mime or "").lower()
    if mime == prompt.PDF_MIME:
        pages = submission_file.page_count or max(1, submission_file.size_bytes // BYTES_PER_PDF_PAGE_GUESS)
        return pages * TOKENS_PER_PDF_PAGE
    if mime in prompt.IMAGE_MIMES:
        return TOKENS_PER_IMAGE
    return max(1, submission_file.size_bytes // 3)


def _problem_tokens(problem) -> int:
    total = TOKENS_FIXED_OVERHEAD
    for field_file in (problem.statement_pdf, problem.model_solution_pdf):
        if not field_file:
            continue
        try:
            pages = prompt.pdf_pages(_read_field(field_file))
        except prompt.MaterialError:
            pages = None
        total += (pages or 2) * TOKENS_PER_PDF_PAGE
    return total


def estimate_tokens(problem, files: list) -> int:
    """Zgrubna liczba tokenów serii – pokazywana zawsze, także gdy ceny modelu nie znamy."""
    if not files:
        return 0
    stable = _problem_tokens(problem)
    return sum(stable + _file_tokens(item) + 300 + TOKENS_OUTPUT_ESTIMATE for item in files)


def estimate_cost(
    problem, files: list, model: str, *, provider: str = "anthropic", row: AiGradingSettings | None = None
) -> Decimal | None:
    """Zgrubny szacunek w USD dla serii prac jednego zadania (``None`` – cena modelu nieznana).

    Pierwsza praca płaci za zapis materiałów zadania do cache, kolejne – za odczyt, bo kolejka
    puszcza prace po kolei, w odstępach krótszych niż czas życia cache. Mnożniki cache są
    dostawcy (Anthropic: 1,25 i 0,1; OpenAI: 1,25 i 0,1; Google: 1 i 0,1; Meta: 1 i 0,12).
    """
    if not files:
        return Decimal("0")
    price = price_for(provider, model, row)
    if price is None:
        return None
    item = providers.get(provider)
    write, read = Decimal(item.cache_write_multiplier), Decimal(item.cache_read_multiplier)
    stable = Decimal(_problem_tokens(problem))
    million = Decimal(1_000_000)
    total = Decimal("0")
    for index, submission_file in enumerate(files):
        multiplier = write if index == 0 else read
        total += stable * price.input * multiplier
        total += Decimal(_file_tokens(submission_file) + 300) * price.input
        total += Decimal(TOKENS_OUTPUT_ESTIMATE) * price.output
    return (total / million).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def plan_generation(
    problem,
    *,
    regenerate: bool,
    submission_ids: list[int] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> Plan:
    competition = problem.stage.edition.competition
    row = settings_for(competition)
    provider, model = resolve_target(row, provider, model)
    plan = Plan(
        problem=problem,
        model=model,
        provider=provider,
        regenerate=regenerate,
        has_model_solution=bool(problem.model_solution_pdf),
        has_statement=bool(problem.statement_pdf),
    )
    candidates = latest_versions(problem)
    if submission_ids is not None:
        wanted = set(submission_ids)
        candidates = [item for item in candidates if item.pk in wanted]
    existing = {
        item.submission_id: item
        for item in AiAssessment.objects.filter(
            submission__in=[c.pk for c in candidates], provider=provider, requested_model=model
        )
    }
    files: list[SubmissionFile] = []
    for submission in candidates:
        current = existing.get(submission.pk)
        if current is not None and current.is_in_progress:
            plan.skipped_in_progress += 1
            continue
        if current is not None and current.is_done and not regenerate:
            plan.skipped_done += 1
            continue
        submission_file = _clean_file(submission)
        if submission_file is None:
            plan.without_file += 1
            continue
        plan.to_generate.append(submission)
        files.append(submission_file)
    plan.estimate_usd = estimate_cost(problem, files, model, provider=provider, row=row)
    plan.estimate_tokens = estimate_tokens(problem, files)
    return plan


def assert_can_request(
    competition, provider: str | None = None, model: str | None = None, *, for_tests: bool = False
) -> AiGradingSettings:
    """Bramka zlecenia – ta sama dla ekranu podglądu, potwierdzenia i pracy testowej.

    Kolejność bramek jest kolejnością naprawy: najpierw to, co koordynator widzi w ustawieniach
    (dostawca, pakiet, klucz), potem oświadczenie prawne (umowa powierzenia – **nie** dotyczy pracy
    testowej), a na końcu bezpieczniki kosztu.
    """
    if not is_enabled(competition):
        raise _conflict("Ocena AI jest w tym konkursie wyłączona.", "AI_DISABLED")
    row = settings_for(competition)
    provider, model = resolve_target(row, provider, model)
    item = providers.get(provider)
    label = provider_label(provider)
    if not item.is_available():
        raise _conflict(
            f"Dostawca {label} jest niedostępny: na serwerze brak pakietu „{item.sdk_package}”.",
            "AI_PROVIDER_UNAVAILABLE",
        )
    account = AiProviderAccount.objects.filter(competition=competition, provider=provider).first()
    if account is None or not account.has_key:
        raise _conflict(
            f"Najpierw dodaj klucz API dostawcy {label} w ustawieniach oceny AI.", "AI_KEY_MISSING"
        )
    if not for_tests and not account.dpa_confirmed:
        raise _conflict(
            f"Umowa powierzenia (DPA) z dostawcą {label} nie jest potwierdzona w ustawieniach oceny AI – "
            "bez niej prace uczestników nie mogą do niego trafić. Do wypróbowania dostawcy służy praca "
            "testowa.",
            "AI_DPA_MISSING",
        )
    if row.limit_reached:
        raise _conflict(
            "Osiągnięto limit wydatków na ocenę AI ustawiony w ustawieniach – podnieś go albo zdejmij.",
            "AI_BUDGET_REACHED",
        )
    if row.spending_limit_usd is not None and price_for(provider, model, row) is None:
        raise _conflict(
            f"Model {model} nie ma ceny, a ustawiony jest limit wydatków w USD – limit nie widziałby "
            "kosztu tego modelu. Wpisz cenę modelu w tabeli cen albo zdejmij limit.",
            "AI_PRICE_UNKNOWN",
        )
    return row


def _reset_fields(*, provider: str, model: str, now, actor) -> dict:
    """Pola oceny po (ponownym) zleceniu – wspólne dla prac uczestników i prac testowych."""
    return {
        "status": AiAssessmentStatus.PENDING,
        "provider": provider,
        "requested_model": model,
        "model": model,
        "requested_at": now,
        "requested_by": _user(actor),
        "dispatched_at": None,
        "started_at": None,
        "sent_at": None,
        "finished_at": None,
        "attempts": 0,
        "proposed_points": None,
        "max_points": None,
        "criteria": [],
        "summary": "",
        "errors": [],
        "confidence": "",
        "injection_suspected": False,
        "error_code": "",
        "error_message": "",
        "request_id": "",
    }


@transaction.atomic
def request_generation(
    problem,
    *,
    regenerate: bool,
    submission_ids: list[int] | None = None,
    actor,
    request=None,
    provider: str | None = None,
    model: str | None = None,
) -> Plan:
    """Zlecenie: oznacza oceny jako ``PENDING`` i wypuszcza tyle, ile mieści kolejka.

    Blokada doradcza per zadanie szereguje dwa równoczesne zlecenia tego samego zadania (dwie
    karty przeglądarki, podwójne kliknięcie): drugie widzi już oceny ``PENDING`` i je pomija,
    zamiast zakładać drugi wiersz na tę samą trójkę (praca, dostawca, model).
    """
    competition = problem.stage.edition.competition
    row = assert_can_request(competition, provider, model)
    provider, model = resolve_target(row, provider, model)
    _lock(problem.pk)
    plan = plan_generation(
        problem, regenerate=regenerate, submission_ids=submission_ids, provider=provider, model=model
    )
    if plan.count > settings.AI_GRADING_MAX_BATCH:
        raise _bad_request(
            f"Jedno zlecenie obejmuje najwyżej {settings.AI_GRADING_MAX_BATCH} prac.", "AI_BATCH_TOO_LARGE"
        )
    now = timezone.now()
    ids = [submission.pk for submission in plan.to_generate]
    existing = {
        item.submission_id: item
        for item in AiAssessment.objects.select_for_update().filter(
            submission_id__in=ids, provider=provider, requested_model=model
        )
    }
    queued: list[Submission] = []
    for submission in plan.to_generate:
        current = existing.get(submission.pk)
        fields = _reset_fields(provider=provider, model=model, now=now, actor=actor)
        if current is None:
            AiAssessment.objects.create(competition=competition, submission=submission, **fields)
        else:
            if current.is_in_progress or (current.is_done and not regenerate):
                continue
            for name, value in fields.items():
                setattr(current, name, value)
            current.save()
        queued.append(submission)
    plan.to_generate = queued
    audit(
        actor,
        "ai_grading.requested",
        problem,
        {"count": len(queued), "regenerate": regenerate, "provider": provider, "model": model},
        request=request,
    )
    pump()
    return plan


# --- kolejka ------------------------------------------------------------------------------------


def _stale_before(now):
    return now - timedelta(minutes=int(settings.AI_GRADING_STALE_MINUTES))


def pump(*, now=None) -> list[int]:
    """Wypuszcza do Celery tyle czekających ocen, ile mieści ogranicznik. Zwraca ich identyfikatory.

    „W locie” jest ocena ``RUNNING`` oraz ``PENDING`` już przekazana do kolejki – obie nie starsze
    niż ``AI_GRADING_STALE_MINUTES``. Starsze uznajemy za zgubione (restart workera) i przestają
    blokować miejsce, a zgubione ``PENDING`` wracają do puli. Zadania trafiają do Celery dopiero
    po zatwierdzeniu transakcji (``on_commit``), żeby worker nie szukał wiersza, którego jeszcze
    nie widać. Prace testowe idą tą samą kolejką – ogranicznik chroni workera, a nie dostawcę.
    """
    from .tasks import run_ai_assessment

    now = now or timezone.now()
    stale = _stale_before(now)
    cap = max(1, int(settings.AI_GRADING_MAX_CONCURRENCY))
    with transaction.atomic():
        _lock(PUMP_LOCK_KEY)
        in_flight = AiAssessment.objects.filter(
            Q(status=AiAssessmentStatus.RUNNING, started_at__gte=stale)
            | Q(status=AiAssessmentStatus.PENDING, dispatched_at__gte=stale)
        ).count()
        free = cap - in_flight
        if free <= 0:
            return []
        chosen = list(
            AiAssessment.objects.filter(status=AiAssessmentStatus.PENDING)
            .filter(Q(dispatched_at__isnull=True) | Q(dispatched_at__lt=stale))
            .order_by("requested_at", "id")
            .values_list("pk", flat=True)[:free]
        )
        if not chosen:
            return []
        AiAssessment.objects.filter(pk__in=chosen).update(dispatched_at=now)
        for pk in chosen:
            transaction.on_commit(lambda pk=pk: run_ai_assessment.delay(pk))
    return chosen


def recover_stale(*, now=None) -> int:
    """Oceny ``RUNNING`` starsze niż próg kończą się błędem – worker zginął w trakcie wywołania.

    Błąd, a nie ponowne zlecenie: wywołanie mogło się zakończyć (i zostać policzone) po stronie
    dostawcy, zanim worker padł. Automatyczne ponowienie mogłoby więc zapłacić drugi raz – o tym
    decyduje koordynator przyciskiem „wygeneruj ponownie”.
    """
    now = now or timezone.now()
    return AiAssessment.objects.filter(
        status=AiAssessmentStatus.RUNNING, started_at__lt=_stale_before(now)
    ).update(
        status=AiAssessmentStatus.ERROR,
        error_code="interrupted",
        error_message="Ocena została przerwana (restart workera?). Wygeneruj ją ponownie.",
        finished_at=now,
    )


# --- przebieg jednej oceny ----------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """Wynik przebiegu dla zadania Celery: status końcowy albo prośba o ponowienie."""

    status: str
    retry_after: int | None = None


class _Fail(Exception):
    def __init__(self, code: str, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def mark_failed(assessment_id: int, code: str, message: str, request_id: str = "") -> RunOutcome:
    AiAssessment.objects.filter(pk=assessment_id).update(
        status=AiAssessmentStatus.ERROR,
        error_code=code[:32],
        error_message=message[:500],
        request_id=(request_id or "")[:80],
        finished_at=timezone.now(),
    )
    return RunOutcome(AiAssessmentStatus.ERROR)


def _needles(submission: Submission | None) -> list[str]:
    """Dane osobowe autora pracy do wymazania z odpowiedzi modelu (``prompt.redact``).

    Praca testowa nie ma autora-uczestnika – nie ma czego wymazywać.
    """
    if submission is None:
        return []
    participant = submission.entry.participant
    user = participant.user
    local_part = (user.email or "").split("@")[0]
    return [user.first_name, user.last_name, user.get_full_name(), local_part, participant.school or ""]


def _refusal_message(category: str | None, provider: str = "anthropic") -> str:
    suffix = f" (kategoria: {category})" if category else ""
    if provider == AiProvider.ANTHROPIC:
        return (
            f"Model odmówił oceny tej pracy{suffix}, także po automatycznym przełączeniu na model "
            "zastępczy. Oceń ją bez sugestii AI."
        )
    return (
        f"Model {provider_label(provider)} odmówił oceny tej pracy albo zablokował ją filtr "
        f"bezpieczeństwa dostawcy{suffix}. Oceń ją bez sugestii AI albo innym dostawcą."
    )


def run_assessment(assessment_id: int, *, final_attempt: bool = True) -> RunOutcome:
    """Jedna ocena od przejęcia do zapisu. Woła ją zadanie Celery – klucz czyta sama, z bazy.

    ``final_attempt`` mówi, czy przy błędzie przejściowym wolno jeszcze poprosić o ponowienie.
    """
    from apps.tenancy.context import competition_context

    now = timezone.now()
    claimed = AiAssessment.objects.filter(pk=assessment_id, status=AiAssessmentStatus.PENDING).update(
        status=AiAssessmentStatus.RUNNING, started_at=now, attempts=F("attempts") + 1
    )
    if not claimed:
        # Drugie dostarczenie tego samego zadania albo ocena już zakończona – nic do zrobienia.
        return RunOutcome("SKIPPED")
    assessment = AiAssessment.objects.select_related(
        "competition",
        "submission",
        "submission__entry__participant__user",
        "submission__problem__stage__edition",
        "submission__problem__stage__scoring_scale",
        "test_work",
        "test_work__problem__stage__edition",
        "test_work__problem__stage__scoring_scale",
    ).get(pk=assessment_id)
    competition = assessment.competition
    with competition_context(competition):
        try:
            return _execute(assessment, competition, final_attempt=final_attempt)
        except _Fail as failure:
            return mark_failed(assessment_id, failure.code, failure.message, failure.request_id)
        except prompt.MaterialError as failure:
            return mark_failed(assessment_id, failure.code, failure.message)


@dataclass(frozen=True)
class _Source:
    """Plik do oceny: z pracy uczestnika albo z pracy testowej – dalej przebieg jest jeden."""

    problem: object
    object_key: str
    mime: str
    size_bytes: int
    page_count: int | None


def _source(assessment: AiAssessment) -> _Source:
    if assessment.is_test:
        work = assessment.test_work
        if not work.is_clean or not work.object_key:
            raise _Fail("no_file", "Praca testowa nie ma pliku po czystym skanie antywirusowym.")
        return _Source(work.problem, work.object_key, work.mime, work.size_bytes, work.page_count)
    submission_file = _clean_file(assessment.submission)
    if submission_file is None:
        raise _Fail("no_file", "Praca nie ma pliku po czystym skanie antywirusowym.")
    return _Source(
        assessment.submission.problem,
        submission_file.object_key,
        submission_file.mime,
        submission_file.size_bytes,
        submission_file.page_count,
    )


def _execute(assessment: AiAssessment, competition, *, final_attempt: bool) -> RunOutcome:
    if not is_enabled(competition):
        raise _Fail("disabled", "Ocena AI została w tym konkursie wyłączona.")
    row = settings_for(competition)
    name = assessment.provider
    label = provider_label(name)
    try:
        provider = providers.get(name)
    except KeyError:
        raise _Fail("provider", f"Nieznany dostawca modelu ({name}).") from None
    if not provider.is_available():
        raise _Fail("provider_unavailable", f"Na serwerze brak pakietu SDK dostawcy {label}.")
    account = AiProviderAccount.objects.filter(competition=competition, provider=name).first()
    if account is None or not account.has_key:
        raise _Fail("no_key", f"Brak klucza API dostawcy {label} – dodaj go w ustawieniach oceny AI.")
    # Bramka prawna sprawdzana **jeszcze raz** przy wysyłce: umowę można wycofać, gdy praca czeka
    # w kolejce, a wtedy nie wolno jej wysłać.
    if not assessment.is_test and not account.dpa_confirmed:
        raise _Fail(
            "dpa",
            f"Umowa powierzenia z dostawcą {label} nie jest potwierdzona – praca nie została wysłana.",
        )
    if row.limit_reached:
        raise _Fail("budget", "Osiągnięto limit wydatków ustawiony przez koordynatora.")
    model = assessment.requested_model or assessment.model or row.model
    if row.spending_limit_usd is not None and price_for(name, model, row) is None:
        raise _Fail(
            "price_unknown",
            f"Model {model} nie ma ceny, a ustawiony jest limit wydatków – wpisz cenę modelu albo zdejmij "
            "limit.",
        )
    key = decrypt_key(account.api_key_encrypted)
    if key is None:
        raise _Fail("key_unreadable", "Klucza API nie da się odczytać – wpisz go ponownie w ustawieniach.")

    source = _source(assessment)
    # Rozmiar sprawdzamy **przed** czytaniem pliku: base64 powiększa go o jedną trzecią, a worker
    # ma ograniczoną pamięć (``mem_limit``) – plik, który i tak nie zmieści się w żądaniu dostawcy,
    # nie ma po co lądować w niej dwa razy.
    limit = provider.limits.max_request_bytes
    if source.size_bytes * 4 // 3 > limit:
        raise _Fail(
            "too_large",
            f"Plik pracy przekracza limit {label} ({limit // (1024 * 1024)} MB w jednym żądaniu po "
            "zakodowaniu). Oceń tę pracę innym dostawcą albo bez sugestii AI.",
        )
    materials = problem_materials(source.problem)
    grading = GradingInput(
        competition_name=competition.name,
        materials=materials,
        submission=read_object(source.object_key),
        submission_mime=source.mime,
        submission_pages=source.page_count,
        max_tokens=int(settings.AI_GRADING_MAX_TOKENS),
    )
    request = provider.build_request(model, grading)

    AiAssessment.objects.filter(pk=assessment.pk).update(sent_at=timezone.now())
    try:
        result = call_model(key, request)
    except ApiFailure as failure:
        if failure.retryable and not final_attempt:
            AiAssessment.objects.filter(pk=assessment.pk).update(
                status=AiAssessmentStatus.PENDING,
                dispatched_at=timezone.now(),
                error_code=failure.code,
                error_message=f"{failure.message} (ponowienie)"[:500],
            )
            return RunOutcome("RETRY", retry_after=failure.retry_after)
        raise _Fail(failure.code, failure.message, failure.request_id) from None

    _record_usage(assessment.pk, competition, result, request["model"], name)
    if result.stop_reason == "refusal":
        raise _Fail("refusal", _refusal_message(result.refusal_category, name), result.request_id)
    if result.stop_reason == "max_tokens":
        raise _Fail(
            "max_tokens",
            "Odpowiedź modelu przekroczyła limit długości i została ucięta. Spróbuj ponownie albo "
            "oceń pracę bez sugestii AI.",
            result.request_id,
        )
    if result.stop_reason not in ("end_turn", "stop_sequence"):
        raise _Fail("api", f"Nieoczekiwane zakończenie odpowiedzi ({result.stop_reason}).", result.request_id)
    try:
        parsed = prompt.validate_output(result.text, max_points=materials.max_points)
    except prompt.InvalidOutput as exc:
        raise _Fail(
            "invalid_output", f"Odpowiedź modelu nie pasuje do schematu: {exc}", result.request_id
        ) from None
    parsed = prompt.redact_assessment(parsed, _needles(assessment.submission))

    AiAssessment.objects.filter(pk=assessment.pk).update(
        status=AiAssessmentStatus.DONE,
        model=result.model[:MODEL_ID_MAX_LENGTH],
        proposed_points=parsed.proposed_points,
        max_points=parsed.max_points,
        criteria=parsed.criteria,
        summary=parsed.summary,
        errors=parsed.errors,
        confidence=parsed.confidence,
        injection_suspected=parsed.injection_suspected,
        error_code="",
        error_message="",
        request_id=result.request_id[:80],
        finished_at=timezone.now(),
    )
    return RunOutcome(AiAssessmentStatus.DONE)


# --- odczyty: koordynator -----------------------------------------------------------------------


def _final_display(score, problem) -> Decimal:
    """Ocena końcowa w postaci do pokazania: skala etapu bywa w bazie przesunięta (``offset``).

    Przesunięcie zna reguła oceny (``ScoreRule.to_display``) – ta sama, która je nałożyła przy
    zapisie, więc zadanie z własnym zakresem nie dostanie cudzego offsetu.
    """
    rule = _rule(problem)
    return rule.to_display(score) if rule is not None else score


def _agreement(diffs: list[Decimal]) -> dict | None:
    if not diffs:
        return None
    return {
        "count": len(diffs),
        "mean_abs_diff": (sum(diffs) / len(diffs)).quantize(Decimal("0.01")),
        "exact_share": round(100 * sum(1 for d in diffs if d < Decimal("0.5")) / len(diffs)),
        "within_one_share": round(100 * sum(1 for d in diffs if d <= 1) / len(diffs)),
    }


def problem_overview(problem) -> dict:
    """Sekcja „Ocena AI” na karcie zadania: liczniki, wiersze prac, zgodność, prace testowe.

    Przy pracy może stać kilka ocen (różni dostawcy albo modele) – najnowsza pierwsza. Zgodność
    z oceną końcową liczymy dwa razy: łącznie (najnowsza gotowa ocena każdej pracy – jak do
    v0.34.0) i osobno dla każdej pary dostawca–model, bo „który model trafia lepiej” jest właśnie
    tym, po co porównuje się dostawców. Prace **testowe** do tych liczb nie wchodzą nigdy.
    """
    from apps.grading.models import FinalGrade

    from .sandbox import test_works_overview

    competition = problem.stage.edition.competition
    submissions = latest_versions(problem)
    by_submission: dict[int, list[AiAssessment]] = {}
    for item in AiAssessment.objects.filter(submission__in=[s.pk for s in submissions]).order_by(
        "-requested_at", "-id"
    ):
        by_submission.setdefault(item.submission_id, []).append(item)
    finals = {
        item.submission_id: item.score
        for item in FinalGrade.objects.filter(submission__in=[s.pk for s in submissions])
    }
    counts = {choice: 0 for choice in AiAssessmentStatus.values}
    rows = []
    diffs: list[Decimal] = []
    per_model: dict[tuple[str, str], list[Decimal]] = {}
    cost = Decimal("0")
    cost_known = True
    for submission in submissions:
        assessments = by_submission.get(submission.pk, [])
        for assessment in assessments:
            counts[assessment.status] += 1
            cost += assessment.cost_usd
            cost_known = cost_known and assessment.cost_known
        final = finals.get(submission.pk)
        final_display = _final_display(final, problem) if final is not None else None
        done = [item for item in assessments if item.is_done]
        if final_display is not None:
            if done:
                newest = max(done, key=lambda item: (item.finished_at or item.requested_at, item.pk))
                diffs.append(abs(Decimal(final_display) - newest.proposed_points))
            for item in done:
                per_model.setdefault((item.provider, item.requested_model), []).append(
                    abs(Decimal(final_display) - item.proposed_points)
                )
        rows.append(
            {
                "submission": submission,
                "assessments": assessments,
                "assessment": assessments[0] if assessments else None,
                "final": final_display,
                "has_file": _clean_file(submission) is not None,
            }
        )
    by_model = [
        {"provider": provider_label(provider), "model": model, **_agreement(values)}
        for (provider, model), values in sorted(per_model.items())
    ]
    submission_targets = targets(competition, for_tests=False)
    test_targets = targets(competition, for_tests=True)
    return {
        "rows": rows,
        "counts": counts,
        "missing": sum(1 for row in rows if not row["assessments"]),
        "in_progress": counts[AiAssessmentStatus.PENDING] + counts[AiAssessmentStatus.RUNNING],
        "agreement": _agreement(diffs),
        "agreement_by_model": by_model if len(by_model) > 1 else [],
        "cost_usd": cost,
        "cost_known": cost_known,
        "targets": submission_targets,
        "test_targets": test_targets,
        "can_generate": bool(submission_targets),
        "can_test": bool(test_targets),
        **test_works_overview(problem),
    }


# --- odczyty: recenzent i uczestnik -------------------------------------------------------------


def reviewer_context(review, competition, *, editable: bool) -> dict | None:
    """Panele „Ocena AI – <dostawca> <model>” przy recenzji albo ``None``, gdy nie ma czego pokazać.

    Wołający (``ReviewDetailView``) dostaje recenzję wyłącznie z własnych przydziałów recenzenta,
    więc sugestia cudzej pracy nie ma tu jak trafić. Pokazujemy wyłącznie oceny ``DONE`` **tej**
    wersji pracy (sugestia do starszej wersji opisywałaby inny plik), każdą jako osobny panel,
    najnowszą pierwszą. Oceny prac testowych nie mają pracy uczestnika, więc tu nie trafiają.
    """
    if not is_enabled(competition):
        return None
    assessments = list(
        AiAssessment.objects.filter(
            submission_id=review.submission_id, status=AiAssessmentStatus.DONE
        ).order_by("-finished_at", "-id")
    )
    if not assessments:
        return None
    from apps.grading.rubric import criteria_for

    has_rubric = bool(list(criteria_for(review.submission.problem)))
    problem = review.submission.problem
    panels = []
    for assessment in assessments:
        # Najbliższa wartość skali albo – w etapie z dowolnymi wartościami (0.35.0) – propozycja
        # przycięta do zakresu zadania (``suggested_points``), dla każdego dostawcy tak samo.
        suggested = suggested_points(assessment.proposed_points, problem)
        panels.append(
            {
                "assessment": assessment,
                "provider_label": provider_short(assessment.provider),
                # Przycisk tylko wypełnia formularz – i tylko tam, gdzie formularz ma jedno pole
                # punktów. Przy rubryce punkty rozdziela recenzent kryterium po kryterium, a
                # sugestia AI dzieli rozwiązanie po swojemu – przeniesienie jej byłoby zgadywaniem.
                "prefill_value": suggested if editable and not has_rubric else None,
            }
        )
    return {
        "assessment": panels[0]["assessment"],
        "prefill_value": panels[0]["prefill_value"],
        "panels": panels,
    }


def suggested_points(proposed: Decimal | None, problem):
    """Punkty do wstawienia przyciskiem „punkty AI jako punkt wyjścia” – albo ``None``.

    W etapie „tylko ze skali” – najbliższa wartość skali (``prompt.nearest_scale_value``), jak
    dotąd. W etapie z dowolnymi wartościami (wydanie 0.35.0) – sama propozycja, przycięta do
    zakresu zadania i sprowadzona do 0,01 (połówka w górę, ``apps.core.points.round_points``):
    formularz przyjmuje wtedy każdą taką liczbę, więc zaokrąglanie do skali odbierałoby
    recenzentowi informację, którą AI już podało.
    """
    if proposed is None:
        return None
    rule = _rule(problem)
    if rule is not None and rule.free:
        from apps.core.points import round_points

        value = round_points(proposed)
        return min(max(value, rule.display_minimum), rule.display_maximum)
    return prompt.nearest_scale_value(proposed, scale_values(problem))


def participant_ai_feedback(participant, stage) -> list[dict]:
    """Sugestie AI dla uczestnika – **wyłącznie** gdy koordynator to włączył i wyniki są ogłoszone.

    Trzy warunki naraz: flaga konkursu, przełącznik etapu i publikacja wyników. Brak któregoś
    daje pustą listę, a szablon bez listy nie ma o czym wspomnieć – uczestnik nie dowiaduje się
    nawet, że ocena AI w ogóle istnieje. Przy kilku ocenach jednej pracy (porównanie dostawców)
    uczestnik widzi **najnowszą** – porównanie modeli jest narzędziem komitetu, nie informacją
    zwrotną.
    """
    from apps.competitions.models import StageEntry
    from apps.results.models import ResultsPublication

    competition = stage.edition.competition
    if participant is None or not is_enabled(competition):
        return []
    if not AiStageVisibility.objects.filter(stage=stage, show_to_participants=True).exists():
        return []
    if not ResultsPublication.objects.filter(stage=stage).exists():
        return []
    entry = StageEntry.objects.filter(participant=participant, stage=stage).first()
    if entry is None:
        return []
    latest: dict[int, Submission] = {}
    for submission in (
        Submission.objects.filter(entry=entry)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .select_related("problem")
        .order_by("problem__number", "problem_id", "-version")
    ):
        latest.setdefault(submission.problem_id, submission)
    assessments: dict[int, AiAssessment] = {}
    for item in AiAssessment.objects.filter(
        submission__in=[s.pk for s in latest.values()], status=AiAssessmentStatus.DONE
    ).order_by("-finished_at", "-id"):
        assessments.setdefault(item.submission_id, item)
    items = []
    for submission in latest.values():
        assessment = assessments.get(submission.pk)
        if assessment is None:
            continue
        items.append(
            {
                "number": submission.problem.number,
                "title": submission.problem.title,
                "proposed_points": assessment.proposed_points,
                "max_points": assessment.max_points,
                "summary": assessment.summary,
                "provider_label": provider_label(assessment.provider),
                "model": assessment.model,
            }
        )
    return items


def export_section(participant) -> list[dict]:
    """Oceny AI w eksporcie danych uczestnika (art. 15 i 20 RODO).

    Zawsze: **fakt** przetwarzania – która praca, kiedy i do kogo wyszła (odbiorca danych jest
    informacją, do której uczestnik ma prawo z art. 15 ust. 1 lit. c, niezależnie od ustawień
    ekranu), osobno dla każdego dostawcy, do którego praca trafiła. Treść sugestii – wyłącznie tam,
    gdzie uczestnik i tak ją widzi w panelu (:func:`participant_ai_feedback`: przełącznik etapu
    i ogłoszone wyniki). Eksport nie może być drugą, luźniejszą drogą do danych, których ekran
    uczestnikowi nie pokazuje.
    """
    if participant is None:
        return []
    from apps.results.models import ResultsPublication

    rows = (
        AiAssessment.objects.filter(submission__entry__participant=participant, sent_at__isnull=False)
        .select_related("submission__problem", "submission__entry__stage__edition__competition")
        .order_by(
            "submission__entry__stage__opens_at",
            "submission__problem__number",
            "submission__version",
            "sent_at",
            "id",
        )
    )
    stage_ids = {row.submission.entry.stage_id for row in rows}
    visible = visible_stage_ids(stage_ids) & set(
        ResultsPublication.objects.filter(stage_id__in=stage_ids).values_list("stage_id", flat=True)
    )
    items = []
    for row in rows:
        stage = row.submission.entry.stage
        shown = stage.pk in visible and row.is_done and is_enabled(stage.edition.competition)
        items.append(
            {
                "etap": stage.display_name,
                "zadanie_numer": row.submission.problem.number,
                "wersja_pracy": row.submission.version,
                "dostawca": provider_label(row.provider),
                "przekazano_do": processor_name(row.provider),
                "przekazano": timezone.localtime(row.sent_at).isoformat(),
                "model": row.model,
                "charakter": (
                    "sugestia oceny dla komitetu, niewiążąca – ocenę wystawia recenzent; "
                    "decyzja nie zapada w sposób zautomatyzowany"
                ),
                "tresc": (
                    {
                        # Liczby JSON jak w reszcie eksportu od wydania 0.35.0 (``points_json``):
                        # ``6``, a nie „6.00”, i ``4.5`` dla ułamka. Tekst z kolumny dziesiętnej
                        # był czytelny dla maszyny, ale niespójny z ``suma_punktow`` obok.
                        "proponowane_punkty": points_json(row.proposed_points),
                        "maksimum": points_json(row.max_points),
                        "podsumowanie": row.summary,
                    }
                    if shown
                    else None
                ),
            }
        )
    return items


def erase_for_participants(participants) -> int:
    """Kasuje oceny AI prac tych uczestników – przy anonimizacji konta (art. 17 RODO).

    Praca i jej oficjalne oceny zostają (są dokumentacją zawodów, patrz ``apps.accounts.profile``),
    ale sugestia AI dokumentacją nie jest: nikt na niej nie opiera kwalifikacji, a po
    anonimizacji nie ma już komu jej pokazać. Liczniki zużycia w ustawieniach konkursu zostają.
    """
    deleted, _ = AiAssessment.objects.filter(submission__entry__participant__in=participants).delete()
    return deleted
