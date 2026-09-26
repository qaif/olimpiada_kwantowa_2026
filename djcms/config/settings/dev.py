"""Ustawienia deweloperskie djcms (``docker-compose.dev.yml``: runserver na ``localhost:8100``).

Wszystko, co różni się od produkcji, jest tutaj i tylko tutaj: jawny klucz deweloperski,
ciasteczka bez ``Secure`` (dev chodzi po HTTP) i statyki z finderów (bez ``collectstatic``).
"""

from .base import *  # noqa: F401,F403
from .base import SECRET_KEY, env

DEBUG = env.bool("DJCMS_DEBUG", default=True)
# Klucz zapasowy wyłącznie dla devu – produkcja ma własny bezpiecznik w ``production.py``.
SECRET_KEY = SECRET_KEY or "djcms-dev-only-insecure-key-do-not-use-anywhere-else-0123456789"  # noqa: S105

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
# Bez ``collectstatic``: WhiteNoise szuka plików przez findery, jak w backendzie.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
