"""Ustawienia wspólne. Wszystko konfigurowalne przychodzi ze zmiennych środowiskowych."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")  # noqa: S105
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "web"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Adresy (albo sieci CIDR) proxy, którym wolno podać adres klienta w nagłówku ``X-Real-IP``.
# Domyślnie pusto: bez jawnej konfiguracji audyt zapisuje wyłącznie ``REMOTE_ADDR``, bo nagłówek
# od nieznanego nadawcy jest danymi od klienta, a nie faktem (patrz apps.core.models.client_ip).
# Adresy proxy podaje orkiestrator (docker-compose ustawia podsieci sieci ``edge``/``internal``).
TRUSTED_PROXY_IPS = env.list("TRUSTED_PROXY_IPS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "django_celery_beat",
    # Wagtail (CMS in-process, PROJEKT.md 1.1). Kolejność jak w dokumentacji Wagtaila:
    # aplikacje contrib przed rdzeniem, rdzeń przed aplikacjami projektu.
    "wagtail.embeds",
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.documents",
    "wagtail.images",
    "wagtail.search",
    "wagtail.admin",
    "wagtail",
    "modelcluster",
    "taggit",
    "apps.cms",
    "apps.core",
    "apps.accounts",
    "apps.competitions",
    "apps.submissions",
    "apps.grading",
    "apps.appeals",
    "apps.results",
    "apps.web",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # CSP bez 'unsafe-inline' dla skryptów – patrz apps/web/middleware.py.
    "apps.web.middleware.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Role do nawigacji (nie do autoryzacji – ta jest w mixinach i uprawnieniach DRF).
                "apps.web.context_processors.roles",
                # Menu części informacyjnej (strony Wagtaila oznaczone „pokaż w menu”).
                "apps.cms.context_processors.cms_menu",
            ],
        },
    },
]

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://olimpiada:olimpiada@localhost:5432/olimpiada")
}
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["ATOMIC_REQUESTS"] = False

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.submissions.tasks.scan_submission_file": {"queue": "scan"},
    "apps.core.tasks.send_mail_task": {"queue": "mail"},
}
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TIMEZONE = "UTC"
CELERY_BEAT_SCHEDULE = {
    # Zamknięcie etapu po deadline: LOCKED na najnowszych wersjach + znacznik Stage.closed_at.
    "close-due-stages": {
        "task": "apps.submissions.tasks.close_due_stages",
        "schedule": 60.0,
    },
    # Po zamknięciu okna reklamacji: GRADED_PROVISIONAL → FINAL (APPEALED czeka na komisję).
    "finalize-closed-appeal-windows": {
        "task": "apps.appeals.tasks.finalize_closed_appeal_windows",
        "schedule": 300.0,
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True  # wszystkie DateTimeField w UTC; deadline'y porównywane przez timezone.now()

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
# Dev (DEBUG=1) nie robi collectstatic, więc WhiteNoise musi szukać plików przez findery,
# a manifest (hashowane nazwy) jest wtedy tylko przeszkodą – brak wpisu wywracałby szablon.
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG
STORAGES = {
    # ``default`` obsługuje media redakcyjne Wagtaila (obrazy, dokumenty) – produkcyjnie bucket
    # ``public-media`` (polityka „download”). Wszystko, co nie może być publiczne, MUSI mieć
    # jawnie wskazany inny storage – patrz alias ``private_media`` niżej.
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Media aplikacyjne, których nie wolno oddać anonimowi: treści zadań (``Problem.statement_pdf``)
    # są jawne dopiero po ``Stage.opens_at`` i serwuje je widok aplikacji, nigdy URL storage.
    "private_media": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
MEDIA_URL = "/media/"
MEDIA_ROOT = env("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Prywatny storage rozwiązań (MinIO / S3). Konfiguracja przez env; użycie w apps.submissions.
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="")
# Adres, pod którym MinIO jest widoczny dla przeglądarki uczestnika. Wewnętrzny ``S3_ENDPOINT_URL``
# (np. http://minio:9000) rozwiązuje się wyłącznie w sieci compose, więc presigned URL musi być
# podpisany hostem publicznym – podpis obejmuje nagłówek Host i nie da się go później podmienić.
S3_PUBLIC_ENDPOINT_URL = env("S3_PUBLIC_ENDPOINT_URL", default="")
S3_ACCESS_KEY = env("MINIO_ROOT_USER", default="")
S3_SECRET_KEY = env("MINIO_ROOT_PASSWORD", default="")
S3_SUBMISSIONS_BUCKET = env("S3_SUBMISSIONS_BUCKET", default="submissions")
S3_PUBLIC_BUCKET = env("S3_PUBLIC_BUCKET", default="public-media")
S3_PRESIGNED_TTL_SECONDS = env.int("S3_PRESIGNED_TTL_SECONDS", default=600)
S3_REGION = env("S3_REGION", default="us-east-1")

# Backend storage rozwiązań: S3/MinIO produkcyjnie, lokalny katalog w testach (config/settings/test.py).
SUBMISSION_STORAGE_BACKEND = env(
    "SUBMISSION_STORAGE_BACKEND", default="apps.submissions.storage.S3SubmissionStorage"
)

CLAMAV_HOST = env("CLAMAV_HOST", default="clamav")
CLAMAV_PORT = env.int("CLAMAV_PORT", default=3310)
# ``StreamMaxLength`` clamd (obraz clamav 1.4 → 100 MB). Powyżej tej wartości clamd zrywa połączenie
# w trakcie INSTREAM, co wyglądałoby jak awaria usługi i uruchamiało bezsensowne retry.
CLAMAV_STREAM_MAX_BYTES = env.int("CLAMAV_STREAM_MAX_BYTES", default=100 * 1024 * 1024)

# --- Wagtail (część informacyjna, T-09) ------------------------------------------------------
# Domena publiczna serwisu. Migracja ``apps.cms.0002`` ustawia z niej ``wagtailcore.Site``;
# późniejsze zmiany domeny robi redaktor w ``/cms/`` (Ustawienia → Witryny), nie deploy.
SITE_DOMAIN = env("SITE_DOMAIN", default="localhost")
WAGTAIL_SITE_NAME = env("WAGTAIL_SITE_NAME", default="Platforma Olimpiady")
WAGTAILADMIN_BASE_URL = env("WAGTAILADMIN_BASE_URL", default=f"https://{SITE_DOMAIN}")
# Whitelist rozszerzeń dokumentów: bez niej redaktor mógłby wrzucić do publicznego bucketu plik
# wykonywalny albo HTML (XSS z tej samej domeny, gdyby kiedyś serwować go bez pośrednictwa widoku).
WAGTAILDOCS_EXTENSIONS = ["pdf", "doc", "docx", "odt", "ods", "odp", "xls", "xlsx", "csv", "txt", "zip"]
# Podgląd i wyszukiwarka: prosty backend bazodanowy – bez dodatkowej usługi w compose.
WAGTAILSEARCH_BACKENDS = {"default": {"BACKEND": "wagtail.search.backends.database"}}
WAGTAIL_APPEND_SLASH = True
WAGTAILEMBEDS_RESPONSIVE_HTML = True

# Logowanie sesyjne interfejsu WWW (apps.web). Niezalogowany dostaje 302 na /login/?next=...
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/me/"
LOGOUT_REDIRECT_URL = "/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    # Token pierwszy: dzięki temu brak uwierzytelnienia daje 401 (nagłówek WWW-Authenticate),
    # a nie 403. Sesja nadal działa dla panelu i widoków przeglądarkowych.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "register": "10/hour",
        "login": "10/min",
        "upload": "30/hour",
    },
    "EXCEPTION_HANDLER": "apps.core.api.exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Platforma Olimpiady API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Schemat opisuje wyłącznie API platformy – wewnętrzne API edytora Wagtaila (/cms/api/) wypada.
    "PREPROCESSING_HOOKS": ["apps.core.api.exclude_admin_endpoints"],
    # Kilka modeli ma pole "status" o różnych zbiorach wartości – nazwy enumów muszą być jawne,
    # inaczej drf-spectacular generuje przypadkowe nazwy typu "StatusE62Enum".
    "ENUM_NAME_OVERRIDES": {
        "CommitteeStatusEnum": "apps.accounts.models.CommitteeStatus.choices",
        "StageEntryStatusEnum": "apps.competitions.models.StageEntryStatus.choices",
        "SubmissionStatusEnum": "apps.submissions.models.SubmissionStatus.choices",
        "AvStatusEnum": "apps.submissions.models.AvStatus.choices",
        "ReviewStatusEnum": "apps.grading.models.ReviewStatus.choices",
        "GradeMethodEnum": "apps.grading.models.GradeMethod.choices",
        "AppealStatusEnum": "apps.appeals.models.AppealStatus.choices",
        "AnonymizationEnum": "apps.results.models.Anonymization.choices",
    },
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # pliki idą strumieniem na dysk tymczasowy powyżej 2 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_TEMP_DIR = "/tmp"  # noqa: S108 - tmpfs w kontenerze

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
