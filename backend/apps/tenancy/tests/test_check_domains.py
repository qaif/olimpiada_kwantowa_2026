"""``manage.py check_domains`` — czy domena konkursu jest wpuszczona we wszystkich trzech listach.

Ta komenda istnieje dlatego, że brak domeny w jednej z trzech konfiguracji objawia się za każdym
razem inaczej (brak certyfikatu / 400 na każde żądanie / odmowa CSRF na każdym formularzu), a każdy
z tych objawów wygląda jak osobna awaria. Testy sprawdzają, że komenda nazywa **przyczynę**,
a nie tylko fakt rozjazdu — i że domyślnie nie przerywa wdrożenia.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from wagtail.models import Site

from apps.tenancy.models import RoutingMode

from .conftest import HOST_B, make_competition

pytestmark = pytest.mark.django_db


def run(**kwargs) -> str:
    out = StringIO()
    call_command("check_domains", stdout=out, **kwargs)
    return out.getvalue()


def test_reports_domain_missing_everywhere(settings):
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost"]
    settings.EXTRA_DOMAINS = []
    make_competition(HOST_B, "fizyczna")

    output = run()

    assert "fizyczna" in output
    # Przyczyna, nie tylko fakt: każdy z trzech braków ma w komunikacie swoją nazwę zmiennej.
    assert "EXTRA_DOMAINS" in output
    assert "DJANGO_ALLOWED_HOSTS" in output
    assert "DJANGO_CSRF_TRUSTED_ORIGINS" in output


def test_silent_when_domain_is_allowed_everywhere(settings):
    settings.ALLOWED_HOSTS = ["localhost", HOST_B]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost", f"https://{HOST_B}"]
    settings.EXTRA_DOMAINS = [HOST_B]
    settings.SITE_DOMAIN = "localhost"
    make_competition(HOST_B, "fizyczna")

    output = run()

    assert "fizyczna" not in output


def test_all_lists_also_the_correct_ones(settings):
    settings.ALLOWED_HOSTS = ["localhost", HOST_B]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost", f"https://{HOST_B}"]
    settings.EXTRA_DOMAINS = [HOST_B]
    make_competition(HOST_B, "fizyczna")

    assert f"ok     fizyczna ({HOST_B})" in run(all=True)


def test_strict_stops_the_caller(settings):
    """Domyślnie ostrzeżenie (wdrożenie idzie dalej), ``--strict`` — błąd (monitoring, CI)."""
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.CSRF_TRUSTED_ORIGINS = []
    settings.EXTRA_DOMAINS = []
    make_competition(HOST_B, "fizyczna")

    run()  # bez --strict: samo ostrzeżenie, żadnego wyjątku

    with pytest.raises(CommandError, match="rozjazdów"):
        run(strict=True)


def test_path_mode_competition_is_not_checked(settings):
    """Konkurs pod prefiksem ścieżki nie ma własnego hosta — sprawdzanie go byłoby fałszywym alarmem."""
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost"]
    settings.EXTRA_DOMAINS = []
    make_competition(HOST_B, "fizyczna", routing_mode=RoutingMode.PATH, path_prefix="fizyczna")

    assert "fizyczna" not in run()


def test_reports_drift_between_competition_and_site(settings):
    """Siatka pod sygnałem: rozjazd domeny witryny i domeny konkursu też jest raportowany.

    Zwykłą drogę — zmianę domeny przez redaktora w ``/cms/`` — domyka sygnał
    ``tenancy.sync_primary_domain``. Ale ``queryset.update()``, migracja i ręczny SQL sygnałów nie
    wywołują, a ``primary_domain`` buduje linki w listach wysyłanych spoza żądania (zadania
    Celery): rozjazd znaczy wtedy listy prowadzące pod adres, którego już nie ma. Dlatego test
    celowo omija sygnał — sprawdza stan, w jakim baza **może** się znaleźć, a nie ten, do którego
    prowadzi panel.
    """
    competition = make_competition(HOST_B, "fizyczna")
    settings.ALLOWED_HOSTS = ["localhost", HOST_B]
    settings.CSRF_TRUSTED_ORIGINS = ["https://localhost", f"https://{HOST_B}"]
    settings.EXTRA_DOMAINS = [HOST_B]
    Site.objects.filter(pk=competition.site_id).update(hostname="przeniesiona.invalid")

    output = run()

    assert "przeniesiona.invalid" in output
