"""Wiadomości – ekrany uczestnika: bramki, skrzynka, wątek (HTMX), katalog, zgłoszenie, ustawienia.

Reguły mają własną suitę (``apps/chat/tests``); tutaj sprawdzamy to, czego serwis nie widzi: kody
odpowiedzi pod adresami, to, co trafia do HTML-a (a co **nie** trafia – e-mail, kod, szkoła, skrypt),
i drogę formularza przez htmx.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import UserFactory
from apps.chat import services
from apps.chat.models import ChatProfile, Message, MessageReport, MessageStatus, PeerMode
from apps.chat.tests.helpers import (
    configure,
    coordinator_of,
    give_key,
    in_directory,
    participant_of,
    start,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db

INBOX = "/me/messages/"
ORGANIZER = "/me/messages/organizer/"
DIRECTORY = "/me/messages/new/"
KEY = "/me/messages/key/"
PREFERENCES = "/account/chat-settings/"
HTMX = {"HTTP_HX_REQUEST": "true"}


def thread_url(conversation) -> str:
    return f"/me/messages/{conversation.pk}/"


def logged(client, participant):
    client.force_login(participant.user)
    return client


# --- bramki ------------------------------------------------------------------------------------------


def test_anonymous_is_sent_to_login(web_client, competition):
    response = web_client.get(INBOX)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


@pytest.mark.parametrize(
    "role", [CompetitionRole.REVIEWER, CompetitionRole.SUPERVISOR, CompetitionRole.APPEALS]
)
@pytest.mark.parametrize(
    "url",
    [INBOX, ORGANIZER, DIRECTORY, KEY, "/me/messages/new/abc/", "/me/messages/1/", "/me/messages/1/report/"],
)
def test_other_roles_get_403_on_every_participant_screen(web_client, competition, role, url):
    user = UserFactory(groups=[role])
    grant_membership(user, competition, role)
    web_client.force_login(user)

    assert web_client.get(url).status_code == 403
    assert web_client.post(url, {"body": "x"}).status_code == 403


def test_disabled_module_is_404_and_leaves_the_menus(web_client, competition):
    configure(competition, enabled=False)
    ala = participant_of(competition)
    client = logged(web_client, ala)

    assert client.get(INBOX).status_code == 404
    assert 'href="/me/messages/"' not in client.get("/me/").content.decode()


# --- skrzynka i wątek ------------------------------------------------------------------------------


def test_empty_inbox_offers_writing_to_the_organizer(web_client, competition):
    client = logged(web_client, participant_of(competition))

    body = client.get(INBOX).content.decode()

    assert "Nie masz jeszcze żadnych wiadomości" in body
    assert f'href="{ORGANIZER}"' in body
    # Kanał między uczestnikami jest domyślnie wyłączony – nie ma „Nowej rozmowy”.
    assert f'href="{DIRECTORY}"' not in body


def test_first_message_to_the_organizer_creates_the_conversation(web_client, competition):
    ala = participant_of(competition)
    client = logged(web_client, ala)

    assert client.get(ORGANIZER).status_code == 200
    response = client.post(ORGANIZER, {"body": "Gdzie jest sala?"})

    message = Message.objects.get()
    assert response.status_code == 302
    assert response["Location"] == thread_url(message.conversation)
    assert "Gdzie jest sala?" in client.get(response["Location"]).content.decode()
    # Druga wizyta pod „Napisz do organizatora” prowadzi do tej samej rozmowy.
    assert client.get(ORGANIZER)["Location"] == thread_url(message.conversation)


def test_htmx_post_returns_the_thread_fragment_with_the_new_message(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pierwsza"
    ).conversation
    client = logged(web_client, ala)

    response = client.post(thread_url(conversation), {"body": "Druga przez htmx"}, **HTMX)

    body = response.content.decode()
    assert response.status_code == 200
    assert 'id="chat-thread"' in body
    assert "Druga przez htmx" in body
    assert "<html" not in body


def test_polling_returns_204_when_nothing_changed(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="Pierwsza"
    ).conversation
    client = logged(web_client, ala)
    version = services.thread_version(conversation, ala)

    unchanged = client.get(f"{thread_url(conversation)}?fragment=messages&v={version}", **HTMX)
    services.organizer_writes_to(
        user=coordinator_of(competition), competition=competition, participant=ala, body="Odpowiedź"
    )
    changed = client.get(f"{thread_url(conversation)}?fragment=messages&v={version}", **HTMX)

    assert unchanged.status_code == 204
    assert changed.status_code == 200
    assert "Odpowiedź" in changed.content.decode()
    assert 'hx-trigger="every 15s, chat-visible from:document"' in changed.content.decode()


def test_script_in_a_message_is_escaped(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(
        user=ala.user, competition=competition, body="<script>alert('xss')</script> https://example.org"
    ).conversation

    body = logged(web_client, ala).get(thread_url(conversation)).content.decode()

    assert "<script>alert(" not in body
    assert "&lt;script&gt;alert(" in body
    assert 'rel="nofollow noopener noreferrer"' in body


def test_a_name_cannot_impersonate_the_organizer_badge(web_client, competition):
    """Pakiet 5, E16: odznaka „Organizator” zależy wyłącznie od ``sender_role`` wiadomości.

    Imię „Organizator · Anna” (wpisane z pominięciem walidatora – np. konto sprzed reguły) jest
    zwykłym tekstem w polu autora; prawdziwy organizator dostaje osobny element odznaki.
    """
    configure(competition, peer_mode=PeerMode.NONE)
    impostor = participant_of(competition, "Organizator · Anna", "")
    ola = participant_of(competition, "Ola", "Nowak")
    peer = start(impostor, ola, competition, body="Podaj hasło").conversation
    boss = coordinator_of(competition, first_name="Beata")
    official = services.organizer_writes_to(
        user=boss, competition=competition, participant=ola, body="Prawdziwy komunikat"
    ).conversation
    client = logged(web_client, ola)

    peer_page = client.get(thread_url(peer)).content.decode()
    official_page = client.get(thread_url(official)).content.decode()

    assert 'data-sender-role="organizer"' not in peer_page.split('id="chat-messages"', 1)[1]
    assert '<span class="chat-msg__author">Organizator · Anna</span>' in peer_page
    assert 'data-sender-role="organizer">Organizator</span>' in official_page
    assert '<span class="chat-msg__author">Beata' in official_page


def test_conversation_of_another_competition_is_404(web_client, competition, other_competition):
    ala = participant_of(competition)
    stranger = participant_of(other_competition, "Obca", "Osoba")
    foreign = services.write_to_organizer(
        user=stranger.user, competition=other_competition, body="x"
    ).conversation

    assert logged(web_client, ala).get(thread_url(foreign)).status_code == 404


def test_someone_elses_conversation_is_404(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola, eva = (
        participant_of(competition),
        participant_of(competition, "Ola", "Nowak"),
        participant_of(competition, "Ewa", "Zych"),
    )
    conversation = start(ala, ola, competition).conversation

    assert logged(web_client, eva).get(thread_url(conversation)).status_code == 404


def test_pending_message_is_invisible_to_the_recipient(web_client, competition):
    configure(competition, peer_mode=PeerMode.PRE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition, body="Czeka").conversation

    sender_view = logged(web_client, ala).get(thread_url(conversation)).content.decode()
    web_client.logout()
    recipient = logged(web_client, ola)

    assert "czeka na akceptację" in sender_view
    assert recipient.get(thread_url(conversation)).status_code == 404
    assert "Czeka" not in recipient.get(INBOX).content.decode()


def test_privacy_notice_depends_on_the_mode(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    conversation = start(ala, ola, competition).conversation
    client = logged(web_client, ala)

    assert (
        "Organizator widzi tylko zgłoszone wiadomości."
        in client.get(thread_url(conversation)).content.decode()
    )
    configure(competition, peer_mode=PeerMode.POST)
    assert "mogą być czytane przez organizatora" in client.get(thread_url(conversation)).content.decode()


# --- katalog -------------------------------------------------------------------------------------------


def test_directory_shows_only_name_initial_and_province(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala = participant_of(competition)
    ola = participant_of(competition, "Ola", "Nazwiskowa", school="LO im. Tajnej Szkoły")
    in_directory(ola)

    body = logged(web_client, ala).get(DIRECTORY).content.decode()

    assert "Ola N." in body
    assert ola.get_district_display() in body
    assert ola.user.email not in body
    assert ola.public_code not in body
    assert "Tajnej Szkoły" not in body
    assert "Nazwiskowa" not in body
    assert f"/me/messages/new/{ola.chat_profile.token}/" in body
    assert f"/{ola.pk}/" not in body


def test_directory_is_404_when_the_peer_channel_is_off(web_client, competition):
    assert logged(web_client, participant_of(competition)).get(DIRECTORY).status_code == 404


def test_starting_a_conversation_from_the_directory(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    token = in_directory(ola).token
    client = logged(web_client, ala)

    assert client.get(f"/me/messages/new/{token}/").status_code == 200
    response = client.post(f"/me/messages/new/{token}/", {"body": "Cześć, Ola!"})

    message = Message.objects.get()
    assert response["Location"] == thread_url(message.conversation)


def test_non_discoverable_person_cannot_be_reached_by_token(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    profile, _ = ChatProfile.objects.get_or_create(participant=ola)

    assert logged(web_client, ala).get(f"/me/messages/new/{profile.token}/").status_code == 404


# --- zgłoszenie i blokada ------------------------------------------------------------------------------


def test_report_and_block_from_the_thread(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    message = start(ala, ola, competition, body="Nieprzyjemna")
    client = logged(web_client, ola)
    url = thread_url(message.conversation)

    page = client.get(url).content.decode()
    client.post(f"{url}report/", {"message": message.pk, "reason": "Obraża"})
    client.post(f"{url}block/", {"action": "block"})

    assert "Zgłoś" in page
    assert "Zablokuj" in page
    assert MessageReport.objects.filter(message=message, reporter=ola.user).exists()
    assert services.has_blocked(ola, ala)
    assert "Odblokuj" in client.get(url).content.decode()


def test_the_organizer_cannot_be_blocked(web_client, competition):
    ala = participant_of(competition)
    conversation = services.write_to_organizer(user=ala.user, competition=competition, body="x").conversation

    response = logged(web_client, ala).post(f"{thread_url(conversation)}block/", {"action": "block"})

    assert response.status_code == 404


# --- liczniki w menu ---------------------------------------------------------------------------------------


def test_unread_counter_in_the_account_bar_and_the_panel(web_client, competition):
    ala = participant_of(competition)
    services.organizer_writes_to(
        user=coordinator_of(competition), competition=competition, participant=ala, body="x"
    )
    client = logged(web_client, ala)

    body = client.get("/me/").content.decode()

    assert 'href="/me/messages/"' in body
    assert "nieprzeczytane rozmowy: </span>1</span>" in body


# --- rozmowa szyfrowana ------------------------------------------------------------------------------------


def test_encrypted_thread_has_no_named_plaintext_field_and_refuses_plaintext(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    give_key(ala)
    give_key(ola)
    token = in_directory(ola).token
    client = logged(web_client, ala)

    client.post(f"/me/messages/new/{token}/", {"action": "open-encrypted"})
    conversation = services.find_peer_conversation(ala, ola)
    body = client.get(thread_url(conversation)).content.decode()
    client.post(thread_url(conversation), {"body": "jawna treść"}, **HTMX)

    assert conversation.is_encrypted
    assert "data-e2e-input" in body
    assert 'name="body"' not in body
    assert "chat-e2e.js" in body
    assert not Message.objects.exists()


def test_key_page_posts_only_public_material(web_client, competition):
    configure(competition, peer_mode=PeerMode.NONE, e2e_enabled=True)
    from apps.chat.tests.helpers import key_fields

    ala = participant_of(competition)
    client = logged(web_client, ala)

    page = client.get(KEY).content.decode()
    response = client.post(KEY, key_fields())

    assert "data-e2e-new-passphrase" in page
    assert 'name="passphrase' not in page
    assert response.status_code == 302
    assert services.key_for(ala) is not None


# --- ustawienia ------------------------------------------------------------------------------------------


def test_preferences_on_the_profile_screen(web_client, competition):
    ala = participant_of(competition)
    client = logged(web_client, ala)

    page = client.get("/me/profile/").content.decode()
    client.post(PREFERENCES, {"discoverable": "on"})

    assert 'id="wiadomosci"' in page
    assert services.profile_for(ala).discoverable is True
    assert services.notifications.preferences_for(ala.user).email_on_message is False


# --- dane konta --------------------------------------------------------------------------------------------


def test_export_contains_own_messages_only(web_client, competition):
    import json

    from apps.web.tests.test_account_export import payload

    configure(competition, peer_mode=PeerMode.NONE)
    ala, ola = participant_of(competition), participant_of(competition, "Ola", "Nowak")
    first = start(ala, ola, competition, body="Moja wiadomość")
    services.send_participant_message(
        user=ola.user, competition=competition, conversation=first.conversation, body="Cudza odpowiedź"
    )
    client = logged(web_client, ala)

    data = payload(client.get("/account/export/"))

    sent = data["wiadomosci_wyslane"]
    assert [row["tresc"] for row in sent] == ["Moja wiadomość"]
    assert "Cudza odpowiedź" not in json.dumps(data, ensure_ascii=False)
    assert data["ustawienia_wiadomosci"]["list_o_nowej_wiadomosci"] is True
    assert first.status == MessageStatus.PUBLISHED
