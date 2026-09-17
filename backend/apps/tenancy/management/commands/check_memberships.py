"""``manage.py check_memberships`` — kto straci dostęp po przełączeniu ``memberships_enforced``.

O tym, czy ktoś jest recenzentem, rozstrzyga dziś **globalna grupa Django**; po przełączeniu flagi
``memberships_enforced`` konkursu rozstrzyga **wiersz ``accounts.Membership``** tego konkursu
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.8, jedyne miejsce tej reguły: ``apps.accounts.services.has_role``).
Przełącznik jest jedną wartością w ``feature_flags``, więc kosztuje minutę — ale kosztuje ją także
wtedy, gdy backfill członkostw kogoś pominął. Objaw jest wtedy najgorszy z możliwych: recenzent
loguje się w dniu, w którym ma oddać recenzje, i widzi panel bez przydziałów.

Ta komenda jest **pre-flightem** tamtej minuty. Porównuje dwie odpowiedzi na to samo pytanie —
grupę Django i członkostwo w konkursie — i wypisuje różnicę **zanim** flaga pójdzie w górę.
``--fix`` dopisuje brakujące członkostwa (nigdy nie kasuje: to jest narzędzie do dopięcia backfillu,
a nie do porządkowania ról; odbieranie roli jest decyzją koordynatora, nie skryptu).

**Skąd komenda wie, do którego konkursu należy członek globalnej grupy.** Grupa nie niesie
właściciela, więc na instalacji z jednym konkursem odpowiedź jest oczywista (należy do niego —
i to jest dzisiejsza produkcja), a przy wielu konkursach trzeba ją wywieść z danych. Osoba jest
**związana** z konkursem, jeżeli ma w nim profil uczestnika (``accounts.Participant``), profil
opiekuna szkolnego (``accounts.SchoolSupervisor``) albo jakiekolwiek członkostwo. Członek grupy
bez żadnego z tych śladów nie jest przypisywany nigdzie — zgadywanie skończyłoby się nadaniem
recenzentowi olimpiady fizycznej wglądu w prace olimpiady kwantowej, czyli dokładnie tym, czemu
członkostwa mają zapobiec.

**Profil uczestnika liczy się mocniej niż grupa.** Wiersz ``Participant`` konkursu jest dowodem
udziału w **tym** konkursie, więc uczestnik bez członkostwa jest rozjazdem także wtedy, gdy nikt
nie dopisał go do grupy ``participant``. Tak samo opiekun szkolny z profilem w konkursie.

**Konta nieaktywne są pomijane.** ``has_role`` odmawia im roli przed i po przełączeniu flagi
(blokada konta ma zamykać dostęp od razu, bez sprzątania członkostw), więc nie mają czego stracić
i tylko zaciemniałyby wynik.

Kod wyjścia: 1, gdy znaleziono rozjazdy i nie podano ``--fix`` — po to, żeby dało się wpiąć
komendę w monitoring i w listę kontrolną wdrożenia.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.tenancy.models import Competition

#: Ile adresów wypisać przy jednej roli, zanim wynik zamieni się w ścianę tekstu. Reszta jest
#: policzona w podsumowaniu — operator, który chce pełną listę, ma ją w bazie, a nie w logu.
MAX_LISTED = 20


class Command(BaseCommand):
    help = (
        "Porównuje grupy Django z członkostwami konkursu i wypisuje, kto straci dostęp po "
        "przełączeniu memberships_enforced. --fix dopisuje brakujące członkostwa."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            default="",
            help="Identyfikator jednego konkursu (slug). Domyślnie sprawdzane są wszystkie.",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Dopisz brakujące członkostwa (nigdy nie kasuje istniejących).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Pokaż także role bez rozjazdu (domyślnie widać same różnice).",
        )

    def handle(self, *args, **options):
        competitions = self._competitions(options["competition"])
        # Jeden konkurs w bazie = dzisiejsza produkcja: każdy członek globalnej grupy należy do
        # niego, bo innego nie ma. Rozstrzygamy to raz, a nie przy każdej roli.
        single = Competition.objects.count() == 1

        problems = 0
        fixed = 0
        for competition in competitions:
            competition_problems, competition_fixed = self._check(
                competition, single=single, fix=options["fix"], show_all=options["all"]
            )
            problems += competition_problems
            fixed += competition_fixed

        summary = f"Sprawdzone konkursy: {len(competitions)}, brakujące członkostwa: {problems}" + (
            f", dopisane: {fixed}." if options["fix"] else "."
        )
        if problems and not options["fix"]:
            # ``CommandError`` zamiast samego ostrzeżenia: kod wyjścia 1 jest tu treścią komunikatu.
            # Komenda odpowiada na pytanie „czy wolno przełączyć flagę”, a „nie wolno” musi dać się
            # przeczytać skryptowi, nie tylko człowiekowi.
            raise CommandError(summary + " Przełączenie memberships_enforced odetnie te osoby.")
        self.stdout.write(self.style.SUCCESS(summary) if not problems else self.style.WARNING(summary))

    # --- wybór konkursów ----------------------------------------------------------------------
    def _competitions(self, slug: str) -> list[Competition]:
        queryset = Competition.objects.order_by("slug")
        if not slug:
            rows = list(queryset)
            if not rows:
                raise CommandError("W bazie nie ma żadnego konkursu – nie ma czego sprawdzać.")
            return rows
        competition = queryset.filter(slug=slug.strip().lower()).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{slug}”.")
        return [competition]

    # --- porównanie ---------------------------------------------------------------------------
    def _check(self, competition: Competition, *, single: bool, fix: bool, show_all: bool):
        from apps.accounts.models import CompetitionRole, Membership

        enforced = competition.has_feature("memberships_enforced")
        state = "flaga włączona" if enforced else "flaga wyłączona"
        expected = self._expected_roles(competition, single=single)

        problems = 0
        fixed = 0
        for role in CompetitionRole.values:
            wanted = expected[role]
            have = set(
                Membership.objects.filter(competition=competition, role=role).values_list(
                    "user_id", flat=True
                )
            )
            missing = sorted(wanted - have)
            if not missing:
                if show_all:
                    self.stdout.write(f"ok     {competition.slug} / {role}: {len(have)} członkostw")
                continue

            problems += len(missing)
            self.stdout.write(
                self.style.WARNING(
                    f"UWAGA  {competition.slug} / {role} ({state}): bez członkostwa "
                    f"{len(missing)} z {len(wanted)} – {self._emails(missing)}"
                )
            )
            if fix:
                fixed += self._grant(competition, role, missing)

        # Członkostwo bez grupy Django nie odbiera niczego po przełączeniu flagi, ale zamyka
        # ``/cms/``: dostęp do panelu redakcyjnego wisi na uprawnieniach grupy ``coordinator``
        # (migracja ``cms.0003_coordinator_permissions``), a nie na członkostwie. Dlatego jest to
        # osobna informacja, a nie rozjazd – i dlatego ``--fix`` jej nie dotyka.
        self._report_missing_groups(competition)
        return problems, fixed

    def _expected_roles(self, competition: Competition, *, single: bool) -> dict[str, set[int]]:
        """Kto **powinien** mieć członkostwo w tym konkursie, w rozbiciu na role."""
        from apps.accounts.models import (
            CompetitionRole,
            Membership,
            Participant,
            SchoolSupervisor,
            User,
        )

        tied: set[int] | None = None
        if not single:
            tied = set(Participant.objects.filter(competition=competition).values_list("user_id", flat=True))
            tied |= set(
                SchoolSupervisor.objects.filter(competition=competition).values_list("user_id", flat=True)
            )
            tied |= set(Membership.objects.filter(competition=competition).values_list("user_id", flat=True))

        expected: dict[str, set[int]] = {}
        for role in CompetitionRole.values:
            in_group = set(
                User.objects.filter(groups__name=role, is_active=True).values_list("pk", flat=True)
            )
            expected[role] = in_group if tied is None else in_group & tied

        # Profile domenowe dokładają się do grup, a nie zastępują ich: uczestnik bywa dopisany do
        # grupy i nie mieć profilu (konto założone przed rejestracją do zawodów), i odwrotnie.
        active = set(User.objects.filter(is_active=True).values_list("pk", flat=True))
        expected[CompetitionRole.PARTICIPANT] |= active & set(
            Participant.objects.filter(competition=competition).values_list("user_id", flat=True)
        )
        expected[CompetitionRole.SUPERVISOR] |= active & set(
            SchoolSupervisor.objects.filter(competition=competition).values_list("user_id", flat=True)
        )
        return expected

    def _report_missing_groups(self, competition: Competition) -> None:
        from apps.accounts.models import CompetitionRole, Membership, User

        for role in CompetitionRole.values:
            in_competition = set(
                Membership.objects.filter(competition=competition, role=role).values_list(
                    "user_id", flat=True
                )
            )
            in_group = set(
                User.objects.filter(pk__in=in_competition, groups__name=role).values_list("pk", flat=True)
            )
            outside = sorted(in_competition - in_group)
            if outside:
                self.stdout.write(
                    f"info   {competition.slug} / {role}: {len(outside)} członkostw bez grupy Django "
                    f"(rola działa po przełączeniu flagi, ale /cms/ wymaga grupy) – "
                    f"{self._emails(outside)}"
                )

    # --- naprawa ------------------------------------------------------------------------------
    def _grant(self, competition: Competition, role: str, user_ids: list[int]) -> int:
        """Dopisuje członkostwa przez ``grant_role`` – ten sam serwis, co rejestracja i panel.

        Własne ``Membership.objects.create`` byłoby drugą drogą do tej samej prawdy: serwis zapisuje
        **oba** zapisy roli (członkostwo i grupę Django), a kopia pilnująca tylko jednego z nich
        rozjeżdżałaby się przy pierwszej zmianie tamtego. Grupa jest tu i tak już nadana — wołanie
        jest idempotentne (``get_or_create``), więc nic to nie kosztuje.
        """
        from apps.accounts.models import User
        from apps.accounts.services import grant_role

        with transaction.atomic():
            users = list(User.objects.filter(pk__in=user_ids))
            for user in users:
                grant_role(user, role, competition=competition)
        self.stdout.write(self.style.SUCCESS(f"       dopisano {len(users)} członkostw ({role})."))
        return len(users)

    # --- wypisywanie --------------------------------------------------------------------------
    def _emails(self, user_ids: list[int]) -> str:
        from apps.accounts.models import User

        emails = list(
            User.objects.filter(pk__in=user_ids[:MAX_LISTED])
            .order_by("email")
            .values_list("email", flat=True)
        )
        text = ", ".join(emails)
        if len(user_ids) > MAX_LISTED:
            text += f", … (+{len(user_ids) - MAX_LISTED})"
        return text
