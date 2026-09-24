from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

from apps.core import dbconnections


def healthz(request):
    """Healthcheck dla compose i proxy: sprawdza bazę i redis, nie wymaga logowania.

    ``db_connections`` to **wyłącznie poziom** zajętości połączeń z Postgresem
    (``ok|warn|critical|unknown``, ``apps.core.dbconnections``) – adres jest publiczny (Caddy
    przepuszcza go jak każdą inną ścieżkę), więc liczby i nazwy usług zostają dla watchdoga
    i ``manage.py db_connections``. Poziom **nie wpływa na kod odpowiedzi**: ``503`` jest tu
    poleceniem dla orkiestratora („ten kontener jest chory”), a zajęte połączenia nie są chorobą
    kontenera, który o nie pyta – restart ``web`` z tego powodu zamieniłby ostrzeżenie w przerwę.
    Odczyt jest buforowany na 30 s we wspólnym cache'u, więc healthcheck co 15 s i dowolnie częste
    pukanie z zewnątrz dają najwyżej jedno zapytanie do ``pg_stat_activity`` na pół minuty.
    """
    db_ok = redis_ok = False
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            db_ok = cur.fetchone() == (1,)
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        cache.set("healthz", "1", 5)
        redis_ok = cache.get("healthz") == "1"
    except Exception:  # noqa: BLE001
        redis_ok = False
    ok = db_ok and redis_ok
    return JsonResponse(
        {
            "status": "ok" if ok else "degraded",
            "db": db_ok,
            "redis": redis_ok,
            "db_connections": dbconnections.level() if db_ok else dbconnections.LEVEL_UNKNOWN,
        },
        status=200 if ok else 503,
    )
