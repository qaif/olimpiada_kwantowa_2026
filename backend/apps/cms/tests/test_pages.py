"""Kryteria 1–4 i 6 z T-09: strona główna, newsroom, zadania, archiwum, wyniki."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.cms.models import ArchiveDocument, ArchiveEditionPage, NewsPage
from apps.competitions.models import Problem, Stage
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

SNAPSHOT = [
    {
        "rank": 1,
        "display": "OLM-AAAAAA",
        "district": "mazowieckie",
        "points": {"1": 6, "2": 5},
        "total": 11,
        "qualified": True,
    },
    {
        "rank": 2,
        "display": "OLM-BBBBBB",
        "district": "małopolskie",
        "points": {"1": 2, "2": 0},
        "total": 2,
        "qualified": False,
    },
]


def publish(stage, snapshot=None) -> ResultsPublication:
    stage.results_published_at = timezone.now()
    stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(
        stage=stage,
        anonymization=Anonymization.CODE,
        snapshot=SNAPSHOT if snapshot is None else snapshot,
    )


# --- kryterium 1: strona główna ---------------------------------------------------------------


def test_home_page_serves_root_with_links_to_all_sections(web_client, open_stage):
    response = web_client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    for url in ("/aktualnosci/", "/zadania/", "/archiwum/", "/wyniki/"):
        assert f'href="{url}"' in content
    # Oś czasu nadal pochodzi z apps.competitions, nie z treści redakcyjnej.
    assert open_stage.edition.year_label in content


def test_home_page_lists_latest_news(web_client, home_page, news_index):
    news_index.add_child(
        instance=NewsPage(title="Ruszyła edycja", slug="ruszyla-edycja", date=timezone.localdate())
    )

    content = web_client.get("/").content.decode()

    assert "Ruszyła edycja" in content
    assert 'href="/aktualnosci/ruszyla-edycja/"' in content


# --- kryterium 2: newsroom --------------------------------------------------------------------


def test_news_page_is_listed_and_has_own_url(web_client, news_index):
    news_index.add_child(
        instance=NewsPage(
            title="Terminy eliminacji",
            slug="terminy-eliminacji",
            date=timezone.localdate(),
            lead="Podajemy terminy pierwszego etapu.",
        )
    )

    index = web_client.get("/aktualnosci/")
    detail = web_client.get("/aktualnosci/terminy-eliminacji/")

    assert index.status_code == 200
    assert "Terminy eliminacji" in index.content.decode()
    assert "Podajemy terminy pierwszego etapu." in index.content.decode()
    assert detail.status_code == 200
    assert "Terminy eliminacji" in detail.content.decode()


def test_draft_news_page_is_not_public(web_client, news_index):
    news_index.add_child(
        instance=NewsPage(title="Szkic", slug="szkic", date=timezone.localdate(), live=False)
    )

    assert web_client.get("/aktualnosci/szkic/").status_code == 404
    assert "Szkic" not in web_client.get("/aktualnosci/").content.decode()


# --- kryterium 3: zadania ---------------------------------------------------------------------


def test_problems_page_shows_problems_of_open_stage(web_client, open_stage):
    Problem.objects.create(stage=open_stage, number=1, title="Nierówność ze średnimi")

    content = web_client.get("/zadania/").content.decode()

    assert "Nierówność ze średnimi" in content
    assert open_stage.get_kind_display() in content


def test_problems_page_hides_statements_before_opens_at(web_client, open_stage):
    problem = Problem.objects.create(
        stage=open_stage,
        number=1,
        title="Tajne zadanie",
        statement_pdf=SimpleUploadedFile("z1.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )
    now = timezone.now()
    # Etap jeszcze nieotwarty – ``update()``, bo interesuje nas stan, a nie droga do niego.
    Stage.objects.filter(pk=open_stage.pk).update(
        opens_at=now + timedelta(days=3),
        deadline_at=now + timedelta(days=10),
        review_deadline_at=now + timedelta(days=20),
        appeal_window_opens_at=now + timedelta(days=22),
        appeal_window_closes_at=now + timedelta(days=29),
    )

    content = web_client.get("/zadania/").content.decode()

    assert "Tajne zadanie" not in content
    assert f"/api/competitions/problems/{problem.pk}/statement/" not in content
    assert "zostaną opublikowane" in content


# --- kryterium 4: archiwum --------------------------------------------------------------------


def test_archive_edition_page_renders_documents_and_results_link(
    web_client, archive_index, edition, open_stage
):
    from wagtail.documents.models import Document

    page = ArchiveEditionPage(title="Edycja archiwalna", slug="edycja-archiwalna", edition=edition)
    archive_index.add_child(instance=page)
    document = Document.objects.create(
        title="Zadania eliminacji",
        file=SimpleUploadedFile("zadania.pdf", b"%PDF-1.4 zadania", content_type="application/pdf"),
    )
    ArchiveDocument.objects.create(
        page=page, kind=ArchiveDocument.Kind.PROBLEMS, title="Zadania eliminacji", document=document
    )
    publish(open_stage)

    content = web_client.get("/archiwum/edycja-archiwalna/").content.decode()

    assert "Edycja archiwalna" in content
    assert "Zadania eliminacji" in content
    assert f'href="/results/{open_stage.pk}/"' in content
    assert "Archiwum" in web_client.get("/archiwum/").content.decode()


def test_archive_edition_without_publication_has_no_results_link(web_client, archive_index, edition):
    page = ArchiveEditionPage(title="Edycja bez wyników", slug="bez-wynikow", edition=edition)
    archive_index.add_child(instance=page)

    content = web_client.get("/archiwum/bez-wynikow/").content.decode()

    assert "/results/" not in content
    assert "nie ogłoszono tabel wyników" in content


# --- kryterium 6: wyniki ----------------------------------------------------------------------


def test_results_page_renders_snapshot_only(web_client, open_stage, participant):
    publish(open_stage)

    response = web_client.get("/wyniki/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "OLM-AAAAAA" in content
    assert ">11<" in content
    # Kryterium 6: strona nie dotyka Participant/User – w HTML-u nie ma ani e-maila, ani nazwiska.
    assert participant.user.email not in content
    assert "@example.test" not in content
    assert participant.user.last_name not in content
    assert participant.public_code not in content


def test_results_page_ignores_stage_without_publication(web_client, open_stage):
    open_stage.results_published_at = timezone.now()
    open_stage.save(update_fields=["results_published_at"])

    content = web_client.get("/wyniki/").content.decode()

    assert "Nie ogłoszono jeszcze żadnych wyników." in content
