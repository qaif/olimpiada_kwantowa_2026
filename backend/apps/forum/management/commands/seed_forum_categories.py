"""``manage.py seed_forum_categories`` – działy startowe forum: „Ogólne” i jeden na warsztat.

Prośba organizatora: uczestnik, który pierwszy raz otwiera ``/forum/``, ma zastać coś więcej niż
pustą listę działów. Dwa źródła, dwa różne działy:

- **„Ogólne”** – miejsce na pytania, które nie pasują pod żaden konkretny temat (rejestracja,
  terminy, sprawy organizacyjne). Zawsze powstaje, niezależnie od tego, czy strona „Warsztaty”
  w ogóle istnieje.
- **po jednym dziale na każdy warsztat** z tabeli harmonogramu (``apps.cms.workshops``). Warsztat
  jest tematem, o którym uczestnicy będą mieli pytania **zanim** się odbędzie i **po** nim – dział
  forum jest naturalnym miejscem tej rozmowy, a organizator nie ma zakładać go ręcznie za każdym
  razem, gdy ktoś dopisze wiersz do harmonogramu.

**Skąd nazwa działu.** Kolumna „Temat” tabeli warsztatów niesie prowadzącego w nawiasie
(``apps/cms/fixtures/legacy/warsztaty.md``: „Liczby zespolone (prowadzący: Tomasz Sowiński)”) – tak
wygląda kolumna, która w źródle ma tylko jedno pole tekstowe na temat i prowadzącego naraz. Dział
forum nie ma dziedziczyć tego dopisku w nazwie: „Liczby zespolone (prowadzący: Tomasz Sowiński)”
jako tytuł działu byłby razem nazwą tematu i metadaną, a lista działów ma być listą **tematów**.
Dopisek trafia więc do opisu, razem z terminem – to jest informacja, która na liście działów i tak
się przyda, tylko nie w tytule.

**Idempotencja jest tu warunkiem użyteczności, nie ozdobą** – tak samo, jak w
``seed_training_problems``. Organizator uruchamia tę komendę za każdym razem, gdy koordynator
dopisze warsztat do harmonogramu, a do tego czasu zdążył już przeredagować nazwy i opisy
poprzednio powstałych działów w panelu. Rozpoznanie po **slugu** (ta sama reguła, co
``apps.forum.services.save_category`` – slug nie zmienia się przy zmianie nazwy, bo stoi w adresie
działu) pozwala odróżnić „ten dział już jest” od „to nowy temat”, nawet gdy nazwa w międzyczasie
się zmieniła – i nic nie nadpisuje, gdy dział już istnieje.

**Numeracja 0, 10, 20, …** – nie 0, 1, 2 – z tego samego powodu, co numeracja stron w dokumencie:
koordynator, który zechce wstawić dział między dwoma warsztatami (np. „Pytania ogólne o warsztaty”
zaraz po pierwszym), ma gdzie to zrobić bez przenumerowania reszty.

Komenda **nie wymaga** włączonej flagi ``participant_forum`` – przygotowuje wyłącznie dane
(kategorie istnieją niezależnie od tego, czy adres ``/forum/`` odpowiada), więc organizator może ją
uruchomić przed właściwym włączeniem forum, tak samo jak ``seed_training_problems`` zakłada etap,
zanim ktokolwiek z niego skorzysta.
"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apps.cms.workshops import workshop_rows, workshops_page
from apps.forum.models import ForumCategory
from apps.forum.services import save_category
from apps.tenancy.context import competition_context

#: Dział, który powstaje zawsze – niezależnie od tego, czy strona „Warsztaty” w ogóle istnieje.
GENERAL_NAME = "Ogólne"
GENERAL_DESCRIPTION = "Pytania o Olimpiadę, rejestrację, terminy i sprawy organizacyjne."
GENERAL_ORDERING = 0

#: Pierwszy warsztat dostaje 10, drugi 20, … – odstęp zostawia koordynatorowi miejsce na wstawienie
#: własnego działu między dwoma warsztatami bez przenumerowania reszty.
WORKSHOP_ORDERING_STEP = 10

#: Dopisek prowadzącego w kolumnie „Temat” tabeli warsztatów (patrz docstring modułu). Nawias na
#: końcu tematu, niezależnie od tego, czy w środku stoi nazwisko, „i PCSS” czy „do ustalenia”.
LECTURER_SUFFIX = re.compile(r"\s*\(\s*prowadzący\s*:\s*(?P<lecturer>[^()]*)\)\s*$")


def split_topic(topic: str) -> tuple[str, str]:
    """Temat warsztatu → (nazwa bez dopisku prowadzącego, sam dopisek – pusty, gdy go nie ma).

    Osobna funkcja, testowalna wprost: reguła "dopisek w nawiasie na końcu nie jest częścią
    nazwy" ma jedno miejsce, a nie kopię wewnątrz ``handle()``.
    """
    match = LECTURER_SUFFIX.search(topic or "")
    if match is None:
        return (topic or "").strip(), ""
    return topic[: match.start()].strip(), match.group("lecturer").strip()


def workshop_description(row: dict, lecturer: str) -> str:
    """Opis działu: termin warsztatu i (jeśli jest) kto go prowadzi.

    Termin bierzemy w brzmieniu, w jakim podał go organizator (``row["date"]``), a nie z
    ``date_value`` – to on trafia na zaświadczenie o obecności (``apps.cms.workshops``) i ma
    brzmieć tak samo tutaj.
    """
    when = row.get("date", "")
    if row.get("time"):
        when = f"{when}, {row['time']}"
    parts = [f"Warsztat {when}".strip()]
    if lecturer:
        parts.append(f"prowadzący: {lecturer}")
    return " · ".join(parts) + ". Pytania i dyskusja do tego spotkania."


def category_slug(name: str) -> str:
    """Slug, jaki dostałby ten dział przy pierwszym założeniu – ta sama baza, co w
    ``apps.forum.services._free_slug``, bez gałęzi kolizji (tu porównujemy, nie zakładamy)."""
    return slugify(name)[:50] or "dzial"


class Command(BaseCommand):
    help = (
        "Zakłada startowe działy forum jednego konkursu: „Ogólne” i po jednym na każdy warsztat "
        "z tabeli harmonogramu (strona „Warsztaty”). Idempotentne – rozpoznaje istniejące działy "
        "po slugu i nie nadpisuje nazw ani opisów zmienionych w panelu."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition",
            dest="competition_slug",
            default="",
            help="Slug konkursu (wymagany, gdy w bazie jest więcej niż jeden konkurs).",
        )

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition_slug"))
        with competition_context(competition):
            created, skipped = self._seed(competition)
        self.stdout.write(
            self.style.SUCCESS(
                f"Konkurs {competition.slug}: utworzonych działów {created}, pominiętych {skipped}."
            )
        )

    # --- konkurs -------------------------------------------------------------------------------

    def _competition(self, slug: str):
        """Konkurs ze slugu, albo jedyny w instalacji. Wiele konkursów bez slugu jest błędem –
        zasianie działów cudzej olimpiadzie byłoby zmianą, której nikt nie zamawiał (ta sama
        ostrożność, co w ``seed_training_problems`` i ``scope_cms_access``)."""
        from apps.tenancy.models import Competition

        if slug:
            slug = slug.strip().lower()
            competition = Competition.objects.filter(slug=slug).first()
            if competition is None:
                raise CommandError(f"Nie ma konkursu o identyfikatorze „{slug}”.")
            return competition
        count = Competition.objects.count()
        if count == 0:
            raise CommandError("W bazie nie ma żadnego konkursu – nie ma czego zasiać.")
        if count > 1:
            raise CommandError(
                "W bazie jest więcej niż jeden konkurs – wskaż --competition <slug>, żeby "
                "wiadomo było, czyje forum zasiewamy."
            )
        return Competition.objects.get()

    # --- działy --------------------------------------------------------------------------------

    def _seed(self, competition) -> tuple[int, int]:
        created = skipped = 0
        general_created = self._ensure(
            competition, name=GENERAL_NAME, description=GENERAL_DESCRIPTION, ordering=GENERAL_ORDERING
        )
        created += general_created
        skipped += not general_created

        page = workshops_page(competition)
        rows = workshop_rows(page) if page is not None else []
        if not rows:
            self.stdout.write("Brak strony „Warsztaty” (albo bez wierszy z terminem) – tylko „Ogólne”.")

        for index, row in enumerate(rows, start=1):
            name, lecturer = split_topic(row["topic"])
            description = workshop_description(row, lecturer)
            ordering = index * WORKSHOP_ORDERING_STEP
            row_created = self._ensure(competition, name=name, description=description, ordering=ordering)
            created += row_created
            skipped += not row_created

        return created, skipped

    def _ensure(self, competition, *, name: str, description: str, ordering: int) -> bool:
        """Zakłada dział, gdy go jeszcze nie ma pod tym slugiem. Zwraca, czy właśnie powstał.

        Sprawdzenie **przed** wywołaniem ``save_category``: tamta funkcja przez
        ``_free_slug`` zawsze znajdzie *jakiś* wolny slug (dokładając „-2” przy kolizji), więc
        sama w sobie nie odpowiada na pytanie „czy ten dział już istnieje” – odpowiedziałaby
        „nie” za każdym razem i tworzyła nowy dział przy każdym uruchomieniu.
        """
        slug = category_slug(name)
        if ForumCategory.objects.for_competition(competition).filter(slug=slug).exists():
            self.stdout.write(f"  pomijam: „{name}” (dział „{slug}” już istnieje).")
            return False
        save_category(
            competition=competition,
            actor=None,
            name=name,
            description=description,
            ordering=ordering,
            is_open=True,
        )
        self.stdout.write(self.style.SUCCESS(f"  utworzony: „{name}” (dział „{slug}”)."))
        return True
