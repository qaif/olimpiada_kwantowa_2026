"""Snapshot tabel wyników – test, który decyduje o wydaniach I i J (§ 5.2, T28).

Pytanie jest jedno i nie da się na nie odpowiedzieć przeglądem kodu: **czy przebieg Olimpiady
Kwantowej wyrażony jako dane daje dokładnie te same tabele, co przebieg wyrażony w kodzie.**

Ta sama edycja liczy się tu **dwa razy**:

``mode="kind"``
    flaga ``process_editor`` wyłączona – kolejność etapów z krotki ``STAGE_ORDER``, próg
    z ``QualificationRule``. To jest stan Konkursu #1 i stan produkcji.
``mode="pipeline"``
    flaga włączona – kolejność z ``PipelineStep``, próg z ``TransitionRule``. Wiersze pisze
    **migracja** ``competitions.0024`` (``results_fixture.write_pipeline_rows``), a nie pętla
    napisana na potrzeby testu: gdyby migracja i serwis rozumiały „N na województwo” inaczej, ten
    test ma paść, a nie zgodzić się same ze sobą dwie kopie tej samej pomyłki.

Porównujemy **siedem** rzeczy, bo każda jest osobnym sposobem na cichą regresję:

1. ``compute_stage_results(stage)`` – lista słowników, klucz po kluczu i w tej samej kolejności
   wierszy. Porównanie podzbioru przepuściłoby dołożony klucz ``category``, czyli dokładnie ten
   błąd, który wyszedłby dopiero w ogłoszonej tabeli;
2. ``rank`` każdego wiersza – remisy i porządek ``public_code`` to dwie różne decyzje;
3. zbiór zakwalifikowanych wpisów – próg jest o tym, kto przechodzi dalej. Wpis wskazujemy
   kodem publicznym, a nie ``entry_id``: uzasadnienie przy :func:`stage_tables`;
4. ``build_snapshot(rows, anonymization)`` dla **każdego** z trzech trybów anonimizacji – to jest
   obiekt, który trafia do ``ResultsPublication.snapshot`` i na stronę publiczną;
5. ``next_stage_of(stage)`` – kolejność zawodów, czyli to, dokąd trafiają zakwalifikowani;
6. odpowiedź publiczna (``PublicResultsSerializer``) i statystyki (``stage_statistics``) – dwa
   odczyty, które publiczność widzi zamiast samego snapshotu;
7. liczniki przeliczenia (``apply_qualification``), razem z liczbą wpisów założonych i usuniętych
   w następnym etapie.

Poza porównaniem dwóch dróg tabela drogi **dzisiejszej** jest dodatkowo zamrożona w pliku
``results_snapshot.json``. Porównanie dwóch dróg złapie rozjazd między nimi, ale nie złapie
zmiany, która dotknie **obu** naraz (poprawka w ``_rank_rows``, w ``_display_name``, w histogramie
statystyk). Zamrożony plik jest na to jedyną odpowiedzią: po takiej zmianie test pokazuje różnicę
wiersz po wierszu i ktoś musi świadomie powiedzieć, że tak ma być.

    Regeneracja pliku: ``RESULTS_SNAPSHOT_REGENERATE=1 pytest apps/tenancy/tests/test_results_snapshot.py``
    Zapis pliku jest **zawsze** zmianą do przeczytania w przeglądzie – nie ma innego powodu, żeby
    tę zmienną ustawić, niż świadoma zmiana kształtu ogłaszanej tabeli.

Trzeci zestaw danych z § 5.2 – kopia produkcyjna – **nie jest testem w CI** (dane osobowe nie
wchodzą do repozytorium). Jest punktem 16 ręcznej listy kontrolnej z § 0.5.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.db import transaction

from apps.competitions.models import Stage
from apps.core.points import jsonable_points
from apps.results.models import Anonymization
from apps.results.serializers import PublicResultsSerializer
from apps.results.services import (
    CATEGORIES_FLAG,
    PROCESS_EDITOR_FLAG,
    apply_qualification,
    build_snapshot,
    next_stage_of,
    publish_results,
    stage_qualification,
)
from apps.results.statistics import stage_statistics
from apps.tenancy.models import Competition

from . import golden as golden_module
from . import results_fixture

pytestmark = pytest.mark.django_db

#: Zamrożona tabela drogi dzisiejszej. Plik leży obok testu, a nie w ``fixtures/``: czyta go
#: wyłącznie ten jeden test i nikt go nie ładuje do bazy.
SNAPSHOT_PATH = Path(__file__).with_name("results_snapshot.json")

#: Przełącznik regeneracji. Zmienna środowiskowa, a nie argument pytesta, bo argumenty wymagają
#: wpisu w ``pyproject.toml`` – a to jest plik wspólny dla wszystkich zadań etapu.
REGENERATE_ENV = "RESULTS_SNAPSHOT_REGENERATE"

#: Kolejność, w której liczymy etapy – ta sama, w której idą zawody. Kolejność ma znaczenie:
#: kwalifikacja etapu zakłada i sprząta wpisy w następnym (``_sync_next_stage``), więc policzenie
#: finału przed eliminacjami opisywałoby świat, którego produkcja nie zna.
STAGE_ORDER = ("training", "elim", "district", "final")

#: Klucze wiersza roboczego, które są identyfikatorem wiersza bazy, a nie treścią tabeli. Między
#: dwiema drogami ``participant_id`` jest równy (uczestnicy powstają raz, przed obydwoma
#: przebiegami), ale w zamrożonym pliku nie ma czego szukać: jego wartość zależy od kolejności
#: testów w suicie.
VOLATILE_ROW_KEYS = ("participant_id",)


# --- mechanika dwóch przebiegów -------------------------------------------------------------------


@contextmanager
def _rolled_back():
    """Blok, po którym baza wraca do stanu sprzed niego (punkt zapisu).

    Obie drogi muszą wyjść **z tego samego świata**, a przeliczenie wyników świat zmienia: ustawia
    statusy wpisów, zakłada wpisy w następnym etapie i zapisuje publikację. Drugi przebieg na
    wyniku pierwszego porównywałby tabelę etapu przed kwalifikacją z tabelą po niej i wywracałby
    się na kolumnie ``status`` – czyli na własnym błędzie, a nie na regresji.

    Punkt zapisu, a nie druga edycja obok: dwa światy znaczyłyby dwa komplety identyfikatorów
    i dwa komplety kodów publicznych, więc porównanie wierszy trzeba by wtedy robić „z
    dokładnością do tłumaczenia” – a to jest dokładnie ta luźność, której ten test ma nie mieć.
    """
    # ``savepoint_create``, a nie ``savepoint``: Django 6.1 oznaczyło tę drugą nazwę jako
    # przestarzałą (znika w 7.0), a jej ciało to jedna linia – wywołanie tej pierwszej. Zmiana jest
    # więc wyłącznie nazwą; mechanika dwóch przebiegów i wszystkie asercje tego pliku zostają
    # co do znaku.
    savepoint = transaction.savepoint_create()
    try:
        yield
    finally:
        transaction.savepoint_rollback(savepoint)


def _set_flags(competition_id: int, **flags) -> None:
    """Przestawia flagi konkursu **w bazie** – tą samą drogą, którą przestawi je organizator.

    Zapis, a nie podmiana ``has_feature``: serwis czyta konkurs przez ``stage.edition.competition``,
    czyli świeżym zapytaniem, więc atrapa na obiekcie z testu i tak nie doszłaby tam, gdzie zapada
    decyzja.
    """
    competition = Competition.objects.get(pk=competition_id)
    competition.feature_flags = {**(competition.feature_flags or {}), **flags}
    competition.save(update_fields=["feature_flags"])


def stage_tables(stages: dict) -> dict:
    """Komplet odczytów o każdym etapie – wszystko, co po zmianie mogłoby wyjść inaczej.

    Etapy liczymy w kolejności zawodów i każdy **naprawdę** kwalifikujemy i publikujemy, zamiast
    czytać sam podgląd: publikacja jest tą czynnością, której wynik ogląda publiczność, a podgląd
    (``preview=True``) świadomie omija bramę „ocenianie zakończone” i nie zapisuje sum.

    ``entry_id`` z wiersza i z kluczy ``entry_totals`` **zamieniamy na kod publiczny** i to nie
    jest rozluźnienie porównania, tylko jedyny sposób, żeby było ono w ogóle prawdziwe:
    kwalifikacja zakłada wpisy w następnym etapie (``_sync_next_stage``), a sekwencja klucza
    głównego w Postgresie **nie cofa się** przy wycofaniu punktu zapisu. Ten sam wpis tego samego
    uczestnika dostaje więc w drugim przebiegu numer o jeden wyższy – co znaczyłoby różnicę
    w każdej tabeli, w której ktokolwiek awansował. Kod publiczny jest przy tym tym samym
    identyfikatorem wiersza co ``entry_id`` (jeden wpis na uczestnika w etapie), tyle że nadanym
    przez nas, a nie przez bazę.
    """
    tables: dict[str, dict] = {}
    for name, stage in stages.items():
        # Etap czytamy **na nowo**, a nie z obiektu fikstury, i to nie jest ostrożność: droga do
        # konkursu wiedzie przez edycję (``competition_of``), a Django trzyma raz pobraną relację
        # w pamięci obiektu. Etap zbudowany fabryką przed przestawieniem flagi niósłby więc
        # konkurs z **poprzednimi** flagami – i cały przebieg „z danych” poszedłby po cichu
        # dzisiejszą gałęzią, a test zgodziłby się sam ze sobą, nie sprawdziwszy niczego.
        stage = Stage.objects.select_related("edition", "edition__competition").get(pk=stage.pk)
        qualification = stage_qualification(stage)
        summary = apply_qualification(stage)
        rows = summary["rows"]
        code_of = {row["entry_id"]: row["public_code"] for row in rows}
        following = next_stage_of(stage)
        publication = publish_results(stage, None, Anonymization.CODE)
        tables[name] = {
            "rows": [{key: value for key, value in row.items() if key != "entry_id"} for row in rows],
            "ranks": [[row["public_code"], row["rank"]] for row in rows],
            "qualified_codes": sorted(row["public_code"] for row in rows if row["qualified"]),
            "snapshots": {
                anonymization: build_snapshot(rows, anonymization) for anonymization in Anonymization.values
            },
            "next_stage_id": None if following is None else following.pk,
            "next_stage_kind": None if following is None else following.kind,
            # Napis o trybie progu jest **z założenia** inny na obu drogach („TOP_N_PER_DISTRICT”
            # kontra „TOP_N_PER_GROUP”): to etykieta do audytu, a nie liczba w tabeli. Stoi osobno,
            # bo porównuje go osobna asercja z odwzorowaniem z migracji.
            "qualification_mode": qualification.mode,
            "summary": {key: value for key, value in summary.items() if key not in ("rows", "mode")},
            "public_rows": [dict(row) for row in PublicResultsSerializer(publication).data["rows"]],
            # ``published_at`` jest znacznikiem **czynności**, a nie treścią tabeli: dwa przebiegi
            # dzieli ułamek sekundy i porównanie ich znaczników nie mówiłoby o niczym.
            "statistics": {
                key: value for key, value in stage_statistics(publication).items() if key != "published_at"
            },
            "entry_totals": {
                code_of[int(entry_id)]: total for entry_id, total in publication.entry_totals.items()
            },
        }
    return tables


def run_pass(competition_id: int, stages: dict, **flags) -> dict:
    """Jeden przebieg całej edycji przy zadanym ustawieniu flag. Nic po sobie nie zostawia."""
    with _rolled_back():
        _set_flags(competition_id, **flags)
        return stage_tables(stages)


# --- porównania ------------------------------------------------------------------------------------


def assert_rows_equal(today: list[dict], from_data: list[dict], where: str) -> None:
    """Wiersze na **równość**: ta sama liczba, ta sama kolejność, ten sam komplet kluczy."""
    assert len(today) == len(from_data), f"{where}: inna liczba wierszy"
    for index, (left, right) in enumerate(zip(today, from_data, strict=True)):
        assert set(left) == set(right), f"{where}, wiersz {index}: inny zestaw kluczy"
        assert left == right, f"{where}, wiersz {index} ({left['public_code']}): inne wartości"


def assert_tables_equal(today: dict, from_data: dict) -> None:
    """Obie drogi, etap po etapie i odczyt po odczycie."""
    assert list(today) == list(from_data)
    for name in today:
        left, right = today[name], from_data[name]
        assert_rows_equal(left["rows"], right["rows"], f"etap {name}")
        assert left["ranks"] == right["ranks"], f"etap {name}: inne miejsca w tabeli"
        assert left["qualified_codes"] == right["qualified_codes"], (
            f"etap {name}: inny skład zakwalifikowanych"
        )
        for anonymization in Anonymization.values:
            assert left["snapshots"][anonymization] == right["snapshots"][anonymization], (
                f"etap {name}: inny snapshot w trybie {anonymization}"
            )
        assert left["next_stage_id"] == right["next_stage_id"], f"etap {name}: inny następny etap"
        assert left["public_rows"] == right["public_rows"], f"etap {name}: inna odpowiedź publiczna"
        assert left["statistics"] == right["statistics"], f"etap {name}: inne statystyki"
        assert left["summary"] == right["summary"], f"etap {name}: inne liczniki przeliczenia"
        assert left["entry_totals"] == right["entry_totals"], f"etap {name}: inne sumy wpisów"


def stable(tables: dict) -> dict:
    """Postać nadająca się do zamrożenia w pliku: bez identyfikatorów i bez znaczników czasu.

    Identyfikatory wpisów i uczestników zależą od kolejności testów w suicie, więc w zamrożonym
    pliku byłyby szumem zmieniającym się bez powodu. Wszystko, co niesie **treść** tabeli – kody
    publiczne, punkty, miejsca, etykiety, liczniki – zostaje.
    """
    frozen = {
        name: {
            "rows": [
                {key: value for key, value in row.items() if key not in VOLATILE_ROW_KEYS}
                for row in table["rows"]
            ],
            "ranks": table["ranks"],
            "qualified": table["qualified_codes"],
            "snapshots": table["snapshots"],
            "next_stage_kind": table["next_stage_kind"],
            "qualification_mode": table["qualification_mode"],
            "public_rows": table["public_rows"],
            "statistics": {
                key: value
                for key, value in table["statistics"].items()
                if key not in ("stage_id", "stage_name", "edition")
            },
            "summary": {
                key: value
                for key, value in table["summary"].items()
                if key not in ("stage_id", "next_stage_id")
            },
        }
        for name, table in tables.items()
    }
    # Przez JSON, a nie wprost: plik i tak jest JSON-em, więc porównanie ma biec na tym, co w nim
    # naprawdę stanie (krotki jako listy), a nie na obiektach Pythona. Punkty są od wydania 0.35.0
    # ``Decimal`` (kolumny dziesiętne) i idą przez ``jsonable_points`` – tę samą zamianę, którą
    # przechodzi snapshot publikacji – a nie przez ``str``: ocena 6 ma w pliku zostać liczbą 6,
    # a nie stać się napisem „6.00”, bo zmieniłby się zapis, a nie tabela.
    return json.loads(json.dumps(jsonable_points(frozen), ensure_ascii=False, default=str))


# --- świat -----------------------------------------------------------------------------------------


@pytest.fixture
def world(competition):
    """Edycja w kształcie Konkursu #1 razem z przebiegiem zapisanym przez migrację."""
    built = results_fixture.build_results_world(competition)
    results_fixture.write_pipeline_rows()
    return built


@pytest.fixture
def world_stages(world) -> dict:
    return {name: world.stage(name) for name in STAGE_ORDER}


# --- 1. ta sama edycja, dwie drogi ------------------------------------------------------------------


def test_stage_results_are_identical_both_ways(world, world_stages):
    """Ta sama edycja, dwie drogi kwalifikacji, wynik na **równość** – nie na podzbiór."""
    today = run_pass(world.competition.pk, world_stages)
    from_data = run_pass(world.competition.pk, world_stages, **{PROCESS_EDITOR_FLAG: True})

    assert_tables_equal(today, from_data)


def test_the_pipeline_orders_the_stages_exactly_like_stage_order(world, world_stages):
    """Kolejność zawodów z danych jest tą samą kolejnością, co krotka ``STAGE_ORDER``."""
    today = run_pass(world.competition.pk, world_stages)
    from_data = run_pass(world.competition.pk, world_stages, **{PROCESS_EDITOR_FLAG: True})

    expected = {"training": None, "elim": "DISTRICT", "district": "FINAL", "final": None}
    assert {name: table["next_stage_kind"] for name, table in today.items()} == expected
    assert {name: table["next_stage_kind"] for name, table in from_data.items()} == expected


def test_the_training_stage_qualifies_nobody_into_the_track(world, world_stages):
    """``off_pipeline=True`` znaczy to, co dziś znaczy nieobecność w ``STAGE_ORDER``.

    Trening nie ma następnika i nie zakłada ani jednego wpisu nigdzie indziej – obiema drogami,
    mimo że ma wpisany próg (``HYBRID``) i mimo że ktoś ten próg spełnia.
    """
    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        training = tables["training"]
        assert training["next_stage_id"] is None
        assert training["summary"]["next_stage_id"] is None
        assert training["summary"]["created_entries"] == 0
        assert training["qualified_codes"], "próg treningu ma kogoś przepuszczać – inaczej nic nie mierzymy"


def test_the_audit_mode_names_the_same_threshold_both_ways(world, world_stages):
    """Napis o trybie jest inny **tylko** tam, gdzie migracja zmienia nazwę, i dokładnie tak.

    ``TOP_N_PER_DISTRICT`` nazywa się na drodze z danych ``TOP_N_PER_GROUP``, bo „N na
    województwo” jest szczególnym przypadkiem „N w grupie”. Odwzorowanie bierzemy z migracji,
    a nie z literału – z tego samego słownika, którym produkcja przepisze progi.
    """
    mode_map = results_fixture.pipeline_from_stages.MODE_MAP
    today = run_pass(world.competition.pk, world_stages)
    from_data = run_pass(world.competition.pk, world_stages, **{PROCESS_EDITOR_FLAG: True})

    for name in world_stages:
        before = today[name]["qualification_mode"]
        after = from_data[name]["qualification_mode"]
        if name == "training":
            # Krok poza torem nie dostaje reguły przejścia, więc obie drogi czytają ten sam próg.
            assert after == before
            continue
        assert after == mode_map[before][0]


# --- 2. zamrożona tabela ----------------------------------------------------------------------------


def test_the_published_tables_match_the_frozen_snapshot(world, world_stages):
    """Tabela drogi dzisiejszej jest **równa** tej zamrożonej w ``results_snapshot.json``.

    Test przewracający się tutaj mówi jedno: ogłaszana tabela zmieniła kształt. To bywa poprawne
    (nowa kolumna uzgodniona z organizatorem) i wtedy plik regeneruje się zmienną środowiskową –
    ale zawsze jest **decyzją**, a nie skutkiem ubocznym.
    """
    current = stable(run_pass(world.competition.pk, world_stages))

    if os.environ.get(REGENERATE_ENV):
        SNAPSHOT_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert list(current) == list(expected)
    for name in current:
        assert current[name] == expected[name], f"etap {name}: tabela różni się od zamrożonej"


def test_the_snapshot_keys_are_unchanged_without_categories(world, world_stages):
    """Snapshot Konkursu #1 nie zyskuje **ani jednego** klucza – obiema drogami.

    Lista pól jest tu wypisana wprost, a nie wzięta z ``build_snapshot``: kopia tej samej funkcji
    zgodziłaby się z nią zawsze, także wtedy, gdy ktoś dołoży do wiersza kolumnę z danymi osobowymi.
    """
    expected = {"CODE": {"rank", "display", "points", "total", "qualified", "manual", "district"}}
    expected["INITIALS_SCHOOL"] = expected["CODE"] - {"district"}
    expected["FULL"] = expected["INITIALS_SCHOOL"]

    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        for name, table in tables.items():
            for anonymization, keys in expected.items():
                for row in table["snapshots"][anonymization]:
                    assert set(row) == keys, f"etap {name}, tryb {anonymization}: inny zestaw pól"


# --- 3. trzy zachowania brzegowe na ogłoszonej tabeli ------------------------------------------------


def test_zero_never_qualifies_in_top_n_modes(world, world_stages):
    """Komplet zer nie kwalifikuje – nawet gdy jest jedynym wpisem w województwie.

    ``0008`` nie oddał ani jednej pracy i jest **jedyny** w swoim województwie, a próg to „dwóch
    najlepszych z województwa”. „N najlepszych” nie może znaczyć awansu za brak rozwiązania.
    """
    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        codes = tables["elim"]["qualified_codes"]
        assert results_fixture.public_code(world.competition, "0008") not in codes
        assert results_fixture.public_code(world.competition, "0007") not in codes


def test_ties_at_the_cutoff_all_qualify(world, world_stages):
    """Progiem jest **wartość** zajmująca miejsce N, a nie samo miejsce.

    W okręgu mazowieckim przechodzi trzech przy ``top_n=2``, bo dwóch remisuje na progu.
    """
    code = results_fixture.public_code
    expected = {code(world.competition, suffix) for suffix in ("0001", "0002", "0003")}

    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        rows = tables["elim"]["rows"]
        totals = {row["public_code"]: row["total"] for row in rows}
        assert totals[code(world.competition, "0002")] == totals[code(world.competition, "0003")]
        assert expected <= set(tables["elim"]["qualified_codes"])


def test_manual_qualification_beats_every_rule(world, world_stages):
    """Decyzja komitetu bije próg w obie strony – i nie podnosi zdyskwalifikowanego.

    ``0004`` ma cztery punkty i przechodzi, ``0006`` ma piętnaście i nie przechodzi, a ``0009``
    ma osiemnaście i jest poza tabelą progu, bo dyskwalifikacja jest decyzją proceduralną.
    """
    code = results_fixture.public_code

    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        qualified = set(tables["elim"]["qualified_codes"])
        assert code(world.competition, "0004") in qualified
        assert code(world.competition, "0006") not in qualified
        assert code(world.competition, "0009") not in qualified


def test_the_final_threshold_is_at_least_not_more_than(world, world_stages):
    """``min_points`` znaczy „co najmniej”: suma równa progowi kwalifikuje."""
    code = results_fixture.public_code

    for flags in ({}, {PROCESS_EDITOR_FLAG: True}):
        tables = run_pass(world.competition.pk, world_stages, **flags)
        rows = {row["public_code"]: row for row in tables["final"]["rows"]}
        row = rows[code(world.competition, "0002")]
        assert row["total"] == results_fixture.FINAL_MIN_POINTS
        assert row["qualified"] is True


# --- 4. kategorie włączone bez ani jednej kategorii ---------------------------------------------------


def test_categories_without_any_category_change_no_place_and_no_total(world, world_stages):
    """Flaga ``categories`` bez ani jednej kategorii nie zmienia żadnej **liczby** w tabeli.

    Miejsca, sumy, skład zakwalifikowanych, liczniki i statystyki wychodzą identycznie jak przy
    fladze wyłączonej: wszystkie wiersze wpadają wtedy do jednej grupy „bez kategorii”, a
    ``_rank_rows`` liczy miejsca dokładnie tak, jak przed etapem 2.
    """
    today = run_pass(world.competition.pk, world_stages)
    with_categories = run_pass(
        world.competition.pk, world_stages, **{PROCESS_EDITOR_FLAG: True, CATEGORIES_FLAG: True}
    )

    for name in today:
        left, right = today[name], with_categories[name]
        assert left["ranks"] == right["ranks"], f"etap {name}: inne miejsca w tabeli"
        assert left["qualified_codes"] == right["qualified_codes"], f"etap {name}: inny skład"
        assert left["summary"] == right["summary"], f"etap {name}: inne liczniki"
        assert left["statistics"] == right["statistics"], f"etap {name}: inne statystyki"
        assert left["entry_totals"] == right["entry_totals"], f"etap {name}: inne sumy wpisów"


def test_categories_without_any_category_add_only_an_empty_label(world, world_stages):
    """Jedyna różnica po włączeniu kategorii bez kategorii: **pusta** etykieta w wierszu.

    To nie jest regresja Konkursu #1 – on tej flagi nie włącza (§ 0.6) – ale jest różnicą
    widoczną w odpowiedzi publicznej, więc ma tu własną, imienną asercję. Gdyby kiedyś zniknęła
    (bo ``build_snapshot`` przestanie dokładać klucz konkursowi bez ani jednej kategorii), ten
    test upomni się o świadomą decyzję zamiast po cichu przepuścić zmianę kształtu odpowiedzi.
    """
    today = run_pass(world.competition.pk, world_stages)
    with_categories = run_pass(
        world.competition.pk, world_stages, **{PROCESS_EDITOR_FLAG: True, CATEGORIES_FLAG: True}
    )

    for name in today:
        for anonymization in Anonymization.values:
            before = today[name]["snapshots"][anonymization]
            after = with_categories[name]["snapshots"][anonymization]
            assert len(before) == len(after)
            for left, right in zip(before, after, strict=True):
                assert set(right) - set(left) == ({"category"} if left else set())
                assert right.get("category", "") == ""
                assert {key: right[key] for key in left} == left


# --- 5. fikstura brzegowa ------------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(results_fixture.EDGE_CASES))
def test_edge_cases_are_identical_both_ways(competition, case):
    """Dziewięć rozstrzygnięć brzegowych, każde na własnym jednoetapowym świecie.

    Cztery tryby progu, remis na progu, komplet zer, wpis zdyskwalifikowany, decyzja komitetu
    w obie strony, etap w formie testu online i etap bez ani jednego zadania. Każdy z nich jest
    **decyzją** zapisaną w ``_top_n_cutoff``, ``qualified_with_manual`` albo w gałęzi formy etapu,
    więc każdy ma prawo rozjechać się osobno.
    """
    stage = results_fixture.build_edge_stage(competition, case)
    results_fixture.write_pipeline_rows()
    stages = {case: stage}

    today = run_pass(competition.pk, stages)
    from_data = run_pass(competition.pk, stages, **{PROCESS_EDITOR_FLAG: True})

    assert_tables_equal(today, from_data)


# --- 6. złota fikstura ----------------------------------------------------------------------------------


def test_the_golden_fixture_gives_the_same_tables_both_ways(competition):
    """Ten sam dowód na świecie z ``golden.py`` – czyli na kształcie, który opisuje produkcję.

    Złota fikstura ma trzy zadania, trzech uczestników w trzech stanach wpisu i trzech stanach
    zgłoszenia, komplet stron CMS i publikację. Po domknięciu oceniania i okien reklamacji
    (``ready_for_results``) daje się z niej policzyć ogłoszoną tabelę – i to jest pierwszy
    z trzech zestawów danych § 5.2.
    """
    golden = golden_module.build_golden(competition)
    stages = {"elim": golden.elim, "district": golden.district, "final": golden.final}
    golden_module.ready_for_results(golden, *stages.values())
    results_fixture.write_pipeline_rows()

    today = run_pass(competition.pk, stages)
    from_data = run_pass(competition.pk, stages, **{PROCESS_EDITOR_FLAG: True})

    assert_tables_equal(today, from_data)
    assert today["elim"]["next_stage_kind"] == "DISTRICT"
    assert today["elim"]["qualified_codes"], "tabela eliminacji ma kogoś kwalifikować"
