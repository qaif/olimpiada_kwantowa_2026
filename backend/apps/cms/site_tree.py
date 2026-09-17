"""Miejsce dokumentów w drzewie stron: sekcja ``/dokumenty/`` i przekierowania ze starych adresów.

Moduł jest wspólny dla ``seed_regulamin`` i ``seed_legacy_content``, bo obie komendy dokładają
strony do tej samej sekcji, a każda bywa uruchamiana pierwsza (patrz README, kolejność kroków).
Reguła „gdzie mieszka dokument” musi więc być jedna – inaczej pierwsza komenda tworzyłaby sekcję
pod stroną główną, a druga drugą taką samą obok.

Trzy decyzje warte uzasadnienia:

- **przenosimy, zamiast tworzyć od nowa.** Baza produkcyjna ma już strony ``/regulamin/``,
  ``/rodo/`` i ``/standardy-ochrony-maloletnich/`` pod stroną główną. ``Page.move`` zachowuje
  identyfikator strony, jej rewizje i wszystkie odnośniki wewnętrzne; utworzenie nowej strony
  i skasowanie starej zerwałoby jedno i drugie,
- **stare adresy zostają, jako przekierowanie 301.** Adres ``/regulamin/`` wisi w pismach do szkół
  i w wynikach wyszukiwarek. ``wagtail.contrib.redirects`` trzyma go w bazie, a nie w urlconfie,
  więc redaktor widzi listę przekierowań w ``/cms/`` i może ją rozszerzyć bez wydania aplikacji,
- **przekierowanie wskazuje stronę, a nie adres.** ``redirect_page`` przelicza cel przy każdym
  żądaniu, więc kolejne przeniesienie dokumentu w drzewie nie zostawia martwego 301. Wyjątkiem jest
  ``ensure_link_redirect``: gdy strona przestała istnieć, a jej treść jest **sekcją** innej strony
  (``/o-olimpiadzie/`` → ``/#o-olimpiadzie``), celem musi być adres, bo kotwica nie jest stroną.
"""

from __future__ import annotations

from wagtail.models import Page, Site

from .models import ArchiveIndexPage, DocumentIndexPage, HomePage

INDEX_SLUG = "dokumenty"
INDEX_TITLE = "Dokumenty"
INDEX_INTRO = (
    "<p>Komplet dokumentów Olimpiady Kwantowej: regulamin zawodów, polityka RODO, standardy "
    "ochrony małoletnich i skład komitetów. Każdy dokument można przeczytać na stronie "
    "(z odnośnikami do paragrafów) albo pobrać w wersji podpisanej przez organizatora.</p>"
)


def home_page(site=None) -> HomePage | None:
    """Strona główna wskazanej witryny; bez argumentu – witryny **domyślnej**.

    Jedno wejście dla seedów i dla komendy zakładającej konkurs. Dotąd każda z nich pisała
    ``HomePage.objects.first()``, czyli „jakakolwiek strona główna w tej bazie” – co w instalacji
    jednokonkursowej jest tą właściwą, a w wielokonkursowej bywa cudzą. Skutek byłby cichy
    i nieodwracalny: ``seed_legacy_content`` nadpisuje treść stron, więc uruchomiony „nie na tej”
    witrynie przepisałby serwis jednego organizatora treścią drugiego.

    Domyślna witryna, a nie „pierwsza”: seedy są narzędziami importu treści Olimpiady Kwantowej
    (§ 4.5) i mają dalej trafiać dokładnie tam, gdzie trafiały – Konkurs #1 stoi na witrynie
    domyślnej i to się w tym etapie nie zmienia.

    ``inclusive=True``, bo korzeniem witryny **jest** strona główna (tak ustawia to migracja
    ``cms.0002``), a nie jej rodzic. ``None`` znaczy „ta witryna nie ma strony głównej” i jest
    odpowiedzią poprawną dla świeżej bazy przed migracją drzewa – wołający wypisuje wtedy
    komunikat, zamiast się wywracać.
    """
    site = site or Site.objects.filter(is_default_site=True).first()
    if site is None:
        return None
    return HomePage.objects.descendant_of(site.root_page, inclusive=True).first()


def ensure_document_index(home) -> tuple[DocumentIndexPage, bool]:
    """Sekcja ``/dokumenty/`` pod stroną główną. Zwraca ``(strona, czy powstała teraz)``."""
    page = DocumentIndexPage.objects.child_of(home).filter(slug=INDEX_SLUG).first()
    created = page is None
    if created:
        page = DocumentIndexPage(title=INDEX_TITLE, slug=INDEX_SLUG)
        home.add_child(instance=page)
        _position_before_archive(page, home)
        # ``move`` przepisuje ``path`` w bazie, nie w obiekcie – bez odświeżenia zapis niżej
        # wpisałby z powrotem ścieżkę sprzed przestawienia i rozjechałby drzewo.
        page = DocumentIndexPage.objects.get(pk=page.pk)

    page.title = INDEX_TITLE
    page.intro = INDEX_INTRO
    page.show_in_menus = True
    page.save()
    page.save_revision().publish()
    return DocumentIndexPage.objects.get(pk=page.pk), created


def take_document_page(model, index, home, slug: str) -> tuple[object | None, bool]:
    """Strona dokumentu o danym slugu pod ``/dokumenty/``. Zwraca ``(strona | None, czy przeniesiona)``.

    Kolejność szukania odpowiada dwóm stanom bazy, w których komenda musi zadziałać tak samo:
    świeżej (strony nie ma nigdzie) i produkcyjnej (strona stoi jeszcze pod stroną główną).
    """
    page = model.objects.child_of(index).filter(slug=slug).first()
    if page is not None:
        return page, False

    legacy = model.objects.child_of(home).filter(slug=slug).first()
    if legacy is None:
        return None, False

    # ``move`` przez ``Page``, nie przez klasę potomną: przepisuje ``url_path`` przenoszonej strony
    # i wszystkich jej potomków, więc adres zmienia się od razu, a nie dopiero po zapisie rewizji.
    Page.objects.get(pk=legacy.pk).move(index, pos="last-child")
    return model.objects.get(pk=legacy.pk), True


def ensure_redirect(old_path: str, page, site=None) -> bool:
    """Trwałe (301) przekierowanie ze starego adresu na stronę. Zwraca, czy powstało teraz.

    ``site=None`` znaczy „wpis wspólny dla wszystkich witryn” i jest **zachowaniem dzisiejszym**:
    tak stoją w produkcyjnej bazie przekierowania Konkursu #1 i tak mają zostać (§ 0). Argument
    jest dla komendy zakładającej **kolejny** konkurs: jego ``/regulamin/`` to inny dokument niż
    nasz, więc jego przekierowanie musi być dowiązane do jego witryny – wpis wspólny przejąłby
    ten adres w całej instalacji.

    Adres może mieć już przekierowanie, którego ta komenda nie zakładała: Wagtail tworzy je sam
    przy każdym przeniesieniu strony w drzewie (``WAGTAILREDIRECTS_AUTO_CREATE``), i to
    z dowiązaniem do konkretnej witryny, podczas gdy nasze jest wspólne dla wszystkich (``site``
    puste). Unikalność w bazie obejmuje parę ``(old_path, site)``, więc ślepe ``create`` dawałoby
    przy każdym wdrożeniu drugi wiersz na ten sam adres – dwa wpisy w ``/cms/`` i pytanie, który
    obowiązuje. Bierzemy więc pierwszy istniejący wpis dla tej ścieżki, aktualizujemy jego cel,
    a nadmiarowe kasujemy.
    """
    return _ensure_redirect(old_path, page=page, site=site)


def ensure_link_redirect(old_path: str, link: str, site=None) -> bool:
    """To samo, ale celem jest **adres**, nie strona w drzewie. Zwraca, czy powstało teraz.

    Potrzebne tam, gdzie strona przestała istnieć, a jej treść mieszka teraz w sekcji innej strony:
    ``/o-olimpiadzie/`` → ``/#o-olimpiadzie``. Kotwicy nie da się wyrazić przez ``redirect_page``
    (to dowiązanie do strony, nie do jej fragmentu), a czytelnik, który klika stary odnośnik, ma
    wylądować na tej treści, nie na górze strony głównej i z pytaniem, gdzie się podziała.

    Cena jest ta, którą ``ensure_redirect`` z premedytacją omija: adres jest tekstem, więc kolejne
    przestawienie drzewa go nie przeliczy. Dla dwóch adresów wskazujących stronę główną (``/``
    i jej kotwicę) to nie problem – korzeń witryny się nie przenosi.

    ``site`` znaczy to samo, co w ``ensure_redirect``.
    """
    return _ensure_redirect(old_path, link=link, site=site)


def _ensure_redirect(old_path: str, *, page=None, link: str = "", site=None) -> bool:
    """Wspólne ciało obu funkcji: jeden wpis na ścieżkę, nadmiarowe kasowane. Patrz wyżej.

    Zakres szukania duplikatów idzie za ``site``: bez witryny pytamy o **wszystkie** wpisy dla tej
    ścieżki (tak było i tak ma zostać dla Konkursu #1 – to ta reguła sprząta po automatycznych
    przekierowaniach Wagtaila), a z witryną – wyłącznie o jej własne. Inaczej założenie
    przekierowania dla drugiego konkursu kasowałoby przekierowanie pierwszego, bo ścieżka
    ``/regulamin/`` jest u obu ta sama.
    """
    from wagtail.contrib.redirects.models import Redirect

    normalised = Redirect.normalise_path(old_path)
    rows = Redirect.objects.filter(old_path=normalised)
    if site is not None:
        rows = rows.filter(site=site)
    existing = list(rows.order_by("pk"))
    for duplicate in existing[1:]:
        duplicate.delete()

    if existing:
        redirect = existing[0]
        redirect.redirect_page = page
        redirect.redirect_link = link
        redirect.is_permanent = True
        redirect.save()
        return False

    Redirect.objects.create(
        old_path=normalised, site=site, redirect_page=page, redirect_link=link, is_permanent=True
    )
    return True


def _position_before_archive(page, home) -> None:
    """Nowa sekcja staje w menu przed „Archiwum”, a nie na jego końcu.

    Menu jest kolejnością rodzeństwa w drzewie (``context_processors.cms_menu`` sortuje po
    ``path``), a ``add_child`` dokłada na sam koniec – sekcja wylądowałaby wtedy za „Wynikami”
    i „Kontaktem”. Docelową kolejność ustawia ``seed_legacy_content``; to jest ustawienie
    sensownego domyślnego miejsca dla przebiegu samego ``seed_regulamin``.
    """
    archive = ArchiveIndexPage.objects.child_of(home).first()
    if archive is None or page.path < archive.path:
        return
    Page.objects.get(pk=page.pk).move(archive, pos="left")
