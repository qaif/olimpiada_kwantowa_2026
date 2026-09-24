"""Ekran ``/coordinator/messages/`` – wysyłka komunikatów organizatora.

Reguły doboru odbiorców i podziału na porcje mają własne testy w
``apps/accounts/tests/test_messaging.py``. Tutaj sprawdzamy sam ekran, a w nim przede wszystkim
jedyny bezpiecznik przed omyłkową wysyłką do kilku tysięcy osób: **podgląd nic nie wysyła**.
"""

import re

import pytest
from django.core import mail

from apps.accounts.models import BroadcastGroup, BroadcastStatus, MessageBroadcast, Region
from apps.accounts.tests.factories import ParticipantFactory, UserFactory
from apps.competitions.tests.factories import StageEntryFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

BASE = {"subject": "Zmiana terminu", "body": "Etap rusza tydzień później."}


SIGNATURE = re.compile(r'name="preview_signature" value="([0-9a-f]+)"')


def _post(client, data, *, action="preview"):
    """Podgląd albo – dla ``action="send"`` – dokładnie to, co robi koordynator: podgląd, potem „Wyślij”.

    Wysyłka bez podglądu nie przechodzi (podpis podglądu), więc pomocnik najpierw ogląda, zabiera
    podpis z ukrytego pola i dopiero z nim wysyła. Przypadki „bez podglądu” i „coś zmieniono po
    podglądzie” mają własne testy niżej.
    """
    if action != "send":
        return client.post("/coordinator/messages/", {**data, "action": action})
    preview = client.post("/coordinator/messages/", {**data, "action": "preview"}).content.decode()
    match = SIGNATURE.search(preview)
    signature = match.group(1) if match else ""
    return client.post("/coordinator/messages/", {**data, "action": "send", "preview_signature": signature})


def test_preview_shows_the_count_and_sends_nothing(web_client, coordinator, elim_stage, entry):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    response = _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS})
    content = response.content.decode()

    assert response.status_code == 200
    assert "1 odbiorców" in content
    # Treść widać dokładnie tak, jak pójdzie w liście – to jedyny moment, w którym pomyłkę
    # da się jeszcze cofnąć.
    assert BASE["body"] in content
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_send_queues_one_letter_per_recipient_and_records_the_broadcast(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    second = ParticipantFactory(user=UserFactory(email="druga@example.test"))
    StageEntryFactory(participant=second, stage=elim_stage)
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    assert response.status_code == 302
    # Jeden list na odbiorcę – nigdy jedna koperta z listą adresów w nagłówku.
    assert len(mail.outbox) == 2
    assert all(len(message.to) == 1 for message in mail.outbox)
    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 2
    assert broadcast.status == BroadcastStatus.SENT
    assert broadcast.created_by == coordinator


def test_audit_records_counters_but_no_addresses(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["recipients"] == 1
    assert entry.participant.user.email not in str(log.diff)


def test_custom_list_requires_at_least_one_address(web_client, coordinator):
    web_client.force_login(coordinator)

    response = _post(web_client, {**BASE, "group": BroadcastGroup.CUSTOM, "addresses": "  "})

    assert response.status_code == 200
    assert "Wklej przynajmniej jeden adres." in response.content.decode()


def test_stage_group_requires_a_stage(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = _post(web_client, {**BASE, "group": BroadcastGroup.STAGE_REGISTERED})

    assert response.status_code == 200
    assert "wymaga wskazania etapu" in response.content.decode()


def test_sending_to_an_empty_group_records_nothing(
    web_client, coordinator, elim_stage, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = _post(
            web_client,
            {**BASE, "group": BroadcastGroup.STAGE_REGISTERED, "stage": elim_stage.pk},
            action="send",
        )

    assert response.status_code == 200
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_history_lists_past_broadcasts(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS}, action="send")

    response = web_client.get("/coordinator/messages/")

    assert BASE["subject"] in response.content.decode()


def test_send_without_a_preview_sends_nothing_and_shows_the_preview(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            "/coordinator/messages/", {**BASE, "group": BroadcastGroup.EDITION_PARTICIPANTS, "action": "send"}
        )

    content = response.content.decode()
    assert response.status_code == 200
    assert "zmieniły się od podglądu" in content
    assert "1 odbiorców" in content
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_switching_the_group_after_the_preview_does_not_send(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    """Podgląd listu do jednego etapu nie jest przepustką dla listu do wszystkich uczestników."""
    ParticipantFactory(user=UserFactory(email="bez-wpisu@example.test"))
    web_client.force_login(coordinator)
    mail.outbox.clear()
    narrow = {**BASE, "group": BroadcastGroup.STAGE_REGISTERED, "stage": elim_stage.pk}
    signature = SIGNATURE.search(_post(web_client, narrow).content.decode()).group(1)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            "/coordinator/messages/",
            {
                **narrow,
                "group": BroadcastGroup.ALL_PARTICIPANTS,
                "action": "send",
                "preview_signature": signature,
            },
        )

    assert response.status_code == 200
    assert "2 odbiorców" in response.content.decode()
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_page_is_forbidden_for_a_reviewer(web_client, reviewer):
    web_client.force_login(reviewer.user)

    assert web_client.get("/coordinator/messages/").status_code == 403


def test_dashboard_links_to_the_messages_screen(web_client, coordinator):
    web_client.force_login(coordinator)

    assert "/coordinator/messages/" in web_client.get("/coordinator/").content.decode()


# --- grupy z 24.09.2026 ---------------------------------------------------------------------------


def _options(content: str, name: str) -> list[str]:
    """Wartości ``<option>`` listy wyboru ``name`` – w kolejności, w jakiej widzi je koordynator."""
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', content, re.S)
    assert select, f"brak listy {name}"
    return re.findall(r'<option value="([^"]*)"', select.group(1))


def test_all_participants_is_the_first_group_on_the_list(web_client, coordinator):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/messages/").content.decode()

    assert _options(content, "group")[0] == BroadcastGroup.ALL_PARTICIPANTS


def test_all_participants_preview_counts_people_without_stage_entries_and_nobody_else(
    web_client, coordinator, entry, other_competition
):
    ParticipantFactory(user=UserFactory(email="bez-wpisu@example.test"))
    ParticipantFactory(user=UserFactory(email="sasiad@example.test"), competition=other_competition)
    web_client.force_login(coordinator)
    mail.outbox.clear()

    content = _post(web_client, {**BASE, "group": BroadcastGroup.ALL_PARTICIPANTS}).content.decode()

    assert "2 odbiorców" in content
    assert "wszyscy uczestnicy konkursu" in content
    assert not mail.outbox


@pytest.mark.parametrize(
    ("group", "message"),
    [
        (BroadcastGroup.STAGE_NO_SUBMISSION, "wymaga wskazania etapu"),
        (BroadcastGroup.REGION_PARTICIPANTS, "Wybierz województwo uczestników."),
        (BroadcastGroup.COMMITTEE_DISTRICT, "Wybierz województwo komitetu."),
        (BroadcastGroup.SCHOOL_PARTICIPANTS, "Wybierz szkołę."),
        (BroadcastGroup.GRADE_PARTICIPANTS, "Wybierz klasę."),
        (BroadcastGroup.WORKSHOP_ATTENDEES, "Wybierz warsztat."),
    ],
)
def test_group_with_a_parameter_requires_it(web_client, coordinator, group, message):
    web_client.force_login(coordinator)
    mail.outbox.clear()

    response = _post(web_client, {**BASE, "group": group}, action="send")

    assert response.status_code == 200
    assert message in response.content.decode()
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_school_of_another_competition_is_not_a_valid_choice(web_client, coordinator, other_competition):
    from apps.schools.tests.factories import SchoolFactory

    school = SchoolFactory()
    ParticipantFactory(competition=other_competition, school_ref=school)
    web_client.force_login(coordinator)

    response = _post(
        web_client, {**BASE, "group": BroadcastGroup.SCHOOL_PARTICIPANTS, "school": f"sio:{school.pk}"}
    )

    assert "Wybierz poprawną wartość" in response.content.decode()


def test_region_screen_uses_custom_regions_when_the_flag_is_on(web_client, coordinator, competition, edition):
    north = Region.objects.create(competition=competition, code="okreg-polnoc", name="Okręg Północ")
    ParticipantFactory(user=UserFactory(email="polnoc@example.test"), region=north)
    web_client.force_login(coordinator)
    assert 'name="region"' not in web_client.get("/coordinator/messages/").content.decode()

    competition.feature_flags = {**(competition.feature_flags or {}), "custom_regions": True}
    competition.save(update_fields=["feature_flags"])
    missing = _post(web_client, {**BASE, "group": BroadcastGroup.REGION_PARTICIPANTS}).content.decode()
    preview = _post(
        web_client, {**BASE, "group": BroadcastGroup.REGION_PARTICIPANTS, "region": north.pk}
    ).content.decode()

    assert "Wybierz region uczestników." in missing
    assert "1 odbiorców" in preview
    assert "Okręg Północ" in preview


def test_send_to_a_school_records_which_school_in_the_history(
    web_client, coordinator, edition, django_capture_on_commit_callbacks
):
    from apps.schools.tests.factories import SchoolFactory

    school = SchoolFactory(name="XIV LO im. Staszica", city="Warszawa")
    ParticipantFactory(user=UserFactory(email="staszic@example.test"), school_ref=school)
    ParticipantFactory(user=UserFactory(email="inna@example.test"))
    web_client.force_login(coordinator)
    mail.outbox.clear()
    data = {**BASE, "group": BroadcastGroup.SCHOOL_PARTICIPANTS, "school": f"sio:{school.pk}"}

    preview = _post(web_client, data).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, data, action="send")

    assert "XIV LO im. Staszica, Warszawa" in preview
    assert [message.to for message in mail.outbox] == [["staszic@example.test"]]
    broadcast = MessageBroadcast.objects.get()
    label = "XIV LO im. Staszica, Warszawa (bieżąca edycja)"
    assert broadcast.target == {"school": f"sio:{school.pk}", "past_editions": False, "label": label}
    history = web_client.get("/coordinator/messages/").content.decode()
    assert label in history
    log = AuditLog.objects.get(action="broadcast.sent")
    assert log.diff["target"]["label"] == label


def test_stage_parameter_is_recorded_for_the_no_submission_reminder(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    web_client.force_login(coordinator)
    data = {**BASE, "group": BroadcastGroup.STAGE_NO_SUBMISSION, "stage": elim_stage.pk}

    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, data, action="send")

    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 1
    assert broadcast.target == {"stage": elim_stage.pk, "label": str(elim_stage)}


def test_parameter_of_another_group_is_ignored(
    web_client, coordinator, elim_stage, entry, django_capture_on_commit_callbacks
):
    """Etap wybrany „przy okazji” nie zawęża listu do wszystkich i nie trafia do historii."""
    ParticipantFactory(user=UserFactory(email="bez-wpisu@example.test"))
    web_client.force_login(coordinator)

    with django_capture_on_commit_callbacks(execute=True):
        _post(
            web_client,
            {**BASE, "group": BroadcastGroup.ALL_PARTICIPANTS, "stage": elim_stage.pk},
            action="send",
        )

    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 2
    # Z parametrów zostaje wyłącznie zakres edycji – jedyne pole, które należy do tej grupy.
    assert broadcast.target == {"past_editions": False, "label": "bieżąca edycja"}


def test_workshop_group_on_the_screen(web_client, coordinator, django_capture_on_commit_callbacks):
    from datetime import date

    from apps.cms.models import ContentPage, HomePage, WorkshopAttendance
    from apps.cms.workshops import WORKSHOPS_SLUG, workshop_rows

    home = HomePage.objects.get()
    page = ContentPage(
        title="Warsztaty",
        slug=WORKSHOPS_SLUG,
        live=True,
        body=[
            (
                "schedule",
                {
                    "caption": "",
                    "topic_label": "Temat",
                    "date_label": "Termin",
                    "time_label": "",
                    "lecturer_label": "Prowadzący",
                    "rows": [
                        {
                            "topic": "Kubity i bramki",
                            "date": "12.11.2026",
                            "date_value": date(2026, 11, 12),
                            "time": "",
                            "lecturer": "",
                        }
                    ],
                },
            )
        ],
    )
    home.add_child(instance=page)
    page.save_revision().publish()
    key = workshop_rows(page)[0]["key"]
    present = ParticipantFactory(user=UserFactory(email="obecna@example.test"))
    ParticipantFactory(user=UserFactory(email="nieobecna@example.test"))
    WorkshopAttendance.objects.create(participant=present, workshop_key=key)
    web_client.force_login(coordinator)

    assert key in _options(web_client.get("/coordinator/messages/").content.decode(), "workshop")
    with django_capture_on_commit_callbacks(execute=True):
        _post(
            web_client, {**BASE, "group": BroadcastGroup.WORKSHOP_ATTENDEES, "workshop": key}, action="send"
        )

    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 1
    assert broadcast.target == {"workshop": key, "label": "12.11.2026 – Kubity i bramki"}


def test_history_hides_broadcasts_of_another_competition(
    web_client, coordinator, competition, other_competition
):
    from apps.tenancy.tests.factories import create_scoped

    create_scoped(
        MessageBroadcast, competition, group=BroadcastGroup.CUSTOM, subject="Nasz komunikat", body="."
    )
    create_scoped(
        MessageBroadcast, other_competition, group=BroadcastGroup.CUSTOM, subject="Cudzy komunikat", body="."
    )
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/messages/").content.decode()

    assert "Nasz komunikat" in content
    assert "Cudzy komunikat" not in content


def test_broadcast_belongs_to_the_competition_of_the_request(
    client_for, other_competition, django_capture_on_commit_callbacks
):
    """Koordynator konkursu B wysyła z domeny B – wiersz rejestru należy do B, odbiorcy też są z B."""
    from apps.accounts.models import GROUP_COORDINATOR
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.tenancy.tests.factories import grant_membership

    coordinator = CoordinatorFactory()
    grant_membership(coordinator, other_competition, GROUP_COORDINATOR)
    ParticipantFactory(user=UserFactory(email="tutejszy@example.test"))
    ParticipantFactory(user=UserFactory(email="w-b@example.test"), competition=other_competition)
    client = client_for(other_competition)
    client.force_login(coordinator)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        # Konkurs B nie ma bieżącej edycji – bez przełącznika grupa byłaby pusta.
        response = _post(
            client,
            {**BASE, "group": BroadcastGroup.ALL_PARTICIPANTS, "include_past_editions": "on"},
            action="send",
        )

    assert response.status_code == 302
    assert [message.to for message in mail.outbox] == [["w-b@example.test"]]
    assert MessageBroadcast.objects.get().competition == other_competition


# --- zakres edycji (decyzja organizatora z 24.09.2026) ----------------------------------------------


def _veteran(email: str):
    """Uczestnik z konta sprzed bieżącej edycji, bez wpisu do jej etapów."""
    from datetime import timedelta

    from django.utils import timezone

    return ParticipantFactory(user=UserFactory(email=email, date_joined=timezone.now() - timedelta(days=400)))


def test_all_participants_default_to_the_current_edition_and_the_switch_widens_it(
    web_client, coordinator, entry, django_capture_on_commit_callbacks
):
    _veteran("zeszloroczny@example.test")
    web_client.force_login(coordinator)
    mail.outbox.clear()
    data = {**BASE, "group": BroadcastGroup.ALL_PARTICIPANTS}

    narrow = _post(web_client, data).content.decode()
    wide = _post(web_client, {**data, "include_past_editions": "on"}).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        _post(web_client, {**data, "include_past_editions": "on"}, action="send")

    assert "1 odbiorców" in narrow
    assert "wszyscy uczestnicy konkursu: bieżąca edycja" in narrow
    assert "2 odbiorców" in wide
    assert "wszyscy uczestnicy konkursu: także poprzednie edycje" in wide
    broadcast = MessageBroadcast.objects.get()
    assert broadcast.recipient_count == 2
    assert broadcast.target == {"past_editions": True, "label": "także poprzednie edycje"}
    assert AuditLog.objects.get(action="broadcast.sent").diff["target"]["past_editions"] is True
    assert "także poprzednie edycje" in web_client.get("/coordinator/messages/").content.decode()


def test_ticking_past_editions_after_the_preview_does_not_send(
    web_client, coordinator, entry, django_capture_on_commit_callbacks
):
    """Podgląd „bieżąca edycja” nie jest przepustką dla listu do wszystkich roczników."""
    _veteran("zeszloroczny@example.test")
    web_client.force_login(coordinator)
    mail.outbox.clear()
    data = {**BASE, "group": BroadcastGroup.ALL_PARTICIPANTS}
    signature = SIGNATURE.search(_post(web_client, data).content.decode()).group(1)

    with django_capture_on_commit_callbacks(execute=True):
        response = web_client.post(
            "/coordinator/messages/",
            {**data, "include_past_editions": "on", "action": "send", "preview_signature": signature},
        )

    assert "zmieniły się od podglądu" in response.content.decode()
    assert not mail.outbox
    assert not MessageBroadcast.objects.exists()


def test_past_editions_switch_is_offered_only_to_the_groups_it_affects(web_client, coordinator):
    import json

    web_client.force_login(coordinator)
    content = web_client.get("/coordinator/messages/").content.decode()
    mapping = json.loads(re.search(r'id="broadcast-parameters"[^>]*>(.*?)</script>', content, re.S).group(1))

    with_switch = {group for group, fields in mapping.items() if "include_past_editions" in fields}
    assert with_switch == {
        BroadcastGroup.ALL_PARTICIPANTS,
        BroadcastGroup.REGION_PARTICIPANTS,
        BroadcastGroup.SCHOOL_PARTICIPANTS,
        BroadcastGroup.GRADE_PARTICIPANTS,
    }
    assert 'data-broadcast-param="include_past_editions"' in content
