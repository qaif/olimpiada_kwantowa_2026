"""Plan zapytania wyszukiwarki szkół po migracji ``0006_pg_trgm_search_indexes``.

**Dlaczego ta migracja w ogóle powstała.** Statystyki produkcji (22.09.2026, okno 15 dni):
``schools_school`` (8118 wierszy) miała 864 sekwencyjne przejścia po całej tabeli (7,0 mln
przeczytanych wierszy) wobec 247 tys. skanów indeksowych, a jedno zapytanie wyszukiwarki kosztowało
130–185 ms – niemal w całości ten ``Seq Scan``. Powód: ``search_schools`` i ``search_cities``
(``apps.schools.api``) pytają ``search_text``/``city_search`` operatorem ``contains`` (dopasowanie
**w środku** napisu), którego zwykły B-tree – nawet z automatycznym indeksem
``varchar_pattern_ops`` pod ``LIKE 'prefiks%'`` – nie obsłuży.

Ten moduł nie testuje **wyników** wyszukiwarki (o to dba reszta ``apps/schools/tests`` – migracja
nie zmienia ani jednego wiersza odpowiedzi, tylko plan, którym baza go liczy), tylko **plan
zapytania i jego poprawność**.

**Zmierzone ręcznie, poza tym plikiem** (baza deweloperska, pełny wykaz – 8118 wierszy,
``EXPLAIN (ANALYZE, BUFFERS)``, ``ANALYZE schools_school`` przed pomiarem):

- ``search_text__contains='lice'`` (bez miasta – zapytanie, które wcześniej czytało prawie całą
  tabelę): ``Seq Scan`` 5,1 ms / 547 buforów → ``Bitmap Index Scan`` na ``schools_search_trgm_idx``
  3,9 ms / 220 buforów (patrz opis w PR),
- ``city_search__contains='|wro'`` (dzielnica): ``Bitmap Index Scan`` na ``schools_city_trgm_idx``
  włącza się do istniejącego ``BitmapOr`` z indeksem prefiksowym (``…_like``).

**Dlaczego testy niżej nie sprawdzają samego naturalnego wyboru planisty.** Na bazie deweloperskiej
powyższe zapytanie samo z siebie wybiera plan z indeksem – różnica kosztu (``Bitmap Heap Scan``
cost≈621 vs ``Seq Scan`` cost≈655) jest jednak na tyle mała, że na świeżej bazie testowej
(``--create-db``, inny rozkład statystyk ``ANALYZE`` mimo tych samych 8118 wierszy) planista
czasem i tak wybiera ``Seq Scan`` – to obserwowane zachowanie, nie błąd indeksu. Zamiast więc
sprawdzać, co „danego dnia” wybierze planista (dokładnie ten przypadek przewiduje zadanie: „jeśli
planista i tak zrobi seq scan na małej tabeli testowej, wystarczy sprawdzić, że indeks istnieje”),
testy wymuszają oba warianty planu przez ``enable_seqscan`` i porównują **wyniki** – to jest
mocniejszy dowód, że indeks jest poprawny i produkuje ten sam zbiór wierszy co ``Seq Scan``,
niezależnie od tego, który plan wybrałby planista bez wymuszenia.
"""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models import Q

from apps.schools.models import School
from apps.schools.normalise import DISTRICT_SEPARATOR

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="GIN + pg_trgm to rozszerzenie PostgreSQL – na innym silniku migracja się nie stosuje.",
)


def _seed():
    """Pełny wykaz (8118 wierszy, ten sam plik co produkcja) + świeże statystyki planisty."""
    call_command("seed_schools")
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE schools_school")


def _rows_under_plan(sql: str, params, *, seqscan: bool) -> tuple[str, set]:
    """Plan i zbiór ``id`` zwróconych wierszy przy wymuszonym (albo zablokowanym) ``Seq Scan``.

    ``enable_seqscan``/``enable_bitmapscan``/``enable_indexscan`` są ``SET LOCAL`` – działają do
    końca **tej** transakcji testu (``pytest.mark.django_db`` opakowuje test w ``atomic``) i nie
    wyciekają do innych testów.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = %s" % ("on" if seqscan else "off"))
        cursor.execute("SET LOCAL enable_bitmapscan = %s" % ("off" if seqscan else "on"))
        cursor.execute("SET LOCAL enable_indexscan = %s" % ("off" if seqscan else "on"))
        cursor.execute(f"EXPLAIN {sql}", params)
        plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute(sql, params)
        rows = {row[0] for row in cursor.fetchall()}
    return plan, rows


@pytest.mark.django_db
def test_pg_trgm_extension_is_enabled():
    """Warunek konieczny dla obu indeksów GIN niżej – bez rozszerzenia ``CREATE INDEX`` by padł."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = %s", ["pg_trgm"])
        assert cursor.fetchone() is not None


@pytest.mark.django_db
def test_trigram_indexes_exist_and_unused_indexes_are_gone():
    """Nazwane indeksy z migracji ``0006`` – dokładnie te, które ``Meta.indexes`` deklaruje.

    Sprawdzamy też **nieobecność** trzech indeksów bez ani jednego skanu na produkcji: złożonego
    ``schools_city_kind_idx`` (porządek liczy ``Case`` w Pythonie, nie kolumna ``kind`` – indeks
    nigdy nie mógł posłużyć sortowaniu) i pary spod ``db_index=True`` na ``search_text``
    (``schools_school_search_text_…`` i jej bliźniak ``_like``), zastąpionej przez
    ``schools_search_trgm_idx``.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", ["schools_school"])
        names = {row[0] for row in cursor.fetchall()}

    assert "schools_search_trgm_idx" in names
    assert "schools_city_trgm_idx" in names
    # Prefiks (``city_search__startswith``) zostaje przy automatycznym indeksie B-tree – tego
    # migracja świadomie nie rusza (patrz docstring modelu). Nazwę dokłada Django hashem, więc
    # sprawdzamy wzorzec, a nie dokładny napis.
    assert any(name.startswith("schools_school_city_search_") and name.endswith("_like") for name in names), (
        f"indeks pattern-ops po city_search miał zostać nietknięty, a nie ma go: {names}"
    )

    assert "schools_city_kind_idx" not in names
    assert not any(name.startswith("schools_school_search_text_") for name in names), (
        f"indeks po search_text miał zniknąć razem z db_index=True, a jest: {names}"
    )


@pytest.mark.django_db
def test_search_text_contains_index_matches_seq_scan_results():
    """``search_text__contains`` (koniunkcja tokenów w ``search_schools``) – bez miasta.

    To jest dokładnie zapytanie, które na produkcji czytało prawie całą tabelę (864 seq scany /
    7,0 mln wierszy w 15 dni). Test wymusza plan z indeksem GIN i osobno plan ``Seq Scan`` na tym
    samym zapytaniu i porównuje zbiory zwróconych ``id`` – identyczne, bo indeks tylko przyspiesza
    dostęp, nie zmienia dopasowania.
    """
    _seed()
    queryset = School.objects.filter(is_active=True).filter(search_text__contains="lice")
    sql, params = queryset.query.sql_with_params()

    index_plan, index_rows = _rows_under_plan(sql, params, seqscan=False)
    seq_plan, seq_rows = _rows_under_plan(sql, params, seqscan=True)

    assert "schools_search_trgm_idx" in index_plan, index_plan
    assert "Seq Scan on schools_school" in seq_plan, seq_plan
    assert index_rows == seq_rows
    assert len(index_rows) > 0


@pytest.mark.django_db
def test_city_district_contains_index_matches_seq_scan_results():
    """Podpowiedź dzielnicy (``search_cities``): ``city_search__contains="|dzielnica"``.

    Prefiks gminy (``city_search__startswith``) dalej idzie przez istniejący indeks
    ``varchar_pattern_ops`` – to jest ta część zapytania, której migracja nie dotyka. Trigram
    obsługuje wyłącznie drugą połowę warunku (``OR``, dzielnica); test – tak jak wyżej – dowodzi
    równości wyników między planem z indeksem i planem ``Seq Scan``, a nie zgaduje, co wybrałby
    planista bez wymuszenia.
    """
    _seed()
    queryset = School.objects.filter(is_active=True).filter(
        Q(city_search__startswith="wro") | Q(city_search__contains=f"{DISTRICT_SEPARATOR}wro")
    )
    sql, params = queryset.query.sql_with_params()

    index_plan, index_rows = _rows_under_plan(sql, params, seqscan=False)
    seq_plan, seq_rows = _rows_under_plan(sql, params, seqscan=True)

    assert "schools_city_trgm_idx" in index_plan, index_plan
    assert "Seq Scan on schools_school" in seq_plan, seq_plan
    assert index_rows == seq_rows
    assert len(index_rows) > 0
