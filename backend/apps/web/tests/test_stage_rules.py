"""Zasady Organizacji Zawodów etapu (ZOZ-01): model, oczyszczanie, strona publiczna, panel, import.

Pilnujemy tego, na czym dokument stoi jako dokument prawny:

- **publiczna jest wyłącznie wersja opublikowana** – projekt i wersja wycofana dają 404, także pod
  adresem wersji i pod adresem PDF-u; etap cudzego konkursu – również 404,
- **obowiązuje ostatnio opublikowana**, a wcześniejsze zostają w historii z ramką „archiwalna”,
- **treść jest oczyszczona** – skrypt, styl i ``javascript:`` z pliku nie dojdą do czytelnika,
- **opublikowanej wersji nie zmienia się z panelu** (nowa wersja), a każda czynność ma wpis audytu,
- **komenda importu jest idempotentna** – drugie uruchomienie z tymi samymi plikami nic nie zmienia.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.cms.timeline import stage_rows
from apps.competitions.stage_rules import (
    StageRules,
    StageRulesStatus,
    chapters,
    create_rules,
    current_rules,
    publish_rules,
    sanitize_html,
    update_rules,
    withdraw_rules,
)
from apps.competitions.tests.factories import CurrentEditionFactory, StageFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.tenancy.tests.factories import grant_membership
from apps.web.coordinator_nav import stage_items

pytestmark = pytest.mark.django_db

BODY = (
    '<p>Wstęp z <a href="https://olimpiadakwantowa.pl/dokumenty/regulamin/">Regulaminem</a>.</p>'
    '<h2 id="par-1">§ 1. Harmonogram</h2><ol><li id="par-1-ust-1">Terminy określa Harmonogram.</li></ol>'
    '<h2 id="par-2">§ 2. Forma</h2><ol type="a"><li>zdalnie</li></ol>'
)
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def stage(competition):
    return StageFactory(edition=CurrentEditionFactory(competition=competition), name="Etap I")


def _published(stage, version="1.0", body=BODY, **kwargs):
    rules = create_rules(
        stage, None, version=version, document_date=date(2026, 11, 12), body_html=body, **kwargs
    )
    return publish_rules(rules, None)


def _coordinator_client(client_for, competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client = client_for(competition)
    client.force_login(user)
    return client


# --- oczyszczanie ---------------------------------------------------------------------------------


def test_sanitizer_keeps_document_structure_and_drops_code():
    dirty = (
        '<h2 id="par-1" onclick="x()" style="color:red">§ 1</h2>'
        "<script>alert(1)</script><style>p{}</style>"
        '<p>Tekst <a href="javascript:alert(1)">zły</a> <a href="mailto:contact@qaif.org">mail</a>'
        ' <img src=x onerror=alert(1)><iframe src="https://evil"></iframe></p>'
        '<ol type="a" start="2"><li>a</li></ol>'
    )

    clean = sanitize_html(dirty)

    assert '<h2 id="par-1">§ 1</h2>' in clean
    assert "script" not in clean and "alert" not in clean and "style" not in clean
    assert "<img" not in clean and "iframe" not in clean and "onerror" not in clean
    assert "<a>zły</a>" in clean
    assert '<a href="mailto:contact@qaif.org">mail</a>' in clean
    assert '<ol type="a" start="2">' in clean


def test_external_links_get_noopener_and_chapters_come_from_h2_ids():
    clean = sanitize_html(BODY)

    assert 'rel="noopener noreferrer"' in clean
    assert chapters(clean) == [
        {"anchor": "par-1", "text": "§ 1. Harmonogram"},
        {"anchor": "par-2", "text": "§ 2. Forma"},
    ]


def test_unclosed_tags_are_closed():
    assert sanitize_html("<p><strong>pogrubione") == "<p><strong>pogrubione</strong></p>"


# --- wersje i stany -------------------------------------------------------------------------------


def test_current_version_is_the_last_published_and_drafts_do_not_count(stage):
    first = _published(stage, "1.0")
    create_rules(stage, None, version="1.1", document_date=date(2026, 11, 20), body_html=BODY)

    assert current_rules(stage) == first

    second = _published(stage, "2.0")
    assert current_rules(stage) == second

    withdraw_rules(second, None)
    assert current_rules(stage) == first


def test_published_version_is_locked_and_duplicate_version_is_refused(stage):
    rules = _published(stage)

    with pytest.raises(DomainError) as locked:
        update_rules(rules, None, body_html="<p>inna treść</p>")
    assert locked.value.machine_code == "STAGE_RULES_PUBLISHED_LOCKED"

    with pytest.raises(DomainError) as duplicate:
        create_rules(stage, None, version="1.0", document_date=date(2026, 11, 12), body_html=BODY)
    assert duplicate.value.machine_code == "STAGE_RULES_DUPLICATE_VERSION"


def test_every_action_is_audited(stage):
    rules = create_rules(stage, None, version="1.0", document_date=date(2026, 11, 12), body_html=BODY)
    update_rules(rules, None, body_html=BODY + "<p>dopisek</p>")
    publish_rules(rules, None)
    withdraw_rules(rules, None)

    actions = list(
        AuditLog.objects.filter(target_type="competitions.stagerules", target_id=str(rules.pk))
        .order_by("at", "pk")
        .values_list("action", flat=True)
    )
    assert actions == [
        "stage_rules.created",
        "stage_rules.updated",
        "stage_rules.published",
        "stage_rules.withdrawn",
    ]


# --- strona publiczna -----------------------------------------------------------------------------


def test_public_page_shows_the_current_version_with_toc(client_for, competition, stage):
    _published(stage)

    response = client_for(competition).get(f"/dokumenty/zasady/{stage.pk}/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "§ 1. Harmonogram" in content
    assert 'href="#par-1"' in content
    assert "Wersja 1.0" in content
    assert "wersja obowiązująca" in content


def test_draft_and_withdrawn_versions_are_404(client_for, competition, stage):
    client = client_for(competition)
    draft = create_rules(stage, None, version="0.9", document_date=date(2026, 11, 1), body_html=BODY)

    assert client.get(f"/dokumenty/zasady/{stage.pk}/").status_code == 404
    assert client.get(f"/dokumenty/zasady/{stage.pk}/wersja/{draft.version}/").status_code == 404

    published = publish_rules(draft, None)
    withdraw_rules(published, None)
    assert client.get(f"/dokumenty/zasady/{stage.pk}/wersja/0.9/").status_code == 404


def test_older_version_stays_available_as_archive(client_for, competition, stage):
    _published(stage, "1.0")
    _published(stage, "1.1", body=BODY.replace("Harmonogram.", "Harmonogram i ZOZ."))
    client = client_for(competition)

    old = client.get(f"/dokumenty/zasady/{stage.pk}/wersja/1.0/").content.decode()
    current = client.get(f"/dokumenty/zasady/{stage.pk}/").content.decode()

    assert "wersja archiwalna" in old and "Wersja obowiązująca: 1.1" in old
    assert "Harmonogram i ZOZ." in current
    assert f"/dokumenty/zasady/{stage.pk}/wersja/1.0/" in current


def test_stage_of_another_competition_is_404(client_for, competition, other_competition, stage):
    _published(stage)

    assert client_for(other_competition).get(f"/dokumenty/zasady/{stage.pk}/").status_code == 404


def test_pdf_is_served_only_for_published_versions(client_for, competition, stage):
    client = client_for(competition)
    rules = create_rules(
        stage,
        None,
        version="1.0",
        document_date=date(2026, 11, 12),
        body_html=BODY,
        pdf=SimpleUploadedFile("zoz.pdf", PDF, content_type="application/pdf"),
    )
    url = f"/dokumenty/zasady/{stage.pk}/wersja/1.0/pdf/"

    assert client.get(url).status_code == 404

    publish_rules(rules, None)
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


def test_timeline_row_links_to_the_rules(competition, stage):
    edition = stage.edition
    assert str(stage_rows(edition, competition=competition)[0]["rules_url"]) == ""

    _published(stage)

    assert (
        str(stage_rows(edition, competition=competition)[0]["rules_url"]) == f"/dokumenty/zasady/{stage.pk}/"
    )


# --- panel koordynatora ---------------------------------------------------------------------------


def test_stage_menu_has_the_rules_screen(competition, stage):
    children = stage_items([stage], competition)[0].children

    assert "Zasady (ZOZ)" in [child.label for child in children]


def test_participant_cannot_open_the_coordinator_screen(client_for, competition, stage):
    participant = ParticipantFactory(competition=competition)
    client = client_for(competition)
    client.force_login(participant.user)

    response = client.get(f"/coordinator/stages/{stage.pk}/rules/")

    assert response.status_code in (302, 403)


def test_coordinator_creates_previews_and_publishes_a_version(client_for, competition, stage):
    client = _coordinator_client(client_for, competition)
    html_file = SimpleUploadedFile("zoz.html", BODY.encode("utf-8"), content_type="text/html")

    created = client.post(
        f"/coordinator/stages/{stage.pk}/rules/",
        {
            "version": "1.0",
            "document_date": "2026-11-12",
            "title": "Zasady Organizacji Zawodów (ZOZ)",
            "body_html": "",
            "html_file": html_file,
        },
    )
    rules = StageRules.objects.get(stage=stage, version="1.0")
    assert created.status_code == 302
    assert rules.status == StageRulesStatus.DRAFT

    preview = client.get(f"/coordinator/stage-rules/{rules.pk}/preview/")
    assert preview.status_code == 200 and "§ 2. Forma" in preview.content.decode()

    published = client.post(f"/coordinator/stage-rules/{rules.pk}/publish/")
    rules.refresh_from_db()
    assert published.status_code == 302
    assert rules.status == StageRulesStatus.PUBLISHED and rules.published_at is not None

    # Opublikowanej wersji nie edytuje się – strona edycji jest tylko do odczytu.
    edit = client.get(f"/coordinator/stage-rules/{rules.pk}/").content.decode()
    assert "Zapisz projekt" not in edit
    assert "Utwórz nową" in edit


def test_coordinator_of_another_competition_gets_404(client_for, competition, other_competition, stage):
    rules = _published(stage)
    client = _coordinator_client(client_for, other_competition)

    assert client.get(f"/coordinator/stage-rules/{rules.pk}/").status_code == 404
    assert client.post(f"/coordinator/stage-rules/{rules.pk}/withdraw/").status_code == 404
    rules.refresh_from_db()
    assert rules.status == StageRulesStatus.PUBLISHED


# --- komenda importu ------------------------------------------------------------------------------


def _import(tmp_path, competition, *extra, body=BODY):
    html = tmp_path / "zoz.html"
    html.write_text(body, encoding="utf-8")
    pdf = tmp_path / "zoz.pdf"
    pdf.write_bytes(PDF)
    out = StringIO()
    call_command(
        "import_stage_rules",
        "--competition",
        competition.slug,
        "--stage",
        "Etap I",
        "--version",
        "1.0",
        "--date",
        "2026-11-12",
        "--html",
        str(html),
        "--pdf",
        str(pdf),
        *extra,
        stdout=out,
    )
    return out.getvalue()


def test_import_command_is_idempotent(tmp_path, competition, stage):
    first = _import(tmp_path, competition, "--publish")
    audit_after_first = AuditLog.objects.filter(target_type="competitions.stagerules").count()
    second = _import(tmp_path, competition, "--publish")

    rules = StageRules.objects.get(stage=stage)
    assert "Opublikowano" in first
    assert "bez zmian" in second
    assert rules.status == StageRulesStatus.PUBLISHED and bool(rules.pdf)
    assert AuditLog.objects.filter(target_type="competitions.stagerules").count() == audit_after_first


def test_import_refuses_to_change_a_published_version_without_the_flag(tmp_path, competition, stage):
    _import(tmp_path, competition, "--publish")

    with pytest.raises(CommandError):
        _import(tmp_path, competition, body=BODY + "<p>poprawka</p>")

    _import(tmp_path, competition, "--replace-published", body=BODY + "<p>poprawka</p>")
    assert "poprawka" in StageRules.objects.get(stage=stage).body_html


def test_import_rejects_an_unknown_stage(tmp_path, competition, stage):  # noqa: ARG001 - etap w bazie
    html = tmp_path / "zoz.html"
    html.write_text(BODY, encoding="utf-8")

    with pytest.raises(CommandError, match="Nieznane"):
        call_command(
            "import_stage_rules",
            "--competition",
            competition.slug,
            "--stage",
            "Etap IX",
            "--version",
            "1.0",
            "--date",
            "2026-11-12",
            "--html",
            str(html),
        )
