"""Migracja danych ``core.0003``: zastanym wpisom audytu dopisujemy konkurs ich obiektu.

Migracja jest z założenia **najlepszej staranności** (§ 3.9) i właśnie to jest tu sprawdzane:
nie „czy wypełniła wszystko”, tylko czy każdą z trzech grup wierszy potraktowała tak, jak trzeba.
Wpis o obiekcie domeny zawodów dostaje konkurs tego obiektu, wpis o samym konkursie – ten konkurs,
a wpis o obiekcie platformowym albo o obiekcie, którego już nie ma, zostaje bez konkursu i pozostaje
widoczny przez ``visible_to``.

Ostatnia grupa jest najważniejsza i najłatwiejsza do zepsucia: gdyby migracja próbowała
„domknąć” takie wiersze przez przypisanie ich do jedynego konkursu instalacji, ślad po skasowanym
obiekcie stałby się w bazie wielokonkursowej cudzą własnością.

Test wygląda inaczej niż reszta pakietu z tych samych powodów, co ``apps/accounts/tests/
test_migrations.py``: przewijanie migracji to DDL po DML, więc potrzebny jest ``transaction=True``.
"""

import pytest
from django.utils import timezone

from .migration_helpers import ensure_competition, migrate_to, migrate_to_head

#: Stan „tuż przed backfillem”. Celów jest kilka, bo ``executor.migrate`` przewija **wyłącznie
#: przodków** podanego węzła: sam ``core.0002`` zabrałby z bazy kolumny konkursu w ``competitions``
#: i ``accounts``, czyli świat, o którego przepisanie w tym teście chodzi. Lista jest dokładnie
#: listą zależności migracji ``0003``.
BEFORE = [
    ("core", "0002_auditlog_competition"),
    ("accounts", "0020_backfill_competition_and_memberships"),
    ("cms", "0022_backfill_announcement_competition"),
    ("competitions", "0020_backfill_edition_competition"),
    ("submissions", "0006_backfill_submission_competition"),
]
AFTER = [("core", "0003_backfill_auditlog_competition")]


@pytest.fixture
def rewound_apps(transactional_db):  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    yield migrate_to(BEFORE)
    migrate_to_head()


def make_entry(AuditLog, target_type: str, target_id) -> object:
    return AuditLog.objects.create(
        action="test.event",
        target_type=target_type,
        target_id=str(target_id),
        at=timezone.now(),
        competition=None,
    )


@pytest.mark.django_db(transaction=True)
def test_entries_get_the_competition_of_their_target(rewound_apps):
    Edition = rewound_apps.get_model("competitions", "Edition")
    AuditLog = rewound_apps.get_model("core", "AuditLog")
    competition = ensure_competition(rewound_apps)
    edition = Edition.objects.create(year_label="I (2025/2026)", competition=competition)

    about_edition = make_entry(AuditLog, "competitions.edition", edition.pk)
    about_competition = make_entry(AuditLog, "tenancy.competition", competition.pk)
    # Obiekt platformowy i obiekt, którego nie ma – obie grupy zostają bez konkursu.
    about_user = make_entry(AuditLog, "accounts.user", 1)
    about_ghost = make_entry(AuditLog, "competitions.stage", 999_999)

    Migrated = migrate_to(AFTER).get_model("core", "AuditLog")

    assert Migrated.objects.get(pk=about_edition.pk).competition_id == competition.pk
    assert Migrated.objects.get(pk=about_competition.pk).competition_id == competition.pk
    assert Migrated.objects.get(pk=about_user.pk).competition_id is None
    assert Migrated.objects.get(pk=about_ghost.pk).competition_id is None
