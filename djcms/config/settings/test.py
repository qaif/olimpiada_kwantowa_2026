"""Ustawienia testów djcms (``pytest``; ``--ds=config.settings.test`` w ``pyproject.toml``).

Klient API aplikacji głównej jest w testach zawsze zamockowany – żaden test nie łączy się
z ``web``. Baza: ``DATABASE_URL`` (w kontenerze rola ``olimpiada_djcms`` z ``CREATEDB`` nadanym
przez ``scripts/djcms_db.sh --allow-createdb``; w CI usługa postgres joba ``djcms``).
"""

import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "djcms-test-only-key-not-a-secret-0123456789-abcdefghijklmnopqrstuvwxyz"  # noqa: S105
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Bufor w pamięci procesu – testy czyszczą go między sobą (``conftest.py``).
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "djcms-test-default"},
}
# Tokeny i adresy API stałe, niezależne od środowiska, w którym ktoś uruchomił testy.
DJCMS_INTERNAL_TOKEN = "t" * 48
DJCMS_MAIN_API_URL = "http://web:8000/internal/djcms/v2/"
DJCMS_MAIN_MEDIA_ORIGIN = "https://s3.olimpiada.example"
# Hosty konkursów w testach wielu witryn: ``testserver`` (konkurs z fixture'a), ``*.olimpiada.example``
# i domena „własna” (``fizyka.example``).
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "djcms", ".olimpiada.example", "fizyka.example"]
# Bez odświeżania leniwego rejestru: testy ustawiają witryny wprost (``conftest.py``), a odświeżenie
# pytałoby (zamockowane) API o listę konkursów przy każdym pierwszym żądaniu testu i otwierało
# bezpiecznik. Testy odświeżania włączają je same.
DJCMS_SITES_REFRESH_SECONDS = 0
TRUSTED_PROXY_IPS = ["172.30.1.0/24"]
# Testy nie robią ``collectstatic``, a WhiteNoise ostrzega przy każdym starcie middleware, gdy katalogu
# ``STATIC_ROOT`` nie ma. Pliki (gdyby test ich potrzebował) biorą się z finderów.
STATIC_ROOT = None
WHITENOISE_USE_FINDERS = True
# Pliki testów poza ``/app/media``: w CI testy biegną na hoście runnera (``/app`` nie istnieje
# i nie da się go założyć), a w kontenerze nie zaśmiecają prawdziwego wolumenu mediów. filer
# wylicza katalog plików prywatnych (``../smedia``) z ``MEDIA_ROOT`` przy starcie, więc ustawienie
# musi paść tutaj, a nie dopiero w fixturze.
MEDIA_ROOT = env("DJCMS_TEST_MEDIA_ROOT", default=str(Path(tempfile.gettempdir()) / "djcms-test" / "media"))  # noqa: F405
