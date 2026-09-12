"""Terminy etapów w panelu koordynatora (``/coordinator/stages/…``).

Cztery rzeczy, na których ten ekran stoi:

- zapis zmienia ``Stage`` **i** zostawia ślad w audycie z różnicą pól,
- zła kolejność terminów nie zapisuje niczego, a błąd stoi pod właściwym polem,
- reguły zależne od stanu zawodów (zgłoszenia, zamknięty etap) odmawiają z kodem 409,
- ekran należy wyłącznie do koordynatora.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import QualificationMode, Stage, StageFormat, StageKind
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.core.models import AuditLog
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

WARSAW_FORMAT = "%Y-%m-%dT%H:%M"


def form_data(stage: Stage, **overrides) -> dict:
    """Komplet pól ``StageForm`` w formacie ``<input type="datetime-local">`` (czas polski)."""
    data = {
        "name": stage.name,
        "format": stage.format,
        "location": stage.location,
        "grace_seconds": stage.grace_seconds,
        "opens_at": timezone.localtime(stage.opens_at).strftime(WARSAW_FORMAT),
        "deadline_at": timezone.localtime(stage.deadline_at).strftime(WARSAW_FORMAT),
        "review_deadline_at": timezone.localtime(stage.review_deadline_at).strftime(WARSAW_FORMAT),
        "appeal_window_opens_at": timezone.localtime(stage.appeal_window_opens_at).strftime(WARSAW_FORMAT),
        "appeal_window_closes_at": timezone.localtime(stage.appeal_window_closes_at).strftime(WARSAW_FORMAT),
    }
    data.update(overrides)
    return data


def local(dt) -> str:
    return timezone.localtime(dt).strftime(WARSAW_FORMAT)


# --- edycja terminów ----------------------------------------------------------------------------


def test_edit_saves_new_deadline_and_writes_audit(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)
    new_deadline = timezone.localtime(elim_stage.deadline_at) + timedelta(days=3)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, deadline_at=new_deadline.strftime(WARSAW_FORMAT), location="Kraków"),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert timezone.localtime(elim_stage.deadline_at).strftime(WARSAW_FORMAT) == new_deadline.strftime(
        WARSAW_FORMAT
    )
    assert elim_stage.location == "Kraków"

    entry = AuditLog.objects.get(action="stage.updated", target_id=str(elim_stage.pk))
    assert entry.actor == coordinator
    assert set(entry.diff) == {"deadline_at", "location"}
    # Diff jest czytelny bez zaglądania do bazy: obie wartości, daty w ISO.
    assert entry.diff["location"] == {"from": "", "to": "Kraków"}
    assert entry.diff["deadline_at"]["to"].startswith(new_deadline.date().isoformat())


def test_edit_form_shows_dates_in_local_time(web_client, coordinator, elim_stage):
    """Formularz otwiera się z godziną polską – inaczej koordynator poprawiałby UTC z ręki."""
    web_client.force_login(coordinator)

    content = web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/").content.decode()

    assert f'value="{local(elim_stage.deadline_at)}"' in content
    assert 'type="datetime-local"' in content


def test_edit_rejects_wrong_order_without_touching_the_stage(web_client, coordinator, elim_stage):
    """Deadline przed otwarciem: błąd pod polem, żadnego zapisu, żadnego wpisu w audycie."""
    web_client.force_login(coordinator)
    before = elim_stage.deadline_at
    broken = timezone.localtime(elim_stage.opens_at) - timedelta(days=1)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, deadline_at=broken.strftime(WARSAW_FORMAT)),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 400
    assert elim_stage.deadline_at == before
    assert not AuditLog.objects.filter(action="stage.updated").exists()
    assert response.context["form"].errors["deadline_at"]


def test_edit_refuses_to_move_deadline_into_the_past_with_submissions(
    web_client, coordinator, elim_stage, entry, problems
):
    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)
    before = elim_stage.deadline_at
    past = timezone.localtime(timezone.now()) - timedelta(hours=1)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, deadline_at=past.strftime(WARSAW_FORMAT)),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 409
    assert elim_stage.deadline_at == before
    assert "nie można cofnąć w przeszłość" in response.content.decode()


def test_edit_of_closed_stage_allows_only_review_and_appeal_window(web_client, coordinator, elim_stage):
    """Etap zamknięty: okno reklamacji i termin recenzji tak, otwarcie i deadline nie."""
    Stage.objects.filter(pk=elim_stage.pk).update(closed_at=timezone.now())
    elim_stage.refresh_from_db()
    web_client.force_login(coordinator)
    later = timezone.localtime(elim_stage.appeal_window_closes_at) + timedelta(days=5)

    allowed = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, appeal_window_closes_at=later.strftime(WARSAW_FORMAT)),
    )
    elim_stage.refresh_from_db()
    assert allowed.status_code == 302
    assert timezone.localtime(elim_stage.appeal_window_closes_at).strftime(WARSAW_FORMAT) == (
        later.strftime(WARSAW_FORMAT)
    )

    opens_before = elim_stage.opens_at
    moved_opening = timezone.localtime(elim_stage.opens_at) - timedelta(days=1)
    refused = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, opens_at=moved_opening.strftime(WARSAW_FORMAT)),
    )

    elim_stage.refresh_from_db()
    assert refused.status_code == 409
    assert elim_stage.opens_at == opens_before
    assert "Etap jest zamknięty" in refused.content.decode()


def test_results_published_at_is_not_editable(web_client, coordinator, elim_stage):
    """Znacznik publikacji nakłada i zdejmuje wyłącznie operacja publikacji wyników."""
    web_client.force_login(coordinator)
    stamp = timezone.now()
    Stage.objects.filter(pk=elim_stage.pk).update(results_published_at=stamp)

    content = web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/").content.decode()
    web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, results_published_at=""),
    )

    elim_stage.refresh_from_db()
    assert 'name="results_published_at"' not in content
    assert elim_stage.results_published_at == stamp


# --- termin wydarzenia (etap stacjonarny) --------------------------------------------------------


def test_event_dates_are_saved_without_touching_the_submission_window(web_client, coordinator, elim_stage):
    """Koordynator wpisuje dni zjazdu – okno oddawania prac zostaje dokładnie takie, jakie było.

    To sedno tych dwóch pól: na finale sesja egzaminacyjna jest kilkugodzinna, a pobyt trwa cztery
    dni. Dopóki była jedna para dat, ogłoszenie „4–7 czerwca” wymagało otwarcia uploadu na cztery
    dni albo skłamania na stronie.
    """
    web_client.force_login(coordinator)
    opens_before, deadline_before = elim_stage.opens_at, elim_stage.deadline_at

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(
            elim_stage,
            location="Kraków",
            event_starts_on="2027-06-04",
            event_ends_on="2027-06-07",
        ),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.event_range == (date(2027, 6, 4), date(2027, 6, 7))
    assert (elim_stage.opens_at, elim_stage.deadline_at) == (opens_before, deadline_before)

    entry = AuditLog.objects.get(action="stage.updated", target_id=str(elim_stage.pk))
    assert entry.diff["event_starts_on"] == {"from": None, "to": "2027-06-04"}


def test_event_dates_come_back_into_the_form_and_onto_the_dashboard(web_client, coordinator, elim_stage):
    """Zapisany termin wraca do pola ``date`` i staje na karcie etapu jako jedno wyrażenie."""
    Stage.objects.filter(pk=elim_stage.pk).update(
        location="Kraków", event_starts_on=date(2027, 6, 4), event_ends_on=date(2027, 6, 7)
    )
    web_client.force_login(coordinator)

    form_page = web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/").content.decode()
    dashboard = web_client.get("/coordinator/").content.decode()

    assert 'type="date"' in form_page
    assert 'value="2027-06-04"' in form_page
    assert 'value="2027-06-07"' in form_page
    assert "Wydarzenie" in dashboard
    assert "4–7 czerwca 2027" in dashboard


def test_half_of_the_event_range_is_rejected_under_the_field(web_client, coordinator, elim_stage):
    """Sam początek bez końca: błąd pod polem, żadnego zapisu, żadnego wpisu w audycie."""
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, event_starts_on="2027-06-04"),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 400
    assert response.context["form"].errors["event_ends_on"]
    assert elim_stage.event_range is None
    assert not AuditLog.objects.filter(action="stage.updated").exists()


def test_event_dates_can_be_cleared(web_client, coordinator, elim_stage):
    """Zjazd odwołany albo wpisany omyłkowo – puste pola czyszczą termin, a nie zostawiają połowy."""
    Stage.objects.filter(pk=elim_stage.pk).update(
        event_starts_on=date(2027, 6, 4), event_ends_on=date(2027, 6, 7)
    )
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, event_starts_on="", event_ends_on=""),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.event_range is None


# --- nazwa etapu ---------------------------------------------------------------------------------


def test_stage_name_falls_back_to_the_kind_label(elim_stage):
    """Puste pole nazwy nie zostawia etapu bez podpisu – wraca etykieta rodzaju."""
    assert elim_stage.name == ""
    assert elim_stage.display_name == elim_stage.get_kind_display()

    elim_stage.name = "Etap I – eliminacje szkolne"
    assert elim_stage.display_name == "Etap I – eliminacje szkolne"


def test_rename_saves_the_name_and_writes_audit(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, name="Etap I – eliminacje"),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.name == "Etap I – eliminacje"
    entry = AuditLog.objects.get(action="stage.updated", target_id=str(elim_stage.pk))
    assert entry.diff["name"] == {"from": "", "to": "Etap I – eliminacje"}


def test_closed_stage_can_still_be_renamed(web_client, coordinator, elim_stage):
    """Przemianowanie zamkniętego etapu niczego nie cofa – podpis w archiwum wolno poprawić."""
    Stage.objects.filter(pk=elim_stage.pk).update(closed_at=timezone.now())
    elim_stage.refresh_from_db()
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/", form_data(elim_stage, name="Eliminacje 2026")
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.name == "Eliminacje 2026"


def test_custom_name_replaces_the_kind_label_on_every_screen(
    web_client, coordinator, participant, entry, elim_stage
):
    """Nazwa nadana w panelu stoi wszędzie: pulpit koordynatora, ekran terminów, panel uczestnika.

    Oś czasu na stronach publicznych ma własny test (``apps/cms/tests/test_stage_timeline.py``) –
    tam jest fixture z zaimportowaną treścią ``/harmonogram/``.
    """
    Stage.objects.filter(pk=elim_stage.pk).update(name="Etap I – eliminacje")
    web_client.force_login(coordinator)
    assert "Etap I – eliminacje" in web_client.get("/coordinator/").content.decode()
    edit = web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/").content.decode()
    assert "Terminy etapu: Etap I – eliminacje" in edit

    web_client.force_login(participant.user)
    assert "Etap I – eliminacje" in web_client.get("/me/").content.decode()


# --- forma etapu ---------------------------------------------------------------------------------


def test_format_can_be_switched_while_the_stage_is_empty(web_client, coordinator, elim_stage):
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, format=StageFormat.INTERVIEW),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 302
    assert elim_stage.format == StageFormat.INTERVIEW


def test_format_cannot_be_switched_once_there_are_submissions(
    web_client, coordinator, elim_stage, entry, problems
):
    from apps.submissions.models import SubmissionStatus
    from apps.submissions.tests.factories import SubmissionFactory

    SubmissionFactory(entry=entry, problem=problems[0], status=SubmissionStatus.SUBMITTED)
    web_client.force_login(coordinator)

    response = web_client.post(
        f"/coordinator/stages/{elim_stage.pk}/edit/",
        form_data(elim_stage, format=StageFormat.INTERVIEW),
    )

    elim_stage.refresh_from_db()
    assert response.status_code == 409
    assert elim_stage.format == StageFormat.SUBMISSIONS
    assert "formy nie można zmienić" in response.content.decode()


# --- dodanie etapu ------------------------------------------------------------------------------


def test_new_stage_gets_scale_and_qualification_rule(web_client, coordinator, edition, elim_stage):
    web_client.force_login(coordinator)
    opens = timezone.localtime(timezone.now()) + timedelta(days=40)
    payload = {
        "kind": StageKind.DISTRICT,
        "name": "",
        "format": StageFormat.SUBMISSIONS,
        "location": "",
        "grace_seconds": 0,
        "opens_at": opens.strftime(WARSAW_FORMAT),
        "deadline_at": (opens + timedelta(days=10)).strftime(WARSAW_FORMAT),
        "review_deadline_at": (opens + timedelta(days=24)).strftime(WARSAW_FORMAT),
        "appeal_window_opens_at": (opens + timedelta(days=26)).strftime(WARSAW_FORMAT),
        "appeal_window_closes_at": (opens + timedelta(days=33)).strftime(WARSAW_FORMAT),
    }

    response = web_client.post("/coordinator/stages/new/", payload)

    stage = Stage.objects.get(edition=edition, kind=StageKind.DISTRICT)
    assert response.status_code == 302
    assert stage.scoring_scale.max_value == 6
    assert stage.qualification_rule.mode == QualificationMode.MIN_POINTS


def test_new_stage_offers_only_the_missing_kinds(web_client, coordinator, edition, elim_stage):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/stages/new/").content.decode()

    assert f'value="{StageKind.DISTRICT}"' in content
    assert f'value="{StageKind.FINAL}"' in content
    assert f'value="{StageKind.ELIM}"' not in content


# --- dashboard ----------------------------------------------------------------------------------


def test_dashboard_links_to_timeline_and_problems(web_client, coordinator, elim_stage, problems):
    web_client.force_login(coordinator)

    content = web_client.get("/coordinator/").content.decode()

    assert f'href="/coordinator/stages/{elim_stage.pk}/edit/"' in content
    assert f'href="/coordinator/stages/{elim_stage.pk}/problems/"' in content
    assert f"Zadania ({len(problems)})" in content
    # Karta pokazuje pełną oś czasu, nie połowę: okno reklamacji i miejsce też.
    assert "Okno reklamacji" in content
    assert "<dt>Miejsce</dt>" in content


# --- uprawnienia --------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["participant", "reviewer"])
def test_other_roles_cannot_touch_stage_timeline(web_client, participant, reviewer, elim_stage, role):
    user = participant.user if role == "participant" else reviewer.user
    web_client.force_login(user)

    assert web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/").status_code == 403
    assert web_client.post(f"/coordinator/stages/{elim_stage.pk}/edit/", {}).status_code == 403
    assert web_client.get("/coordinator/stages/new/").status_code == 403


def test_anonymous_is_redirected_to_login(web_client, elim_stage):
    response = web_client.get(f"/coordinator/stages/{elim_stage.pk}/edit/")

    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


def test_stage_of_another_edition_is_editable_by_id(web_client, coordinator, edition):
    """Panel edytuje etap wskazany identyfikatorem – także z edycji archiwalnej.

    Terminy edycji zamkniętej bywają poprawiane (błąd w archiwum), a ograniczenie ekranu do
    edycji bieżącej zamykałoby tę drogę bez powodu: uprawnienie jest rolą, nie edycją.
    """
    archived = StageFactory(kind=StageKind.FINAL)
    ProblemFactory(stage=archived, number=1)
    web_client.force_login(coordinator)

    response = web_client.get(f"/coordinator/stages/{archived.pk}/edit/")

    assert response.status_code == 200
    assert archived.edition != edition
