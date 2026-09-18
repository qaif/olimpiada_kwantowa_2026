"""Ustawienia wspólne. Wszystko konfigurowalne przychodzi ze zmiennych środowiskowych."""

import logging
from pathlib import Path

import environ
from wagtail.embeds import oembed_providers

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")  # noqa: S105
DEBUG = env.bool("DJANGO_DEBUG", default=False)
# Domeny konkursów dołożonych do platformy, rozdzielone spacjami. Ta sama zmienna, z której
# ``scripts/render_caddyfile.sh`` generuje bloki serwerowe Caddy'ego – i to jest cały powód, dla
# którego jest jedna. Dołożenie domeny musi zadziałać w trzech miejscach naraz (proxy,
# ``ALLOWED_HOSTS``, ``CSRF_TRUSTED_ORIGINS``); wpisana w dwóch z trzech daje albo 400 na każde
# żądanie, albo odmowę weryfikacji CSRF na każdym formularzu, a przyczyna wygląda za każdym razem
# inaczej (docs/UNIWERSALNY-ETAP-1.md § 2.5). Rozjazd między tą listą a bazą wykrywa
# ``manage.py check_domains``, wołane na końcu ``scripts/deploy.sh``.
#
# Wartości wpisane wprost w ``DJANGO_ALLOWED_HOSTS`` i ``DJANGO_CSRF_TRUSTED_ORIGINS`` zostają
# nietknięte i stoją na początku list – ta zmienna wyłącznie **dokłada**, żeby konfiguracja
# działającej instalacji jednokonkursowej nie zmieniła się ani o jeden wpis.
EXTRA_DOMAINS = [host for host in env("EXTRA_DOMAINS", default="").split() if host]
ALLOWED_HOSTS = list(
    dict.fromkeys(
        [*env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "web"]), *EXTRA_DOMAINS]
    )
)
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(
        [
            *env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]),
            *(f"https://{host}" for host in EXTRA_DOMAINS),
        ]
    )
)
# Czytelna strona zamiast surowego „Weryfikacja CSRF nie powiodła się”: najczęstszy powód to
# zalogowanie się na inne konto w drugiej karcie (Django wymienia wtedy token) – apps/web/views/errors.py.
CSRF_FAILURE_VIEW = "apps.web.views.errors.csrf_failure"

# Tryb scenariusza end-to-end. Odblokowuje ``manage.py e2e_timeline`` (przesunięcie osi czasu
# etapu), żeby test nie musiał czekać tygodnia na otwarcie okna reklamacji, oraz tryb testowy
# CAPTCHY, bo przeglądarka Playwrighta nie odczyta obrazka (patrz sekcja „CAPTCHA” niżej).
# Nie zmienia żadnej reguły domenowej i domyślnie jest wyłączony – w produkcji nie ustawia się
# go nigdy; ustawiony **wyłączyłby** ochronę antyspamową publicznej rejestracji.
E2E_MODE = env.bool("E2E_MODE", default=False)

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
    # ``redirects`` trzyma stare adresy stron (np. /regulamin/ po przeniesieniu dokumentów
    # pod /dokumenty/) w bazie, a nie w urlconfie – redaktor widzi je i rozszerza w /cms/.
    "wagtail.contrib.redirects",
    "wagtail.contrib.settings",
    # Tłumaczenie stron „strona po stronie” w ``/cms/`` (decyzja D19, docs/UNIWERSALNY-ETAP-2.md
    # § 1.6). Aplikacja wnosi **jeden** model bez kolumn (nośnik uprawnienia „Can submit
    # translations”) i przycisk „Translate” w liście stron. Przycisk pokazuje się wyłącznie wtedy,
    # gdy istnieje ``Locale``, na który strona nie jest jeszcze przetłumaczona – instalacja
    # z jednym językiem treści (czyli Konkurs #1) nie widzi go ani razu.
    # XLIFF (``wagtail-localize``) to osobna zależność i osobna decyzja – nie w tym etapie.
    "wagtail.contrib.simple_translation",
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
    # CAPTCHA obrazkowa publicznych formularzy rejestracji (django-simple-captcha). Aplikacja
    # wnosi model ``CaptchaStore`` (wyzwanie + odpowiedź + termin ważności) i widok obrazka –
    # wszystko w naszym procesie i naszej bazie, patrz sekcja „CAPTCHA” niżej.
    "captcha",
    "apps.cms",
    "apps.core",
    # Konkursy (wielodostępność). **Po** ``apps.cms``, bo ``Competition`` ma klucze obce do
    # ``wagtailcore.Site`` i ``wagtailimages.Image``, i **przed** ``apps.accounts``, bo to
    # członkostwa i profile uczestników będą wskazywać na konkurs, a nie odwrotnie – kolejność
    # w tej liście ma odbijać kierunek zależności.
    "apps.tenancy",
    "apps.accounts",
    # Słownik szkół ponadpodstawowych (SIO/RSPO). Po ``apps.accounts``, bo model ``School``
    # korzysta z zamkniętej listy województw zdefiniowanej przy kontach.
    "apps.schools",
    "apps.competitions",
    "apps.submissions",
    "apps.grading",
    "apps.appeals",
    # Testy online sprawdzane automatycznie. **Przed** ``apps.results``, bo to wyniki sięgają po
    # punkty z testu (``apps.quiz.services.stage_scores``), a nie odwrotnie – kolejność w tej
    # liście ma odbijać kierunek zależności, żeby dało się go z niej odczytać.
    "apps.quiz",
    "apps.results",
    # Zgłoszenia do organizatora (support desk). Osobna aplikacja, a nie model w ``apps.core``:
    # ma własny model, własne reguły i własną pocztę, a z domeną zawodów łączy ją wyłącznie
    # kontekst zgłoszenia – czyli odczyt, nigdy zapis.
    "apps.support",
    # Warstwa integracyjna: klucze API dla systemów zewnętrznych, webhooki i eksporty na zewnątrz.
    # **Po** aplikacjach domeny, bo czyta je wszystkie (edycje, wyniki, zgłoszenia), a żadna z nich
    # nie czyta jej – zależność idzie w jedną stronę i kolejność w tej liście ma to pokazywać.
    "apps.integrations",
    "apps.web",
    # Logowanie przez dostawców zewnętrznych (Google, Facebook). ``allauth.account`` jest wymagane
    # przez ``allauth.socialaccount`` (model ``EmailAddress``, adaptery) – jego **widoki** nie są
    # montowane, patrz apps/web/social_urls.py. ``django.contrib.sites`` celowo nie ma: allauth
    # wykrywa jego brak (``SITES_ENABLED``) i buduje migracje bez zależności od witryn, a jedyną
    # witryną w projekcie jest ``wagtailcore.Site``.
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.facebook",
]

AUTH_USER_MODEL = "accounts.User"

# ``ModelBackend`` zostaje jedynym backendem uwierzytelniania: logowanie hasłem robi nasz
# ``apps.web.views.public.LoginView`` (z throttlingiem), a social login nie sprawdza haseł – po
# stronie allauth kończy się wywołaniem ``django.contrib.auth.login``. Backend allauth dołożyłby
# drugą, nieobjętą naszym limitem ścieżkę logowania hasłem i dlatego go nie ma.
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CSP bez 'unsafe-inline' dla skryptów – patrz apps/web/middleware.py. Musi stać **przed**
    # WhiteNoise: WhiteNoise odpowiada na /static/… sam, nie wołając dalszych warstw, więc niżej
    # w łańcuchu nagłówek nie objąłby ani jednego pliku statycznego.
    "apps.web.middleware.ContentSecurityPolicyMiddleware",
    # Licznik odpowiedzi 5xx dla watchdoga alertów (apps/core/middleware.py). Możliwie na zewnątrz,
    # żeby zobaczyć także odpowiedzi 500 złożone z wyjątku widoku przez wewnętrzne warstwy Django –
    # te wracają tędy jak każda inna odpowiedź. **Pod** warstwą CSP, bo to ona ma kontrakt na drugie
    # miejsce w łańcuchu (musi objąć również pliki statyczne oddawane przez WhiteNoise, patrz
    # apps/web/tests/test_public.py); dla samego zliczania kodu odpowiedzi ta różnica jest bez
    # znaczenia. Warstwa niczego nie modyfikuje i nie może rzucić.
    "apps.core.middleware.ServerErrorCounterMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Język z ciasteczka albo z nagłówka ``Accept-Language``. Za sesją (czyta ją) i przed
    # ``CommonMiddleware`` (to ono przekierowuje na adres z ukośnikiem i musi już znać język) –
    # dokładnie tak, jak każe dokumentacja Django.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Konkurs żądania: ``request.competition`` i zmienna kontekstowa dla kodu, który żądania nie
    # widzi (poczta, zadania). **Za** ``AuthenticationMiddleware``, bo rozstrzygnięcie ma docelowo
    # móc zależeć od użytkownika (przełącznik konkursu przy kilku członkostwach), i **przed**
    # ``PreferencesMiddleware``, bo język domyślny konkursu jest niższym priorytetem niż wybór
    # człowieka. Przy jednym konkursie warstwa niczego nie zmienia w odpowiedzi – patrz
    # apps/tenancy/middleware.py.
    "apps.tenancy.middleware.CompetitionMiddleware",
    # Jawny wybór człowieka: język zapisany na koncie i tryb wysokiego kontrastu. **Za**
    # ``AuthenticationMiddleware`` (czyta ``request.user``) i za ``LocaleMiddleware``, którego
    # rozstrzygnięcie ma prawo nadpisać – ustawienie konta wygrywa z ustawieniem przeglądarki.
    "apps.accounts.preferences.PreferencesMiddleware",
    # Drugi składnik logowania (TOTP). **Za** ``AuthenticationMiddleware``, bo czyta
    # ``request.user``, i przed warstwami, które cokolwiek robią w imieniu zalogowanego konta.
    # Sesja po samym haśle jest tu w poczekalni: przechodzą wyłącznie adresy z listy
    # w ``apps.accounts.twofactor`` (ekran weryfikacji, wylogowanie, strona statusu).
    "apps.accounts.twofactor.TwoFactorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Wymagana przez allauth: ustawia kontekst żądania (``allauth.core.context``), z którego
    # korzystają adaptery i przepływ social login. Nie montuje żadnego adresu i nie zmienia
    # obsługi 404 – przekierowanie „/accounts/ → logowanie” włącza się dopiero, gdy istnieje
    # nazwa ``account_email`` (nie mamy jej, bo widoków allauth nie montujemy).
    "allauth.account.middleware.AccountMiddleware",
    # Na samym końcu łańcucha: warstwa działa wyłącznie na odpowiedzi 404, więc musi zobaczyć
    # ostatnie słowo widoków (Wagtail jest catch-allem w korzeniu). Dopiero gdy nikt nie umiał
    # obsłużyć adresu, sprawdzamy, czy nie jest to adres strony przeniesionej w drzewie.
    "wagtail.contrib.redirects.middleware.RedirectMiddleware",
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
                # Język interfejsu i tryb wysokiego kontrastu – atrybut ``data-contrast`` na
                # ``<html>`` i przełącznik „EN / PL” w pasku konta (apps/accounts/preferences.py).
                "apps.accounts.preferences.interface",
                # Konkurs żądania (marka w nagłówku i stopce). Przepisanie atrybutu ustawionego
                # przez ``apps.tenancy.middleware`` – bez zapytania do bazy.
                "apps.tenancy.context_processors.competition",
                # Role do nawigacji (nie do autoryzacji – ta jest w mixinach i uprawnieniach DRF).
                "apps.web.context_processors.roles",
                # Dane prezentacyjne ramy serwisu: etykieta edycji w logotypie, wersja w stopce.
                "apps.web.context_processors.site_chrome",
                # Stan rejestracji uczestników (okno ustawiane przez koordynatora). Steruje
                # wyłącznie widocznością i treścią przycisków – regułą jest bramka w serwisie.
                "apps.web.context_processors.registration",
                # Lista skonfigurowanych dostawców OAuth (Google/Facebook). Przycisk pojawia się
                # wyłącznie wtedy, gdy dostawca ma w środowisku komplet kluczy.
                "apps.web.context_processors.social_providers",
                # Menu części informacyjnej (strony Wagtaila oznaczone „pokaż w menu”).
                "apps.cms.context_processors.cms_menu",
                # Baner komunikatów organizatora pod nagłówkiem – na każdej stronie serwisu.
                # Odczyt jest z pamięci podręcznej (60 s, unieważnianej przy zapisie), bo inaczej
                # każda odsłona kosztowałaby zapytanie; panel redakcyjny baneru nie dostaje.
                "apps.cms.announcements.announcements",
                # Nazwa serwisu, hasło i dane organizatora – ``cms.SiteSettings`` edytowane
                # w ``/cms/`` (Ustawienia → Serwis). Szablony czytają je jako
                # ``settings.cms.SiteSettings``; nic z tego nie jest zaszyte w kodzie.
                "wagtail.contrib.settings.context_processors.settings",
            ],
        },
    },
]

# Komunikaty w sesji, nie w ciasteczku. Powód jest konkretny: koordynator dostaje jawny kod
# zaproszenia przez ``messages`` i przy domyślnym ``FallbackStorage`` ten kod wyjeżdżałby
# do przeglądarki w ciasteczku ``messages`` – czyli na dysk, do logów proxy i do każdego
# rozszerzenia czytającego ciasteczka. Sesja trzyma go po stronie serwera (Redis).
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://olimpiada:olimpiada@localhost:5432/olimpiada")
}
# Bez trwałych połączeń (``CONN_MAX_AGE=0``). Aplikacja chodzi pod ASGI (gunicorn + UvicornWorker),
# a Django wykonuje synchroniczne widoki w **nowym wątku na żądanie** (``ThreadSensitiveContext``).
# Trwałe połączenie jest przypięte do wątku i zamyka je tylko ``close_old_connections`` w tym samym
# wątku – wątek po żądaniu ginie, a jego połączenie zostaje otwarte aż do wygaśnięcia po stronie
# Pythona. Z ``CONN_MAX_AGE=60`` produkcja po dobie trzymała 92 bezczynne połączenia z ``web``
# i Postgres odpowiadał „too many clients already” (limit 100) – każda strona dawała 500.
# Koszt nowego połączenia do bazy w tej samej sieci compose to pojedyncze milisekundy; pula
# psycopg (``OPTIONS["pool"]``, Django 5.1) wymaga pakietu ``psycopg[pool]`` i jest w BACKLOG-u.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
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
    # Porcje komunikatu organizatora. Ta sama kolejka co pojedynczy list, bo to ta sama praca:
    # zadanie rozbija porcję na koperty i oddaje je ``send_mail_task``. Na kolejce ``default``
    # kilkadziesiąt porcji zablokowałoby workerowi resztę zadań serwisu.
    "apps.accounts.messaging.send_broadcast_chunk": {"queue": "mail"},
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
    # Konta, których adresu e-mail nikt nie potwierdził w oknie aktywacji (4 h), są kasowane –
    # inaczej blokowałyby ten adres przed ponowną rejestracją (apps/accounts/tasks.py).
    # Kwadrans, a nie minuta: opóźnienie w skasowaniu konta-widma nikogo nie boli, a przebieg
    # przegląda tabelę użytkowników.
    "purge-unactivated-accounts": {
        "task": "apps.accounts.tasks.purge_unactivated_accounts",
        "schedule": 900.0,
    },
    # Przypomnienia o terminach recenzji (apps/grading/tasks.py). Raz na dobę, a nie co godzinę:
    # recenzent ma dostać jeden list dziennie, a nie dwadzieścia cztery – częstotliwość przebiegu
    # jest tu zarazem regułą wysyłki. Godzina przebiegu wynika z momentu startu beatu; dokładna
    # pora nie ma znaczenia, bo recenzje mają terminy dzienne, nie godzinowe.
    "remind-overdue-reviews": {
        "task": "apps.grading.tasks.remind_overdue_reviews",
        "schedule": 86400.0,
    },
    # Przypomnienie o jutrzejszej rozmowie kwalifikacyjnej razem z linkiem do pokoju i do testu
    # kamery (apps/competitions/tasks.py). Raz na dobę z tego samego powodu, co wyżej: częstotliwość
    # przebiegu jest tu zarazem regułą wysyłki, a uczestnik ma dostać jeden list, nie dwadzieścia
    # cztery. Drugi list temu samemu odbiorcy blokuje ``InterviewBooking.reminder_sent_at``.
    "remind-interviews": {
        "task": "apps.competitions.tasks.remind_interviews",
        "schedule": 86400.0,
    },
    # Retencja danych osobowych (art. 5 ust. 1 lit. e RODO): konta uczestników edycji, której
    # upłynął okres retencji, są anonimizowane (apps/accounts/retention.py). Raz na dobę, bo
    # termin jest liczony w miesiącach – częstszy przebieg przesuwałby moment anonimizacji
    # o minuty i nie zmieniał niczego poza obciążeniem bazy.
    "anonymise-expired-editions": {
        "task": "apps.accounts.retention.anonymise_expired_editions",
        "schedule": 86400.0,
    },
    # Puls workera zapisywany w cache'u – z niego strona ``/status/`` czyta, czy kolejka zadań
    # w ogóle żyje (apps/core/tasks.py). Co minutę, bo próg „brak pulsu” na stronie statusu jest
    # liczony w minutach; rzadszy przebieg zamieniłby zdrowy system w okresowo „niedostępny”.
    "heartbeat": {
        "task": "apps.core.tasks.heartbeat",
        "schedule": 60.0,
    },
    # Watchdog aplikacyjny (apps/core/alerts.py): podsystemy, wolne miejsce na dysku, nieudane
    # zadania, odpowiedzi 5xx i stan kopii zapasowych → list do ``ALERT_EMAILS``. Co pięć minut,
    # bo tyle wynosi akceptowalne opóźnienie wykrycia awarii w noc przed deadline'em; częściej
    # nie ma sensu, bo wyciszenie alertu i tak trwa godzinę.
    "alerts-check": {
        "task": "apps.core.tasks.alerts_check",
        "schedule": 300.0,
    },
}

# Adresy dyżurnych, na które watchdog wysyła alarmy (przecinkami). **Pusta lista wyłącza wysyłkę**
# i to jest domyślne zachowanie: instalacja deweloperska nie ma nikogo budzić, a na produkcji
# adresy wpisuje ten, kto bierze na siebie odbieranie tych listów.
ALERT_EMAILS = env.list("ALERT_EMAILS", default=[])

# --- Logowanie dwuskładnikowe (TOTP, apps/accounts/twofactor.py) -------------------------------
# Wyłącznik główny całej funkcji. **Domyślnie wyłączony** – decyzja organizatora („autoryzacja
# 2-etapowa wyłączona”), a nie ostrożność techniczna: na tej instalacji drugiego składnika nie ma
# i nie ma go widać.
#
# Wyłączony znaczy dokładnie tyle:
#   - ``apps.accounts.twofactor.TwoFactorMiddleware`` przepuszcza **każde** żądanie bez jednego
#     zapytania do bazy – także konta, które mają już potwierdzone urządzenie. Logują się samym
#     hasłem, tak jak wszyscy,
#   - adresy ``/account/2fa/…``, ``/login/2fa/`` i reset z panelu koordynatora odpowiadają 404,
#   - w interfejsie nie ma ani jednego odnośnika do drugiego składnika,
#   - ``TWO_FACTOR_REQUIRED_ROLES`` nie znaczy nic (czyta je wyłącznie kod za tym wyłącznikiem).
#
# Czego wyłącznik **nie** robi: nie kasuje zapisanych urządzeń. Wiersze ``TwoFactorDevice``
# zostają w bazie nietknięte, więc ponowne włączenie przywraca stan sprzed wyłączenia zamiast
# kazać całemu komitetowi konfigurować aplikacje od nowa. Skasowanie ich jest osobną, świadomą
# czynnością opisaną w docs/OPERACJE.md § 5.
TWO_FACTOR_ENABLED = env.bool("TWO_FACTOR_ENABLED", default=False)

# Role, od których drugi składnik jest **wymagany**: konto z tej grupy bez potwierdzonego
# urządzenia trafia na ekran konfiguracji i nie zrobi nic innego, dopóki go nie włączy.
# Znaczenie ma wyłącznie przy ``TWO_FACTOR_ENABLED=1``.
#
# Domyślnie pusto i to nie jest ostrożność dla samej ostrożności: włączenie tego w dniu wdrożenia
# zamknęłoby koordynatorowi drogę do własnego panelu, zanim ktokolwiek zdążyłby zainstalować
# aplikację uwierzytelniającą. Kolejność jest odwrotna – najpierw komitet włącza 2FA dobrowolnie
# (ekran ``/account/2fa/``), a dopiero potem organizator domyka furtkę tą zmienną.
# Sensowna wartość produkcyjna: ``TWO_FACTOR_REQUIRED_ROLES=coordinator,reviewer,appeals``.
TWO_FACTOR_REQUIRED_ROLES = env.list("TWO_FACTOR_REQUIRED_ROLES", default=[])

# --- Poczta wychodząca -----------------------------------------------------------------------
# Jedna zmienna (``EMAIL_URL``) zamiast sześciu: dev ma ``smtp://mailpit:1025``, produkcja
# ``smtp+tls://user:haslo@host:587``. Domyślną wartością jest **konsola**, a nie SMTP na
# ``localhost:25``: Django bez konfiguracji próbuje lokalnego MTA, którego w kontenerze nie ma, więc
# pierwsza wysyłka kończyłaby się ``ConnectionRefusedError`` w środku żądania HTTP. ``consolemail``
# zawsze „działa”, a brak konfiguracji widać w logu, a nie w błędzie 500.
_email = env.email_url("EMAIL_URL", default="consolemail://")
EMAIL_BACKEND = _email["EMAIL_BACKEND"]
# ``environ`` zwraca ``None`` dla brakujących części adresu; Django oczekuje w tych ustawieniach
# łańcuchów i liczb, więc normalizujemy je tutaj, a nie w miejscu wysyłki.
EMAIL_HOST = _email.get("EMAIL_HOST") or "localhost"
EMAIL_PORT = _email.get("EMAIL_PORT") or 25
EMAIL_HOST_USER = _email.get("EMAIL_HOST_USER") or ""
EMAIL_HOST_PASSWORD = _email.get("EMAIL_HOST_PASSWORD") or ""
EMAIL_USE_TLS = bool(_email.get("EMAIL_USE_TLS"))
EMAIL_USE_SSL = bool(_email.get("EMAIL_USE_SSL"))
EMAIL_FILE_PATH = _email.get("EMAIL_FILE_PATH") or ""
# Bez limitu czasu wysyłka wisi na gnieździe tak długo, jak pozwoli sieć – a robimy ją synchronicznie
# w żądaniu POST /password-reset/, więc worker gunicorna zostałby zajęty na czas dowolnie długi.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@localhost")
# Nadawca wiadomości systemowych (``mail_admins``, raporty 500). Ten sam adres: MTA odbiorcy i tak
# sprawdza SPF dla domeny nadawcy, więc drugi, nieskonfigurowany adres tylko psułby dostarczalność.
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Dotyczy wyłącznie ``mail_admins``/``mail_managers``. Temat resetu hasła bierze nazwę serwisu
# z ``cms.SiteSettings`` (patrz ``apps.web.views.public.PasswordResetView``) – redaktor zmienia ją
# w ``/cms/`` i nie wymaga to wydania aplikacji.
EMAIL_SUBJECT_PREFIX = "[Olimpiada Kwantowa] "

# Ważność linku resetu hasła. Doba to kompromis: krócej – link umiera, zanim uczestnik zajrzy do
# skrzynki; dłużej – token leży w cudzej skrzynce pocztowej i jest ważny przez weekend.
PASSWORD_RESET_TIMEOUT = 24 * 3600

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
# Dwa języki interfejsu. Polski jest podstawowy (olimpiada jest polska, organizator jest polski
# i takie są dokumenty formalne), angielski dochodzi dla uczestników szkół z programem
# międzynarodowym i dla opiekunów spoza kraju. Etykiety są **natywne**: kto szuka swojego języka
# na liście, szuka go zapisanego po swojemu, a nie w tłumaczeniu na cudzy.
LANGUAGES = [("pl", "polski"), ("en", "English")]
# Katalogi tłumaczeń projektu (źródła ``.po`` w repozytorium, skompilowane ``.mo`` obok nich).
# Obraz ma ``gettext``, więc ``django-admin compilemessages`` działa w kontenerze – patrz README.
LOCALE_PATHS = [BASE_DIR / "locale"]
# ``i18n_patterns`` świadomie **nie** jest używane: adresy serwisu trafiają do listów, do regulaminu
# i do pism (``/me/``, ``/results/12/``, ``/zgoda/<token>/``), a prefiks języka zrobiłby z każdego
# z nich dwa adresy. Język wybiera człowiek, a wybór jedzie ciasteczkiem, sesją i – dla konta –
# wierszem ``accounts.UserPreference`` (patrz ``apps.accounts.preferences``). Dowód, że tej decyzji
# nie odwraca także wielojęzyczność **treści**: docs/UNIWERSALNY-ETAP-2.md § 1.6.2 – wielojęzyczność
# realizuje drzewo stron per ``Locale`` pod osobną domeną (``apps.tenancy.aliases``), a nie prefiks.
#
# Wielojęzyczność treści Wagtaila. Domyślnie **wyłączona**: włączenie jest decyzją operatora wpisaną
# w ``.env``, a nie skutkiem ``git pull``. Wyłączona znaczy dokładnie dzisiejszy stan – jeden
# ``Locale`` (``pl``), jedno drzewo stron, ani jednego wyboru języka w ``/cms/`` i ani jednego
# adresu więcej. Włączona nie zmienia żadnego adresu: drugie drzewo stoi pod drugą witryną
# (``tenancy.CompetitionSiteAlias``), więc ``/`` zostaje ``/``, a nie ``/pl/``.
WAGTAIL_I18N_ENABLED = env.bool("WAGTAIL_I18N_ENABLED", default=False)
# Języki **treści** to ta sama lista, co języki interfejsu: drugi komplet nazw byłby drugim
# miejscem, w którym trzeba pamiętać o dopisaniu języka, i pierwszym, w którym ktoś zapomni.
WAGTAIL_CONTENT_LANGUAGES = LANGUAGES
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
    # Lokalnie i w testach to podkatalog ``private/`` w ``MEDIA_ROOT`` – rozdział katalogów jest
    # tym samym, czym w produkcji rozdział bucketów, i tak samo daje się sprawdzić testem
    # (plik ``statement_pdf`` nie może leżeć w drzewie storage ``default``).
    "private_media": {"BACKEND": "apps.competitions.storage.PrivateMediaFileSystemStorage"},
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
# Poświadczenia administracyjne MinIO. Zostają wyłącznie jako awaryjny fallback dla instalacji
# sprzed rozdzielenia kont serwisowych – normalnie backend ich nie używa (patrz niżej).
S3_ACCESS_KEY = env("MINIO_ROOT_USER", default="")
S3_SECRET_KEY = env("MINIO_ROOT_PASSWORD", default="")


def _bucket_credentials(prefix: str, purpose: str) -> tuple[str, str]:
    """Para (klucz, sekret) konta serwisowego z polityką ograniczoną do jednego bucketu.

    Konta tworzy ``minio-init`` w compose: ``S3_PUBLIC_*`` widzi wyłącznie bucket ``public-media``,
    ``S3_PRIVATE_*`` wyłącznie ``submissions``. Rozdział jest tu po to, żeby kompromitacja
    ścieżki redakcyjnej (Wagtail przyjmuje pliki od redaktora) nie dawała dostępu do prac
    uczestników ani do treści zadań przed otwarciem etapu – i odwrotnie.

    Gdy zmiennych nie ma, schodzimy na ``MINIO_ROOT_*`` (zgodność wsteczna z instalacjami sprzed
    T-09), ale zostawiamy o tym ostrzeżenie: to konto ma dostęp do **wszystkich** bucketów.
    """
    access = env(f"{prefix}_ACCESS_KEY", default="")
    secret = env(f"{prefix}_SECRET_KEY", default="")
    if access and secret:
        return access, secret
    if S3_ACCESS_KEY:
        logging.getLogger("config.settings").warning(
            "Brak %s_ACCESS_KEY/%s_SECRET_KEY – %s używa poświadczeń administracyjnych MinIO "
            "(dostęp do wszystkich bucketów). Utwórz konto serwisowe ograniczone do jednego bucketu.",
            prefix,
            prefix,
            purpose,
        )
    return S3_ACCESS_KEY, S3_SECRET_KEY


#: Konto serwisowe bucketu ``public-media`` – media redakcyjne Wagtaila (alias ``default``).
S3_PUBLIC_ACCESS_KEY, S3_PUBLIC_SECRET_KEY = _bucket_credentials("S3_PUBLIC", "storage publiczny")
#: Konto serwisowe bucketu ``submissions`` – prace uczestników i treści zadań (``private_media``).
S3_PRIVATE_ACCESS_KEY, S3_PRIVATE_SECRET_KEY = _bucket_credentials("S3_PRIVATE", "storage prywatny")

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

# --- Strona błędu serwera (templates/500.html) -------------------------------------------------
# Adres kontaktowy pokazywany na stronie 500. Ustawienie, a nie pole konkursu: ta strona renderuje
# się **bez bazy** (i to jest jej sens), więc nie ma jak zapytać, czyj konkurs stał pod tym
# żądaniem. Domyślną wartością jest dzisiejszy adres organizatora Olimpiady Kwantowej, więc
# produkcja bez wpisu w ``.env`` wygląda dokładnie tak, jak przed etapem 2 (decyzja D14,
# ``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4). Wartość podaje szablonowi ``handler500``
# (``apps.web.views.errors.server_error``).
ERROR_PAGE_CONTACT_EMAIL = env("ERROR_PAGE_CONTACT_EMAIL", default="contact@qaif.org")

# --- Wagtail (część informacyjna, T-09) ------------------------------------------------------
# Domena publiczna serwisu. Migracja ``apps.cms.0002`` ustawia z niej ``wagtailcore.Site``;
# późniejsze zmiany domeny robi redaktor w ``/cms/`` (Ustawienia → Witryny), nie deploy.
SITE_DOMAIN = env("SITE_DOMAIN", default="localhost")

# --- konkursy w subdomenach platformy ----------------------------------------------------------
# Wyłącznik funkcji „koordynator zakłada konkurs z panelu, a konkurs stoi pod
# ``<slug>.{SITE_DOMAIN}``”. **Domyślnie wyłączony**, bo jego włączenie jest decyzją operatora
# serwera, a nie skutkiem ``git pull``: działa dopiero wtedy, gdy DNS ma rekord wieloznaczny
# ``*.{SITE_DOMAIN}``, a Caddy – on-demand TLS pytający ``/internal/tls-allowed``
# (``apps/tenancy/internal_views.py``). Bez tych dwóch rzeczy nowy adres nie odpowiadałby mimo
# poprawnej konfiguracji Django, a objawem byłby „konkurs założony, strona nie działa”.
#
# Włączony robi dokładnie trzy rzeczy i wszystkie trzy są tutaj albo wynikają z tej wartości:
#
# 1. wpuszcza **całą** domenę platformy do ``ALLOWED_HOSTS`` (wpis z kropką wiodącą – tak Django
#    zapisuje „ta domena i jej subdomeny”) i do ``CSRF_TRUSTED_ORIGINS`` (wzorzec z gwiazdką,
#    którego Django wymaga od wersji 4). Wartości zastane zostają **nietknięte i na początku
#    list**: instalacja bez tego przełącznika ma mieć konfigurację co do wpisu taką, jak miała,
# 2. każe odmówić (404) żądaniu spod subdomeny, pod którą nie stoi aktywny konkurs – inaczej
#    wildcard znaczyłby „Konkurs #1 pod nieskończenie wieloma adresami”
#    (``apps/tenancy/resolution.py``, ``platform_subdomain_miss``),
# 3. otwiera ekran „Nowy konkurs” w panelu koordynatora – ale dopiero razem z flagą konkursu
#    ``competition_creation``: instalacja ma umieć, a konkret musi być włączony.
#
# Czego **nie** robi: nie rusza ``SESSION_COOKIE_DOMAIN`` ani ``CSRF_COOKIE_DOMAIN``. Zostają
# nieustawione (host-only), więc sesja z ``fizyczna.{SITE_DOMAIN}`` nie jedzie na
# ``{SITE_DOMAIN}`` ani na subdomenę cudzego konkursu (etap 1 § 2.6). Ciasteczko na całą domenę
# byłoby tu jedną linijką i jednym wyciekiem sesji między organizatorami.
PLATFORM_SUBDOMAINS = env.bool("PLATFORM_SUBDOMAINS", default=False)
if PLATFORM_SUBDOMAINS:
    ALLOWED_HOSTS = list(dict.fromkeys([*ALLOWED_HOSTS, f".{SITE_DOMAIN}"]))
    CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([*CSRF_TRUSTED_ORIGINS, f"https://*.{SITE_DOMAIN}"]))

WAGTAIL_SITE_NAME = env("WAGTAIL_SITE_NAME", default="Olimpiada Kwantowa")
WAGTAILADMIN_BASE_URL = env("WAGTAILADMIN_BASE_URL", default=f"https://{SITE_DOMAIN}")
# Whitelist rozszerzeń dokumentów: bez niej redaktor mógłby wrzucić do publicznego bucketu plik
# wykonywalny albo HTML (XSS z tej samej domeny, gdyby kiedyś serwować go bez pośrednictwa widoku).
WAGTAILDOCS_EXTENSIONS = ["pdf", "doc", "docx", "odt", "ods", "odp", "xls", "xlsx", "csv", "txt", "zip"]
# Dokumenty idą **przez widok** ``/documents/<id>/<nazwa>``, nie przez przekierowanie na URL bucketu.
# Wagtail bez tej wartości wybiera dla zdalnego storage tryb ``redirect``: obiekt jest wtedy
# oddawany 302 na publiczny adres MinIO, więc ograniczenie widoczności kolekcji („tylko zalogowani”)
# byłoby sprawdzane, ale sam link do bucketu zostawałby w historii przeglądarki i w logach proxy.
# ``serve_view`` streamuje plik z aplikacji, więc kontrola dostępu i treść idą tą samą drogą.
# UWAGA (PROJEKT.md 1.4): obiekt nadal leży w anonimowo czytelnym buckecie ``public-media`` –
# ograniczenie kolekcji utrudnia znalezienie pliku, ale nie czyni go tajnym. Materiały, które
# naprawdę nie mogą wyciec, idą do ``private_media``, nie do dokumentów Wagtaila.
WAGTAILDOCS_SERVE_METHOD = "serve_view"
# Podgląd i wyszukiwarka: prosty backend bazodanowy – bez dodatkowej usługi w compose.
WAGTAILSEARCH_BACKENDS = {"default": {"BACKEND": "wagtail.search.backends.database"}}
WAGTAIL_APPEND_SLASH = True
WAGTAILEMBEDS_RESPONSIVE_HTML = True
# Osadzenia (``EmbedBlock``) wyłącznie z dwóch serwisów. Domyślny finder Wagtaila akceptuje ponad
# 70 dostawców oEmbed – każdy z nich to obcy ``<iframe>`` na naszej domenie i obce żądanie z
# przeglądarki czytelnika, którego nikt u nas nie przeglądał. Lista jest tą samą listą, co
# ``frame-src`` w ``apps/web/middleware.py``: finder pilnuje, co redaktor może wstawić, CSP – co
# przeglądarka wykona. Adres spoza listy kończy się ``EmbedUnsupportedProviderException``
# już w edytorze, więc redaktor dostaje błąd, a nie cichy pusty blok.
WAGTAILEMBEDS_FINDERS = [
    {
        "class": "wagtail.embeds.finders.oembed",
        "providers": [oembed_providers.youtube, oembed_providers.vimeo],
    }
]

# Logowanie sesyjne interfejsu WWW (apps.web). Niezalogowany dostaje 302 na /login/?next=...
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/me/"
LOGOUT_REDIRECT_URL = "/"

# --- Logowanie przez Google i Facebooka (django-allauth, wyłącznie socialaccount) --------------
#
# Zakres użycia allauth jest celowo wąski: bierzemy z niego **tylko** uścisk dłoni OAuth2 i model
# ``SocialAccount``. Rejestracja hasłem, logowanie hasłem i reset hasła zostają nasze
# (``apps.web.views.public``), bo tam jest throttling (``apps.web.throttle``) i tam jest audyt.
# Dlatego ``allauth.account.urls`` **nie** jest montowane, a ``ACCOUNT_ADAPTER`` odmawia rejestracji
# lokalnej – patrz ``apps.accounts.adapters`` i ``apps.web.social_urls``.
#
# Klucze dostawców przychodzą wyłącznie ze środowiska. Pusta wartość = dostawca wyłączony:
# nie ma go w ``SOCIALACCOUNT_PROVIDERS[...]["APPS"]``, więc allauth go nie zna, a interfejs nie
# pokazuje przycisku (``apps.web.context_processors.social_providers``). W bazie nie ma ani jednego
# obiektu ``SocialApp`` – sekret nigdy nie trafia do dumpów bazy ani do panelu admina.
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", default="")
FACEBOOK_APP_ID = env("FACEBOOK_APP_ID", default="")
FACEBOOK_APP_SECRET = env("FACEBOOK_APP_SECRET", default="")

# Uwaga operacyjna: ``SESSION_COOKIE_SAMESITE`` musi zostać przy ``"Lax"`` (domyślne Django).
# Adres powrotny dostawcy to nawigacja GET z obcej domeny – przy ``"Strict"`` przeglądarka nie
# wysyła ciasteczka sesji, więc allauth nie znajduje w sesji parametru ``state`` i logowanie kończy
# się stroną błędu. Biblioteka ma na to obejście (dodatkowe przekierowanie), ale nie ma powodu
# wchodzić w tę ścieżkę: ``Lax`` i tak nie wysyła ciasteczka przy żądaniach POST z obcej domeny.
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.SocialAccountAdapter"

# Model konta nie ma pola ``username`` – loginem jest e-mail (``accounts.User.USERNAME_FIELD``).
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
# Nie prowadzimy weryfikacji adresu przez allauth: konto zakładane społecznościowo ma adres
# potwierdzony przez dostawcę (Google), a konto zakładane hasłem – nasz własny przepływ bez
# weryfikacji (świadomy dług, patrz docs/BACKLOG.md). „mandatory” wymagałoby drugiego kanału
# wysyłki listów obok naszego resetu hasła.
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
# Limity allauth zostają w konfiguracji jako zabezpieczenie na wypadek zamontowania jego widoków;
# dziś nie są aktywne, bo ``allauth.account.urls`` nie jest w urlconfie. Ścieżkę, którą naprawdę
# mamy (dokończenie rejestracji), ogranicza nasz ``ThrottledFormMixin`` ze scope'em ``register``.
ACCOUNT_RATE_LIMITS = {"login": "30/m/ip", "signup": "10/m/ip", "login_failed": "10/m/ip,5/300s/key"}

# Konto **nie** powstaje automatycznie po udanym OAuth: użytkownik trafia najpierw na nasz
# formularz dokończenia rejestracji (szkoła, okręg, rok urodzenia, zgoda RODO). Dopiero jego
# zatwierdzenie tworzy ``User`` – bez zgody RODO nie powstaje żaden wiersz.
SOCIALACCOUNT_AUTO_SIGNUP = False
# Nie przechowujemy tokenów dostawcy. Nie robimy nic w imieniu użytkownika po zalogowaniu, więc
# token byłby wyłącznie kolejnym sekretem do wycieku.
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_QUERY_EMAIL = True
# Przycisk dostawcy jest POST-em z tokenem CSRF (zalecenie allauth): GET otwierałby uścisk dłoni
# z dowolnej obcej strony (login CSRF). Wartość ``False`` sprawia, że GET pokazuje tylko stronę
# potwierdzenia (``templates/socialaccount/login.html``).
SOCIALACCOUNT_LOGIN_ON_GET = False
# Łączenie loginu społecznościowego z istniejącym kontem po adresie e-mail włączamy **per
# dostawca**, nie globalnie (patrz ``EMAIL_AUTHENTICATION`` przy Google niżej). Globalne ``True``
# nadpisałoby ustawienie każdego dostawcy – także tego, któremu nie ufamy w kwestii adresu.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
# Gdy dostawca, któremu ufamy, poda **zweryfikowany** adres istniejącego konta, allauth zapisuje
# powiązanie ``SocialAccount`` od razu, bez pytania o hasło. Ryzyko jest udokumentowane
# w docs/SECURITY_CHECKLIST.md § 3.2: zaufanie do Google zastępuje tu potwierdzenie hasłem.
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        # ``online`` – nie prosimy o refresh token, bo i tak nie przechowujemy tokenów.
        "AUTH_PARAMS": {"access_type": "online"},
        # PKCE (RFC 7636) obok parametru ``state``: przechwycony kod autoryzacyjny jest bezużyteczny
        # bez ``code_verifier``, który nigdy nie opuszcza serwera.
        "OAUTH_PKCE_ENABLED": True,
        # Google zwraca ``email_verified``; adres bez tej flagi zostaje nieweryfikowany i wtedy
        # (patrz niżej) nie łączy się z żadnym istniejącym kontem.
        "EMAIL_AUTHENTICATION": True,
        # Nigdy „na słowo”: zaufanie do adresu bierze się z odpowiedzi Google, nie z konfiguracji.
        "VERIFIED_EMAIL": False,
    },
    "facebook": {
        "METHOD": "oauth2",
        "SCOPE": ["email", "public_profile"],
        "FIELDS": ["id", "email", "name", "first_name", "last_name"],
        # Facebook nie mówi, czy adres jest potwierdzony (pole ``verified`` dotyczy konta, nie
        # adresu). Dlatego jego adresy zostają **nieweryfikowane**…
        "VERIFIED_EMAIL": False,
        # …i dodatkowo wprost odmawiamy logowania po adresie: konto Facebooka nigdy nie przejmie
        # istniejącego konta w serwisie bez potwierdzenia hasłem albo resetem hasła.
        "EMAIL_AUTHENTICATION": False,
    },
}

if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["google"]["APPS"] = [
        {"client_id": GOOGLE_OAUTH_CLIENT_ID, "secret": GOOGLE_OAUTH_CLIENT_SECRET, "key": ""}
    ]
if FACEBOOK_APP_ID and FACEBOOK_APP_SECRET:
    SOCIALACCOUNT_PROVIDERS["facebook"]["APPS"] = [
        {"client_id": FACEBOOK_APP_ID, "secret": FACEBOOK_APP_SECRET, "key": ""}
    ]

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
        # Reset hasła: każdy POST wysyła e-mail na adres podany przez nadawcę żądania, więc bez
        # limitu formularz byłby wysyłaczem spamu na cudze skrzynki (i tanim sposobem na
        # sprawdzenie, czy dostawca poczty przyjmuje nasze wiadomości). Stawka jest niska,
        # bo człowiek prosi o reset raz, a nie pięć razy w godzinie.
        "password_reset": "5/hour",
        # Zgłoszenia do organizatora (``/support/new/``). Każde wysyła list na adres organizatora,
        # więc bez limitu publiczny formularz byłby wysyłaczem spamu – tak samo jak reset hasła.
        # Stawka jest wyższa niż przy rejestracji: człowiek, któremu coś nie działa, pisze czasem
        # drugie zgłoszenie w tej samej sprawie, a odbicie go limitem byłoby karą za problem.
        "support": "10/hour",
        # Podpowiedzi szkół w formularzu rejestracji. Limit jest wysoki, bo jedno wypełnienie
        # formularza to kilkanaście żądań (jedno na przerwę w pisaniu), a dane są jawnym
        # rejestrem publicznym – chronimy tu koszt zapytania, nie treść.
        "schools": "120/min",
        # Kod drugiego składnika (``/login/2fa/``). Sześć cyfr to milion możliwości, a kod żyje
        # trzydzieści sekund – bez limitu da się je przeszukać w kilka godzin z jednego adresu,
        # mając samo hasło. Stawka jest niska, bo człowiek przepisuje kod raz, najwyżej dwa razy.
        "two_factor": "10/min",
        # Webhook płatności (``/api/v1/payments/<dostawca>/``, § 1.5.1). Limit liczy się per adres
        # nadawcy, bo żądanie przychodzi bez konta i bez klucza – jedynym poświadczeniem jest
        # podpis, a podpis sprawdza się **po** przyjęciu żądania. Sześćdziesiąt na minutę mieści
        # z zapasem dostawcę ponawiającego doręczenia całej edycji naraz i jednocześnie zamyka
        # dobieranie podpisu: milion prób na minutę byłoby atakiem, tysiąc dziennie nie jest.
        "payments": "60/min",
        # Zakładanie konkursu z panelu koordynatora (``/coordinator/competitions/new/``). Stawka
        # jest **dzienna i niska**, bo taka jest ta czynność: konkurs zakłada się raz na sezon,
        # a każde założenie to nowa witryna, nowe drzewo stron, nowa edycja i wniosek o certyfikat
        # do Let's Encrypt (limit 50 na domenę na tydzień). Podgląd przed zapisem limitu **nie**
        # konsumuje – liczy się dopiero potwierdzenie (patrz ``apps/web/views/coordinator_competitions.py``).
        "competition_create": "5/day",
    },
    "EXCEPTION_HANDLER": "apps.core.api.exception_handler",
}

# --- CAPTCHA publicznych formularzy rejestracji (django-simple-captcha) ------------------------
# Dlaczego własna, a nie reCAPTCHA/hCaptcha/Turnstile: polityka cookies (``/dokumenty/cookies/``)
# obiecuje, że serwis nie ładuje treści od podmiotów trzecich i nie stawia ciasteczek
# analitycznych. Każdy z tych dostawców wymagałby skryptu z obcej domeny (czyli rozluźnienia CSP)
# i profilowania odwiedzającego przez firmę spoza EOG – obietnicy nie da się wtedy utrzymać.
# Tutaj obrazek rysuje Pillow, wyzwanie leży w naszej bazie (``captcha.CaptchaStore``), a adres
# obrazka jest nasz (``/captcha/image/<klucz>/``), więc ``img-src 'self'`` wystarcza.
#
# Wyzwaniem jest **działanie arytmetyczne**, a nie losowe litery: uczestnik wpisuje wynik, więc
# pomyłka „l” z „1” przestaje istnieć, a wyzwanie da się rozwiązać także przy słabym wzroku
# i na małym ekranie. Ceną jest brak wersji dźwiękowej (patrz ``CAPTCHA_FLITE_PATH``).
CAPTCHA_CHALLENGE_FUNCT = "captcha.helpers.math_challenge"
# Znak mnożenia zamiast gwiazdki: „3 × 5 =” czyta się jak z zeszytu, „3 * 5 =” jak z konsoli.
CAPTCHA_MATH_CHALLENGE_OPERATOR = "×"
# 10 minut: tyle, żeby spokojnie wypełnić długi formularz uczestnika (szkoła, zgody), i nie
# więcej – wyzwanie jest jednorazowe, ale im dłużej żyje, tym więcej wart jest jego zapas
# zebrany przez bota. Po wygaśnięciu formularz wraca z błędem i **nowym** obrazkiem.
CAPTCHA_TIMEOUT = 10
# Obrazek dobrany do szerokości formularza (``.form`` ma 34rem). Wysokość z zapasem na ogonki
# znaku „×” i na obrót liter; szerokość na najdłuższe wyzwanie („10 × 10 =”).
CAPTCHA_IMAGE_SIZE = (200, 60)
CAPTCHA_FONT_SIZE = 36
# Szum: same kropki. Łuki (domyślne) przy czcionce 36 px przechodzą przez środek cyfr i mylą
# 8 z 9 – a to nie jest utrudnienie dla bota OCR, tylko dla człowieka.
CAPTCHA_NOISE_FUNCTIONS = ("captcha.helpers.noise_dots",)
# Obrót ograniczony do ±12°: przy domyślnych ±35° odwrócona „6” jest nieodróżnialna od „9”,
# czyli uczestnik z dobrym wzrokiem podaje zły wynik dobrze odczytanego działania.
CAPTCHA_LETTER_ROTATION = (-12, 12)
# Kolory jasnego motywu na sztywno: obrazek jest bitmapą, więc nie podąża za ``prefers-color-scheme``.
CAPTCHA_BACKGROUND_COLOR = "#fffefb"
CAPTCHA_FOREGROUND_COLOR = "#171d27"
# Bez wersji dźwiękowej: ``flite`` to kolejny binarny pakiet w obrazie, a synteza mowy po
# angielsku i tak nie pomogłaby polskiemu uczestnikowi. Ograniczenie dostępności jest jawne –
# formularz podaje adres kontaktowy organizatora (patrz templates/web/_antispam_fields.html)
# i tą drogą konto zakłada człowiek, a nie automat.
CAPTCHA_FLITE_PATH = None
# Minimalny czas wypełniania formularza (sekundy) – patrz apps/web/captcha.py. Człowiek nie
# wypełni rejestracji w trzy sekundy; skrypt wysyłający POST-a od razu po GET-cie owszem.
ANTISPAM_MIN_FILL_SECONDS = 3

if E2E_MODE:
    # Scenariusz end-to-end prowadzi prawdziwą przeglądarkę przez prawdziwy formularz, ale
    # obrazka nie odczyta – w trybie testowym pakiet przyjmuje odpowiedź „PASSED”. Razem
    # z wyzerowanym progiem czasu to **jedyne** miejsce, w którym zabezpieczenie da się obejść,
    # i dlatego wisi na tej samej zmiennej, co przesuwanie osi czasu etapu: ``E2E_MODE`` nie
    # jest ustawiane w produkcji (patrz komentarz przy jego definicji na początku pliku).
    CAPTCHA_TEST_MODE = True
    ANTISPAM_MIN_FILL_SECONDS = 0

# --- Swagger UI (/api/docs/) -----------------------------------------------------------------
# Wersja pinowana co do łatki i weryfikowana przez SRI. Domyślne ``@latest`` z drf-spectacular
# oznaczałoby, że treść skryptu na naszej stronie zmienia się bez naszego udziału – z SRI byłoby
# to zresztą nie do pogodzenia (hash przestałby pasować przy pierwszym wydaniu biblioteki).
SWAGGER_UI_VERSION = "5.32.15"
SWAGGER_UI_DIST = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
# Skróty policzone z plików tej właśnie wersji. Zmiana wersji = ponowne policzenie hashy
# (sha384-base64), inaczej przeglądarka odrzuci zasób i /api/docs/ zostanie pustą stroną.
SWAGGER_UI_SRI = {
    "swagger-ui.css": "sha384-fgyWYkUAamzuI8mJFu/xpRP0JWCJRwkwUwsYDoOYVHUJ8NQE5cENn8ib3ppwFFSX",
    "swagger-ui-bundle.js": "sha384-m7zaGj7MPzU+G4lz2eyy73GxK9bbRDr9bB2CSdj8wodg2wu/Wnt6wsoLP3JD+RS9",
    "swagger-ui-standalone-preset.js": (
        "sha384-9rDX8vR4ir9/JIiV/XvxMpb5T9tVyFbssk49PV935hrdwmkVNJ7VZMM0RXAm184h"
    ),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Platforma Olimpiady API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": SWAGGER_UI_DIST,
    # Ikonka też przychodziła z ``@latest``; własnego pliku nie mamy, a poszerzanie ``img-src``
    # o CDN dla 32×32 pikseli się nie opłaca. Pusta wartość = szablon nie renderuje <link rel=icon>.
    "SWAGGER_UI_FAVICON_HREF": "",
    # Ustawienia trafiają do inline'owego skryptu Swaggera (nasz szablon nadaje mu nonce).
    # ``persistAuthorization`` zostaje wyłączone: token API nie ma leżeć w ``localStorage``
    # przeglądarki po zamknięciu karty.
    "SWAGGER_UI_SETTINGS": {"deepLinking": True, "persistAuthorization": False},
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
        # Pole „kind” ma w schemacie kilka różnych zbiorów wartości (rodzaj etapu, rodzaj
        # dokumentu, typ szkoły). Odkąd etap wychodzi także publicznym API integracji
        # (``/api/v1/editions/<id>/stages/``), generator musiał rozstrzygać kolizję sam i nadawał
        # nazwy w rodzaju „KindBd6Enum” – czyli takie, które zmieniają się przy każdej zmianie
        # zawartości schematu i psują klientom generowanie modeli.
        "StageKindEnum": "apps.competitions.models.StageKind.choices",
    },
}

# --- pieczęć elektroniczna dyplomów (apps.results.signing) -------------------------------------
# Bez ścieżki do pliku PKCS#12 podpisywanie jest **wyłączone** i dokumenty wychodzą niepodpisane –
# tak samo, jak przed wprowadzeniem tej funkcji. To jest stan domyślny, bo klucz pieczęci jest
# materiałem kryptograficznym organizacji: deweloper nie ma go mieć, a środowisko testowe tym
# bardziej. Plik montuje się do kontenera wolumenem i trzyma poza repozytorium.
CERT_SIGN_P12_PATH = env("CERT_SIGN_P12_PATH", default="")
CERT_SIGN_P12_PASSWORD = env("CERT_SIGN_P12_PASSWORD", default="")
# Adres znacznika czasu (RFC 3161). Bez niego podpis niesie czas z zegara serwera, czyli dowodzi
# jedynie „kiedyś”; ze znacznikiem – „nie później niż wtedy”, i dlatego długoterminowa ważność
# pieczęci (PAdES-LT) zaczyna się właśnie tutaj. Pusta wartość = podpis bez znacznika.
CERT_SIGN_TSA_URL = env("CERT_SIGN_TSA_URL", default="")
CERT_SIGN_REASON = env("CERT_SIGN_REASON", default="Dokument wystawiony przez Olimpiadę Kwantową")
CERT_SIGN_LOCATION = env("CERT_SIGN_LOCATION", default="")

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
