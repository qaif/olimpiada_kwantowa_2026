# ruff: noqa: T201
"""Dane audytu dostępności (A11Y-01) – wykonywane przez ``manage.py shell`` w e2e/a11y/serve.sh.

Dwa konkursy na jednej instalacji, tak jak na produkcji:

- **Konkurs #1** (Olimpiada Kwantowa, motyw klasyczny = brak paczki) – ``seed_demo`` + ``seed_cms``,
  plus to, czego seed deweloperski nie zakłada, a co jest badanym ekranem: test (quiz) w etapie
  treningowym, rozmowa z organizatorem, prace z przydziałami recenzentów, ogłoszona tabela wyników,
  konto z 2FA, dokumenty w stopce (``/dokumenty/…``),
- **IQO** (``iqo``, prefiks ścieżki ``/iqo/``, kraje, rejestracja przez delegacje, 11 języków,
  logistyka finału, motyw ``iqo-quantum`` wgrany z katalogu ``themes/`` repozytorium): opiekun
  drużyny z dwojgiem uczniów, wydarzenie finałowe i wystawiony list wizowy.

Wszystko idzie przez **serwisy** aplikacji (te same, co ekrany), a nie przez fabryki testowe –
obraz produkcyjny w CI nie ma ``factory_boy``. Na końcu powstaje ``state/fixture.json`` z adresami,
których test nie zna z góry (identyfikatory, kody, token resetu hasła, sekret TOTP).

Skrypt zakłada **świeżą bazę** (scripts/a11y.sh zawsze startuje od pustego Postgresa w tmpfs) – nie
służy do zasilania środowiska deweloperskiego.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.utils import timezone

STATE = Path("/e2e/a11y/state")
THEME_DIR = Path("/themes/iqo-quantum")
PASSWORD = "Demo12345!"  # noqa: S105 - konta demonstracyjne przebiegu testów
#: Sekret TOTP konta z 2FA (RFC 6238, base32). Jawny, bo konto żyje tylko w bazie tego przebiegu.
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
IQO_SLUG = "iqo"
IQO_PREFIX = "iqo"
IQO_LANGUAGES = ["en", "pl", "ar", "zh-hans", "hi", "es", "fr", "bn", "pt", "ru", "id"]

fixture: dict = {"password": PASSWORD, "iqo_prefix": f"/{IQO_PREFIX}", "totp_secret": TOTP_SECRET}


def log(message: str) -> None:
    print(f"a11y-seed: {message}", flush=True)


def theme_zip() -> bytes:
    """Paczka ``iqo-quantum`` złożona w pamięci tą samą listą plików, co ``build_zip.py``."""
    spec = importlib.util.spec_from_file_location("iqo_build_zip", THEME_DIR / "build_zip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in module.collect():
            zf.write(path, path.relative_to(THEME_DIR).as_posix())
    return buffer.getvalue()


def verified(user):
    user.email_verified_at = user.email_verified_at or timezone.now()
    user.is_active = True
    user.save(update_fields=["email_verified_at", "is_active"])
    return user


# --- Konkurs #1: dane deweloperskie ----------------------------------------------------------------
call_command("seed_demo", force=True, verbosity=0)
call_command("seed_cms", verbosity=0)
log("Konkurs #1: seed_demo + seed_cms")

from apps.accounts.models import CommitteeMember, Participant, User  # noqa: E402
from apps.accounts.twofactor import TwoFactorDevice, encrypt_secret  # noqa: E402
from apps.competitions.models import Stage, StageEntry, StageFormat, StageKind  # noqa: E402
from apps.competitions.services import create_stage, current_edition  # noqa: E402
from apps.tenancy.context import competition_context  # noqa: E402
from apps.tenancy.models import Competition  # noqa: E402

main = Competition.objects.get(slug="kwantowa")
coordinator = User.objects.get(email="koordynator@example.com")
participant_user = User.objects.get(email="uczestnik1@example.com")
now = timezone.now()

with competition_context(main):
    edition = current_edition(main)
    elim = Stage.objects.get(edition=edition, kind=StageKind.ELIM)
    district = Stage.objects.get(edition=edition, kind=StageKind.DISTRICT)
    fixture["main"] = {"elim": elim.pk, "district": district.pk}

    # Test (quiz) w etapie treningowym: etap eliminacyjny zostaje etapem z wysyłką prac, bo jego
    # formularz uploadu jest osobnym badanym ekranem.
    from apps.quiz.services import save_question, save_quiz_settings, start_attempt

    training = create_stage(
        edition=edition,
        kind=StageKind.TRAINING,
        opens_at=now - timedelta(days=1),
        deadline_at=now + timedelta(days=30),
        review_deadline_at=now + timedelta(days=40),
        appeal_window_opens_at=now + timedelta(days=41),
        appeal_window_closes_at=now + timedelta(days=42),
        format=StageFormat.QUIZ,
        name="Trening – test",
        min_points=0,
    )
    quiz = save_quiz_settings(
        stage=training,
        title="Test próbny",
        instructions="Zaznacz jedną odpowiedź w każdym pytaniu.",
        duration_minutes=30,
        attempts_allowed=3,
    )
    save_question(
        quiz=quiz,
        kind="SINGLE_CHOICE",
        text="Ile stanów bazowych ma kubit?",
        points=1,
        options=[{"text": "1", "is_correct": False}, {"text": "2", "is_correct": True}],
    )
    save_question(
        quiz=quiz,
        kind="MULTIPLE_CHOICE",
        text="Które bramki są jednokubitowe?",
        points=2,
        options=[
            {"text": "Hadamard", "is_correct": True},
            {"text": "Pauli-X", "is_correct": True},
            {"text": "CNOT", "is_correct": False},
        ],
    )
    participant = Participant.objects.get(user=participant_user, competition=main)
    training_entry, _ = StageEntry.objects.get_or_create(stage=training, participant=participant)
    attempt = start_attempt(quiz=quiz, entry=training_entry)
    fixture["main"].update({"training": training.pk, "quiz_attempt": attempt.pk})

    # Rozmowa z organizatorem (wątek po obu stronach).
    from apps.chat.services import organizer_writes_to, write_to_organizer

    message = write_to_organizer(
        user=participant_user, competition=main, body="Dzień dobry, mam pytanie o termin etapu."
    )
    organizer_writes_to(
        user=coordinator,
        competition=main,
        participant=participant,
        body="Dzień dobry, termin jest w harmonogramie na stronie głównej.",
    )
    fixture["main"]["conversation"] = message.conversation_id

    # Prace i przydziały recenzentów (ekran recenzenta i tabela przydziałów koordynatora).
    from apps.grading.models import ROUND_BLIND, Review, ReviewStatus
    from apps.submissions.models import Submission, SubmissionStatus

    reviewers = list(CommitteeMember.objects.filter(competition=main).order_by("pk"))
    first_review = None
    for index, entry in enumerate(StageEntry.objects.filter(stage=elim).order_by("pk")):
        for problem in elim.problems.order_by("number"):
            submission = Submission.objects.create(
                competition=main,
                entry=entry,
                problem=problem,
                version=1,
                status=SubmissionStatus.SUBMITTED,
            )
            for reviewer in reviewers[:2]:
                review = Review.objects.create(
                    submission=submission,
                    reviewer=reviewer,
                    round=ROUND_BLIND,
                    status=ReviewStatus.ASSIGNED,
                    assigned_at=now,
                )
                if first_review is None and reviewer == reviewers[0]:
                    first_review = review
        if index >= 3:
            break
    fixture["main"]["review"] = first_review.pk if first_review else None

    # Ogłoszona tabela wyników (publiczna, „ciężka” – 40 wierszy).
    from apps.results.models import Anonymization, ResultsPublication

    ResultsPublication.objects.create(
        stage=district,
        anonymization=Anonymization.CODE,
        published_at=now,
        snapshot=[
            {
                "rank": index + 1,
                "display": f"OK-{index + 1:03d}",
                "points": {"1": max(0, 6 - index % 7), "2": index % 6, "3": (index * 5) % 7},
                "total": max(0, 6 - index % 7) + index % 6 + (index * 5) % 7,
                "qualified": index < 15,
            }
            for index in range(40)
        ],
    )

    # Konto z 2FA (ekran „kod z aplikacji” przy logowaniu) – osobne, żeby logowania innych ról
    # nie zatrzymywały się na drugim kroku.
    twofa_user = verified(User.objects.get(email="uczestnik5@example.com"))
    TwoFactorDevice.objects.update_or_create(
        user=twofa_user,
        defaults={
            "secret": encrypt_secret(TOTP_SECRET),
            "confirmed_at": now,
            "backup_codes": [],
            "last_counter": 0,
        },
    )
    fixture["twofa_email"] = twofa_user.email

    # Link resetu hasła – konto, które w przebiegu nigdy się nie loguje (logowanie unieważnia token).
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    reset_user = User.objects.get(email="uczestnik4@example.com")
    fixture["reset_path"] = (
        f"/reset/{urlsafe_base64_encode(force_bytes(reset_user.pk))}/"
        f"{default_token_generator.make_token(reset_user)}/"
    )

# Dokumenty w stopce (RODO, cookies) i deklaracja dostępności – te same ścieżki, co na produkcji.
call_command("seed_legacy_content", verbosity=0)
call_command("seed_accessibility_statement", "kwantowa", publish=True, verbosity=0)
log("Konkurs #1: quiz, czat, przydziały, wyniki, 2FA, dokumenty")

# --- IQO --------------------------------------------------------------------------------------------
# Drugi konkurs wymaga ról z członkostw, a nie z globalnych grup (docs/OPERACJE.md § 6.1).
call_command("check_memberships", fix=True, verbosity=0)
main.refresh_from_db()
main.feature_flags = {**(main.feature_flags or {}), "memberships_enforced": True}
main.save(update_fields=["feature_flags"])
call_command(
    "create_competition",
    slug=IQO_SLUG,
    name="International Quantum Olympiad",
    domain="iqo.localhost",
    path_prefix=IQO_PREFIX,
    regions="countries",
    registration="delegations",
    coordinator_email=coordinator.email,
    verbosity=0,
)
call_command("competition_languages", IQO_SLUG, *IQO_LANGUAGES, default="en", verbosity=0)
iqo = Competition.objects.get(slug=IQO_SLUG)
iqo.feature_flags = {**(iqo.feature_flags or {}), "onsite_logistics": True, "themes": True}
iqo.save(update_fields=["feature_flags"])

from apps.themes import services as theme_services  # noqa: E402

version, result = theme_services.install_package(theme_zip())
if version is None or not version.is_valid:
    raise SystemExit(f"Paczka motywu odrzucona: {result.errors}")
with competition_context(iqo):
    theme_services.activate(iqo, version)
log(f"IQO: konkurs, języki, motyw {version.theme.slug} {version.version}")

with competition_context(iqo):
    from apps.accounts import delegation_services as delegations
    from apps.accounts.models import ConsentKind
    from apps.delegation_logistics import letters
    from apps.delegation_logistics import services as logistics
    from apps.delegation_logistics.models import AccessRole, FinalEvent, LogisticsAccess

    captured: dict = {}

    def capture(invitation, token, *, request=None):
        captured["token"] = token
        invitation.sent_at = timezone.now()
        invitation.save(update_fields=["sent_at"])

    original_send = delegations._send_leader_invitation
    delegations._send_leader_invitation = capture
    try:
        invitation = delegations.invite_leader(
            iqo, email="leader-de@example.com", country_code="de", actor=coordinator
        )
    finally:
        delegations._send_leader_invitation = original_send
    leader = delegations.accept_leader_invitation(
        invitation,
        first_name="Lena",
        last_name="Leiter",
        password=PASSWORD,
        given={ConsentKind.TERMS: True, ConsentKind.PRIVACY: True},
    )
    verified(leader.user)
    today = timezone.localdate()
    students = [
        delegations.add_student(
            leader,
            first_name=first,
            last_name=last,
            email=email,
            birth_date=today.replace(year=today.year - age),
            school="Gymnasium Berlin",
            grade=2,
        )
        for first, last, email, age in (
            ("Anna", "Adult", "anna-de@example.com", 19),
            ("Max", "Minor", "max-de@example.com", 16),
        )
    ]
    # Jeden uczeń z aktywnym kontem – panel uczestnika w motywie IQO.
    student_user = students[0].user
    student_user.set_password(PASSWORD)
    student_user.save(update_fields=["password"])
    verified(student_user)

    edition = current_edition(iqo)
    start = today + timedelta(days=60)
    FinalEvent.objects.create(
        competition=iqo,
        edition=edition,
        name="IQO 2027",
        city="Warsaw",
        starts_on=start,
        ends_on=start + timedelta(days=6),
        letter_prefix="IQO",
    )
    LogisticsAccess.objects.create(competition=iqo, user=coordinator, role=AccessRole.OFFICER)
    member = next(m for m in logistics.members_of(leader.delegation) if m.participant_id == students[0].pk)
    logistics.save_member(
        member,
        {
            "passport_name": "ANNA ADULT",
            "nationality": "de",
            "date_of_birth": today.replace(year=today.year - 19),
            "passport_number": "C01X00T47",
            "passport_expiry": today + timedelta(days=900),
        },
        actor=leader.user,
    )
    member = logistics.member_for_leader(leader, member.pk)
    letter = letters.issue_letter(iqo, leader.delegation, member=member, actor=coordinator)
    fixture["iqo"] = {
        "leader_email": leader.user.email,
        "student_email": student_user.email,
        "student_pk": students[1].pk,
        "member_pk": member.pk,
        "letter_code": letter.display_code,
    }
call_command("seed_accessibility_statement", IQO_SLUG, publish=True, verbosity=0)
log("IQO: delegacja, uczniowie, wydarzenie finałowe, list wizowy")

STATE.mkdir(parents=True, exist_ok=True)
(STATE / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
log("gotowe")
