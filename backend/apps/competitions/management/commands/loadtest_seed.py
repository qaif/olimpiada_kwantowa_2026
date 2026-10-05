"""``manage.py loadtest_seed`` – dane testu obciążenia (PERF-01, docs/OPERACJE.md § 42).

Zakłada w **osobnej bazie testu obciążenia** komplet, którego potrzebuje generator ruchu
(``scripts/loadtest/loadgen.py``): edycję z etapem międzynarodowym w formie pisemnej (otwartym
**teraz**, z treściami zadań w PDF), etap-test online (z pytaniami), wcześniejszy etap z ogłoszoną
tabelą wyników, aktualności, N uczniów z wpisami do obu etapów, rozmowę każdego ucznia
z organizatorem i kilku koordynatorów. Na wyjściu (``--manifest``) – JSON z identyfikatorami, które
generator wpisuje w adresy.

**Dwa bezpieczniki, oba wymagane jednocześnie** (zakłada tysiące kont ze wspólnym, jawnym hasłem):

- ``DEBUG`` albo jawne ``--i-know-this-is-not-prod`` – ta sama umowa, co ``seed_demo --force``,
- nazwa bazy z ``DATABASES["default"]["NAME"]`` zawiera ``loadtest`` – produkcja (``olimpiada``) i
  baza deweloperska nie spełnią tego warunku żadną flagą. To jest właściwa bariera: flaga chroni
  przed pomyłką w poleceniu, nazwa bazy – przed pomyłką w środowisku (skopiowany ``.env``).

Szybkość zamiast wierności ścieżce rejestracji: konta powstają ``bulk_create`` z **jednym**
skrótem hasła policzonym raz (PBKDF2 to ok. 0,15–0,5 s na hasło – 3000 kont serwisem rejestracji to
kwadrans samego liczenia skrótów). Kształt danych jest ten sam, który zostawia serwis: konto aktywne
z potwierdzonym adresem, grupa i członkostwo roli, profil uczestnika z kodem publicznym konkursu,
wpis do etapu. Zgód (``ConsentRecord``) seed nie zakłada – panel pokazuje wtedy znacznik
„brak zgód”, co kosztuje to samo zapytanie, co znacznik „zgody kompletne”.

Powtórne uruchomienie dokłada brakujące konta (do ``--students``) i niczego nie dubluje.
"""

from __future__ import annotations

import json
import os
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import (
    CompetitionRole,
    Membership,
    Participant,
    User,
    Voivodeship,
    generate_public_code,
)
from apps.accounts.services import make_coordinator
from apps.competitions.models import (
    Edition,
    Problem,
    Stage,
    StageEntry,
    StageEntryStatus,
    StageFormat,
    StageKind,
)
from apps.competitions.scoping import require_competition
from apps.competitions.services import create_stage

#: Wspólne hasło kont testu. Jawne z założenia – baza jest jednorazowa i lokalna (bezpieczniki wyżej).
DEFAULT_PASSWORD = "Loadtest-2026!"  # noqa: S105 - konto testu obciążenia, wyłącznie baza *loadtest*
EMAIL_DOMAIN = "loadtest.local"
EDITION_LABEL = "LOADTEST IQO 2027"
#: Słowo, które musi stać w nazwie bazy. Stała, a nie argument – argument dałoby się podać na produkcji.
REQUIRED_DB_MARKER = "loadtest"
BATCH = 500

#: Kraje delegacji (ISO 3166-1 alpha-2) – rozkład uczniów jak w etapie międzynarodowym.
COUNTRIES = (
    "PL DE FR ES IT GB US CA MX BR AR CL IN CN JP KR VN ID PH TH MY SG AU NZ ZA NG EG KE MA TR "
    "UA CZ SK HU RO BG GR PT NL BE SE NO FI DK EE LV LT IE AT CH IL SA AE IR PK BD LK NP KZ GE AM"
).split()


def student_email(index: int) -> str:
    return f"lt-student-{index:05d}@{EMAIL_DOMAIN}"


def coordinator_email(index: int) -> str:
    return f"lt-coordinator-{index:02d}@{EMAIL_DOMAIN}"


def fake_pdf(size_kb: int, label: str) -> bytes:
    """Poprawny nagłówkowo PDF zadanej wielkości – treść zadania do pobierania.

    Generator mierzy przepływ bajtów przez ``ProblemStatementView`` (S3 → gunicorn → Caddy), a nie
    renderowanie PDF-u w przeglądarce, więc wystarcza plik o realistycznym rozmiarze. Wypełnienie jest
    losowe, żeby kompresja w Caddym (``encode gzip``) nie zrobiła z niego kilku kilobajtów – prawdziwe
    PDF-y są już skompresowane wewnątrz.
    """
    padding = os.urandom(max(0, size_kb * 1024 - 400))
    head = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
        b"4 0 obj<</Length " + str(len(padding)).encode() + b">>stream\n"
    )
    tail = b"\nendstream endobj\n% " + label.encode() + b"\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    return head + padding + tail


class Command(BaseCommand):
    help = (
        "Dane testu obciążenia (tylko baza z 'loadtest' w nazwie): etapy, zadania, uczniowie, koordynatorzy."
    )

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=3000, help="liczba kont uczniów (domyślnie 3000)")
        parser.add_argument("--coordinators", type=int, default=5, help="liczba kont koordynatorów")
        parser.add_argument("--problems", type=int, default=3, help="liczba zadań etapu pisemnego")
        parser.add_argument("--statement-kb", type=int, default=600, help="rozmiar PDF-u treści zadania (KB)")
        parser.add_argument("--quiz-questions", type=int, default=20, help="liczba pytań testu online")
        parser.add_argument("--news", type=int, default=24, help="liczba aktualności w newsroomie")
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="wspólne hasło kont testu")
        parser.add_argument(
            "--manifest", default="", help="ścieżka pliku JSON z identyfikatorami ('-' = stdout)"
        )
        parser.add_argument(
            "--i-know-this-is-not-prod",
            action="store_true",
            dest="not_prod",
            help="pozwól uruchomić przy DEBUG=False (baza nadal MUSI mieć 'loadtest' w nazwie)",
        )

    def handle(self, *args, **options):
        self._guard(options["not_prod"])
        random.seed(20271)
        self.now = timezone.now()
        self.competition = require_competition()
        with transaction.atomic():
            edition = self._edition()
            written = self._written_stage(edition)
            problems = self._problems(written, options["problems"], options["statement_kb"])
            quiz_stage = self._quiz_stage(edition, options["quiz_questions"])
            previous = self._previous_stage(edition)
        password_hash = make_password(options["password"])
        coordinators = self._coordinators(options["coordinators"], options["password"])
        participants = self._students(options["students"], password_hash)
        self._entries(participants, (written, quiz_stage, previous))
        conversations = self._conversations(participants)
        self._results_publication(previous, participants)
        self._news(options["news"])
        self._clear_page_cache()

        manifest = {
            "competition_id": self.competition.pk,
            "edition_id": edition.pk,
            "written_stage_id": written.pk,
            "quiz_stage_id": quiz_stage.pk,
            "results_stage_id": previous.pk,
            "problem_numbers": [problem.number for problem in problems],
            "problem_ids": [problem.pk for problem in problems],
            "password": options["password"],
            "students": [
                {"email": p.user.email, "conversation_id": conversations.get(p.pk)} for p in participants
            ],
            "coordinators": [user.email for user in coordinators],
        }
        self._write_manifest(options["manifest"], manifest)
        self.stdout.write(
            self.style.SUCCESS(
                f"loadtest_seed: {len(participants)} uczniów, {len(coordinators)} koordynatorów, "
                f"etap pisemny {written.pk}, test {quiz_stage.pk}, wyniki {previous.pk}."
            )
            if options["manifest"] != "-"
            else ""
        )

    # --- bezpieczniki -----------------------------------------------------------------------------

    def _guard(self, not_prod: bool) -> None:
        db_name = str(connection.settings_dict.get("NAME") or "")
        if REQUIRED_DB_MARKER not in db_name.lower():
            raise CommandError(
                f"loadtest_seed odmawia: baza '{db_name}' nie ma '{REQUIRED_DB_MARKER}' w nazwie. "
                "Dane testu obciążenia zakłada się wyłącznie w osobnej bazie (scripts/loadtest/run.sh)."
            )
        if not settings.DEBUG and not not_prod:
            raise CommandError(
                "loadtest_seed zakłada tysiące kont ze wspólnym hasłem. Uruchom przy DEBUG=True albo "
                "świadomie z --i-know-this-is-not-prod (baza i tak musi mieć 'loadtest' w nazwie)."
            )

    # --- edycja i etapy ---------------------------------------------------------------------------

    def _edition(self) -> Edition:
        edition, _ = Edition.objects.get_or_create(competition=self.competition, year_label=EDITION_LABEL)
        Edition.objects.filter(competition=self.competition, is_current=True).exclude(pk=edition.pk).update(
            is_current=False
        )
        if not edition.is_current:
            edition.is_current = True
            edition.save(update_fields=["is_current"])
        return edition

    def _stage(self, edition, kind, *, opens, deadline, name, fmt=StageFormat.SUBMISSIONS) -> Stage:
        existing = Stage.objects.filter(edition=edition, kind=kind, name=name).first()
        if existing is not None:
            # Przesunięcie terminów przy każdym uruchomieniu: generator zakłada etap otwarty **teraz**.
            existing.opens_at, existing.deadline_at = opens, deadline
            existing.save(update_fields=["opens_at", "deadline_at"])
            return existing
        return create_stage(
            edition=edition,
            kind=kind,
            name=name,
            format=fmt,
            opens_at=opens,
            deadline_at=deadline,
            review_deadline_at=deadline + timedelta(days=14),
            appeal_window_opens_at=deadline + timedelta(days=15),
            appeal_window_closes_at=deadline + timedelta(days=20),
            min_points=0,
        )

    def _written_stage(self, edition) -> Stage:
        return self._stage(
            edition,
            StageKind.FINAL,
            name="International stage (loadtest)",
            opens=self.now - timedelta(minutes=10),
            deadline=self.now + timedelta(hours=6),
        )

    def _quiz_stage(self, edition, questions: int) -> Stage:
        from apps.quiz.models import Quiz, QuizOption, QuizQuestion

        stage = self._stage(
            edition,
            StageKind.ROUND,
            name="Online test (loadtest)",
            fmt=StageFormat.QUIZ,
            opens=self.now - timedelta(minutes=5),
            deadline=self.now + timedelta(hours=6),
        )
        quiz, _ = Quiz.objects.get_or_create(
            stage=stage, defaults={"title": "Loadtest quiz", "duration_minutes": 120, "attempts_allowed": 50}
        )
        if not quiz.questions.exists():
            for order in range(1, questions + 1):
                question = QuizQuestion.objects.create(
                    quiz=quiz,
                    order=order,
                    kind="SINGLE_CHOICE",
                    text=f"Question {order}?",
                    points=Decimal("1"),
                )
                QuizOption.objects.bulk_create(
                    QuizOption(question=question, order=i, text=f"Option {i}", is_correct=i == 1)
                    for i in range(1, 5)
                )
        return stage

    def _previous_stage(self, edition) -> Stage:
        stage = self._stage(
            edition,
            StageKind.ELIM,
            name="National round (loadtest)",
            opens=self.now - timedelta(days=90),
            deadline=self.now - timedelta(days=60),
        )
        if stage.results_published_at is None:
            stage.results_published_at = self.now - timedelta(days=20)
            stage.save(update_fields=["results_published_at"])
        return stage

    def _problems(self, stage, count: int, statement_kb: int) -> list[Problem]:
        problems = []
        for number in range(1, count + 1):
            problem, _ = Problem.objects.get_or_create(
                stage=stage, number=number, defaults={"title": f"Loadtest problem {number}"}
            )
            if not problem.statement_pdf:
                problem.statement_pdf.save(
                    f"loadtest-{number}.pdf",
                    ContentFile(fake_pdf(statement_kb, f"problem {number}")),
                    save=True,
                )
            problems.append(problem)
        return problems

    # --- konta ------------------------------------------------------------------------------------

    def _coordinators(self, count: int, password: str) -> list[User]:
        users = []
        for index in range(1, count + 1):
            email = coordinator_email(index)
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    email=email, password=password, first_name="Coordinator", last_name=f"LT{index:02d}"
                )
            if not user.is_active or user.email_verified_at is None:
                user.is_active, user.email_verified_at = True, self.now
                user.save(update_fields=["is_active", "email_verified_at"])
            make_coordinator(user, self.competition)
            users.append(user)
        return users

    def _students(self, count: int, password_hash: str) -> list[Participant]:
        emails = [student_email(i) for i in range(1, count + 1)]
        existing = set(User.objects.filter(email__in=emails).values_list("email", flat=True))
        missing = [email for email in emails if email not in existing]
        group, _ = Group.objects.get_or_create(name=CompetitionRole.PARTICIPANT.value)
        taken = set(
            Participant.objects.filter(competition=self.competition).values_list("public_code", flat=True)
        )
        for start in range(0, len(missing), BATCH):
            chunk = missing[start : start + BATCH]
            with transaction.atomic():
                users = User.objects.bulk_create(
                    User(
                        email=email,
                        password=password_hash,
                        first_name="Student",
                        last_name=email.split("@")[0].rsplit("-", 1)[-1],
                        is_active=True,
                        email_verified_at=self.now,
                    )
                    for email in chunk
                )
                User.groups.through.objects.bulk_create(
                    User.groups.through(user_id=user.pk, group_id=group.pk) for user in users
                )
                Membership.objects.bulk_create(
                    Membership(
                        user=user, competition=self.competition, role=CompetitionRole.PARTICIPANT.value
                    )
                    for user in users
                )
                Participant.objects.bulk_create(
                    Participant(
                        user=user,
                        competition=self.competition,
                        public_code=self._unique_code(taken),
                        school=f"Loadtest School {i % 400}",
                        country=COUNTRIES[i % len(COUNTRIES)],
                        grade=(i % 4) + 1,
                        district=Voivodeship.MAZOWIECKIE,
                        birth_year=2008,
                        gdpr_consent_at=self.now,
                        terms_accepted_at=self.now,
                    )
                    for i, user in enumerate(users, start=start)
                )
        return list(
            Participant.objects.filter(competition=self.competition, user__email__in=emails)
            .select_related("user")
            .order_by("user__email")
        )

    def _unique_code(self, taken: set) -> str:
        while True:
            code = generate_public_code(self.competition)
            if code not in taken:
                taken.add(code)
                return code

    def _entries(self, participants, stages) -> None:
        for stage in stages:
            have = set(StageEntry.objects.filter(stage=stage).values_list("participant_id", flat=True))
            StageEntry.objects.bulk_create(
                (
                    StageEntry(participant=p, stage=stage, status=StageEntryStatus.REGISTERED)
                    for p in participants
                    if p.pk not in have
                ),
                batch_size=BATCH,
            )

    def _conversations(self, participants) -> dict[int, int]:
        """Rozmowa każdego ucznia z organizatorem i jedna wiadomość – cel odpytywania czatu (co 15 s)."""
        from apps.chat.models import (
            Conversation,
            ConversationKind,
            ConversationMember,
            Message,
            MessageStatus,
            SenderRole,
        )

        have = dict(
            Conversation.objects.filter(
                kind=ConversationKind.ORGANIZER, participant__in=participants
            ).values_list("participant_id", "pk")
        )
        todo = [p for p in participants if p.pk not in have]
        for start in range(0, len(todo), BATCH):
            chunk = todo[start : start + BATCH]
            with transaction.atomic():
                created = Conversation.objects.bulk_create(
                    Conversation(competition=self.competition, kind=ConversationKind.ORGANIZER, participant=p)
                    for p in chunk
                )
                ConversationMember.objects.bulk_create(
                    ConversationMember(conversation=c, participant=p)
                    for c, p in zip(created, chunk, strict=True)
                )
                Message.objects.bulk_create(
                    Message(
                        conversation=c,
                        sender=p.user,
                        sender_role=SenderRole.PARTICIPANT,
                        body="Hello, a question about problem 2.",
                        status=MessageStatus.PUBLISHED,
                    )
                    for c, p in zip(created, chunk, strict=True)
                )
                have.update({p.pk: c.pk for c, p in zip(created, chunk, strict=True)})
        return have

    def _results_publication(self, stage, participants) -> None:
        """Ogłoszona tabela poprzedniego etapu – ``/results/<id>/`` w dniu zawodów czytają tysiące osób."""
        from apps.results.models import ResultsPublication

        if ResultsPublication.objects.filter(stage=stage).exists():
            return
        rows = []
        for rank, participant in enumerate(participants, start=1):
            # Punkty tabeli testowej – rozkład, nie kryptografia.
            points = {str(n): str(Decimal(random.randint(0, 20)) / 2) for n in (1, 2, 3)}  # noqa: S311
            rows.append(
                {
                    "rank": rank,
                    "display": participant.public_code,
                    "district": participant.country,
                    "points": points,
                    "total": str(sum(Decimal(v) for v in points.values())),
                    "qualified": rank <= len(participants) // 2,
                    "manual": False,
                }
            )
        ResultsPublication.objects.create(
            stage=stage, snapshot=rows, published_at=self.now - timedelta(days=20)
        )

    def _news(self, count: int) -> None:
        from apps.cms.models import NewsIndexPage, NewsPage

        index = NewsIndexPage.objects.first()
        if index is None or count <= 0:
            return
        have = NewsPage.objects.child_of(index).count()
        for number in range(have + 1, count + 1):
            page = NewsPage(
                title=f"Loadtest news {number}",
                slug=f"loadtest-news-{number}",
                date=(self.now - timedelta(days=count - number)).date(),
                lead="Stage day announcement for the load test.",
            )
            index.add_child(instance=page)
            page.save_revision().publish()

    def _clear_page_cache(self) -> None:
        from apps.web.page_cache import invalidate_all

        invalidate_all()

    def _write_manifest(self, target: str, manifest: dict) -> None:
        if not target:
            return
        payload = json.dumps(manifest, ensure_ascii=False, indent=1)
        if target == "-":
            self.stdout.write(payload)
            return
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(payload)
