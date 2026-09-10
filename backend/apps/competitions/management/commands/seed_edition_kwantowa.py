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

Wyjątkiem jest **III etap (finał)**: organizator podał go jako zawody stacjonarne w Krakowie,
4–7 czerwca 2027. Etap trwający cztery dni w jednym miejscu nie ma „doby na oddanie pliku”, więc
jego godziny brzegowe są jawne (rozpoczęcie 4 czerwca o 9:00, zakończenie 7 czerwca o 18:00) i one
też **wymagają potwierdzenia** – organizator przekazał same daty dzienne.

Model etapów jest trójstopniowy (``ELIM`` → ``DISTRICT`` → ``FINAL``), zgodnie z motywem starej
strony i z modelem nowego portalu. Regulamin w § 10 opisuje **dwa** etapy plus rozmowę
kwalifikacyjną – tej sprzeczności komenda nie rozstrzyga i nie może rozstrzygnąć: to decyzja
właściciela, opisana w README („Import treści starej strony”).

Edycja **nie zostaje bieżąca**, dopóki nie poda się ``--make-current``. Domyślnie na środowisku
deweloperskim bieżąca zostaje edycja demonstracyjna z ``seed_demo`` (ma otwarty etap, uczestników
i zgłoszenia), a edycja kwantowa czeka obok – jej pierwszy etap otwiera się we wrześniu 2026.

Idempotencja: edycja jest rozpoznawana po ``year_label``, etap po parze (edycja, rodzaj). Komenda
domyślnie **nie przesuwa** terminów istniejącego etapu – po utworzeniu oś czasu należy do
koordynatora, a nie do skryptu.

``--sync-dates`` jest wyjątkiem od tej zasady i istnieje, bo organizator zmienił termin finału już
po utworzeniu edycji (III etap: 4–7 czerwca 2027 w Krakowie zamiast 10 kwietnia 2027 w Warszawie).
Bez tej flagi jedyną drogą byłoby ręczne poprawienie czterech dat w panelu na każdym środowisku –
czyli dokładnie ta operacja, przy której terminy zaczynają się różnić między devem, stagingiem
i produkcją. Flaga jest jawna i domyślnie wyłączona: przesunięcie terminu otwartego etapu ma być
decyzją, a nie skutkiem ubocznym wdrożenia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.competitions.models import Edition, Stage, StageFormat, StageKind
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

#: Domyślne godziny brzegowe etapu zdalnego: otwarcie o północy, oddanie o 23:59 czasu polskiego.
DEFAULT_OPENS_TIME = (0, 0)
DEFAULT_DEADLINE_TIME = (23, 59)

#: Otwarcie rejestracji uczestników I edycji – 8 września 2026, północ czasu polskiego. Data
#: pochodzi od organizatora i jest **wartością początkową świeżej instalacji**, dokładnie tak samo
#: jak nazwy etapów: po utworzeniu edycji okno rejestracji należy do koordynatora, a ``--sync-dates``
#: go nie rusza (ta flaga dotyczy osi czasu etapów, nie ustawień edycji). Bez tej wartości serwis
#: postawiony przed wrześniem przyjmowałby konta od pierwszego dnia, choć strona zapowiada start.
REGISTRATION_OPENS = datetime(2026, 9, 8, 0, 0, tzinfo=WARSAW)


@dataclass(frozen=True)
class StagePlan:
    """Jeden etap w planie edycji: rodzaj, oś czasu i miejsce zawodów.

    Godziny są jawne, bo od finału przestały być regułą: III etap jest zawodami **stacjonarnymi**
    w Krakowie i trwa cztery dni (4–7 czerwca 2027), więc jego otwarcie to godzina rozpoczęcia
    zawodów (9:00 pierwszego dnia), a „deadline” – zakończenie ostatniego (18:00). Dla etapów
    zdalnych zostają dotychczasowe 00:00 i 23:59.
    """

    kind: str
    opens: tuple[int, int, int]
    deadline: tuple[int, int, int]
    opens_time: tuple[int, int] = DEFAULT_OPENS_TIME
    deadline_time: tuple[int, int] = DEFAULT_DEADLINE_TIME
    location: str = ""
    format: str = StageFormat.SUBMISSIONS
    # Nazwa, jaką etap dostaje przy utworzeniu. Regulamin numeruje etapy („Etap I/II/III”), więc
    # portal ma je podpisywać tak samo, a nie słownikowymi „Eliminacje/Okręgowy/Finał”. Po
    # utworzeniu nazwa należy do koordynatora – ``--sync-dates`` jej nie przestawia.
    name: str = ""


#: Plan etapów I edycji. Terminy I i II etapu pochodzą ze starej strony i pozostają bez zmian;
#: finał ma termin i miejsce przekazane przez organizatora we wrześniu 2026.
#:
#: II etap ma formę **rozmowy kwalifikacyjnej online** – tak opisuje go regulamin (§ 10) i tak
#: rozstrzygnął organizator. Forma jest tu wyłącznie wartością początkową świeżej instalacji:
#: ``--sync-dates`` jej nie przestawia, bo po utworzeniu etapu forma należy do koordynatora tak
#: samo, jak reszta jego parametrów.
STAGE_PLAN = (
    StagePlan(StageKind.ELIM, (2026, 9, 1), (2026, 11, 7), name="Etap I"),
    StagePlan(
        StageKind.DISTRICT,
        (2026, 11, 8),
        (2027, 1, 16),
        format=StageFormat.INTERVIEW,
        name="Etap II",
    ),
    StagePlan(
        StageKind.FINAL,
        opens=(2027, 6, 4),
        deadline=(2027, 6, 7),
        opens_time=(9, 0),
        deadline_time=(18, 0),
        location="Kraków",
        name="Etap III",
    ),
)

#: Próg kwalifikacji: co najmniej jeden punkt. Regulamin progów nie podaje (odsyła do ZOZ,
#: który nie istnieje), a zero oznaczałoby kwalifikację każdego, kto oddał pustą kartkę.
MIN_POINTS = 1


def _moment(day: tuple[int, int, int], time: tuple[int, int]) -> datetime:
    return datetime(*day, *time, tzinfo=WARSAW)


def _timeline(plan: StagePlan) -> dict:
    """Pełna oś czasu etapu wyliczona z planu – jedno miejsce dla tworzenia i dla ``--sync-dates``.

    Gdyby reguła („+14 dni na recenzje, potem okno reklamacji”) była zapisana dwa razy, obie ścieżki
    dawałyby po zmianie planu dwa różne wyniki, a różnicy nie widać w bazie – widać ją dopiero
    wtedy, gdy uczestnik nie może złożyć reklamacji.
    """
    deadline_at = _moment(plan.deadline, plan.deadline_time)
    review_deadline_at = deadline_at + REVIEW_AFTER_DEADLINE
    return {
        "opens_at": _moment(plan.opens, plan.opens_time),
        "deadline_at": deadline_at,
        "review_deadline_at": review_deadline_at,
        "appeal_window_opens_at": review_deadline_at + APPEAL_OPENS_AFTER_REVIEW,
        "appeal_window_closes_at": review_deadline_at + APPEAL_CLOSES_AFTER_REVIEW,
    }


class Command(BaseCommand):
    help = "Tworzy edycję „I edycja 2026/2027” z trzema etapami wg harmonogramu organizatora."

    def add_arguments(self, parser):
        parser.add_argument(
            "--make-current",
            action="store_true",
            help="Ustaw tę edycję jako bieżącą (zdejmuje znacznik z dotychczasowej).",
        )
        parser.add_argument(
            "--sync-dates",
            action="store_true",
            help=(
                "Przestaw terminy i miejsce istniejących etapów na wartości z planu w tej komendzie. "
                "Bez flagi terminy istniejących etapów zostają nietknięte."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # ``defaults`` działa wyłącznie przy tworzeniu – istniejąca edycja zachowuje okno
        # rejestracji ustawione w panelu, nawet jeżeli koordynator przesunął je albo wyłączył.
        edition, created = Edition.objects.get_or_create(
            year_label=EDITION_LABEL,
            defaults={"registration_enabled": True, "registration_opens_at": REGISTRATION_OPENS},
        )
        self.stdout.write(f"edycja: {edition.year_label} [{'utworzono' if created else 'istnieje'}]")
        if created:
            self.stdout.write(f"rejestracja uczestników: otwarcie {REGISTRATION_OPENS.isoformat()}")

        for plan in STAGE_PLAN:
            self._ensure_stage(edition, plan, sync_dates=options["sync_dates"])

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

    def _ensure_stage(self, edition: Edition, plan: StagePlan, *, sync_dates: bool) -> None:
        timeline = _timeline(plan)
        stage = Stage.objects.filter(edition=edition, kind=plan.kind).first()
        if stage is not None:
            if not sync_dates:
                self.stdout.write(f"etap {plan.kind}: istnieje – terminów nie ruszam (--sync-dates)")
                return
            self._sync_stage(stage, plan, timeline)
            return

        stage = create_stage(
            edition=edition,
            kind=plan.kind,
            name=plan.name,
            location=plan.location,
            format=plan.format,
            min_points=MIN_POINTS,
            **timeline,
        )
        self.stdout.write(
            f"etap {plan.kind}: utworzono {stage.opens_at.isoformat()} → {stage.deadline_at.isoformat()}"
            f"{f' ({plan.location})' if plan.location else ''}"
        )

    def _sync_stage(self, stage: Stage, plan: StagePlan, timeline: dict) -> None:
        """Przestawia oś czasu i miejsce istniejącego etapu na wartości z planu.

        **Formy etapu ta metoda nie rusza** i nie ma tego robić: forma decyduje o tym, co uczestnik
        w etapie robi, więc jej przestawienie wdrożeniem unieważniłoby oddane prace albo zapisy na
        rozmowy. Zmienia ją wyłącznie koordynator z panelu, pod bramką ``STAGE_FORMAT_LOCKED``.

        ``full_clean`` przed zapisem, bo przesunięcie jednej daty potrafi odwrócić kolejność
        w łańcuchu (deadline przed otwarciem, okno reklamacji przed recenzjami) – wtedy komenda ma
        stanąć z czytelnym błędem, a nie zostawić etap, którego baza i tak nie przyjmie.
        """
        changes = {name: value for name, value in timeline.items() if getattr(stage, name) != value}
        if stage.location != plan.location:
            changes["location"] = plan.location
        if not changes:
            self.stdout.write(f"etap {plan.kind}: terminy już zgodne z planem")
            return

        for name, value in changes.items():
            setattr(stage, name, value)
        stage.full_clean()
        stage.save(update_fields=list(changes))
        self.stdout.write(
            self.style.WARNING(
                f"etap {plan.kind}: przestawiono {', '.join(sorted(changes))} → "
                f"{stage.opens_at.isoformat()} … {stage.deadline_at.isoformat()}"
            )
        )

    def _make_current(self, edition: Edition) -> None:
        """Znacznik bieżącej edycji jest w bazie unikalny częściowym indeksem – najpierw go zdejmujemy."""
        Edition.objects.filter(is_current=True).exclude(pk=edition.pk).update(is_current=False)
        if not edition.is_current:
            edition.is_current = True
            edition.save(update_fields=["is_current"])
        self.stdout.write(self.style.WARNING(f"bieżąca edycja: {edition.year_label}"))
