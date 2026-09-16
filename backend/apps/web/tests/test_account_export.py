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
