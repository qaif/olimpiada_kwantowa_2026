"""Ustawienia produkcyjne djcms: bez DEBUG, twarde ciasteczka, statyki z manifestem.

``collectstatic`` odbywa się w czasie budowania obrazu (``djcms/Dockerfile``) z ``DJCMS_BUILD=1`` –
tylko wtedy wolno wczytać ten plik bez sekretu. Każdy inny start bez ``DJCMS_SECRET_KEY`` kończy
się ``ImproperlyConfigured``: compose celowo nie ma ``${DJCMS_SECRET_KEY:?}`` (wywróciłoby
``docker compose config`` każdemu bez tej zmiennej, także przy wyłączonym profilu ``djcms``),
więc brak sekretu wykrywa dopiero ten plik.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import BUILDING_IMAGE, SECRET_KEY, env

DEBUG = False

if BUILDING_IMAGE:
    # Klucz tylko dla ``collectstatic`` w czasie budowania – żaden proces z tą flagą nie podpisuje
    # sesji ani tokenów, a obraz nie może nieść prawdziwego sekretu.
    SECRET_KEY = "djcms-build-only-not-a-secret"  # noqa: S105
elif len(SECRET_KEY) < 50:
    raise ImproperlyConfigured(
        "DJCMS_SECRET_KEY musi być ustawiony (min. 50 znaków) – osobny sekret djcms, nie DJANGO_SECRET_KEY."
    )

SESSION_COOKIE_SECURE = env.bool("DJCMS_SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("DJCMS_CSRF_COOKIE_SECURE", default=True)
LANGUAGE_COOKIE_SECURE = SESSION_COOKIE_SECURE

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
