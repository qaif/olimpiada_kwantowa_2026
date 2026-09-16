"""Ekrany jakości i dokumentów w panelu koordynatora: dostęp, parametry adresu i akcje POST.

Testy warstwy WWW pilnują trzech rzeczy, których nie sprawdzi test serwisu: że każdy z tych
ekranów jest zamknięty dla ról innych niż koordynator, że parametry w adresie (sortowanie, próg)
działają bez ani jednej linijki JavaScriptu, i że akcje zmieniające stan są POST-ami, które
wracają na właściwy ekran z komunikatem.
"""

import zipfile
from io import BytesIO

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.models import ManualQualification
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.certificates import issue_certificate
from apps.results.models import Certificate, CertificateKind
from apps.submissions.models import AvStatus, SubmissionSimilarity, SubmissionStatus
from apps.submissions.similarity import recompute_problem
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory

pytestmark = pytest.mark.django_db

#: Dwa warianty tego samego rozwiązania: nazwy i komentarze inne, kształt identyczny. Kod musi być
#: dłuższy niż próg ``MIN_TOKENS`` – dwulinijkowe „importuj i wypisz” jest identyczne z natury
#: zadania, a nie z przepisania, i moduł świadomie takich par nie liczy.
SOURCE = """
# rozwiazanie zadania
import math


def policz_sume(dane):
    wynik = 0
    for element in dane:
        wynik += math.sqrt(element)
    return wynik


def sprawdz(dane):
    if not dane:
        return False
    return policz_sume(dane) > 0


print(policz_sume([1, 2, 3]), sprawdz([1, 2, 3]))
"""

RENAMED = """
# moje podejscie
import math


def suma(lista):
    acc = 0
    for x in lista:
        acc += math.sqrt(x)
    return acc


def test(lista):
    if not lista:
        return False
    return suma(lista) > 0


print(suma([4, 5, 6]), test([4, 5, 6]))
"""


@pytest.fixture
def logged_coordinator(web_client, coordinator):
    web_client.force_login(coordinator)
    return coordinator


def code_problem(stage):
    return ProblemFactory(stage=stage, number=9, allowed_formats=["py"])


def stored(stage, problem, content: str):
    entry = StageEntryFactory(stage=stage, participant=ParticipantFactory())
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.LOCKED)
    submission_file = SubmissionFileFactory(
        submission=submission,
        av_status=AvStatus.CLEAN,
        object_key=f"1/1/{entry.participant.public_code}/{submission.uuid}/{'b' * 64}.py",
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(content.encode("utf-8")), "text/plain")
    return submission


# --- kalibracja ----------------------------------------------------------------------------------


def test_kalibracja_pokazuje_recenzentow_i_przyjmuje_sortowanie(web_client, logged_coordinator, elim_stage):
    """Sortowanie jedzie parametrem adresu – bez skryptu i bez błędu przy nieznanej nazwie."""
    reviewer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=elim_stage, number=7)
    entry = StageEntryFactory(stage=elim_stage)
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.LOCKED)
    ReviewFactory(submission=submission, reviewer=reviewer, score=2, status=ReviewStatus.SUBMITTED)
    FinalGradeFactory(submission=submission, score=6)
    url = reverse("web:coordinator-stage-calibration", args=[elim_stage.pk])

    response = web_client.get(url, {"sort": "-vs_final"})

    assert response.status_code == 200
    assert reviewer.user.get_full_name() in response.content.decode()
    assert web_client.get(url, {"sort": "cokolwiek"}).status_code == 200


def test_kalibracja_zamknieta_dla_recenzenta(web_client, reviewer, elim_stage):
    """Tożsamość recenzentów w zestawieniu widzi wyłącznie koordynator (PROJEKT.md 2.3)."""
    web_client.force_login(reviewer.user)

    url = reverse("web:coordinator-stage-calibration", args=[elim_stage.pk])

    assert web_client.get(url).status_code == 403


# --- podobieństwa --------------------------------------------------------------------------------


def test_ekran_podobienstw_filtruje_progiem_z_adresu(web_client, logged_coordinator, elim_stage):
    """Próg jest parametrem GET, więc adres z wynikiem da się wkleić w wiadomości do komitetu."""
    problem = code_problem(elim_stage)
    stored(elim_stage, problem, SOURCE)
    stored(elim_stage, problem, RENAMED)
    recompute_problem(problem)
    pair = SubmissionSimilarity.objects.get()
    # Wynik ustawiamy wprost: przedmiotem tego testu jest **filtr ekranu**, a nie miara
    # podobieństwa (tę sprawdza ``apps/submissions/tests/test_similarity.py``). Para dwóch
    # wariantów tej samej pracy wychodzi po normalizacji identyczna, więc żaden próg z zakresu
    # 0–1 by jej nie odciął i test niczego by nie sprawdzał.
    SubmissionSimilarity.objects.filter(pk=pair.pk).update(score=0.7)
    pair.refresh_from_db()
    url = reverse("web:coordinator-stage-similarity", args=[elim_stage.pk])

    shown = web_client.get(url, {"threshold": "0.5"}).content.decode()
    hidden = web_client.get(url, {"threshold": "0.9"}).content.decode()

    assert f"{pair.percent}%" in shown
    assert "Nie ma par powyżej tego progu" in hidden


def test_przeliczenie_zleca_zadanie_i_wraca_z_komunikatem(
    web_client, logged_coordinator, elim_stage, monkeypatch
):
    """Liczenie idzie w tle: widok ma **zlecić** zadanie, a nie policzyć wszystko w żądaniu."""
    code_problem(elim_stage)
    calls = []
    monkeypatch.setattr(
        "apps.submissions.tasks.recompute_similarity.delay",
        lambda *args, **kwargs: calls.append(args),
    )

    response = web_client.post(
        reverse("web:coordinator-similarity-recompute", args=[elim_stage.pk]), follow=True
    )

    assert calls == [(elim_stage.pk, logged_coordinator.pk)]
    assert "Przeliczanie podobieństw zostało zlecone" in response.content.decode()


def test_przeliczenie_odmawia_bez_zadan_kodowych(web_client, logged_coordinator, elim_stage, problems):
    """Etap z samymi zadaniami PDF-owymi nie ma czego porównywać – i mówi to wprost."""
    response = web_client.post(
        reverse("web:coordinator-similarity-recompute", args=[elim_stage.pk]), follow=True
    )

    assert "nie ma zadań oddawanych jako kod" in response.content.decode()


def test_porownanie_pary_pokazuje_oczyszczona_tabele(web_client, logged_coordinator, elim_stage):
    """Treść prac trafia na stronę jako HTML – po escapowaniu i po białej liście znaczników."""
    problem = code_problem(elim_stage)
    stored(elim_stage, problem, SOURCE)
    stored(elim_stage, problem, f"{RENAMED}\n# <script>alert(1)</script>\n")
    recompute_problem(problem)
    pair = SubmissionSimilarity.objects.get()

    body = web_client.get(
        reverse("web:coordinator-similarity-pair", args=[elim_stage.pk, pair.pk])
    ).content.decode()

    assert "diff_header" in body
    assert "<script>alert(1)</script>" not in body


def test_zgloszenie_pary_do_komitetu_przelacza_sie(web_client, logged_coordinator, elim_stage):
    """Jedno kliknięcie oznacza parę do rozpatrzenia, drugie cofa oznaczenie."""
    problem = code_problem(elim_stage)
    stored(elim_stage, problem, SOURCE)
    stored(elim_stage, problem, RENAMED)
    recompute_problem(problem)
    pair = SubmissionSimilarity.objects.get()
    url = reverse("web:coordinator-similarity-report", args=[elim_stage.pk, pair.pk])

    web_client.post(url)
    pair.refresh_from_db()
    assert pair.is_reported

    web_client.post(url)
    pair.refresh_from_db()
    assert not pair.is_reported


# --- kwalifikacja ręczna -------------------------------------------------------------------------


def test_decyzja_komitetu_zapisuje_sie_z_tabeli_symulacji(web_client, logged_coordinator, elim_stage):
    """Formularz w wierszu tabeli wysyła decyzję i uzasadnienie pod adres wpisu."""
    entry = StageEntryFactory(stage=elim_stage)

    response = web_client.post(
        reverse("web:coordinator-manual-qualification", args=[entry.pk]),
        {"decision": ManualQualification.QUALIFIED, "reason": "Decyzja komitetu z posiedzenia 12.05."},
        follow=True,
    )

    entry.refresh_from_db()
    assert entry.manual_qualification == ManualQualification.QUALIFIED
    assert "Zapisano decyzję komitetu" in response.content.decode()


def test_decyzja_bez_uzasadnienia_konczy_sie_komunikatem(web_client, logged_coordinator, elim_stage):
    """Odmowa serwisu wraca jako komunikat na ekranie, a nie jako 500."""
    entry = StageEntryFactory(stage=elim_stage)

    response = web_client.post(
        reverse("web:coordinator-manual-qualification", args=[entry.pk]),
        {"decision": ManualQualification.NOT_QUALIFIED, "reason": "nie"},
        follow=True,
    )

    entry.refresh_from_db()
    assert entry.manual_qualification == ManualQualification.NONE
    assert "co najmniej" in response.content.decode()


def test_kwalifikacja_reczna_zamknieta_dla_uczestnika(web_client, participant, elim_stage):
    """Decyzję komitetu podejmuje koordynator – nikt inny nie ma do tego adresu dostępu."""
    entry = StageEntryFactory(stage=elim_stage, participant=participant)
    web_client.force_login(participant.user)

    response = web_client.post(
        reverse("web:coordinator-manual-qualification", args=[entry.pk]),
        {"decision": ManualQualification.QUALIFIED, "reason": "Chcę być zakwalifikowany."},
    )

    assert response.status_code == 403
    entry.refresh_from_db()
    assert entry.manual_qualification == ManualQualification.NONE


# --- dyplomy -------------------------------------------------------------------------------------


def test_ekran_dyplomow_wystawia_pojedynczy_dokument(web_client, logged_coordinator, elim_stage):
    """„Wystaw” tworzy dokument z numerem i pokazuje go w tabeli przy uczestniku."""
    entry = StageEntryFactory(stage=elim_stage)

    web_client.post(
        reverse("web:coordinator-certificate-issue", args=[elim_stage.pk]),
        {"entry": entry.pk, "kind": CertificateKind.FINALISTA},
    )

    certificate = Certificate.objects.get()
    assert certificate.entry_id == entry.pk
    body = web_client.get(
        reverse("web:coordinator-stage-certificates", args=[elim_stage.pk])
    ).content.decode()
    assert certificate.number in body


def test_wystaw_wszystkim_oddaje_paczke_zip(web_client, logged_coordinator, elim_stage):
    """Komplet dokumentów jednego rodzaju wychodzi jedną paczką, a nie klikaniem po wierszu."""
    StageEntryFactory(stage=elim_stage)
    StageEntryFactory(stage=elim_stage)

    response = web_client.post(
        reverse("web:coordinator-certificates-all", args=[elim_stage.pk]),
        {"kind": CertificateKind.UCZESTNIK},
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        assert len(archive.namelist()) == 2
    assert Certificate.objects.count() == 2


def test_pobranie_dokumentu_daje_pdf(web_client, logged_coordinator, elim_stage):
    """Dokument powstaje w locie – w storage go nie ma i nie musi być."""
    entry = StageEntryFactory(stage=elim_stage)
    certificate, _ = issue_certificate(edition=elim_stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    response = web_client.get(reverse("web:coordinator-certificate-download", args=[certificate.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


def test_uczestnik_widzi_i_pobiera_wlasne_dokumenty(web_client, participant, elim_stage):
    """Panel uczestnika pokazuje jego dokumenty – i wyłącznie jego."""
    entry = StageEntryFactory(stage=elim_stage, participant=participant)
    mine, _ = issue_certificate(edition=elim_stage.edition, kind=CertificateKind.UCZESTNIK, entry=entry)
    other_entry = StageEntryFactory(stage=elim_stage)
    theirs, _ = issue_certificate(
        edition=elim_stage.edition, kind=CertificateKind.UCZESTNIK, entry=other_entry
    )
    web_client.force_login(participant.user)

    body = web_client.get(reverse("web:participant-certificates")).content.decode()

    assert mine.number in body
    assert theirs.number not in body
    assert web_client.get(reverse("web:participant-certificate-download", args=[mine.pk])).status_code == 200
    assert (
        web_client.get(reverse("web:participant-certificate-download", args=[theirs.pk])).status_code == 404
    )


def test_publiczna_weryfikacja_dziala_bez_logowania(web_client, elim_stage):
    """Kod z papieru sprawdza każdy – i nie dostaje przy tym danych osobowych bez zgody."""
    entry = StageEntryFactory(stage=elim_stage)
    certificate, _ = issue_certificate(edition=elim_stage.edition, kind=CertificateKind.LAUREAT, entry=entry)

    body = web_client.get(reverse("web:certificate-verify", args=[certificate.code])).content.decode()

    assert certificate.number in body
    assert entry.participant.user.last_name not in body


def test_nieznany_kod_daje_strone_z_wyjasnieniem(web_client):
    """Strona wygląda tak samo – rozróżnienie kodem HTTP byłoby narzędziem do sprawdzania kodów."""
    response = web_client.get(reverse("web:certificate-verify", args=["NIEISTNIEJACY"]))

    assert response.status_code == 200
    assert "Nie mamy dokumentu o takim kodzie" in response.content.decode()
