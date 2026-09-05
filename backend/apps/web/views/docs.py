"""Swagger UI (``/api/docs/``) pod ostrą polityką CSP.

Domyślny szablon drf-spectacular wstawia trzy znaczniki ``<script>`` bez ``nonce``, w tym jeden
inline (konfiguracja ``SwaggerUIBundle``). Przy naszej polityce – ``script-src`` bez
``'unsafe-inline'`` – przeglądarka blokowała je wszystkie i ``/api/docs/`` była pustą stroną.

Rozwiązanie jest w dwóch kawałkach:

- ``backend/templates/drf_spectacular/swagger_ui.html`` przesłania szablon biblioteki (nasz
  katalog ``templates`` stoi w ``TEMPLATES["DIRS"]``, czyli **przed** szablonami aplikacji)
  i dokleja ``nonce`` oraz ``integrity``/``crossorigin`` do każdego znacznika,
- ta klasa dokłada do kontekstu skróty SRI z ``settings.SWAGGER_UI_SRI``. Widok biblioteki
  podaje szablonowi wyłącznie adresy plików; hashe muszą przyjść z konfiguracji, bo to tam
  pinowana jest wersja (``SWAGGER_UI_VERSION``) i tylko tam da się je utrzymać razem.

Ścieżka ``/api/docs/`` celowo **nie** wchodzi do ``ADMIN_PATH_PREFIXES``: to jest strona API,
nie panel biblioteki, której szablonów nie kontrolujemy – i po tej zmianie kontrolujemy.
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.views import SpectacularSwaggerView


class NonceSwaggerView(SpectacularSwaggerView):
    """Swagger UI z nonce'ami i SRI. Reszta zachowania bez zmian."""

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # ``response.data`` to jeszcze surowy słownik kontekstu – renderowanie odbywa się później,
        # przy ``response.render()``, więc dopisanie klucza tutaj jest bezpieczne.
        response.data["sri"] = dict(getattr(settings, "SWAGGER_UI_SRI", {}) or {})
        return response
