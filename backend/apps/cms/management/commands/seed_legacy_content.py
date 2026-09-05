"""``manage.py seed_legacy_content`` – treści przeniesione ze starej strony WordPressa.

Źródłem są pliki ``apps/cms/fixtures/legacy/*.md``: kopie treści opisanych w
``docs/import/stara-strona-inwentarz.md`` i ``docs/import/tresci/``. Komenda **nie redaguje**
ani jednego zdania organizatora – zmienia wyłącznie strukturę (nagłówek → blok ``heading``,
tabela dwukolumnowa → lista definicji, wyróżniona ramka → blok ``notice``); patrz
``apps.cms.legacy_markdown``.

Co powstaje:

- **strony opublikowane** (``ContentPage``): „O Olimpiadzie”, „Jak zacząć?”, „Terminarz
  i harmonogram”, „Kontakt”, „Dla nauczycieli i materiały”,
- **dokumenty opublikowane** (``DocumentPage``): RODO i standardy ochrony małoletnich – obie
  treści README starej strony oznacza jako demonstracyjne, więc dostają ramkę ostrzegawczą
  na górze i status w metryce. Bez załącznika: plików tych dokumentów stara strona nie ma
  (jedyne pliki binarne to PDF/DOCX regulaminu – ``docs/import/assets.md``),
- **szkice** (``live=False``): „Komitety” (szesnaście nazwisk do potwierdzenia przez organizatora)
  i „Partnerzy i sponsorzy” (kafle bez logotypów, z nieistniejącym „Uniwersytetem Kwantowym”).
  Szkic jest tu świadomym wyborem: obie strony da się obejrzeć w ``/cms/``, ale ``/komitety/``
  odpowiada 404, więc dane osobowe i sugerowane patronaty nie trafiają do sieci przez pomyłkę,
- **trzy aktualności** ze starego seedera – z datą dzisiejszą, bo oryginał nie miał ``post_date``
  (``docs/import/aktualnosci.md``), i z dopiskiem o przeniesieniu na końcu treści,
- **strona główna**: hasło, opis i sekcja „Jak zacząć w 3 krokach” z ``tresci/strona-glowna.md``.
  Kafli partnerów świadomie **nie** przenosimy – strona partnerów jest szkicem.

Czego komenda **nie** tworzy i dlaczego – strony, które w nowym portalu obsługują istniejące typy
albo widoki aplikacji: ``biezaca-edycja`` i ``harmonogram`` jako węzeł nadrzędny (terminy trzyma
``competitions.Stage``), ``dokumenty`` (``DocumentPage`` jest dzieckiem strony głównej),
``aktualnosci-edycji`` (drugi newsroom bez powodu), ``przepisy`` (jedno zdanie, miejsce w
regulaminie), ``olimpiady-miedzynarodowe`` (temat bezprzedmiotowy przed I edycją), ``zadania``
(``ProblemsPage``), ``poprzednie-edycje`` (``ArchiveIndexPage``), ``wyniki-*`` i
``finalisci-laureaci`` (``ResultsPage`` + snapshot publikacji), ``galeria`` (zero zdjęć),
``rejestracja``/``panel-*`` (widoki ``apps.web``).

Idempotencja: strony rozpoznajemy po slugu pod stroną główną i aktualizujemy zamiast tworzyć
duplikaty. Powtórny przebieg nadpisuje treść tą samą treścią – to narzędzie importujące, nie tryb
pracy redakcyjnej: poprawki wprowadzone później w ``/cms/`` zostaną skasowane, więc komendy nie
uruchamia się rutynowo po każdym deployu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from wagtail.models import Page
from wagtail.rich_text import RichText

from apps.cms.legacy_markdown import parse_markdown
from apps.cms.models import ContentPage, DocumentPage, HomePage, NewsIndexPage, NewsPage

#: ``…/apps/cms/management/commands/`` → ``…/apps/cms/fixtures/legacy/``.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "legacy"

#: Ramka nad treścią dokumentów, które README starej strony oznacza jako demonstracyjne.
DEMO_NOTICE = "Wersja demonstracyjna – treść wymaga zatwierdzenia prawnego przed publikacją produkcyjną."
DEMO_STATUS = "Treść demonstracyjna – do zatwierdzenia prawnego"
DOCUMENT_VERSION = "1.0"
DOCUMENT_DATE = date(2026, 7, 22)

HOME_TITLE = "Olimpiada Kwantowa"
HOME_HERO_TITLE = "Przyszłość ma naturę kwantową."
HOME_HERO_TEXT = (
    "<p>Ogólnopolska olimpiada dla uczniów szkół ponadpodstawowych, którzy chcą zrozumieć "
    "fizykę kwantową i tworzyć technologie jutra.</p>"
)
HOME_STEPS_TITLE = "Jak zacząć w 3 krokach"
HOME_STEPS = (
    ("Załóż konto", "Wypełnij formularz ucznia i potwierdź swój adres e-mail."),
    ("Rozwiąż zadania", "Pobierz arkusz, przygotuj rozwiązania i prześlij je w systemie."),
    ("Sprawdź wynik", "Oceny są anonimowe, a wynik znajdziesz bezpiecznie na swoim koncie."),
)

#: Dopisek na końcu każdej przeniesionej aktualności – czytelnik ma wiedzieć, skąd wzięła się treść.
NEWS_FOOTNOTE = "<p><em>Wpis przeniesiony ze starej strony.</em></p>"
NEWS = (
    (
        "otwieramy-i-edycje",
        "Otwieramy I edycję Olimpiady Kwantowej",
        "Zapraszamy uczniów szkół ponadpodstawowych z całej Polski. Rejestracja rusza 1 września 2026.",
    ),
    (
        "znamy-harmonogram-zawodow",
        "Znamy harmonogram zawodów",
        "Sprawdź terminy rejestracji, etapów szkolnych, okręgowych i finału.",
    ),
    (
        "materialy-przygotowawcze",
        "Materiały przygotowawcze już dostępne",
        "Rozpocznij przygotowania z wykładami i zestawami ćwiczeń opracowanymi przez komitet.",
    ),
)

#: Kolejność pozycji w pasku nawigacji = kolejność rodzeństwa w drzewie (``cms_menu`` sortuje po
#: ``path``). Slug spoza tej listy zostaje tam, gdzie stoi – menu opisuje tylko strony menu.
MENU_ORDER = (
    "o-olimpiadzie",
    "jak-zaczac",
    "aktualnosci",
    "zadania",
    "harmonogram",
    "regulamin",
    "archiwum",
    "wyniki",
    "kontakt",
)


@dataclass(frozen=True)
class LegacyPage:
    """Jedna strona do przeniesienia: plik źródłowy, typ docelowy i status publikacji."""

    slug: str
    title: str
    document: bool = False
    in_menu: bool = False
    publish: bool = True
    demo_notice: bool = False
    metadata: dict = field(default_factory=dict)


PAGES = (
    LegacyPage(slug="o-olimpiadzie", title="O Olimpiadzie", in_menu=True),
    LegacyPage(slug="jak-zaczac", title="Jak zacząć?", in_menu=True),
    LegacyPage(slug="harmonogram", title="Terminarz i harmonogram", in_menu=True),
    LegacyPage(slug="kontakt", title="Kontakt", in_menu=True),
    LegacyPage(slug="dla-nauczycieli", title="Dla nauczycieli i materiały"),
    LegacyPage(
        slug="rodo",
        title="RODO – klauzula informacyjna",
        document=True,
        demo_notice=True,
        metadata={
            "version_label": DOCUMENT_VERSION,
            "document_date": DOCUMENT_DATE,
            "status_label": DEMO_STATUS,
        },
    ),
    LegacyPage(
        slug="standardy-ochrony-maloletnich",
        title="Standardy ochrony małoletnich",
        document=True,
        demo_notice=True,
        metadata={
            "version_label": DOCUMENT_VERSION,
            "document_date": DOCUMENT_DATE,
            "status_label": DEMO_STATUS,
        },
    ),
    LegacyPage(slug="komitety", title="Komitety", publish=False),
    LegacyPage(slug="partnerzy", title="Partnerzy i sponsorzy", publish=False),
)


class Command(BaseCommand):
    help = "Przenosi treści starej strony Olimpiady Kwantowej do CMS-a. Idempotentne."

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        if not FIXTURES.is_dir():
            raise CommandError(f"Brak katalogu z treściami: {FIXTURES}.")

        self._seed_home(home)
        for spec in PAGES:
            self._seed_page(home, spec)
        self._seed_news(home)
        moved = self._order_menu(home)

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_legacy_content: {len(PAGES)} stron, {len(NEWS)} aktualności, "
                f"menu {'przestawione' if moved else 'bez zmian'}"
            )
        )

    # --- strona główna --------------------------------------------------------------------

    def _seed_home(self, home: HomePage) -> None:
        home.title = HOME_TITLE
        home.hero_title = HOME_HERO_TITLE
        home.hero_text = HOME_HERO_TEXT
        home.steps_title = HOME_STEPS_TITLE
        home.steps = [("step", {"title": title, "text": text}) for title, text in HOME_STEPS]
        home.save()
        home.save_revision().publish()
        self.stdout.write("strona główna: hasło, opis i sekcja kroków")

    # --- strony treści i dokumenty --------------------------------------------------------

    def _seed_page(self, home: HomePage, spec: LegacyPage) -> None:
        source = FIXTURES / f"{spec.slug}.md"
        if not source.exists():
            raise CommandError(f"Brak pliku źródłowego {source}.")
        intro, blocks = parse_markdown(source.read_text(encoding="utf-8"))
        if spec.demo_notice:
            # Ostrzeżenie stoi nad treścią, a nie w metryce obok wersji: czytelnik ma je zobaczyć,
            # zanim zacznie czytać zapisy, których nikt jeszcze prawnie nie zatwierdził.
            blocks.insert(0, ("notice", {"tone": "warning", "text": RichText(f"<p>{DEMO_NOTICE}</p>")}))

        model = DocumentPage if spec.document else ContentPage
        page = model.objects.child_of(home).filter(slug=spec.slug).first()
        created = page is None
        if created:
            page = model(title=spec.title, slug=spec.slug, live=spec.publish)
            home.add_child(instance=page)

        page.title = spec.title
        page.intro = intro
        page.body = blocks
        for name, value in spec.metadata.items():
            setattr(page, name, value)
        if spec.document:
            page.show_in_menus = spec.in_menu
        else:
            page.show_in_menu = spec.in_menu
        page.save()

        revision = page.save_revision()
        if spec.publish:
            revision.publish()
        status = "opublikowana" if spec.publish else "szkic"
        self.stdout.write(
            f"{'utworzono' if created else 'zaktualizowano'}: /{spec.slug}/ ({status}, {len(blocks)} bloków)"
        )

    # --- aktualności ----------------------------------------------------------------------

    def _seed_news(self, home: HomePage) -> None:
        index = NewsIndexPage.objects.child_of(home).first()
        if index is None:  # pragma: no cover - newsroom tworzy migracja cms.0002
            self.stderr.write("Brak newsroomu – pomijam aktualności.")
            return
        for slug, title, lead in NEWS:
            page = NewsPage.objects.child_of(index).filter(slug=slug).first()
            created = page is None
            if created:
                page = NewsPage(title=title, slug=slug, date=timezone.localdate())
                index.add_child(instance=page)
            page.title = title
            page.lead = lead
            page.body = [("paragraph", RichText(NEWS_FOOTNOTE))]
            page.save()
            page.save_revision().publish()
            self.stdout.write(f"{'utworzono' if created else 'zaktualizowano'}: aktualność {slug}")

    # --- kolejność menu -------------------------------------------------------------------

    def _order_menu(self, home: HomePage) -> bool:
        """Ustawia rodzeństwo strony głównej w kolejności ``MENU_ORDER``. Zwraca, czy ruszyliśmy drzewo.

        Przestawiamy tylko wtedy, gdy kolejność faktycznie się nie zgadza: ``move`` przepisuje
        ścieżki wszystkim potomkom przenoszonego węzła, a pod newsroomem wiszą aktualności.
        """
        children = {page.slug: page for page in Page.objects.child_of(home).order_by("path")}
        wanted = [slug for slug in MENU_ORDER if slug in children]
        current = [slug for slug in children if slug in set(wanted)]
        if current == wanted:
            return False

        previous: Page | None = None
        for slug in wanted:
            page = Page.objects.get(pk=children[slug].pk)
            if previous is None:
                page.move(home, pos="first-child")
            else:
                page.move(previous, pos="right")
            previous = Page.objects.get(pk=page.pk)
        return True
