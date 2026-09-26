"""Typy stron redakcyjnych (tabela 6.1 docs/tasks/DJ-01.md, DJ-01e): szablony, spisy, grupy, metryki.

Każdy test buduje strony przez ``cms.api`` (``apps.blocks.tests.factories``) i ogląda je jak anonim.
Oczekiwany markup to markup szablonów ``backend/templates/cms/*_page.html`` – te same klasy CSS.
"""

from __future__ import annotations

import datetime
import re

import pytest
from cms.toolbar.utils import get_object_edit_url, get_object_preview_url

from apps.blocks.tests import factories as f
from apps.pages.models import ArchiveMeta, DocumentMeta, NewsMeta


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _get(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, path
    return response.content.decode()


def _nonce(response) -> str:
    return re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"]).group(1)


def _heading(ph, number: int, level: str = "2", in_toc: bool = True):
    f.plugin(
        ph,
        "body",
        "HeadingPlugin",
        text=f"Rozdział {number}",
        level=level,
        anchor=f"r-{number}",
        in_toc=in_toc,
    )


# --- strona treści --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_content_page_toc_from_three_chapters(client, make_page, superuser):
    _page, content, ph = f.draft(make_page, "Jak zacząć", "jak-zaczac", "dj/pages/content.html")
    f.plugin(ph, "intro", "TextPlugin", body="<p>Wprowadzenie.</p>")
    for number in (1, 2):
        _heading(ph, number)
    _heading(ph, 9, level="3")
    _heading(ph, 10, in_toc=False)
    f.publish(content, superuser)
    html = _get(client, "/jak-zaczac/")
    # Dwa rozdziały w spisie (H3 i „poza spisem” się nie liczą) – za mało na spis.
    assert '<div class="doc-layout doc-layout--plain">' in html
    assert "doc-toc" not in html
    assert '<div class="cms-body prose doc-intro"><p>Wprowadzenie.</p>' in html
    assert "doc-download" not in html  # bez plików nie ma karty „Do pobrania”


@pytest.mark.django_db
def test_content_page_with_toc_and_attachments(client, make_page, superuser):
    _page, content, ph = f.draft(make_page, "Regulamin", "regulamin", "dj/pages/content.html")
    for number in (1, 2, 3):
        _heading(ph, number)
    f.plugin(
        ph,
        "attachments",
        "AttachmentPlugin",
        label="PDF do druku",
        url="https://olimpiada.example/documents/5/regulamin.pdf",
        title="Regulamin",
        extension="pdf",
        size_bytes=254113,
    )
    f.plugin(
        ph,
        "attachments",
        "AttachmentPlugin",
        url="https://olimpiada.example/documents/6/regulamin.docx",
        title="Regulamin (DOCX)",
        extension="docx",
    )
    f.publish(content, superuser)
    html = _get(client, "/regulamin/")
    assert '<div class="doc-layout">' in html
    assert '<nav class="doc-toc" aria-labelledby="spis-sekcji">' in html
    assert '<h2 class="doc-toc__title" id="spis-sekcji">Na tej stronie</h2>' in html
    assert '<li><a href="#r-3">Rozdział 3</a></li>' in html
    assert '<div class="doc-body prose">' in html
    assert "doc-intro" not in html  # pusty slot ``intro`` – bez opakowania
    assert '<div class="card card--accent doc-download">' in html
    assert '<li class="doc-files__item doc-files__item--pdf">' in html
    assert '<span class="doc-files__label">PDF do druku</span>' in html
    assert "regulamin.pdf\n      · PDF\n      · 248,2\xa0KB" in html
    pdf = "https://olimpiada.example/documents/5/regulamin.pdf"
    assert f'class="btn btn--small btn--primary"\n     href="{pdf}" download>Pobierz PDF' in html
    assert '<span class="doc-files__label">Regulamin (DOCX)</span>' in html
    docx = "https://olimpiada.example/documents/6/regulamin.docx"
    assert f'btn--secondary"\n     href="{docx}" download>Pobierz DOCX' in html


@pytest.mark.django_db
def test_contact_page_gets_social_icons_from_chrome(client, make_page, superuser, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    _page, content, _ph = f.draft(make_page, "Kontakt", "kontakt", "dj/pages/content.html")
    f.publish(content, superuser)
    html = _get(client, "/kontakt/")
    body = html.split('<div class="doc-body prose">', 1)[1].split("</main>", 1)[0]
    assert 'href="https://facebook.com/x" aria-label="Facebook"' in body


@pytest.mark.django_db
def test_workshops_page_shows_materials_teaser(client, make_page, superuser, main_api, chrome_payload):
    # Rama też z API – martwy ``chrome`` otworzyłby bezpiecznik i ``workshops`` nie byłby pytany.
    main_api.set("chrome", chrome_payload())
    main_api.set(
        "workshops",
        {
            "page_path": "/warsztaty/",
            "upcoming": [],
            "rows": [],
            "materials": {
                "show": True,
                "count": 4,
                "login_url": "https://olimpiada.example/login/?next=/materialy/",
                "materials_url": "https://olimpiada.example/materialy/",
            },
        },
    )
    _page, content, _ph = f.draft(make_page, "Warsztaty", "warsztaty", "dj/pages/content.html")
    f.publish(content, superuser)
    html = _get(client, "/warsztaty/")
    assert 'id="materialy-z-warsztatow"' in html
    assert "(4)" in html


# --- aktualności ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_news_index_lists_published_news_newest_first(client, make_page, superuser):
    index = make_page("Aktualności", "aktualnosci", template="dj/pages/news_index.html")
    dates = {"stara": datetime.date(2026, 9, 1), "nowa": datetime.date(2026, 9, 20)}
    for slug, day in dates.items():
        _page, content, ph = f.draft(make_page, f"Wiadomość {slug}", slug, "dj/pages/news.html", parent=index)
        NewsMeta.objects.create(extended_object=content, date=day, lead=f"Lead {slug}")
        f.plugin(ph, "body", "TextPlugin", body=f"<p>Treść {slug}</p>")
        f.publish(content, superuser)
    # Wersja robocza (nieopublikowana) nie wycieka na listę – jak ``live()``.
    f.draft(make_page, "Szkic", "szkic", "dj/pages/news.html", parent=index)
    html = _get(client, "/aktualnosci/")
    assert '<ul class="cards cards--wide news-list">' in html
    assert html.index("Wiadomość nowa") < html.index("Wiadomość stara")
    assert '<span class="news-card__date">20 września 2026</span>' in html
    assert '<h2><a href="/aktualnosci/nowa/">Wiadomość nowa</a></h2>' in html
    assert "<p>Lead nowa</p>" in html
    assert '<p class="card__foot"><a href="/aktualnosci/nowa/">Czytaj dalej</a></p>' in html
    assert "Szkic" not in html

    article = _get(client, "/aktualnosci/nowa/")
    assert '<article class="article prose news">' in article
    assert '<p class="article__meta"><time datetime="2026-09-20">20 września 2026</time></p>' in article
    assert '<p class="article__lead">Lead nowa</p>' in article
    assert "<p>Treść nowa</p>" in article
    assert '<a class="backlink" href="/aktualnosci/">← Wszystkie aktualności</a>' in article


@pytest.mark.django_db
def test_empty_news_index(client, make_page):
    make_page("Aktualności", "aktualnosci", template="dj/pages/news_index.html")
    html = _get(client, "/aktualnosci/")
    assert '<span class="empty__title">Cisza w eterze</span>' in html


# --- dokumenty ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_document_index_cards(client, make_page, superuser):
    index = make_page("Dokumenty", "dokumenty", template="dj/pages/document_index.html")
    _page, content, ph = f.draft(make_page, "Regulamin", "regulamin", "dj/pages/document.html", parent=index)
    DocumentMeta.objects.create(
        extended_object=content,
        version_label="1.2",
        document_date=datetime.date(2026, 9, 8),
        status_label="Obowiązuje",
    )
    words = " ".join(f"słowo{n}" for n in range(40))
    f.plugin(ph, "intro", "TextPlugin", body=f"<p>Pierwszy</p><p>{words}</p>")
    f.plugin(
        ph,
        "attachments",
        "AttachmentPlugin",
        url="https://olimpiada.example/d/r.pdf",
        title="R",
        extension="pdf",
    )
    f.publish(content, superuser)
    _page, bare, _ph = f.draft(make_page, "RODO", "rodo", "dj/pages/document.html", parent=index)
    bare.meta_description = "Opis dla wyszukiwarki."
    bare.save()
    f.publish(bare, superuser)

    html = _get(client, "/dokumenty/")
    assert '<ul class="cards cards--wide doc-index">' in html
    assert '<h2 class="doc-card__title"><a href="/dokumenty/regulamin/">Regulamin</a></h2>' in html
    assert '<span class="doc-meta__item">Wersja 1.2</span>' in html
    assert '<time class="doc-meta__item" datetime="2026-09-08">8 września 2026</time>' in html
    assert '<span class="tag">Obowiązuje</span>' in html
    summary = re.search(r'<p class="doc-card__lead">([^<]*)</p>', html).group(1)
    assert summary.startswith("Pierwszy słowo0 ") and summary.endswith("słowo26…")
    assert len(summary.split()) == 28
    assert (
        'href="https://olimpiada.example/d/r.pdf" download\n               title="R">Pobierz PDF</a>' in html
    )
    # Dokument bez wprowadzenia – zajawka z opisu SEO; bez metryki – bez wiersza metryki.
    assert '<p class="doc-card__lead">Opis dla wyszukiwarki.</p>' in html
    assert html.count("doc-card__meta") == 1


@pytest.mark.django_db
def test_document_page(client, make_page, superuser, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    _page, content, ph = f.draft(make_page, "Regulamin", "regulamin", "dj/pages/document.html")
    DocumentMeta.objects.create(extended_object=content, version_label="2.0")
    _heading(ph, 1)
    f.plugin(
        ph,
        "attachments",
        "AttachmentPlugin",
        url="https://olimpiada.example/d/r.docx",
        title="R",
        extension="docx",
    )
    f.plugin(
        ph,
        "attachments",
        "AttachmentPlugin",
        url="https://olimpiada.example/d/r.pdf",
        title="R",
        extension="pdf",
    )
    f.publish(content, superuser)
    response = client.get("/regulamin/")
    html = response.content.decode()
    assert '<span class="eyebrow">Dokument</span>' in html
    assert '<p class="article__meta doc-meta">' in html
    assert '<span class="doc-meta__item">Wersja 2.0</span>' in html
    # „Pobierz PDF” nad treścią – pierwszy PDF, nie pierwszy plik.
    assert '<a class="btn btn--small btn--primary" href="https://olimpiada.example/d/r.pdf" download>' in html
    assert (
        '<button type="button" class="btn btn--small btn--secondary" data-print hidden>Drukuj</button>'
        in html
    )
    # Spis rozdziałów od pierwszego śródtytułu.
    assert '<h2 class="doc-toc__title" id="spis-rozdzialow">Spis rozdziałów</h2>' in html
    assert '<meta name="description" content="Regulamin (wersja 2.0) – dokument Olimpiady' in html
    # ``print.js`` ze wspólnym nonce – reguła 6.
    nonce = _nonce(response)
    assert f'<script defer nonce="{nonce}" src="/djcms/static/js/print.js"></script>' in html
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert f'nonce="{nonce}"' in tag, tag


# --- partnerzy ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_partners_grouped_by_level_order(client, make_page, superuser, media_root):
    _page, content, ph = f.draft(make_page, "Partnerzy", "partnerzy", "dj/pages/partners.html")
    f.plugin(ph, "partners", "PartnerPlugin", name="Sponsor Złoty SA", level="sponsor-zloty")
    f.plugin(
        ph,
        "partners",
        "PartnerPlugin",
        name="Instytut Fizyki PAN",
        level="patron-honorowy",
        url="https://ifpan.example/",
        description="Patronat merytoryczny.",
        logo=f.filer_image("pas.png", size=(1200, 100)),
    )
    f.plugin(ph, "partners", "PartnerPlugin", name="Uniwersytet Testowy", level="patron-honorowy")
    f.plugin(
        ph,
        "become_partner",
        "BecomePartnerPlugin",
        title="Zostań partnerem",
        body="<p>Zapraszamy.</p>",
        contact_email="partnerzy@olimpiada.example",
    )
    f.publish(content, superuser)
    html = _get(client, "/partnerzy/")
    assert html.index('id="poziom-patron-honorowy"') < html.index('id="poziom-sponsor-zloty"')
    assert '<h2 class="mt-0" id="poziom-patron-honorowy">Patron honorowy</h2>' in html
    assert '<section class="section partners__group">' in html
    assert '<ul class="cards partners">' in html
    assert '<li class="partner--wide">' in html
    link = '<a class="partner-card__link" href="https://ifpan.example/" target="_blank"'
    assert f'{link} rel="noopener noreferrer">' in html
    assert '<div class="partner-card__mark partner-card__mark--logo">' in html
    assert re.search(
        r'<img alt="Instytut Fizyki PAN" class="partner-card__logo" height="\d+" loading="lazy" '
        r'src="/djcms/media/',
        html,
    )
    assert '<span class="partner-card__initials" aria-hidden="true">UT</span>' in html
    assert '<h3 class="partner-card__name">Uniwersytet Testowy</h3>' in html
    assert '<p class="partner-card__text">Patronat merytoryczny.</p>' in html
    assert '<div class="card card--accent partners-cta">' in html
    assert '<a class="btn btn--accent" href="mailto:partnerzy@olimpiada.example">Napisz do nas</a>' in html
    assert "Lista partnerów I edycji" not in html


@pytest.mark.django_db
def test_partners_empty_state(client, make_page):
    make_page("Partnerzy", "partnerzy", template="dj/pages/partners.html")
    html = _get(client, "/partnerzy/")
    assert "Lista partnerów I edycji zostanie opublikowana wkrótce." in html


# --- FAQ ------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_faq_sections_in_order_of_first_appearance(client, make_page, superuser, main_api, chrome_payload):
    main_api.set("chrome", chrome_payload())
    _page, content, ph = f.draft(make_page, "FAQ", "faq", "dj/pages/faq.html")
    f.plugin(
        ph,
        "faq",
        "FAQEntryPlugin",
        section="Konto",
        question="Jak założyć konto?",
        answer="<p>Tak.</p>",
        anchor="pytanie-1",
    )
    f.plugin(ph, "faq", "FAQEntryPlugin", section="Zadania", question="Gdzie zadania?", answer="<p>Tu.</p>")
    f.plugin(
        ph, "faq", "FAQEntryPlugin", section="Konto", question="Jak zmienić hasło?", answer="<p>Tak.</p>"
    )
    f.publish(content, superuser)
    html = _get(client, "/faq/")
    assert html.count("<h2>Konto</h2>") == 1
    assert html.index("<h2>Konto</h2>") < html.index("Jak zmienić hasło?") < html.index("<h2>Zadania</h2>")
    assert '<details class="card faq__item" id="pytanie-1">' in html
    assert '<summary class="faq__question">Jak założyć konto?</summary>' in html
    assert '<div class="faq__answer"><p>Tak.</p></div>' in html
    assert '<a href="https://olimpiada.example/support/new/">Zgłoś problem organizatorowi</a>' in html
    assert "<title>FAQ</title>" in html


@pytest.mark.django_db
def test_faq_support_link_without_api(client, make_page):
    make_page("FAQ", "faq", template="dj/pages/faq.html")
    html = _get(client, "/faq/")
    assert '<a href="https://olimpiada.example/support/new/">Zgłoś problem organizatorowi</a>' in html
    assert "Nie ma jeszcze żadnych pytań" in html


# --- archiwum -------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_archive_index_with_and_without_api(client, make_page, superuser, main_api, chrome_payload):
    index = make_page("Archiwum", "archiwum", template="dj/pages/archive_index.html")
    _page, content, _ph = f.draft(
        make_page, "Edycja 0", "edycja-0", "dj/pages/archive_edition.html", parent=index
    )
    ArchiveMeta.objects.create(extended_object=content, edition_id=2)
    f.publish(content, superuser)
    html = _get(client, "/archiwum/")  # API martwe – karta bez nazwy edycji
    assert '<h2 class="card__title"><a href="/archiwum/edycja-0/">Edycja 0</a></h2>' in html
    assert "news-card__date" not in html

    from django.core.cache import cache

    cache.clear()  # bufor i bezpiecznik po martwym API
    main_api.set("chrome", chrome_payload())
    main_api.set(
        "editions",
        {"editions": [{"id": 2, "year_label": "0 2025/2026", "title": "0 edycja", "title_cap": "0 Edycja"}]},
    )
    html = _get(client, "/archiwum/")
    assert '<span class="news-card__date">0 2025/2026</span>' in html
    assert '<p class="card__foot"><a href="/archiwum/edycja-0/">Materiały i wyniki</a></p>' in html


@pytest.mark.django_db
def test_archive_documents_render_in_doc_list(client, make_page, superuser):
    _page, content, ph = f.draft(make_page, "Edycja 0", "edycja-0", "dj/pages/archive_edition.html")
    f.plugin(
        ph,
        "documents",
        "ArchiveDocumentPlugin",
        kind="SOLUTIONS",
        title="Rozwiązania etapu I",
        url="https://olimpiada.example/documents/9/rozw.pdf",
        extension="pdf",
    )
    f.publish(content, superuser)
    html = _get(client, "/edycja-0/")
    assert '<span class="tag">rozwiązania</span>' in html
    assert '<a href="https://olimpiada.example/documents/9/rozw.pdf">Rozwiązania etapu I</a>' in html
    assert '<span class="hint">(PDF)</span>' in html


# --- rozszerzenia i wersje ------------------------------------------------------------------------


@pytest.mark.django_db
def test_content_extensions_are_copied_to_new_draft(make_page, superuser):
    """Test obowiązkowy z § 6.1: nowa wersja robocza z opublikowanej kopiuje ``PageContentExtension``."""
    from djangocms_versioning.models import Version

    _page, content, _ph = f.draft(make_page, "Aktualność", "aktualnosc", "dj/pages/news.html")
    NewsMeta.objects.create(extended_object=content, date=datetime.date(2026, 9, 1), lead="Lead")
    DocumentMeta.objects.create(extended_object=content, version_label="1.0")
    ArchiveMeta.objects.create(extended_object=content, edition_id=7)
    f.publish(content, superuser)

    new_version = Version.objects.get_for_content(content).copy(superuser)
    draft = new_version.content
    assert draft.pk != content.pk
    assert draft.newsmeta.date == datetime.date(2026, 9, 1) and draft.newsmeta.lead == "Lead"
    assert draft.documentmeta.version_label == "1.0"
    assert draft.archivemeta.edition_id == 7
    assert draft.newsmeta.pk != content.newsmeta.pk  # kopia, a nie ten sam wiersz


@pytest.mark.django_db
def test_faq_anchor_survives_new_version(make_page, superuser):
    from djangocms_versioning.models import Version

    from apps.blocks.models import FAQEntry

    _page, content, ph = f.draft(make_page, "FAQ", "faq", "dj/pages/faq.html")
    entry = f.plugin(ph, "faq", "FAQEntryPlugin", question="Jak?", answer="<p>Tak.</p>")
    entry.refresh_from_db()
    f.publish(content, superuser)
    Version.objects.get_for_content(content).copy(superuser)
    anchors = set(FAQEntry.objects.values_list("anchor", flat=True))
    assert FAQEntry.objects.count() == 2 and anchors == {entry.anchor}


@pytest.mark.django_db
def test_extension_admin_refuses_published_content(client, make_page, superuser):
    _page, content, _ph = f.draft(make_page, "Aktualność", "aktualnosc", "dj/pages/news.html")
    client.force_login(superuser)
    url = f"/djcms/admin/dj_pages/newsmeta/add/?extended_object={content.pk}"
    response = client.post(url, {"date": "2026-09-01", "lead": "Szkic"})
    assert response.status_code == 302
    assert NewsMeta.objects.get(extended_object=content).lead == "Szkic"

    f.publish(content, superuser)
    meta = NewsMeta.objects.get(extended_object=content)
    response = client.post(
        f"/djcms/admin/dj_pages/newsmeta/{meta.pk}/change/", {"date": "2026-09-02", "lead": "Po cichu"}
    )
    assert response.status_code == 403
    assert NewsMeta.objects.get(pk=meta.pk).lead == "Szkic"


@pytest.mark.django_db
def test_archive_meta_form_offers_editions_from_api(client, make_page, superuser, main_api):
    _page, content, _ph = f.draft(make_page, "Edycja 0", "edycja-0", "dj/pages/archive_edition.html")
    client.force_login(superuser)
    url = f"/djcms/admin/dj_pages/archivemeta/add/?extended_object={content.pk}"
    html = client.get(url).content.decode()  # API martwe – zwykłe pole liczbowe
    assert 'type="number" name="edition_id"' in html
    assert "Lista edycji jest chwilowo niedostępna" in html

    from django.core.cache import cache

    cache.clear()
    main_api.set(
        "editions", {"editions": [{"id": 2, "year_label": "0 2025/2026", "title": "0 edycja 2025/2026"}]}
    )
    html = client.get(url).content.decode()
    assert '<select name="edition_id"' in html
    assert '<option value="2">0 edycja 2025/2026</option>' in html
    assert client.post(url, {"edition_id": "2"}).status_code == 302
    assert ArchiveMeta.objects.get(extended_object=content).edition_id == 2


# --- tryb edycji i podgląd ------------------------------------------------------------------------


EDITORIAL_PAGES = [
    ("dj/pages/content.html", "body", "TextPlugin", {"body": "<p>Treść</p>"}),
    ("dj/pages/news_index.html", "intro", "TextPlugin", {"body": "<p>Wstęp</p>"}),
    ("dj/pages/news.html", "body", "TextPlugin", {"body": "<p>Treść</p>"}),
    ("dj/pages/document_index.html", "intro", "TextPlugin", {"body": "<p>Wstęp</p>"}),
    ("dj/pages/document.html", "body", "TextPlugin", {"body": "<p>Treść</p>"}),
    ("dj/pages/partners.html", "partners", "PartnerPlugin", {"name": "Partner Testowy"}),
    ("dj/pages/archive_index.html", "intro", "TextPlugin", {"body": "<p>Wstęp</p>"}),
    ("dj/pages/faq.html", "faq", "FAQEntryPlugin", {"question": "Pytanie?", "answer": "<p>Tak.</p>"}),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("template", "slot", "plugin_type", "data"), EDITORIAL_PAGES)
def test_edit_and_preview_modes_render(client, make_page, superuser, template, slot, plugin_type, data):
    """Redaktor otwiera wersję roboczą każdego typu w trybie edycji i podglądu – bez błędu, z treścią."""
    _page, content, ph = f.draft(make_page, "Strona", "strona", template)
    f.plugin(ph, slot, plugin_type, **data)
    client.force_login(superuser)
    for url in (get_object_edit_url(content), get_object_preview_url(content)):
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        marker = data.get("name") or data.get("question") or re.sub(r"<[^>]+>", "", data["body"])
        assert marker in html, url
    edit_html = client.get(get_object_edit_url(content)).content.decode()
    assert "cms-placeholder" in edit_html  # slot rysowany do edycji


@pytest.mark.django_db
def test_toolbar_offers_metadata_only_on_matching_page_type(client, make_page, superuser):
    _page, news, _ph = f.draft(make_page, "Aktualność", "aktualnosc", "dj/pages/news.html")
    _page, plain, _ph = f.draft(make_page, "Zwykła", "zwykla", "dj/pages/content.html")
    client.force_login(superuser)
    assert "Data i lead aktualności" in client.get(get_object_edit_url(news)).content.decode()
    html = client.get(get_object_edit_url(plain)).content.decode()
    assert "Data i lead aktualności" not in html and "Metryka dokumentu" not in html


ADDABLE = [
    ("dj/pages/content.html", "body", "ImageWithCaptionPlugin"),
    ("dj/pages/content.html", "body", "DocumentLinkPlugin"),
    ("dj/pages/content.html", "body", "EmbedPlugin"),
    ("dj/pages/content.html", "body", "HeadingPlugin"),
    ("dj/pages/content.html", "body", "NoticePlugin"),
    ("dj/pages/content.html", "body", "DefinitionListPlugin"),
    ("dj/pages/content.html", "body", "SchedulePlugin"),
    ("dj/pages/content.html", "attachments", "AttachmentPlugin"),
    ("dj/pages/partners.html", "partners", "PartnerPlugin"),
    ("dj/pages/partners.html", "become_partner", "BecomePartnerPlugin"),
    ("dj/pages/faq.html", "faq", "FAQEntryPlugin"),
    ("dj/pages/archive_edition.html", "documents", "ArchiveDocumentPlugin"),
    ("dj/pages/home.html", "hero", "HeroPlugin"),
    ("dj/pages/home.html", "steps", "StepsSectionPlugin"),
    ("dj/pages/home.html", "about", "AboutSectionPlugin"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("template", "slot", "plugin_type"), ADDABLE)
def test_editor_can_open_add_form_of_every_plugin(client, make_page, superuser, template, slot, plugin_type):
    """Kryterium 9: redaktor może dodać każdą wtyczkę z 6.2 w jej slocie (formularz dodawania się otwiera)."""
    _page, _content, ph = f.draft(make_page, "Strona", "strona", template)
    client.force_login(superuser)
    response = client.get(
        "/djcms/admin/cms/placeholder/add-plugin/",
        {
            "placeholder_id": ph[slot].pk,
            "plugin_type": plugin_type,
            "plugin_language": "pl",
            "plugin_position": 1,
        },
    )
    assert response.status_code == 200, response.content[:500]


@pytest.mark.django_db
def test_editor_adds_notice_through_admin_form_and_html_is_cleaned(client, make_page, superuser):
    from apps.blocks.models import Notice

    _page, _content, ph = f.draft(make_page, "Strona", "strona", "dj/pages/content.html")
    client.force_login(superuser)
    query = (
        f"?placeholder_id={ph['body'].pk}&plugin_type=NoticePlugin&plugin_language=pl&plugin_position=1"
        "&cms_path=/strona/"
    )
    response = client.post(
        "/djcms/admin/cms/placeholder/add-plugin/" + query,
        {"tone": "warning", "text": "<p>Uwaga<script>alert(1)</script></p>"},
    )
    assert response.status_code == 200
    notice = Notice.objects.get()
    assert notice.tone == "warning" and "<script" not in notice.text and "Uwaga" in notice.text
