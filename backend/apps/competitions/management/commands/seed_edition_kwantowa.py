"""``manage.py seed_edition_kwantowa`` – I edycja Olimpiady Kwantowej (2026/2027) w bazie.

Daty pochodzą ze starej strony (``docs/import/stara-strona-inwentarz.md``, sekcja 3.1) i są tam
zgodne w dwóch niezależnych miejscach: tabeli „Terminarz i harmonogram” oraz kaflach strony
głównej. Stara strona podaje jednak **jedną datę na etap**, a ``Stage`` wymaga pełnej osi czasu.
Brakujące terminy uzupełniamy jawną, stałą regułą – i tylko dlatego, że bez nich nie da się
utworzyć etapu; **wszystkie muszą zostać potwierdzone przez organizatora** (punkt 8 listy decyzji
w inwentarzu):

- otwarcie etapu: godzina 00:00 czasu polskiego dnia podanego jako początek etapu,
- oddanie rozwiązań: 23:59 czasu polskiego w dniu podanym jako data etapu,
- recenzje: 14 dni po terminie oddania,
- okno reklamacji: otwarcie 2 dni po terminie recenzji, zamknięcie 9 dni po nim.

Model etapów jest trójstopniowy (``ELIM`` → ``DISTRICT`` → ``FINAL``), zgodnie z motywem starej
strony i z modelem nowego portalu. Regulamin w § 10 opisuje **dwa** etapy plus rozmowę
kwalifikacyjną – tej sprzeczności komenda nie rozstrzyga i nie może rozstrzygnąć: to decyzja
właściciela, opisana w README („Import treści starej strony”).

Edycja **nie zostaje bieżąca**, dopóki nie poda się ``--make-current``. Domyślnie na środowisku
deweloperskim bieżąca zostaje edycja demonstracyjna z ``seed_demo`` (ma otwarty etap, uczestników
i zgłoszenia), a edycja kwantowa czeka obok – jej pierwszy etap otwiera się we wrześniu 2026.

Idempotencja: edycja jest rozpoznawana po ``year_label``, etap po parze (edycja, rodzaj). Komenda
nie przesuwa terminów istniejącego etapu – po utworzeniu oś czasu należy do koordynatora,
a nie do skryptu.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.competitions.models import Edition, Stage, StageKind
from apps.competitions.services import create_stage

EDITION_LABEL = "I edycja 2026/2027"

#: Terminy podajemy w strefie organizatora – deadline „7 listopada, 23:59” ma być tą godziną
#: w Warszawie także wtedy, gdy serwer stoi gdzie indziej. Do bazy trafi UTC (``USE_TZ``).
WARSAW = ZoneInfo("Europe/Warsaw")

#: Odstęp między terminem oddania a terminem recenzji.
REVIEW_AFTER_DEADLINE = timedelta(days=14)
#: Okno reklamacji względem terminu recenzji.
APPEAL_OPENS_AFTER_REVIEW = timedelta(days=2)
APPEAL_CLOSES_AFTER_REVIEW = timedelta(days=9)

#: (rodzaj, otwarcie, termin oddania) – reszta osi czasu z reguł powyżej.
STAGE_PLAN = (
    (StageKind.ELIM, (2026, 9, 1), (2026, 11, 7)),
    (StageKind.DISTRICT, (2026, 11, 8), (2027, 1, 16)),
    (StageKind.FINAL, (2027, 1, 17), (2027, 4, 10)),
)

#: Próg kwalifikacji: co najmniej jeden punkt. Regulamin progów nie podaje (odsyła do ZOZ,
#: który nie istnieje), a zero oznaczałoby kwalifikację każdego, kto oddał pustą kartkę.
MIN_POINTS = 1


def _opens(day: tuple[int, int, int]) -> datetime:
    return datetime(*day, 0, 0, tzinfo=WARSAW)


def _deadline(day: tuple[int, int, int]) -> datetime:
    return datetime(*day, 23, 59, tzinfo=WARSAW)


class Command(BaseCommand):
    help = "Tworzy edycję „I edycja 2026/2027” z trzema etapami wg harmonogramu starej strony."

    def add_arguments(self, parser):
        parser.add_argument(
            "--make-current",
            action="store_true",
            help="Ustaw tę edycję jako bieżącą (zdejmuje znacznik z dotychczasowej).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        edition, created = Edition.objects.get_or_create(year_label=EDITION_LABEL)
        self.stdout.write(f"edycja: {edition.year_label} [{'utworzono' if created else 'istnieje'}]")

        for kind, opens_day, deadline_day in STAGE_PLAN:
            self._ensure_stage(edition, kind, opens_day, deadline_day)

        if options["make_current"]:
            self._make_current(edition)
        else:
            current = Edition.objects.filter(is_current=True).exclude(pk=edition.pk).first()
            self.stdout.write(
                f"bieżąca edycja bez zmian: {current.year_label if current else 'brak'} "
                "(użyj --make-current, żeby przełączyć)"
            )

        self.stdout.write(
            self.style.SUCCESS(f"seed_edition_kwantowa: {edition.year_label}, etapy {edition.stages.count()}")
        )

    def _ensure_stage(self, edition: Edition, kind: str, opens_day, deadline_day) -> None:
        if Stage.objects.filter(edition=edition, kind=kind).exists():
            self.stdout.write(f"etap {kind}: istnieje – terminów nie ruszam")
            return
        deadline_at = _deadline(deadline_day)
        review_deadline_at = deadline_at + REVIEW_AFTER_DEADLINE
        stage = create_stage(
            edition=edition,
            kind=kind,
            opens_at=_opens(opens_day),
            deadline_at=deadline_at,
            review_deadline_at=review_deadline_at,
            appeal_window_opens_at=review_deadline_at + APPEAL_OPENS_AFTER_REVIEW,
            appeal_window_closes_at=review_deadline_at + APPEAL_CLOSES_AFTER_REVIEW,
            min_points=MIN_POINTS,
        )
        self.stdout.write(
            f"etap {kind}: utworzono {stage.opens_at.isoformat()} → {stage.deadline_at.isoformat()}"
        )

    def _make_current(self, edition: Edition) -> None:
        """Znacznik bieżącej edycji jest w bazie unikalny częściowym indeksem – najpierw go zdejmujemy."""
        Edition.objects.filter(is_current=True).exclude(pk=edition.pk).update(is_current=False)
        if not edition.is_current:
            edition.is_current = True
            edition.save(update_fields=["is_current"])
        self.stdout.write(self.style.WARNING(f"bieżąca edycja: {edition.year_label}"))
