"""Kwalifikacja dwudrożna: ``STAGE_ORDER`` + ``QualificationRule`` albo przebieg z danych (T30).

Przedmiotem testu jest **równość dwóch dróg**, a nie poprawność jednej z nich. Pytanie brzmi:
czy przebieg Olimpiady Kwantowej wyrażony jako wiersze (``PipelineStep``, ``TransitionRule``,
``docs/UNIWERSALNY-ETAP-2.md`` § 1.2) daje dokładnie ten sam skład zakwalifikowanych, co przebieg
wyrażony w kodzie. Dlatego prawie każdy test tego pliku liczy jedno i to samo **dwa razy** –
z flagą ``process_editor`` wyłączoną i włączoną – i porównuje zbiory na równość.

Odwzorowanie czterech dzisiejszych trybów nie jest tu przepisane literałem: bierzemy ``MODE_MAP``
wprost z migracji ``competitions.0024_pipeline_from_stages``, czyli z tego samego słownika, którym
produkcja przepisze progi. Gdyby migracja i serwis rozumiały „N na województwo” inaczej, ten test
ma paść, a nie zgodzić się same ze sobą dwie kopie tej samej pomyłki.

Trzy zachowania brzegowe mają tu własne, imiennie nazwane asercje, bo każde jest **decyzją**,
a nie skutkiem ubocznym: zero nie kwalifikuje w trybach z ``top_n``, remis na progu wpuszcza
wszystkich, a decyzja komitetu bije regułę – także sumę reguł. Pełny dowód na ogłoszonych tabelach
Konkursu #1 jest osobno (§ 5.2, T28); tutaj jest warstwa jednostkowa tej samej reguły.
"""

from __future__ import annotations

import importlib

import pytest

from apps.competitions.models import (
    Category,
    ManualQualification,
    PipelineStep,
    QualificationMode,
    QualificationRule,
    StageEntry,
    StageEntryStatus,
    StageKind,
    TransitionGroupBy,
    TransitionMode,
    TransitionRule,
)
from apps.core.api import DomainError
from apps.results.services import (
    PROCESS_EDITOR_FLAG,
    apply_qualification,
    compute_stage_results,
    next_stage_of,
    process_editor_enabled,
    stage_qualification,
    transition_rules_for,
)
from apps.results.simulation import build_transition_rule, simulate, simulate_transition

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db

#: Odwzorowanie trybów bierzemy z migracji, a nie z własnego literału – patrz docstring modułu.
MODE_MAP = importlib.import_module("apps.competitions.migrations.0024_pipeline_from_stages").MODE_MAP


# --- narzędzia -----------------------------------------------------------------------------------


def enable(competition):
    """Przestawia konkurs na przebieg z danych. Zapis w bazie, a nie podmiana ``has_feature``."""
    competition.feature_flags = {**(competition.feature_flags or {}), PROCESS_EDITOR_FLAG: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def disable(competition):
    """Powrót do stanu Konkursu #1 – ta sama droga, którą flagę wyłączy wycofanie wydania."""
    flags = {**(competition.feature_flags or {})}
    flags.pop(PROCESS_EDITOR_FLAG, None)
    competition.feature_flags = flags
    competition.save(update_fields=["feature_flags"])
    return competition


def make_step(stage, position: int = 1, *, off_pipeline: bool = False) -> PipelineStep:
    """Krok przebiegu dla etapu. Bez fabryki – ``tests/factories.py`` jest plikiem wspólnym."""
    return PipelineStep.objects.create(
        edition_id=stage.edition_id, stage=stage, position=position, off_pipeline=off_pipeline
    )


def add_rule(step, mode, *, position: int = 0, **kwargs) -> TransitionRule:
    return TransitionRule.objects.create(step=step, mode=mode, position=position, **kwargs)


def mirror_rule(stage) -> TransitionRule:
    """Reguła przejścia odwzorowująca dzisiejszy próg etapu – dokładnie tak, jak robi to migracja."""
    rule = QualificationRule.objects.get(stage=stage)
    mode, group_by = MODE_MAP[rule.mode]
    return add_rule(
        make_step(stage),
        mode,
        group_by=group_by,
        min_points=rule.min_points,
        top_n=rule.top_n,
    )


def qualified_ids(stage) -> set[int]:
    """Kto mieści się w progu – tą drogą, którą akurat wskazuje flaga konkursu.

    Liczymy w trybie podglądu, bo przedmiotem testu jest **próg**, a nie zapis statusów: podgląd
    idzie przez tę samą ``stage_qualification`` i te same wiersze, co ``apply_qualification``.
    """
    rows = compute_stage_results(stage, preview=True)
    candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    return stage_qualification(stage).qualified_entry_ids(candidates)


def both_ways(stage, competition) -> tuple[set[int], set[int]]:
    """Ten sam etap policzony dwiema drogami: ``(dzisiejsza, z danych)``."""
    disable(competition)
    today = qualified_ids(stage)
    enable(competition)
    from_data = qualified_ids(stage)
    return today, from_data


# --- flaga ---------------------------------------------------------------------------------------


def test_competition_one_reads_the_pipeline_flag_as_off(competition):
    """Wymaganie nadrzędne: Konkurs #1 nie wchodzi w gałąź danych ani razu (§ 0.1)."""
    assert competition.has_feature(PROCESS_EDITOR_FLAG) is False
    assert process_editor_enabled(competition) is False


def test_an_unknown_competition_is_not_a_pipeline_competition():
    """„Nie wiadomo, czyj to etap” ma dać zachowanie sprzed etapu 2, a nie wywrócić przeliczenie."""
    assert process_editor_enabled(None) is False


# --- kolejność etapów ----------------------------------------------------------------------------


def test_next_stage_ignores_the_pipeline_while_the_flag_is_off(competition):
    """Wiersze przebiegu mogą mówić co chcą – wyłączona flaga znaczy ``STAGE_ORDER`` i tylko ją."""
    elim = make_stage(kind=StageKind.ELIM, problems=1)
    district = make_stage(kind=StageKind.DISTRICT, edition=elim.edition, problems=1)
    # Kolejność w danych jest **odwrotna** niż w krotce: gdyby serwis ją czytał, odpowiedź byłaby
    # inna. To jest cały sens tej asercji.
    make_step(district, 1)
    make_step(elim, 2)

    assert next_stage_of(elim) == district
    assert next_stage_of(district) is None


def test_next_stage_follows_the_pipeline_when_the_flag_is_on(competition):
    """Przy włączonej fladze o kolejności rozstrzyga ``position``, a nie rodzaj etapu."""
    first = make_stage(kind=StageKind.ELIM, problems=1)
    second = make_stage(kind=StageKind.DISTRICT, edition=first.edition, problems=1)
    third = make_stage(kind=StageKind.FINAL, edition=first.edition, problems=1)
    make_step(first, 1)
    make_step(second, 2)
    make_step(third, 3)
    enable(competition)

    assert next_stage_of(first) == second
    assert next_stage_of(second) == third
    assert next_stage_of(third) is None


def test_both_ways_give_the_same_order_for_competition_one(competition):
    """Ta sama edycja, dwie drogi, ta sama odpowiedź – tego pilnuje § 5.2 na całych tabelach."""
    elim = make_stage(kind=StageKind.ELIM, problems=1)
    district = make_stage(kind=StageKind.DISTRICT, edition=elim.edition, problems=1)
    final = make_stage(kind=StageKind.FINAL, edition=elim.edition, problems=1)
    training = make_stage(kind=StageKind.TRAINING, edition=elim.edition, problems=1)
    for stage, position in ((elim, 1), (district, 2), (final, 3)):
        make_step(stage, position)
    make_step(training, 0, off_pipeline=True)

    today = {stage.pk: next_stage_of(stage) for stage in (elim, district, final, training)}
    enable(competition)
    from_data = {stage.pk: next_stage_of(stage) for stage in (elim, district, final, training)}

    assert today == from_data


def test_a_step_outside_the_track_has_no_next_stage(competition):
    """``off_pipeline=True`` znaczy dokładnie to, co dziś znaczy nieobecność w ``STAGE_ORDER``."""
    training = make_stage(kind=StageKind.TRAINING, problems=1)
    final = make_stage(kind=StageKind.FINAL, edition=training.edition, problems=1)
    make_step(training, 0, off_pipeline=True)
    make_step(final, 1)
    enable(competition)

    assert next_stage_of(training) is None
    # I w drugą stronę: krok poza torem nie jest niczyim następnikiem.
    assert next_stage_of(final) is None


def test_a_stage_without_a_step_has_no_next_stage(competition):
    """Etap spoza przebiegu nie ma miejsca, z którego można pójść dalej – i to jest odpowiedź.

    Jedyne miejsce, w którym **nie ma** odwrotu na ``STAGE_ORDER``: konkurs o pięciu rundach ma
    wszystkie etapy rodzaju ``ROUND``, a krotka nie umie ich ustawić w kolejności.
    """
    elim = make_stage(kind=StageKind.ELIM, problems=1)
    make_stage(kind=StageKind.DISTRICT, edition=elim.edition, problems=1)
    enable(competition)

    assert next_stage_of(elim) is None


def test_rounds_are_ordered_by_position_not_by_kind(competition):
    """Po to jest cały ``PipelineStep``: pięć rund rozróżnia miejsce w kolejce, a nie rodzaj."""
    first = make_stage(kind=StageKind.ROUND, problems=1)
    second = make_stage(kind=StageKind.ROUND, edition=first.edition, problems=1)
    first.name, second.name = "Runda 1", "Runda 2"
    make_step(first, 1)
    make_step(second, 2)
    enable(competition)

    assert next_stage_of(first) == second
    assert next_stage_of(second) is None


# --- odwzorowanie czterech dzisiejszych trybów ---------------------------------------------------


def test_the_migration_maps_every_mode_in_use_today():
    """Cztery tryby, cztery odwzorowania – brak choćby jednego znaczyłby próg bez czytelnika."""
    assert set(MODE_MAP) == set(QualificationMode.values)
    assert MODE_MAP[QualificationMode.TOP_N_PER_DISTRICT] == (
        TransitionMode.TOP_N_PER_GROUP,
        TransitionGroupBy.REGION,
    )


def test_min_points_is_the_same_set_both_ways(competition):
    stage = make_stage(mode=QualificationMode.MIN_POINTS, min_points=7, problems=2)
    graded_entry(stage, [6, 5])  # 11 – wchodzi
    graded_entry(stage, [5, 2])  # 7 – wchodzi, próg jest „co najmniej”
    graded_entry(stage, [2, 2])  # 4 – nie wchodzi
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 2
    assert today == from_data


def test_top_n_is_the_same_set_both_ways(competition):
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2, problems=1)
    graded_entry(stage, [6])
    graded_entry(stage, [5])
    graded_entry(stage, [2])
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 2
    assert today == from_data


def test_top_n_per_district_is_the_same_set_both_ways(competition):
    """„N na województwo” to szczególny przypadek „N w grupie”, a nie osobna arytmetyka."""
    stage = make_stage(mode=QualificationMode.TOP_N_PER_DISTRICT, min_points=None, top_n=1, problems=1)
    graded_entry(stage, [6], district="pomorskie")
    graded_entry(stage, [2], district="pomorskie")
    graded_entry(stage, [5], district="malopolskie")
    graded_entry(stage, [0], district="malopolskie")
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 2
    assert today == from_data


def test_hybrid_is_the_same_set_both_ways(competition):
    stage = make_stage(mode=QualificationMode.HYBRID, min_points=8, top_n=2, problems=2)
    graded_entry(stage, [6, 5])  # 11 – mieści się w obu warunkach
    graded_entry(stage, [6, 2])  # 8 – mieści się w obu warunkach
    graded_entry(stage, [5, 2])  # 7 – w top 2 by się zmieścił, ale nie ma minimum
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 2
    assert today == from_data


# --- zachowania brzegowe, każde jako osobna decyzja ----------------------------------------------


def test_zero_never_qualifies_in_top_n_modes(competition):
    """„N najlepszych” nie może oznaczać awansu za brak rozwiązania – także drogą z danych."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=5, problems=1)
    graded_entry(stage, [0])
    graded_entry(stage, [0])
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert today == set()
    assert from_data == set()


def test_ties_at_the_cutoff_all_qualify(competition):
    """Progiem jest **wartość** zajmująca miejsce N, a nie samo miejsce – więc remis wchodzi cały."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2, problems=1)
    graded_entry(stage, [6])
    graded_entry(stage, [5])
    graded_entry(stage, [5])
    graded_entry(stage, [2])
    mirror_rule(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 3
    assert today == from_data


def test_manual_qualification_beats_the_sum_of_rules(competition):
    """Decyzja komitetu bije regułę – także sumę reguł, bo dokładamy ją po zsumowaniu zbiorów."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    winner = graded_entry(stage, [6])
    promoted = graded_entry(stage, [0])
    step = make_step(stage)
    add_rule(step, TransitionMode.TOP_N, top_n=1)
    add_rule(step, TransitionMode.MIN_POINTS, min_points=6, position=1)
    StageEntry.objects.filter(pk=promoted.pk).update(
        manual_qualification=ManualQualification.QUALIFIED,
        manual_qualification_reason="decyzja komitetu",
    )
    enable(competition)

    summary = apply_qualification(stage)

    decided = {row["entry_id"] for row in summary["rows"] if row["qualified"]}
    assert decided == {winner.pk, promoted.pk}


def test_a_rejected_decision_beats_the_sum_of_rules_too(competition):
    """W drugą stronę tak samo: komitet potrafi kogoś wyjąć z progu, który ten ktoś spełnia."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2, problems=1)
    kept = graded_entry(stage, [6])
    dropped = graded_entry(stage, [5])
    add_rule(make_step(stage), TransitionMode.TOP_N, top_n=2)
    StageEntry.objects.filter(pk=dropped.pk).update(
        manual_qualification=ManualQualification.NOT_QUALIFIED,
        manual_qualification_reason="decyzja komitetu",
    )
    enable(competition)

    summary = apply_qualification(stage)

    decided = {row["entry_id"] for row in summary["rows"] if row["qualified"]}
    assert decided == {kept.pk}


# --- suma reguł kroku ----------------------------------------------------------------------------


def test_several_rules_on_one_step_are_a_union(competition):
    """„30 najlepszych **oraz** każdy z co najmniej 90 punktami” – suma zbiorów, nie iloczyn."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=2)
    best = graded_entry(stage, [6, 6])  # 12 – z reguły „najlepszy 1”
    by_points = graded_entry(stage, [6, 5])  # 11 – z reguły „co najmniej 11”
    outside = graded_entry(stage, [2, 2])  # 4 – z żadnej
    step = make_step(stage)
    add_rule(step, TransitionMode.TOP_N, top_n=1)
    add_rule(step, TransitionMode.MIN_POINTS, min_points=11, position=1)
    enable(competition)

    assert qualified_ids(stage) == {best.pk, by_points.pk}
    assert outside.pk not in qualified_ids(stage)


def test_hybrid_stays_an_intersection_inside_one_rule(competition):
    """Iloczyn ma własny tryb – i to jest cała różnica między ``HYBRID`` a dwiema regułami."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=2)
    graded_entry(stage, [6, 6])  # 12
    weaker = graded_entry(stage, [2, 2])  # 4 – w top 2, ale bez minimum
    add_rule(make_step(stage), TransitionMode.HYBRID, top_n=2, min_points=10)
    enable(competition)

    assert weaker.pk not in qualified_ids(stage)
    assert len(qualified_ids(stage)) == 1


# --- tryby, których Konkurs #1 nie używa ---------------------------------------------------------


def test_percentile_rounds_up_and_still_rejects_zeros(competition):
    """„Najlepsze 10 %” z siedmiu osób ma znaczyć jedną osobę, a nie zero – ale nie zerową."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    best = graded_entry(stage, [6])
    for score in (5, 5, 2, 2, 2):
        graded_entry(stage, [score])
    graded_entry(stage, [0])
    add_rule(make_step(stage), TransitionMode.PERCENTILE, percentile=10)
    enable(competition)

    assert qualified_ids(stage) == {best.pk}


def test_percentile_of_a_field_without_points_qualifies_nobody(competition):
    """Komplet zer to brak kandydatów – w każdym trybie liczącym miejsca, także w procentach."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    graded_entry(stage, [0])
    graded_entry(stage, [0])
    add_rule(make_step(stage), TransitionMode.PERCENTILE, percentile=100)
    enable(competition)

    assert qualified_ids(stage) == set()


def test_manual_mode_qualifies_nobody_by_itself(competition):
    """``MANUAL`` znaczy „przechodzi wyłącznie ten, komu komitet wpisał decyzję”."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    graded_entry(stage, [6])
    promoted = graded_entry(stage, [2])
    add_rule(make_step(stage), TransitionMode.MANUAL)
    StageEntry.objects.filter(pk=promoted.pk).update(
        manual_qualification=ManualQualification.QUALIFIED,
        manual_qualification_reason="decyzja komitetu",
    )
    enable(competition)

    assert qualified_ids(stage) == set()
    summary = apply_qualification(stage)
    assert {row["entry_id"] for row in summary["rows"] if row["qualified"]} == {promoted.pk}


# --- kategorie -----------------------------------------------------------------------------------


def make_category(competition, code: str, **kwargs) -> Category:
    kwargs.setdefault("name", code.title())
    return Category.objects.create(competition=competition, code=code, **kwargs)


def test_a_rule_narrowed_to_a_category_touches_nobody_else(competition):
    """Zawężenie kategorią jest zawężeniem **pola reguły**, a nie filtrem tabeli wyników."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    junior_category = make_category(competition, "podstawowa")
    senior_category = make_category(competition, "ponadpodstawowa", position=1)
    junior = graded_entry(stage, [2])
    senior = graded_entry(stage, [6])
    StageEntry.objects.filter(pk=junior.pk).update(category=junior_category)
    StageEntry.objects.filter(pk=senior.pk).update(category=senior_category)
    add_rule(make_step(stage), TransitionMode.TOP_N, top_n=1, category=junior_category)
    enable(competition)

    assert qualified_ids(stage) == {junior.pk}


def test_top_n_per_category_gives_each_category_its_own_cutoff(competition):
    """Osobny ranking w każdej kategorii – ten sam mechanizm, co „N na województwo”."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    junior_category = make_category(competition, "podstawowa")
    senior_category = make_category(competition, "ponadpodstawowa", position=1)
    junior_best = graded_entry(stage, [2])
    junior_worst = graded_entry(stage, [1])
    senior_best = graded_entry(stage, [6])
    senior_worst = graded_entry(stage, [5])
    for entry, category in (
        (junior_best, junior_category),
        (junior_worst, junior_category),
        (senior_best, senior_category),
        (senior_worst, senior_category),
    ):
        StageEntry.objects.filter(pk=entry.pk).update(category=category)
    add_rule(make_step(stage), TransitionMode.TOP_N_PER_GROUP, top_n=1, group_by=TransitionGroupBy.CATEGORY)
    enable(competition)

    assert qualified_ids(stage) == {junior_best.pk, senior_best.pk}


# --- odwrót na dzisiejszą regułę i bramki --------------------------------------------------------


def test_a_step_without_rules_keeps_todays_threshold(competition):
    """Flaga mówi „edytor jest dostępny”, a nie „każdy etap został już przepisany” (§ 0.1).

    Bez tego odwrotu włączenie flagi w trakcie sezonu zabierałoby próg etapom, których nikt jeszcze
    nie tknął – czyli zmieniałoby wynik, którego zmieniać nie wolno.
    """
    stage = make_stage(mode=QualificationMode.MIN_POINTS, min_points=7, problems=2)
    graded_entry(stage, [6, 5])
    graded_entry(stage, [2, 2])
    make_step(stage)

    today, from_data = both_ways(stage, competition)

    assert len(today) == 1
    assert today == from_data
    assert stage_qualification(stage).from_pipeline is False


def test_a_stage_without_a_step_keeps_todays_threshold(competition):
    """Tak samo dla etapu, dla którego kroku w ogóle nie ma – próg czyta się tam, gdzie zawsze."""
    stage = make_stage(mode=QualificationMode.MIN_POINTS, min_points=7, problems=2)
    graded_entry(stage, [6, 5])
    enable(competition)

    assert transition_rules_for(stage) == []
    assert stage_qualification(stage).mode == QualificationMode.MIN_POINTS


def test_a_stage_without_any_threshold_still_refuses(competition):
    """Brak progu **obu** dróg zostaje tym, czym był: konfliktem 409, a nie cichym „nikt”."""
    stage = make_stage(problems=1)
    QualificationRule.objects.filter(stage=stage).delete()
    make_step(stage)
    enable(competition)

    with pytest.raises(DomainError) as error:
        apply_qualification(stage)

    assert error.value.machine_code == "QUALIFICATION_RULE_MISSING"


def test_a_rule_without_its_parameter_is_refused(competition):
    """Próg bez liczby nie jest progiem łagodnym, tylko progiem, którego nie da się policzyć."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    # Zapis z pominięciem ``full_clean`` – dokładnie tą drogą, którą wiersz wpisze ``/admin/``.
    add_rule(make_step(stage), TransitionMode.TOP_N)
    enable(competition)

    with pytest.raises(DomainError) as error:
        apply_qualification(stage)

    assert error.value.machine_code == "QUALIFICATION_RULE_INVALID"


def test_the_audit_mode_names_every_rule_of_the_step(competition):
    """Napis w audycie ma być powtarzalny i ma powiedzieć, ile decyzji złożyło się na próg."""
    stage = make_stage(problems=1)
    graded_entry(stage, [6])
    step = make_step(stage)
    add_rule(step, TransitionMode.TOP_N, top_n=1)
    add_rule(step, TransitionMode.MIN_POINTS, min_points=3, position=1)
    enable(competition)

    assert stage_qualification(stage).mode == "TOP_N+MIN_POINTS"


# --- symulacja -----------------------------------------------------------------------------------


def test_the_simulation_shows_the_same_set_as_the_recalculation(competition):
    """Ekran, na którym dobiera się próg, nie ma prawa pokazać innego składu niż ogłoszenie."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2, problems=2)
    graded_entry(stage, [6, 6])
    graded_entry(stage, [5, 5])
    graded_entry(stage, [2, 0])
    step = make_step(stage)
    add_rule(step, TransitionMode.TOP_N, top_n=2)
    enable(competition)

    preview = simulate_transition(stage)
    summary = apply_qualification(stage)

    assert {row["entry_id"] for row in preview["rows"] if row["qualified"]} == {
        row["entry_id"] for row in summary["rows"] if row["qualified"]
    }
    assert preview["mode"] == TransitionMode.TOP_N
    assert preview["rules"] == tuple(transition_rules_for(stage))


def test_the_simulation_previews_unsaved_rules(competition):
    """„Co by było, gdyby przebieg wyglądał tak” – bez zapisywania czegokolwiek."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=2, problems=1)
    best = graded_entry(stage, [6])
    graded_entry(stage, [5])
    make_step(stage)
    enable(competition)

    preview = simulate_transition(stage, [build_transition_rule(stage, TransitionMode.TOP_N, top_n=1)])

    assert preview["qualified"] == 1
    assert {row["entry_id"] for row in preview["rows"] if row["qualified"]} == {best.pk}
    assert not TransitionRule.objects.filter(step__stage=stage).exists()


def test_an_unsaved_rule_is_validated_like_a_saved_one(competition):
    """Podgląd odrzuca dokładnie te parametry, których nie przyjąłby zapis – walidacja jest w modelu."""
    stage = make_stage(problems=1)

    with pytest.raises(DomainError) as error:
        build_transition_rule(stage, TransitionMode.TOP_N_PER_GROUP, top_n=3)

    assert error.value.machine_code == "QUALIFICATION_RULE_INVALID"


def test_the_old_simulation_does_not_read_the_flag(competition):
    """``simulate`` odpowiada na pytanie z formularza, a nie na pytanie o zapisany przebieg."""
    stage = make_stage(mode=QualificationMode.TOP_N, min_points=None, top_n=1, problems=1)
    best = graded_entry(stage, [6])
    graded_entry(stage, [5])
    add_rule(make_step(stage), TransitionMode.MIN_POINTS, min_points=0)
    enable(competition)

    preview = simulate(stage, QualificationMode.TOP_N, None, 1)

    assert {row["entry_id"] for row in preview["rows"] if row["qualified"]} == {best.pk}
    assert preview["rule"].mode == QualificationMode.TOP_N


# --- przejście do następnego etapu ---------------------------------------------------------------


def test_qualified_entries_land_in_the_stage_the_pipeline_points_to(competition):
    """Cała kwalifikacja od końca: próg z danych i wpisy w etapie wskazanym przez ``position``."""
    elim = make_stage(mode=QualificationMode.MIN_POINTS, min_points=5, problems=1)
    final = make_stage(kind=StageKind.FINAL, edition=elim.edition, problems=1)
    passing = graded_entry(elim, [6])
    failing = graded_entry(elim, [2])
    add_rule(make_step(elim, 1), TransitionMode.MIN_POINTS, min_points=5)
    make_step(final, 2)
    enable(competition)

    summary = apply_qualification(elim)

    assert summary["next_stage_id"] == final.pk
    assert StageEntry.objects.filter(stage=final, participant=passing.participant).exists()
    assert not StageEntry.objects.filter(stage=final, participant=failing.participant).exists()
