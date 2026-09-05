from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Healthcheck dla compose i proxy: sprawdza bazę i redis, nie wymaga logowania."""
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
        {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok}, status=200 if ok else 503
    )
