"""Klient śledzenia błędów (``sentry-sdk`` → własny GlitchTip), OPS-02 § 2.

Wyłączony, dopóki ``SENTRY_DSN`` jest pusty – i wtedy **nie importuje** ``sentry_sdk`` wcale:
import jest wewnątrz :func:`init_sentry`, a woła ją :func:`init_from_settings` dopiero po
sprawdzeniu DSN. Instalacja bez DSN nie ładuje więc ani jednego modułu klienta, nie uruchamia
wątku wysyłki i nie łata Django/Celery/Redisa (test: ``tests/test_sentry.py``).

Gdzie jest wołane: ``MonitoringConfig.ready()``, czyli w każdym procesie, który składa Django –
``web`` (gunicorn), ``worker`` i ``beat`` (Celery woła ``django.setup()`` przy starcie) oraz
``manage.py``. Nie w ``settings``: ustawienia testowe muszą móc wyłączyć klienta (``SENTRY_DSN=""``
w ``config/settings/test.py``), zanim ktokolwiek go uruchomi – inaczej suita odpalona z ``.env``
dewelopera wysyłałaby zdarzenia z testów.
"""

from __future__ import annotations

import logging
import os

from .scrubbing import scrub_breadcrumb, scrub_event

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry(
    *,
    dsn: str,
    release: str,
    environment: str,
    sample_rate: float = 1.0,
    traces_sample_rate: float = 0.0,
    transport=None,
) -> bool:
    """Uruchamia klienta. ``transport`` – wyłącznie dla testów (nic nie wychodzi z procesu)."""
    global _initialized
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.atexit import AtexitIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.dedupe import DedupeIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.excepthook import ExcepthookIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.redis import RedisIntegration
    from sentry_sdk.integrations.stdlib import StdlibIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    options = dict(
        dsn=dsn,
        release=release or None,
        environment=environment or None,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
        # Lista integracji **zamknięta**: bez domyślnych i bez „samowłączających się”. Te drugie
        # włączyłyby się same dla każdej biblioteki w obrazie – w tym ``anthropic``, ``openai``
        # i ``google-genai`` (ocena AI), których integracje zapisują treść zapytań, czyli prace
        # uczestników. Z domyślnych świadomie pomijamy ``ArgvIntegration`` (linia poleceń procesu
        # w ``extra`` – ``manage.py … --email …``) i ``ModulesIntegration`` (lista pakietów).
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            LoggingIntegration(),
            StdlibIntegration(),
            ExcepthookIntegration(),
            DedupeIntegration(),
            AtexitIntegration(),
            ThreadingIntegration(),
            # ``middleware_spans`` i ``signals_spans`` mają sens wyłącznie przy próbkowaniu
            # transakcji; ``cache_spans`` – to samo. Domyślne wartości biblioteki zostają.
            # ``transaction_style="url"`` (domyślne, tu jawnie): nazwą transakcji jest **wzorzec
            # trasy** (``/reset/{uidb64}/{token}/``) – filtr podstawia go za ścieżkę adresu żądania.
            DjangoIntegration(transaction_style="url"),
            # ``propagate_traces=False``: nagłówek śledzenia nie jedzie w wiadomości zadania
            # (Redis jest brokerem, a my nie potrzebujemy łączenia śladów żądanie → zadanie).
            CeleryIntegration(propagate_traces=False),
            RedisIntegration(),
        ],
        # --- prywatność (OPS-02 § 2) – niezależnie od filtra w ``before_send`` -----------------
        send_default_pii=False,
        # Zmienne lokalne ramek to miejsce, w którym leżą dane paszportowe, hasła, tokeny i treść
        # formularzy – wyłączone **globalnie**, a nie per aplikacja.
        include_local_variables=False,
        max_request_body_size="never",
        attach_stacktrace=False,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        # Mniej okruszków niż domyślne 100: każdy jest kolejnym napisem do przefiltrowania, a do
        # zrozumienia błędu wystarczają ostatnie zapytania i logi tuż przed nim.
        max_breadcrumbs=50,
    )
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)
    _initialized = True
    return True


def init_from_settings() -> bool:
    """Woła :func:`init_sentry` z ustawień Django – albo nie robi nic, gdy DSN jest pusty."""
    from django.conf import settings

    dsn = (getattr(settings, "SENTRY_DSN", "") or "").strip()
    if not dsn or _initialized:
        return False
    try:
        return init_sentry(
            dsn=dsn,
            release=os.environ.get("APP_VERSION", "dev"),
            environment=getattr(settings, "SENTRY_ENVIRONMENT", "production"),
            sample_rate=float(getattr(settings, "SENTRY_SAMPLE_RATE", 1.0)),
            traces_sample_rate=float(getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0)),
        )
    except Exception:  # noqa: BLE001 - zły DSN albo brak pakietu nie może położyć serwisu
        logger.exception("Śledzenie błędów nie wystartowało (SENTRY_DSN) – serwis działa bez niego.")
        return False


def set_competition_tag(slug: str | None) -> None:
    """Tag ``competition`` (sam slug) dla bieżącego żądania – no-op bez działającego klienta."""
    if not _initialized or not slug:
        return
    import sentry_sdk

    sentry_sdk.get_isolation_scope().set_tag("competition", slug)
