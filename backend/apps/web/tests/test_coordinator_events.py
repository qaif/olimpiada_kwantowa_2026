"""Wydarzenia linii czasu w panelu koordynatora (``/coordinator/events/…``).

Pięć rzeczy, na których ten ekran stoi:

- dodanie, zmiana i usunięcie zapisują ``EditionEvent`` **i** zostawiają ślad w audycie,
- odnośnik jest sprawdzany co do schematu: w ``href`` z linii czasu klika publiczność,
  a nie koordynator, więc ``javascript:`` i adres podszywający się pod ścieżkę są odrzucane,
- koniec przed początkiem nie zapisuje niczego, a błąd stoi pod właściwym polem,
- dopisane wydarzenie jest w nagłówku **od razu** – serwis zdejmuje bufor linii czasu,
- ekran należy wyłącznie do koordynatora (uczestnik i recenzent dostają 403).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import EditionEvent
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

#: Termin w przyszłości – wydarzenie w kalendarzu bieżącej edycji.
SOON = timezone.localdate() + timedelta(days=60)
LATER = SOON + timedelta(days=2)


def form_data(**overrides) -> dict:
    """Komplet pól ``EditionEventForm``. Puste „do” znaczy wydarzenie jednodniowe."""
    data = {
        "title": "Gala finałowa",
        "starts_on": SOON.isoformat(),
        "ends_on": "",
        "note": "Kraków, ICE",
        "url": "/warsztaty/",
        "show_on_timeline": "on",
    }
    data.update(overrides)
    return data


# --- dodanie ------------------------------------------------------------------------------------


def test_create_saves_the_event_and_writes_audit(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post("/coordinator/events/new/", form_data())

    assert response.status_code == 302
    event = EditionEvent.objects.get(title="Gala finałowa")
    assert event.edition == elim_stage.edition
    assert event.starts_on == SOON
    # Puste „do” zostaje puste w bazie, ale ``date_range`` mówi „jeden dzień” – jedno wejście
    # dla wszystkich czytających, więc nigdzie nie powstaje ekran z połową terminu.
    assert event.ends_on is None
    assert event.date_range == (SOON, SOON)
    assert event.created_by == coordinator

    entry = AuditLog.objects.get(action="event.created", target_id=str(event.pk))
    assert entry.actor == coordinator
    assert entry.diff["title"] == "Gala finałowa"
    assert entry.diff["starts_on"] == SOON.isoformat()


def test_create_without_a_current_edition_refuses(web_client, coordinator, edition):
    """Wydarzenie nie ma do czego należeć – i mówimy to wprost, zamiast zapisywać sierotę."""
    edition.is_current = False
    edition.save()
    web_client.force_login(coordinator)

    response = web_client.post("/coordinator/events/new/", form_data(), follow=True)

    assert EditionEvent.objects.count() == 0
    assert "Nie ustawiono bieżącej edycji" in response.content.decode()


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "//evil.example/warsztaty/", "ftp://example.org/plik"],
)
def test_url_outside_http_and_site_paths_is_rejected(web_client, coordinator, elim_stage, url):
    """Adres z linii czasu klika publiczność – schemat jest tu regułą bezpieczeństwa."""
    web_client.force_login(coordinator)

    response = web_client.post("/coordinator/events/new/", form_data(url=url))

    assert response.status_code == 400
    assert EditionEvent.objects.count() == 0


@pytest.mark.parametrize("url", ["/warsztaty/", "https://example.org/gala", ""])
def test_site_paths_and_https_are_accepted(web_client, coordinator, elim_stage, url):
    web_client.force_login(coordinator)

    response = web_client.post("/coordinator/events/new/", form_data(url=url))

    assert response.status_code == 302
    assert EditionEvent.objects.get().url == url


def test_end_before_start_is_refused_under_the_field(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        "/coordinator/events/new/",
        form_data(starts_on=LATER.isoformat(), ends_on=SOON.isoformat()),
    )

    assert response.status_code == 400
    assert EditionEvent.objects.count() == 0
    assert "ends_on" in response.context["form"].errors


def test_title_is_required(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post("/coordinator/events/new/", form_data(title="   "))

    assert response.status_code == 400
    assert EditionEvent.objects.count() == 0


# --- zmiana i usunięcie -------------------------------------------------------------------------


@pytest.fixture
def event(elim_stage):
    return EditionEvent.objects.create(
        edition=elim_stage.edition, title="Dzień otwarty", starts_on=SOON, note="online"
    )


def test_edit_saves_only_the_changed_fields(web_client, coordinator, event):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/events/{event.pk}/edit/",
        form_data(title="Dzień otwarty", note="online", url="", ends_on=LATER.isoformat()),
    )

    event.refresh_from_db()
    assert response.status_code == 302
    assert event.ends_on == LATER
    entry = AuditLog.objects.get(action="event.updated", target_id=str(event.pk))
    # W audycie stoi wyłącznie to, co faktycznie się zmieniło – otwarcie i zapisanie formularza
    # bez zmian nie może zostawić wpisu o zmianie sześciu pól.
    assert set(entry.diff) == {"ends_on"}
    assert entry.diff["ends_on"] == {"from": None, "to": LATER.isoformat()}


def test_edit_without_changes_writes_no_audit(web_client, coordinator, event):
    web_client.force_login(coordinator)

    web_client.post(
        f"/coordinator/events/{event.pk}/edit/",
        form_data(title="Dzień otwarty", note="online", url=""),
    )

    assert not AuditLog.objects.filter(action="event.updated").exists()


def test_hiding_an_event_keeps_it_on_the_coordinator_list(web_client, coordinator, event):
    """Schowane wydarzenie znika z nagłówka, ale zostaje na liście – inaczej nie da się go odsłonić."""
    web_client.force_login(coordinator)

    web_client.post(
        f"/coordinator/events/{event.pk}/edit/",
        form_data(title="Dzień otwarty", note="online", url="", show_on_timeline=""),
    )

    event.refresh_from_db()
    assert event.show_on_timeline is False
    assert "Dzień otwarty" in web_client.get("/coordinator/events/").content.decode()


def test_delete_removes_the_event_and_keeps_its_content_in_the_audit(web_client, coordinator, event):
    web_client.force_login(coordinator)

    response = web_client.post(f"/coordinator/events/{event.pk}/delete/")

    assert response.status_code == 302
    assert not EditionEvent.objects.filter(pk=event.pk).exists()
    entry = AuditLog.objects.get(action="event.deleted")
    assert entry.diff["title"] == "Dzień otwarty"
    assert entry.diff["starts_on"] == SOON.isoformat()


# --- lista, pulpit i pasek ----------------------------------------------------------------------


def test_list_shows_the_events_of_the_current_edition(web_client, coordinator, event):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/events/").content.decode()

    assert "Dzień otwarty" in content
    assert f"/coordinator/events/{event.pk}/edit/" in content
    assert f"/coordinator/events/{event.pk}/delete/" in content


def test_dashboard_links_to_the_events_screen(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert "/coordinator/events/" in content
    assert "Wydarzenia (linia czasu)" in content


def test_new_event_shows_up_in_the_strip_without_waiting_for_the_cache(web_client, coordinator, elim_stage):
    """Koordynator sprawdza swoją pracę na stronie – ekran nie może jej nie znać przez pięć minut."""
    web_client.force_login(coordinator)
    # Pasek trafia do bufora już przy wejściu na pulpit; dopisanie wydarzenia musi go zdjąć.
    assert "Gala finałowa" not in web_client.get("/coordinator/").content.decode()

    web_client.post("/coordinator/events/new/", form_data())

    assert "Gala finałowa" in web_client.get("/coordinator/").content.decode()


def test_event_with_a_url_becomes_a_link_on_the_strip(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)
    web_client.post("/coordinator/events/new/", form_data(url="/warsztaty/"))

    content = web_client.get("/coordinator/").content.decode()

    assert 'href="/warsztaty/"' in content


def test_single_day_event_is_announced_with_one_date(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)
    web_client.post("/coordinator/events/new/", form_data(starts_on=SOON.isoformat(), ends_on=""))

    content = web_client.get("/coordinator/").content.decode()

    assert f"{SOON.day:02d}.{SOON.month:02d}.{SOON.year}" in content


# --- uprawnienia --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/coordinator/events/", "/coordinator/events/new/"],
)
def test_other_roles_get_403(web_client, participant, path):
    web_client.force_login(participant.user)

    assert web_client.get(path).status_code == 403


def test_anonymous_is_sent_to_login(web_client):
    response = web_client.get("/coordinator/events/")

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_reviewer_cannot_delete_an_event(web_client, reviewer, event):
    web_client.force_login(reviewer.user)

    assert web_client.post(f"/coordinator/events/{event.pk}/delete/").status_code == 403
    assert EditionEvent.objects.filter(pk=event.pk).exists()


def test_unknown_event_id_gives_404(web_client, coordinator, event):
    """Adres z nieistniejącym identyfikatorem ma dać 404, a nie pięćsetkę w środku formularza."""
    web_client.force_login(coordinator)

    assert web_client.get(f"/coordinator/events/{event.pk + 999}/edit/").status_code == 404


def test_event_of_the_current_edition_lands_on_the_public_strip(web_client, coordinator, elim_stage):
    """Pasek jest w nagłówku, więc wydarzenie widzi także niezalogowany czytelnik."""
    web_client.force_login(coordinator)
    web_client.post("/coordinator/events/new/", form_data(title="Gala finałowa", url=""))
    web_client.logout()

    content = web_client.get("/login/").content.decode()

    assert "Gala finałowa" in content
