"""Czynności na medalach (``apps.medals.services``): podgląd, ręczne zmiany, ogłoszenie, odmrożenie,
dokumenty i eksport – na prawdziwym rankingu ``apps.results`` (MED-01 § 1–4)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.http import Http404

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import Stage, StageEntryStatus, StageKind
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import FinalGrade
from apps.medals import services
from apps.medals.models import Award, CertificateLanguage, MedalOverride, MedalScheme
from apps.results.models import Anonymization, Certificate, CertificateKind
from apps.results.tests.conftest import make_stage

from .conftest import contestant, final_stage, publish

pytestmark = pytest.mark.django_db


@pytest.fixture
def field(iqo):
    """Finał z dziesięciorgiem uczestników z trzech krajów: sumy 12, 11, 10 … 3 (dwa zadania po 6)."""
    stage = final_stage(iqo)
    scores = [(6, 6), (6, 5), (5, 5), (5, 4), (4, 4), (4, 3), (3, 3), (3, 2), (2, 2), (2, 1)]
    codes = ["de", "de", "pl", "pl", "fr", "fr", "de", "pl", "fr", "fr"]
    entries = [contestant(stage, pair, code=code) for pair, code in zip(scores, codes, strict=True)]
    return stage, entries


def scheme(stage, **values):
    row = services.scheme_for(stage, create=True)
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    return row


def test_reading_the_screen_does_not_create_a_scheme(field):
    stage, _entries = field

    preview = services.preview(services.scheme_for(stage))

    assert not MedalScheme.objects.filter(stage=stage).exists()
    assert preview.thresholds.field_size == 10


def test_preview_uses_the_live_ranking_with_ipho_defaults(field):
    stage, entries = field

    result = services.preview(services.scheme_for(stage))

    # Pole 10: złoto ceil(0,8) = 1, srebro łącznie ceil(2,5) = 3, brąz łącznie ceil(5) = 5.
    assert result.computed[entries[0].pk] == Award.GOLD
    assert [result.computed[entry.pk] for entry in entries[1:3]] == [Award.SILVER, Award.SILVER]
    assert [result.computed[entry.pk] for entry in entries[3:5]] == [Award.BRONZE, Award.BRONZE]
    # Wyróżnienie: ≥ 50 % z 12 = 6 punktów, albo pełne zadanie (6 z 6) – tu nikt poniżej brązu.
    assert result.computed[entries[5].pk] == Award.HONOURABLE  # 7 ≥ 6
    assert result.computed[entries[6].pk] == Award.HONOURABLE  # 6 ≥ 6
    assert result.computed[entries[7].pk] == Award.NONE


def test_update_scheme_audits_and_refuses_over_one_hundred_percent(field, coordinator):
    stage, _entries = field
    row = services.scheme_for(stage, create=True)
    values = {
        "gold_percent": Decimal(10),
        "silver_percent": Decimal(20),
        "bronze_percent": Decimal(30),
        "tie_policy": "EXCLUSIVE",
        "hm_percent_of_best": None,
        "hm_full_solution": False,
    }

    services.update_scheme(row, values=values, actor=coordinator)

    row.refresh_from_db()
    assert row.gold_percent == Decimal(10) and row.hm_percent_of_best is None
    entry = AuditLog.objects.get(action="medals.scheme_updated")
    assert entry.diff["after"]["gold_percent"] == 10
    with pytest.raises(DomainError) as error:
        services.update_scheme(row, values=values | {"bronze_percent": Decimal(80)}, actor=coordinator)
    assert error.value.machine_code == "PERCENT_OVER_100"


def test_only_a_coordinator_of_this_competition_may_change_anything(field):
    stage, entries = field
    outsider = ParticipantFactory().user

    with pytest.raises(DomainError) as error:
        services.set_override(
            services.scheme_for(stage, create=True),
            entry_id=entries[0].pk,
            award=Award.NONE,
            justification="x",
            actor=outsider,
        )
    assert error.value.status_code == 403


def test_override_requires_justification_and_is_audited_without_it(field, coordinator):
    stage, entries = field
    row = services.scheme_for(stage, create=True)

    with pytest.raises(DomainError) as error:
        services.set_override(
            row, entry_id=entries[7].pk, award=Award.BRONZE, justification="  ", actor=coordinator
        )
    assert error.value.machine_code == "JUSTIFICATION_REQUIRED"

    services.set_override(
        row,
        entry_id=entries[7].pk,
        award=Award.BRONZE,
        justification="Błąd w protokole zadania 2.",
        actor=coordinator,
    )

    result = services.preview(row)
    assert result.computed[entries[7].pk] == Award.NONE
    assert result.final[entries[7].pk] == Award.BRONZE
    log = AuditLog.objects.get(action="medals.override_set")
    assert log.diff == {"stage_id": stage.pk, "entry_id": entries[7].pk, "award": "BRONZE", "created": True}
    assert "protokole" not in str(log.diff)


def test_override_of_an_entry_from_another_stage_is_404(field, coordinator, iqo):
    stage, _entries = field
    other = make_stage(kind=StageKind.ELIM, edition=current_edition(iqo))
    stranger = contestant(other, (1, 1))

    with pytest.raises(Http404):
        services.set_override(
            services.scheme_for(stage, create=True),
            entry_id=stranger.pk,
            award=Award.GOLD,
            justification="x",
            actor=coordinator,
        )


def test_freeze_requires_published_results(field, coordinator):
    stage, _entries = field

    with pytest.raises(DomainError) as error:
        services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    assert error.value.machine_code == "RESULTS_NOT_PUBLISHED"


def test_freeze_refuses_when_totals_changed_after_publication(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    FinalGrade.objects.filter(submission__entry=entries[9]).update(score=6)

    with pytest.raises(DomainError) as error:
        services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    assert error.value.machine_code == "RESULTS_CHANGED"


def test_freeze_snapshots_awards_public_table_and_country_table(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    row = services.scheme_for(stage, create=True)
    services.set_override(
        row, entry_id=entries[7].pk, award=Award.HONOURABLE, justification="x", actor=coordinator
    )

    services.freeze(row, actor=coordinator)

    row.refresh_from_db()
    assert row.is_frozen
    assert row.awards[str(entries[0].pk)]["award"] == Award.GOLD
    assert row.awards[str(entries[7].pk)] | {} == {
        "award": "HM",
        "computed": "NONE",
        "overridden": True,
        "total": 5,
        "rank": 8,
    }
    assert row.thresholds["final_counts"]["HM"] == 3
    # Publiczna tabela: tryb CODE – kod i kraj przy każdym wierszu, bez nazwisk.
    first = row.public_rows[0]
    assert first["display"] == entries[0].participant.public_code
    assert first["country"] == "Germany" and first["award"] == "GOLD"
    assert all(entries[0].participant.user.last_name not in str(item) for item in row.public_rows)
    assert next(item for item in row.public_rows if item["manual"])["award"] == "HM"
    germany = next(item for item in row.country_table if item["code"] == "de")
    assert (germany["contestants"], germany["GOLD"], germany["SILVER"], germany["HM"]) == (3, 1, 1, 1)
    assert AuditLog.objects.filter(action="medals.frozen").exists()


def test_frozen_awards_cannot_be_changed_until_unfrozen(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    with pytest.raises(DomainError) as error:
        services.set_override(
            row, entry_id=entries[1].pk, award=Award.GOLD, justification="x", actor=coordinator
        )
    assert error.value.machine_code == "AWARDS_FROZEN"
    with pytest.raises(DomainError):
        services.unfreeze(row, justification="", actor=coordinator)

    services.unfreeze(row, justification="Korekta po odwołaniu.", actor=coordinator)

    row.refresh_from_db()
    assert not row.is_frozen and row.public_rows == [] and row.awards == {}
    assert AuditLog.objects.get(action="medals.unfrozen").diff["reason"] == "Korekta po odwołaniu."
    services.set_override(row, entry_id=entries[1].pk, award=Award.GOLD, justification="x", actor=coordinator)


def test_named_publication_shows_country_only_next_to_consented_names(field, coordinator):
    stage, entries = field
    consenting = entries[0].participant
    consenting.publish_full_name = True
    consenting.guardian_consent = True
    consenting.save(update_fields=["publish_full_name", "guardian_consent"])
    publish(stage, coordinator, Anonymization.FULL_ALL)

    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    named = row.public_rows[0]
    assert named["display"] == consenting.user.get_full_name()
    assert named["country"] == "Germany"
    coded = row.public_rows[1]
    assert coded["display"] == entries[1].participant.public_code
    assert coded["country"] == "" and coded["country_code"] == ""


def test_disqualified_entry_gets_nothing_and_is_not_in_the_public_table(iqo, coordinator):
    stage = final_stage(iqo)
    best = contestant(stage, (6, 6), status=StageEntryStatus.DISQUALIFIED)
    others = [contestant(stage, (5, 5)), contestant(stage, (2, 2))]
    publish(stage, coordinator)
    # Publikacja przestawia statusy kwalifikacji, ale zdyskwalifikowanego nie rusza.
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    assert str(best.pk) not in row.awards
    assert row.awards[str(others[0].pk)]["award"] == Award.GOLD
    assert len(row.public_rows) == 2


def test_issue_certificates_freezes_the_students_language(field, coordinator, iqo, monkeypatch):
    from apps.accounts.models import UserPreference
    from apps.medals import typesetting

    # Przypięcie języka nie składa dokumentu – potrzebna jest tylko odpowiedź „pismo dostępne”.
    monkeypatch.setattr(typesetting, "shaping_available", lambda: True)
    stage, entries = field
    iqo.interface_languages = ["en", "ar", "hi", "pl"]
    iqo.default_language = "en"
    iqo.save(update_fields=["interface_languages", "default_language"])
    UserPreference.objects.create(user=entries[0].participant.user, language="ar")
    # Etap wczytany na nowo – z konkursem już po zmianie języków (fikstura trzyma stary obiekt).
    stage = Stage.objects.select_related("edition__competition").get(pk=stage.pk)
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    report = services.issue_certificates(row, participation=True, actor=coordinator)

    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)
    assert CertificateLanguage.objects.get(certificate=gold).language == "ar"
    silver = Certificate.objects.get(entry=entries[1], kind=CertificateKind.MEDAL_SILVER)
    assert CertificateLanguage.objects.get(certificate=silver).language == "en"
    assert report.medals == 7  # złoto 1 + srebro 2 + brąz 2 + wyróżnienia 2
    assert report.participation == 10
    # Zmiana języka konta po wystawieniu nie zmienia dokumentu.
    UserPreference.objects.filter(user=entries[0].participant.user).update(language="hi")
    from apps.medals.documents import certificate_language

    assert certificate_language(gold) == "ar"
    # Drugi przebieg – te same numery, nic nowego.
    again = services.issue_certificates(row, participation=True, actor=coordinator)
    assert again.created == 0


def test_certificates_issued_before_a_change_are_reported_as_stale(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    services.issue_certificates(row, participation=False, actor=coordinator)
    services.unfreeze(row, justification="Zmiana", actor=coordinator)
    services.set_override(
        row, entry_id=entries[0].pk, award=Award.SILVER, justification="x", actor=coordinator
    )
    row = services.freeze(row, actor=coordinator)

    report = services.issue_certificates(row, participation=False, actor=coordinator)

    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)
    assert report.stale == [gold.number]
    assert Certificate.objects.filter(entry=entries[0], kind=CertificateKind.MEDAL_SILVER).exists()


def test_ceremony_rows_read_names_now_and_the_award_from_the_freeze(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    rows = services.ceremony_rows(row)

    assert rows[0]["entry_id"] == entries[0].pk
    assert rows[0]["name"] == entries[0].participant.user.get_full_name()
    assert rows[0]["country"] == "Germany"
    assert rows[0]["award"] == Award.GOLD


def test_override_cascades_with_the_entry(field, coordinator):
    stage, entries = field
    row = services.scheme_for(stage, create=True)
    services.set_override(
        row, entry_id=entries[9].pk, award=Award.BRONZE, justification="x", actor=coordinator
    )
    entries[9].submissions.all().delete()

    entries[9].delete()

    assert not MedalOverride.objects.exists()


# --- M1: kraj przy wierszu w każdym trybie anonimizacji ----------------------------------------------


@pytest.mark.parametrize(
    ("mode", "countries_shown"),
    [
        (Anonymization.CODE, "all"),
        (Anonymization.INITIALS_SCHOOL, "none"),
        (Anonymization.FULL, "consenting"),
        (Anonymization.FULL_ALL, "consenting"),
    ],
)
def test_country_column_follows_the_publication_mode_and_consent(field, coordinator, mode, countries_shown):
    stage, entries = field
    consenting = entries[0].participant
    consenting.publish_full_name = True
    consenting.guardian_consent = True
    consenting.save(update_fields=["publish_full_name", "guardian_consent"])
    publish(stage, coordinator, mode)

    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    shown = [bool(item["country"]) for item in row.public_rows]
    if countries_shown == "all":
        assert all(shown)
    elif countries_shown == "none":
        # „Inicjały i szkoła” zmieniają podpis, ale nie są zgodą – kraj przy nich zawęża do osoby.
        assert not any(shown)
        assert row.public_rows[0]["display"] != consenting.public_code
    else:
        assert shown[0] and not any(shown[1:])
        assert row.public_rows[0]["display"] == consenting.user.get_full_name()


# --- M4: zmiana stanów po publikacji ------------------------------------------------------------------


def test_disqualification_after_publication_blocks_the_freeze(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    entries[3].status = StageEntryStatus.DISQUALIFIED
    entries[3].save(update_fields=["status"])

    with pytest.raises(DomainError) as error:
        services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    assert error.value.machine_code == "RESULTS_CHANGED"
    assert "stany" in str(error.value.detail)


def test_republishing_after_a_disqualification_unblocks_the_freeze(field, coordinator):
    stage, entries = field
    publish(stage, coordinator)
    entries[3].status = StageEntryStatus.DISQUALIFIED
    entries[3].save(update_fields=["status"])
    publish(stage, coordinator)

    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    assert str(entries[3].pk) not in row.awards


def test_entry_added_after_publication_blocks_the_freeze(field, coordinator, iqo):
    stage, _entries = field
    publish(stage, coordinator)
    contestant(stage, (1, 1))

    with pytest.raises(DomainError) as error:
        services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    assert error.value.machine_code == "RESULTS_CHANGED"


# --- L2: zdyskwalifikowany bez ręcznej nagrody -------------------------------------------------------


def test_override_of_a_disqualified_entry_is_refused(iqo, coordinator):
    stage = final_stage(iqo)
    banned = contestant(stage, (6, 6), status=StageEntryStatus.DISQUALIFIED)

    with pytest.raises(DomainError) as error:
        services.set_override(
            services.scheme_for(stage, create=True),
            entry_id=banned.pk,
            award=Award.GOLD,
            justification="x",
            actor=coordinator,
        )
    assert error.value.machine_code == "ENTRY_DISQUALIFIED"
    assert not MedalOverride.objects.exists()


# --- M3: ranking krajów przy publikacji „tylko awansujący” ------------------------------------------


def test_country_table_with_qualified_only_publication_sums_awarded_results_only(field, coordinator):
    stage, entries = field
    publish(stage, coordinator, qualified_only=True)

    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    france = next(item for item in row.country_table if item["code"] == "fr")
    # Francja: 8 (brąz), 7 (wyróżnienie), 4 i 3 (bez nagrody) – do sumy idą tylko dwa nagrodzone,
    # a dwa wyniki to za mało, żeby sumę w ogóle pokazać.
    assert france["contestants"] == 4
    assert france["total"] is None and france["rank"] is None
    assert france["BRONZE"] == 1 and france["HM"] == 1


# --- M5: dyplom nieaktualny po zmianie nagrody ------------------------------------------------------


def test_medal_certificate_stops_being_current_after_a_change(field, coordinator):
    from apps.results.certificates import certificate_is_current, verify

    stage, entries = field
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)
    services.issue_certificates(row, participation=True, actor=coordinator)
    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)
    participation = Certificate.objects.get(entry=entries[0], kind=CertificateKind.UCZESTNIK)
    assert certificate_is_current(gold) and verify(gold.code)["current"] is True

    services.unfreeze(row, justification="Korekta", actor=coordinator)
    assert verify(gold.code)["current"] is False  # w trakcie korekty

    services.set_override(
        row, entry_id=entries[0].pk, award=Award.SILVER, justification="x", actor=coordinator
    )
    services.freeze(row, actor=coordinator)
    assert verify(gold.code)["current"] is False  # ogłoszona nagroda jest inna
    # Zaświadczenie o udziale nie zależy od nagrody.
    assert certificate_is_current(participation) is True
    services.unfreeze(row, justification="Powrót", actor=coordinator)
    services.remove_override(row, entry_id=entries[0].pk, actor=coordinator)
    services.freeze(row, actor=coordinator)
    assert verify(gold.code)["current"] is True


def test_issue_reports_language_fallbacks(field, coordinator, iqo, monkeypatch):
    from apps.accounts.models import UserPreference
    from apps.medals import typesetting

    monkeypatch.setattr(typesetting, "shaping_available", lambda: False)
    stage, entries = field
    iqo.interface_languages = ["en", "hi"]
    iqo.default_language = "en"
    iqo.save(update_fields=["interface_languages", "default_language"])
    UserPreference.objects.create(user=entries[0].participant.user, language="hi")
    stage = Stage.objects.select_related("edition__competition").get(pk=stage.pk)
    publish(stage, coordinator)
    row = services.freeze(services.scheme_for(stage, create=True), actor=coordinator)

    report = services.issue_certificates(row, participation=False, actor=coordinator)

    gold = Certificate.objects.get(entry=entries[0], kind=CertificateKind.MEDAL_GOLD)
    assert report.fallbacks == [gold.number]
    assert CertificateLanguage.objects.get(certificate=gold).language == "en"
    assert AuditLog.objects.get(action="medals.certificates_issued").diff["language_fallbacks"] == 1
