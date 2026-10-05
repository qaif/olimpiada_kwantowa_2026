"""Bramka zgód (CONS-01 § 1–2): kto, kiedy i dokąd jest odsyłany – i ile to kosztuje.

Testy idą przez **żądania** pod hostem konkursu tam, gdzie liczy się zachowanie (przekierowanie,
lista dozwolona, API, HTMX, prefiks ścieżki), i przez warstwę wprost tam, gdzie liczy się koszt
(budżet zapytań personelu, anonima i uczestnika).
"""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import URLResolver, get_resolver, resolve
from rest_framework.authtoken.models import Token

from apps.accounts import consents
from apps.accounts.consents import CONSENT_FEATURE_FLAG, ConsentKind
from apps.accounts.guardian import confirm_consent
from apps.accounts.models import CompetitionRole, ConsentDefinition, ConsentRecord
from apps.accounts.services import grant_role
from apps.accounts.tests.factories import UserFactory
from apps.consent_gate import middleware, state
from apps.tenancy.models import RoutingMode
from conftest import make_competition

from .conftest import give, make_adult, make_minor, seen

pytestmark = pytest.mark.django_db

SCREEN = "/me/consents/complete/"


def screen_for(path: str) -> str:
    return f"{SCREEN}?next={path.replace('/', '%2F').replace('?', '%3F').replace('=', '%3D')}"


def enable_definitions(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), CONSENT_FEATURE_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    if not ConsentDefinition.objects.filter(competition=competition).exists():
        consents.definitions_from_defaults(competition)
    return competition


# --- każdy rodzaj zgody ---------------------------------------------------------------------------


def test_participant_with_every_required_consent_reaches_the_panel(web, adult):
    give(adult)
    web.force_login(adult.user)

    assert web.get("/me/").status_code == 200


@pytest.mark.parametrize("kind", [ConsentKind.TERMS, ConsentKind.PRIVACY])
def test_missing_always_required_consent_sends_to_the_screen(web, adult, kind):
    give(adult, {ConsentKind.TERMS, ConsentKind.PRIVACY} - {kind})
    web.force_login(adult.user)

    response = web.get("/me/")

    assert response.status_code == 302
    assert response["Location"] == screen_for("/me/")


def test_minor_without_the_guardian_statement_is_sent_to_the_screen(web, minor):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)

    assert web.get("/me/")["Location"] == screen_for("/me/")


def test_adult_does_not_need_the_guardian_consent(web, adult):
    give(adult, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(adult.user)

    assert web.get("/me/").status_code == 200


def test_optional_publish_name_consent_never_blocks(web, adult):
    give(adult)
    assert not ConsentRecord.objects.filter(participant=adult, kind=ConsentKind.PUBLISH_NAME).exists()
    web.force_login(adult.user)

    assert web.get("/me/").status_code == 200


def test_withdrawn_consent_does_not_count(web, adult):
    from django.utils import timezone

    give(adult)
    ConsentRecord.objects.filter(participant=adult, kind=ConsentKind.TERMS).update(
        withdrawn_at=timezone.now()
    )
    web.force_login(adult.user)

    assert web.get("/me/").status_code == 302


def test_minor_with_statement_but_without_online_guardian_confirmation_keeps_access(web, minor):
    """Dziś potwierdzenie online nie blokuje panelu (wymaga go tylko nadzór zdalny) – i tak zostaje."""
    give(minor)
    web.force_login(minor.user)

    assert web.get("/me/").status_code == 200


def test_online_guardian_confirmation_satisfies_the_guardian_consent(web, minor):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    minor.guardian_email = "rodzic@example.test"
    minor.save(update_fields=["guardian_email"])
    web.force_login(minor.user)
    assert web.get("/me/").status_code == 302

    confirm_consent(minor)

    assert web.get("/me/").status_code == 200


# --- wersja dokumentu -----------------------------------------------------------------------------


def test_consent_given_under_an_older_version_of_the_constant_forces_re_consent(web, adult):
    """Konkurs bez flagi: wersja ze stałej (``TERMS_VERSION``) – wpis pod inną wersją nie wystarcza."""
    give(adult, {ConsentKind.PRIVACY})
    give(adult, {ConsentKind.TERMS}, version="1.0 z 1 września 2026")
    web.force_login(adult.user)

    assert web.get("/me/")["Location"] == screen_for("/me/")


def test_version_bump_on_the_consents_screen_forces_re_consent_from_the_next_request(web, competition, adult):
    enable_definitions(competition)
    give(adult)
    web.force_login(adult.user)
    assert web.get("/me/").status_code == 200  # stan w cache'u

    definition = ConsentDefinition.objects.get(competition=competition, kind=ConsentKind.PRIVACY)
    consents.change_version(definition, "2.0 z 1 listopada 2026")

    assert web.get("/me/")["Location"] == screen_for("/me/")
    response = web.post(SCREEN, seen(competition, gdpr_consent="on", next="/me/"))
    assert response["Location"] == "/me/"
    assert ConsentRecord.objects.filter(
        participant=adult, kind=ConsentKind.PRIVACY, document_version="2.0 z 1 listopada 2026"
    ).exists()
    assert web.get("/me/").status_code == 200


def test_new_required_definition_gates_participants(web, competition, adult):
    enable_definitions(competition)
    give(adult)
    web.force_login(adult.user)
    assert web.get("/me/").status_code == 200

    ConsentDefinition.objects.filter(competition=competition, kind=ConsentKind.PUBLISH_NAME).update(
        required=True
    )
    # ``update`` nie wysyła sygnału – zestaw konkursu odświeży TTL; tu unieważniamy jak sygnał.
    state.forget_definitions(competition.pk)

    assert web.get("/me/").status_code == 302


def test_guardian_confirmed_online_after_a_version_bump_carries_the_competition_version(
    web, competition, minor
):
    """Poprawka przy okazji: potwierdzenie opiekuna bierze wersję z zestawu konkursu, nie ze stałej."""
    enable_definitions(competition)
    definition = ConsentDefinition.objects.get(competition=competition, kind=ConsentKind.GUARDIAN)
    consents.change_version(definition, "1.0 z 1 listopada 2026")
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    minor.guardian_email = "rodzic@example.test"
    minor.save(update_fields=["guardian_email"])

    record = confirm_consent(minor)

    assert record.document_version == "1.0 z 1 listopada 2026"
    web.force_login(minor.user)
    assert web.get("/me/").status_code == 200


def test_changing_the_birth_date_to_a_minor_requires_the_guardian_consent(web, adult):
    from datetime import date

    from django.utils import timezone

    give(adult)
    web.force_login(adult.user)
    assert web.get("/me/").status_code == 200

    adult.birth_date = date(timezone.localdate().year - 15, 1, 1)
    adult.save(update_fields=["birth_date"])

    assert web.get("/me/").status_code == 302


# --- obszar i lista dozwolona ---------------------------------------------------------------------


BLOCKED = [
    ("get", "/me/"),
    ("get", "/me/messages/"),
    ("get", "/me/calendar/"),
    ("get", "/me/certificates/"),
    ("get", "/forum/"),
    ("post", "/me/stages/1/problems/1/upload/"),
    ("get", "/me/stages/1/test/"),
]


@pytest.mark.parametrize(("method", "path"), BLOCKED)
def test_participant_area_is_closed(web, adult, method, path):
    web.force_login(adult.user)

    response = getattr(web, method)(path)

    assert response.status_code == 302
    assert response["Location"].startswith(SCREEN)


OPEN = [
    "/account/export/",
    "/account/delete/",
    "/account/password/",
    "/account/profile/",
    "/me/profile/",
    "/support/new/",
    SCREEN,
]


@pytest.mark.parametrize("path", OPEN)
def test_gdpr_rights_password_and_profile_stay_reachable(web, adult, path):
    web.force_login(adult.user)

    response = web.get(path)

    assert not response.get("Location", "").startswith(SCREEN), path
    assert response.status_code in (200, 302), path


def test_logout_and_preferences_stay_reachable(web, adult):
    web.force_login(adult.user)

    preferences = web.post("/account/preferences/", {"language": "", "high_contrast": "1", "next": "/"})
    logout = web.post("/logout/")

    assert not preferences["Location"].startswith(SCREEN)
    assert not logout["Location"].startswith(SCREEN)
    assert "_auth_user_id" not in web.session


def test_quiz_autosave_is_never_stopped_by_the_gate(web, adult):
    """Odpowiedzi testu nie mogą zginąć przez zmianę wersji dokumentu w trakcie podejścia."""
    web.force_login(adult.user)

    response = web.post("/me/test/999999/zapis/", {}, HTTP_ACCEPT="application/json")

    assert response.status_code != 302
    assert b"CONSENTS_REQUIRED" not in response.content


def test_public_pages_stay_reachable(web, adult):
    web.force_login(adult.user)

    assert web.get("/").status_code == 200


def test_every_allowed_view_and_gated_segment_exists():
    """Kontrakt listy: zmiana nazwy adresu bez poprawki w bramce ma wywrócić test, a nie bramkę."""
    names: set[str] = set()
    segments: set[str] = set()

    def walk(patterns, prefix="", namespace=None):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern), pattern.namespace or namespace)
            else:
                names.add(f"{namespace}:{pattern.name}" if namespace else str(pattern.name))
                segments.add((prefix + str(pattern.pattern)).lstrip("^").split("/", 1)[0])

    walk(get_resolver().url_patterns)

    assert middleware.ALLOWED_VIEWS <= names, middleware.ALLOWED_VIEWS - names
    assert middleware.GATED_SEGMENTS <= segments, middleware.GATED_SEGMENTS - segments


def test_gate_stands_after_two_factor_and_before_the_proctoring_gate():
    chain = django_settings.MIDDLEWARE
    ours = chain.index("apps.consent_gate.middleware.ConsentGateMiddleware")

    assert chain.index("apps.accounts.twofactor.TwoFactorMiddleware") < ours
    assert chain.index("apps.time_windows.middleware.ParticipantTimezoneMiddleware") < ours
    assert chain.index("django.contrib.messages.middleware.MessageMiddleware") < ours
    assert ours < chain.index("apps.proctoring.middleware.ProctoringGateMiddleware")


def test_switch_turns_the_gate_off(web, adult, settings):
    settings.CONSENT_GATE_ENABLED = False
    web.force_login(adult.user)

    assert web.get("/me/").status_code == 200


# --- klienci: HTMX, API, prefiks ścieżki ---------------------------------------------------------


def test_htmx_request_gets_a_full_page_redirect_header(web, adult):
    web.force_login(adult.user)

    response = web.get("/me/", HTTP_HX_REQUEST="true")

    assert response.status_code == 403
    # Bez ``HX-Current-URL`` nie wiadomo, na jakiej stronie stoi przeglądarka – ekran bez ``next``.
    assert response["HX-Redirect"] == SCREEN


def test_api_with_session_gets_a_json_refusal(web, adult):
    web.force_login(adult.user)

    response = web.get("/api/competitions/me/entries/")

    assert response.status_code == 403
    assert response.json()["code"] == "CONSENTS_REQUIRED"


def test_api_with_token_is_checked_for_the_token_owner(client_for, competition, adult):
    token = Token.objects.create(user=adult.user)
    api = client_for(competition)

    refused = api.get("/api/competitions/me/entries/", HTTP_AUTHORIZATION=f"Token {token.key}")
    give(adult)
    allowed = api.get("/api/competitions/me/entries/", HTTP_AUTHORIZATION=f"Token {token.key}")

    assert refused.status_code == 403
    assert refused.json()["code"] == "CONSENTS_REQUIRED"
    assert allowed.status_code == 200


def test_api_outside_the_gate_keeps_working(web, adult):
    web.force_login(adult.user)

    assert web.get("/api/auth/me/").status_code == 200


def test_path_prefix_competition_keeps_its_prefix(client_for, competition):
    competition.feature_flags = {**(competition.feature_flags or {}), "path_prefix_routing": True}
    competition.save(update_fields=["feature_flags"])
    second = make_competition("druga.invalid", "druga", routing_mode=RoutingMode.PATH, path_prefix="druga")
    participant = make_adult(competition=second)
    web = client_for(competition)
    web.force_login(participant.user)

    response = web.get("/druga/me/")

    assert response["Location"] == f"/druga{SCREEN}?next=%2Fdruga%2Fme%2F"
    screen = web.get(response["Location"])
    assert screen.status_code == 200
    assert 'action="/druga/me/consents/complete/"' in screen.content.decode()


def test_competitions_are_isolated(client_for, competition, other_competition):
    """Komplet zgód w konkursie A nie otwiera panelu konkursu B – zgoda należy do administratora danych."""
    here = make_adult(competition=competition)
    give(here)
    there = make_adult(competition=other_competition, user=here.user)
    web_b = client_for(other_competition)
    web_b.force_login(there.user)

    assert web_b.get("/me/").status_code == 302


# --- personel ------------------------------------------------------------------------------------


def test_coordinator_screens_are_untouched(web, competition):
    coordinator = UserFactory()
    grant_role(coordinator, CompetitionRole.COORDINATOR, competition=competition)
    web.force_login(coordinator)

    assert web.get("/coordinator/").status_code == 200


# --- koszt ----------------------------------------------------------------------------------------


def _request(path, user, competition):
    request = RequestFactory().get(path, HTTP_HOST=competition.primary_domain)
    request.user = user
    request.competition = competition
    request.resolver_match = resolve(path)
    request.session = {}
    return request


def _gate():
    return middleware.ConsentGateMiddleware(lambda request: None)


def test_staff_and_anonymous_cost_zero_queries(competition, django_assert_num_queries):
    staff = UserFactory(is_staff=True)
    superuser = UserFactory(is_superuser=True)
    gate = _gate()

    with django_assert_num_queries(0):
        for user in (AnonymousUser(), staff, superuser):
            request = _request("/me/", user, competition)
            assert gate.process_view(request, None, (), {}) is None
        # Ekran personelu – poza obszarem bramki, bez pytania o cokolwiek.
        coordinator = _request("/coordinator/", UserFactory.build(pk=10**6), competition)
        assert gate.process_view(coordinator, None, (), {}) is None


def test_participant_costs_one_query_cold_and_zero_warm(competition, adult, django_assert_num_queries):
    give(adult)
    gate = _gate()

    with django_assert_num_queries(1):
        assert gate.process_view(_request("/me/", adult.user, competition), None, (), {}) is None
    with django_assert_num_queries(0):
        assert gate.process_view(_request("/me/", adult.user, competition), None, (), {}) is None


def test_coordinator_on_a_participant_screen_is_remembered_as_no_profile(
    competition, django_assert_num_queries
):
    coordinator = UserFactory()
    gate = _gate()

    with django_assert_num_queries(1):
        assert gate.process_view(_request("/forum/", coordinator, competition), None, (), {}) is None
    with django_assert_num_queries(0):
        assert gate.process_view(_request("/forum/", coordinator, competition), None, (), {}) is None


def test_competition_definitions_are_cached_per_competition(competition, adult, django_assert_num_queries):
    enable_definitions(competition)
    give(adult)
    gate = _gate()
    gate.process_view(_request("/me/", adult.user, competition), None, (), {})
    state.forget_state(competition.pk, adult.user_id)

    # Zestaw zgód konkursu jest w cache'u – chybienie stanu konta to dalej jedno zapytanie.
    with django_assert_num_queries(1):
        assert gate.process_view(_request("/me/", adult.user, competition), None, (), {}) is None


def test_new_consent_record_invalidates_the_cached_state(web, minor):
    give(minor, {ConsentKind.TERMS, ConsentKind.PRIVACY})
    web.force_login(minor.user)
    assert web.get("/me/").status_code == 302

    give(minor, {ConsentKind.GUARDIAN})

    assert web.get("/me/").status_code == 200


def test_deleted_consent_record_invalidates_the_cached_state(web, adult):
    give(adult)
    web.force_login(adult.user)
    assert web.get("/me/").status_code == 200

    ConsentRecord.objects.filter(participant=adult, kind=ConsentKind.TERMS).delete()

    assert web.get("/me/").status_code == 302


def test_participant_without_a_profile_in_this_competition_is_not_gated(web, competition, other_competition):
    elsewhere = make_minor(competition=other_competition)
    web.force_login(elsewhere.user)

    # Konto bez profilu tutaj – bramka nie ma czego żądać; widok sam odpowie po swojemu (403).
    assert web.get("/me/").status_code == 403
