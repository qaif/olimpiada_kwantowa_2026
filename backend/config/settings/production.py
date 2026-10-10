"""Ustawienia produkcyjne: twarde ciasteczka, nagłówki i media na S3/MinIO.

Storage (T-09):

- ``default`` → bucket ``S3_PUBLIC_BUCKET`` (``public-media``, anonimowo wyłącznie ``s3:GetObject`` –
  deploy/minio/policy-anonymous-public-media.json; do 1.10.2026 „download”, z listowaniem). Tu żyją
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
    DATABASES,
    DEBUG,
    E2E_MODE,
    MAILERS,
    S3_ENDPOINT_URL,
    S3_PRIVATE_ACCESS_KEY,
    S3_PRIVATE_SECRET_KEY,
    S3_PUBLIC_ACCESS_KEY,
    S3_PUBLIC_BUCKET,
    S3_PUBLIC_ENDPOINT_URL,
    S3_PUBLIC_SECRET_KEY,
    S3_REGION,
    S3_SECRET_KEY,
    S3_SUBMISSIONS_BUCKET,
    STORAGES,
    env,
)

# Bezpiecznik: produkcja nie może wstać z kluczem zapasowym z repozytorium ani bez sekretów storage.
if SECRET_KEY == "insecure-dev-key-change-me" or len(SECRET_KEY) < 50:  # noqa: S105
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY musi być ustawiony (min. 50 znaków) w środowisku produkcyjnym."
    )
# Wymagane są poświadczenia OBU bucketów: konta serwisowe ``S3_PUBLIC_*`` i ``S3_PRIVATE_*`` albo – na
# instalacji sprzed ich rozdzielenia – ``MINIO_ROOT_*`` (``_bucket_credentials`` w base.py schodzi na
# nie sam). Od 10.10.2026 (audyt bezpieczeństwa, W6) compose NIE przekazuje ``MINIO_ROOT_*`` do
# web/worker/beat, więc dawny warunek „jest konto root” zatrzymywałby każdą poprawnie rozdzieloną
# instalację – sprawdzamy to, czego storage naprawdę użyje.
if not (S3_PUBLIC_ACCESS_KEY and S3_PUBLIC_SECRET_KEY and S3_PRIVATE_ACCESS_KEY and S3_PRIVATE_SECRET_KEY):
    raise ImproperlyConfigured(
        "Brak poświadczeń storage: ustaw konta serwisowe S3_PUBLIC_ACCESS_KEY/S3_PUBLIC_SECRET_KEY "
        "i S3_PRIVATE_ACCESS_KEY/S3_PRIVATE_SECRET_KEY (tworzy je minio-init) albo – na instalacji "
        "sprzed ich rozdzielenia – MINIO_ROOT_USER/MINIO_ROOT_PASSWORD."
    )

# Sekrety z .env.example (audyt z 1.10.2026) – ta sama zasada co DJANGO_SECRET_KEY wyżej, więc też
# bezwarunkowo: instalacja postawiona z przykładowego pliku bez podmiany haseł ma hasło bazy i klucze
# MinIO znane z publicznego repozytorium. Wszystkie placeholdery .env.example zaczynają się od
# ``change-me`` – i tak samo DJANGO_SECRET_KEY z tego pliku (28 znaków), który bezpiecznik wyżej
# odrzuca od dawna, więc nikt nie uruchamia tego modułu (także w devie) na czystej kopii .env.example.
# Sprawdzamy to, co Django faktycznie widzi: hasło z ``DATABASE_URL`` (compose składa je
# z POSTGRES_PASSWORD), ``POSTGRES_PASSWORD`` (z ``env_file``), ``MINIO_ROOT_PASSWORD`` i sekrety
# kont serwisowych S3. Nazwy w komunikacie, wartości – nigdy.
PLACEHOLDER_SECRET_PREFIX = "change-me"
_secrets_to_check = {
    "DATABASE_URL (hasło bazy)": DATABASES["default"].get("PASSWORD") or "",
    "POSTGRES_PASSWORD": env("POSTGRES_PASSWORD", default=""),
    "MINIO_ROOT_PASSWORD": S3_SECRET_KEY,
    "S3_PUBLIC_SECRET_KEY": S3_PUBLIC_SECRET_KEY,
    "S3_PRIVATE_SECRET_KEY": S3_PRIVATE_SECRET_KEY,
}
_placeholders = sorted(
    name
    for name, value in _secrets_to_check.items()
    if str(value).strip().lower().startswith(PLACEHOLDER_SECRET_PREFIX)
)
if _placeholders:
    raise ImproperlyConfigured(
        "Sekrety z .env.example („change-me…”) w środowisku produkcyjnym: "
        + ", ".join(_placeholders)
        + ". Wygeneruj własne wartości (scripts/deploy.sh robi to przy pierwszej instalacji)."
    )

# ``E2E_MODE`` zeruje próg antyspamowy rejestracji, włącza tryb testowy CAPTCHY (odpowiedź „PASSED”)
# i odblokowuje ``manage.py e2e_timeline`` (przesuwanie terminów etapu). Ustawione na serwerze przez
# pomyłkę (skopiowany .env deweloperski) nie daje żadnego widocznego objawu – stąd odmowa startu.
# Wyłącznie przy ``DEBUG`` wyłączonym: środowisko deweloperskie też chodzi na tym module ustawień,
# z ``E2E_MODE=1`` i ``DJANGO_DEBUG=1`` dla `web` (docker-compose.dev.yml, scripts/e2e.sh), i ma
# działać dalej. ``DEBUG=1`` na serwerze jest awarią samą w sobie (strony błędów z kodem
# i ustawieniami), więc ten wyjątek nie otwiera niczego, co nie byłoby już otwarte.
if E2E_MODE and not DEBUG:
    raise ImproperlyConfigured(
        "E2E_MODE=1 przy DJANGO_DEBUG=0 – tryb scenariusza E2E (CAPTCHA w trybie testowym, zerowy próg "
        "antyspamowy, przesuwanie terminów etapu) nie może działać na produkcji. Usuń E2E_MODE z .env."
    )

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
# Prefiks ``__Host-`` (audyt bezpieczeństwa 10.10.2026, S19): przeglądarka przyjmuje takie ciasteczko
# WYŁĄCZNIE z ``Secure``, ``Path=/`` i bez ``Domain`` – czyli kod na sąsiedniej subdomenie (Jitsi
# pod ``meet.``, Uptime Kuma pod ``monitor.``, konkurs pod ``<slug>.``) nie podrzuci nam ani sesji
# (zalogowanie ofiary na konto napastnika), ani tokenu CSRF z ``Domain=.<domena>``: ciasteczka
# bez prefiksu o tej nazwie aplikacja po prostu nie czyta. Warunki są spełnione: ``Path`` domyślne
# (``/``), ``SESSION_COOKIE_DOMAIN``/``CSRF_COOKIE_DOMAIN`` nieustawione (base.py), ``Secure`` – tutaj.
# Tylko przy ``Secure``: dev po http (``SESSION_COOKIE_SECURE=0`` w .env) z prefiksem nie dostałby
# ciasteczka wcale. Zmiana nazwy = jednorazowe wylogowanie wszystkich przy wdrożeniu (stare
# ``sessionid`` nie jest już czytane) i nowy token CSRF przy pierwszym wejściu. JavaScript, który
# czyta token z ciasteczka (static/js/quiz.js, review-worklog.js), zna obie nazwy.
if SESSION_COOKIE_SECURE:
    SESSION_COOKIE_NAME = "__Host-sessionid"
if CSRF_COOKIE_SECURE:
    CSRF_COOKIE_NAME = "__Host-csrftoken"
# Ciasteczko języka (``django_language``, przełącznik języka gościa) tak jak w djcms: tylko po TLS
# i bez dostępu ze skryptu – żaden kod strony go nie czyta (preferencja wraca z serwera).
LANGUAGE_COOKIE_SECURE = SESSION_COOKIE_SECURE
LANGUAGE_COOKIE_HTTPONLY = True
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

#: ``S3Storage`` z typem pliku z rozszerzenia, a nie z nagłówka od przeglądarki, i z wymuszonym
#: pobraniem dla wszystkiego poza obrazem, filmem, dźwiękiem i PDF-em (apps/core/storage.py – audyt
#: z 1.10.2026: ``x.txt`` wgrane jako ``text/html`` wracało z ``public-media`` jako HTML).
S3_STORAGE_BACKEND = "apps.core.storage.ExtensionContentTypeS3Storage"

STORAGES = {
    **STORAGES,
    "default": {
        "BACKEND": S3_STORAGE_BACKEND,
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
        # Ta sama klasa: bucket jest prywatny (plik podaje ``ProblemStatementView``), ale zapisany typ
        # i tak nie ma pochodzić od klienta – obiekt bywa pobierany także poza aplikacją (``mc``, kopie).
        "BACKEND": S3_STORAGE_BACKEND,
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
