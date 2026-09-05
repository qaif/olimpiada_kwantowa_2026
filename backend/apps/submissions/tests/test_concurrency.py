"""Kryterium 8: dwa równoległe uploady dostają wersje 1 i 2, bez IntegrityError.

Test wymaga prawdziwej bazy z commitami (``transaction=True``) – ``select_for_update`` w serwisie
ma sens tylko wtedy, gdy transakcje faktycznie się kończą i widzą nawzajem swoje zapisy.
"""

import threading

import pytest
from django.db import connection

from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.submissions.models import Submission
from apps.submissions.services import create_submission
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
