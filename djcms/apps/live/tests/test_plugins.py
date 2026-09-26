"""Niezmienniki wtyczek żywych i sekcji strony głównej (DJ-01f)."""

from __future__ import annotations

from datetime import date

import pytest
from cms.plugin_pool import plugin_pool
from django.apps import apps as django_apps
from django.db import models

from apps.live import homepage
from apps.live.models import Problems, StageTimeline

LIVE_PLUGINS = ("StageTimelinePlugin", "ProblemsPlugin", "ResultsPlugin", "ArchiveResultsPlugin")


def _site():
    """Witryna konkursu z fixture'a ``competition_site`` – sekcje strony głównej są per witryna."""
    from django.contrib.sites.models import Site

    return Site.objects.get(pk=1)


@pytest.mark.parametrize("model", [StageTimeline, Problems])
def test_live_plugin_models_have_no_date_or_number_fields(model):
    """Reguła 1 z § 7: redaktor nie może wpisać terminu ani punktów obok danych z systemu."""
    # Pola **tej** wtyczki – bez odziedziczonych z ``CMSPlugin`` (pozycja, daty utworzenia/zmiany).
    own = [field for field in model._meta.local_fields if not field.auto_created]
    forbidden = (
        models.DateField,
        models.DateTimeField,
        models.TimeField,
        models.IntegerField,
        models.DecimalField,
        models.FloatField,
        models.DurationField,
    )
    assert own, "model bez własnych pól – test niczego nie sprawdza"
    assert not [field.name for field in own if isinstance(field, forbidden)]


@pytest.mark.parametrize("name", LIVE_PLUGINS)
def test_live_plugins_are_never_cached(name):
    plugin = plugin_pool.get_plugin(name)
    assert plugin.cache is False
    assert plugin.allow_children is False


@pytest.mark.django_db
def test_home_sections_are_empty_without_pages():
    assert homepage.home_sections(_site()) == {
        "latest_news": [],
        "news_index": None,
        "partners": None,
        "downloads": [],
        "documents_index": None,
    }


@pytest.mark.django_db
def test_latest_news_order_and_limit(make_page, monkeypatch):
    """Data malejąco, potem strona malejąco, najwyżej trzy; brak ``NewsMeta`` = na końcu."""
    monkeypatch.setattr(homepage, "NEWS_TEMPLATE", "dj/pages/content.html")
    pages = {slug: make_page(slug.upper(), slug) for slug in ("a", "b", "c", "d")}
    dates = {"a": date(2026, 9, 1), "b": date(2026, 9, 20), "c": date(2026, 9, 20)}
    leads = {"a": "Lead A"}

    def fake_meta(content):
        return dates.get(content.page.get_slug("pl")), leads.get(content.page.get_slug("pl"), "")

    monkeypatch.setattr(homepage, "_news_meta", fake_meta)
    items = homepage.latest_news(_site())
    assert [item.title for item in items] == ["C", "B", "A"]
    assert items[2].lead == "Lead A"
    assert items[0].url == pages["c"].get_absolute_url("pl")
    assert len(homepage.latest_news(_site(), limit=4)) == 4
    assert homepage.latest_news(_site(), limit=4)[-1].title == "D"


@pytest.mark.django_db
def test_unpublished_pages_are_ignored(make_page, monkeypatch):
    monkeypatch.setattr(homepage, "NEWS_TEMPLATE", "dj/pages/content.html")
    make_page("Szkic", "szkic", publish=False)
    assert homepage.latest_news(_site()) == []


def test_attachment_href_and_pdf_detection():
    class File:
        url = "/media/filer_public/regulamin.pdf"

    class Uploaded:
        file = File()
        url = ""
        extension = "PDF"

    class Linked:
        file = None
        url = "javascript:alert(1)"
        extension = "pdf"

    assert homepage._attachment_href(Uploaded()) == "/media/filer_public/regulamin.pdf"
    assert homepage._is_pdf(Uploaded())
    assert homepage._attachment_href(Linked()) == ""


def test_logo_failure_is_not_fatal():
    class Broken:
        pk = 1
        file = None

    assert homepage._logo(None) is None
    assert homepage._logo(Broken()) is None


requires_blocks = pytest.mark.skipif(
    not django_apps.is_installed("apps.blocks"),
    reason="wtyczki redakcyjne DJ-01e (apps.blocks) nie są jeszcze w INSTALLED_APPS",
)


@requires_blocks
@pytest.mark.django_db
def test_home_sections_with_editorial_plugins(live_page, make_page, monkeypatch):
    """Pełna droga na prawdziwych modelach DJ-01e: ``NewsMeta``, ``PartnerPlugin``, ``AttachmentPlugin``.

    Szablony typów stron podmieniamy na szablony DJ-01f o podobnych slotach – test sprawdza
    zapytania i odczyt pól, a nie istnienie szablonów DJ-01e.
    """
    from cms.models import PageContent

    from apps.pages.models import NewsMeta

    monkeypatch.setattr(homepage, "NEWS_TEMPLATE", "dj/pages/content.html")
    monkeypatch.setattr(homepage, "PARTNERS_TEMPLATE", "dj/pages/results.html")
    monkeypatch.setattr(homepage, "PARTNERS_SLOT", "results")
    monkeypatch.setattr(homepage, "ATTACHMENT_TEMPLATES", ("dj/pages/problems.html",))
    monkeypatch.setattr(homepage, "ATTACHMENTS_SLOT", "intro")

    news = make_page("Nowina", "nowina")
    NewsMeta.objects.create(
        extended_object=PageContent.objects.get(page=news), date=date(2026, 9, 20), lead="Krótko"
    )
    live_page(
        "Partnerzy",
        "partnerzy",
        "dj/pages/results.html",
        results=[("PartnerPlugin", {"name": "Instytut", "url": "https://instytut.example"})],
    )
    live_page(
        "Regulamin",
        "regulamin",
        "dj/pages/problems.html",
        intro=[
            (
                "AttachmentPlugin",
                {"url": "https://olimpiada.example/documents/1/a.docx", "extension": "docx"},
            ),
            (
                "AttachmentPlugin",
                {
                    "url": "https://olimpiada.example/documents/5/regulamin.pdf",
                    "extension": "pdf",
                    "size_bytes": 2048,
                },
            ),
        ],
    )

    sections = homepage.home_sections(_site())
    assert [(item.title, item.date, item.lead) for item in sections["latest_news"]] == [
        ("Nowina", date(2026, 9, 20), "Krótko")
    ]
    assert sections["partners"]["entries"] == [
        {"name": "Instytut", "url": "https://instytut.example", "logo": None, "is_wide": False}
    ]
    assert sections["downloads"] == [
        {
            "title": "Regulamin",
            "url": "/regulamin/",
            "extension": "pdf",
            "size_bytes": 2048,
            "href": "https://olimpiada.example/documents/5/regulamin.pdf",
        }
    ]
