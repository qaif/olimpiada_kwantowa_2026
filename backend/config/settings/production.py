"""Ustawienia produkcyjne: twarde ciasteczka, nagłówki i media na S3/MinIO.

Storage (T-09):

- ``default`` → bucket ``S3_PUBLIC_BUCKET`` (``public-media``, polityka MinIO „download”). Tu żyją
  **wyłącznie** media redakcyjne Wagtaila: obrazy i dokumenty, które i tak mają być publiczne.
  URL-e są budowane bez podpisu (``querystring_auth=False``) i wskazują ``S3_PUBLIC_ENDPOINT_URL``,
  czyli host widoczny dla przeglądarki (wewnętrzne ``http://minio:9000`` nie rozwiązuje się poza
  siecią compose),
- ``private_media`` → prefiks ``problem-statements/`` w prywatnym buckecie ``S3_SUBMISSIONS_BUCKET``.
  Trafia tu ``Problem.statement_pdf``: treść zadania jest jawna dopiero po ``Stage.opens_at``, więc
  publiczny bucket byłby wyciekiem terminu zerowego. Plik serwuje ``ProblemStatementView``
  (``FileResponse`` ze strumienia z storage), nigdy bezpośredni URL obiektu,
- bucket ``submissions`` pozostaje niedostępny dla Wagtaila: Wagtail używa wyłącznie ``default``.
"""

from urllib.parse import urlsplit

from .base import *  # noqa: F401,F403
from .base import (
    S3_ACCESS_KEY,
    S3_ENDPOINT_URL,
    S3_PUBLIC_BUCKET,
    S3_PUBLIC_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_KEY,
    S3_SUBMISSIONS_BUCKET,
    STORAGES,
    env,
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Zostaje DENY: podgląd strony w /cms/ jest jedynym miejscem, które potrzebuje ramki, a Wagtail
# nadpisuje nagłówek na SAMEORIGIN sam (``xframe_options_sameorigin_override`` w widoku podglądu).
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

_public = urlsplit(S3_PUBLIC_ENDPOINT_URL or S3_ENDPOINT_URL or "")
_s3_common = {
    "endpoint_url": S3_ENDPOINT_URL or None,
    "access_key": S3_ACCESS_KEY or None,
    "secret_key": S3_SECRET_KEY or None,
    "region_name": S3_REGION,
    "addressing_style": "path",
    "signature_version": "s3v4",
    "file_overwrite": False,
    "default_acl": None,
}

STORAGES = {
    **STORAGES,
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_s3_common,
            "bucket_name": S3_PUBLIC_BUCKET,
            # Bucket jest anonimowo czytelny, więc URL nie potrzebuje (i nie powinien mieć) podpisu:
            # podpisany link wygasa i psułby cache przeglądarki oraz proxy.
            "querystring_auth": False,
            "custom_domain": f"{_public.netloc}/{S3_PUBLIC_BUCKET}" if _public.netloc else None,
            "url_protocol": f"{_public.scheme}:" if _public.scheme else "https:",
        },
    },
    "private_media": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_s3_common,
            "bucket_name": S3_SUBMISSIONS_BUCKET,
            # Osobny prefiks: klucze rozwiązań budowane są z identyfikatorów zgłoszeń (patrz
            # apps.submissions.storage), więc kolizja jest niemożliwa, a listing zostaje czytelny.
            "location": "problem-statements",
        },
    },
}
