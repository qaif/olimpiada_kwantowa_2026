"""``manage.py scope_cms_access`` — zawęża dostęp do ``/cms/`` koordynatorom jednego konkursu.

Grupa ``cms:<slug>`` (``apps.cms.permissions``) **dokłada** uprawnienia: koordynator, który do niej
wejdzie, a zostanie w globalnej grupie ``coordinator``, dalej widzi strony i media wszystkich
konkursów, bo tamta grupa ma prawa na korzeniu drzewa i na korzeniu kolekcji (``cms.0003``).
Zawężenie polega więc na **odebraniu globalnej grupy** — a to jest czynność, która komuś coś
zabiera, więc nie dzieje się sama: ani przy zakładaniu konkursu, ani przy przełączaniu flagi.
Robi ją człowiek, tą komendą, po przeczytaniu listy z ``--dry-run``
(``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.5). Wzorzec jest ten sam, co ``check_memberships --fix``.

**Trzy odmowy, wszystkie twarde i wszystkie w kodzie, a nie w dokumentacji.**

1. **Konkurs #1.** ``--competition kwantowa`` jest odrzucane bez wyjątków: zestaw uprawnień grupy
   ``coordinator`` Olimpiady Kwantowej ma zostać identyczny (§ 0.2 punkt 9, § 0.5 punkt 20).
2. **Wyłączona flaga ``scoped_cms_permissions``.** Bez niej konkurs nie ma własnej kolekcji
   w torze wgrywania plików, więc koordynator po odebraniu globalnej grupy wgrywałby pliki do
   kolekcji, do której sam nie ma prawa. Kolejność jest jedna: najpierw flaga, potem komenda.
3. **Wyłączona flaga ``memberships_enforced``.** Przy niej wyłączonej o roli rozstrzyga **grupa
   Django** (``apps.accounts.services.has_role``), więc odebranie grupy ``coordinator`` nie
   zawęziłoby dostępu do ``/cms/``, tylko odebrało rolę koordynatora w całości — razem z panelem,
   wynikami i recenzjami. To jest różnica między „widzisz mniej” a „nie jesteś już koordynatorem”.

**Kogo komenda pomija.** Osobę, która jest koordynatorem także w **innym** konkursie, którego
odebranie globalnej grupy by dotknęło (bo tamten konkurs nie ma obu flag). Zgadywanie kończyłoby
się odcięciem człowieka od konkursu, o którym komenda nie była pytana; taka osoba jest wypisana
z powodem i nie zmienia się jej ani jeden wiersz.

Komenda **niczego nie kasuje poza przynależnością do grupy**: nie rusza członkostw, nie przenosi
mediów między kolekcjami i nie dotyka migracji ``cms.0003``.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import GROUP_COORDINATOR
from apps.cms.permissions import FEATURE, cms_group_name, ensure_cms_group
from apps.tenancy.models import Competition

#: Konkurs #1. Slug jest tu literałem celowo: to jest warunek ciągłości Olimpiady Kwantowej,
#: a nie parametr konfiguracji, który ktoś mógłby przestawić razem z resztą ustawień.
PROTECTED_SLUG = "kwantowa"


class Command(BaseCommand):
    help = (
        "Przenosi koordynatorów konkursu z globalnej grupy „coordinator” do grupy cms:<slug>, "
        "czyli zawęża ich dostęp w /cms/ do stron i mediów tego konkursu. --dry-run tylko wypisuje."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            required=True,
            help="Identyfikator konkursu (slug), którego koordynatorów zawężamy.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Wypisz, kto co straci, i nie zapisuj niczego.",
        )

    def handle(self, *args, **options):
        competition = self._competition(options["competition"])
        dry_run = options["dry_run"]

        # Wycofanie **po** wykonaniu całości, a nie pominięcie zapisów — ten sam wzorzec, co
        # ``create_competition --dry-run``: próba na sucho ma pokazać to, co zrobi baza (więzy,
        # unikalność, istniejące wiersze), a nie to, co pamiętamy o bazie.
        with transaction.atomic():
            moved, skipped = self._scope(competition, dry_run=dry_run)
            if dry_run:
                transaction.set_rollback(True)

        summary = f"Konkurs {competition.slug}: zawężonych koordynatorów {moved}, pominiętych {skipped}."
        if dry_run:
            self.stdout.write(self.style.WARNING(summary + " Próba na sucho — nic nie zapisano."))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

    # --- odmowy -------------------------------------------------------------------------------
    def _competition(self, slug: str) -> Competition:
        slug = (slug or "").strip().lower()
        if slug == PROTECTED_SLUG:
            raise CommandError(
                "Odmawiam zawężenia dostępu dla Konkursu #1 („kwantowa”). Zestaw uprawnień grupy "
                "„coordinator” Olimpiady Kwantowej ma zostać niezmieniony — to jest warunek "
                "ciągłości z docs/UNIWERSALNY-ETAP-2.md § 0.2 punkt 9, a nie zalecenie."
            )
        competition = Competition.objects.select_related("site").filter(slug=slug).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{slug}”.")
        if not competition.has_feature(FEATURE):
            raise CommandError(
                f"Konkurs „{slug}” ma wyłączoną flagę {FEATURE}, więc nie ma własnej kolekcji "
                "mediów w torze wgrywania plików. Najpierw flaga, potem ta komenda."
            )
        if not competition.has_feature("memberships_enforced"):
            raise CommandError(
                f"Konkurs „{slug}” ma wyłączoną flagę memberships_enforced, więc o roli "
                "koordynatora rozstrzyga globalna grupa Django. Odebranie jej nie zawęziłoby "
                "dostępu do /cms/, tylko odebrało rolę koordynatora w całości."
            )
        return competition

    # --- przeniesienie ------------------------------------------------------------------------
    def _scope(self, competition: Competition, *, dry_run: bool) -> tuple[int, int]:
        from apps.accounts.models import CompetitionRole, Membership

        group = ensure_cms_group(competition)
        global_group = Group.objects.filter(name=GROUP_COORDINATOR).first()
        rows = (
            Membership.objects.filter(competition=competition, role=CompetitionRole.COORDINATOR)
            .select_related("user")
            .order_by("user__email")
        )

        moved = 0
        skipped = 0
        for membership in rows:
            user = membership.user
            blocking = self._blocking_competitions(user, competition)
            if blocking:
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"{'pomijam':<11}{user.email}: koordynuje też {', '.join(blocking)} — tamten "
                        "konkurs nie ma obu flag, a odebranie globalnej grupy dotknęłoby i jego."
                    )
                )
                continue

            in_global = global_group is not None and user.groups.filter(pk=global_group.pk).exists()
            user.groups.add(group)
            if in_global:
                user.groups.remove(global_group)
            moved += 1
            verb = "zawęziłbym" if dry_run else "zawężam"
            loses = (
                "traci prawa do stron i mediów pozostałych konkursów"
                if in_global
                else "globalnej grupy i tak nie miał(a)"
            )
            self.stdout.write(
                f"{verb:<11}{user.email}: „{cms_group_name(competition)}” "
                f"zamiast „{GROUP_COORDINATOR}” — {loses}."
            )
        return moved, skipped

    def _blocking_competitions(self, user, competition: Competition) -> list[str]:
        """Konkursy, w których ta osoba koordynuje, a które ucierpiałyby po odebraniu grupy.

        Konkurs nie przeszkadza, jeżeli ma obie flagi — wtedy jego dostęp i tak stoi na własnej
        grupie ``cms:<slug>`` albo stanie po uruchomieniu tej komendy dla niego. Każdy inny
        przeszkadza, bo o roli albo o kolekcji rozstrzyga tam jeszcze grupa globalna.
        """
        from apps.accounts.models import CompetitionRole, Membership

        others = (
            Membership.objects.filter(user=user, role=CompetitionRole.COORDINATOR)
            .exclude(competition=competition)
            .select_related("competition")
            .order_by("competition__slug")
        )
        return [
            row.competition.slug
            for row in others
            if not (
                row.competition.has_feature(FEATURE) and row.competition.has_feature("memberships_enforced")
            )
        ]
