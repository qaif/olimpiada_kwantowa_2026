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
  na górze i status w metryce,
- **PDF-y organizatora** z ``fixtures/legacy/pdf/`` przypięte do właściwych stron: regulamin
  (PDF przed plikiem źródłowym .docx z ``seed_regulamin``), RODO, standardy ochrony małoletnich
  i skład komitetów. To one są wersjami do wydruku i to z nich bierze się sekcja „Dokumenty
  do pobrania” na stronie głównej,
- **strona „Komitety” jest opublikowana**, bo skład komitetów wyszedł spod pióra organizatora
  jako podpisany PDF (``Sklad-komitetow-Olimpiady-Kwantowej.pdf``) – lista nazwisk nie jest już
  roboczą notatką do potwierdzenia, tylko oficjalnym dokumentem, a strona jest jego wersją
  czytelną w przeglądarce. Treść (nazwiska i zakresy odpowiedzialności) pochodzi z PDF-u,
- **szkic** (``live=False``): „Partnerzy i sponsorzy” – kafle bez logotypów, z nieistniejącym
  „Uniwersytetem Kwantowym”. Szkic jest tu świadomym wyborem: stronę da się obejrzeć w ``/cms/``,
  ale ``/partnerzy/`` odpowiada 404, więc sugerowane patronaty nie trafiają do sieci przez pomyłkę,
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
from wagtail.documents import get_document_model
from wagtail.models import Page
from wagtail.rich_text import RichText

from apps.cms.attachments import (
    LABEL_PDF,
    LABEL_SOURCE_DOCX,
    PDF_DIR,
    ensure_document,
    set_attachments,
)
from apps.cms.legacy_markdown import parse_markdown
from apps.cms.management.commands.seed_regulamin import DOCUMENT_TITLE as REGULAMIN_DOCX_TITLE
from apps.cms.management.commands.seed_regulamin import PAGE_SLUG as REGULAMIN_SLUG
from apps.cms.management.commands.seed_regulamin import PDF_DOCUMENT_TITLE as REGULAMIN_PDF_TITLE
from apps.cms.models import (
    ContentPage,
    ContentPageAttachment,
    DocumentPage,
    DocumentPageAttachment,
    HomePage,
    NewsIndexPage,
    NewsPage,
)

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
    "komitety",
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
    """Jedna strona do przeniesienia: plik źródłowy, typ docelowy i status publikacji.

    ``pdf``/``pdf_title`` opisują plik organizatora z ``fixtures/legacy/pdf/``, który ma zawisnąć
    przy stronie jako wersja do wydruku. Tytuł jest jawny, bo to on – a nie nazwa pliku – jest
    tożsamością dokumentu w bibliotece Wagtaila (patrz ``apps.cms.attachments``).
    """

    slug: str
    title: str
    document: bool = False
    in_menu: bool = False
    publish: bool = True
    demo_notice: bool = False
    metadata: dict = field(default_factory=dict)
    pdf: str = ""
    pdf_title: str = ""


PAGES = (
    LegacyPage(slug="o-olimpiadzie", title="O Olimpiadzie", in_menu=True),
    LegacyPage(
        slug="komitety",
        title="Komitety",
        in_menu=True,
        pdf="Sklad-komitetow-Olimpiady-Kwantowej.pdf",
        pdf_title="Skład komitetów Olimpiady Kwantowej (PDF)",
    ),
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
        pdf="Polityka-RODO-Olimpiada-Kwantowa.pdf",
        pdf_title="Polityka RODO Olimpiady Kwantowej (PDF)",
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
        pdf="Standardy-ochrony-maloletnich-Olimpiada-Kwantowa.pdf",
        pdf_title="Standardy ochrony małoletnich (PDF)",
    ),
    LegacyPage(slug="partnerzy", title="Partnerzy i sponsorzy", publish=False),
)

#: PDF regulaminu jest osobno: strona ``/regulamin/`` powstaje w ``seed_regulamin`` (treść pochodzi
#: z konwersji .docx, nie z pliku Markdown), a ta komenda dokłada do niej wyłącznie plik do wydruku.
REGULAMIN_PDF = "Regulamin-Olimpiady-Kwantowej.pdf"


class Command(BaseCommand):
    help = "Przenosi treści starej strony Olimpiady Kwantowej do CMS-a. Idempotentne."

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        if not FIXTURES.is_dir():
            raise CommandError(f"Brak katalogu z treściami: {FIXTURES}.")

        if not PDF_DIR.is_dir():
            raise CommandError(f"Brak katalogu z PDF-ami organizatora: {PDF_DIR}.")

        self._seed_home(home)
        for spec in PAGES:
            self._seed_page(home, spec)
        self._seed_regulamin_pdf(home)
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

        note = self._seed_attachment(page, spec)

        # Świeży obiekt z bazy: rewizja serializuje także wiersze załączników, a te dopisaliśmy
        # przez ORM już po ``page.save()`` – rewizja ze starego obiektu zdjęłaby je z publikacji.
        page = model.objects.get(pk=page.pk)
        revision = page.save_revision()
        if spec.publish:
            revision.publish()
        status = "opublikowana" if spec.publish else "szkic"
        self.stdout.write(
            f"{'utworzono' if created else 'zaktualizowano'}: /{spec.slug}/ "
            f"({status}, {len(blocks)} bloków{note})"
        )

    # --- pliki do pobrania ----------------------------------------------------------------

    def _seed_attachment(self, page, spec: LegacyPage) -> str:
        """Wgrywa PDF organizatora i przypina go do strony. Zwraca dopisek do komunikatu."""
        if not spec.pdf:
            return ""
        source = PDF_DIR / spec.pdf
        if not source.exists():
            raise CommandError(f"Brak pliku {source}.")
        document, action = ensure_document(spec.pdf_title, source)
        model = DocumentPageAttachment if spec.document else ContentPageAttachment
        set_attachments(page, model, [(document, LABEL_PDF)])
        return f", PDF #{document.pk} {action}"

    def _seed_regulamin_pdf(self, home: HomePage) -> None:
        """PDF do wydruku przy stronie ``/regulamin/`` – przed plikiem źródłowym .docx.

        Strona może jeszcze nie istnieć (``seed_regulamin`` bywa uruchamiany później); wtedy plik
        i tak trafia do biblioteki, a ``seed_regulamin._attachment_specs`` postawi go na stronie
        na pierwszym miejscu. Kolejność „PDF, potem DOCX” jest merytoryczna: podpisany PDF jest
        wersją, którą się drukuje i cytuje, .docx – materiałem redakcyjnym.
        """
        source = PDF_DIR / REGULAMIN_PDF
        if not source.exists():
            raise CommandError(f"Brak pliku {source}.")
        pdf, action = ensure_document(REGULAMIN_PDF_TITLE, source)

        page = DocumentPage.objects.child_of(home).filter(slug=REGULAMIN_SLUG).first()
        if page is None:
            self.stdout.write(f"regulamin: PDF #{pdf.pk} {action} (strona jeszcze nie istnieje)")
            return

        specs = [(pdf, LABEL_PDF)]
        docx = get_document_model().objects.filter(title=REGULAMIN_DOCX_TITLE).first()
        if docx is not None:
            specs.append((docx, LABEL_SOURCE_DOCX))
        set_attachments(page, DocumentPageAttachment, specs)

        page = DocumentPage.objects.get(pk=page.pk)
        page.save_revision().publish()
        self.stdout.write(f"zaktualizowano: /{REGULAMIN_SLUG}/ (PDF #{pdf.pk} {action}, {len(specs)} pliki)")

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
