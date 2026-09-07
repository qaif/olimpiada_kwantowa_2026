"""``manage.py seed_legacy_content`` – treści przeniesione ze starej strony WordPressa.

Źródłem są pliki ``apps/cms/fixtures/legacy/*.md``: kopie treści opisanych w
``docs/import/stara-strona-inwentarz.md`` i ``docs/import/tresci/``. Komenda **nie redaguje**
ani jednego zdania organizatora – zmienia wyłącznie strukturę (nagłówek → blok ``heading``,
tabela dwukolumnowa → lista definicji, wyróżniona ramka → blok ``notice``); patrz
``apps.cms.legacy_markdown``.

Co powstaje:

- **strony opublikowane** (``ContentPage``): „O Olimpiadzie”, „Jak zacząć?”, „Terminarz
  i harmonogram”, „Kontakt”, „Dla nauczycieli i materiały”,
- **sekcja dokumentów** (``DocumentIndexPage`` pod ``/dokumenty/``) z kompletem dokumentów
  organizatora jako dziećmi. Sekcja jest jedną pozycją menu z listą rozwijaną; stare adresy
  jednosegmentowe (``/regulamin/``, ``/rodo/``…) zostają jako trwałe przekierowania,
- **dokumenty opublikowane** (``DocumentPage``): polityka RODO, standardy ochrony małoletnich
  i skład komitetów. Wszystkie trzy treści są przepisane z podpisanych PDF-ów organizatora
  (``fixtures/legacy/pdf-text/``), sekcja po sekcji, więc nie są już „wersją demonstracyjną”
  ze starego WordPressa: metryka mówi, z jakiego eksportu pochodzą, a ramka na górze wskazuje
  PDF jako wersję źródłową,
- **PDF-y organizatora** z ``fixtures/legacy/pdf/`` przypięte do właściwych stron: RODO, standardy
  ochrony małoletnich i skład komitetów. To one są wersjami do wydruku i to z nich bierze się
  sekcja „Dokumenty do pobrania” na stronie głównej. Regulaminu tu **nie** ma: jego PDF, .docx
  i tekst strony to trzy postacie jednej wersji dokumentu i wgrywa je razem ``seed_regulamin``
  (rozdzielenie kończyło się stroną z nowego .docx i plikiem do pobrania ze starego PDF-u),
- **„Skład komitetów” jest dokumentem, a nie stroną treści**, bo lista nazwisk wyszła spod pióra
  organizatora jako podpisany PDF (``Sklad-komitetow-Olimpiady-Kwantowej.pdf``) – nie jest roboczą
  notatką do potwierdzenia, tylko oficjalnym dokumentem, a strona jest jego wersją czytelną
  w przeglądarce. Treść (nazwiska i zakresy odpowiedzialności) pochodzi z PDF-u. Baza sprzed tej
  zmiany ma stronę ``ContentPage`` o tym slugu; komenda ją kasuje i tworzy dokument na nowo,
  bo typu strony nie da się zmienić w miejscu (dwie tabele),
- **strona partnerów** (``PartnersPage`` pod ``/partnerzy/``) – opublikowana, ale z **pustą** listą
  partnerów. Stara strona wymieniała trzy nazwy: „Ministerstwo Edukacji”, „Uniwersytet Kwantowy”
  (instytucja nieistniejąca) i „Polskie Towarzystwo Fizyczne”, żadnej z potwierdzonym patronatem.
  Poprzedni import zostawiał je w treści i chował całą stronę jako szkic (404); to broniło sieci
  przed zmyśloną nazwą, ale kosztowało zaproszenie do współpracy, którego nie było gdzie
  przeczytać. Teraz jest odwrotnie: strona żyje, sekcja „Zostań partnerem” działa, a lista
  partnerów zaczyna się pusta i wypełnia ją redakcja w ``/cms/`` po podpisaniu umów. Nazwy ze
  starej strony zostają w inwentarzu (``docs/import/tresci/partnerzy.md``) i nigdzie indziej,
- **trzy aktualności** ze starego seedera – z datą dzisiejszą, bo oryginał nie miał ``post_date``
  (``docs/import/aktualnosci.md``), i z dopiskiem o przeniesieniu na końcu treści,
- **strona główna**: hasło, opis i sekcja „Jak zacząć w 3 krokach” z ``tresci/strona-glowna.md``.
  Kafli partnerów świadomie **nie** przenosimy: pas logotypów na stronie głównej rysuje się sam
  z wpisów ``PartnersPage``, więc pojawi się dopiero z pierwszym potwierdzonym partnerem.

Czego komenda **nie** tworzy i dlaczego – strony, które w nowym portalu obsługują istniejące typy
albo widoki aplikacji: ``biezaca-edycja`` i ``harmonogram`` jako węzeł nadrzędny (terminy trzyma
``competitions.Stage``), ``aktualnosci-edycji`` (drugi newsroom bez powodu), ``przepisy``
(jedno zdanie, miejsce w regulaminie), ``olimpiady-miedzynarodowe`` (temat bezprzedmiotowy
przed I edycją), ``zadania``
(``ProblemsPage``), ``poprzednie-edycje`` (``ArchiveIndexPage``), ``wyniki-*`` i
``finalisci-laureaci`` (``ResultsPage`` + snapshot publikacji), ``galeria`` (zero zdjęć),
``rejestracja``/``panel-*`` (widoki ``apps.web``).

Idempotencja: strony rozpoznajemy po slugu (dokumenty – w sekcji ``/dokumenty/``, a gdy ich tam
jeszcze nie ma, pod stroną główną, skąd je przenosimy) i aktualizujemy zamiast tworzyć duplikaty.
Komenda daje ten sam wynik na świeżej bazie i na produkcyjnej sprzed wydzielenia sekcji.
Powtórny przebieg nadpisuje treść tą samą treścią – to narzędzie importujące, nie tryb
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

from apps.cms.attachments import LABEL_PDF, PDF_DIR, ensure_document, set_attachments
from apps.cms.legacy_markdown import parse_markdown
from apps.cms.models import (
    ContentPage,
    ContentPageAttachment,
    DocumentIndexPage,
    DocumentPage,
    DocumentPageAttachment,
    HomePage,
    NewsIndexPage,
    NewsPage,
    PartnersPage,
    SiteSettings,
)
from apps.cms.site_tree import INDEX_SLUG, ensure_document_index, ensure_redirect, take_document_page

#: ``…/apps/cms/management/commands/`` → ``…/apps/cms/fixtures/legacy/``.
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "legacy"

#: Ramka nad treścią dokumentów przepisanych z PDF-u organizatora: skąd wzięła się treść i który
#: plik rozstrzyga spór o brzmienie. Ton informacyjny, nie ostrzegawczy – to nie jest zastrzeżenie
#: do treści, tylko wskazanie wersji źródłowej.
SOURCE_NOTICE = (
    "Treść odpowiada dokumentowi organizatora z 7 września 2026 r. "
    "Wersja do pobrania (PDF) jest wersją źródłową."
)
SOURCE_STATUS = "Dokument organizatora (Fundacja Quantum AI), eksport z 7 września 2026"
#: Metryka opisuje **eksport**, a nie wersję dokumentu: numer wersji („1.0 z 22 lipca 2026 r.”)
#: stoi w ostatniej sekcji obu dokumentów, a data z nagłówka PDF-u to data przekazania pliku.
DOCUMENT_VERSION = ""  # numer wersji nie występuje w metryce PDF; datę eksportu niesie document_date
DOCUMENT_DATE = date(2026, 9, 7)

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

PARTNERS_SLUG = "partnerzy"
PARTNERS_TITLE = "Partnerzy"
PARTNERS_CTA_TITLE = "Zostań partnerem"
#: Zaproszenie do współpracy. Ostatnie zdanie nie jest ozdobnikiem: powtarza § 22 ust. 3
#: Regulaminu (sponsorzy i partnerzy nie mają wpływu na zadania, ocenę ani wyniki), czyli
#: odpowiada na pytanie, które przy stronie partnerów zadaje sobie każdy uczestnik i nauczyciel.
PARTNERS_CTA_BODY = (
    "<p>Olimpiadę Kwantową organizuje Fundacja Quantum AI. Zapraszamy uczelnie, instytuty, "
    "firmy technologiczne i instytucje publiczne do współpracy: patronat, wsparcie merytoryczne, "
    "nagrody dla laureatów i finansowanie finału. Zgodnie z Regulaminem partnerzy i sponsorzy "
    "nie mają wpływu na treść zadań, ocenę prac ani wyniki.</p>"
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
    INDEX_SLUG,
    "archiwum",
    "wyniki",
    # „Partnerzy” tuż przed „Kontaktem”: obie pozycje odpowiadają na pytanie „kto za tym stoi
    # i jak się z nimi skontaktować”, a strona partnerów sama kończy się zaproszeniem do pisania.
    PARTNERS_SLUG,
    "kontakt",
)

#: Kolejność dokumentów w sekcji ``/dokumenty/`` – ta sama na stronie-spisie, w rozwijanej pozycji
#: menu i w sekcji „Dokumenty do pobrania” na stronie głównej (wszystkie trzy sortują po ``path``).
#: Regulamin pierwszy, bo to on rozstrzyga przebieg zawodów; skład komitetów ostatni, bo jest
#: informacją o ludziach, a nie zbiorem zasad.
DOCUMENT_ORDER = (
    "regulamin",
    "rodo",
    "standardy-ochrony-maloletnich",
    "komitety",
)

#: Adresy sprzed przeniesienia dokumentów pod ``/dokumenty/``. Wiszą w pismach do szkół i w indeksach
#: wyszukiwarek, więc zostają jako trwałe (301) przekierowania – patrz ``apps.cms.site_tree``.
LEGACY_DOCUMENT_PATHS = (
    "/regulamin/",
    "/rodo/",
    "/standardy-ochrony-maloletnich/",
    "/komitety/",
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
    source_notice: bool = False
    metadata: dict = field(default_factory=dict)
    pdf: str = ""
    pdf_title: str = ""


PAGES = (
    LegacyPage(slug="o-olimpiadzie", title="O Olimpiadzie", in_menu=True),
    LegacyPage(
        slug="komitety",
        title="Skład komitetów",
        document=True,
        source_notice=True,
        metadata={
            "version_label": DOCUMENT_VERSION,
            "document_date": DOCUMENT_DATE,
            "status_label": SOURCE_STATUS,
        },
        pdf="Sklad-komitetow-Olimpiady-Kwantowej.pdf",
        pdf_title="Skład komitetów Olimpiady Kwantowej (PDF)",
    ),
    LegacyPage(slug="jak-zaczac", title="Jak zacząć?", in_menu=True),
    LegacyPage(slug="harmonogram", title="Harmonogram", in_menu=True),
    LegacyPage(slug="kontakt", title="Kontakt", in_menu=True),
    LegacyPage(slug="dla-nauczycieli", title="Dla nauczycieli i materiały"),
    LegacyPage(
        slug="rodo",
        title="Polityka RODO Olimpiady Kwantowej",
        document=True,
        source_notice=True,
        metadata={
            "version_label": DOCUMENT_VERSION,
            "document_date": DOCUMENT_DATE,
            "status_label": SOURCE_STATUS,
        },
        pdf="Polityka-RODO-Olimpiada-Kwantowa.pdf",
        pdf_title="Polityka RODO Olimpiady Kwantowej (PDF)",
    ),
    LegacyPage(
        slug="standardy-ochrony-maloletnich",
        title="Standardy ochrony małoletnich Olimpiady Kwantowej",
        document=True,
        source_notice=True,
        metadata={
            "version_label": DOCUMENT_VERSION,
            "document_date": DOCUMENT_DATE,
            "status_label": SOURCE_STATUS,
        },
        pdf="Standardy-ochrony-maloletnich-Olimpiada-Kwantowa.pdf",
        pdf_title="Standardy ochrony małoletnich (PDF)",
    ),
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

        if not PDF_DIR.is_dir():
            raise CommandError(f"Brak katalogu z PDF-ami organizatora: {PDF_DIR}.")

        index, index_created = ensure_document_index(home)
        if index_created:
            self.stdout.write("utworzono sekcję: /dokumenty/")

        self._seed_home(home)
        self._drop_legacy_komitety_page(home)
        for spec in PAGES:
            self._seed_page(home, index, spec)
        self._seed_partners(home)
        self._seed_news(home)
        moved = self._order_children(home, MENU_ORDER)
        # Przestawienie rodzeństwa strony głównej przepisało ``path`` także sekcji dokumentów,
        # a ``child_of`` czyta ścieżkę z obiektu – bez odświeżenia szukalibyśmy dzieci pod
        # adresem, którego już nie ma.
        index = DocumentIndexPage.objects.get(pk=index.pk)
        self._order_children(index, DOCUMENT_ORDER)
        redirects = self._seed_redirects(index)

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_legacy_content: {len(PAGES) + 1} stron, {len(NEWS)} aktualności, "
                f"{redirects} przekierowań, menu {'przestawione' if moved else 'bez zmian'}"
            )
        )

    # --- konwersja składu komitetów -------------------------------------------------------

    def _drop_legacy_komitety_page(self, home: HomePage) -> None:
        """Usuwa „Komitety” w postaci ``ContentPage`` – ten sam slug wraca niżej jako dokument.

        Skład komitetów jest podpisanym PDF-em organizatora, więc należy do sekcji dokumentów
        i ma mieć ich układ: metrykę, spis sekcji z kotwic i ramkę wskazującą wersję źródłową.
        ``ContentPage`` i ``DocumentPage`` to dwie różne tabele, więc typu strony nie da się
        zmienić w miejscu – zostaje skasowanie i utworzenie na nowo pod tym samym slugiem.
        Treść nie ginie: pochodzi z pliku ``fixtures/legacy/komitety.md``, a PDF zostaje
        w bibliotece Wagtaila (rozpoznawany po tytule) i wraca na nową stronę.
        """
        page = ContentPage.objects.descendant_of(home).filter(slug="komitety").first()
        if page is None:
            return
        page.delete()
        self.stdout.write("konwersja: „Komitety” przestają być stroną treści, powstaje dokument")

    # --- przekierowania ze starych adresów ------------------------------------------------

    def _seed_redirects(self, index) -> int:
        """Trwałe przekierowania ``/regulamin/`` → ``/dokumenty/regulamin/`` itd."""
        created = 0
        for old_path in LEGACY_DOCUMENT_PATHS:
            slug = old_path.strip("/")
            page = DocumentPage.objects.child_of(index).filter(slug=slug).first()
            if page is None:
                self.stderr.write(f"brak dokumentu {slug} – pomijam przekierowanie z {old_path}")
                continue
            if ensure_redirect(old_path, page):
                created += 1
        return created

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

    def _seed_page(self, home: HomePage, index, spec: LegacyPage) -> None:
        source = FIXTURES / f"{spec.slug}.md"
        if not source.exists():
            raise CommandError(f"Brak pliku źródłowego {source}.")
        # Tabela → ``<dl>`` tylko w dokumentach: tam komórki są całymi zdaniami („Cel | Podstawa”
        # w polityce RODO). Terminarz na stronie treści ma po dwa słowa w komórce i czyta się
        # lepiej jako akapit „Rejestracja — 1 września…” niż jako karta z nagłówkami kolumn.
        intro, blocks = parse_markdown(source.read_text(encoding="utf-8"), definition_lists=spec.document)
        if spec.source_notice:
            # Ramka stoi nad treścią, a nie w metryce obok wersji: czytelnik ma wiedzieć, co czyta
            # i który plik rozstrzyga, zanim zacznie czytać zapisy dokumentu.
            blocks.insert(0, ("notice", {"tone": "info", "text": RichText(f"<p>{SOURCE_NOTICE}</p>")}))

        # Dokumenty mieszkają w sekcji ``/dokumenty/``, reszta – bezpośrednio pod stroną główną.
        # ``take_document_page`` przenosi dokument spod strony głównej, jeśli baza pamięta jeszcze
        # układ sprzed wydzielenia sekcji; na świeżej bazie zwraca ``None`` i strona powstaje niżej.
        model = DocumentPage if spec.document else ContentPage
        parent = index if spec.document else home
        moved = False
        if spec.document:
            page, moved = take_document_page(model, index, home, spec.slug)
        else:
            page = model.objects.child_of(home).filter(slug=spec.slug).first()
        created = page is None
        if created:
            page = model(title=spec.title, slug=spec.slug, live=spec.publish)
            parent.add_child(instance=page)

        page.title = spec.title
        page.intro = intro
        page.body = blocks
        for name, value in spec.metadata.items():
            setattr(page, name, value)
        if spec.document:
            # Dokument nie jest osobną pozycją paska nawigacji: rozwijana sekcja „Dokumenty”
            # czyta dzieci sekcji, a nie znacznik ``show_in_menus``.
            page.show_in_menus = False
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
        if moved:
            note += ", przeniesiono pod /dokumenty/"
        self.stdout.write(
            f"{'utworzono' if created else 'zaktualizowano'}: {page.url} "
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

    # --- partnerzy ------------------------------------------------------------------------

    def _seed_partners(self, home: HomePage) -> None:
        """Strona ``/partnerzy/`` jako ``PartnersPage`` – opublikowana, z pustą listą partnerów.

        Baza sprzed tej zmiany ma pod tym slugiem ``ContentPage`` (szkic z trzema nazwami ze
        starej strony). ``ContentPage`` i ``PartnersPage`` to dwie różne tabele, więc typu strony
        nie da się zmienić w miejscu – zostaje skasowanie szkicu i utworzenie strony na nowo, tak
        samo jak przy „Komitetach”. Nic nie ginie: szkic nigdy nie był publiczny, a jego treść
        (w tym nazwy, których nie przenosimy) leży w ``docs/import/tresci/partnerzy.md``.

        ``partners`` ustawiamy na pustą listę **przy każdym przebiegu**, tak jak każdą inną treść
        w tej komendzie: to narzędzie importujące, nie tryb pracy redakcyjnej. Wpisy dodane
        w ``/cms/`` powtórny przebieg skasuje – dlatego komendy nie uruchamia się po każdym
        deployu (patrz docstring modułu i README 6.6).
        """
        draft = ContentPage.objects.child_of(home).filter(slug=PARTNERS_SLUG).first()
        if draft is not None:
            draft.delete()
            self.stdout.write("konwersja: „Partnerzy” przestają być stroną treści, powstaje PartnersPage")

        source = FIXTURES / f"{PARTNERS_SLUG}.md"
        if not source.exists():
            raise CommandError(f"Brak pliku źródłowego {source}.")
        intro, _ = parse_markdown(source.read_text(encoding="utf-8"))

        page = PartnersPage.objects.child_of(home).filter(slug=PARTNERS_SLUG).first()
        created = page is None
        if created:
            page = PartnersPage(title=PARTNERS_TITLE, slug=PARTNERS_SLUG)
            home.add_child(instance=page)

        page.title = PARTNERS_TITLE
        page.intro = intro
        page.partners = []
        page.become_partner_title = PARTNERS_CTA_TITLE
        page.become_partner_body = PARTNERS_CTA_BODY
        # Adres bierzemy z ustawień serwisu, a nie z literału: to ten sam kontakt, co w stopce
        # i na stronie „Kontakt”, więc jego zmiana ma być jedną poprawką w ``/cms/``.
        page.contact_email = SiteSettings.for_site(home.get_site()).contact_email
        page.show_in_menus = True
        page.save()
        page.save_revision().publish()
        self.stdout.write(
            f"{'utworzono' if created else 'zaktualizowano'}: {page.url} "
            f"(opublikowana, {len(page.partners)} partnerów, kontakt {page.contact_email})"
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

    # --- kolejność rodzeństwa -------------------------------------------------------------

    def _order_children(self, parent, order) -> bool:
        """Ustawia dzieci ``parent`` w kolejności ``order``. Zwraca, czy ruszyliśmy drzewo.

        Kolejność rodzeństwa jest jedynym źródłem kolejności w pasku nawigacji, w rozwijanej
        sekcji „Dokumenty” i na stronie-spisie – wszystkie trzy sortują po ``path``.

        Przestawiamy tylko wtedy, gdy kolejność faktycznie się nie zgadza: ``move`` przepisuje
        ścieżki wszystkim potomkom przenoszonego węzła, a pod newsroomem wiszą aktualności.
        """
        children = {page.slug: page for page in Page.objects.child_of(parent).order_by("path")}
        wanted = [slug for slug in order if slug in children]
        current = [slug for slug in children if slug in set(wanted)]
        if current == wanted:
            return False

        previous: Page | None = None
        for slug in wanted:
            page = Page.objects.get(pk=children[slug].pk)
            if previous is None:
                page.move(parent, pos="first-child")
            else:
                page.move(previous, pos="right")
            previous = Page.objects.get(pk=page.pk)
        return True
