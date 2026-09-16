"""Baner komunikatów organizatora: okno czasowe, pamięć podręczna, baner i ekran koordynatora.

Komunikat wisi na **każdej** stronie serwisu, więc każda z tych rzeczy popsuta po cichu ma skutek
na całym serwisie naraz. Testy pilnują czterech:

- **okno czasowe i wyłącznik** – to one decydują o widoczności, a nie ręczne gaszenie. Komunikat
  przed startem i po końcu nie jest widoczny; wyłącznik zdejmuje go natychmiast, niezależnie od dat,
- **pamięć podręczna jest unieważniana przy zapisie.** Bez tego komunikat o awarii pojawiałby się
  z minutowym opóźnieniem – czyli w chwili, w której ogłasza się go po to, żeby był od razu,
- **baner renderuje tekst, nie HTML.** Treść przechodzi przez autoescapowanie, a odnośnik jest
  osobną parą pól. Pasek na każdej stronie serwisu nie jest miejscem na cudzy znacznik,
- **bez JavaScriptu baner jest sprawny.** Serwer rysuje go od razu; skrypt dokłada wyłącznie
  przycisk zamknięcia (w HTML-u ma ``hidden``) i pamięć o nim. Komunikat niezamykalny przycisku
  nie dostaje w ogóle.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cms.announcements import active_announcements, cached_announcements
from apps.cms.models import Announcement, AnnouncementLevel

pytestmark = pytest.mark.django_db

PANEL_URL = "/coordinator/announcements/"


def announcement(**overrides) -> Announcement:
    data = {"text": "Przedłużamy termin oddania prac do piątku.", "level": AnnouncementLevel.INFO}
    data.update(overrides)
    return Announcement.objects.create(**data)


# --- okno czasowe ---------------------------------------------------------------------------


def test_an_active_announcement_without_an_end_is_live():
    item = announcement()

    assert item.is_live()
    assert active_announcements() == [item]


def test_an_announcement_before_its_start_is_not_live():
    announcement(starts_at=timezone.now() + timedelta(hours=1))

    assert active_announcements() == []


def test_an_announcement_after_its_end_is_not_live():
    now = timezone.now()
    announcement(starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1))

    assert active_announcements() == []


def test_the_switch_takes_it_down_regardless_of_the_dates():
    """Wyłącznik jest hamulcem awaryjnym: „zdejmij to natychmiast” to inna potrzeba niż termin."""
    announcement(is_active=False)

    assert active_announcements() == []


def test_a_reversed_window_is_refused_by_the_model():
    from django.core.exceptions import ValidationError

    now = timezone.now()
    item = Announcement(text="Treść.", starts_at=now, ends_at=now - timedelta(hours=1))

    with pytest.raises(ValidationError):
        item.full_clean()


def test_the_most_important_level_comes_first():
    """Czytelnik, który przeczyta tylko pierwszy komunikat, ma przeczytać ten najważniejszy."""
    announcement(text="Informacja.", level=AnnouncementLevel.INFO)
    danger = announcement(text="Awaria wysyłki prac.", level=AnnouncementLevel.DANGER)
    warning = announcement(text="Termin przesunięty.", level=AnnouncementLevel.WARNING)

    assert active_announcements()[:2] == [danger, warning]


# --- pamięć podręczna -----------------------------------------------------------------------


def test_saving_an_announcement_invalidates_the_cache():
    cached_announcements()

    item = announcement(text="Awaria – pracujemy nad tym.")

    assert cached_announcements() == [item]


def test_deleting_an_announcement_invalidates_the_cache():
    item = announcement()
    cached_announcements()

    item.delete()

    assert cached_announcements() == []


# --- baner ----------------------------------------------------------------------------------


def test_the_banner_shows_on_every_page(web_client):
    announcement(text="KOMUNIKAT-ORGANIZATORA")

    assert "KOMUNIKAT-ORGANIZATORA" in web_client.get("/login/").content.decode()
    assert "KOMUNIKAT-ORGANIZATORA" in web_client.get("/register/").content.decode()


def test_the_banner_escapes_the_text(web_client):
    """Pasek na każdej stronie serwisu nie jest miejscem na znacznik od redaktora."""
    announcement(text="<script>alert(1)</script> awaria")

    body = web_client.get("/login/").content.decode()

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_link_needs_both_the_address_and_the_label(web_client):
    """Sam adres bez etykiety byłby gołym URL-em wklejonym w środek zdania."""
    announcement(link_url="https://example.test/info")

    body = web_client.get("/login/").content.decode()

    assert "https://example.test/info" not in body


def test_the_link_shows_with_both_fields(web_client):
    announcement(link_url="https://example.test/info", link_label="Szczegóły")

    body = web_client.get("/login/").content.decode()

    assert "https://example.test/info" in body
    assert "Szczegóły" in body


def test_a_dismissible_banner_carries_the_close_button_hidden_until_the_script_runs(web_client):
    """Przycisk jest obietnicą, że kliknięcie coś zrobi – bez skryptu byłby atrapą."""
    announcement(dismissible=True)

    body = web_client.get("/login/").content.decode()

    assert "data-announcement-dismissible" in body
    assert "data-announcement-close" in body
    assert "hidden" in body


def test_a_non_dismissible_banner_has_no_close_button_at_all(web_client):
    """Decyzja organizatora zapisana w modelu: awarii i terminu nie wolno schować."""
    announcement(dismissible=False)

    body = web_client.get("/login/").content.decode()

    assert "data-announcement-close" not in body


def test_the_dismissal_script_is_loaded_with_a_nonce(web_client):
    """Skrypt bez znacznika w szablonie to przycisk zamknięcia, który nigdy się nie odsłoni."""
    announcement(dismissible=True)

    body = web_client.get("/login/").content.decode()

    assert "js/announcements.js" in body
    # Polityka CSP nie dopuszcza skryptów inline – każdy znacznik ma nonce.
    assert "nonce=" in body


def test_a_danger_announcement_is_announced_to_screen_readers(web_client):
    announcement(level=AnnouncementLevel.DANGER)

    assert 'role="alert"' in web_client.get("/login/").content.decode()


def test_the_editorial_panel_never_shows_the_banner(web_client, coordinator):
    """Pasek wstrzyknięty w cudzy layout rozsypuje go, zamiast cokolwiek ogłosić."""
    announcement(text="KOMUNIKAT-ORGANIZATORA")
    web_client.force_login(coordinator)

    body = web_client.get("/cms/").content.decode()

    assert "KOMUNIKAT-ORGANIZATORA" not in body


# --- ekran koordynatora ---------------------------------------------------------------------


def test_the_panel_is_for_the_coordinator_only(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(PANEL_URL).status_code == 403


def test_the_coordinator_can_publish_an_announcement(web_client, coordinator):
    web_client.force_login(coordinator)

    response = web_client.post(
        PANEL_URL,
        {
            "text": "Wysyłka prac jest chwilowo niedostępna.",
            "level": AnnouncementLevel.DANGER,
            "starts_at": "",
            "ends_at": "",
            "link_url": "",
            "link_label": "",
            "is_active": "on",
        },
    )

    item = Announcement.objects.get()
    assert response.status_code == 302
    assert item.level == AnnouncementLevel.DANGER
    assert item.created_by_id == coordinator.pk
    # Puste „od” znaczy „od zaraz”: organizator ogłaszający awarię nie przepisuje godziny z zegarka.
    assert item.is_live()


def test_publishing_leaves_an_audit_entry_without_the_body(web_client, coordinator):
    web_client.force_login(coordinator)

    web_client.post(
        PANEL_URL,
        {
            "text": "TRESC-KOMUNIKATU",
            "level": AnnouncementLevel.INFO,
            "starts_at": "",
            "ends_at": "",
            "link_url": "",
            "link_label": "",
            "is_active": "on",
        },
    )

    from apps.core.models import AuditLog

    entry = AuditLog.objects.filter(action="announcement.created").get()
    assert entry.actor_id == coordinator.pk
    assert "TRESC-KOMUNIKATU" not in str(entry.diff)


def test_the_coordinator_can_delete_an_announcement(web_client, coordinator):
    item = announcement()
    web_client.force_login(coordinator)

    web_client.post(PANEL_URL, {"action": "delete", "pk": item.pk})

    from apps.core.models import AuditLog

    assert not Announcement.objects.filter(pk=item.pk).exists()
    assert AuditLog.objects.filter(action="announcement.deleted").exists()


def test_the_panel_marks_which_announcements_are_on_air(web_client, coordinator):
    announcement(text="NA-ANTENIE")
    announcement(text="WYLACZONY", is_active=False)
    web_client.force_login(coordinator)

    body = web_client.get(PANEL_URL).content.decode()

    assert "na antenie" in body
    assert "wyłączony" in body
