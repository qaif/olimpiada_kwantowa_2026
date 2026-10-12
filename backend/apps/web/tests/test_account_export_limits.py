"""Eksport konta a pełny dysk tymczasowy (audyt 10.10.2026, S15).

Uczestnik wgrywał kilkanaście wersji po 20 MB, a ``/account/export/`` budował ZIP ze **wszystkich**
w tym samym ``/tmp``, do którego Django odkłada uploady. Pełny tmpfs wywracał w tym czasie cudze
prace – tuż przed terminem. Te testy pilnują trzech napraw:

- w paczce jest **najnowsza** wersja pliku każdego zadania (metryka wszystkich zostaje w ``dane.json``),
- paczka ponad ``PACKAGE_MAX_BYTES`` jest odrzucana **przed** budową, z komunikatem,
- dwa równoległe eksporty jednego konta nie budują dwóch paczek (blokada ``cache.add``).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.core.cache import cache

from apps.submissions.models import AvStatus, Submission
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFileFactory
from apps.web.views.account import EXPORT_LOCK_PREFIX

pytestmark = pytest.mark.django_db

EXPORT_URL = "/account/export/"


def _version(entry, problem, version: int, body: bytes):
    submission = Submission.objects.create(entry=entry, problem=problem, version=version)
    item = SubmissionFileFactory(
        submission=submission,
        av_status=AvStatus.CLEAN,
        original_name=f"wersja{version}.pdf",
        size_bytes=len(body),
    )
    get_submission_storage().put(item.object_key, io.BytesIO(body), "application/pdf")
    return item


def test_the_package_holds_only_the_latest_version_of_each_problem(web_client, participant, entry, problems):
    _version(entry, problems[0], 1, b"%PDF stara")
    _version(entry, problems[0], 2, b"%PDF nowa")
    _version(entry, problems[1], 1, b"%PDF druga")
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL)

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as package:
        names = [name for name in package.namelist() if name.startswith("pliki/")]
        data = json.loads(package.read("dane.json"))
        assert sorted(names) == sorted(
            [
                f"pliki/zad{problems[0].number}-v2-wersja2.pdf",
                f"pliki/zad{problems[1].number}-v1-wersja1.pdf",
            ]
        )
        assert package.read(f"pliki/zad{problems[0].number}-v2-wersja2.pdf") == b"%PDF nowa"
    # Metryka starszej wersji (z sha256) nadal jest w paczce – dowód, co system przyjął.
    works = data["zgloszenia_do_etapow"][0]["prace"]
    assert {work["wersja"] for work in works if work["zadanie_numer"] == problems[0].number} >= {1, 2}


def test_bundle_name_is_given_only_for_files_that_are_in_the_package(
    web_client, participant, entry, problems
):
    """``nazwa_w_paczce`` starszej wersji to ``None`` – jej pliku w archiwum nie ma (S15).

    Każda nazwa, która stoi w ``dane.json``, musi dać się otworzyć w tej samej paczce.
    """
    _version(entry, problems[0], 1, b"%PDF stara")
    _version(entry, problems[0], 2, b"%PDF nowa")
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL)

    with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as package:
        data = json.loads(package.read("dane.json"))
        names = set(package.namelist())
    works = {work["wersja"]: work for work in data["zgloszenia_do_etapow"][0]["prace"]}
    assert works[1]["pliki"][0]["nazwa_w_paczce"] is None
    assert works[2]["pliki"][0]["nazwa_w_paczce"] == f"pliki/zad{problems[0].number}-v2-wersja2.pdf"
    listed = {
        item["nazwa_w_paczce"]
        for entry_row in data["zgloszenia_do_etapow"]
        for work in entry_row["prace"]
        for item in work["pliki"]
        if item["nazwa_w_paczce"] is not None
    }
    assert listed <= names


def test_a_package_above_the_limit_is_refused_before_it_is_built(
    web_client, participant, entry, problems, settings
):
    settings.PACKAGE_MAX_BYTES = 10
    _version(entry, problems[0], 1, b"%PDF wieksza niz limit")
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL, follow=True)

    assert response.redirect_chain, "odmowa ma wrócić na stronę z komunikatem, nie oddać pliku"
    body = response.content.decode()
    assert "Paczka miałaby" in body
    # Odmowa nie zapala licznika – po zawężeniu (albo podniesieniu limitu) można od razu spróbować.
    settings.PACKAGE_MAX_BYTES = 10 * 1024 * 1024
    assert web_client.get(EXPORT_URL).status_code == 200


def test_a_second_export_while_one_is_being_built_is_refused(web_client, participant):
    cache.add(f"{EXPORT_LOCK_PREFIX}:{participant.user.pk}", 1, 60)
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL)

    assert response.status_code == 429
    assert response["Retry-After"]


def test_the_lock_is_released_after_the_build(web_client, participant):
    web_client.force_login(participant.user)

    assert web_client.get(EXPORT_URL).status_code == 200

    assert cache.get(f"{EXPORT_LOCK_PREFIX}:{participant.user.pk}") is None


def test_the_package_is_built_in_the_package_directory(web_client, participant, settings, tmp_path):
    settings.PACKAGE_TMP_DIR = str(tmp_path / "paczki")
    web_client.force_login(participant.user)

    response = web_client.get(EXPORT_URL)

    assert response.status_code == 200
    # Katalog powstał przy pierwszym użyciu – to w nim, a nie w ``/tmp`` uploadów, leżał plik.
    assert (tmp_path / "paczki").is_dir()
