# ruff: noqa: F401
# rest_framework.throttling przypisuje ``SimpleRateThrottle.timer = time.time`` w czasie importu.
# Gdyby ten import wypadł po raz pierwszy wewnątrz ``freeze_time``, klasa zapamiętałaby na stałe
# freezegunowy ``fake_time`` i kolejne testy przewracałyby się na TypeError – zależnie od kolejności
# uruchomienia. Import na starcie sesji ustala prawdziwy zegar raz na zawsze.
import rest_framework.throttling  # isort: skip

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Izolacja testów: cache trzyma m.in. liczniki throttlingu, które przeciekałyby między testami."""
    cache.clear()
    yield
    cache.clear()


# =================================================================================================
# Wielokonkursowość (``docs/UNIWERSALNY-ETAP-1.md`` § 7.1)
# =================================================================================================
#
# Dwa konkursy dla całej suity, a nie dla jednego pakietu: reguła izolacji brzmi „obiekt cudzego
# konkursu daje 404”, a żeby ją w ogóle dało się sprawdzić, musi istnieć konkurs **drugi**. Trzyma
# to konftest projektowy, bo fabryki (``apps/*/tests/factories.py``) też muszą znać „konkurs teraz”,
# a żadna z nich nie należy do ``apps.tenancy``.
#
# Wszystkie importy Django/Wagtaila są **wewnątrz** funkcji. Ten plik pytest wczytuje jako konftest
# korzenia, czyli zanim zdąży się wykonać ``django.setup()`` – import modelu na górze modułu
# wywracałby całą sesję na ``AppRegistryNotReady``, i to niezależnie od tego, co uruchamiamy.

#: Host Konkursu #1 w testach. Domeny ``.invalid`` i ``.test`` są zarezerwowane normą (RFC 2606,
#: RFC 6761) i nigdy nie zostaną kupione, więc test, który przypadkiem wyjdzie do sieci, nie trafi
#: pod cudzy adres. Host jest **stałą testu**, a nie odczytem z ``SITE_DOMAIN``: instalacja
#: deweloperska ma tam ``localhost``, produkcyjna własną domenę, a test ma dawać ten sam wynik
#: w obu miejscach.
HOST_COMPETITION = "kwantowa.invalid"

#: Host i identyfikator Konkursu #2. Konkurs istnieje wyłącznie po to, żeby dało się sprawdzić,
#: że go **nie widać** – stąd nazwa bez związku z jakąkolwiek prawdziwą olimpiadą.
HOST_OTHER_COMPETITION = "inny.test"
OTHER_COMPETITION_SLUG = "inny"

#: Sufiksy dopisywane do ``ALLOWED_HOSTS`` tam, gdzie test faktycznie wysyła żądanie pod host
#: konkursu. Kropka wiodąca dopuszcza całą domenę, więc host nieznany (``nieznany.invalid``) też
#: przechodzi walidację Django i dochodzi do **naszego** rozstrzygania – inaczej test hosta bez
#: konkursu kończyłby się kodem 400 i przechodziłby z niewłaściwego powodu.
TEST_HOST_SUFFIXES = (".invalid", ".test")

#: Fikstury pytest-django, których obecność w zamknięciu testu znaczy „ten test ma bazę”.
#: Marker ``django_db`` sprawdzamy osobno, bo wstrzykuje bazę dynamicznie i nie zostawia po sobie
#: nazwy w ``request.fixturenames``.
_DB_FIXTURE_NAMES = frozenset(
    {"db", "transactional_db", "django_db_reset_sequences", "django_db_serialized_rollback"}
)


def root_page():
    """Korzeń drzewa stron; zakłada go, gdy w bazie go nie ma.

    Nie zakładamy, że korzeń postawiony migracją ``cms.0002`` w bazie jest: test transakcyjny
    (przewijający migracje) czyści bazę po sobie, a ``flush`` odtwarza wyłącznie typy treści
    i uprawnienia – nie wiersze wpisane przez ``RunPython``.
    """
    from django.conf import settings as django_settings
    from wagtail.models import Locale, Page

    root = Page.objects.filter(depth=1).order_by("path").first()
    if root is not None:
        return root
    locale = Locale.objects.order_by("pk").first() or Locale.objects.create(
        language_code=django_settings.LANGUAGE_CODE
    )
    return Page.add_root(title="Root", slug="root", locale=locale)


def make_site(hostname: str, *, default: bool = False, own_root: bool = False):
    """Witryna Wagtaila pod wskazanym hostem.

    ``own_root=True`` daje jej **własne** poddrzewo stron. To nie jest kosmetyka: dwie witryny
    wskazujące ten sam korzeń serwowałyby te same strony, więc test „strona konkursu B nie
    otwiera się spod domeny konkursu A” przechodziłby z definicji, nic nie sprawdzając.
    """
    from wagtail.models import Page, Site

    root = root_page()
    if own_root:
        # ``Page`` bazowa, a nie ``cms.HomePage``: korzeń witryny musi tu wyłącznie **istnieć**
        # i mieć własną ścieżkę w drzewie. Strony, których test naprawdę dotyczy, dokłada sam.
        root = root.add_child(instance=Page(title=hostname, slug=hostname.replace(".", "-")))
    return Site.objects.create(
        hostname=hostname, port=80, site_name=hostname, root_page=root, is_default_site=default
    )


def make_competition(hostname: str, slug: str, **kwargs):
    """Konkurs z własną witryną. Domyślnie tak, jak Konkurs #1: własna domena, bez prefiksu."""
    from apps.tenancy.models import Competition, RoutingMode

    site = make_site(
        hostname,
        default=kwargs.pop("default_site", False),
        own_root=kwargs.pop("own_root", True),
    )
    values = {
        "name": f"Olimpiada {slug}",
        "organizer_name": "Organizator testowy",
        "primary_domain": hostname,
        "routing_mode": RoutingMode.DOMAIN,
    }
    values.update(kwargs)
    return Competition.objects.create(site=site, slug=slug, **values)


def existing_competition():
    """Konkurs #1 tak, jak stoi w bazie testowej, albo ``None``. **Niczego nie tworzy.**

    Brak konkursu jest tu odpowiedzią poprawną, a nie stanem do naprawienia: baza po teście
    transakcyjnym (``apps/tenancy/tests/test_migration_0002.py``) bywa pusta, a założenie w takiej
    chwili konkursu „na wszelki wypadek” podstawiałoby testowi świat, którego nie zamawiał.
    """
    from apps.tenancy.models import Competition

    return Competition.objects.select_related("site").order_by("pk").first()


def restored_competition():
    """Konkurs #1 odtworzony po ``flush`` testu transakcyjnego – razem z witryną i korzeniem stron.

    Ta funkcja istnieje od wydania D i z jego powodu. ``flush`` po teście transakcyjnym przywraca
    wyłącznie typy treści i uprawnienia, a nie wiersze wpisane przez ``RunPython`` – więc od tamtej
    chwili w bazie nie ma ani korzenia drzewa stron (odtwarza go ``root_page``), ani Konkursu #1
    z ``tenancy.0002``. Do wydania D wiersz domeny zawodów bez właściciela był tylko niewidoczny;
    od wydania D kolumna ``competition`` jest ``NOT NULL``, więc **każdy** test uruchomiony po
    teście transakcyjnym wywracałby się na ``IntegrityError`` – i to zależnie od kolejności
    pakietu, czyli w sposób, którego nie da się odtworzyć z samej nazwy testu.

    Odtworzenie jest tym samym, co ``root_page`` robi dla drzewa stron: przywróceniem stanu, który
    migracje gwarantują, a nie podstawieniem testowi świata, którego nie zamawiał.
    """
    return make_competition(HOST_COMPETITION, "kwantowa", default_site=True)


def allow_test_hosts(settings) -> None:
    """Dopisuje domeny testowe do ``ALLOWED_HOSTS`` – wołane tylko tam, gdzie leci żądanie.

    Nie jest to fikstura ``autouse``: lista dozwolonych hostów jest częścią konfiguracji, którą
    część testów sprawdza, i nie ma powodu przestawiać jej 2900 razy po to, żeby skorzystało z tego
    kilkadziesiąt testów.
    """
    missing = [suffix for suffix in TEST_HOST_SUFFIXES if suffix not in settings.ALLOWED_HOSTS]
    if missing:
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, *missing]


@pytest.fixture
def competition(db):  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    """Konkurs #1 – ten, który w bazie testowej założyła migracja ``tenancy.0002``.

    Nie zakładamy drugiego „kwantowa”: identyfikator jest unikalny, a przede wszystkim testy mają
    pracować na **tym** konkursie, który dostanie produkcja – razem z jego marką, nadawcą listów
    i przełącznikami wziętymi z danych, a nie z literałów.

    Jedyna zmiana wobec stanu z migracji to **przypięcie hosta** do ``HOST_COMPETITION``. Bez niego
    domena Konkursu #1 zależałaby od ``SITE_DOMAIN`` środowiska, na którym akurat lecą testy, więc
    test porównujący „host konkursu A” z „hostem konkursu B” raz miałby dwie różne wartości, a raz
    tę samą. Przypięcie dotyczy wyłącznie testów, które tę fiksturę **zamówiły** – autouse niżej
    wiąże kontekst bez ruszania czegokolwiek w bazie.
    """
    from wagtail.models import Site

    from apps.tenancy.models import Competition

    row = existing_competition()
    if row is None:
        Site.objects.filter(is_default_site=True).update(is_default_site=False)
        return make_competition(HOST_COMPETITION, "kwantowa", default_site=True, own_root=False)
    Site.objects.exclude(pk=row.site_id).update(is_default_site=False)
    Site.objects.filter(pk=row.site_id).update(hostname=HOST_COMPETITION, is_default_site=True)
    # ``update`` nie wywołuje sygnału synchronizującego domenę, więc wpisujemy ją wprost –
    # w teście ma być widać, co jest ustawiane, a nie co się dzieje przy okazji.
    Competition.objects.filter(pk=row.pk).update(primary_domain=HOST_COMPETITION)
    # Pełne pobranie, a nie ``refresh_from_db``: relacja ``site`` jest już w pamięci ze starym
    # hostem, a to właśnie jej dotyczyła zmiana.
    return Competition.objects.select_related("site").get(pk=row.pk)


@pytest.fixture
def other_competition(db, settings):  # noqa: ARG001 - jw.
    """Konkurs #2 – istnieje wyłącznie po to, żeby dało się sprawdzić, że go nie widać."""
    allow_test_hosts(settings)
    return make_competition(HOST_OTHER_COMPETITION, OTHER_COMPETITION_SLUG)


@pytest.fixture
def as_competition():
    """Menedżer kontekstu „na czas tego bloku konkursem jest X”.

    Odpowiednik tego, co w żądaniu robi ``CompetitionMiddleware``, dla kodu wołanego **poza**
    żądaniem: serwisu składającego list, zadania Celery, komendy. Fikstura oddaje wprost
    ``apps.tenancy.context.competition_context``, żeby test i produkcja przechodziły przez tę samą
    funkcję – kopia tej logiki w testach znaczyłaby, że sprzątanie kontekstu jest sprawdzane na
    kopii, a nie na oryginale.
    """
    from apps.tenancy.context import competition_context

    return competition_context


@pytest.fixture
def client_for(settings):
    """``client_for(competition)`` – klient testowy wysyłający żądania pod domenę tego konkursu.

    Ustawiamy ``HTTP_HOST`` **i** ``SERVER_NAME``: po hoście rozstrzyga konkurs
    (``Site.find_for_request``), a ``SERVER_NAME`` widzi ``request.build_absolute_uri``, czyli
    linki w listach wysyłanych z żądania. Rozjazd między nimi dałby test, w którym uczestnik
    rejestruje się w konkursie A, a link aktywacyjny prowadzi do konkursu B.
    """
    from django.test import Client

    def make(competition, **defaults):
        allow_test_hosts(settings)
        host = competition.primary_domain or competition.site.hostname
        return Client(HTTP_HOST=host, SERVER_NAME=host, **defaults)

    return make


@pytest.fixture
def unbound_competition():
    """Wyłącza autouse'owe związanie kontekstu na czas tego testu.

    Jest dokładnie jeden powód, żeby o to poprosić: test, którego **przedmiotem** jest zachowanie
    przy pustym kontekście – sprzątanie konkursu przez ``CompetitionMiddleware`` albo odwrót
    ``absolute_url`` do witryny domyślnej. Dla takiego testu związanie z zewnątrz byłoby
    podstawieniem odpowiedzi: „kontekst jest pusty” nie da się sprawdzić w kontekście ustawionym.

    Fikstura nic nie robi i tak ma być – liczy się sama jej **obecność** w zamknięciu testu, którą
    czyta ``_bind_competition``. Fikstura, a nie marker, bo markery wymagają wpisu w
    ``pyproject.toml`` (``--strict-markers``), a to jest plik współdzielony z resztą zadań.
    """
    return None


def _rewinds_migrations(request) -> bool:
    """Czy ten test jest transakcyjny, czyli czy wolno mu przewijać migracje i czyścić bazę.

    Pytanie zadajemy wyłącznie po to, żeby **nie** odtwarzać takiemu testowi Konkursu #1: testy
    przewijające migracje (``tenancy.0002``, ``accounts.0010``, ``cms``) opisują bazę same, wiersz
    po wierszu, i wstawiony im z zewnątrz konkurs trzymałby ``PROTECT``-em witrynę, którą za chwilę
    kasują. Dane budują fabrykami, a te odtwarzają konkurs same, gdy naprawdę go potrzebują
    (``apps/tenancy/tests/factories.py``).
    """
    marker = request.node.get_closest_marker("django_db")
    if marker is not None and marker.kwargs.get("transaction"):
        return True
    return "transactional_db" in request.fixturenames


def _wants_database(request) -> bool:
    """Czy ten test ma w ogóle bazę.

    Sprawdzamy jedno i drugie, bo pytest-django ma dwie drogi: jawną fiksturę (``db``,
    ``transactional_db``) i marker ``django_db``, który wstrzykuje bazę dynamicznie i nie zostawia
    po sobie nazwy w zamknięciu fikstur.
    """
    if request.node.get_closest_marker("django_db"):
        return True
    return bool(_DB_FIXTURE_NAMES & set(request.fixturenames))


@pytest.fixture(autouse=True)
def _bind_competition(request):
    """Ustawia konkurs kontekstu na Konkurs #1 dla **każdego** testu z bazą.

    ``autouse`` jest tu świadomą decyzją dokumentu (§ 7.1): bez niego każdy z blisko trzech tysięcy
    istniejących testów wymagałby dopisania fikstury, żeby kod czytający ``current_competition()``
    (dziś: ``apps.accounts.activation.absolute_url`` i ``apps.core.status``) zachowywał się tak, jak
    w żądaniu. Z nim istniejące testy widzą dokładnie to, co widziały, a testy wielokonkursowe
    wskazują konkurs jawnie – przez ``as_competition`` albo ``client_for``.

    Trzy decyzje, każda z powodem:

    - **nic nie tworzymy ponad to, co gwarantują migracje** (``existing_competition``): świat testu
      ma opisywać test, a nie fikstura globalna. Jedynym wyjątkiem jest baza wyczyszczona przez
      wcześniejszy test transakcyjny – tam Konkurs #1 jest **odtwarzany** (``restored_competition``,
      wydanie D), bo jego brak jest skutkiem ``flush``, a nie decyzją tego testu. Testom
      transakcyjnym niczego nie odtwarzamy: to one przewijają migracje i kasują witryny,
    - **nie dotykamy bazy bez potrzeby**: test bez bazy nie dostaje jej tylnymi drzwiami, bo
      inaczej ``autouse`` zamieniłby każdy test jednostkowy w test bazodanowy,
    - ``getfixturevalue("db")`` zamiast zadeklarowanej zależności: fikstura ``autouse`` bywa
      przygotowywana **przed** fiksturami zamówionymi przez test, a sięgnięcie do bazy przed
      ustawieniem bazy przez pytest-django kończy się blokadą dostępu. Wywołanie jawne porządkuje
      kolejność, a przy teście transakcyjnym oddaje tę samą (transakcyjną) bazę, bo o wariancie
      rozstrzyga marker testu, nie nazwa, o którą prosimy.
    """
    from apps.tenancy.context import competition_context

    if not _wants_database(request) or "unbound_competition" in request.fixturenames:
        yield None
        return
    request.getfixturevalue("db")
    # Test, który sam zamówił ``competition``, dostaje konkurs z **przypiętym** hostem – ten sam
    # obiekt, którego użyje w żądaniu. Inaczej host w kontekście i host w kliencie byłyby różne.
    if "competition" in request.fixturenames:
        current = request.getfixturevalue("competition")
    else:
        current = existing_competition()
        if current is None and not _rewinds_migrations(request):
            current = restored_competition()
    with competition_context(current):
        yield current
