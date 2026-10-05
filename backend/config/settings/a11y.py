"""Ustawienia przebiegu testów dostępności (A11Y-01, ``scripts/a11y.sh``, ``e2e/a11y/``).

Serwer audytu ma być **tą samą aplikacją** (te same szablony, arkusze, middleware, ta sama CSP) co
produkcja, ale ma wstawać z samą bazą Postgres – bez Redisa, MinIO, ClamAV i workera Celery. Dzięki
temu job CI odpala go w kilkadziesiąt sekund, a lokalnie nie dotyka stosu compose dewelopera.
Podmieniamy więc wyłącznie **infrastrukturę**, nigdy zachowanie widoków:

- pliki (media Wagtaila, pliki motywu) na dysku pod ``MEDIA_ROOT``, serwowane przez ``DEBUG``
  (``config/urls.py``) – adres ``/media/…`` jest tym samym originem, więc CSP zostaje co do bajtu
  polityką strony bez zewnętrznego bucketu,
- cache w pamięci procesu (``runserver`` = jeden proces), Celery w trybie ``eager``,
- skan ClamAV paczki motywu wyłączony (paczka pochodzi z repozytorium, a nie od użytkownika),
- ``E2E_MODE`` – CAPTCHA w trybie testowym i zerowy próg antyspamowy, inaczej formularz rejestracji
  nie dałby się przejść, a jego **stan z błędami** jest jednym z badanych ekranów,
- throttling wyłączony jak w ``test.py`` – kilkadziesiąt logowań z jednego adresu w minutę to
  przebieg testów, nie atak.

Moduł **nie** nadaje się do niczego poza tym przebiegiem: ``DEBUG`` jest włączone na sztywno,
a produkcja i dev compose'a i tak wskazują ``config.settings.production``.
"""

from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK, env

DEBUG = True
E2E_MODE = True
CAPTCHA_TEST_MODE = True
ANTISPAM_MIN_FILL_SECONDS = 0
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PAGE_CACHE_ENABLED = False
THEMES_AV_SCAN = False
SUBMISSION_STORAGE_BACKEND = "apps.submissions.storage.LocalSubmissionStorage"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private_media": {"BACKEND": "apps.competitions.storage.PrivateMediaFileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
MEDIA_ROOT = env("DJANGO_MEDIA_ROOT", default="/tmp/a11y-media")  # noqa: S108 - kontener jednorazowy
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
MAIL_ALLOWED_SENDER_DOMAINS = None
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": dict.fromkeys(REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
}
# Ekrany 2FA (konfiguracja, kody zapasowe, kod przy logowaniu) są badanymi ekranami; bez listy ról
# wymagających drugiego składnika – inaczej każda rola zatrzymywałaby się na konfiguracji.
TWO_FACTOR_ENABLED = True
TWO_FACTOR_REQUIRED_ROLES = []
