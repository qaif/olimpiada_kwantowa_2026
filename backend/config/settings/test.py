from .base import *  # noqa: F401,F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Testy nie dotykają MinIO ani sieci: pliki rozwiązań lądują pod MEDIA_ROOT (tmp_path per test).
SUBMISSION_STORAGE_BACKEND = "apps.submissions.storage.LocalSubmissionStorage"
# Throttling wyłączony w testach dwustopniowo: pusta lista klas zdejmuje throttle domyślny,
# a stawka ``None`` dla każdego scope'u neutralizuje też widoki z jawnym ``throttle_classes``.
# ``SimpleRateThrottle.allow_request`` przy ``rate is None`` wychodzi zanim dotknie zegara – dzięki
# temu test pod ``freeze_time`` nie trafia na ``SimpleRateThrottle.timer`` zamrożone przez freezegun.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {"anon": None, "register": None, "login": None, "upload": None},
}
