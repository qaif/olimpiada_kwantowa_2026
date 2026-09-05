"""Wspólne elementy API: kody błędów w stałym formacie {"code": ..., "detail": ...}."""

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(APIException):
    """Błąd domenowy z maszynowym kodem. Podklasy ustawiają code i status."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "DOMAIN_ERROR"
    default_detail = "Błąd domenowy."

    def __init__(self, detail: str | None = None, code: str | None = None, status_code: int | None = None):
        super().__init__(detail=detail or self.default_detail, code=code or self.default_code)
        self.machine_code = code or self.default_code
        if status_code is not None:
            self.status_code = status_code


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, DomainError):
        response.data = {"code": exc.machine_code, "detail": str(exc.detail)}
    elif isinstance(response.data, dict) and "code" not in response.data:
        code = getattr(exc, "default_code", "error")
        detail = response.data.get("detail", response.data)
        response.data = {"code": str(code).upper(), "detail": detail}
    return response


def exclude_admin_endpoints(endpoints, **kwargs):
    """Hook drf-spectacular: wycina z publicznego schematu wewnętrzne API paneli.

    Wagtail rejestruje pod ``/cms/api/`` własne endpointy DRF dla edytora (strony, obrazy,
    dokumenty). Nie są częścią kontraktu platformy: wymagają uprawnień redaktora, zmieniają się
    razem z wersją Wagtaila i tylko zaśmiecałyby ``/api/schema/`` (a przy okazji generowały
    ostrzeżenia W001/W002 o kolizjach ``operationId``).
    """
    return [item for item in endpoints if not item[0].startswith("/cms/")]
