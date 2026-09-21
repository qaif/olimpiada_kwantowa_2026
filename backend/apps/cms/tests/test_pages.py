"""Kryteria 1–4 i 6 z T-09: strona główna, newsroom, zadania, archiwum, wyniki."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cms.models import ArchiveDocument, ArchiveEditionPage, NewsPage
from apps.competitions.models import Problem, Stage, StageKind
from apps.competitions.tests.factories import EditionFactory, StageFactory
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
        "district": "malopolskie",
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
    for url in ("/aktualnosci/", "/zadania/", "/wyniki/"):
        assert f'href="{url}"' in content
    # „Archiwum” zniknęło z nawigacji 21.09.2026 (``HIDDEN_MENU_SLUGS``) – adres nadal odpowiada,
    # tylko strona główna już do niego nie prowadzi (i żadna inna sekcja chrome też nie).
    assert 'href="/archiwum/"' not in content
    assert web_client.get("/archiwum/").status_code == 200
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

    assert "Wyników jeszcze nie ogłoszono" in content


def test_results_page_query_count_does_not_grow_with_publications(
    web_client, edition, open_stage, django_assert_num_queries
):
    """Przegląd Critica T-09, finding 5: jedno zapytanie na całość, nie jedno na etap.

    Rozgrzewka przed pomiarem jest konieczna: pierwsze żądanie w teście dociąga m.in. cache
    ``ContentType`` i wpis witryny, więc bez niej porównywalibyśmy start procesu z jego pracą.
    """
    web_client.get("/wyniki/")
    publish(open_stage)

    with CaptureQueriesContext(connection) as one_publication:
        web_client.get("/wyniki/")
    baseline = len(one_publication.captured_queries)

    for kind in (StageKind.DISTRICT, StageKind.FINAL):
        publish(StageFactory(edition=edition, kind=kind))

    with django_assert_num_queries(baseline):
        response = web_client.get("/wyniki/")

    # I nadal jest to strona z trzema tabelami, a nie z pustką, na której łatwo o stałą liczbę.
    assert response.content.decode().count("OLM-AAAAAA") == 3


def test_results_page_shows_older_editions_as_links_not_as_tables(web_client, edition, open_stage):
    """Snapshot finału to tysiące wierszy – archiwalne roczniki zostają odnośnikiem."""
    old_edition = EditionFactory(year_label="2019/2020", is_current=False)
    old_stage = StageFactory(edition=old_edition, kind=StageKind.FINAL)
    publish(old_stage, snapshot=[{"rank": 1, "display": "OLM-ZZZZZZ", "total": 30, "points": {}}])
    publish(open_stage)

    content = web_client.get("/wyniki/").content.decode()

    assert "OLM-AAAAAA" in content  # bieżąca edycja: pełna tabela
    assert "OLM-ZZZZZZ" not in content  # archiwalna: bez tabeli…
    assert f'href="/results/{old_stage.pk}/"' in content  # …ale z linkiem
    assert old_edition.year_label in content


def test_archive_edition_documents_are_fetched_in_one_query(
    web_client, archive_index, edition, django_assert_num_queries
):
    """Przegląd Critica T-09, finding 6: ``select_related`` na dokumentach archiwum."""
    from wagtail.documents.models import Document

    page = ArchiveEditionPage(title="Edycja z materiałami", slug="z-materialami", edition=edition)
    archive_index.add_child(instance=page)
    for number in range(1, 4):
        ArchiveDocument.objects.create(
            page=page,
            kind=ArchiveDocument.Kind.PROBLEMS,
            title=f"Materiał {number}",
            document=Document.objects.create(
                title=f"Materiał {number}",
                file=SimpleUploadedFile(f"m{number}.pdf", b"%PDF-1.4", content_type="application/pdf"),
            ),
        )
    web_client.get(page.url)  # rozgrzewka – patrz test wyżej

    with CaptureQueriesContext(connection) as three_documents:
        web_client.get(page.url)
    baseline = len(three_documents.captured_queries)

    ArchiveDocument.objects.create(
        page=page,
        kind=ArchiveDocument.Kind.SOLUTIONS,
        title="Materiał 4",
        document=Document.objects.create(
            title="Materiał 4",
            file=SimpleUploadedFile("m4.pdf", b"%PDF-1.4", content_type="application/pdf"),
        ),
    )

    with django_assert_num_queries(baseline):
        response = web_client.get(page.url)

    assert "Materiał 4" in response.content.decode()
