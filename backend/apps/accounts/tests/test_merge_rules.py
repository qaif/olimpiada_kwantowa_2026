"""Reguły scalania kont (ACC-MERGE-01) – inwentaryzacja relacji, bez bazy danych.

Ten plik jest bezpiecznikiem na **przyszłość**: każda nowa relacja do ``Participant`` albo ``User``
musi dostać regułę scalania (``apps.accounts.merge.RULES`` albo regułę domyślną). Bez tego
scalenie odmówiłoby w chwili, gdy kopia ma w nowej tabeli wiersze – a to lepsze niż ciche
skasowanie ich kaskadą razem z kopią, ale gorsze od świadomej decyzji przy dodawaniu modelu.
"""

from __future__ import annotations

from apps.accounts.merge import (
    AUDIT_LOG,
    CONFLICT,
    CUSTOM,
    MOVE,
    MOVE_UNIQUE,
    NEWER,
    ROLE,
    RULES,
    STAYS,
    relations,
    rule_for,
)


def _labels() -> set[str]:
    return {f"{model._meta.label}.{fld.name}" for model, fld in relations()}


def test_every_relation_is_classified():
    missing = sorted(
        f"{model._meta.label}.{fld.name}" for model, fld in relations() if rule_for(model, fld) is None
    )

    assert missing == [], f"Relacje bez reguły scalania (dopisz do apps.accounts.merge.RULES): {missing}"


def test_explicit_rules_name_existing_relations():
    """Literówka w kluczu ``RULES`` znaczyłaby regułę, która nigdy nie zadziała."""
    assert sorted(set(RULES) - _labels()) == []


def test_the_audit_log_is_never_moved():
    assert RULES["core.AuditLog.actor"].kind == AUDIT_LOG


def test_rules_from_the_specification_table():
    expected = {
        "competitions.StageEntry.participant": CUSTOM,
        "accounts.ConsentRecord.participant": MOVE,
        "student_status.StudentStatusCertificate.participant": CUSTOM,
        "cms.WorkshopAttendance.participant": MOVE_UNIQUE,
        "proctoring.ProctoringSession.participant": CONFLICT,
        "alumni.AlumniProfile.participant": CONFLICT,
        "time_windows.ParticipantTimezone.participant": NEWER,
        "forum.ForumSubscription.user": MOVE_UNIQUE,
        "accounts.CommitteeMember.user": ROLE,
        "translation_review.TranslatorGrant.user": ROLE,
        "socialaccount.SocialAccount.user": STAYS,
        "accounts.TwoFactorDevice.user": STAYS,
    }

    assert {label: RULES[label].kind for label in expected} == expected


def test_authorship_columns_follow_the_person_by_default():
    """Kolumny „kto to zrobił” (``SET_NULL``) w aplikacjach projektu przechodzą na konto docelowe."""
    by_label = {f"{model._meta.label}.{fld.name}": (model, fld) for model, fld in relations()}
    for label in ("forum.ForumThread.author", "forum.ForumPost.author", "chat.Message.sender"):
        model, fld = by_label[label]
        assert rule_for(model, fld).kind == MOVE, label
    # CMS i panel Django – ślad redakcji, zostaje z kopią.
    model, fld = by_label["wagtailcore.Revision.user"]
    assert rule_for(model, fld).kind == STAYS
