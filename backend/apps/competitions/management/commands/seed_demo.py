"""``manage.py seed_demo`` – idempotentne dane demonstracyjne dla środowiska deweloperskiego.

Komenda zakłada konta z jawnym, znanym hasłem, więc na środowisku bez ``DEBUG`` wymaga ``--force``.
Powtórne uruchomienie nie tworzy duplikatów: wszystko idzie przez ``get_or_create`` albo przez
sprawdzenie istnienia konta przed wywołaniem serwisu rejestracji.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CommitteeMember, CommitteeStatus, InvitationCode, User, Voivodeship
from apps.accounts.services import (
    approve_committee_member,
    create_invitation,
    make_coordinator,
    register_participant,
)
from apps.competitions.models import Edition, Problem, Stage, StageEntry, StageEntryStatus, StageKind
from apps.competitions.services import create_stage

DEMO_PASSWORD = "Demo12345!"  # noqa: S105 - konto demonstracyjne, wyłącznie dla środowiska dev
DEMO_EDITION_LABEL = "XV (2026/2027)"
DEMO_PARTICIPANT_COUNT = 5
DEMO_REVIEWER_DISTRICTS = (
    Voivodeship.MAZOWIECKIE,
    Voivodeship.MALOPOLSKIE,
    Voivodeship.MAZOWIECKIE,
)
DEMO_COORDINATOR_EMAIL = "koordynator@example.com"

# Offsety terminów liczone od momentu pierwszego uruchomienia (dni względem `now`).
STAGE_PLAN = (
    # kind, opens_at, deadline_at, review_deadline_at, appeal_opens_at, appeal_closes_at
    (StageKind.ELIM, -1, 14, 28, 30, 37),
    (StageKind.DISTRICT, 60, 74, 88, 90, 97),
    (StageKind.FINAL, 120, 134, 148, 150, 157),
)

DEMO_PROBLEMS = (
    (1, "Nierówność ze średnimi"),
    (2, "Kolorowanie grafu turniejowego"),
    (3, "Punkt izogonalny w czworokącie"),
)


class Command(BaseCommand):
    help = "Tworzy idempotentne dane demonstracyjne: edycję z 3 etapami, zadania, uczestników, komitet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Pozwól uruchomić przy DEBUG=False (konta demo mają jawne hasło – używać świadomie).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_demo tworzy konta z jawnym hasłem. Uruchom z DEBUG=True albo świadomie z --force."
            )

        now = timezone.now()
        edition = self._ensure_edition()
        stages = {kind: self._ensure_stage(edition, kind, plan, now) for kind, *plan in STAGE_PLAN}
        elim = stages[StageKind.ELIM]
        self._ensure_problems(elim)
        coordinator = self._ensure_coordinator()
        self._ensure_participants(elim)
        self._ensure_reviewers(coordinator)
        self._ensure_invitation(coordinator)

        self.stdout.write(
            self.style.SUCCESS(
                f"Gotowe: edycja {edition.year_label}, etapy {', '.join(sorted(stages))}, "
                f"uczestnicy {StageEntry.objects.filter(stage=elim).count()}."
            )
        )

    # --- edycja i etapy -------------------------------------------------------------------

    def _ensure_edition(self) -> Edition:
        edition, created = Edition.objects.get_or_create(year_label=DEMO_EDITION_LABEL)
        if not edition.is_current:
            # Najpierw zdejmij flagę z innych edycji – częściowy unique constraint dopuszcza jedną bieżącą.
            Edition.objects.filter(is_current=True).exclude(pk=edition.pk).update(is_current=False)
            edition.is_current = True
            edition.save(update_fields=["is_current"])
        self._report("edycja", edition.year_label, created)
        return edition

    def _ensure_stage(self, edition: Edition, kind: str, offsets: list[int], now) -> Stage:
        """Etap tworzy serwis ``create_stage`` – razem z domyślną skalą 0/2/5/6 i progiem MIN_POINTS."""
        existing = Stage.objects.filter(edition=edition, kind=kind).first()
        if existing is not None:
            self._report("etap", kind, created=False)
            return existing
        opens, deadline, review, appeal_opens, appeal_closes = offsets
        stage = create_stage(
            edition=edition,
            kind=kind,
            opens_at=now + timedelta(days=opens),
            deadline_at=now + timedelta(days=deadline),
            review_deadline_at=now + timedelta(days=review),
            appeal_window_opens_at=now + timedelta(days=appeal_opens),
            appeal_window_closes_at=now + timedelta(days=appeal_closes),
            min_points=0,
        )
        self._report("etap", kind, created=True)
        return stage

    def _ensure_problems(self, stage: Stage) -> None:
        for number, title in DEMO_PROBLEMS:
            _, created = Problem.objects.get_or_create(stage=stage, number=number, defaults={"title": title})
            self._report("zadanie", f"{stage.kind}/{number}", created)

    # --- konta ----------------------------------------------------------------------------

    def _ensure_coordinator(self) -> User:
        user = User.objects.filter(email=DEMO_COORDINATOR_EMAIL).first()
        created = user is None
        if user is None:
            user = User.objects.create_superuser(
                email=DEMO_COORDINATOR_EMAIL,
                password=DEMO_PASSWORD,
                first_name="Koordynator",
                last_name="Demo",
            )
        make_coordinator(user)
        self._report("koordynator", DEMO_COORDINATOR_EMAIL, created)
        return user

    def _ensure_participants(self, elim: Stage) -> None:
        for index in range(1, DEMO_PARTICIPANT_COUNT + 1):
            email = f"uczestnik{index}@example.com"
            user = User.objects.filter(email=email).first()
            if user is None:
                participant = register_participant(
                    email=email,
                    password=DEMO_PASSWORD,
                    first_name=f"Uczestnik{index}",
                    last_name="Demo",
                    school=f"LO nr {index}",
                    district=Voivodeship.MAZOWIECKIE if index % 2 else Voivodeship.MALOPOLSKIE,
                    birth_year=2008,
                    gdpr_consent=True,
                    guardian_consent=True,
                )
                created = True
            else:
                participant = user.participant
                created = False
            self._report("uczestnik", email, created)
            # Świadomie ``get_or_create`` zamiast ``register_for_stage``: seed musi być idempotentny,
            # a serwis rejestracji z założenia odrzuca powtórne zgłoszenie (ALREADY_REGISTERED).
            StageEntry.objects.get_or_create(
                participant=participant, stage=elim, defaults={"status": StageEntryStatus.REGISTERED}
            )

    def _ensure_reviewers(self, coordinator: User) -> None:
        for index, district in enumerate(DEMO_REVIEWER_DISTRICTS, start=1):
            email = f"recenzent{index}@example.com"
            user = User.objects.filter(email=email).first()
            created = user is None
            if user is None:
                user = User.objects.create_user(
                    email=email,
                    password=DEMO_PASSWORD,
                    first_name=f"Recenzent{index}",
                    last_name="Demo",
                )
            member, _ = CommitteeMember.objects.get_or_create(
                user=user, defaults={"district": district, "status": CommitteeStatus.PENDING}
            )
            if member.status == CommitteeStatus.PENDING:
                # Ta sama ścieżka co w panelu koordynatora: status ACTIVE + grupy reviewer/appeals.
                approve_committee_member(member, actor=coordinator)
            self._report("recenzent", f"{email} ({district})", created)

    def _ensure_invitation(self, coordinator: User) -> None:
        if InvitationCode.objects.filter(created_by=coordinator).exists():
            self.stdout.write(
                "kod zaproszenia: już istnieje (kodu nie da się odtworzyć – nowy przez create_invitation)"
            )
            return
        invitation, plain_code = create_invitation(coordinator, valid_for=timedelta(days=30), max_uses=5)
        self.stdout.write(
            self.style.SUCCESS(f"Kod zaproszenia (zapisz teraz, nie da się go odtworzyć): {plain_code}")
        )
        self.stdout.write(f"  max_uses={invitation.max_uses} expires_at={invitation.expires_at.isoformat()}")

    def _report(self, label: str, name: str, created: bool) -> None:
        self.stdout.write(f"{label}: {name} [{'utworzono' if created else 'istnieje'}]")
