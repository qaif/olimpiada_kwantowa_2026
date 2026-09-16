"""Kryterium 8: dwa równoległe uploady dostają wersje 1 i 2, bez IntegrityError.

Test wymaga prawdziwej bazy z commitami (``transaction=True``) – ``select_for_update`` w serwisie
ma sens tylko wtedy, gdy transakcje faktycznie się kończą i widzą nawzajem swoje zapisy.
"""

import threading

import pytest
from django.db import connection

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.services import create_submission, lock_for_review
from apps.submissions.tests.factories import pdf_upload


@pytest.mark.django_db(transaction=True)
def test_two_parallel_uploads_get_versions_one_and_two():
    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"])
    entry = StageEntryFactory(stage=stage)
    user = entry.participant.user

    barrier = threading.Barrier(2, timeout=15)
    results: list = []
    errors: list = []

    def worker(index: int) -> None:
        try:
            barrier.wait()
            submission = create_submission(
                user=user, stage=stage, problem_number=problem.number, upload=pdf_upload(f"v{index}.pdf")
            )
            results.append(submission.version)
        except Exception as exc:  # zbieramy, żeby test pokazał prawdziwą przyczynę, nie tylko timeout
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sorted(results) == [1, 2]
    versions = sorted(
        Submission.objects.filter(entry=entry, problem=problem).values_list("version", flat=True)
    )
    assert versions == [1, 2]
    assert Submission.objects.filter(entry=entry, problem=problem).count() == 2


#: Ile wątek blokady czeka na upload, zanim uzna, że tamten stoi na blokadzie wpisu.
#: Wartość jest płacona wyłącznie wtedy, gdy serwis działa poprawnie (upload czeka i nigdy nie
#: sygnalizuje); przy regresji upload kończy się w kilkadziesiąt milisekund i czekanie znika.
UPLOAD_HANDSHAKE_TIMEOUT = 2.0


@pytest.mark.django_db(transaction=True)
def test_lock_for_review_never_locks_a_superseded_version(monkeypatch):
    """Blokada do oceny i upload szeregują się na wpisie do etapu (``select_for_update``).

    Test wymusza dokładnie ten przeplot, który bez wspólnej blokady psuł dane: wątek blokady czyta
    listę kandydatów, a dopiero **potem** upload dokłada nowszą wersję. Bez blokady wpisu upload
    przechodzi od razu, a zablokowana zostaje wersja przedostatnia – do oceniania wchodzi wtedy nie
    ta praca, którą uczestnik oddał jako ostatnią. Z blokadą upload czeka na wpisie do końca
    transakcji blokady, więc uzgodnienie („poczekaj na upload”) rozpada się po ``timeout`` i całość
    kończy się stanem spójnym.
    """
    from apps.submissions import services as submissions_services

    stage = StageFactory()
    problem = ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"])
    entry = StageEntryFactory(stage=stage)
    create_submission(
        user=entry.participant.user, stage=stage, problem_number=problem.number, upload=pdf_upload()
    )

    uploaded = threading.Event()
    candidates_read = threading.Event()
    errors: list = []
    original = submissions_services.lockable_submission_ids

    def read_then_wait_for_upload(target_stage):
        ids = original(target_stage)
        candidates_read.set()
        uploaded.wait(timeout=UPLOAD_HANDSHAKE_TIMEOUT)
        return ids

    monkeypatch.setattr(submissions_services, "lockable_submission_ids", read_then_wait_for_upload)

    def upload() -> None:
        try:
            candidates_read.wait(timeout=15)
            create_submission(
                user=entry.participant.user,
                stage=stage,
                problem_number=problem.number,
                upload=pdf_upload("v2.pdf"),
            )
            uploaded.set()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    def lock() -> None:
        try:
            lock_for_review(stage)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for worker in (upload, lock)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    rows = dict(Submission.objects.filter(entry=entry, problem=problem).values_list("version", "status"))
    assert rows[1] == SubmissionStatus.SUBMITTED, "starsza wersja nigdy nie może wejść do oceniania"
    assert rows[2] in (SubmissionStatus.SUBMITTED, SubmissionStatus.LOCKED)
