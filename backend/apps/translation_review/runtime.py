"""Zatwierdzone poprawki tłumaczy w gettext – w czasie działania, bez wydania (L10N-01 § 6).

Jak to działa: Django trzyma jeden obiekt ``DjangoTranslation`` na język, a w nim
``TranslationCatalog`` – listę słowników przeszukiwanych po kolei (``_catalogs``). Nakładka to
**pierwszy** słownik tej listy: klucze gettext (``msgid``, ``"kontekst\\x04msgid"`` albo
``(msgid, forma)``) z tekstem zatwierdzonym przez recenzenta. Wszystkiego, czego w nakładce nie ma,
szuka się dalej dokładnie tak jak dotąd.

Punkt zaczepienia to ``trans_real.translation()`` – Django nie ma publicznego haka „katalog
języka został załadowany”. Owijamy tę jedną funkcję, bo przez nią przechodzi **każda** aktywacja
języka: ``LocaleMiddleware``, ``PreferencesMiddleware``, ``translation.override()`` w zadaniach
Celery (listy). Owinięcie niczego nie zmienia w wyniku, gdy nakładka jest pusta albo wyłączona,
i nigdy nie rzuca – błąd nakładki kończy się wpisem w logu i katalogiem z repozytorium.

Pamięć (unieważnianie): wersja nakładki i jej treść stoją w cache'u (Redis) bez terminu ważności;
proces sprawdza wersję najwyżej raz na ``TRANSLATION_OVERRIDES_CHECK_SECONDS``, więc zwykłe żądanie
nie dokłada nawet odczytu z Redisa. Zmiana (zatwierdzenie, cofnięcie, przycięcie) przebudowuje
wpis z bazy pod nową wersją (``publish``) – proces, który ją zrobił, widzi ją od razu, pozostałe
po kilku sekundach. Utracony cache (restart Redisa) to jedno zapytanie przy najbliższym żądaniu
w danym języku. Język źródłowy (polski) nie ma nakładki nigdy.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import trans_real

logger = logging.getLogger(__name__)

VERSION_KEY = "translation-review:version:{language}"
PAYLOAD_KEY = "translation-review:overlay:{language}"
#: Atrybut na obiekcie ``DjangoTranslation``: ``(wersja, słownik)`` aktualnie włożonej nakładki.
LAYER_ATTR = "_translation_review_layer"


@dataclass
class _Memo:
    version: str
    entries: dict
    next_check: float


_memo: dict[str, _Memo] = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(getattr(settings, "TRANSLATION_OVERRIDES_ENABLED", True))


def check_interval() -> float:
    return float(getattr(settings, "TRANSLATION_OVERRIDES_CHECK_SECONDS", 5))


def gettext_key(msgctxt: str | None, msgid: str, plural_index: int | None):
    """Klucz, pod którym ``DjangoTranslation`` szuka napisu (``pgettext`` skleja kontekst ``\\x04``)."""
    base = f"{msgctxt}{trans_real.CONTEXT_SEPARATOR}{msgid}" if msgctxt else msgid
    return base if plural_index is None else (base, plural_index)


def entries_from_db(language: str) -> dict:
    """Nakładki języka bez tych, które niczego nie zmieniają.

    Pomijamy nakładkę z tekstem identycznym z katalogiem (potwierdzenie „obecne tłumaczenie jest
    dobre” albo poprawka już wdrożona, a jeszcze nieprzycięta). Gdyby trafiła do gettext, po
    kolejnym wydaniu, które zmieni ten ``msgstr`` w repozytorium, przykrywałaby nowy tekst starym.
    """
    from .catalogs import index
    from .models import TranslationOverride

    known = index(language).by_key
    rows = TranslationOverride.objects.filter(language=language).values_list(
        "key", "msgctxt", "msgid", "plural_index", "text"
    )
    entries = {}
    for key, ctxt, msgid, form, text in rows:
        row = known.get(key)
        if row is not None and row.translation == text:
            continue
        entries[gettext_key(ctxt, msgid, form)] = text
    return entries


def publish(language: str) -> str:
    """Przebudowuje nakładkę języka z bazy pod nową wersją. Woła ją każda zmiana nakładek."""
    version = uuid.uuid4().hex
    entries = entries_from_db(language)
    cache.set(PAYLOAD_KEY.format(language=language), {"version": version, "entries": entries}, None)
    cache.set(VERSION_KEY.format(language=language), version, None)
    with _lock:
        _memo[language] = _Memo(version, entries, time.monotonic() + check_interval())
    return version


def forget() -> None:
    """Zapomina stan procesu (testy). Cache i obiekty tłumaczeń odświeżą się przy następnym użyciu."""
    with _lock:
        _memo.clear()


def overlay(language: str) -> _Memo:
    now = time.monotonic()
    memo = _memo.get(language)
    if memo is not None and now < memo.next_check:
        return memo
    version = cache.get(VERSION_KEY.format(language=language))
    if memo is not None and version is not None and version == memo.version:
        memo.next_check = now + check_interval()
        return memo
    payload = cache.get(PAYLOAD_KEY.format(language=language)) if version is not None else None
    if payload is None or payload.get("version") != version:
        try:
            publish(language)
        except Exception:  # noqa: BLE001 - baza niedostępna: katalog z repozytorium, próba za chwilę
            logger.exception("Nie udało się zbudować nakładki tłumaczeń dla %s.", language)
            fallback = _Memo(memo.version if memo else "unavailable", memo.entries if memo else {}, now + 30)
            with _lock:
                _memo[language] = fallback
            return fallback
        return _memo[language]
    fresh = _Memo(payload["version"], payload["entries"], now + check_interval())
    with _lock:
        _memo[language] = fresh
    return fresh


def _strip(translation) -> None:
    layer = getattr(translation, LAYER_ATTR, None)
    if layer is None:
        return
    catalog = translation._catalog
    with _lock:
        for position, item in enumerate(catalog._catalogs):
            if item is layer[1]:
                del catalog._catalogs[position]
                del catalog._plurals[position]
                break
        setattr(translation, LAYER_ATTR, None)


def apply(translation, language: str) -> None:
    """Wkłada (albo wymienia) nakładkę w obiekcie tłumaczenia języka ``language``."""
    if language == settings.LANGUAGE_CODE or language not in dict(settings.LANGUAGES):
        return
    if not enabled():
        _strip(translation)
        return
    memo = overlay(language)
    layer = getattr(translation, LAYER_ATTR, None)
    if layer is not None and layer[0] == memo.version:
        return
    catalog = translation._catalog
    with _lock:
        if layer is not None:
            for position, item in enumerate(catalog._catalogs):
                if item is layer[1]:
                    # Podmiana jednym przypisaniem – wątek, który właśnie szuka napisu, widzi
                    # starą albo nową nakładkę, nigdy listę bez niej.
                    catalog._catalogs[position] = memo.entries
                    break
            else:
                layer = None
        if layer is None:
            from .catalogs import plural_function

            catalog._catalogs.insert(0, memo.entries)
            catalog._plurals.insert(0, plural_function(language) or translation.plural)
        setattr(translation, LAYER_ATTR, (memo.version, memo.entries))


def install() -> None:
    """Owija ``trans_real.translation`` – raz na proces."""
    original = trans_real.translation
    if getattr(original, "_translation_review", False):
        return

    @functools.wraps(original)
    def translation(language):
        result = original(language)
        try:
            apply(result, language)
        except Exception:  # noqa: BLE001 - nakładka nie może zepsuć tłumaczeń całej strony
            logger.exception("Nakładka tłumaczeń dla %s nie została zastosowana.", language)
        return result

    translation._translation_review = True
    trans_real.translation = translation
