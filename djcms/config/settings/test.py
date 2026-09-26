"""Ustawienia testów djcms (``pytest``; ``--ds=config.settings.test`` w ``pyproject.toml``).

Klient API aplikacji głównej jest w testach zawsze zamockowany – żaden test nie łączy się
z ``web``. Baza: ``DATABASE_URL`` (w kontenerze rola ``olimpiada_djcms`` z ``CREATEDB`` nadanym
przez ``scripts/djcms_db.sh --allow-createdb``; w CI usługa postgres joba ``djcms``).
"""

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "djcms-test-only-key-not-a-secret-0123456789-abcdefghijklmnopqrstuvwxyz"  # noqa: S105
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Oba bufory w pamięci procesu – testy czyszczą je między sobą (``conftest.py``), a plikowy
# bufor blokady logowania w ``/tmp`` przenosiłby stan między przebiegami.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "djcms-test-default"},
    "throttle": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "djcms-test-throttle",
    },
}
# Tokeny i adresy API stałe, niezależne od środowiska, w którym ktoś uruchomił testy.
DJCMS_INTERNAL_TOKEN = "t" * 48
DJCMS_MAIN_API_URL = "http://web:8000/internal/djcms/v1/"
DJCMS_MAIN_PUBLIC_URL = "https://olimpiada.example"
DJCMS_MAIN_MEDIA_ORIGIN = "https://s3.olimpiada.example"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
TRUSTED_PROXY_IPS = ["172.30.1.0/24"]
# Testy nie robią ``collectstatic``, a WhiteNoise ostrzega przy każdym starcie middleware, gdy katalogu
# ``STATIC_ROOT`` nie ma. Pliki (gdyby test ich potrzebował) biorą się z finderów.
STATIC_ROOT = None
WHITENOISE_USE_FINDERS = True
