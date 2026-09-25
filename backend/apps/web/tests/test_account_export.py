"""Eksport danych konta (art. 20 RODO): co jest w paczce, czego w niej nigdy nie ma i kto ją dostaje.

Przepis daje prawo do **swoich** danych w formacie do odczytu maszynowego. Te testy pilnują obu
połówek tego zdania:

- **swoich**: w paczce jest to, co uczestnik i tak widzi w panelu – zgody z wersjami dokumentów,
  zgłoszenia do etapów, metryka prac (razem z sha256), ogłoszony wynik i komentarze recenzentów
  napisane do niego. Nie ma tam ani komentarza wewnętrznego komitetu, ani cudzych wierszy tabeli
  wyników, ani hasła,
- **do odczytu maszynowego**: ZIP z ``dane.json`` i katalogiem plików, których uczestnik nie ma
  skąd inaczej odzyskać.

Do tego dwie rzeczy operacyjne: limit jednej paczki na dziesięć minut (budowa czyta cały storage
uczestnika) i druga droga – koordynator wydający paczkę na wniosek złożony poza serwisem, z własną
akcją w audycie.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.utils import timezone

from apps.accounts.consents import ConsentKind
from apps.accounts.models import ConsentRecord
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

EXPORT_URL = "/account/export/"


def archive(response) -> zipfile.ZipFile:
    """Odpowiedź ``FileResponse`` jako otwarte archiwum."""
    return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))


def payload(response) -> dict:
    with archive(response) as package:
        return json.loads(package.read("dane.json").decode("utf-8"))


# --- dostęp ---------------------------------------------------------------------------------


def test_an_anonymous_visitor_is_sent_to_the_login_page(web_client):
    response = web_client.get(EXPORT_URL)

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_any_logged_in_role_can_export(web_client, reviewer):
    """Prawo z art. 20 nie zależy od tego, kim ktoś jest w zawodach – także recenzent je ma."""
    web_client.force_login(reviewer.user)

    response = web_client.get(EXPORT_URL)

    assert response.status_code == 200
    assert payload(response)["profil_komitetu"]["status"] == reviewer.status


# --- zawartość paczki -----------------------------------------------------------------------


def test_the_archive_holds_the_data_file(web_client, participant):
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL)

    with archive(response) as package:
        assert "dane.json" in package.namelist()


def test_the_data_file_has_the_account_and_the_participant_profile(web_client, participant):
    web_client.force_login(participant.user)

    data = payload(web_client.get(EXPORT_URL))

    assert data["konto"]["email"] == participant.user.email
    assert data["profil_uczestnika"]["kod_publiczny"] == participant.public_code
    assert data["profil_uczestnika"]["wojewodztwo"] == participant.district


def test_the_data_file_never_carries_the_password_hash(web_client, participant):
    """Kopia skrótu hasła nie jest daną do przeniesienia, tylko materiałem do łamania offline."""
    web_client.force_login(participant.user)

    body = json.dumps(payload(web_client.get(EXPORT_URL)))

    assert "pbkdf2" not in body
    assert participant.user.password not in body


def test_the_data_file_holds_the_own_forum_posts(web_client, participant, competition):
    """Wypowiedzi na forum są danymi tej osoby, więc wchodzą do paczki razem ze stanem moderacji.

    Uzasadnienie odrzucenia **też**: decyzja o wypowiedzi dotyczy jej autora, a ekran „Twoje wpisy”
    jest jedynym miejscem, w którym autor się o niej dowiaduje – paczka nie może być uboższa.
    """
    from apps.forum.models import ModerationStatus
    from apps.forum.tests.factories import ForumPostFactory

    ForumPostFactory(
        competition=competition,
        author=participant.user,
        body="TRESC-MOJEGO-WPISU",
        status=ModerationStatus.REJECTED,
        moderation_note="UZASADNIENIE-ORGANIZATORA",
    )
    web_client.force_login(participant.user)

    section = payload(web_client.get(EXPORT_URL))["wpisy_na_forum"]

    assert len(section) == 1
    assert section[0]["tresc"] == "TRESC-MOJEGO-WPISU"
    assert section[0]["uzasadnienie_moderatora"] == "UZASADNIENIE-ORGANIZATORA"


def test_the_forum_section_never_carries_somebody_elses_post(web_client, participant, competition):
    """Art. 15 pyta o dane **tej** osoby, a nie o rozmowę, w której brała udział.

    Paczka z odpowiedziami innych uczestników byłaby wydaniem ich danych komuś, kto o nie nie pytał
    i nie ma do nich prawa – a na forum piszą osoby niepełnoletnie.
    """
    from apps.forum.tests.factories import ForumPostFactory, ForumThreadFactory

    thread = ForumThreadFactory(competition=competition)
    ForumPostFactory(competition=competition, thread=thread, author=participant.user, body="MOJE")
    ForumPostFactory(competition=competition, thread=thread, body="CUDZE-ZDANIE")
    web_client.force_login(participant.user)

    body = json.dumps(payload(web_client.get(EXPORT_URL)))

    assert "MOJE" in body
    assert "CUDZE-ZDANIE" not in body


def test_the_forum_section_is_an_empty_list_for_an_account_that_never_wrote(web_client, participant):
    """Kształt pliku ma być ten sam dla każdego konta – brak wpisów to pusta lista, a nie brak klucza."""
    web_client.force_login(participant.user)

    assert payload(web_client.get(EXPORT_URL))["wpisy_na_forum"] == []


def test_the_forum_notification_state_is_in_the_package(web_client, participant, competition):
    """Ustawienia powiadomień i obserwowane wątki – kategorie danych z rejestru czynności 1.9.

    Temat wątku stoi przy obserwacji wyłącznie wtedy, gdy ta osoba może go przeczytać: obserwacja
    cudzego wątku, który moderator potem odrzucił, nie może wynieść w paczce jego tematu.
    """
    from apps.forum.models import (
        DecisionKind,
        ForumDecisionNotice,
        ForumSubscription,
        ModerationStatus,
        NotificationFrequency,
    )
    from apps.forum.notifications import save_preferences
    from apps.forum.tests.factories import ForumThreadFactory

    user = participant.user
    save_preferences(user, frequency=NotificationFrequency.DAILY, moderation_digest=False)
    visible = ForumThreadFactory(competition=competition, title="WATEK-OPUBLIKOWANY")
    rejected = ForumThreadFactory(
        competition=competition, title="CUDZY-ODRZUCONY", status=ModerationStatus.REJECTED
    )
    own_pending = ForumThreadFactory(
        competition=competition, author=user, title="MOJ-CZEKAJACY", status=ModerationStatus.PENDING
    )
    for thread in (visible, rejected, own_pending):
        ForumSubscription.objects.create(competition=competition, thread=thread, user=user)
    ForumSubscription.objects.filter(thread=rejected).update(is_active=False)
    ForumDecisionNotice.objects.create(
        competition=competition, user=user, kind=DecisionKind.THREAD_APPROVED, thread=visible
    )
    web_client.force_login(user)

    data = payload(web_client.get(EXPORT_URL))
    section = data["powiadomienia_z_forum"]

    assert section["ustawienia"]["listy_o_obserwowanych_watkach"] == "raz dziennie"
    assert section["ustawienia"]["listy_o_kolejce_moderacji"] is False
    assert section["ustawienia"]["zmienione"] is not None
    titles = [row["watek"] for row in section["obserwowane_watki"]]
    assert titles == ["WATEK-OPUBLIKOWANY", None, "MOJ-CZEKAJACY"]
    assert [row["obserwuje"] for row in section["obserwowane_watki"]] == [True, False, True]
    assert "CUDZY-ODRZUCONY" not in json.dumps(data)
    assert section["decyzje_moderatora_do_powiadomienia"][0]["decyzja"] == "wątek zatwierdzony"
    assert section["decyzje_moderatora_do_powiadomienia"][0]["obsluzona"] is None


def test_the_forum_notification_section_shows_the_defaults_of_an_untouched_account(web_client, participant):
    """Brak wiersza ustawień to „domyślne”, a nie brak danych – kształt pliku ten sam dla każdego konta."""
    web_client.force_login(participant.user)

    section = payload(web_client.get(EXPORT_URL))["powiadomienia_z_forum"]

    assert section == {
        "ustawienia": {
            "listy_o_obserwowanych_watkach": "na bieżąco",
            "listy_o_kolejce_moderacji": True,
            "zmienione": None,
        },
        "obserwowane_watki": [],
        "decyzje_moderatora_do_powiadomienia": [],
    }


def test_anonymisation_erases_the_forum_notification_state(participant, competition):
    """Po anonimizacji nie zostaje, które wątki ta osoba obserwowała – wpisy zostają bez podpisu."""
    from apps.accounts.profile import anonymise_account
    from apps.forum.models import (
        ForumNotificationSettings,
        ForumSubscription,
        NotificationFrequency,
    )
    from apps.forum.notifications import save_preferences
    from apps.forum.tests.factories import ForumThreadFactory

    user = participant.user
    save_preferences(user, frequency=NotificationFrequency.IMMEDIATE, moderation_digest=True)
    ForumSubscription.objects.create(
        competition=competition, thread=ForumThreadFactory(competition=competition), user=user
    )
    other = ForumSubscription.objects.create(
        competition=competition, thread=ForumThreadFactory(competition=competition), user=UserFactory()
    )

    anonymise_account(user)

    assert not ForumSubscription.objects.filter(user=user).exists()
    assert not ForumNotificationSettings.objects.filter(user=user).exists()
    assert ForumSubscription.objects.filter(pk=other.pk).exists()


def test_consents_come_with_the_document_version_and_both_timestamps(web_client, participant):
    ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.TERMS,
        document_version="1.0",
        source="web",
    )
    web_client.force_login(participant.user)

    consents = payload(web_client.get(EXPORT_URL))["zgody"]

    assert consents[0]["rodzaj"] == ConsentKind.TERMS
    assert consents[0]["wersja_dokumentu"] == "1.0"
    assert consents[0]["wyrazona"]
    assert consents[0]["wycofana"] is None


def _supervisor(email: str = "opiekun.eksport@szkola.test"):
    """Konto opiekuna, poza fabryką: ``SchoolSupervisor`` nie ma jeszcze własnej ``Factory``."""
    from django.contrib.auth.models import Group

    from apps.accounts.models import GROUP_SUPERVISOR, SchoolSupervisor
    from apps.tenancy.tests.factories import current_or_default_competition

    user = UserFactory(email=email)
    user.groups.add(Group.objects.get_or_create(name=GROUP_SUPERVISOR)[0])
    return SchoolSupervisor.objects.create(
        user=user, school="XIV LO", competition=current_or_default_competition()
    )


def test_a_pure_supervisor_export_carries_their_own_consents(web_client):
    """B1: konto bez profilu uczestnika nie może dostać paczki bez dowodu **swoich** zgód."""
    supervisor = _supervisor()
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.TERMS, document_version="z 20 września 2026", source="web"
    )
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.PRIVACY, document_version="1.0", source="web"
    )
    web_client.force_login(supervisor.user)

    body = payload(web_client.get(EXPORT_URL))

    assert body["profil_opiekuna_szkolnego"]["szkola"] == "XIV LO"
    consents = {row["rodzaj"]: row for row in body["zgody"]}
    assert consents.keys() == {ConsentKind.TERMS, ConsentKind.PRIVACY}
    assert all(row["wlasciciel"] == "opiekun_szkolny" for row in consents.values())
    assert consents[ConsentKind.TERMS]["wersja_dokumentu"] == "z 20 września 2026"


def test_a_dual_role_account_gets_both_sets_of_consents(web_client, participant):
    """Jedno konto, dwa profile: paczka niesie zgody **obu**, każdą podpisaną właścicielem."""
    from apps.accounts.models import SchoolSupervisor
    from apps.tenancy.tests.factories import current_or_default_competition

    ConsentRecord.objects.create(
        participant=participant, kind=ConsentKind.TERMS, document_version="1.0", source="web"
    )
    supervisor = SchoolSupervisor.objects.create(
        user=participant.user, school="I LO", competition=current_or_default_competition()
    )
    ConsentRecord.objects.create(
        supervisor=supervisor, kind=ConsentKind.TERMS, document_version="1.0", source="web"
    )
    web_client.force_login(participant.user)

    consents = payload(web_client.get(EXPORT_URL))["zgody"]

    owners = {row["wlasciciel"] for row in consents}
    assert owners == {"uczestnik", "opiekun_szkolny"}
    assert len(consents) == 2


def test_stage_entries_and_submission_metadata_are_included(web_client, participant, entry, problems):
    from apps.submissions.models import Submission, SubmissionFile

    submission = Submission.objects.create(entry=entry, problem=problems[0], version=1)
    SubmissionFile.objects.create(
        submission=submission,
        object_key="1/1/kod/uuid/" + "a" * 64 + ".pdf",
        sha256="a" * 64,
        original_name="moje-rozwiazanie.pdf",
        mime="application/pdf",
        size_bytes=1024,
    )
    web_client.force_login(participant.user)

    entries = payload(web_client.get(EXPORT_URL))["zgloszenia_do_etapow"]

    work = entries[0]["prace"][0]
    assert entries[0]["etap"] == entry.stage.display_name
    assert work["zadanie_numer"] == problems[0].number
    # sha256 jest tu jedynym dowodem, że plik w paczce jest tym, który system przyjął.
    assert work["pliki"][0]["sha256"] == "a" * 64


def test_preferences_and_guardian_state_are_included(web_client, participant):
    web_client.force_login(participant.user)

    data = payload(web_client.get(EXPORT_URL))

    assert "jezyk" in data["ustawienia_interfejsu"]
    assert "stan" in data["zgoda_opiekuna"]


def test_the_export_never_carries_internal_reviewer_comments(web_client, participant, entry, problems):
    """Komentarz wewnętrzny jest notatką komitetu o pracy, a nie daną uczestnika."""
    from apps.accounts.tests.factories import ActiveReviewerFactory
    from apps.grading.models import Review, ReviewStatus
    from apps.results.models import Anonymization, ResultsPublication
    from apps.submissions.models import Submission

    submission = Submission.objects.create(entry=entry, problem=problems[0], version=1)
    Review.objects.create(
        submission=submission,
        reviewer=ActiveReviewerFactory(),
        score=5,
        comment_internal="TAJNA-NOTATKA-KOMITETU",
        comment_for_participant="Dobrze uzasadnione przejście.",
        status=ReviewStatus.SUBMITTED,
        submitted_at=timezone.now(),
    )
    stage = entry.stage
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    ResultsPublication.objects.create(stage=stage, anonymization=Anonymization.CODE, snapshot=[])
    web_client.force_login(participant.user)

    body = json.dumps(payload(web_client.get(EXPORT_URL)), ensure_ascii=False)

    assert "TAJNA-NOTATKA-KOMITETU" not in body
    assert "Dobrze uzasadnione przejście." in body


def test_published_results_are_reduced_to_the_own_row(web_client, participant, entry, problems):
    """Cała tabela jest jawna pod własnym adresem – prośba o swoje dane nie wywozi cudzych wierszy."""
    from apps.results.models import Anonymization, ResultsPublication

    stage = entry.stage
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    ResultsPublication.objects.create(
        stage=stage,
        anonymization=Anonymization.CODE,
        snapshot=[
            {"rank": 1, "display": "CUDZY-KOD", "points": {}, "total": 12, "qualified": True},
            {"rank": 2, "display": participant.public_code, "points": {}, "total": 0, "qualified": False},
        ],
        entry_totals={str(entry.pk): 0},
    )
    web_client.force_login(participant.user)

    data = payload(web_client.get(EXPORT_URL))

    assert "CUDZY-KOD" not in json.dumps(data, ensure_ascii=False)
    assert data["wyniki_ogloszone"][0]["moj_wiersz_tabeli"]["suma_punktow"] == 0


# --- audyt i limit --------------------------------------------------------------------------


def test_the_export_leaves_an_audit_entry_without_content(web_client, participant):
    web_client.force_login(participant.user)

    web_client.get(EXPORT_URL)

    entry = AuditLog.objects.filter(action="account.exported").get()
    assert entry.actor_id == participant.user.pk
    assert set(entry.diff) == {"user_id", "files"}


def test_a_second_export_within_ten_minutes_is_refused(web_client, participant):
    """Budowa paczki czyta cały storage uczestnika – bez odstępu jedno konto zajęłoby workera."""
    web_client.force_login(participant.user)
    web_client.get(EXPORT_URL)

    response = web_client.get(EXPORT_URL)

    assert response.status_code == 429
    assert response["Retry-After"]


def test_the_limit_is_per_account_not_per_address(web_client, participant):
    """Cała pracownia szkolna wychodzi spod jednego adresu IP – limit po adresie blokowałby klasę."""
    other = ParticipantFactory(user=UserFactory(email="druga@example.test", groups=["participant"]))
    web_client.force_login(participant.user)
    web_client.get(EXPORT_URL)

    web_client.force_login(other.user)
    assert web_client.get(EXPORT_URL).status_code == 200


# --- druga droga: koordynator ---------------------------------------------------------------


def test_the_coordinator_can_export_another_account(web_client, participant):
    """Wniosek z art. 20 przychodzi też listem – od osoby, która akurat nie może się zalogować."""
    web_client.force_login(CoordinatorFactory())

    response = web_client.post(f"/coordinator/accounts/{participant.user.pk}/export/")

    assert response.status_code == 200
    assert payload(response)["profil_uczestnika"]["kod_publiczny"] == participant.public_code


def test_the_coordinator_export_has_its_own_audit_action(web_client, participant):
    coordinator = CoordinatorFactory()
    web_client.force_login(coordinator)

    web_client.post(f"/coordinator/accounts/{participant.user.pk}/export/")

    entry = AuditLog.objects.filter(action="account.exported_by_coordinator").get()
    assert entry.actor_id == coordinator.pk
    assert entry.target_id == str(participant.user.pk)


def test_a_participant_cannot_export_another_account(web_client, participant):
    other = ParticipantFactory(user=UserFactory(email="cudze@example.test", groups=["participant"]))
    web_client.force_login(participant.user)

    response = web_client.post(f"/coordinator/accounts/{other.user.pk}/export/")

    assert response.status_code == 403


def test_the_profile_page_links_to_the_export(web_client, participant):
    web_client.force_login(participant.user)

    body = web_client.get("/me/profile/").content.decode()

    assert EXPORT_URL in body
