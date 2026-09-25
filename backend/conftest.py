# ruff: noqa: F401
# rest_framework.throttling przypisuje ``SimpleRateThrottle.timer = time.time`` w czasie importu.
# Gdyby ten import wypadł po raz pierwszy wewnątrz ``freeze_time``, klasa zapamiętałaby na stałe
# freezegunowy ``fake_time`` i kolejne testy przewracałyby się na TypeError – zależnie od kolejności
# uruchomienia. Import na starcie sesji ustala prawdziwy zegar raz na zawsze.
import rest_framework.throttling  # isort: skip

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from django.core.cache import cache

#: Katalog ``backend/`` – tu leżą ``locale/`` i ``static/``, niezależnie od katalogu, z którego
#: ktoś uruchomił pytest.
BACKEND_DIR = Path(__file__).resolve().parent


# =================================================================================================
# Infrastruktura przebiegu (``docs/TESTY.md``)
# =================================================================================================


def pytest_configure(config):
    _compile_translations(config)
    _group_scoped_tests_under_xdist(config)


def _compile_translations(config) -> None:
    """Kompiluje ``locale/*/LC_MESSAGES/django.po`` do ``.mo``, gdy ``.mo`` brakuje albo jest starszy.

    Obraz robi to przy budowaniu, a CI w osobnym kroku – ale lokalny przebieg w kontenerze
    z zamontowanym kodem nie robi tego nigdzie, a brak ``.mo`` nie jest błędem Django: angielski
    po prostu cicho oddaje polskie napisy, więc testy języka listów i panelu padały z komunikatem
    o treści, a nie o brakującym pliku. Kompilacja trwa ułamek sekundy i dzieje się raz na sesję
    (pod xdist – w procesie sterującym, zanim wystartują workery).

    Brak ``msgfmt`` (host bez gettext) nie przerywa sesji, tylko zostawia ostrzeżenie z nazwą
    narzędzia – testy tłumaczeń powiedzą wtedy same, czego im brakuje.
    """
    if hasattr(config, "workerinput"):  # worker xdist: kompilował już proces sterujący
        return
    stale = [
        po
        for po in sorted(BACKEND_DIR.glob("locale/*/LC_MESSAGES/*.po"))
        if not po.with_suffix(".mo").exists() or po.with_suffix(".mo").stat().st_mtime < po.stat().st_mtime
    ]
    if not stale:
        return
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        config.issue_config_time_warning(
            pytest.PytestConfigWarning(
                "Brak skompilowanych katalogów tłumaczeń (.mo) i brak programu msgfmt (pakiet gettext) – "
                "testy wersji angielskiej nie przejdą. Uruchom testy w kontenerze albo doinstaluj gettext."
            ),
            stacklevel=2,
        )
        return
    for po in stale:
        # Zapis przez plik tymczasowy i ``os.replace``: dwie sesje uruchomione naraz na tym samym
        # katalogu nie zobaczą nigdy połowy pliku.
        fd, tmp = tempfile.mkstemp(suffix=".mo", dir=po.parent)
        os.close(fd)
        try:
            subprocess.run([msgfmt, "-o", tmp, str(po)], check=True)  # noqa: S603 - ścieżka z which
            os.replace(tmp, po.with_suffix(".mo"))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    # Katalog mógł zostać wczytany (bez pliku) jeszcze przy ``django.setup()`` – zapominamy go,
    # żeby pierwsze ``activate("en")`` sięgnęło po świeżo skompilowany plik.
    from django.utils.translation import trans_real

    trans_real._translations = {}


def _group_scoped_tests_under_xdist(config) -> None:
    """``-n N`` bez ``--dist`` rozdziela testy po jednym – zamieniamy to na ``loadgroup``.

    ``loadgroup`` rozdziela dokładnie tak samo jak ``load``, z jednym wyjątkiem: testy z tym samym
    ``xdist_group`` idą do jednego workera. Tak oznaczamy moduły z fiksturą o zasięgu modułu,
    która jest droga (przewinięta baza w testach migracji, ``migration_helpers``) – rozrzucone po
    workerach płaciłyby za nią tyle razy, ilu workerów dotknęły.
    """
    if getattr(config.option, "dist", "no") == "load":
        config.option.dist = "loadgroup"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    # ``tryfirst``: worker xdist dokleja grupę do identyfikatora testu we własnym
    # ``pytest_collection_modifyitems`` – marker ``xdist_group`` dołożony po nim nie miałby skutku.
    """Markery wynikające z **kształtu** testu, a nie z pamięci autora (``docs/TESTY.md``).

    - ``slow`` dostaje każdy test transakcyjny (``transaction=True``/``transactional_db``: po
      teście ``TRUNCATE`` wszystkich tabel i odtworzenie uprawnień) i każdy test migracji
      (``migrations``: przewijanie bazy). Szybka pętla lokalna to ``-m "not slow"``; CI uruchamia
      wszystko.
    - test migracji dostaje ``xdist_group`` swojego modułu: przewinięta baza jest fiksturą modułu
      i ma powstać raz, a nie raz na worker,
    - w obrębie modułu testy migracji idą **po** pozostałych: fikstura modułu trzyma bazę
      przewiniętą aż do ostatniego testu modułu, więc zwykły test po niej zastałby cudzy schemat
      (pilnuje tego też ``_bind_competition`` – zwykły test na przewiniętej bazie kończy się
      błędem, a nie wynikiem z przypadku).
    """
    slow = pytest.mark.slow
    for item in items:
        is_migration = item.get_closest_marker("migrations") is not None
        if (_is_transactional(item) or is_migration) and item.get_closest_marker("slow") is None:
            item.add_marker(slow)
        if is_migration:
            item.add_marker(pytest.mark.xdist_group(name=item.module.__name__))
    items[:] = _migration_tests_last_in_their_module(items)


def _migration_tests_last_in_their_module(items):
    """Stabilnie: w każdym ciągłym bloku testów jednego modułu testy ``migrations`` na koniec."""
    ordered, block = [], []

    def flush():
        ordered.extend(item for item in block if item.get_closest_marker("migrations") is None)
        ordered.extend(item for item in block if item.get_closest_marker("migrations") is not None)
        block.clear()

    for item in items:
        if block and getattr(item, "module", None) is not getattr(block[0], "module", None):
            flush()
        block.append(item)
    flush()
    return ordered


def _is_transactional(item) -> bool:
    """Czy test kończy się ``flush``-em (``TransactionTestCase``), a nie wycofaniem transakcji."""
    db_marker = item.get_closest_marker("django_db")
    if db_marker is not None and db_marker.kwargs.get("transaction"):
        return True
    return "transactional_db" in getattr(item, "fixturenames", ())


# --- baza po teście transakcyjnym --------------------------------------------------------------
#
# Test transakcyjny kończy się ``flush``-em: pusta **każda** tabela, a potem ``post_migrate`` odtwarza
# typy treści i uprawnienia – ale nie wiersze wpisane migracjami (Konkurs #1 z ``tenancy.0002``,
# drzewo stron z ``cms.0002``, ustawienia serwisu). Do 25.09.2026 zostawało to tak, a kolejne testy
# w tym samym procesie dostawały konkurs „odtworzony” przez ``restored_competition`` – bez nadawcy,
# bez prefiksu tematów, bez stron. Wynik testu zależał więc od tego, czy **przed nim** w tym samym
# procesie biegł test transakcyjny: w jednym procesie kolejność była stała, pod xdist i w shardach
# CI – nie. ``--reuse-db`` utrwalał ten stan między sesjami.
#
# Teraz baza wraca do stanu po migracjach dokładnie: migawka (Django ``serialize_db_to_string``,
# ten sam mechanizm co ``serialized_rollback``) powstaje raz na sesję – tylko wtedy, gdy w zbiorze
# jest choć jeden test transakcyjny – a po każdym takim teście baza jest czyszczona bez
# ``post_migrate`` i ładowana z migawki (z typami treści i uprawnieniami o tych samych kluczach).

_db_snapshot: dict[str, str] = {}

#: Moduł, którego fikstura trzyma w tej chwili przewiniętą bazę (``migration_helpers.rewound_database``).
REWOUND_DATABASE: dict[str, str | None] = {"module": None}


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker, request):  # noqa: ARG001 - nadpisanie fikstury
    """Baza testowa pytest-django + migawka stanu po migracjach (patrz komentarz wyżej)."""
    if any(_is_transactional(item) for item in request.session.items):
        from django.db import connection

        with django_db_blocker.unblock():
            _db_snapshot["default"] = connection.creation.serialize_db_to_string()
    yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        return (yield)
    finally:
        # ``nextitem is None`` – koniec sesji: bez ``--reuse-db`` baza testowa właśnie zniknęła,
        # z nim zostaje dla następnej sesji i ma zostać w stanie po migracjach.
        keeps_db = item.config.getoption("reuse_db") and not item.config.getoption("create_db")
        if "default" in _db_snapshot and _is_transactional(item) and (nextitem is not None or keeps_db):
            _restore_db_snapshot(item.config)


def _restore_db_snapshot(config) -> None:
    from django.core.management import call_command
    from django.db import connection
    from pytest_django.plugin import blocking_manager_key

    with config.stash[blocking_manager_key].unblock():
        call_command(
            "flush", interactive=False, inhibit_post_migrate=True, reset_sequences=False, verbosity=0
        )
        connection.creation.deserialize_db_from_string(_db_snapshot["default"])


@pytest.fixture
def django_assert_num_queries():
    """Jak w pytest-django, ale niepowodzenie pokazuje zapytania zawsze, a nie tylko z ``-v``.

    Raport (powtórzenia na górze, potem pełna lista) składa ``apps/core/tests/query_budgets.py``.
    """
    from functools import partial

    from apps.core.tests.query_budgets import assert_queries

    return partial(assert_queries, exact=True)


@pytest.fixture
def django_assert_max_num_queries():
    """Jak ``django_assert_num_queries`` wyżej, dla progu „najwyżej N”."""
    from functools import partial

    from apps.core.tests.query_budgets import assert_queries

    return partial(assert_queries, exact=False)


@pytest.fixture(autouse=True)
def _media_tmp(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Izolacja testów: cache trzyma m.in. liczniki throttlingu, które przeciekałyby między testami."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clear_process_caches():
    """Pamięci podręczne **procesu** (poza ``django.core.cache``) – od zera w każdym teście.

    Dwa przełączniki czytane przy każdej odpowiedzi HTML trzymają wynik przez 30 s w słowniku
    modułu: identyfikator GA4 (``apps.cms.analytics``) i rejestracja opiekunów
    (``apps.accounts.supervisors``). Wycofanie transakcji ich nie czyści, a klucz (identyfikator
    witryny domyślnej) jest w każdym teście ten sam – więc bez tego wynik testu zależał od tego,
    co przed nim biegło w **tym samym procesie**. Pod xdist kolejność zmienia się z każdym
    przebiegiem, więc zależność od kolejności zamieniłaby się w losowe czerwone testy.
    """
    from apps.accounts.supervisors import reset_registration_cache
    from apps.cms import analytics

    analytics._cache.clear()
    reset_registration_cache()
    yield
    analytics._cache.clear()
    reset_registration_cache()


# =================================================================================================
# Skaner antywirusowy: podmieniony na poziomie gniazda dla każdego testu
# =================================================================================================

#: Fragment wzorca EICAR – po nim podstawiony clamd rozpoznaje „wirusa”, jak prawdziwy.
EICAR_MARK = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
#: Sygnatura, którą podstawiony clamd zgłasza dla EICAR-a (ta sama, co w clamav 1.4).
FAKE_CLAMD_SIGNATURE = "Win.Test.EICAR_HDB-1"


class FakeClamdConnection:
    """Gniazdo „do clamd”, które mówi protokołem ``zINSTREAM``/``zPING`` bez sieci.

    Ramki są parsowane **ściśle** (4-bajtowa długość, porcja, zero na końcu), więc test przez ten
    obiekt sprawdza też sposób, w jaki ``scan_stream`` pakuje dane – a nie tylko to, co zadanie
    robi z werdyktem.
    """

    def __init__(self):
        self.sent = bytearray()
        self._answered = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass

    def recv(self, bufsize):
        if self._answered:
            return b""
        self._answered = True
        return self._answer()

    def _answer(self) -> bytes:
        data = bytes(self.sent)
        if data.startswith(b"zPING\0"):
            return b"PONG\0"
        if not data.startswith(b"zINSTREAM\0"):
            return b"UNKNOWN COMMAND\0"
        payload, offset = bytearray(), len(b"zINSTREAM\0")
        while True:
            if offset + 4 > len(data):
                return b"INSTREAM: truncated frame ERROR\0"
            size = int.from_bytes(data[offset : offset + 4], "big")
            offset += 4
            if size == 0:
                break
            payload += data[offset : offset + size]
            offset += size
        if EICAR_MARK in payload:
            return f"stream: {FAKE_CLAMD_SIGNATURE} FOUND\0".encode()
        return b"stream: OK\0"


class FakeClamdSocketModule:
    """To, czego ``apps.submissions.antivirus`` używa z modułu ``socket`` – i nic więcej."""

    def __init__(self):
        self.connections: list[FakeClamdConnection] = []

    def create_connection(self, address, timeout=None):
        connection = FakeClamdConnection()
        self.connections.append(connection)
        return connection


@pytest.fixture(autouse=True)
def _fake_clamd(request, monkeypatch):
    """Żaden test nie rozmawia z prawdziwym clamd – chyba że poprosi o to markerem ``clamav``.

    Podmiana jest na poziomie **gniazda** w ``apps.submissions.antivirus``, a nie funkcji
    ``scan_stream``: tę funkcję importuje po nazwie pięć modułów (i każdy nowy zrobi to samo),
    więc podmiana nazwy łatałaby tylko te, o których ktoś pamiętał. Wcześniej testy uploadu spoza
    ``apps/submissions`` szły do prawdziwego ``clamav:3310`` – w kontenerze compose działały (wolno),
    w CI kończyły się ponowieniami zadania, a na maszynie bez compose zależały od DNS-u.

    Pakiety, które sterują werdyktem (``clamd`` w ``apps/submissions/tests/conftest.py``
    i ``apps/ai_grading/tests/conftest.py``), dalej podmieniają ``scan_stream`` – ta fikstura
    jest siatką pod spodem, a nie ich zastępstwem.
    """
    if request.node.get_closest_marker("clamav"):
        yield None
        return
    from apps.submissions import antivirus

    fake = FakeClamdSocketModule()
    monkeypatch.setattr(antivirus, "socket", fake)
    yield fake


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
    transakcyjnym przerwanym w połowie (``--reuse-db``) bywa pusta, a założenie w takiej
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

    Od 25.09.2026 to jest już tylko siatka bezpieczeństwa: po każdym teście transakcyjnym baza wraca
    do stanu po migracjach z migawki (``_restore_db_snapshot``), więc ta ścieżka zadziała wyłącznie na
    bazie ``--reuse-db`` zostawionej przez przerwany przebieg.
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
    return pinned_competition()


def pinned_competition():
    """Ciało fikstury ``competition`` – osobno, bo woła je też fikstura modułu testów migracji."""
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
def english_enabled_site(db):  # noqa: ARG001 - fikstura bazy, używana przez efekt uboczny
    """``english_enabled_site()`` – włącza angielską wersję interfejsu witrynie tego konkursu.

    Przełącznik ``cms.SiteSettings.english_interface_enabled`` jest **domyślnie wyłączony**, bo tak
    poprosił organizator Olimpiady Kwantowej („do polskiej olimpiady niech będzie wersja tylko
    w języku polskim na razie”). Każdy test, którego przedmiotem jest angielski – nagłówek
    ``Accept-Language``, flaga w pasku konta, panel uczestnika po angielsku, list po angielsku –
    musi więc ten stan włączyć **jawnie**. Testy samej polskości zaczynają od stanu domyślnego
    i tej fikstury nie wołają; to jest ta sama umowa, co przy ``supervisor_registration_on``.

    Fikstura jest wywoływalna, a nie „gotowym wierszem”, bo pytanie jest per witryna: żądania
    ``Client()`` idą pod witrynę domyślną (tak rozstrzyga ``Site.find_for_request``), a listy
    o konkursie – pod witrynę **tego** konkursu, i bywa to inna witryna niż domyślna.
    """

    def enable(competition=None):
        from wagtail.models import Site

        from apps.cms.models import SiteSettings

        site = competition.site if competition is not None else Site.objects.get(is_default_site=True)
        row = SiteSettings.for_site(site)
        row.english_interface_enabled = True
        row.save()
        return row

    return enable


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
    rewound_module = REWOUND_DATABASE.get("module")
    if rewound_module is not None and not request.node.get_closest_marker("migrations"):
        pytest.fail(
            f"Test bez markera ``migrations`` biegnie na bazie przewiniętej przez fiksturę modułu "
            f"{rewound_module} – oznacz go ``@pytest.mark.migrations`` albo przenieś do innego modułu."
        )
    if request.node.get_closest_marker("migrations"):
        # Test migracji zastaje bazę **przewiniętą** fiksturą modułu (``migration_helpers``), a żywy
        # model konkursu pyta o kolumny, których w tej chwili w tabeli nie ma. Kontekst wiąże wtedy
        # sama fikstura modułu – tutaj tylko przełączamy więzy na natychmiastowe (powód przy
        # ``migration_helpers.immediate_constraints``).
        from apps.core.tests.migration_helpers import immediate_constraints

        request.getfixturevalue("db")
        immediate_constraints()
        if "competition" in request.fixturenames:
            with competition_context(request.getfixturevalue("competition")) as current:
                yield current
        else:
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
