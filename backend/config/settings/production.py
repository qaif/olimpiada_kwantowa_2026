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

**Poświadczenia są rozdzielone razem z bucketami.** ``default`` dostaje konto ``S3_PUBLIC_*``
(polityka MinIO obejmuje wyłącznie ``public-media``), ``private_media`` i backend rozwiązań –
konto ``S3_PRIVATE_*`` (wyłącznie ``submissions``). Sam podział aliasów storage nie byłby
zabezpieczeniem: z jednym kontem administracyjnym każdy błąd w ścieżce redakcyjnej (a tam pliki
przychodzą od człowieka) sięgałby także prac uczestników. Klucze ustawia ``minio-init``.
"""

import logging
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import (
    MAILERS,
    S3_ENDPOINT_URL,
    S3_PRIVATE_ACCESS_KEY,
    S3_PRIVATE_SECRET_KEY,
    S3_PUBLIC_ACCESS_KEY,
    S3_PUBLIC_BUCKET,
    S3_PUBLIC_ENDPOINT_URL,
    S3_PUBLIC_SECRET_KEY,
    S3_REGION,
    S3_SUBMISSIONS_BUCKET,
    STORAGES,
    env,
)

# Bezpiecznik: produkcja nie może wstać z kluczem zapasowym z repozytorium ani bez sekretów storage.
if SECRET_KEY == "insecure-dev-key-change-me" or len(SECRET_KEY) < 50:  # noqa: S105
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY musi być ustawiony (min. 50 znaków) w środowisku produkcyjnym."
    )
if not S3_ACCESS_KEY or not S3_SECRET_KEY:
    raise ImproperlyConfigured("MINIO_ROOT_USER/MINIO_ROOT_PASSWORD (lub konta serwisowe S3_*) są wymagane.")

# Poczta: ostrzeżenie, nie wyjątek. Brak SMTP wyłącza wyłącznie reset hasła (reszta systemu nie
# wysyła listów), więc nie ma powodu, żeby z tego powodu nie dało się wdrożyć aplikacji – ale musi
# to być widać w logu startowym, bo objawem jest cicho niedziałający formularz „Nie pamiętasz hasła?”.
# ``localhost:25`` to domyślne ustawienie Django, czyli „nikt tego nie skonfigurował”: w kontenerze
# aplikacyjnym nie ma MTA i połączenie skończy się odmową. Czytamy to z ``MAILERS`` (ustawienia
# ``EMAIL_*`` już nie istnieją – patrz base.py), ale wejściem nadal jest zmienna ``EMAIL_URL``
# i o niej mówi komunikat, bo to ją operator ma poprawić w ``.env``.
_default_mailer = MAILERS["default"]
_default_mailer_options = _default_mailer.get("OPTIONS", {})
_email_host = _default_mailer_options.get("host", "")
_email_port = _default_mailer_options.get("port")
if _default_mailer["BACKEND"] == "django.core.mail.backends.smtp.EmailBackend" and (
    _email_host in ("", "localhost", "127.0.0.1", "::1") or _email_port == 25
):
    logging.getLogger("config.settings").warning(
        "EMAIL_URL wskazuje %s:%s – w kontenerze aplikacyjnym nie ma MTA, więc reset hasła nie "
        "wyśle wiadomości. Ustaw EMAIL_URL=smtp+tls://uzytkownik:haslo@host:587 oraz "
        "DEFAULT_FROM_EMAIL na adres w domenie z rekordami SPF/DKIM.",
        _email_host,
        _email_port,
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
            # Konto z polityką wyłącznie na ``public-media``: Wagtail nie ma czym dosięgnąć
            # bucketu ``submissions``, nawet gdyby ktoś przestawił mu ``bucket_name``.
            "access_key": S3_PUBLIC_ACCESS_KEY or None,
            "secret_key": S3_PUBLIC_SECRET_KEY or None,
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
            # Konto z polityką wyłącznie na ``submissions`` – to samo, którego używa
            # ``apps.submissions.storage.S3SubmissionStorage``.
            "access_key": S3_PRIVATE_ACCESS_KEY or None,
            "secret_key": S3_PRIVATE_SECRET_KEY or None,
            "bucket_name": S3_SUBMISSIONS_BUCKET,
            # Osobny prefiks: klucze rozwiązań budowane są z identyfikatorów zgłoszeń (patrz
            # apps.submissions.storage), więc kolizja jest niemożliwa, a listing zostaje czytelny.
            "location": "problem-statements",
        },
    },
}
