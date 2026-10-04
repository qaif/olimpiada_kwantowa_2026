"""Conocny test odtwarzania kopii zapasowej – sprawdzenia aplikacyjne i ich wynik (OPS-01).

Część powłokowa (``scripts/backup_verify.sh``) robi to, czego aplikacja zrobić nie może i nie
powinna: wybiera paczkę z katalogu kopii hosta, stawia **jednorazowego** Postgresa na własnej,
odciętej sieci i wlewa do niego zrzut. Ten moduł odpowiada na pytanie, które zostaje potem:
„czy **ta wersja kodu** z **tym kluczem** dałaby radę pracować na tej bazie”. Na to pytanie
nie odpowie ``SELECT count(*)`` z powłoki – odpowie wyłącznie sama aplikacja, uruchomiona
z obrazu, który właśnie działa na produkcji (docs/tasks/OPS-01.md § 2).

Trzy role, trzy podkomendy ``manage.py restore_check`` i trzy miejsca uruchomienia:

- :func:`live_counts` – w **żywym** kontenerze ``web``: wyłącznie ``SELECT count(*)`` na tabelach
  kluczowych. Punkt odniesienia dla widełek liczności,
- :func:`verify` – w **jednorazowym** kontenerze z obrazem ``web``, którego jedyną bazą jest
  baza tymczasowa (bramka :func:`guard_isolated`). Wynik – dokument JSON na stdout,
- :func:`record` – znowu w żywym ``web``: wynik do cache'u (dla ``/healthz/``, ``/status.json``
  i watchdoga), wpis audytu (historia w bazie) i – przy porażce – list do dyżurnych od razu.

Wynik niesie **wyłącznie liczby i nazwy** (tabel, modeli, sprawdzeń). Wartości pól, w tym
odszyfrowanych, nie opuszczają funkcji, która je sprawdza – wynik ląduje w logu crona, w Redisie
i w audycie, a żadne z tych miejsc nie jest miejscem na numer paszportu.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Wersja kształtu dokumentu wyniku. Podnieść przy zmianie niezgodnej wstecz – historia na hoście
#: (``restore-checks.jsonl``) trzyma dokumenty z wielu wersji.
RESULT_VERSION = 1

#: Klucz ostatniego wyniku w cache'u. Obok znaczników ``apps.core.backup`` i z tym samym czasem
#: życia – z tych samych powodów (stan „nie wiem” ma wyglądać jak stan zły, a nie znikać).
LAST_RESULT_KEY = "backup:restore_check"

#: Akcja wpisu audytu. Historia testów w bazie: ``AuditLog.objects.filter(action=…)``.
AUDIT_ACTION = "backup.restore_check"

#: Klucz alarmu – ten sam dla natychmiastowego listu z ``record`` i dla watchdoga, więc oba
#: dzielą jedno okno wyciszenia i dyżurny nie dostaje dwóch listów o tej samej porażce.
ALERT_KEY = "backup-restore-check"

#: Przedrostek nazwy bazy, do której wolno podłączyć :func:`verify`. Część umowy ze skryptem.
RESTORE_DB_PREFIX = "restorecheck_"

#: Nazwa hosta usługi ``db`` w compose – czyli żywej bazy. Cel testu nie może się tak nazywać.
LIVE_DB_HOST = "db"

STATUS_OK = "ok"
STATUS_FAILED = "failed"

CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"
CHECK_SKIP = "skip"

LEVEL_OK = "ok"
LEVEL_FAILED = "failed"
LEVEL_STALE = "stale"
LEVEL_UNKNOWN = "unknown"

#: Tabele kluczowe: odpowiedzi na „czy z tego da się odtworzyć zawody” – kto startował, w czym,
#: pod jakim adresem, co oddał (metryki i pliki), co o tym postanowiono (audyt) i kto przyjeżdża
#: na finał. Etykiety modeli, nie nazwy tabel: tabelę rozstrzyga ``_meta.db_table`` po stronie
#: kodu, który ją zna. Model nieobecny w instalacji jest pomijany (a nie zgłaszany).
KEY_MODELS = (
    "accounts.User",
    "accounts.Participant",
    "tenancy.Competition",
    "wagtailcore.Site",
    "competitions.Stage",
    "submissions.Submission",
    "submissions.SubmissionFile",
    "core.AuditLog",
    "delegation_logistics.DelegationMember",
)

#: Konto koordynatora istnieje zawsze, także przed pierwszą rejestracją – zero kont to zrzut pusty.
MIN_USERS = 1


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Thresholds:
    """Progi testu. Zmienne środowiskowe, bo ustawia je operator w ``.env`` (docs/OPERACJE.md § 43.5).

    Widełki liczności są **asymetryczne**: kopia z 3:15 sprawdzana o 4:40 jest z natury trochę
    mniejsza od bazy żywej (nocne wpisy audytu, rejestracje), więc dolna granica ma zapas, a górna
    – prawie żadnego. Kopia **większa** od żywej bazy znaczy, że od nocy coś masowo zniknęło
    (skasowana edycja) – to nie jest awaria kopii, ale dyżurny ma się o tym dowiedzieć rano.
    ``slack`` chroni małe tabele: przy pięciu etapach jeden dodany po zrzucie to już 20 %.
    """

    max_backup_age_hours: float = 26.0
    min_ratio: float = 0.90
    max_ratio: float = 1.05
    slack: int = 20
    media_sample: int = 20
    media_max_missing_ratio: float = 0.10

    @classmethod
    def from_env(cls) -> Thresholds:
        return cls(
            max_backup_age_hours=_env_float("RESTORE_CHECK_MAX_BACKUP_AGE_HOURS", cls.max_backup_age_hours),
            min_ratio=_env_float("RESTORE_CHECK_MIN_RATIO", cls.min_ratio),
            max_ratio=_env_float("RESTORE_CHECK_MAX_RATIO", cls.max_ratio),
            slack=_env_int("RESTORE_CHECK_SLACK_ROWS", cls.slack),
            media_sample=_env_int("RESTORE_CHECK_MEDIA_SAMPLE", cls.media_sample),
            media_max_missing_ratio=_env_float(
                "RESTORE_CHECK_MEDIA_MAX_MISSING_RATIO", cls.media_max_missing_ratio
            ),
        )


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail[:500]}


# --- bramka ----------------------------------------------------------------------------------


class NotIsolated(RuntimeError):
    """Komenda ``verify`` zobaczyła bazę, która może być bazą żywą. Nie wolno jej dotknąć."""


def guard_isolated(db_settings: dict | None = None, environ=None) -> None:
    """Odmawia, jeśli baza ``default`` może być bazą produkcyjną.

    Cztery warunki, każdy osobno wystarczający do odmowy – bo każdy chroni przed inną pomyłką:
    brak ``RESTORE_CHECK_ISOLATED=1`` (ktoś wywołał ``verify`` ręcznie w kontenerze ``web``),
    nazwa bazy bez przedrostka ``restorecheck_`` i nazwa równa ``POSTGRES_DB`` (zły
    ``DATABASE_URL``), host ``db`` (sieć compose zamiast tymczasowej). ``verify`` jest i tak
    tylko do odczytu, ale test, który przypadkiem liczy żywą bazę, zgłasza „kopia w porządku”
    bez otwarcia kopii – i to jest szkoda gorsza od zapisu.
    """
    environ = os.environ if environ is None else environ
    db = db_settings if db_settings is not None else settings.DATABASES["default"]
    name = str(db.get("NAME") or "")
    host = str(db.get("HOST") or "")
    reasons = []
    if environ.get("RESTORE_CHECK_ISOLATED") != "1":
        reasons.append("brak RESTORE_CHECK_ISOLATED=1")
    if not name.startswith(RESTORE_DB_PREFIX):
        reasons.append(f"nazwa bazy bez przedrostka {RESTORE_DB_PREFIX}")
    live_name = environ.get("POSTGRES_DB", "")
    if live_name and name == live_name:
        reasons.append("nazwa bazy równa POSTGRES_DB")
    if host in (LIVE_DB_HOST, ""):
        reasons.append(f"host bazy „{host or '(pusty)'}” – to może być baza żywa")
    if reasons:
        raise NotIsolated("; ".join(reasons))


# --- liczności -------------------------------------------------------------------------------


def _key_tables() -> dict[str, str]:
    """Etykieta modelu → tabela, dla modeli kluczowych obecnych w tej instalacji."""
    from django.apps import apps

    tables = {}
    for label in KEY_MODELS:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        tables[label] = model._meta.db_table
    return tables


def _count(table: str) -> int | None:
    """``count(*)`` tabeli albo ``None``, gdy jej nie ma. Surowy SQL – menedżery bywają zawężone konkursem."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {connection.ops.quote_name(table)}")  # noqa: S608
            return int(cursor.fetchone()[0])
    except Exception:  # noqa: BLE001 - brak tabeli w odtworzonej bazie jest wynikiem, nie awarią
        logger.info("Test odtwarzania: nie udało się policzyć %s.", table, exc_info=True)
        return None


def live_counts() -> dict[str, int | None]:
    """Liczności tabel kluczowych w bazie, do której podłączona jest aplikacja (``web``)."""
    return {label: _count(table) for label, table in _key_tables().items()}


def compare_counts(restored: dict, live: dict, thresholds: Thresholds) -> tuple[str, list[str], list[str]]:
    """Werdykt widełek: (status, problemy, podsumowanie). Czysta funkcja – testowana wprost."""
    problems: list[str] = []
    summary: list[str] = []
    for label, count in restored.items():
        table = label.split(".", 1)[-1].lower()
        reference = live.get(label)
        summary.append(
            f"{table}={count if count is not None else 'brak'}/{reference if reference is not None else '?'}"
        )
        if count is None:
            problems.append(f"{label}: brak tabeli")
            continue
        if reference is None:
            # Brak odczytu żywej bazy (np. tabela dopiero co dodana migracją) – nie ma z czym
            # porównać, więc nie zgadujemy. Próg bezwzględny niżej i tak działa.
            continue
        low = reference * thresholds.min_ratio - thresholds.slack
        high = reference * thresholds.max_ratio + thresholds.slack
        if count < low or count > high:
            problems.append(f"{label}: {count} wobec {reference} w bazie żywej")
    users = restored.get("accounts.User")
    if users is not None and users < MIN_USERS:
        problems.append(f"accounts.User: {users} < {MIN_USERS}")
    return (CHECK_FAIL if problems else CHECK_OK), problems, summary


def check_row_counts(live: dict, thresholds: Thresholds) -> dict:
    restored = {label: _count(table) for label, table in _key_tables().items()}
    status, problems, summary = compare_counts(restored, live or {}, thresholds)
    detail = "; ".join(problems) if problems else " ".join(summary)
    return _check("row_counts", status, detail) | {"counts": restored}


# --- migracje --------------------------------------------------------------------------------


def migration_verdict(*, pending: list, unknown: list, deployed_after_backup: bool) -> tuple[str, str]:
    """Werdykt migracji z dwóch list i jednego faktu (czy wdrożenie było po zrzucie).

    Brak migracji, które działająca wersja kodu **już miała** w chwili zrzutu, znaczy, że zrzut
    nie jest zrzutem tej bazy (inna instalacja, zrzut w połowie migracji) – porażka. Te same
    braki po wdrożeniu, które nastąpiło **po** zrzucie, są stanem oczekiwanym: po odtworzeniu
    ``migrate`` je dokończy (entrypoint ``web`` robi to sam) – ostrzeżenie.
    Migracje znane bazie, a nieznane kodowi, to najczęściej wycofanie wersji albo stare wpisy po
    scaleniu migracji (``squashmigrations``) – ostrzeżenie, nie porażka.
    """
    notes = []
    status = CHECK_OK
    if pending:
        sample = ", ".join(pending[:3]) + ("…" if len(pending) > 3 else "")
        if deployed_after_backup:
            status = CHECK_WARN
            notes.append(f"{len(pending)} migracji wdrożonych po zrzucie ({sample}) – dokończy je migrate")
        else:
            status = CHECK_FAIL
            notes.append(
                f"brak {len(pending)} migracji, które wersja działająca miała już przy zrzucie ({sample})"
            )
    if unknown:
        sample = ", ".join(unknown[:3]) + ("…" if len(unknown) > 3 else "")
        if status == CHECK_OK:
            status = CHECK_WARN
        notes.append(f"{len(unknown)} migracji nieznanych tej wersji kodu ({sample})")
    return status, "; ".join(notes) or "zgodne z wdrożoną wersją"


def check_migrations(deployed_after_backup: bool) -> dict:
    from django.db.migrations.exceptions import InconsistentMigrationHistory
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connection)
    except Exception as exc:  # noqa: BLE001 - np. brak tabeli django_migrations
        return _check(
            "migrations", CHECK_FAIL, f"nie da się odczytać historii migracji ({type(exc).__name__})"
        )
    loader = executor.loader
    if not loader.applied_migrations:
        return _check("migrations", CHECK_FAIL, "tabela django_migrations jest pusta albo jej nie ma")
    try:
        loader.check_consistent_history(connection)
    except InconsistentMigrationHistory as exc:
        return _check("migrations", CHECK_FAIL, f"niespójna historia migracji: {exc}")
    plan = executor.migration_plan(loader.graph.leaf_nodes())
    pending = [f"{migration.app_label}.{migration.name}" for migration, backwards in plan if not backwards]
    # „Znane” to także migracje zastąpione przez scalone (``replaces``, np. początkowe migracje
    # Wagtaila): ich wiersze zostają w django_migrations na zawsze, a w grafie już ich nie ma.
    known = set(loader.graph.nodes) | set(loader.disk_migrations)
    for migration in loader.replacements.values():
        known.update(migration.replaces)
    unknown = sorted(
        f"{app}.{name}"
        for app, name in loader.applied_migrations
        if app in loader.migrated_apps and (app, name) not in known
    )
    status, detail = migration_verdict(
        pending=pending, unknown=unknown, deployed_after_backup=deployed_after_backup
    )
    return _check("migrations", status, detail) | {"applied": len(loader.applied_migrations)}


# --- integralność ----------------------------------------------------------------------------


def _app_models():
    from django.apps import apps

    for model in apps.get_models():
        meta = model._meta
        if meta.proxy or not meta.managed or meta.swapped:
            continue
        yield model


def check_models_readable() -> dict:
    """Każdy model aplikacji czyta z odtworzonej bazy wiersz po **wszystkich** swoich kolumnach.

    To jest sprawdzenie zgodności schematu zrzutu z kodem wdrożonej wersji: kolumna dodana w kodzie,
    a nieobecna w zrzucie (albo odwrotnie: typ, którego ``from_db_value`` nie przełknie), wychodzi
    tu, a nie przy pierwszym żądaniu po odtworzeniu. Koszt: jedno ``SELECT … LIMIT 1`` na model.
    """
    broken = []
    total = 0
    for model in _app_models():
        total += 1
        try:
            list(model._base_manager.order_by()[:1])
        except Exception as exc:  # noqa: BLE001 - każdy błąd to wynik, zbieramy wszystkie
            broken.append(f"{model._meta.label} ({type(exc).__name__})")
    if broken:
        sample = ", ".join(broken[:5]) + ("…" if len(broken) > 5 else "")
        return _check("models_readable", CHECK_FAIL, f"{len(broken)}/{total} modeli nieczytelnych: {sample}")
    return _check("models_readable", CHECK_OK, f"{total} modeli")


def check_sequences() -> dict:
    """Sekwencje kluczy głównych nie mogą być za ``max(id)``.

    ``pg_dump`` zapisuje ich stan osobno od danych (``setval``); zrzut, w którym tego zabrakło,
    odtwarza się bez błędu, a pierwszy zapis po odtworzeniu kończy się naruszeniem klucza
    głównego – czyli platforma „działa”, dopóki nikt nie spróbuje oddać pracy.
    """
    behind = []
    checked = 0
    with connection.cursor() as cursor:
        for model in _app_models():
            pk = model._meta.pk
            if pk is None or pk.get_internal_type() not in ("AutoField", "BigAutoField", "SmallAutoField"):
                continue
            table, column = model._meta.db_table, pk.column
            try:
                cursor.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)", [connection.ops.quote_name(table), column]
                )
                sequence = cursor.fetchone()[0]
                if not sequence:
                    continue
                cursor.execute(f"SELECT last_value, is_called FROM {sequence}")  # noqa: S608 - nazwa z katalogu Postgresa
                last_value, is_called = cursor.fetchone()
                cursor.execute(
                    f"SELECT max({connection.ops.quote_name(column)}) FROM {connection.ops.quote_name(table)}"  # noqa: S608
                )
                top = cursor.fetchone()[0]
            except Exception:  # noqa: BLE001, S112 - brak tabeli zgłasza models_readable
                continue
            checked += 1
            next_value = last_value + 1 if is_called else last_value
            if top is not None and next_value <= top:
                behind.append(f"{table} (następny {next_value}, max {top})")
    if behind:
        return _check("sequences", CHECK_FAIL, "sekwencje za max(id): " + ", ".join(behind[:5]))
    return _check("sequences", CHECK_OK, f"{checked} sekwencji")


def check_superuser() -> dict:
    from django.contrib.auth import get_user_model

    try:
        exists = get_user_model()._base_manager.filter(is_superuser=True, is_active=True).exists()
    except Exception as exc:  # noqa: BLE001
        return _check("superuser", CHECK_WARN, f"nie da się sprawdzić ({type(exc).__name__})")
    if exists:
        return _check("superuser", CHECK_OK)
    return _check(
        "superuser", CHECK_WARN, "brak aktywnego superużytkownika – po odtworzeniu: createsuperuser"
    )


# --- Fernet ----------------------------------------------------------------------------------


def _encrypted_columns() -> list[tuple[str, str]]:
    """(tabela, kolumna) każdego pola ``EncryptedTextField`` w instalacji."""
    from apps.delegation_logistics.crypto import EncryptedTextField

    columns = []
    for model in _app_models():
        for field in model._meta.concrete_fields:
            if isinstance(field, EncryptedTextField):
                columns.append((model._meta.db_table, field.column))
    return columns


def _sample_tokens(limit_per_column: int = 1) -> list[str]:
    from apps.delegation_logistics.crypto import PREFIX

    tokens = []
    with connection.cursor() as cursor:
        for table, column in _encrypted_columns():
            try:
                cursor.execute(
                    f"SELECT {connection.ops.quote_name(column)} FROM {connection.ops.quote_name(table)} "  # noqa: S608
                    f"WHERE {connection.ops.quote_name(column)} LIKE %s LIMIT %s",
                    [PREFIX + "%", limit_per_column],
                )
                tokens.extend(row[0] for row in cursor.fetchall())
            except Exception:  # noqa: BLE001, S112 - brak tabeli zgłasza models_readable
                continue
    return tokens


def fernet_verdict(tokens: list[str]) -> tuple[str, str]:
    """Czy szyfrogramy z kopii dają się odszyfrować kluczami **tej** instalacji.

    Ścisłe odszyfrowanie (``MultiFernet.decrypt``, z wyjątkiem), a nie ``crypto.decrypt``, które
    po błędzie oddaje pusty napis – dla ekranu to właściwa łagodność, dla testu kopii – ślepota.
    Odszyfrowana wartość nie wychodzi z tej funkcji: liczy się wyłącznie „udało się / nie”.
    """
    from cryptography.fernet import InvalidToken

    from apps.delegation_logistics import crypto

    if not tokens:
        return CHECK_SKIP, "brak zaszyfrowanych wierszy"
    current = crypto._fernet_for(settings.SECRET_KEY)
    multi = crypto._multi()
    failed = fallback_only = 0
    for token in tokens:
        raw = token[len(crypto.PREFIX) :].encode("ascii")
        try:
            current.decrypt(raw)
            continue
        except InvalidToken, ValueError:
            pass
        try:
            multi.decrypt(raw)
            fallback_only += 1
        except InvalidToken, ValueError:
            failed += 1
    if failed:
        return (
            CHECK_FAIL,
            f"{failed}/{len(tokens)} szyfrogramów nie odszyfrowuje się kluczami aplikacji "
            "(SECRET_KEY / SECRET_KEY_FALLBACKS)",
        )
    if fallback_only:
        return CHECK_WARN, f"{fallback_only}/{len(tokens)} szyfrogramów tylko kluczem z SECRET_KEY_FALLBACKS"
    return CHECK_OK, f"{len(tokens)} szyfrogramów, bieżący SECRET_KEY"


def check_fernet() -> dict:
    status, detail = fernet_verdict(_sample_tokens())
    return _check("fernet", status, detail)


# --- pliki -----------------------------------------------------------------------------------

#: Katalog kubełka w paczce plików (``backup.sh``: ``tar -C buckets .``).
SUBMISSIONS_BUCKET_DIR = "submissions"
PUBLIC_BUCKET_DIR = "public-media"
#: Wyłączone z kopii nocnej (``backup.sh``, krok 2) – nie wolno ich szukać w paczce.
EXCLUDED_PREFIXES = ("workshop-materials/",)


def normalize_listing(lines) -> set[str]:
    """Wpisy ``tar -tf`` (``./submissions/a/b.pdf``) jako ``submissions/a/b.pdf``, bez katalogów."""
    entries = set()
    for line in lines:
        entry = line.strip()
        if not entry or entry.endswith("/"):
            continue
        entries.add(entry[2:] if entry.startswith("./") else entry)
    return entries


def _sample_media(limit: int) -> list[str]:
    """Losowa próbka ścieżek ``<kubełek>/<klucz>`` plików, które **muszą** być w paczce."""
    from apps.submissions.models import AvStatus, SubmissionFile

    paths: list[str] = []
    try:
        keys = (
            SubmissionFile._base_manager.filter(av_status=AvStatus.CLEAN)
            .order_by("?")
            .values_list("object_key", flat=True)[:limit]
        )
        paths.extend(f"{SUBMISSIONS_BUCKET_DIR}/{key}" for key in keys)
    except Exception:  # noqa: BLE001 - brak tabeli zgłasza models_readable
        logger.info("Test odtwarzania: próbka plików prac nieudana.", exc_info=True)
    try:
        from wagtail.documents import get_document_model
        from wagtail.images import get_image_model

        for model in (get_image_model(), get_document_model()):
            names = model._base_manager.order_by("?").values_list("file", flat=True)[: max(1, limit // 2)]
            paths.extend(f"{PUBLIC_BUCKET_DIR}/{name}" for name in names if name)
    except Exception:  # noqa: BLE001 - jak wyżej
        logger.info("Test odtwarzania: próbka mediów CMS nieudana.", exc_info=True)
    return [path for path in paths if not path.split("/", 1)[-1].startswith(EXCLUDED_PREFIXES)]


def media_verdict(sample: list[str], listing: set[str] | None, thresholds: Thresholds) -> tuple[str, str]:
    if listing is None:
        return CHECK_FAIL, "brak listy obiektów z paczki plików"
    if not sample:
        return CHECK_SKIP, "brak plików w bazie"
    missing = [path for path in sample if path not in listing]
    allowed = max(1, int(len(sample) * thresholds.media_max_missing_ratio))
    if len(missing) > allowed:
        return (
            CHECK_FAIL,
            f"brak {len(missing)}/{len(sample)} plików z próbki w paczce "
            f"(np. {missing[0].split('/', 1)[0]}/…)",
        )
    if missing:
        return CHECK_WARN, f"brak {len(missing)}/{len(sample)} plików z próbki (w granicy {allowed})"
    return CHECK_OK, f"{len(sample)} plików z próbki w paczce"


def check_media(listing: set[str] | None, thresholds: Thresholds) -> dict:
    status, detail = media_verdict(_sample_media(thresholds.media_sample), listing, thresholds)
    return _check("media_sample", status, detail)


# --- wiek kopii ------------------------------------------------------------------------------

_STAMP = re.compile(r"(\d{8}T\d{6}Z)")


def backup_moment(backup: dict) -> datetime | None:
    """Chwila zrzutu: znacznik z nazwy paczki (UTC), a bez niego – czas modyfikacji pliku."""
    match = _STAMP.search(str(backup.get("name") or ""))
    if match:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    mtime = backup.get("mtime_epoch")
    if mtime:
        return datetime.fromtimestamp(float(mtime), tz=UTC)
    return None


def parse_docker_time(value: str | None) -> datetime | None:
    """``2026-10-04T03:20:11.123456789Z`` (Docker, nanosekundy) jako ``datetime`` ze strefą."""
    if not value:
        return None
    match = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?", value.strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    fraction = (fraction or "")[:7]  # kropka + mikrosekundy
    zone = "+00:00" if zone in (None, "Z") else zone
    try:
        return datetime.fromisoformat(f"{base}{fraction}{zone}")
    except ValueError:
        return None


def check_backup_age(backup: dict, thresholds: Thresholds, now: datetime) -> tuple[dict, float | None]:
    moment = backup_moment(backup)
    if moment is None:
        return _check("backup_age", CHECK_FAIL, "nie da się ustalić wieku kopii"), None
    age = (now - moment).total_seconds() / 3600
    detail = f"{age:.1f} h (próg {thresholds.max_backup_age_hours:g} h)"
    if age > thresholds.max_backup_age_hours:
        return _check("backup_age", CHECK_FAIL, "kopia za stara: " + detail), age
    return _check("backup_age", CHECK_OK, detail), age


# --- całość ----------------------------------------------------------------------------------


def _enter_read_only() -> None:
    """Sesja tylko do odczytu – druga linia obrony obok :func:`guard_isolated`."""
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")


def verify(header: dict, listing: set[str] | None, *, now: datetime | None = None, thresholds=None) -> dict:
    """Komplet sprawdzeń odtworzonej bazy i wynik w kształcie dokumentu JSON (wersja 1).

    ``header`` przychodzi od skryptu: paczka (nazwa, rozmiar, czas modyfikacji), czasy kroków
    powłokowych, liczności bazy żywej, stan paczki plików, sprawdzenia dj. i chwila utworzenia
    działającego kontenera ``web`` (do werdyktu migracji). ``listing`` – obiekty z paczki plików
    albo ``None``, gdy paczki nie ma lub jest nieczytelna.
    """
    from apps.web.context_processors import APP_VERSION

    now = now or timezone.now()
    thresholds = thresholds or Thresholds.from_env()
    started = time.monotonic()
    backup = dict(header.get("backup") or {})
    checks: list[dict] = []

    age_check, age_hours = check_backup_age(backup, thresholds, now)
    checks.append(age_check)
    if age_hours is not None:
        backup["age_hours"] = round(age_hours, 2)

    moment = backup_moment(backup)
    web_created = parse_docker_time(header.get("web_created_at"))
    deployed_after_backup = bool(moment and web_created and web_created > moment)

    checks.append(check_migrations(deployed_after_backup))
    checks.append(check_row_counts(header.get("live_counts") or {}, thresholds))
    checks.append(check_models_readable())
    checks.append(check_sequences())
    checks.append(check_superuser())
    checks.append(check_fernet())

    files_status = header.get("files_status") or "missing"
    if files_status == "ok":
        checks.append(
            _check("files_archive", CHECK_OK, f"{header.get('files_entries', len(listing or ()))} wpisów")
        )
    else:
        label = {"missing": "brak paczki plików z tej nocy", "unreadable": "paczka plików nieczytelna"}
        checks.append(_check("files_archive", CHECK_FAIL, label.get(files_status, files_status)))
    checks.append(check_media(listing if files_status == "ok" else None, thresholds))
    checks.extend(
        _check(item.get("name", "extra"), item.get("status", CHECK_FAIL), item.get("detail", ""))
        for item in header.get("extra_checks") or []
    )

    timings = dict(header.get("timings") or {})
    timings["checks_s"] = round(time.monotonic() - started, 2)
    # Czas całego przebiegu od startu skryptu (zegar hosta = zegar kontenera): to jest liczba,
    # z której bierze się RTO w docs/OPERACJE.md § 43.4 – rozszyfrowanie, odtworzenie, pliki i sprawdzenia.
    try:
        timings["total_s"] = round(time.time() - float(header["script_started_epoch"]), 1)
    except KeyError, TypeError, ValueError:
        pass
    return build_result(
        checks=checks,
        backup=backup,
        timings=timings,
        started_at=header.get("started_at"),
        pg_image=header.get("pg_image", ""),
        app_version=APP_VERSION,
        now=now,
    )


def build_result(*, checks, backup, timings, started_at=None, pg_image="", app_version="", now=None) -> dict:
    now = now or timezone.now()
    failed = [item["name"] for item in checks if item["status"] == CHECK_FAIL]
    return {
        "version": RESULT_VERSION,
        "status": STATUS_FAILED if failed else STATUS_OK,
        "failed": failed,
        "warnings": [item["name"] for item in checks if item["status"] == CHECK_WARN],
        "started_at": started_at or now.isoformat(),
        "finished_at": now.isoformat(),
        "backup": backup,
        "timings": timings,
        "checks": checks,
        "app_version": app_version,
        "pg_image": pg_image,
    }


def failure_result(
    step: str, detail: str, *, backup: dict | None = None, timings: dict | None = None
) -> dict:
    """Wynik przebiegu przerwanego przed sprawdzeniami (złe hasło, ``pg_restore``, brak kopii…)."""
    from apps.web.context_processors import APP_VERSION

    return build_result(
        checks=[_check(step, CHECK_FAIL, detail)],
        backup=backup or {},
        timings=timings or {},
        app_version=APP_VERSION,
    )


# --- wynik w aplikacji -----------------------------------------------------------------------


def summary(result: dict) -> str:
    """Jedno zdanie dla notatki, listu i audytu – nazwy sprawdzeń i szczegóły nieudanych."""
    name = (result.get("backup") or {}).get("name") or "?"
    if result.get("status") == STATUS_OK:
        total = (result.get("timings") or {}).get("total_s")
        extra = f", {total:g} s" if isinstance(total, int | float) else ""
        warn = f", ostrzeżenia: {', '.join(result.get('warnings') or [])}" if result.get("warnings") else ""
        return f"test odtwarzania OK: {name}{extra}{warn}"
    details = [
        f"{item['name']}: {item.get('detail') or 'nieudane'}"
        for item in result.get("checks") or []
        if item.get("status") == CHECK_FAIL
    ]
    return f"test odtwarzania NIEUDANY ({name}) – " + "; ".join(details)


def record(result: dict, *, send_alert: bool = True) -> None:
    """Zapisuje wynik: cache, znacznik ``backup`` (tylko ``ok``), audyt, list przy porażce."""
    from apps.core import alerts, backup

    from .backup import STATE_TTL_SECONDS

    text = summary(result)
    cache.set(LAST_RESULT_KEY, json.dumps(result, ensure_ascii=False), STATE_TTL_SECONDS)
    if result.get("status") == STATUS_OK:
        backup.record(verified=True, note=text)
    else:
        backup.record(note=text)
    _audit(result)
    if send_alert and result.get("status") != STATUS_OK:
        alerts.send([failure_alert(result)])


def failure_alert(result: dict):
    from apps.core.alerts import Alert

    return Alert(key=ALERT_KEY, title="test odtwarzania kopii zapasowej NIEUDANY", detail=summary(result))


def _audit(result: dict) -> None:
    """Wpis audytu – historia testów w bazie. Jak ``alerts._audit``: wiersz wprost, bez obiektu."""
    from apps.core.models import AuditLog

    backup_info = result.get("backup") or {}
    try:
        AuditLog.objects.create(
            actor=None,
            action=AUDIT_ACTION,
            target_type="core.backup",
            target_id=str(backup_info.get("name") or "")[:64],
            diff={
                "status": result.get("status"),
                "failed": result.get("failed") or [],
                "warnings": result.get("warnings") or [],
                "timings": result.get("timings") or {},
                "age_hours": backup_info.get("age_hours"),
            },
        )
    except Exception:  # noqa: BLE001 - wynik testu nie może zależeć od audytu
        logger.warning("Test odtwarzania: nie udało się zapisać audytu.", exc_info=True)


def last_result() -> dict | None:
    try:
        raw = cache.get(LAST_RESULT_KEY)
    except Exception:  # noqa: BLE001 - brak cache'u nie może wywrócić /healthz/
        logger.warning("Test odtwarzania: nie udało się odczytać wyniku.", exc_info=True)
        return None
    if not raw:
        return None
    try:
        result = json.loads(raw)
    except TypeError, ValueError:
        return None
    return result if isinstance(result, dict) else None


def level(result: dict | None = None, now: datetime | None = None) -> str:
    """``ok|failed|stale|unknown`` – jedyne, co o teście widać publicznie (``/healthz/``, ``/status.json``).

    ``stale``: ostatni wynik był udany, ale starszy niż próg testu (``backup.MAX_VERIFY_AGE_HOURS``),
    czyli test przestał chodzić. ``failed`` wygrywa z wiekiem: zepsuta kopia sprzed dwóch dni nie
    staje się „nieświeża”, tylko dalej jest zepsuta.
    """
    from apps.core.backup import MAX_VERIFY_AGE_HOURS

    result = last_result() if result is None else result
    if not result:
        return LEVEL_UNKNOWN
    if result.get("status") != STATUS_OK:
        return LEVEL_FAILED
    finished = parse_docker_time(result.get("finished_at"))
    now = now or timezone.now()
    if finished is None or now - finished > timedelta(hours=MAX_VERIFY_AGE_HOURS):
        return LEVEL_STALE
    return LEVEL_OK
