"""Uprawnienia, ekrany, audyt i zgłoszenia ze stopki (L10N-01 § 2, § 8, § 9)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.translation_review import services
from apps.translation_review.models import (
    GrantLevel,
    SuggestionStatus,
    TranslationOverride,
    TranslationReport,
    TranslationSuggestion,
    TranslationVote,
    TranslatorGrant,
)

from .conftest import grant_language, simple_row

pytestmark = pytest.mark.django_db


def detail_url(row, language="es"):
    return reverse("web:translation-string", args=[language, row.key])


# --- dostęp ---------------------------------------------------------------------------------------


def test_anonymous_is_sent_to_login(client):
    response = client.get(reverse("web:translation-list", args=["es"]))
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_translator_sees_only_own_language(client, translator):
    client.force_login(translator)
    assert client.get(reverse("web:translation-list", args=["es"])).status_code == 200
    assert client.get(reverse("web:translation-list", args=["fr"])).status_code == 403
    assert client.get(reverse("web:translation-list", args=["pl"])).status_code == 404
    assert client.get(reverse("web:translation-list", args=["xx"])).status_code == 404


def test_account_without_grant_gets_403(client):
    client.force_login(UserFactory())
    assert client.get(reverse("web:translations")).status_code == 403
    assert client.get(reverse("web:translation-report")).status_code == 403


def test_super_coordinator_reviews_every_language(super_coordinator):
    assert services.can_review(super_coordinator, "ar")
    assert services.can_review(super_coordinator, "zh-hans")
    assert not services.can_review(super_coordinator, "pl")


def test_list_filters_and_escapes(client, translator):
    row = simple_row()
    TranslationOverride.objects.create(
        language="es",
        key=row.key,
        msgid=row.msgid,
        text="<script>alert(1)</script>",
        base_text=row.translation,
    )  # wiersz wstawiony z pominięciem walidacji: ekran i tak ma go pokazać jako tekst
    client.force_login(translator)
    response = client.get(
        reverse("web:translation-list", args=["es"]), {"status": "reviewed", "q": row.msgid}
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body
    machine = client.get(reverse("web:translation-list", args=["es"]), {"status": "machine", "q": row.msgid})
    assert row.key not in machine.content.decode()


def test_detail_shows_source_pivot_and_context(client, translator):
    row = simple_row()
    client.force_login(translator)
    response = client.get(detail_url(row))
    assert response.status_code == 200
    body = response.content.decode()
    assert row.locations[0].split(":")[0] in body
    assert client.get(reverse("web:translation-string", args=["es", "f" * 64])).status_code == 404


# --- propozycje, głosy, decyzje -------------------------------------------------------------------


def test_translator_suggests_but_cannot_approve(client, translator):
    row = simple_row()
    client.force_login(translator)
    response = client.post(detail_url(row), {"action": "suggest", "text": "Propuesta nueva", "approve": "on"})
    # Zaznaczone „zatwierdź od razu” bez roli recenzenta – odmowa w serwisie, nic się nie zapisuje.
    assert response.status_code == 403
    assert not TranslationSuggestion.objects.exists()
    response = client.post(detail_url(row), {"action": "suggest", "text": "Propuesta nueva"})
    assert response.status_code == 302
    suggestion = TranslationSuggestion.objects.get()
    assert suggestion.author == translator
    response = client.post(detail_url(row), {"action": "approve", "suggestion": suggestion.pk})
    suggestion.refresh_from_db()
    assert suggestion.status == SuggestionStatus.PENDING
    assert not TranslationOverride.objects.exists()


def test_invalid_suggestion_is_rejected_with_message(client, translator):
    row = simple_row()
    client.force_login(translator)
    response = client.post(detail_url(row), {"action": "suggest", "text": '<img src=x onerror="alert(1)">'})
    assert response.status_code == 400
    assert not TranslationSuggestion.objects.exists()


def test_votes_and_approval_flow(client, translator, reviewer):
    row = simple_row()
    second = UserFactory()
    grant_language(second)
    suggestion = services.suggest(user=translator, language="es", key=row.key, text="Versión A")
    with pytest.raises(services.TranslationInvalid):
        services.vote(user=translator, suggestion=suggestion)  # własna propozycja
    assert services.vote(user=second, suggestion=suggestion) is True
    # Ta sama treść od innej osoby to głos, a nie druga pozycja w kolejce.
    same = services.suggest(user=reviewer, language="es", key=row.key, text="Versión A")
    assert same.pk == suggestion.pk
    assert TranslationVote.objects.filter(suggestion=suggestion).count() == 2
    other = services.suggest(user=second, language="es", key=row.key, text="Versión B")

    client.force_login(reviewer)
    response = client.post(detail_url(row), {"action": "approve", "suggestion": suggestion.pk})
    assert response.status_code == 302
    assert TranslationOverride.objects.get(language="es", key=row.key).text == "Versión A"
    other.refresh_from_db()
    assert other.status == SuggestionStatus.SUPERSEDED
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"translation.suggested", "translation.approved"} <= actions


def test_own_pending_suggestion_is_replaced_not_duplicated(translator):
    row = simple_row()
    services.suggest(user=translator, language="es", key=row.key, text="Uno")
    services.suggest(user=translator, language="es", key=row.key, text="Dos")
    assert list(TranslationSuggestion.objects.values_list("text", flat=True)) == ["Dos"]


def test_reviewer_of_other_language_cannot_decide(translator):
    row = simple_row()
    suggestion = services.suggest(user=translator, language="es", key=row.key, text="Hola")
    french = UserFactory()
    grant_language(french, "fr", GrantLevel.REVIEWER)
    with pytest.raises(services.TranslationDenied):
        services.approve(user=french, suggestion=suggestion)


def test_confirm_marks_catalog_text_as_reviewed(reviewer):
    row = simple_row()
    services.confirm(user=reviewer, language="es", key=row.key)
    assert TranslationOverride.objects.get(key=row.key).text == row.translation
    assert services.string_view("es", row.key).status == "reviewed"


# --- nadawanie roli -------------------------------------------------------------------------------


def test_coordinator_grants_translator_to_competition_member(client, coordinator, member):
    client.force_login(coordinator)
    url = reverse("web:coordinator-translators")
    assert client.get(url).status_code == 200
    response = client.post(url, {"email": member.email, "language": "es", "level": "translator"})
    assert response.status_code == 302
    assert TranslatorGrant.objects.get(user=member).level == GrantLevel.TRANSLATOR
    assert AuditLog.objects.filter(action="translation.grant").exists()


def test_coordinator_cannot_grant_reviewer_or_outsider(client, coordinator, member, multilingual):
    client.force_login(coordinator)
    url = reverse("web:coordinator-translators")
    response = client.post(url, {"email": member.email, "language": "es", "level": "reviewer"})
    assert response.status_code == 400  # poziomu nie ma w formularzu koordynatora
    with pytest.raises(services.TranslationDenied):
        services.grant(
            actor=coordinator,
            competition=multilingual,
            email=member.email,
            language="es",
            level=GrantLevel.REVIEWER,
        )
    outsider = UserFactory()
    response = client.post(url, {"email": outsider.email, "language": "es", "level": "translator"})
    assert response.status_code == 400
    assert not TranslatorGrant.objects.exists()


def test_coordinator_screen_is_404_in_single_language_competition(client, competition):
    from apps.accounts.models import CompetitionRole
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.tenancy.tests.factories import grant_membership

    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    client.force_login(user)
    assert client.get(reverse("web:coordinator-translators")).status_code == 404


def test_super_coordinator_grants_reviewer_to_anyone(super_coordinator, competition):
    anyone = UserFactory()
    grant = services.grant(
        actor=super_coordinator,
        competition=competition,
        email=anyone.email.upper(),
        language="ar",
        level="reviewer",
    )
    assert grant.level == GrantLevel.REVIEWER
    assert services.can_review(anyone, "ar")


def test_revoke(client, coordinator, member, multilingual):
    grant = grant_language(member, competition=multilingual)
    client.force_login(coordinator)
    response = client.post(reverse("web:coordinator-translators"), {"action": "revoke", "grant": grant.pk})
    assert response.status_code == 302
    assert not TranslatorGrant.objects.exists()


# --- zgłoszenie ze stopki ---------------------------------------------------------------------


def test_footer_link_for_translator_on_foreign_language_page(client, translator, multilingual):
    client.force_login(translator)
    client.cookies["django_language"] = "es"
    body = client.get(reverse("web:translations"), HTTP_ACCEPT_LANGUAGE="es").content.decode()
    assert reverse("web:translation-report") + "?page=%2Ftranslations%2F" in body


def test_footer_link_absent_for_others(client, multilingual):
    client.force_login(UserFactory())
    body = client.get("/me/", HTTP_ACCEPT_LANGUAGE="es", follow=True).content.decode()
    assert reverse("web:translation-report") not in body


def test_report_stores_only_the_path(client, translator):
    client.force_login(translator)
    url = reverse("web:translation-report")
    page = client.get(url, {"page": "/me/?token=secret#frag"})
    assert page.status_code == 200
    response = client.post(
        url,
        {
            "language": "es",
            "page": "/me/?token=secret",
            "phrase": "Entrar",
            "comment": "Debería ser «Acceder»",
        },
    )
    assert response.status_code == 302
    assert response["Location"] == "/me/"
    report = TranslationReport.objects.get()
    assert report.page == "/me/"
    assert report.reporter == translator
    log = AuditLog.objects.get(action="translation.reported")
    assert "Acceder" not in str(log.diff)


@pytest.mark.parametrize(
    "page", ["//evil.example/x", "https://evil.example/", "javascript:alert(1)", "me", "/\\evil.example/"]
)
def test_clean_page_refuses_foreign_targets(page):
    """Zostaje najwyżej ścieżka **w serwisie** – host, schemat i ``//`` odpadają."""
    result = services.clean_page(page)
    assert result == "" or (result.startswith("/") and not result.startswith("//") and "evil" not in result)


def test_reviewer_closes_report(client, reviewer, translator):
    report = services.report(user=translator, language="es", page="/", phrase="", comment="Mal")
    client.force_login(translator)
    assert client.get(reverse("web:translation-reports", args=["es"])).status_code == 403
    client.force_login(reviewer)
    assert client.get(reverse("web:translation-reports", args=["es"])).status_code == 200
    client.post(reverse("web:translation-reports", args=["es"]), {"report": report.pk})
    report.refresh_from_db()
    assert report.status == "closed"


# --- RODO -----------------------------------------------------------------------------------------


def test_export_and_erase(translator, reviewer):
    row = simple_row()
    suggestion = services.suggest(user=translator, language="es", key=row.key, text="Hola")
    services.vote(user=reviewer, suggestion=suggestion)
    services.report(user=translator, language="es", page="/", phrase="x", comment="Mal")
    section = services.export_section(translator)
    assert section["uprawnienia"] == [{"jezyk": "es", "poziom": "translator"}]
    assert section["propozycje"][0]["tlumaczenie"] == "Hola"
    assert section["zgloszenia"][0]["uwaga"] == "Mal"
    services.erase_for_user(translator)
    assert not TranslatorGrant.objects.filter(user=translator).exists()
    assert not TranslationReport.objects.exists()
    suggestion.refresh_from_db()
    assert suggestion.author is None


# --- poprawki po przeglądzie (M2, L3, L4, L5) ---------------------------------------------------


def test_coordinator_grant_belongs_to_competition_and_dies_with_the_link(
    client, coordinator, member, multilingual
):
    """M2: nadanie koordynatora ma konkurs; działa tylko, dopóki osoba jest z konkursem związana."""
    from apps.accounts.models import Membership, Participant

    grant = services.grant(
        actor=coordinator, competition=multilingual, email=member.email, language="es", level="translator"
    )
    assert grant.competition == multilingual
    member.refresh_from_db()
    assert services.can_translate(member, "es")
    # Osoba odchodzi z konkursu – rola przestaje działać bez żadnego sprzątania.
    Membership.objects.filter(user=member).delete()
    Participant.objects.filter(user=member).delete()
    services.forget_user(member)
    member.refresh_from_db()
    assert not services.can_translate(member, "es")
    # …ale koordynator (także inny niż nadający) nadal widzi nadanie i może je usunąć.
    from apps.accounts.models import CompetitionRole
    from apps.accounts.tests.factories import CoordinatorFactory
    from apps.tenancy.tests.factories import grant_membership

    other = CoordinatorFactory()
    grant_membership(other, multilingual, CompetitionRole.COORDINATOR)
    client.force_login(other)
    page = client.get(reverse("web:coordinator-translators")).content.decode()
    assert member.email in page
    client.post(reverse("web:coordinator-translators"), {"action": "revoke", "grant": grant.pk})
    assert not TranslatorGrant.objects.exists()


def test_coordinator_cannot_touch_platform_grants(client, coordinator, member, multilingual):
    grant = grant_language(member)  # nadanie superkoordynatora (bez konkursu)
    client.force_login(coordinator)
    response = client.post(reverse("web:coordinator-translators"), {"action": "revoke", "grant": grant.pk})
    assert response.status_code == 404
    assert TranslatorGrant.objects.exists()


def test_coordinator_grants_only_competition_languages(coordinator, member, multilingual):
    """L4: konkurs ma pl, en, es – hindi nie jest jego językiem interfejsu."""
    with pytest.raises(services.TranslationDenied):
        services.grant(
            actor=coordinator, competition=multilingual, email=member.email, language="hi", level="translator"
        )
    assert services.grantable_languages(coordinator, multilingual) == ["en", "es"]


def test_arabic_cells_are_rtl_and_source_ltr(client):
    """L3: kierunek z ``get_language_info``, źródło i angielski zawsze od lewej."""
    user = UserFactory()
    grant_language(user, "ar")
    client.force_login(user)
    body = client.get(reverse("web:translation-list", args=["ar"])).content.decode()
    assert 'lang="ar" dir="rtl"' in body
    assert 'lang="pl" dir="ltr"' in body
    assert 'lang="en" dir="ltr"' in body


def test_double_click_vote_is_idempotent(client, translator):
    """L5: dwa takie same POST-y „Popieram” – jeden głos, bez 500 i bez cofnięcia głosu."""
    row = simple_row()
    author = UserFactory()
    grant_language(author)
    suggestion = services.suggest(user=author, language="es", key=row.key, text="Otra versión")
    client.force_login(translator)
    for _attempt in range(2):
        response = client.post(
            detail_url(row), {"action": "vote", "support": "1", "suggestion": suggestion.pk}
        )
        assert response.status_code == 302
    assert TranslationVote.objects.filter(suggestion=suggestion).count() == 1


def test_vote_race_on_unique_constraint_is_swallowed(translator):
    row = simple_row()
    author = UserFactory()
    grant_language(author)
    suggestion = services.suggest(user=author, language="es", key=row.key, text="Otra versión")
    TranslationVote.objects.create(suggestion=suggestion, user=translator)
    services._add_vote(suggestion, translator)  # drugi INSERT trafia na więz – bez wyjątku
    assert TranslationVote.objects.filter(suggestion=suggestion).count() == 1


def test_stale_override_is_flagged_on_the_list(client, translator):
    row = simple_row()
    TranslationOverride.objects.create(
        language="es",
        key=row.key,
        msgid=row.msgid,
        text="Vieja decisión",
        base_text="otro texto del catálogo",
    )
    client.force_login(translator)
    body = client.get(reverse("web:translation-list", args=["es"]), {"q": row.msgid}).content.decode()
    assert "badge--danger" in body
    assert services.string_view("es", row.key).current == row.translation
