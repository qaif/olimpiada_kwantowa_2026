"""Konfiguracja poczty: czy ``MAILERS`` naprawdę jest podłączone tam, gdzie było ``EMAIL_BACKEND``.

Ten plik nie sprawdza treści żadnego listu – od tego jest dwadzieścia innych plików. Sprawdza
**instalację elektryczną** pod nimi, i powstał dlatego, że migracja z ustawień ``EMAIL_*`` na
słownik ``MAILERS`` (Django 6.1) ma jeden tryb awarii, którego nie widać w wyniku testów:

Django, gdy ``MAILERS`` jest zdefiniowane, przestaje czytać ``EMAIL_BACKEND``
(``django/conf/__init__.py``: dostęp do przestarzałej nazwy kończy się wtedy ``AttributeError``,
a ``django/core/mail/handler.py`` bierze konfigurację wyłącznie ze słownika). Gdyby więc
przechwytywanie poczty w testach zostało przy ``EMAIL_BACKEND``, listy poszłyby prawdziwym
backendem (u nas: konsolowym), ``mail.outbox`` zostałby **pusty**, a około stu asercji na treść
wiadomości – z których wiele ma postać „dla każdej wiadomości w outboksie…” – przechodziłoby,
nie sprawdzając niczego. Suita świeciłaby na zielono i nie sprawdzała poczty w ogóle.

Stąd cztery grupy testów niżej:

1. **droga do outboksu** – każde publiczne wejście, którym ta aplikacja wysyła listy, kończy się
   w ``mail.outbox``, i to w **policzalnej** liczbie sztuk (asercje są na dokładną liczbę, nie na
   „coś tam przyszło”),
2. **kształt konfiguracji** – pod ustawieniami testowymi nadajnik ``default`` jest ``locmem``,
   a pod produkcyjnymi: SMTP z hostem, portem, TLS-em i użytkownikiem wziętymi z ``EMAIL_URL``
   (czyli ze zmiennej, która stoi w produkcyjnym ``.env`` i **nie** zmienia nazwy),
3. **brak pozostałości** – żaden moduł ustawień nie definiuje już przestarzałej nazwy ``EMAIL_*``.
   To nie jest czystość dla czystości: moduł, który poda jedno i drugie naraz, Django odrzuca
   wyjątkiem ``ImproperlyConfigured`` (``_check_email_settings_conflicts``), więc dopisanie takiej
   nazwy w przyszłości wywróciłoby start aplikacji, a nie wysyłkę,
4. **cisza ostrzeżeń** – przejście listu przez każdą z naszych dróg nie wywołuje ostrzeżenia
   o wycofaniu. ``RemovedInDjango70Warning`` dziedziczy po ``PendingDeprecationWarning``, więc
   test zamienia na błąd obie kategorie.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core import mail
from django.core.mail import EmailMessage, mailers, send_mail

from apps.accounts.activation import queue_mail
from apps.core.tasks import send_mail_task

#: Backend, którym testy przechwytują pocztę, i ten, którym wysyła ją produkcja.
LOCMEM_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

#: Nazwy ustawień, które Django 6.1 uznaje za przestarzałe (``django.conf.DEPRECATED_EMAIL_SETTINGS``).
#: Lista jest tu przepisana wprost, a nie zaimportowana: jej sens to „czego **nasze** moduły mają
#: nie definiować”, i ma zostać taka sama także wtedy, gdy Django skasuje swoją stałą w 7.0.
DEPRECATED_EMAIL_SETTINGS = frozenset(
    {
        "EMAIL_BACKEND",
        "EMAIL_FILE_PATH",
        "EMAIL_HOST",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_HOST_USER",
        "EMAIL_PORT",
        "EMAIL_SSL_CERTFILE",
        "EMAIL_SSL_KEYFILE",
        "EMAIL_TIMEOUT",
        "EMAIL_USE_SSL",
        "EMAIL_USE_TLS",
    }
)

#: Katalog z pakietem ``config`` – korzeń, z którego uruchamia się ``manage.py``.
BACKEND_ROOT = Path(__file__).resolve().parents[3]

#: Program dla podprocesu z punktu 2: wczytuje ustawienia **produkcyjne** i wypisuje wynikowy
#: słownik ``MAILERS`` jako JSON. Podproces, a nie ``override_settings``, bo pytanie brzmi „co
#: zbuduje moduł ustawień wczytany od zera w podanym środowisku” – a moduł ustawień wykonuje się
#: raz na proces i czyta ``os.environ`` w czasie importu.
PRODUCTION_PROBE = """
import json
import warnings

import django

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    django.setup()
    from django.conf import settings

    result = {
        "mailers": settings.MAILERS,
        "default_from_email": settings.DEFAULT_FROM_EMAIL,
        "server_email": settings.SERVER_EMAIL,
        "email_warnings": sorted(
            {str(item.message) for item in caught if "EMAIL_" in str(item.message)}
        ),
    }
print("PROBE" + json.dumps(result))
"""


def _run_production_probe(**environment: str) -> dict:
    """Wczytuje ``config.settings.production`` w podprocesie o **kontrolowanym** środowisku.

    Środowisko jest budowane od zera (bez ``os.environ`` tego procesu poza ``PATH``), bo cała treść
    tego testu to zdanie „te i tylko te zmienne sterują konfiguracją poczty”. Odziedziczone
    ``EMAIL_URL`` z kontenera deweloperskiego podstawiłoby odpowiedź.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        # Produkcja odmawia startu z kluczem z repozytorium i bez poświadczeń storage – te dwa
        # bezpieczniki nie mają z pocztą nic wspólnego, ale bez nich moduł się nie wczyta.
        "DJANGO_SECRET_KEY": "k" * 60,
        "MINIO_ROOT_USER": "probe-access-key",
        "MINIO_ROOT_PASSWORD": "probe-secret-key",
        "DEFAULT_FROM_EMAIL": "olimpiada@example.test",
        **environment,
    }
    completed = subprocess.run(  # noqa: S603 - własny interpreter, własny program, stałe argumenty
        [sys.executable, "-c", PRODUCTION_PROBE],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    marker = [line for line in completed.stdout.splitlines() if line.startswith("PROBE")]
    assert marker, f"Podproces nie wypisał wyniku.\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    return json.loads(marker[-1][len("PROBE") :])


# =================================================================================================
# 1. Droga do outboksu
# =================================================================================================


def test_send_mail_reaches_the_outbox():
    """``django.core.mail.send_mail`` – droga alertów watchdoga (``apps/core/alerts.py``)."""
    mail.outbox.clear()

    sent = send_mail("Temat", "Treść", "nadawca@example.test", ["odbiorca@example.test"])

    assert sent == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "Temat"


def test_email_message_send_reaches_the_outbox():
    """``EmailMessage.send()`` – droga, którą listy składa sam Django (reset hasła, allauth)."""
    mail.outbox.clear()

    sent = EmailMessage("Temat", "Treść", "nadawca@example.test", ["odbiorca@example.test"]).send()

    assert sent == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["odbiorca@example.test"]


def test_the_celery_task_reaches_the_outbox():
    """``apps.core.tasks.send_mail_task`` – droga **wszystkich** listów tej aplikacji."""
    mail.outbox.clear()

    sent = send_mail_task("Temat", "Treść", ["odbiorca@example.test"], "listy@example.test")

    assert sent == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].from_email == "listy@example.test"


@pytest.mark.django_db
def test_queue_mail_reaches_the_outbox(django_capture_on_commit_callbacks):
    """``apps.accounts.activation.queue_mail`` – kolejkowanie po commicie plus zadanie Celery."""
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        queue_mail("Temat", "Treść", "odbiorca@example.test")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "Temat"


def test_mail_admins_reaches_the_outbox(settings):
    """``mail_admins`` – tą drogą Django raportuje błędy 500; jako jedyna dokleja prefiks tematu."""
    from django.core.mail import mail_admins

    settings.ADMINS = ["dyzurny@example.test"]
    mail.outbox.clear()

    mail_admins("Awaria", "Szczegóły")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == f"{settings.EMAIL_SUBJECT_PREFIX}Awaria"
    assert mail.outbox[0].from_email == settings.SERVER_EMAIL


# =================================================================================================
# 2. Kształt konfiguracji
# =================================================================================================


def test_the_default_mailer_is_locmem_under_test_settings():
    """Nadajnik ``default`` (alias, którego używa każda wysyłka bez ``using=``) pisze do outboksu.

    Sprawdzamy jedno i drugie: ustawienie **i** klasę zbudowanego połączenia. Samo ustawienie nie
    wystarczy, bo ``setup_test_environment()`` podmienia je w locie i to podmiana jest tym, co
    naprawdę obowiązuje w sesji testowej.
    """
    assert django_settings.MAILERS["default"]["BACKEND"] == LOCMEM_BACKEND

    connection = mailers["default"]

    assert f"{type(connection).__module__}.{type(connection).__qualname__}" == LOCMEM_BACKEND
    assert connection.alias == "default"


def test_production_settings_build_an_smtp_mailer_from_email_url():
    """Wariant B z README § 4.1: dostawca zewnętrzny z uwierzytelnieniem i TLS-em.

    Adres, port, użytkownik, hasło i TLS mają przyjść z ``EMAIL_URL``, a limit czasu z
    ``EMAIL_TIMEOUT`` – z tych samych zmiennych, co przed migracją. To jest cała treść zdania
    „produkcyjny ``.env`` nie wymaga żadnej zmiany”.
    """
    probe = _run_production_probe(
        EMAIL_URL="smtp+tls://relay-user%40example.test:tajne-haslo@smtp.dostawca.example:587",
        EMAIL_TIMEOUT="10",
    )

    assert probe["mailers"] == {
        "default": {
            "BACKEND": SMTP_BACKEND,
            "OPTIONS": {
                "host": "smtp.dostawca.example",
                "port": 587,
                "username": "relay-user@example.test",
                "password": "tajne-haslo",
                "use_tls": True,
                "use_ssl": False,
                "timeout": 10,
            },
        }
    }
    assert probe["email_warnings"] == []


def test_production_settings_keep_the_relay_from_the_deploy_script():
    """Wariant domyślny: własny Postfix z compose'a (``scripts/deploy.sh``: ``smtp://mail:587``).

    Bez poświadczeń i bez TLS-u – dokładnie tak, jak wygląda dziś ``/opt/olimpiada/.env``. Drugi
    ``EMAIL_TIMEOUT`` niż w teście wyżej jest tu celowo: pokazuje, że wartość **przechodzi**
    ze zmiennej, a nie jest wpisana w ustawieniach na sztywno.
    """
    probe = _run_production_probe(EMAIL_URL="smtp://mail:587", EMAIL_TIMEOUT="25")

    options = probe["mailers"]["default"]["OPTIONS"]
    assert probe["mailers"]["default"]["BACKEND"] == SMTP_BACKEND
    assert (options["host"], options["port"]) == ("mail", 587)
    assert (options["username"], options["password"]) == ("", "")
    assert (options["use_tls"], options["use_ssl"]) == (False, False)
    assert options["timeout"] == 25


def test_production_settings_without_email_url_fall_back_to_the_console():
    """Brak ``EMAIL_URL`` to nadal konsola, a nie SMTP na ``localhost:25``.

    Powód jest ten sam, co przed migracją (komentarz w ``config/settings/base.py``): w kontenerze
    aplikacyjnym nie ma MTA, więc domyślny SMTP zamieniałby pierwszą wysyłkę w ``Connection
    refused`` w środku żądania HTTP. Backend bez opcji dostaje **pusty** słownik – nieznany klucz
    w ``OPTIONS`` jest u nadajnika wyjątkiem, a nie zignorowanym argumentem.
    """
    probe = _run_production_probe()

    assert probe["mailers"] == {
        "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend", "OPTIONS": {}}
    }


def test_the_sender_still_comes_from_default_from_email():
    """Nadawca instalacji i nadawca wiadomości systemowych to nadal jedna zmienna środowiskowa."""
    probe = _run_production_probe(DEFAULT_FROM_EMAIL="olimpiada@olimpiadakwantowa.pl")

    assert probe["default_from_email"] == "olimpiada@olimpiadakwantowa.pl"
    assert probe["server_email"] == "olimpiada@olimpiadakwantowa.pl"


# =================================================================================================
# 3. Brak pozostałości po ``EMAIL_*``
# =================================================================================================


def _assigned_names(path: Path) -> set[str]:
    """Nazwy przypisywane na najwyższym poziomie modułu ustawień (bez importów gwiazdkowych)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("module", ["base", "test", "production"])
def test_no_settings_module_defines_a_deprecated_email_setting(module: str):
    """Statycznie, bez wczytywania modułu – także produkcyjnego, którego tu wczytać się nie da.

    Gdyby któraś z tych nazw wróciła, Django odrzuciłoby **cały** moduł ustawień
    (``ImproperlyConfigured: Deprecated email settings are not allowed when MAILERS is defined``),
    czyli objawem byłby nieuruchamiający się kontener, a nie niedziałająca poczta. Ten test mówi
    o tym wcześniej i wprost.
    """
    path = BACKEND_ROOT / "config" / "settings" / f"{module}.py"

    assert not (DEPRECATED_EMAIL_SETTINGS & _assigned_names(path))


@pytest.mark.parametrize("name", sorted(DEPRECATED_EMAIL_SETTINGS))
def test_django_no_longer_exposes_the_deprecated_email_settings(name: str):
    """Skutek uboczny zdefiniowania ``MAILERS``: przestarzała nazwa znika z ustawień.

    To jest dowód od strony Django, że nikt (ani nasz kod, ani biblioteka) nie czyta już tych
    wartości po cichu: odczyt kończy się ``AttributeError``, a nie wartością domyślną z
    ``global_settings``.
    """
    assert not django_settings.is_overridden(name)
    with pytest.raises(AttributeError):
        getattr(django_settings, name)


def test_the_settings_that_stayed_are_the_ones_that_are_not_deprecated():
    """``DEFAULT_FROM_EMAIL``, ``SERVER_EMAIL`` i ``EMAIL_SUBJECT_PREFIX`` zostają – i mają zostać.

    Nie ma ich na liście przestarzałych: Django 7.0 nadal będzie je czytać, a u nas trzyma się na
    nich nadawca listów (``apps.core.tasks.mail_from``) i temat wiadomości systemowych.
    """
    assert django_settings.DEFAULT_FROM_EMAIL
    assert django_settings.SERVER_EMAIL == django_settings.DEFAULT_FROM_EMAIL
    assert django_settings.EMAIL_SUBJECT_PREFIX == "[Olimpiada Kwantowa] "


# =================================================================================================
# 4. Cisza ostrzeżeń
# =================================================================================================


@pytest.mark.django_db
def test_no_send_path_raises_a_deprecation_warning(django_capture_on_commit_callbacks):
    """Każda nasza droga wysyłki przechodzi z ostrzeżeniami o wycofaniu podniesionymi do błędu.

    ``RemovedInDjango70Warning`` dziedziczy po ``PendingDeprecationWarning`` (a nie po
    ``DeprecationWarning``), więc na błąd podnosimy obie kategorie. Wywołanie ``simplefilter``
    unieważnia przy okazji rejestr już pokazanych ostrzeżeń – bez tego test zależałby od tego, co
    zdążyło się wykonać przed nim w tej samej sesji.
    """
    mail.outbox.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        warnings.simplefilter("error", PendingDeprecationWarning)

        send_mail("Temat", "Treść", "nadawca@example.test", ["odbiorca@example.test"])
        EmailMessage("Temat", "Treść", "nadawca@example.test", ["odbiorca@example.test"]).send()
        send_mail_task("Temat", "Treść", ["odbiorca@example.test"])
        with django_capture_on_commit_callbacks(execute=True):
            queue_mail("Temat", "Treść", "odbiorca@example.test")

    assert len(mail.outbox) == 4
