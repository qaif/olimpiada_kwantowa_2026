"""Ekrany medali (MED-01): bramka flagi, role, izolacja konkursów, strony publiczne i eksporty.

Olimpiada Kwantowa (flaga ``medals`` wyłączona): ani adresu, ani pozycji menu, ani odnośnika na
stronie wyników, a formularz „Wystaw” bez rodzajów medalowych.
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory
from apps.competitions.tests.factories import EditionFactory
from apps.core.models import AuditLog
from apps.medals import services
from apps.medals.models import MedalScheme
from apps.results.models import Anonymization
from apps.results.tests.conftest import graded_entry, make_stage

from .conftest import contestant, enable_medals, final_stage, publish

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator_client(client_for, iqo, coordinator):
    client = client_for(iqo)
    client.force_login(coordinator)
    return client


@pytest.fixture
def announced(iqo, coordinator):
    """Finał z ogłoszonymi wynikami (tryb CODE) i ogłoszonymi medalami."""
    stage = final_stage(iqo)
    # Sumy: Niemcy 12 (złoto), Polska 10 i Francja 10 (brąz – remis na granicy wchodzi w całości),
    # Francja 9 (wyróżnienie: ≥ 50 % najlepszego). Francja ma największą sumę krajową (19).
    entries = [
        contestant(stage, (6, 6), code="de"),
        contestant(stage, (5, 5), code="pl"),
        contestant(stage, (6, 4), code="fr"),
        contestant(stage, (5, 4), code="fr"),
    ]
    publish(stage, coordinator)
    services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    return stage, entries


# --- Olimpiada Kwantowa bez zmian ---------------------------------------------------------------


def test_kwantowa_has_no_medal_screens_menu_or_link(client_for, competition):
    stage = make_stage()
    graded_entry(stage, (5, 5))
    coordinator = CoordinatorFactory()
    publish(stage, coordinator)
    client = client_for(competition)
    client.force_login(coordinator)

    assert client.get("/coordinator/medals/").status_code == 404
    assert client.get(f"/coordinator/medals/{stage.pk}/").status_code == 404
    assert client.get(f"/results/{stage.pk}/medals/").status_code == 404
    assert client.get(f"/results/{stage.pk}/countries/").status_code == 404
    assert ">Medale<" not in client.get("/coordinator/").content.decode()
    assert "/medals/" not in client.get(f"/results/{stage.pk}/").content.decode()


def test_kwantowa_issue_form_offers_only_the_five_old_kinds(client_for, competition):
    stage = make_stage()
    graded_entry(stage, (5, 5))
    client = client_for(competition)
    client.force_login(CoordinatorFactory())

    content = client.get(f"/coordinator/stages/{stage.pk}/certificates/").content.decode()

    assert 'value="LAUREAT"' in content
    assert "MEDAL_GOLD" not in content
    assert "HON_MENTION" not in content


def test_medal_kinds_are_offered_for_graphic_templates_only_with_the_flag(client_for, competition):
    client = client_for(competition)
    client.force_login(CoordinatorFactory())
    assert "MEDAL_GOLD" not in client.get("/coordinator/certificates/templates/new/").content.decode()

    enable_medals(competition)
    assert "MEDAL_GOLD" in client.get("/coordinator/certificates/templates/new/").content.decode()


# --- role i izolacja ----------------------------------------------------------------------------


def test_participant_gets_403(client_for, iqo):
    stage = final_stage(iqo)
    client = client_for(iqo)
    client.force_login(ParticipantFactory().user)

    assert client.get(f"/coordinator/medals/{stage.pk}/").status_code == 403


def test_stage_of_another_competition_is_404(coordinator_client, other_competition):
    foreign = make_stage(edition=EditionFactory(competition=other_competition))

    assert coordinator_client.get(f"/coordinator/medals/{foreign.pk}/").status_code == 404
    assert coordinator_client.post(f"/coordinator/medals/{foreign.pk}/freeze/").status_code == 404


def test_menu_has_the_medals_item(coordinator_client):
    assert ">Medale<" in coordinator_client.get("/coordinator/").content.decode()


# --- ekran koordynatora ---------------------------------------------------------------------------


def test_list_and_detail_render_without_writing(coordinator_client, iqo):
    stage = final_stage(iqo)
    contestant(stage, (6, 6))

    listing = coordinator_client.get("/coordinator/medals/").content.decode()
    detail = coordinator_client.get(f"/coordinator/medals/{stage.pk}/")

    assert stage.display_name in listing
    assert "العربية" in listing  # stan potoku składu dla każdego języka
    assert detail.status_code == 200
    assert "Podgląd" in detail.content.decode()
    assert "≥ 12" in detail.content.decode()  # próg złota: jedyny wynik w polu
    assert not MedalScheme.objects.exists()


def test_scheme_form_saves_and_validates(coordinator_client, iqo):
    stage = final_stage(iqo)
    url = f"/coordinator/medals/{stage.pk}/"
    payload = {
        "gold_percent": "10",
        "silver_percent": "20",
        "bronze_percent": "30",
        "tie_policy": "INCLUSIVE",
        "hm_percent_of_best": "",
        "hm_full_solution": "on",
    }

    assert coordinator_client.post(url, payload).status_code == 302
    scheme = MedalScheme.objects.get(stage=stage)
    assert scheme.gold_percent == 10 and scheme.hm_percent_of_best is None

    response = coordinator_client.post(url, payload | {"bronze_percent": "90"})
    assert response.status_code == 400
    assert "100 %" in response.content.decode()


def test_override_and_freeze_through_the_screen(coordinator_client, iqo, coordinator):
    stage = final_stage(iqo)
    entry = contestant(stage, (1, 1))
    contestant(stage, (6, 6))
    publish(stage, coordinator)

    coordinator_client.post(
        f"/coordinator/medals/{stage.pk}/overrides/",
        {"entry_id": entry.pk, "award": "BRONZE", "justification": "Decyzja jury."},
    )
    coordinator_client.post(f"/coordinator/medals/{stage.pk}/freeze/")

    scheme = MedalScheme.objects.get(stage=stage)
    assert scheme.is_frozen
    assert scheme.awards[str(entry.pk)]["award"] == "BRONZE"
    detail = coordinator_client.get(f"/coordinator/medals/{stage.pk}/").content.decode()
    assert "Odmroź medale" in detail
    assert "Zmień nagrodę" not in detail


# --- strony publiczne ---------------------------------------------------------------------------


def test_public_pages_do_not_exist_before_the_announcement(client_for, iqo, coordinator):
    stage = final_stage(iqo)
    contestant(stage, (6, 6))
    publish(stage, coordinator)

    assert client_for(iqo).get(f"/results/{stage.pk}/medals/").status_code == 404
    assert client_for(iqo).get(f"/results/{stage.pk}/countries/").status_code == 404
    assert "/medals/" not in client_for(iqo).get(f"/results/{stage.pk}/").content.decode()


def test_public_medal_page_with_country_filter(client_for, iqo, announced):
    stage, entries = announced
    client = client_for(iqo)

    content = client.get(f"/results/{stage.pk}/medals/").content.decode()
    assert entries[0].participant.public_code in content
    assert "medal--gold" in content
    assert entries[0].participant.user.last_name not in content

    filtered = client.get(f"/results/{stage.pk}/medals/?country=fr").content.decode()
    assert entries[2].participant.public_code in filtered
    assert entries[3].participant.public_code in filtered
    assert entries[0].participant.public_code not in filtered

    # Kod spoza listy krajów jest ignorowany – strona pokazuje wszystkich, a nie pustą tabelę.
    unknown = client.get(f"/results/{stage.pk}/medals/?country=<script>").content.decode()
    assert entries[0].participant.public_code in unknown
    assert "<script>" not in unknown


def test_results_page_links_to_announced_medals(client_for, iqo, announced):
    stage, _entries = announced

    content = client_for(iqo).get(f"/results/{stage.pk}/").content.decode()

    assert f"/results/{stage.pk}/medals/" in content
    assert f"/results/{stage.pk}/countries/" in content


def test_country_ranking_shows_aggregates_only(client_for, iqo, announced):
    stage, entries = announced

    content = client_for(iqo).get(f"/results/{stage.pk}/countries/").content.decode()
    by_medals = client_for(iqo).get(f"/results/{stage.pk}/countries/?sort=medals").content.decode()

    assert "Germany" in content and "France" in content
    assert all(entry.participant.public_code not in content for entry in entries)
    # Żaden kraj nie ma trzech wyników – sumy i miejsca to „—”, a kolejność idzie po medalach (M3).
    assert "—" in content
    assert "co najmniej 3 wynikami" in content
    assert content.index("Germany") < content.index("France")
    assert by_medals.index("Germany") < by_medals.index("France")  # złoto przed brązem


def test_public_pages_of_another_competition_are_404(client_for, other_competition, announced):
    stage, _entries = announced

    assert client_for(other_competition).get(f"/results/{stage.pk}/medals/").status_code == 404


def test_public_page_respects_name_consent(client_for, iqo, coordinator):
    stage = final_stage(iqo)
    named = contestant(stage, (6, 6), publish_full_name=True, guardian_consent=True)
    hidden = contestant(stage, (5, 5))
    for entry, surname in ((named, "Zgodna"), (hidden, "Ukryta")):
        entry.participant.user.last_name = surname
        entry.participant.user.save(update_fields=["last_name"])
    publish(stage, coordinator, Anonymization.FULL_ALL)
    services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    content = client_for(iqo).get(f"/results/{stage.pk}/medals/").content.decode()

    assert named.participant.user.get_full_name() in content
    assert hidden.participant.user.last_name not in content
    assert hidden.participant.public_code in content


# --- eksporty ------------------------------------------------------------------------------------


def test_csv_export_has_names_and_is_audited(coordinator_client, announced):
    stage, entries = announced

    response = coordinator_client.get(f"/coordinator/medals/{stage.pk}/export.csv")
    body = b"".join(response.streaming_content).decode("utf-8-sig")

    assert entries[0].participant.user.last_name in body
    assert "złoty medal" in body
    assert AuditLog.objects.filter(action="medals.exported", diff__format="csv").exists()


def test_ceremony_pdf_is_grouped_in_presentation_order(coordinator_client, announced):
    stage, _entries = announced
    from io import BytesIO

    from pypdf import PdfReader

    response = coordinator_client.get(f"/coordinator/medals/{stage.pk}/ceremony.pdf")
    text = PdfReader(BytesIO(response.content)).pages[0].extract_text()

    assert response["Content-Type"] == "application/pdf"
    assert text.index("wyróżnienie") < text.index("brązowy medal") < text.index("złoty medal")
    assert AuditLog.objects.filter(action="medals.exported", diff__format="pdf").exists()


def test_issue_and_zip_through_the_screen(coordinator_client, announced):
    stage, _entries = announced

    coordinator_client.post(f"/coordinator/medals/{stage.pk}/certificates/", {"participation": "on"})
    response = coordinator_client.get(f"/coordinator/medals/{stage.pk}/certificates.zip")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


# --- M5: dyplom nieaktualny – strona weryfikacji i „Moje dyplomy” -------------------------------------


def test_outdated_medal_certificate_is_flagged_and_hidden_from_the_student(
    client_for, iqo, announced, coordinator
):
    from apps.results.models import Certificate, CertificateKind

    stage, entries = announced
    scheme = services.scheme_for(stage)
    services.issue_certificates(scheme, participation=True, actor=coordinator)
    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)
    student = client_for(iqo)
    student.force_login(entries[0].participant.user)
    assert gold.number in student.get("/me/certificates/").content.decode()

    services.unfreeze(scheme, justification="Korekta", actor=coordinator)

    verify_page = client_for(iqo).get(f"/dyplomy/{gold.code}/").content.decode()
    assert "nie jest już aktualny" in verify_page
    listing = student.get("/me/certificates/").content.decode()
    assert gold.number not in listing
    assert student.get(f"/me/certificates/{gold.pk}/").status_code == 404


def test_current_certificate_verification_has_no_warning(client_for, iqo, announced, coordinator):
    from apps.results.models import Certificate, CertificateKind

    stage, entries = announced
    services.issue_certificates(services.scheme_for(stage), participation=False, actor=coordinator)
    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)

    assert "nie jest już aktualny" not in client_for(iqo).get(f"/dyplomy/{gold.code}/").content.decode()


# --- L4/L5: ostrzeżenie EXCLUSIVE, limity żądań ------------------------------------------------------


def test_screen_warns_when_exclusive_policy_leaves_a_pool_empty(coordinator_client, iqo):
    stage = final_stage(iqo)
    for _ in range(3):
        contestant(stage, (5, 5))
    scheme = services.scheme_for(stage, create=True)
    scheme.tie_policy = "EXCLUSIVE"
    scheme.save(update_fields=["tie_policy"])

    content = coordinator_client.get(f"/coordinator/medals/{stage.pk}/").content.decode()

    assert "nikt nie dostaje" in content and "złoty medal" in content


def test_override_removal_and_csv_export_are_throttled():
    from apps.medals import views

    assert views.OverrideRemoveView.throttle_scope == "medals"
    assert views.ExportCsvView.throttle_scope == "medals"
    assert "GET" in views.ExportCsvView.throttle_methods
