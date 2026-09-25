from .base import *  # noqa: F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# Testy bez puli połączeń (``config.dbpool``). Każdy test i tak trzyma jedno połączenie przez cały
# czas trwania (transakcja wycofywana na końcu), więc pula nie miałaby czego współdzielić, a jej
# wątki tła przeżywałyby między testami tworzenie i kasowanie testowej bazy. Reguły doboru puli
# mają własne testy (``apps/core/tests/test_dbpool.py``), a to, że pula naprawdę działa, sprawdza
# się na stosie compose'a – docs/OPERACJE.md § 11.2.
DATABASES["default"]["OPTIONS"].pop("pool", None)  # noqa: F405 - z ``base`` przez gwiazdkę
# Wartość sprzed puli (ta sama, co w procesach Celery), żeby suita nie zmieniła zachowania razem z nią.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)  # noqa: F405
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Osobny katalog (``MEDIA_ROOT/private``), a nie ten sam co ``default``. Rozdział buckietów
    # jest produkcyjny, ale reguła „``statement_pdf`` nie idzie przez ``default``” ma się dać
    # sprawdzić skutkiem, a nie nazwą aliasu – patrz apps/cms/tests/test_security.py.
    "private_media": {"BACKEND": "apps.competitions.storage.PrivateMediaFileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Wyłączone jawnie, mimo że ``DEBUG = False`` powyżej dałoby ``base.py`` powód, żeby włączyć je
# samo (patrz komentarz przy ``PAGE_CACHE_ENABLED`` w base.py). Budżety zapytań
# (``apps/tenancy/tests/test_invariants.py``) i reszta suity mają mierzyć **kod**, a nie trafienia
# bufora – test, któremu ten cache naprawdę jest potrzebny, włącza go sam przez ``settings``.
PAGE_CACHE_ENABLED = False
# Poczta do ``django.core.mail.outbox``: testy sprawdzają treść wiadomości, a nie to, czy udało się
# otworzyć gniazdo do mailpita. Backend konsolowy z ``base.py`` niczego by nie zapisał.
#
# Przechwycenie idzie przez ``MAILERS`` – ustawienia ``EMAIL_*`` w Django 6.1 już nie działają
# (patrz base.py). Drugim, niezależnym zabezpieczeniem jest sam Django: ``setup_test_environment()``
# (``django/test/utils.py``) podmienia **każdy** alias z ``MAILERS`` na ``locmem`` na czas sesji
# testowej, a ``pytest-django`` 4.14 nie robi w tej sprawie nic własnego – woła tę funkcję
# (``pytest_django/plugin.py``, fixture ``django_test_environment``). Że obie drogi naprawdę
# prowadzą do ``mail.outbox``, sprawdza ``apps/core/tests/test_mailers_config.py``.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
# Testy nie dotykają MinIO ani sieci: pliki rozwiązań lądują pod MEDIA_ROOT (tmp_path per test).
SUBMISSION_STORAGE_BACKEND = "apps.submissions.storage.LocalSubmissionStorage"
# CAPTCHA w trybie testowym: pakiet przyjmuje odpowiedź „PASSED” niezależnie od wyzwania, więc
# test POST-uje formularz bez rozwiązywania obrazka (helper ``apps/web/tests/conftest.py``).
# Sama **obecność** pola i odrzucanie złej odpowiedzi są testowane osobno – przez podmianę tej
# flagi na ``False`` w konkretnym teście (apps/web/tests/test_antispam.py).
CAPTCHA_TEST_MODE = True
# Próg minimalnego czasu wypełniania **jawnie**, a nie z ``base.py``: nakładka developerska
# compose'a ustawia ``E2E_MODE=1`` dla usługi ``web``, a ``base.py`` w tym trybie zeruje próg –
# testy uruchamiane w tym samym kontenerze przestałyby wtedy sprawdzać tę regułę, nie mówiąc
# o tym ani słowem. Helper z ``apps/web/tests/conftest.py`` podpisuje znacznik czasu z przeszłości,
# a test „za szybko” – z teraz.
ANTISPAM_MIN_FILL_SECONDS = 3
# Throttling wyłączony w testach dwustopniowo: pusta lista klas zdejmuje throttle domyślny,
# a stawka ``None`` dla każdego scope'u neutralizuje też widoki z jawnym ``throttle_classes``.
# ``SimpleRateThrottle.allow_request`` przy ``rate is None`` wychodzi zanim dotknie zegara – dzięki
# temu test pod ``freeze_time`` nie trafia na ``SimpleRateThrottle.timer`` zamrożone przez freezegun.
#
# **Scope'y są wzięte z ``base.py``, a nie przepisane tutaj.** ``ScopedRateThrottle`` szuka stawki
# po nazwie i **brak klucza jest u niego wyjątkiem**, a nie „bez limitu”, więc scope dopisany
# w ``base.py`` i pominięty w tym pliku wywracał pięćsetką każdy test swojego widoku. Do 25.09.2026
# stała tu przepisana lista, którą każdy nowy scope musiał poprawić w drugim pliku; teraz zestaw
# jest ten sam z konstrukcji (pilnuje tego nadal
# ``apps/web/tests/test_throttle.py::test_test_settings_list_the_same_throttle_scopes_as_base``).
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": dict.fromkeys(REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
}
