"""Wtyczki redakcyjne (§ 6.2 docs/tasks/DJ-01.md): render zgodny z blokami Wagtaila, walidacja, sanityzacja.

Strony powstają przez ``cms.api`` (``factories``), renderuje je zwykłe żądanie anonima – ten sam
widok, ta sama polityka CSP i ten sam bufor placeholderów, co na produkcji. Markup porównujemy
z szablonami ``backend/templates/cms/blocks/*``: te same znaczniki i klasy CSS = ten sam wygląd.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.core.exceptions import ValidationError

from apps.blocks import models
from apps.blocks.embeds import embed_src
from apps.blocks.tests import factories as f

CONTENT = "dj/pages/content.html"


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def body_page(make_page, superuser, media_root):
    """Strona treści z kompletem wtyczek zestawu DOC w slocie ``body`` – opublikowana."""
    _page, content, ph = f.draft(make_page, "Regulamin testowy", "regulamin-testowy", CONTENT)
    f.plugin(ph, "body", "TextPlugin", body="<p>Akapit <strong>pogrubiony</strong>.</p>")
    f.plugin(ph, "body", "HeadingPlugin", text="Rozdział 1", level="2", anchor="rozdzial-1", in_toc=True)
    f.plugin(ph, "body", "HeadingPlugin", text="§ 1", level="3", anchor="par-1", in_toc=True)
    f.plugin(ph, "body", "NoticePlugin", tone="warning", text="<p>Uwaga <em>ważne</em></p>")
    image = f.filer_image("rys.png", size=(1600, 800), alt="Schemat układu")
    f.plugin(ph, "body", "ImageWithCaptionPlugin", image=image, caption="Rysunek 1")
    f.plugin(
        ph,
        "body",
        "DocumentLinkPlugin",
        url="https://olimpiada.example/documents/5/regulamin.pdf",
        title="Regulamin",
        extension="pdf",
        size_bytes=254113,
    )
    f.plugin(
        ph, "body", "DocumentLinkPlugin", label="Wzór zgody", file=f.filer_file("zgoda.pdf"), extension="pdf"
    )
    f.plugin(
        ph,
        "body",
        "EmbedPlugin",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Film o olimpiadzie",
    )
    definitions = f.plugin(ph, "body", "DefinitionListPlugin", term_label="Cel", description_label="Podstawa")
    f.plugin(
        ph, "body", "DefinitionItemPlugin", target=definitions, term="Rejestracja", description="art. 6 RODO"
    )
    schedule = f.plugin(ph, "body", "SchedulePlugin", caption="Warsztaty", topic_label="Temat")
    f.plugin(
        ph,
        "body",
        "ScheduleRowPlugin",
        target=schedule,
        topic="Kubity",
        date="9 stycznia 2027",
        date_value=datetime.date(2027, 1, 9),
        time="17:00–18:30",
    )
    f.plugin(ph, "body", "ScheduleRowPlugin", target=schedule, topic="Splątanie", date="do potwierdzenia")
    f.publish(content, superuser)
    return content


def _html(client, path="/regulamin-testowy/") -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
def test_every_doc_plugin_renders_wagtail_markup(client, body_page):
    html = _html(client)
    assert "<p>Akapit <strong>pogrubiony</strong>.</p>" in html
    assert '<h2 class="doc-heading doc-heading--chapter" id="rozdzial-1">Rozdział 1</h2>' in html
    assert '<h3 class="doc-heading doc-heading--par" id="par-1">§ 1</h3>' in html
    assert '<aside class="notice notice--warning"><p>Uwaga <em>ważne</em></p></aside>' in html
    # Obraz: miniatura 900 px (bez powiększania), alt z biblioteki, podpis.
    assert '<figure class="cms-figure">' in html
    image = re.search(r'<img alt="Schemat układu" height="(\d+)" src="([^"]+)" width="(\d+)">', html)
    assert image and image.group(3) == "900" and image.group(1) == "450"
    assert image.group(2).startswith("/media/")
    assert "<figcaption>Rysunek 1</figcaption>" in html
    # Dokument: adres na domenie głównej i plik z biblioteki – ten sam przycisk.
    assert '<p class="cms-doc">' in html
    assert 'href="https://olimpiada.example/documents/5/regulamin.pdf"' in html
    assert "Pobierz: Regulamin</a>" in html
    assert '<span class="hint">(PDF, 248,2\xa0KB)</span>' in html
    assert "Pobierz: Wzór zgody</a>" in html
    # Film: ramka z wzorca, nocookie, opakowanie jak przy WAGTAILEMBEDS_RESPONSIVE_HTML.
    assert 'class="responsive-object"' in html
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in html
    assert 'title="Film o olimpiadzie"' in html
    # Tabela dwukolumnowa: nagłówki kolumn powtórzone przy parze.
    assert '<dl class="definitions">' in html
    assert '<div class="definitions__row">' in html
    assert '<span class="definitions__label">Cel</span>' in html
    assert '<span class="definitions__label">Podstawa</span>' in html
    # Harmonogram: kolumna godzin jest (jeden wiersz ją ma), prowadzącego – nie.
    assert '<div class="table-scroll">' in html and '<table class="table schedule">' in html
    assert "<caption>Warsztaty</caption>" in html
    assert '<th scope="col">Godziny</th>' in html
    assert "Prowadzący" not in html
    assert '<th scope="row" class="schedule__topic">Kubity</th>' in html
    assert '<td class="schedule__time">17:00–18:30</td>' in html
    assert html.count('<td class="schedule__time">') == 2  # pusta komórka w drugim wierszu zostaje
    assert "schedule__lecturer" not in html


@pytest.mark.django_db
def test_plugins_render_again_from_placeholder_cache(client, body_page):
    first = _html(client)
    second = _html(client)
    assert first.count('id="rozdzial-1"') == second.count('id="rozdzial-1"') == 1
    assert first.count("<iframe") == second.count("<iframe") == 1


# --- osadzenia ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        (
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        ),
        ("https://vimeo.com/76979871", "https://player.vimeo.com/video/76979871"),
        ("https://player.vimeo.com/video/76979871", "https://player.vimeo.com/video/76979871"),
        ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
        ("javascript:alert(1)//youtube.com/watch?v=dQw4w9WgXcQ", None),
        ("https://youtube.com@evil.example/watch?v=dQw4w9WgXcQ", None),
        ("https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ", None),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ%22onload%3D", None),
        ("https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ", None),
        ("https://vimeo.com/76979871/../../evil", None),
        ("ftp://vimeo.com/76979871", None),
        ("", None),
    ],
)
def test_embed_src_only_from_pattern(url, expected):
    assert embed_src(url) == expected


def test_embed_clean_rejects_other_hosts():
    embed = models.Embed(url="https://evil.example/film", title="Film")
    with pytest.raises(ValidationError) as error:
        embed.clean()
    assert "url" in error.value.message_dict


@pytest.mark.django_db
def test_embed_with_bad_url_saved_past_the_form_renders_nothing(client, make_page, superuser):
    """Wiersz zapisany z pominięciem formularza (import, powłoka) – render sprawdza adres jeszcze raz."""
    _page, content, ph = f.draft(make_page, "Film", "film", CONTENT)
    f.plugin(ph, "body", "EmbedPlugin", url="https://evil.example/x", title="Zły film")
    f.publish(content, superuser)
    html = _html(client, "/film/")
    assert "<iframe" not in html
    assert "evil.example" not in html


# --- pliki: z biblioteki albo adres ---------------------------------------------------------------


@pytest.mark.django_db
def test_linked_file_clean_requires_exactly_one_source(media_root):
    with pytest.raises(ValidationError):
        models.DocumentLink(label="x").clean()
    both = models.DocumentLink(file=f.filer_file(), url="https://olimpiada.example/a.pdf")
    with pytest.raises(ValidationError):
        both.clean()


@pytest.mark.django_db
def test_linked_file_takes_extension_and_size_from_library_file(media_root):
    item = models.Attachment(file=f.filer_file("zgoda.pdf", b"x" * 2048))
    item.clean()
    assert item.extension == "pdf"
    assert item.size_bytes == 2048
    assert item.filename == "zgoda.pdf"
    assert item.is_pdf


def test_linked_file_extension_from_url():
    item = models.Attachment(url="https://olimpiada.example/documents/7/Regulamin%20v2.DOCX")
    item.clean()
    assert item.extension == "docx"
    assert item.filename == "Regulamin v2.DOCX"
    assert not item.is_pdf


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://olimpiada.example/a.pdf", "data:text/html,x"])
def test_linked_file_url_must_be_http(url):
    item = models.DocumentLink(url=url, title="x")
    with pytest.raises(ValidationError) as error:
        item.full_clean(exclude=["placeholder", "language", "position", "plugin_type", "parent"])
    assert "url" in error.value.message_dict
    # Wiersz zapisany z pominięciem walidacji i tak nie trafi do ``href``.
    assert item.href == ""


# --- partner, harmonogram, FAQ --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "initials"),
    [
        ("Uniwersytet im. Adama Mickiewicza", "UA"),
        ("Instytut Fizyki PAN", "IF"),
        ("i w na", "IW"),
        ("Poznańskie Centrum Superkomputerowo-Sieciowe", "PC"),
    ],
)
def test_partner_initials(name, initials):
    assert models.Partner(name=name).initials == initials


@pytest.mark.django_db
def test_partner_is_wide_from_logo_proportions(media_root):
    assert models.Partner(name="Bez znaku").is_wide is False
    assert models.Partner(name="Pas", logo=f.filer_image("pas.png", size=(900, 100))).is_wide is True
    assert models.Partner(name="Kwadrat", logo=f.filer_image("kw.png", size=(584, 528))).is_wide is False


@pytest.mark.django_db
def test_schedule_columns_follow_rows(make_page):
    _page, _content, ph = f.draft(make_page, "Harmonogram", "harmonogram", CONTENT)
    schedule = f.plugin(ph, "body", "SchedulePlugin")
    f.plugin(ph, "body", "ScheduleRowPlugin", target=schedule, topic="A", date="1 marca", time="  ")
    assert schedule.has_time is False and schedule.has_lecturer is False
    f.plugin(ph, "body", "ScheduleRowPlugin", target=schedule, topic="B", date="2 marca", lecturer="dr X")
    schedule = models.Schedule.objects.get(pk=schedule.pk)
    assert schedule.has_time is False and schedule.has_lecturer is True
    assert [row.topic for row in schedule.rows()] == ["A", "B"]


@pytest.mark.django_db
def test_faq_anchor_is_assigned_once_and_kept(make_page):
    _page, _content, ph = f.draft(make_page, "FAQ", "faq", "dj/pages/faq.html")
    entry = f.plugin(ph, "faq", "FAQEntryPlugin", question="Jak?", answer="<p>Tak.</p>")
    entry.refresh_from_db()
    assert entry.anchor == f"pytanie-dj-{entry.pk}"
    imported = f.plugin(
        ph, "faq", "FAQEntryPlugin", question="Kiedy?", answer="<p>Wtedy.</p>", anchor="pytanie-41"
    )
    imported.refresh_from_db()
    assert imported.anchor == "pytanie-41"


# --- sanityzacja ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_html_fields_are_sanitized_on_save(client, make_page, superuser):
    _page, content, ph = f.draft(make_page, "Zły HTML", "zly-html", CONTENT)
    f.plugin(
        ph,
        "body",
        "NoticePlugin",
        text='<p onclick="x()">Ramka<script>alert(1)</script><img src=x onerror="alert(2)"></p>',
    )
    f.plugin(
        ph,
        "body",
        "TextPlugin",
        body='<p>Tekst<script>alert(3)</script><a href="javascript:alert(4)">l</a></p>',
    )
    f.publish(content, superuser)
    notice = models.Notice.objects.get()
    assert "<script" not in notice.text and "onerror" not in notice.text and "onclick" not in notice.text
    html = _html(client, "/zly-html/")
    for forbidden in ("alert(1)", "alert(2)", "alert(3)", "javascript:alert(4)", "onerror", "onclick"):
        assert forbidden not in html, forbidden
    assert "Ramka" in html and "Tekst" in html


@pytest.mark.django_db
def test_text_is_not_hyphenated(client, make_page, superuser):
    """Bez ``&shy;`` – tekst ma łamać się tak samo, jak na stronie Wagtaila (``TEXT_AUTO_HYPHENATE``)."""
    _page, content, ph = f.draft(make_page, "Długie słowa", "dlugie-slowa", CONTENT)
    f.plugin(ph, "body", "TextPlugin", body="<p>Konstantynopolitańczykowianeczka niedowiarkowie</p>")
    f.publish(content, superuser)
    html = _html(client, "/dlugie-slowa/")
    assert "Konstantynopolitańczykowianeczka niedowiarkowie" in html
    assert "­" not in html and "&shy;" not in html


# --- rodzice i dzieci, brak pól zawodów -----------------------------------------------------------


def test_child_plugins_require_their_parent():
    from cms.plugin_pool import plugin_pool

    for child, parent in [
        ("DefinitionItemPlugin", "DefinitionListPlugin"),
        ("ScheduleRowPlugin", "SchedulePlugin"),
        ("StepPlugin", "StepsSectionPlugin"),
    ]:
        plugin = plugin_pool.get_plugin(child)
        assert plugin.require_parent and plugin.parent_classes == [parent]
        assert child in plugin_pool.get_plugin(parent).child_classes


def test_editorial_plugins_have_no_competition_dates():
    """Reguła 1: jedyna data w wtyczkach redakcyjnych to data warsztatu z harmonogramu redakcyjnego."""
    from django.apps import apps
    from django.db.models import DateField, DateTimeField

    dated = sorted(
        f"{model.__name__}.{field.name}"
        for model in apps.get_app_config("dj_blocks").get_models()
        for field in model._meta.local_fields  # bez pól ``CMSPlugin`` (daty utworzenia/zmiany wiersza)
        if isinstance(field, DateField | DateTimeField)
    )
    assert dated == ["ScheduleRow.date_value"]
