"""Strona statusu ``/status/`` i jej wariant maszynowy ``/status.json``.

Strona odpowiada na pytanie zadawane o 23:40 przed deadline'em: „nie mogę wysłać pracy – to u was,
czy u mnie?”. Dlatego jest publiczna i dlatego te testy pilnują czterech rzeczy:

- **jest dostępna bez logowania** i pokazuje czas serwera w czasie polskim (to on rozstrzyga
  o przyjęciu pliku, a nie zegarek na telefonie),
- **mówi o stanie usług, a nie o infrastrukturze.** Werdykt jest binarny; nazw hostów, wersji
  bibliotek ani treści błędów na tej stronie nie ma i być nie może,
- **kolejka zadań jest sprawdzana pulsem**, a nie synchronicznym pytaniem do brokera: strona nie
  może wisieć dokładnie wtedy, gdy worker nie żyje,
- **oba warianty pokazują to samo.** Monitoring czyta JSON-a, człowiek HTML – rozjazd znaczyłby,
  że jedno z dwojga kłamie.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.cms.models import Announcement, AnnouncementLevel
from apps.core.status import HEARTBEAT_MAX_AGE_MINUTES, as_json, snapshot
from apps.core.tasks import HEARTBEAT_CACHE_KEY, heartbeat

pytestmark = pytest.mark.django_db

STATUS_URL = "/status/"
JSON_URL = "/status.json"


#: Uwaga o buforze: widok jest opakowany w ``cache_page`` na 30 sekund i korzysta z tego samego
#: backendu, co puls workera. Izolację między testami daje autouse'owa fixture ``_clear_cache``
#: z ``backend/conftest.py`` – dlatego każdy test tutaj wykonuje **jedno** żądanie na wariant
#: strony i nie polega na tym, że drugie ominie bufor.


def beat() -> None:
    """Przebieg zadania pulsu – tak, jak robi to beat co minutę."""
    heartbeat()


# --- dostępność ------------------------------------------------------------------------------


def test_the_page_is_public(web_client):
    beat()

    response = web_client.get(STATUS_URL)

    assert response.status_code == 200
    assert "Status serwisu" in response.content.decode()


def test_the_page_shows_the_server_time(web_client):
    """Czas serwera jest tu najważniejszą liczbą: to on rozstrzyga o przyjęciu pracy."""
    beat()

    body = web_client.get(STATUS_URL).content.decode()

    assert timezone.localtime(timezone.now()).strftime("%Y") in body


def test_the_page_never_names_hosts_or_library_versions(web_client):
    beat()

    body = web_client.get(STATUS_URL).content.decode()

    assert "postgres" not in body.lower()
    assert "redis" not in body.lower()
    assert "minio" not in body.lower()


# --- stan usług ------------------------------------------------------------------------------


def test_the_database_and_the_cache_answer_in_a_working_installation():
    beat()

    checks = {item.name: item.ok for item in snapshot()["services"]}

    assert checks["database"] is True
    assert checks["cache"] is True


def test_the_queue_is_healthy_right_after_a_beat_run():
    beat()

    queue = next(item for item in snapshot()["services"] if item.name == "queue")

    assert queue.ok is True


def test_the_queue_is_down_without_a_heartbeat():
    """Brak pulsu znaczy dla uczestnika to samo, co awaria: list nie wyjdzie, skan się nie wykona."""
    cache.delete(HEARTBEAT_CACHE_KEY)

    queue = next(item for item in snapshot()["services"] if item.name == "queue")

    assert queue.ok is False
    assert "brak sygnału" in queue.detail


def test_a_stale_heartbeat_counts_as_down():
    old = timezone.now() - timedelta(minutes=HEARTBEAT_MAX_AGE_MINUTES + 5)
    cache.set(HEARTBEAT_CACHE_KEY, old.isoformat(), 600)

    queue = next(item for item in snapshot()["services"] if item.name == "queue")

    assert queue.ok is False


def test_the_overall_verdict_needs_every_subsystem():
    """„Większość działa” nie istnieje: uczestnikowi z niedziałającym magazynem nie pomaga baza."""
    cache.delete(HEARTBEAT_CACHE_KEY)

    assert snapshot()["all_ok"] is False


# --- stan zawodów ----------------------------------------------------------------------------


def test_the_page_shows_the_registration_state_and_the_current_stage(web_client, elim_stage):
    beat()

    body = web_client.get(STATUS_URL).content.decode()

    assert elim_stage.edition.year_label in body
    assert elim_stage.display_name in body


def test_the_competition_state_comes_from_the_same_source_as_the_rest_of_the_site(elim_stage):
    """Strona statusu nie może pokazać innego stanu niż ten, który egzekwuje serwer."""
    from apps.competitions.registration import current_registration_status

    state = snapshot()["competition"]

    assert state["registration_open"] == current_registration_status().is_open
    assert state["stage"] == elim_stage.display_name


# --- komunikaty ------------------------------------------------------------------------------


def test_announcements_show_on_the_status_page(web_client):
    Announcement.objects.create(text="KOMUNIKAT-NA-STATUSIE", level=AnnouncementLevel.WARNING)
    beat()

    body = web_client.get(STATUS_URL).content.decode()

    assert "KOMUNIKAT-NA-STATUSIE" in body


# --- wariant maszynowy -----------------------------------------------------------------------


def test_the_json_variant_answers_with_the_same_verdict(web_client):
    beat()

    response = web_client.get(JSON_URL)
    data = json.loads(response.content)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert data["status"] == "ok"
    assert set(data["services"]) == {"database", "cache", "storage", "queue"}


def test_the_json_variant_stays_200_even_when_a_subsystem_is_down(web_client):
    """Monitor, który dostaje 503, uznaje zwykle, że niedostępna jest sama strona statusu."""
    cache.delete(HEARTBEAT_CACHE_KEY)

    response = web_client.get(JSON_URL)

    assert response.status_code == 200
    assert json.loads(response.content)["status"] == "degraded"


def test_both_variants_are_built_from_one_snapshot(elim_stage):
    beat()
    data = snapshot()

    payload = as_json(data)

    assert payload["status"] == ("ok" if data["all_ok"] else "degraded")
    assert payload["stage"] == data["competition"]["stage"]
    assert payload["services"] == {item.name: item.ok for item in data["services"]}


def test_the_json_variant_carries_the_announcements(web_client):
    Announcement.objects.create(text="Awaria wysyłki.", level=AnnouncementLevel.DANGER)
    beat()

    data = json.loads(web_client.get(JSON_URL).content)

    assert data["announcements"] == [{"level": "danger", "text": "Awaria wysyłki."}]


def test_the_json_variant_carries_no_infrastructure_details(web_client):
    beat()

    data = json.loads(web_client.get(JSON_URL).content)

    assert set(data) == {
        "status",
        "time",
        "version",
        "services",
        # Stan kopii zapasowych – wyłącznie wartości logiczne. Dat tu nie ma i być nie może:
        # data ostatniej kopii mówi obcemu, kiedy uderzenie zaboli najbardziej
        # (apps/core/status.py, ``as_json``).
        "backup_last_ok",
        "backup_last_verified",
        "registration_open",
        "edition",
        "stage",
        "stage_deadline",
        "announcements",
        # Etap 2 § 1.7.3: jedyny dołożony klucz. Wartość logiczna „instalacja czeka jeszcze na
        # kreator ``/setup/``”, czyli też nic o infrastrukturze (apps/tenancy/setup.py).
        "setup_pending",
        # Poziom zajętości połączeń z Postgresem (``ok|warn|critical|unknown``) – bez liczb
        # (apps/core/dbconnections.py).
        "db_connections",
    }
