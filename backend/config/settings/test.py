from .base import *  # noqa: F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Osobny katalog (``MEDIA_ROOT/private``), a nie ten sam co ``default``. Rozdział buckietów
    # jest produkcyjny, ale reguła „``statement_pdf`` nie idzie przez ``default``” ma się dać
    # sprawdzić skutkiem, a nie nazwą aliasu – patrz apps/cms/tests/test_security.py.
    "private_media": {"BACKEND": "apps.competitions.storage.PrivateMediaFileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Poczta do ``django.core.mail.outbox``: testy sprawdzają treść wiadomości, a nie to, czy udało się
# otworzyć gniazdo do mailpita. Backend konsolowy z ``base.py`` niczego by nie zapisał.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
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
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "anon": None,
        "register": None,
        "login": None,
        "upload": None,
        "password_reset": None,
        "schools": None,
    },
}
