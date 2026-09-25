"""Menu części informacyjnej dla ``templates/base.html``.

Nawigacja nie jest zabezpieczeniem (tak samo jak ``apps.web.context_processors.roles``) – to tylko
lista publicznych stron Wagtaila oznaczonych „pokaż w menu”. Menu składa się z drzewa **witryny
z żądania** (``Site.find_for_request``) i tak było od początku – ta część była poprawna jeszcze
przed wielokonkursowością (``docs/UNIWERSALNY-ETAP-1.md`` § 3.7, „piąty, mniej oczywisty”).

Poprawki wymagała lista zapasowa. ``FALLBACK_MENU`` to cztery adresy, które w bazie założyła
migracja ``apps.cms.0002`` – czyli drzewo **Konkursu #1**, a nie „menu każdego serwisu”. Dlatego
sięgamy po nią wyłącznie wtedy, gdy żądanie trafiło w witrynę **domyślną**: to jest ta jedna
witryna, o której wiadomo, że te adresy w niej istnieją. Konkurs, którego drzewo stron dopiero
powstaje, dostaje menu **puste** – nagłówek bez pozycji jest wtedy uczciwy, a nagłówek z cudzymi
adresami prowadziłby jego czytelników na cztery strony, których pod tą domeną nie ma.

Szablon bazowy nigdy nie może wywrócić się przez CMS: błąd bazy i brak drzewa kończą się pustym
menu albo listą zapasową, nigdy wyjątkiem.

Jedna pozycja menu ma listę rozwijaną: sekcja dokumentów (``DocumentIndexPage``). Rozwijamy
**wyłącznie** ten typ, a nie „każdą stronę menu, która ma dzieci”: newsroom też ma dzieci i pod
regułą ogólną wysypałby do nagłówka wszystkie aktualności, a archiwum – wszystkie edycje.
Dzieci czytamy jednym zapytaniem dla całego menu, więc dołożenie kolejnego dokumentu nie dokłada
zapytania do każdej strony serwisu.

Jedna pozycja nie pochodzi z drzewa Wagtaila: „Dla szkół/nauczycieli” (prośba organizatora z 22.09.2026,
patrz ``_supervisor_menu_item``) stoi zawsze **ostatnia**, za drzewem albo za listą zapasową, i tylko
dla niezalogowanego czytelnika na witrynie, która ma dziś włączoną rejestrację opiekunów szkolnych.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError
from django.db.models import Q

logger = logging.getLogger(__name__)

#: Pozycje, które stoją w **przyklejonym pasku** obok logotypu, a nie w dolnym menu serwisu:
#: to, czego uczestnik szuka najczęściej i w trakcie pracy z długim dokumentem (zadania, terminy,
#: warsztaty). Dobór jest po slugu strony, nie po tytule, bo tytuł redakcja może zmienić.
PRIMARY_MENU_SLUGS = ("zadania", "harmonogram", "warsztaty", "kontakt")

#: Kolejność pozycji menu ustalona przez organizatora (uwagi z 21.09.2026): strona główna (domek),
#: Komitety, Partnerzy, Harmonogram, Zadania, Wyniki, Warsztaty, Dokumenty, Kontakt, FAQ. Dobór po
#: **slugu**, z tego samego powodu co wyżej. Strona oznaczona „pokaż w menu”, której tu nie ma
#: (nowa zakładka założona w /cms/), staje **za** tą listą w kolejności drzewa – lista ustawia
#: znane pozycje, a nie zamyka menu przed redakcją.
MENU_ORDER = (
    "komitety",
    "partnerzy",
    "harmonogram",
    "zadania",
    "wyniki",
    "warsztaty",
    "dokumenty",
    "kontakt",
    "faq",
)

#: Krótkie etykiety pozycji menu. Tytuł strony zostaje pełny („Skład komitetów”, „Najczęstsze
#: pytania”) – to on stoi w nagłówku strony i w wynikach wyszukiwania; menu ma dziesięć pozycji
#: w jednym wierszu i potrzebuje słów, a nie zdań.
MENU_TITLES = {"komitety": "Komitety", "faq": "FAQ"}

#: Strony, które organizator zdjął z menu, choć w drzewie mają „pokaż w menu”: newsroom (jego
#: miejsce zajął domek, a aktualności stoją w panelu strony głównej) i archiwum (w pierwszej
#: edycji puste). Adresy działają dalej – znika wyłącznie pozycja nagłówka.
HIDDEN_MENU_SLUGS = frozenset({"aktualnosci", "archiwum"})

#: Dokumenty wyjęte z listy rozwijanej „Dokumenty” i postawione jako **osobna** pozycja menu.
#: Skład komitetów jest stroną-dokumentem (``/dokumenty/komitety/``), ale czytelnik szuka go jak
#: zakładki „kto to organizuje”, a nie jak regulaminu.
PROMOTED_DOCUMENT_SLUGS = frozenset({"komitety"})

#: Pierwsza pozycja menu: strona główna, rysowana w szablonie jako domek (``item.home``).
HOME_ITEM_TITLE = "Strona główna"

#: Zapasowe menu = dokładnie te ścieżki, które tworzy migracja drzewa stron **witryny domyślnej**.
#: Stała zostaje nietknięta (pilnuje jej ``test_menu_matches_seeded_tree``); zmieniło się to, komu
#: wolno ją pokazać – patrz docstring modułu.
FALLBACK_MENU = (
    {"title": "Aktualności", "url": "/aktualnosci/"},
    {"title": "Zadania", "url": "/zadania/"},
    {"title": "Archiwum", "url": "/archiwum/"},
    {"title": "Wyniki", "url": "/wyniki/"},
)


def _menu_item(slug: str, title: str, url: str, request, kids: list[dict] | None = None) -> dict:
    """Jedna pozycja menu w kształcie, którego oczekuje ``templates/base.html``."""
    kids = kids or []
    return {
        "slug": slug,
        "title": MENU_TITLES.get(slug, title),
        "url": url,
        "children": kids,
        # Pozycja rodzica jest podświetlona także wtedy, gdy czytelnik stoi na jej dziecku –
        # inaczej na stronie regulaminu nagłówek nie wskazywałby niczego.
        "active": request.path == url or any(kid["active"] for kid in kids),
        "primary": slug in PRIMARY_MENU_SLUGS,
        "home": False,
    }


def _ordered(items: list[dict]) -> list[dict]:
    """Pozycje w kolejności organizatora; nieznane slugi za nimi, w kolejności drzewa."""
    rank = {slug: index for index, slug in enumerate(MENU_ORDER)}
    # ``sorted`` jest stabilne, więc pozycje spoza listy zachowują kolejność, w jakiej przyszły.
    return sorted(items, key=lambda item: rank.get(item["slug"], len(rank)))


def _supervisor_menu_item(request) -> dict | None:
    """Osobna pozycja menu „Dla nauczycieli” – prośba organizatora z 22.09.2026.

    Do tej pory rejestracja opiekuna szkolnego (``/register/supervisor/``) miała odnośnik
    wyłącznie na ``/register/`` i na ``/login/``; organizator poprosił o osobną pozycję w głównym
    menu, bo nauczyciel szukający konta nie zawsze trafia na stronę logowania uczestnika.

    Warunek jest **wyłącznie** dla niezalogowanych: konto, które już jest zalogowane (uczestnik,
    koordynator, opiekun z gotowym profilem), nie ma z odnośnika żadnego pożytku – nie zarejestruje
    się nim drugi raz. Nie sprawdzamy przy tym, którą to rolę ma zalogowana osoba (uczestnik czy już
    opiekun) – reguła „zalogowany = nie pokazuj” jest prosta i nie rozjeżdża się z każdą nową rolą,
    jaka kiedyś dojdzie do systemu.

    Przełącznik czytamy tą samą funkcją, którą bramkuje sam adres i którą pyta procesor
    ``apps.web.context_processors.supervisor_registration`` na ``/register/`` i na ``/login/`` –
    odnośnik i adres, do którego prowadzi, muszą odpowiadać na to samo pytanie „czy ta witryna
    oferuje dziś tę rolę”, inaczej pozycja menu na cudzej witrynie prowadziłaby w 404. W przeciwieństwie
    do tamtego procesora wołamy funkcję **od razu**, a nie leniwie za ``SimpleLazyObject`` – trzeba
    znać wynik, żeby wiedzieć, czy w ogóle dokładać pozycję do listy.

    Zapytanie kosztuje to samo, co gdziekolwiek indziej: przełącznik ma trzydziestosekundową pamięć
    podręczną na proces (``apps.accounts.supervisors._registration_cache``), czyszczoną od razu przy
    zapisie ``cms.SiteSettings`` w /cms/ – tym samym sygnałem, który unieważnia też stronę w anonimowej
    pamięci podręcznej (``apps.web.page_cache``, nasłuch na ``SiteSettings``), więc zmiana organizatora
    jest widoczna od razu na obu warstwach, a nie dopiero po wygaśnięciu TTL.

    Od 23.09.2026 (prośba organizatora) pozycja nazywa się „Dla szkół/nauczycieli” i jest listą
    rozwijaną: „Rejestracja nauczyciela” (warunki jak wyżej) i „Plakaty do pobrania” (gdy konkurs ma
    opublikowany plakat – ``apps.promo.availability``, wpis w pamięci podręcznej współdzielony ze
    stopką, więc bez dodatkowego zapytania). Plakaty przydają się także zalogowanym, dlatego lista
    istnieje, gdy ma choć jedną pozycję, a znika dopiero bez żadnej.
    """
    from django.urls import reverse

    from apps.accounts.supervisors import registration_enabled_for_request
    from apps.promo.availability import has_public_materials

    user = getattr(request, "user", None)
    anonymous = not (user is not None and getattr(user, "is_authenticated", False))
    kids = []
    if anonymous and registration_enabled_for_request(request):
        kids.append(_child("Rejestracja nauczyciela", reverse("web:register-supervisor"), request))
    try:
        posters = has_public_materials(getattr(request, "competition", None))
    except Exception:  # noqa: BLE001 - awaria pamięci podręcznej/bazy nie może położyć nagłówka
        logger.warning("Nie udało się sprawdzić, czy konkurs ma plakaty – menu bez nich.", exc_info=True)
        posters = False
    if posters:
        kids.append(_child("Plakaty do pobrania", reverse("web:posters"), request))
    if not kids:
        return None
    return _menu_item("nauczyciele", "Dla szkół/nauczycieli", kids[0]["url"], request, kids)


def _child(title: str, url: str, request) -> dict:
    """Pozycja listy rozwijanej – ten sam kształt co dzieci sekcji dokumentów."""
    return {"title": title, "url": url, "active": request.path == url}


def _expandable_children(pages: list, request) -> tuple[dict[int, list[dict]], list[dict]]:
    """Opublikowane dzieci stron-indeksów dokumentów, w jednym zapytaniu na całe menu.

    Zwraca parę: mapę „identyfikator rodzica → pozycje listy rozwijanej” oraz dokumenty
    **wyniesione** do menu głównego (``PROMOTED_DOCUMENT_SLUGS``) – te drugie z listy rozwijanej
    znikają, bo ta sama strona w dwóch miejscach nagłówka byłaby szumem. Strony bez rozwijanej
    listy w mapie nie występują, więc ``dict.get`` w pętli menu daje pustą listę i szablon rysuje
    zwykły odnośnik.
    """
    from django.contrib.contenttypes.models import ContentType
    from wagtail.models import Page

    from .models import DocumentIndexPage

    index_type = ContentType.objects.get_for_model(DocumentIndexPage)
    parents = [page for page in pages if page.content_type_id == index_type.pk]
    if not parents:
        return {}, []

    # Dzieci = potomkowie o głębokości rodzica + 1. Warunek na ``path`` jest indeksowany
    # (treebeard trzyma ścieżkę materializowaną), więc zapytanie zostaje jedno niezależnie
    # od liczby rozwijanych pozycji.
    query = Q()
    for parent in parents:
        query |= Q(path__startswith=parent.path, depth=parent.depth + 1)

    found: dict[int, list[dict]] = {parent.pk: [] for parent in parents}
    promoted: list[dict] = []
    for child in Page.objects.live().filter(query).order_by("path"):
        parent = next((item for item in parents if child.path.startswith(item.path)), None)
        if parent is None:  # pragma: no cover - filtr wyżej nie przepuszcza obcych ścieżek
            continue
        url = child.get_url(request=request)
        if child.slug in PROMOTED_DOCUMENT_SLUGS:
            promoted.append(_menu_item(child.slug, child.title, url, request))
        else:
            found[parent.pk].append({"title": child.title, "url": url, "active": request.path == url})
    return found, promoted


def cms_menu(request) -> dict:
    """Menu serwisu złożone z drzewa witryny, w którą trafiło żądanie.

    ``fallback_allowed`` rozstrzyga o liście zapasowej: pokazujemy ją wyłącznie dla witryny domyślnej,
    bo tylko o jej drzewie wiadomo, że ma te cztery adresy (patrz docstring modułu). Rozstrzygamy
    to **przed** pętlą i na obiekcie, który i tak mamy w ręku – ``is_default_site`` jest kolumną
    tego samego wiersza, więc nie kosztuje ani jednego zapytania więcej.
    """
    from django.urls import get_script_prefix
    from wagtail.models import Page, Site

    fallback_allowed = False
    try:
        site = Site.find_for_request(request)
        if site is None:
            raise Site.DoesNotExist
        fallback_allowed = site.is_default_site
        pages = list(Page.objects.live().in_menu().child_of(site.root_page).order_by("path"))
        children, promoted = _expandable_children(pages, request)
        items = [
            _menu_item(page.slug, page.title, page.get_url(request=request), request, children.get(page.pk))
            for page in pages
            if page.slug not in HIDDEN_MENU_SLUGS
        ]
        items = _ordered(items + promoted)
        if items:
            # Domek stoi pierwszy i prowadzi na stronę główną **tej** witryny. Dokładamy go tylko
            # do menu, które ma cokolwiek: konkurs bez drzewa stron ma nagłówek pusty (docstring
            # modułu), a sam domek udawałby tam nawigację, której nie ma.
            #
            # Adres to korzeń witryny: ``/`` pod własną domeną konkursu, a ``/<prefiks>/`` w konkursie
            # adresowanym prefiksem ścieżki (``get_script_prefix`` ustawia warstwa konkursu).
            # ``root_page.get_url()`` dałoby ten sam napis, ale na zimnej pamięci podręcznej kosztuje
            # zapytanie o ścieżki witryn – na każdej stronie serwisu (wyłapał to budżet zapytań
            # panelu uczestnika).
            home = get_script_prefix()
            items.insert(0, {**_menu_item("", HOME_ITEM_TITLE, home, request), "home": True})
    except (DatabaseError, Site.DoesNotExist, AttributeError):  # pragma: no cover - baza bez drzewa
        # Witryny nie znamy, więc nie wiemy też, czy to ta domyślna – a lista zapasowa opisuje
        # wyłącznie jej drzewo. Puste menu jest tu jedyną odpowiedzią, która nie może być cudza.
        logger.warning("Menu CMS niedostępne – nagłówek zostaje bez pozycji.")
        items = []
    fallback = (
        [
            {
                **item,
                "slug": item["url"].strip("/"),
                "children": [],
                "active": request.path == item["url"],
                "primary": item["url"].strip("/") in PRIMARY_MENU_SLUGS,
                "home": False,
            }
            for item in FALLBACK_MENU
        ]
        if fallback_allowed
        else []
    )
    menu = items or fallback
    if menu:
        # Ostatnia pozycja, za drzewem CMS albo za listą zapasową – tak samo jak domek wyżej, konkurs
        # bez żadnej pozycji menu (drzewo puste, brak witryny domyślnej) nie dostaje jej też: nagłówek
        # bez nawigacji jest tu uczciwą odpowiedzią, a osobna pozycja bez reszty menu wyglądałaby na
        # pomyłkę renderowania, nie na świadomy wybór.
        supervisor_item = _supervisor_menu_item(request)
        if supervisor_item is not None:
            menu = [*menu, supervisor_item]
    # Osobna lista dla przyklejonego paska zamiast filtrowania w szablonie: pasek i menu serwisu
    # czytają to samo źródło, a pasek pokazuje swoje pozycje dopiero po przyklejeniu (skrypt
    # static/js/sticky-bar.js) – dolne menu zostaje w pełnym składzie.
    return {
        "cms_menu": menu,
        "cms_menu_primary": [item for item in menu if item["primary"]],
    }
