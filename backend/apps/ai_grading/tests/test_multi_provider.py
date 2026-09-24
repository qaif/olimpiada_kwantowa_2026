"""Wielu dostawców: klucze i umowy powierzenia, wybór dostawcy i modelu, porównanie, ceny, rejestr.

Model jest podmieniony na poziomie ``services.call_model`` (dyspozytor dostawców) – test dostaje
żądanie zbudowane przez **prawdziwego** dostawcę (z jego nazwą w ``request.provider``) i sprawdza,
co serwis zrobi z odpowiedzią. Żadne żądanie nie wychodzi do sieci.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.accounts.processing_register import activities_for, ai_grading_activity
from apps.accounts.profile import anonymise_account
from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory, ParticipantFactory
from apps.ai_grading import services, tasks
from apps.ai_grading.models import AiAssessment, AiAssessmentStatus, AiGradingSettings, AiProviderAccount
from apps.ai_grading.providers.base import Usage
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.tenancy.tests.factories import grant_membership

from .conftest import PROVIDER_KEYS, SENTINEL_SUMMARY, answer, enable_ai, make_submission, result, with_key

pytestmark = pytest.mark.django_db

SETTINGS_URL = "/coordinator/ai-grading/"


@pytest.fixture
def coordinator(competition):
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


@pytest.fixture
def ready(competition, coordinator):
    enable_ai(competition)
    with_key(competition, coordinator)
    return competition


@pytest.fixture
def model(monkeypatch):
    """Podmieniony dyspozytor: zapisuje (klucz, żądanie) i oddaje kolejne odpowiedzi."""
    state = {"replies": [], "requests": []}

    def fake_call(api_key, request):
        state["requests"].append((api_key, request))
        reply = state["replies"].pop(0) if state["replies"] else result(model=request["model"])
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(services, "call_model", fake_call)
    return state


@pytest.fixture
def web(coordinator):
    client = Client()
    client.force_login(coordinator)
    return client


def run_all():
    for pk in AiAssessment.objects.filter(status=AiAssessmentStatus.PENDING).values_list("pk", flat=True):
        services.run_assessment(pk)


# --- bramka umowy powierzenia ----------------------------------------------------------------------


def test_provider_without_dpa_confirmation_gets_no_participant_work(competition, problem, coordinator):
    enable_ai(competition)
    with_key(competition, coordinator, provider="openai", dpa=False)
    make_submission(problem)

    with pytest.raises(DomainError) as info:
        services.request_generation(
            problem, regenerate=False, actor=coordinator, provider="openai", model="gpt-6-sol"
        )

    assert info.value.machine_code == "AI_DPA_MISSING"
    assert not AiAssessment.objects.exists()


def test_dpa_confirmation_is_audited_with_who_and_when_and_is_idempotent(competition, coordinator):
    enable_ai(competition)

    account, changed = services.set_dpa_confirmation(
        competition, "google", True, actor=coordinator, note="umowa z 1.09"
    )
    again, changed_again = services.set_dpa_confirmation(
        competition, "google", True, actor=CoordinatorFactory()
    )

    assert changed is True and changed_again is False
    assert again.dpa_confirmed_by == coordinator
    assert again.dpa_confirmed_at == account.dpa_confirmed_at
    entry = AuditLog.objects.get(action="ai_grading.dpa_confirmed")
    assert entry.actor == coordinator
    assert entry.diff == {"provider": "google", "note": "umowa z 1.09", "via": "panel", "info_version": None}


def test_revoking_the_dpa_stops_queued_work_before_it_is_sent(ready, problem, coordinator, model):
    make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.set_dpa_confirmation(ready, "anthropic", False, actor=coordinator)

    run_all()

    assessment = AiAssessment.objects.get()
    assert assessment.error_code == "dpa"
    assert assessment.sent_at is None
    assert model["requests"] == []


def test_missing_sdk_package_blocks_the_provider(ready, problem, coordinator, monkeypatch):
    from apps.ai_grading import providers

    with_key(ready, coordinator, provider="openai")
    make_submission(problem)
    monkeypatch.setattr(providers.get("openai"), "is_available", lambda: False)

    with pytest.raises(DomainError) as info:
        services.request_generation(problem, regenerate=False, actor=coordinator, provider="openai")

    assert info.value.machine_code == "AI_PROVIDER_UNAVAILABLE"


# --- wybór dostawcy i modelu, kilka ocen jednej pracy ---------------------------------------------


def test_same_work_can_be_assessed_by_several_providers_as_separate_rows(ready, problem, coordinator, model):
    with_key(ready, coordinator, provider="openai")
    submission = make_submission(problem)

    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.request_generation(
        problem, regenerate=False, actor=coordinator, provider="openai", model="gpt-6-sol"
    )
    again = services.request_generation(
        problem, regenerate=False, actor=coordinator, provider="openai", model="gpt-6-sol"
    )
    run_all()

    rows = AiAssessment.objects.filter(submission=submission).order_by("provider")
    assert [(row.provider, row.requested_model, row.status) for row in rows] == [
        ("anthropic", "claude-opus-5", "DONE"),
        ("openai", "gpt-6-sol", "DONE"),
    ]
    assert again.count == 0  # idempotencja: (wersja pracy, dostawca, model)
    requests = [request for _, request in model["requests"]]
    assert sorted(request.provider for request in requests) == ["anthropic", "openai"]
    openai_key, openai_request = next((k, r) for k, r in model["requests"] if r.provider == "openai")
    assert openai_key.reveal() == PROVIDER_KEYS["openai"]
    assert openai_request["model"] == "gpt-6-sol"
    assert openai_request["store"] is False


def test_regenerate_resets_only_the_assessment_of_that_model(ready, problem, coordinator, model):
    with_key(ready, coordinator, provider="openai")
    make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    services.request_generation(
        problem, regenerate=False, actor=coordinator, provider="openai", model="gpt-6-sol"
    )
    run_all()

    plan = services.request_generation(
        problem, regenerate=True, actor=coordinator, provider="openai", model="gpt-6-sol"
    )

    assert plan.count == 1
    statuses = dict(AiAssessment.objects.values_list("provider", "status"))
    assert statuses == {"anthropic": "DONE", "openai": "PENDING"}


def test_default_target_skips_a_default_provider_without_dpa(competition, coordinator):
    enable_ai(competition)
    with_key(competition, coordinator, dpa=False)  # Anthropic – domyślny, ale bez umowy
    with_key(competition, coordinator, provider="google")

    assert services.default_target(competition) == ("google", "gemini-3.8-flash")
    assert services.default_target(competition, for_tests=True) == ("anthropic", "claude-opus-5")
    assert [group["provider"] for group in services.targets(competition, for_tests=False)] == ["google"]
    assert [group["provider"] for group in services.targets(competition, for_tests=True)] == [
        "anthropic",
        "google",
    ]


def test_preview_and_confirm_carry_the_chosen_provider_and_model(
    ready, problem, coordinator, web, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(tasks.run_ai_assessment, "delay", lambda *args, **kwargs: None)
    with_key(ready, coordinator, provider="openai")
    make_submission(problem)
    url = f"/coordinator/problems/{problem.pk}/ai/generate/"

    preview = web.post(url, {"action": "preview", "target": "openai:gpt-6-luna"}).content.decode()

    assert "OpenAI (GPT)" in preview and "<code>gpt-6-luna</code>" in preview
    assert 'name="target" value="openai:gpt-6-luna"' in preview  # formularz potwierdzenia
    assert "Anthropic (Claude)" in preview  # wybór dostawcy na ekranie podglądu
    with django_capture_on_commit_callbacks(execute=True):
        web.post(url, {"action": "confirm", "target": "openai:gpt-6-luna"})
    assessment = AiAssessment.objects.get()
    assert (assessment.provider, assessment.requested_model) == ("openai", "gpt-6-luna")


def test_confirm_with_a_provider_without_dpa_is_refused(competition, problem, coordinator, web):
    enable_ai(competition)
    with_key(competition, coordinator, provider="openai", dpa=False)
    make_submission(problem)

    response = web.post(
        f"/coordinator/problems/{problem.pk}/ai/generate/",
        {"action": "confirm", "target": "openai:gpt-6-sol"},
        follow=True,
    )

    assert (
        "Umowa powierzenia (DPA) z dostawcą OpenAI (GPT) nie jest potwierdzona" in response.content.decode()
    )
    assert not AiAssessment.objects.exists()


def test_custom_model_identifier_is_used_and_checked(ready, problem, coordinator, web, monkeypatch):
    monkeypatch.setattr(tasks.run_ai_assessment, "delay", lambda *args, **kwargs: None)
    make_submission(problem)
    url = f"/coordinator/problems/{problem.pk}/ai/generate/"

    web.post(
        url, {"action": "confirm", "target": "anthropic:claude-opus-5", "custom_model": "claude-opus-5-1"}
    )
    web.post(url, {"action": "confirm", "target": "anthropic:claude-opus-5", "custom_model": "zły model!"})

    assert list(AiAssessment.objects.values_list("requested_model", flat=True)) == ["claude-opus-5-1"]


def test_meta_contributor_models_are_refused(competition, coordinator):
    enable_ai(competition)

    with pytest.raises(DomainError) as info:
        services.update_options(
            competition,
            model="muse-spark-1.3-contributor",
            provider="meta",
            spending_limit_usd=None,
            actor=coordinator,
        )

    assert info.value.machine_code == "AI_MODEL_FORBIDDEN"


def test_default_provider_follows_the_curated_model_list(competition, coordinator, web):
    enable_ai(competition)

    web.post(SETTINGS_URL, {"action": "options", "model": "gemini-3.5-flash-lite", "spending_limit_usd": ""})
    row = services.settings_for(competition)
    assert (row.provider, row.model) == ("google", "gemini-3.5-flash-lite")

    web.post(
        SETTINGS_URL,
        {
            "action": "options",
            "model": "gpt-6-sol",
            "custom_model": "gpt-6.1-astra",
            "custom_provider": "openai",
            "spending_limit_usd": "",
        },
    )
    row = services.settings_for(competition)
    assert (row.provider, row.model) == ("openai", "gpt-6.1-astra")


# --- panele recenzenta, uczestnik, eksport ---------------------------------------------------------


@pytest.fixture
def compared(ready, problem, coordinator, model):
    """Praca oceniona dwoma dostawcami – najpierw Anthropic, potem OpenAI."""
    with_key(ready, coordinator, provider="openai")
    submission = make_submission(problem)
    services.request_generation(problem, regenerate=False, actor=coordinator)
    run_all()
    model["replies"] = [result(answer(proposed_points=2, summary="Zdanie OpenAI."), model="gpt-6-sol")]
    services.request_generation(
        problem, regenerate=False, actor=coordinator, provider="openai", model="gpt-6-sol"
    )
    run_all()
    return submission


def test_reviewer_sees_one_panel_per_provider_newest_first(compared):
    reviewer = ActiveReviewerFactory()
    review = ReviewFactory(submission=compared, reviewer=reviewer, status=ReviewStatus.ASSIGNED)
    client = Client()
    client.force_login(reviewer.user)

    content = client.get(f"/review/{review.pk}/").content.decode()

    newest = content.index("Ocena AI – OpenAI gpt-6-sol (sugestia, niewiążąca)")
    older = content.index("Ocena AI – Anthropic claude-opus-5 (sugestia, niewiążąca)")
    assert newest < older
    assert 'data-ai-prefill="2"' in content and 'data-ai-prefill="5"' in content


def test_export_lists_each_provider_as_a_recipient(compared):
    items = services.export_section(compared.entry.participant)

    assert sorted(item["dostawca"] for item in items) == ["Anthropic (Claude)", "OpenAI (GPT)"]
    recipients = " ".join(item["przekazano_do"] for item in items)
    assert "Anthropic PBC" in recipients and "OpenAI" in recipients


def test_overview_counts_every_assessment_and_compares_models(compared, problem):
    FinalGradeFactory(submission=compared, score=5)

    overview = services.problem_overview(problem)

    assert overview["counts"]["DONE"] == 2
    [row] = overview["rows"]
    assert [item.provider for item in row["assessments"]] == ["openai", "anthropic"]
    by_model = {item["model"]: item["mean_abs_diff"] for item in overview["agreement_by_model"]}
    assert by_model == {"claude-opus-5": Decimal("0.00"), "gpt-6-sol": Decimal("3.00")}


def test_anonymisation_erases_assessments_of_every_provider(compared, coordinator):
    anonymise_account(compared.entry.participant.user, actor=coordinator)

    assert not AiAssessment.objects.exists()


# --- klucze: żaden ekran, log ani audyt ich nie pokazuje -------------------------------------------


def test_keys_of_every_provider_are_write_only_on_the_screen(competition, coordinator, web):
    enable_ai(competition)
    for name, value in PROVIDER_KEYS.items():
        web.post(SETTINGS_URL, {"action": "set_key", "provider": name, "api_key": value})

    content = web.get(SETTINGS_URL).content.decode()

    for name, value in PROVIDER_KEYS.items():
        assert value not in content
        assert f"kończy się na …{value[-4:]}" in content
        assert services.account_for(competition, name).has_key
    dumped = json.dumps([entry.diff for entry in AuditLog.objects.filter(action__startswith="ai_grading.")])
    for value in PROVIDER_KEYS.values():
        assert value not in dumped and value[-4:] not in dumped


def test_invalid_keys_are_not_echoed_for_any_provider(competition, coordinator, web):
    enable_ai(competition)

    for name in ("openai", "google", "meta"):
        content = web.post(
            SETTINGS_URL, {"action": "set_key", "provider": name, "api_key": "tajne haslo"}, follow=True
        ).content.decode()
        assert "tajne haslo" not in content
        assert not services.account_for(competition, name).has_key


def test_settings_page_lists_every_provider_and_marks_unavailable_packages(
    competition, coordinator, web, monkeypatch
):
    from apps.ai_grading import providers

    enable_ai(competition)
    monkeypatch.setattr(providers.get("google"), "is_available", lambda: False)

    content = web.get(SETTINGS_URL).content.decode()

    for label in ("Anthropic", "OpenAI", "Google", "Meta"):
        assert f'id="dostawca-{label.lower()}"' in content
    assert "niedostępny – brak pakietu „google-genai”" in content
    assert "Potwierdź umowę powierzenia" in content


def dpa_url(provider: str) -> str:
    return f"/coordinator/ai-grading/dpa/{provider}/"


def test_settings_page_links_to_the_confirmation_page_instead_of_a_checkbox(competition, coordinator, web):
    enable_ai(competition)

    content = web.get(SETTINGS_URL).content.decode()

    for provider in ("anthropic", "openai", "google", "meta"):
        assert f'href="{dpa_url(provider)}"' in content
    assert 'name="ack"' not in content  # oświadczenie jest wyłącznie na ekranie informacji


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google", "meta"])
def test_confirmation_page_shows_the_provider_specific_information(competition, coordinator, web, provider):
    from apps.ai_grading.disclosures import AGE_WARNING, DISCLOSURES

    enable_ai(competition)
    info = DISCLOSURES[provider]

    response = web.get(dpa_url(provider))
    content = response.content.decode()

    assert response.status_code == 200
    assert info.recipient in content and info.retention in content and info.training in content
    assert info.zero_retention in content and info.transfer in content
    for _label, url in info.links:
        assert f'href="{url}"' in content
    assert "praca uczestnika" in content.lower() or "plik pracy uczestnika" in content
    assert (
        f"potwierdzam, że organizator zawarł umowę\n          powierzenia z {info.label} obejmującą tę usługę"
        in content
    )
    assert f'name="info_version" value="{info.version}"' in content
    assert (AGE_WARNING in content) is (provider in ("google", "meta"))
    if provider == "meta":
        assert "Llama API" in content and "Meta Model API" in content


def test_confirmation_without_the_checkbox_is_refused(competition, coordinator, web):
    from apps.ai_grading.disclosures import DISCLOSURES

    enable_ai(competition)

    response = web.post(dpa_url("google"), {"info_version": DISCLOSURES["google"].version})

    assert response.status_code == 400
    assert not services.account_for(competition, "google").dpa_confirmed
    assert not AuditLog.objects.filter(action="ai_grading.dpa_confirmed").exists()


def test_confirmation_with_an_outdated_information_version_is_refused(competition, coordinator, web):
    enable_ai(competition)

    response = web.post(dpa_url("openai"), {"ack": "on", "info_version": "2026-01-01/0000"})

    assert response.status_code == 409
    assert not services.account_for(competition, "openai").dpa_confirmed


def test_confirmation_records_who_when_and_the_information_version(competition, coordinator, web):
    from apps.ai_grading.disclosures import DISCLOSURES

    enable_ai(competition)
    version = DISCLOSURES["meta"].version

    response = web.post(dpa_url("meta"), {"ack": "on", "info_version": version, "note": "umowa 1.09"})

    assert response.status_code == 302
    account = services.account_for(competition, "meta")
    assert account.dpa_confirmed and account.dpa_confirmed_by == coordinator
    assert account.dpa_info_version == version
    entry = AuditLog.objects.get(action="ai_grading.dpa_confirmed")
    assert entry.diff == {"provider": "meta", "note": "umowa 1.09", "via": "panel", "info_version": version}


def test_revoking_is_one_click_on_the_settings_page(competition, coordinator, web):
    enable_ai(competition)
    with_key(competition, coordinator, provider="openai")

    content = web.get(SETTINGS_URL).content.decode()
    assert 'value="dpa_revoke"' in content and "data-confirm=" in content

    web.post(SETTINGS_URL, {"action": "dpa_revoke", "provider": "openai"})

    assert not services.account_for(competition, "openai").dpa_confirmed
    assert AuditLog.objects.filter(action="ai_grading.dpa_revoked").count() == 1


def test_confirmation_page_is_for_coordinators_and_known_providers_only(competition, coordinator, web):
    enable_ai(competition)
    assert web.get(dpa_url("mistral")).status_code == 404

    participant = ParticipantFactory()
    grant_membership(participant.user, competition, CompetitionRole.PARTICIPANT)
    client = Client()
    client.force_login(participant.user)
    assert client.post(dpa_url("openai"), {"ack": "on"}).status_code == 403


# --- ceny i model bez ceny ---------------------------------------------------------------------------


def test_cost_uses_provider_cache_multipliers():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )

    openai = services.cost_of(usage, served_model="gpt-6-sol", requested_model="gpt-6-sol", provider="openai")
    google = services.cost_of(
        usage, served_model="gemini-3.8-flash", requested_model="gemini-3.8-flash", provider="google"
    )

    assert openai == Decimal("2") + Decimal("10") + Decimal("2.5") + Decimal("0.2")
    assert google == Decimal("0.75") + Decimal("3.75") + Decimal("0.75") + Decimal("0.075")


def test_unknown_model_has_unknown_cost_and_counts_tokens_only(ready, problem, coordinator, model):
    make_submission(problem)
    plan = services.plan_generation(problem, regenerate=False, provider="anthropic", model="claude-nowy-7")
    assert plan.estimate_usd is None and plan.estimate_tokens > 0

    services.request_generation(problem, regenerate=False, actor=coordinator, model="claude-nowy-7")
    run_all()

    assessment = AiAssessment.objects.get()
    row = services.settings_for(ready)
    assert assessment.status == AiAssessmentStatus.DONE
    assert assessment.cost_known is False and assessment.cost_usd == 0
    assert row.total_unpriced_calls == 1 and row.total_cost_usd == 0
    assert row.total_input_tokens > 0


def test_spending_limit_refuses_a_model_without_price(ready, problem, coordinator):
    make_submission(problem)
    services.update_options(ready, model="claude-opus-5", spending_limit_usd=Decimal("10"), actor=coordinator)

    with pytest.raises(DomainError) as info:
        services.request_generation(problem, regenerate=False, actor=coordinator, model="claude-nowy-7")

    assert info.value.machine_code == "AI_PRICE_UNKNOWN"


def test_price_entered_by_the_coordinator_is_used(ready, problem, coordinator, model, web):
    web.post(
        SETTINGS_URL,
        {
            "action": "prices",
            "new_provider": "anthropic",
            "new_model": "claude-nowy-7",
            "new_in": "1",
            "new_out": "2",
        },
    )
    AiGradingSettings.objects.filter(competition=ready).update(spending_limit_usd=Decimal("10"))
    make_submission(problem)

    services.request_generation(problem, regenerate=False, actor=coordinator, model="claude-nowy-7")
    run_all()

    assessment = AiAssessment.objects.get()
    # result(): 1000 wej. + 4000 zapisu cache·1,25 po 1 USD, 2000 wyj. po 2 USD
    assert assessment.cost_known is True
    assert assessment.cost_usd == Decimal("0.010000")
    assert AuditLog.objects.filter(action="ai_grading.prices_updated").exists()


def test_price_equal_to_the_default_is_not_stored_as_an_override(competition, coordinator):
    enable_ai(competition)

    services.set_prices(competition, {"openai/gpt-6-sol": (Decimal("2"), Decimal("10"))}, actor=coordinator)
    services.set_prices(competition, {"openai/gpt-6-luna": (Decimal("0.2"), Decimal("1"))}, actor=coordinator)

    assert services.settings_for(competition).price_overrides == {"openai/gpt-6-luna": ["0.2", "1"]}


# --- rejestr czynności -------------------------------------------------------------------------------


def test_register_lists_only_providers_with_a_key_and_a_confirmed_dpa(competition, coordinator):
    enable_ai(competition)
    assert "nie opuszczają serwera" in " ".join(ai_grading_activity(competition).recipients)

    with_key(competition, coordinator, provider="openai")  # klucz + umowa
    with_key(competition, coordinator, provider="google", dpa=False)  # sam klucz
    services.set_dpa_confirmation(competition, "meta", True, actor=coordinator)  # sama umowa

    [row] = [item for item in activities_for(competition) if item.key == "ocena_ai"]
    recipients = " ".join(row.recipients)
    assert "OpenAI" in recipients
    assert "Google" not in recipients and "Meta" not in recipients and "Anthropic" not in recipients
    assert "państwa trzeciego" in recipients


# --- komenda operatora --------------------------------------------------------------------------------


def test_command_confirms_every_provider_idempotently(competition, coordinator):
    enable_ai(competition)
    note = "potwierdzone przez organizatora w rozmowie 24.09.2026"

    call_command(
        "confirm_ai_provider_dpa",
        competition=competition.slug,
        provider="all",
        confirmed_by=coordinator.email.upper(),
        note=note,
    )
    first = {row.provider: row.dpa_confirmed_at for row in AiProviderAccount.objects.all()}
    call_command(
        "confirm_ai_provider_dpa",
        competition=competition.slug,
        provider="all",
        confirmed_by=coordinator.email,
    )

    rows = AiProviderAccount.objects.all()
    assert {row.provider for row in rows} == {"anthropic", "openai", "google", "meta"}
    assert all(row.dpa_confirmed_by == coordinator and row.dpa_note == note for row in rows)
    assert {row.provider: row.dpa_confirmed_at for row in rows} == first
    entries = AuditLog.objects.filter(action="ai_grading.dpa_confirmed")
    assert entries.count() == 4
    assert all(entry.diff["via"] == "command" and entry.diff["note"] == note for entry in entries)
    # Operator informacji nie widział – wersja pusta, a ekran pisze „wpisane komendą operatora”.
    assert all(entry.diff["info_version"] is None for entry in entries)
    assert all(row.dpa_info_version == "" for row in rows)
    assert all(entry.competition_id == competition.pk for entry in entries)


def test_command_refuses_unknown_accounts_and_competitions(competition, coordinator):
    with pytest.raises(CommandError):
        call_command(
            "confirm_ai_provider_dpa", competition="nie-ma", provider="openai", confirmed_by=coordinator.email
        )
    with pytest.raises(CommandError):
        call_command(
            "confirm_ai_provider_dpa",
            competition=competition.slug,
            provider="openai",
            confirmed_by="ktos@nie.ma",
        )
    assert not AiProviderAccount.objects.filter(dpa_confirmed_at__isnull=False).exists()


def test_participant_sees_only_the_newest_suggestion(compared, competition):
    from apps.ai_grading.models import AiStageVisibility
    from apps.results.models import ResultsPublication

    stage = compared.entry.stage
    AiStageVisibility.objects.create(stage=stage, show_to_participants=True)
    ResultsPublication.objects.create(stage=stage, published_by=CoordinatorFactory())

    items = services.participant_ai_feedback(compared.entry.participant, stage)

    assert [item["summary"] for item in items] == ["Zdanie OpenAI."]
    assert items[0]["provider_label"] == "OpenAI (GPT)"
    assert services.participant_ai_feedback(ParticipantFactory(), stage) == []
    assert SENTINEL_SUMMARY not in json.dumps(items, ensure_ascii=False, default=str)
