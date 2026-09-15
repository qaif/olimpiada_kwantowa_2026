"""``manage.py seed_legacy_content`` – treści przeniesione ze starej strony WordPressa.

Źródłem są pliki ``apps/cms/fixtures/legacy/*.md``: kopie treści opisanych w
``docs/import/stara-strona-inwentarz.md`` i ``docs/import/tresci/``. Komenda **nie redaguje**
ani jednego zdania organizatora – zmienia wyłącznie strukturę (nagłówek → blok ``heading``,
tabela dwukolumnowa → lista definicji, wyróżniona ramka → blok ``notice``); patrz
``apps.cms.legacy_markdown``.

Co powstaje:

- **strony opublikowane** (``ContentPage``): „Harmonogram”, „Warsztaty”, „Kontakt”,
  „Dla nauczycieli i materiały”,
- **strony wycofane** (``OBSOLETE_PAGES``): „Jak zacząć?” i „O Olimpiadzie” **przestały istnieć**
  jako podstrony – ich treść stoi na stronie głównej (sekcja kroków i sekcja ``#o-olimpiadzie``),
  a stare adresy odpowiadają trwałym przekierowaniem. Komenda kasuje je przy każdym pełnym
  przebiegu, bo baza produkcyjna ma je jeszcze w drzewie,
- **sekcja dokumentów** (``DocumentIndexPage`` pod ``/dokumenty/``) z kompletem dokumentów
  organizatora jako dziećmi. Sekcja jest jedną pozycją menu z listą rozwijaną; stare adresy
  jednosegmentowe (``/regulamin/``, ``/rodo/``…) zostają jako trwałe przekierowania,
- **dokumenty opublikowane** (``DocumentPage``): polityka RODO, wzór zgody rodzica lub opiekuna
  prawnego, standardy ochrony małoletnich i skład komitetów. Trzy z nich są przepisane
  z podpisanych PDF-ów organizatora (``fixtures/legacy/pdf-text/``), sekcja po sekcji, więc nie
  są już „wersją demonstracyjną” ze starego WordPressa: metryka mówi, z jakiego eksportu
  pochodzą, a ramka na górze wskazuje PDF jako wersję źródłową,
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
- **strony zredagowane w /cms/ są nietykalne**: strona, która ma choć jedną rewizję z autorem
  (rewizje tej komendy nie mają autora), zostaje przy pełnym przebiegu pominięta – z komunikatem.
  Reguła istnieje, bo organizator redaguje harmonogram i skład komitetów bezpośrednio na
  serwerze, a pełny przebieg po wdrożeniu cofałby te poprawki do plików z repozytorium.
  To samo dotyczy stron i aktualności **skasowanych** w /cms/ (wpis dziennika z autorem):
  komenda ich nie odtwarza. ``--force`` wyłącza obie ochrony (świadome przywrócenie treści
  z plików). Strony wycofane
  (``OBSOLETE_PAGES``) są kasowane niezależnie od tego, kto je redagował – to decyzja
  organizatora o strukturze serwisu, nie o treści,
- **strona partnerów** (``PartnersPage`` pod ``/partnerzy/``) – opublikowana; jej lista partnerów
  startuje pusta i jest **jedyną** treścią, której powtórny przebieg nie nadpisuje (wypełniają ją
  redakcja w ``/cms/`` i komenda ``seed_partners`` z logotypami organizatora).
  Stara strona wymieniała trzy nazwy: „Ministerstwo Edukacji”, „Uniwersytet Kwantowy”
  (instytucja nieistniejąca) i „Polskie Towarzystwo Fizyczne”, żadnej z potwierdzonym patronatem.
  Poprzedni import zostawiał je w treści i chował całą stronę jako szkic (404); to broniło sieci
  przed zmyśloną nazwą, ale kosztowało zaproszenie do współpracy, którego nie było gdzie
  przeczytać. Teraz jest odwrotnie: strona żyje, sekcja „Zostań partnerem” działa, a lista
  partnerów zaczyna się pusta i wypełnia ją redakcja w ``/cms/`` po podpisaniu umów. Nazwy ze
  starej strony zostają w inwentarzu (``docs/import/tresci/partnerzy.md``) i nigdzie indziej,
- **trzy aktualności** ze starego seedera – z datą dzisiejszą, bo oryginał nie miał ``post_date``
  (``docs/import/aktualnosci.md``), i z dopiskiem o przeniesieniu na końcu treści,
- **strona główna**: hasło, opis i sekcja „Jak zacząć w 3 krokach” z ``tresci/strona-glowna.md``
  oraz sekcja „O Olimpiadzie” (``about_body``) z ``o-olimpiadzie.md`` – ta ostatnia **tylko gdy
  jest pusta**, bo po pierwszym imporcie jej właścicielem jest redakcja (patrz ``_seed_about``).
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

- **wzór zgody opiekuna** (``/dokumenty/zgoda-opiekuna/``) nie pochodzi od organizatora: powstał
  w repozytorium, bo zgoda opiekuna w formularzu rejestracji musi mieć do czego linkować
  (``apps.accounts.consents``). Dlatego nie ma pliku do pobrania, a metryka i ramka nad treścią
  mówią wprost, że to wersja robocza do akceptacji – patrz README, „Decyzje do podjęcia przez
  właściciela”. Wersja w metryce musi zgadzać się z ``apps.accounts.consents.GUARDIAN_VERSION``:
  to ona trafia do wpisu dowodowego zgody.

- **Zasady Organizacji Zawodów** (``/dokumenty/zoz/``) też są nasze, nie organizatora, i też są
  projektem: Regulamin w § 1 ust. 4 odsyła do ZOZ harmonogram, formę zadań, wykaz narzędzi, progi
  punktowe i literaturę, a dokumentu o tej nazwie nie było – więc każde z tych odesłań prowadziło
  w pustkę. Treść jest wyprowadzona z tego, co robi serwis (terminy z ``competitions.Stage``, skala
  ``ScoringScale``, dwie niezależne recenzje, okno reklamacji ``Stage.appeal_window_*``), a nie
  wymyślona; terminów **nie powtarza** – odsyła do ``/harmonogram/``, bo dwa źródła terminów to
  dwie różne odpowiedzi na to samo pytanie. Zapisy, których organizator nie podjął, stoją w ostatniej
  sekcji dokumentu jako jawna lista decyzji (liczba finalistów, progi, literatura, koszty finału,
  narzędzia w finale, okres poufności) – patrz README, „Decyzje do podjęcia przez właściciela”.

- **polityka plików cookie** (``/dokumenty/cookies/``) jest nasza, ale – inaczej niż dwa dokumenty
  powyżej – **obowiązuje**: opisuje stan faktyczny serwisu (``sessionid``, ``csrftoken``,
  ``wagtail_sidebar_collapsed``, klucze ``cookie-consent``/``cookie-consent-at``/
  ``cookie-notice-ack`` paska zgody oraz ``_ga``/``_ga_…`` zapisywane dopiero po zgodzie),
  więc nie ma czego zatwierdzać, jest co utrzymywać w zgodzie z kodem. Zmiana zestawu
  ciasteczek albo dołożenie zewnętrznego osadzenia jest zmianą tego pliku.

``--only <slug>`` seeduje wyłącznie wskazane strony (można podać wielokrotnie). Tryb istnieje dla
produkcji, na której ta komenda **nie** chodzi po każdym wdrożeniu: pełny przebieg nadpisałby
treść wszystkich stron plikami z repozytorium, więc dołożenie jednego dokumentu kosztowałoby
skasowanie każdej poprawki wprowadzonej w ``/cms/`` od ostatniego importu. Kolejność dokumentów
w sekcji jest ustawiana także w tym trybie – nowa strona musi trafić na swoje miejsce w menu
i w spisie.

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
from apps.cms.site_tree import (
    INDEX_SLUG,
    ensure_document_index,
    ensure_link_redirect,
    ensure_redirect,
    take_document_page,
)
from apps.cms.workshops import WORKSHOPS_SLUG

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

#: Wzór zgody opiekuna nie pochodzi od organizatora – powstał w repozytorium jako **projekt**
#: i czeka na akceptację Fundacji oraz radcy prawnego. Metryka mówi to wprost (status), a ramka
#: nad treścią powtarza to zdanie w pliku źródłowym, żeby ostrzeżenie zostało także w wydruku.
#: Numer wersji jest ten sam, co w ``apps.accounts.consents.GUARDIAN_VERSION`` – to on trafia do
#: wpisu dowodowego ``ConsentRecord.document_version``, więc rozjazd znaczyłby dowód wskazujący
#: na wersję dokumentu, której nigdy nie opublikowano.
GUARDIAN_CONSENT_VERSION = "0.1 (projekt)"
GUARDIAN_CONSENT_DATE = date(2026, 9, 10)
GUARDIAN_CONSENT_STATUS = "Wersja robocza do akceptacji organizatora"

#: Zasady Organizacji Zawodów (ZOZ) I edycji – tak samo jak wzór zgody opiekuna **nie pochodzą od
#: organizatora**: powstały w repozytorium, bo Regulamin (§ 1 ust. 4) odsyła do nich harmonogram,
#: formę zadań, wykaz narzędzi, progi punktowe i literaturę, a dokumentu o tej nazwie nie było.
#: Treść jest wyprowadzona z tego, co serwis faktycznie robi (terminy z ``competitions.Stage``,
#: skala 0/2/5/6, dwie niezależne recenzje, okno reklamacji), a zapisy, których organizator jeszcze
#: nie podjął, stoją w ostatniej sekcji dokumentu jako jawna lista decyzji – nie jako ogólnikowe
#: zdanie udające regulację. Dlatego metryka i ramka nad treścią mówią wprost, że to projekt.
ZOZ_VERSION = "0.1 (projekt)"
ZOZ_DATE = date(2026, 9, 12)
ZOZ_STATUS = "projekt do akceptacji organizatora"

#: Polityka plików cookie jest naszym dokumentem, ale – inaczej niż ZOZ – **obowiązuje**: opisuje
#: stan faktyczny serwisu (trzy pliki niezbędne, klucze ``localStorage`` paska zgody, pliki
#: analityczne GA4 zapisywane wyłącznie po zgodzie), a nie propozycję do zatwierdzenia. Wersję
#: trzyma metryka, bo jej pierwszym czytelnikiem jest ktoś, kto sprawdza, czy czyta wersję
#: aktualną – a treść zmieniła się materialnie (1.0 zapewniała, że analityki nie ma w ogóle).
COOKIES_VERSION = "1.1"
COOKIES_DATE = date(2026, 9, 15)
COOKIES_STATUS = "obowiązuje"

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
        "Sprawdź terminy rejestracji, etapów szkolnych, wojewódzkich i finału.",
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
    "aktualnosci",
    "zadania",
    "harmonogram",
    # „Warsztaty” zaraz za „Harmonogramem”: obie pozycje odpowiadają na pytanie „kiedy”, tylko
    # jedna o zawodach, a druga o przygotowaniu do nich.
    WORKSHOPS_SLUG,
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
#: Regulamin pierwszy, bo to on rozstrzyga przebieg zawodów; skład komitetów zamyka dokumenty
#: zawodów, bo jest informacją o ludziach, a nie zbiorem zasad, a polityka plików cookie stoi
#: całkiem na końcu: dotyczy samego serwisu, nie Olimpiady.
DOCUMENT_ORDER = (
    "regulamin",
    # ZOZ zaraz za Regulaminem, bo to jedna para: Regulamin ustala zasady stałe i w § 1 ust. 4
    # odsyła do ZOZ szczegóły edycji, a przy sprzeczności rozstrzyga na swoją korzyść. Czytelnik,
    # który sięga po jeden z nich, potrzebuje drugiego w następnym wierszu spisu.
    "zoz",
    "rodo",
    # Wzór zgody opiekuna zaraz za polityką RODO: to jej praktyczne przedłużenie (zgoda osoby
    # uprawnionej na przetwarzanie danych małoletniego), a nie osobny zbiór zasad.
    "zgoda-opiekuna",
    "standardy-ochrony-maloletnich",
    "komitety",
    # Polityka plików cookie na końcu: jest dokumentem o **serwisie** (sesja, ochrona formularzy),
    # a nie o zawodach, więc nie wchodzi między dokumenty, które czyta się przed przystąpieniem
    # do Olimpiady. Stopka i pasek informacyjny prowadzą do niej wprost, więc jej miejsce w spisie
    # nie jest jedyną drogą dojścia.
    "cookies",
)

#: Strony, które **przestały istnieć**, i adres, na który ma prowadzić ich stary link. Obie miały
#: pozycję w menu i obie dublowały treść, która stoi już na stronie głównej:
#:
#: - ``jak-zaczac`` – pięć kroków uczestnika. Na stronie głównej jest sekcja „Jak zacząć w 3 krokach”
#:   (pola ``steps_title``/``steps``), czyli ta sama procedura o dwa kliknięcia bliżej. Dwie listy
#:   kroków w jednym serwisie różniłyby się przy pierwszej zmianie regulaminu,
#: - ``o-olimpiadzie`` – po co, dla kogo, kto organizuje. Treść wraca na stronę główną jako sekcja
#:   ``#o-olimpiadzie`` (``HomePage.about_body``), bo to odpowiedź na pierwsze pytanie czytelnika,
#:   który dopiero zobaczył hasło; jako osobna podstrona była o jedno kliknięcie za daleko.
#:
#: Komenda kasuje te strony przy każdym pełnym przebiegu (nie tylko raz): baza produkcyjna ma je
#: jeszcze w drzewie, a wpis tutaj jest jedynym miejscem, w którym decyzja o ich zniknięciu jest
#: zapisana. Stare adresy wiszą w pismach i w wyszukiwarkach, więc zostają jako 301.
OBSOLETE_PAGES = (
    ("jak-zaczac", "/"),
    ("o-olimpiadzie", "/#o-olimpiadzie"),
)

#: Plik, z którego bierze się sekcja „O Olimpiadzie” na stronie głównej. Strony o tym slugu już nie
#: ma, ale plik zostaje – jest teraz źródłem treści sekcji, a nie podstrony.
ABOUT_SOURCE = "o-olimpiadzie"

#: Adresy sprzed przeniesienia dokumentów pod ``/dokumenty/``. Wiszą w pismach do szkół i w indeksach
#: wyszukiwarek, więc zostają jako trwałe (301) przekierowania – patrz ``apps.cms.site_tree``.
LEGACY_DOCUMENT_PATHS = (
    "/regulamin/",
    "/rodo/",
    "/standardy-ochrony-maloletnich/",
    "/komitety/",
)


def deleted_in_cms(title: str) -> bool:
    """Czy stronę o tym tytule skasował człowiek w ``/cms/``.

    Kasowanie nie zostawia rewizji, zostawia wpis dziennika (``PageLogEntry``, akcja
    ``wagtail.delete``) z autorem; wpisy z komend seedujących autora nie mają. Bez tej kontroli
    komenda odtwarzałaby po każdym przebiegu strony, które organizator świadomie usunął –
    tak stało się z aktualnościami startowymi na produkcji.
    """
    from wagtail.models import PageLogEntry

    return PageLogEntry.objects.filter(action="wagtail.delete", user__isnull=False, label=title).exists()


def edited_in_cms(page) -> bool:
    """Czy stronę redagował człowiek w ``/cms/``.

    Rozstrzyga autor rewizji: edycja w panelu zapisuje ``Revision.user``, a ``save_revision()``
    wołane przez komendy seedujące zostawia to pole puste. To jedyny ślad, który odróżnia
    „treść z pliku, którą wolno odtworzyć” od „treść redakcji, której nie wolno cofnąć”.
    """
    return page.revisions.filter(user__isnull=False).exists()


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
    # ZOZ stoi pierwszy wśród dokumentów tej listy, bo pierwszy jest w sekcji ``/dokumenty/``
    # (zaraz za Regulaminem, którego seeduje osobna komenda ``seed_regulamin``). Pliku do pobrania
    # nie ma – tak samo jak przy wzorze zgody opiekuna, bo to nasz projekt, a nie plik organizatora.
    LegacyPage(
        slug="zoz",
        title="Zasady Organizacji Zawodów (ZOZ)",
        document=True,
        metadata={
            "version_label": ZOZ_VERSION,
            "document_date": ZOZ_DATE,
            "status_label": ZOZ_STATUS,
        },
    ),
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
    LegacyPage(slug="harmonogram", title="Harmonogram", in_menu=True),
    # Warsztaty dostały własną pozycję menu, bo wcześniej cały ich harmonogram był tabelą w środku
    # ``/harmonogram/``: kto nie wszedł na tę podstronę i nie przewinął jej do końca, nie dowiadywał
    # się, że warsztaty w ogóle są – mimo że są bezpłatne, otwarte i to od nich zaczyna się
    # przygotowanie. Strona jest też jedynym źródłem zapowiedzi na stronie głównej
    # (``apps.cms.workshops``), więc harmonogram warsztatów istnieje w serwisie dokładnie raz.
    LegacyPage(slug=WORKSHOPS_SLUG, title="Warsztaty", in_menu=True),
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
        slug="zgoda-opiekuna",
        title="Zgoda rodzica lub opiekuna prawnego",
        document=True,
        metadata={
            "version_label": GUARDIAN_CONSENT_VERSION,
            "document_date": GUARDIAN_CONSENT_DATE,
            "status_label": GUARDIAN_CONSENT_STATUS,
        },
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
    # Polityka plików cookie – ostatnia w sekcji i ostatnia tutaj. Bez PDF-a: dokument opisuje stan
    # serwisu i zmienia się razem z nim, więc jego wersją źródłową jest ta strona, a nie plik.
    LegacyPage(
        slug="cookies",
        title="Polityka plików cookie",
        document=True,
        metadata={
            "version_label": COOKIES_VERSION,
            "document_date": COOKIES_DATE,
            "status_label": COOKIES_STATUS,
        },
    ),
)


class Command(BaseCommand):
    help = "Przenosi treści starej strony Olimpiady Kwantowej do CMS-a. Idempotentne."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Nadpisz także strony zredagowane w /cms/ (te z rewizją z autorem). Bez tej flagi "
                "pełny przebieg je pomija, żeby nie cofać poprawek organizatora."
            ),
        )
        parser.add_argument(
            "--only",
            action="append",
            default=[],
            metavar="SLUG",
            help=(
                "Zaseeduj wyłącznie stronę o tym slugu (można podać wielokrotnie). Reszta treści "
                "zostaje nietknięta – tryb do dołożenia pojedynczego dokumentu na działającym "
                "serwisie, bez cofania redakcyjnych poprawek zrobionych w /cms/."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        home = HomePage.objects.first()
        if home is None:
            raise CommandError("Brak drzewa stron – uruchom najpierw `manage.py migrate`.")
        if not FIXTURES.is_dir():
            raise CommandError(f"Brak katalogu z treściami: {FIXTURES}.")

        if not PDF_DIR.is_dir():
            raise CommandError(f"Brak katalogu z PDF-ami organizatora: {PDF_DIR}.")

        # ``--only`` istnieje dla produkcji, na której ta komenda **nie** chodzi po każdym wdrożeniu:
        # pełny przebieg nadpisuje treść wszystkich stron plikami z repozytorium, więc dołożenie
        # jednego nowego dokumentu kosztowałoby skasowanie każdej poprawki wprowadzonej w /cms/
        # od ostatniego importu. Z tą flagą komenda dotyka dokładnie wskazanych stron.
        only = set(options.get("only") or [])
        self.force = bool(options.get("force"))
        known = {spec.slug for spec in PAGES}
        unknown = sorted(only - known)
        if unknown:
            raise CommandError(f"Nieznane slugi: {', '.join(unknown)}. Dostępne: {', '.join(sorted(known))}.")
        specs = [spec for spec in PAGES if not only or spec.slug in only]

        index, index_created = ensure_document_index(home)
        if index_created:
            self.stdout.write("utworzono sekcję: /dokumenty/")

        if not only:
            self._seed_home(home)
            self._drop_legacy_komitety_page(home)
            self._drop_obsolete_pages(home)
        for spec in specs:
            self._seed_page(home, index, spec)
        if not only:
            self._seed_partners(home)
            self._seed_news(home)
        moved = self._order_children(home, MENU_ORDER) if not only else False
        # Przestawienie rodzeństwa strony głównej przepisało ``path`` także sekcji dokumentów,
        # a ``child_of`` czyta ścieżkę z obiektu – bez odświeżenia szukalibyśmy dzieci pod
        # adresem, którego już nie ma.
        index = DocumentIndexPage.objects.get(pk=index.pk)
        # Kolejność dokumentów przestawiamy także przy ``--only``: nowa strona musi trafić na swoje
        # miejsce w sekcji, w menu i w spisie, a ``_order_children`` rusza drzewo tylko wtedy, gdy
        # kolejność faktycznie się nie zgadza.
        self._order_children(index, DOCUMENT_ORDER)
        redirects = self._seed_redirects(index, only=only)

        scope = f" (--only {', '.join(sorted(only))})" if only else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_legacy_content{scope}: {len(specs) + (0 if only else 1)} stron, "
                f"{0 if only else len(NEWS)} aktualności, "
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

    # --- strony wycofane ------------------------------------------------------------------

    def _drop_obsolete_pages(self, home: HomePage) -> None:
        """Kasuje strony wycofane z serwisu i zostawia po nich trwałe przekierowanie.

        Kasujemy, a nie ukrywamy jako szkic: szkic zostaje w drzewie, w menu ``/cms/`` i w liście
        stron redakcji jako pozycja bez wyjaśnienia, dlaczego nie jest opublikowana – a treść
        obu tych stron **jest** w serwisie, tylko w innym miejscu (patrz ``OBSOLETE_PAGES``).
        Skasowanie nie jest utratą treści: jedynym źródłem jest plik w ``fixtures/legacy/``,
        a ``o-olimpiadzie.md`` nadal jest w repozytorium – wypełnia teraz sekcję strony głównej.

        Przekierowanie zakładamy **także wtedy, gdy strony już nie było**. Tak wygląda drugie
        i każde następne uruchomienie na produkcji, a 301 jest potrzebny dokładnie tam, gdzie
        strony nie ma; wiązanie go z faktem skasowania sprawiłoby, że raz usunięty adres traci
        przekierowanie przy pierwszym powtórnym przebiegu.
        """
        for slug, link in OBSOLETE_PAGES:
            page = ContentPage.objects.descendant_of(home).filter(slug=slug).first()
            if page is not None:
                page.delete()
                self.stdout.write(f"usunięto stronę: /{slug}/ (treść jest teraz pod {link})")
            if ensure_link_redirect(f"/{slug}/", link):
                self.stdout.write(f"przekierowanie: /{slug}/ → {link}")

    # --- przekierowania ze starych adresów ------------------------------------------------

    def _seed_redirects(self, index, *, only: set[str] | None = None) -> int:
        """Trwałe przekierowania ``/regulamin/`` → ``/dokumenty/regulamin/`` itd.

        Przy ``--only`` bierzemy pod uwagę wyłącznie wskazane strony: przebieg dokładający jeden
        dokument nie ma powodu dotykać przekierowań pozostałych.
        """
        created = 0
        for old_path in LEGACY_DOCUMENT_PATHS:
            slug = old_path.strip("/")
            if only and slug not in only:
                continue
            page = DocumentPage.objects.child_of(index).filter(slug=slug).first()
            if page is None:
                self.stderr.write(f"brak dokumentu {slug} – pomijam przekierowanie z {old_path}")
                continue
            if ensure_redirect(old_path, page):
                created += 1
        return created

    # --- strona główna --------------------------------------------------------------------

    def _seed_home(self, home: HomePage) -> None:
        if not self.force and edited_in_cms(home):
            # Hasło i kroki należą do redakcji; sekcja „O Olimpiadzie” i tak wypełnia się tylko
            # wtedy, gdy jest pusta, więc jej dołożenie niczego nie cofa.
            about = self._seed_about(home)
            if home.about_body:
                home.save()
                home.save_revision().publish()
            self.stdout.write(f"strona główna: pominięto hasło i kroki (zredagowana w /cms/){about}")
            return
        home.title = HOME_TITLE
        home.hero_title = HOME_HERO_TITLE
        home.hero_text = HOME_HERO_TEXT
        home.steps_title = HOME_STEPS_TITLE
        home.steps = [("step", {"title": title, "text": text}) for title, text in HOME_STEPS]
        about = self._seed_about(home)
        home.save()
        home.save_revision().publish()
        self.stdout.write(f"strona główna: hasło, opis, sekcja kroków{about}")

    def _seed_about(self, home: HomePage) -> str:
        """Wypełnia sekcję „O Olimpiadzie” na stronie głównej – **tylko gdy jest pusta**.

        To jedyne odstępstwo od reguły „powtórny przebieg nadpisuje treść plikiem z repozytorium”,
        i z tego samego powodu, co przy liście partnerów: ta treść jest **redakcyjna i skończona**.
        Plik ``o-olimpiadzie.md`` to kopia starej podstrony, czyli punkt startowy; po pierwszym
        imporcie właścicielem sekcji jest redakcja w ``/cms/``. Nadpisywanie jej przy każdym
        przebiegu kasowałoby dopisany akapit o partnerach albo poprawioną nazwę fundacji, a jedyne,
        co dawałoby w zamian, to powrót do tekstu sprzed roku.

        Treść idzie przez ten sam parser, co strony treści, i do pola o tym samym zestawie bloków –
        akapit przeniesiony ze strony do sekcji (albo odwrotnie) nie gubi żadnego bloku. Śródtytuły
        pliku (``## Po co powstała Olimpiada?``) zostają blokami ``heading`` z kotwicami, więc
        można na nie linkować tak samo jak wcześniej na sekcje podstrony.
        """
        if home.about_body:
            return ", sekcja „O Olimpiadzie” bez zmian (treść redakcji)"
        source = FIXTURES / f"{ABOUT_SOURCE}.md"
        if not source.exists():
            raise CommandError(f"Brak pliku źródłowego {source}.")
        # ``intro`` zostaje pusty: plik zaczyna się od śródtytułu, a sekcja na stronie głównej nie
        # ma osobnego pola na wprowadzenie – nagłówek sekcji niesie ``about_title``.
        _, blocks = parse_markdown(source.read_text(encoding="utf-8"))
        home.about_body = blocks
        return f", sekcja „O Olimpiadzie” z {source.name} ({len(blocks)} bloków)"

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
        if created and not self.force and deleted_in_cms(spec.title):
            self.stdout.write(f"pominięto: /{spec.slug}/ (usunięta w /cms/; --force odtworzy)")
            return
        if not created and not self.force and edited_in_cms(page):
            # Treść redakcji zostaje w całości – także tytuł i załączniki; komenda nie wie, którą
            # część zmieniono, a „pół strony z pliku, pół z panelu” byłoby gorsze niż obie całości.
            self.stdout.write(f"pominięto: {page.url} (zredagowana w /cms/; --force nadpisze)")
            return
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

        ``partners`` to **jedyna** treść, której ta komenda nie nadpisuje. Reszta pochodzi z plików
        w repozytorium, więc powtórny przebieg odtwarza dokładnie to, co było; lista partnerów rośnie
        gdzie indziej – w ``/cms/`` razem z podpisywanymi umowami i przez ``seed_partners``, który
        wgrywa logotypy z ``fixtures/partners/``. Wcześniej stało tu ``page.partners = []``, przez co
        każdy import treści kasował partnerów dopisanych po ostatnim wdrożeniu, a kolejność obu komend
        w skrypcie wdrożeniowym decydowała o wyniku. Pustą listę ustawiamy więc wyłącznie przy
        zakładaniu strony – to ona jest pustym stanem opisanym w ``PartnersPage``.
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
        if not created and not self.force and edited_in_cms(page):
            # Ta sama reguła, co dla stron treści: wstęp i wezwanie „zostań partnerem” po
            # redakcji w /cms/ należą do organizatora, nie do pliku.
            self.stdout.write(f"pominięto: {page.url} (zredagowana w /cms/; --force nadpisze)")
            return
        if created:
            page = PartnersPage(title=PARTNERS_TITLE, slug=PARTNERS_SLUG, partners=[])
            home.add_child(instance=page)

        page.title = PARTNERS_TITLE
        page.intro = intro
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
            if created and not self.force and deleted_in_cms(title):
                self.stdout.write(f"pominięto: aktualność {slug} (usunięta w /cms/; --force odtworzy)")
                continue
            if not created and not self.force and edited_in_cms(page):
                self.stdout.write(f"pominięto: aktualność {slug} (zredagowana w /cms/)")
                continue
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
