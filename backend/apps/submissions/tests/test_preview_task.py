"""Liczenie stron PDF-a osobnym zadaniem Celery z limitem 30 s (audyt 10.10.2026, niskie).

pypdf liczył strony w środku ``scan_submission_file`` – w workerze współdzielonym z kolejkami
``scan`` i ``mail`` i pod ogólnym limitem 30 minut. Spreparowany PDF zatrzymywał wtedy skany
i pocztę. Te testy pilnują, że praca idzie osobnym zadaniem z krótkim limitem, a przekroczenie
limitu (albo awaria kolejki) nie wywraca skanu.
"""

from __future__ import annotations

import io

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from apps.submissions import preview, tasks
from apps.submissions.models import AvStatus
from apps.submissions.services import apply_scan_verdict
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFileFactory
from apps.web.tests.test_upload_preview import multipage_pdf

pytestmark = pytest.mark.django_db


def _pdf_file(pages: int = 3):
    item = SubmissionFileFactory(av_status=AvStatus.PENDING, original_name="praca.pdf")
    get_submission_storage().put(item.object_key, io.BytesIO(multipage_pdf(pages)), "application/pdf")
    return item


def test_the_task_has_a_short_time_limit():
    assert tasks.store_preview_metrics.soft_time_limit == 30
    assert tasks.store_preview_metrics.time_limit > tasks.store_preview_metrics.soft_time_limit


def test_a_clean_scan_stores_the_page_count_through_the_task(monkeypatch):
    item = _pdf_file(pages=4)
    queued = []
    real_delay = tasks.store_preview_metrics.delay
    monkeypatch.setattr(
        tasks.store_preview_metrics, "delay", lambda file_id: queued.append(file_id) or real_delay(file_id)
    )

    apply_scan_verdict(item, AvStatus.CLEAN)

    item.refresh_from_db()
    assert queued == [item.pk]
    assert item.page_count == 4


def test_an_infected_file_is_never_parsed(monkeypatch):
    item = _pdf_file()
    queued = []
    monkeypatch.setattr(tasks.store_preview_metrics, "delay", queued.append)

    apply_scan_verdict(item, AvStatus.INFECTED, "Eicar")

    assert queued == []


def test_a_time_limit_ends_the_task_quietly(monkeypatch):
    item = _pdf_file()
    item.av_status = AvStatus.CLEAN
    item.save(update_fields=["av_status"])

    def slow(_submission_file):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(preview, "store_page_count", slow)

    tasks.store_preview_metrics(item.pk)

    item.refresh_from_db()
    assert item.page_count is None


def test_the_task_skips_a_file_that_is_no_longer_clean(monkeypatch):
    item = _pdf_file()
    called = []
    monkeypatch.setattr(preview, "store_page_count", called.append)

    tasks.store_preview_metrics(item.pk)

    assert called == []


def test_a_queue_failure_does_not_break_the_scan(monkeypatch):
    item = _pdf_file()

    def broken(_file_id):
        raise ConnectionError("broker niedostępny")

    monkeypatch.setattr(tasks.store_preview_metrics, "delay", broken)

    result = apply_scan_verdict(item, AvStatus.CLEAN)

    assert result.av_status == AvStatus.CLEAN
