"""Dzisiejszy przebieg Olimpiady Kwantowej zapisany jako wiersze (§ 1.2.8).

Migracja **czyta bazę i zapisuje to, co w niej jest** – nie literały. Dla każdej edycji powstaje
tyle kroków, ile ma etapów, a dla każdego progu kwalifikacji jedna reguła przejścia o tym samym
znaczeniu. Żadne pole ``Stage``, ``ScoringScale``, ``QualificationRule``, ``Problem`` ani
``StageEntry`` nie jest ruszane, nie powstają komponenty, kategorie, rozstrzygnięcia remisów ani
role recenzenckie, a flaga ``process_editor`` zostaje wyłączona. Po tej migracji tabela wyników
liczy się dokładnie tą samą drogą, co przed nią – nowe wiersze są zapisem, którego jeszcze nikt
nie czyta (czytelnika dokłada T30, za flagą).

**Kolejność bierzemy z rodzaju etapu, a nie z ``opens_at``.** Terminy bywają poprawiane w panelu
i etap II potrafi mieć chwilowo datę wcześniejszą niż etap I; kolejność zawodów nie jest o datach,
tylko o tym, co jest po czym w regulaminie. Odwzorowanie ``ELIM → 1, DISTRICT → 2, FINAL → 3`` jest
tą samą kolejnością, co krotka ``apps.results.services.STAGE_ORDER`` – przepisaną tutaj literałem,
bo migracja opisuje stan z chwili wdrożenia i nie wolno jej zmienić zachowania tylko dlatego, że
ktoś kiedyś zmieni stałą w serwisie. Że obie kolejności są dziś równe, pilnuje osobny test
(``test_pipeline_matches_stage_order``).

Etap treningowy dostaje ``off_pipeline=True`` – dokładnie to, co dziś znaczy jego nieobecność
w ``STAGE_ORDER``: nie kwalifikuje, nie jest niczyim następnikiem i nie dostaje reguły przejścia
nawet wtedy, gdy ktoś wpisał mu próg w panelu.

Idempotencja: wszystko idzie przez ``update_or_create`` po kluczu naturalnym (``stage`` dla kroku,
``step`` + ``position`` dla reguły), więc powtórzony przebieg niczego nie duplikuje ani nie
przestawia. Odwrotność jest ``noop`` i to jest decyzja, a nie przeoczenie – uzasadnienie przy
``backwards``.
"""

from django.db import migrations

#: Miejsce w kolejce dla rodzajów, które ją mają. Odpowiednik ``STAGE_ORDER`` na dzień wdrożenia.
POSITION_BY_KIND = {"ELIM": 1, "DISTRICT": 2, "FINAL": 3}
#: Rodzaje poza torem zawodów – dziś jeden: piaskownica treningowa.
OFF_PIPELINE_KINDS = {"TRAINING"}
#: Miejsce dla rodzaju spoza kolejki (dziś: żadnego). Wartość jest umyślnie wysoka i poza zakresem
#: dzisiejszych etapów, żeby krok bez rozpoznanego rodzaju stanął na końcu, a nie w środku toru.
UNKNOWN_POSITION = 99
#: Cztery dzisiejsze tryby progu na parę (tryb reguły przejścia, podział). „N na województwo” jest
#: szczególnym przypadkiem „N w grupie” z grupą = region, a nie osobną arytmetyką (§ 1.2.5).
MODE_MAP = {
    "MIN_POINTS": ("MIN_POINTS", ""),
    "TOP_N": ("TOP_N", ""),
    "TOP_N_PER_DISTRICT": ("TOP_N_PER_GROUP", "REGION"),
    "HYBRID": ("HYBRID", ""),
}


def forwards(apps, schema_editor):
    """Dla każdej edycji: kroki toru z istniejących etapów, reguły z istniejących progów."""
    Edition = apps.get_model("competitions", "Edition")
    Stage = apps.get_model("competitions", "Stage")
    QualificationRule = apps.get_model("competitions", "QualificationRule")
    PipelineStep = apps.get_model("competitions", "PipelineStep")
    TransitionRule = apps.get_model("competitions", "TransitionRule")

    for edition_id in Edition.objects.order_by("pk").values_list("pk", flat=True).iterator():
        for stage in Stage.objects.filter(edition_id=edition_id).order_by("id"):
            off = stage.kind in OFF_PIPELINE_KINDS
            step, _ = PipelineStep.objects.update_or_create(
                stage_id=stage.pk,
                defaults={
                    "edition_id": edition_id,
                    "position": 0 if off else POSITION_BY_KIND.get(stage.kind, UNKNOWN_POSITION),
                    "off_pipeline": off,
                },
            )
            if off:
                continue
            # Próg czytamy zapytaniem, a nie odwrotnym akcesorem ``stage.qualification_rule``:
            # brak progu jest tu zwykłym przypadkiem (etap bez kwalifikacji), a nie wyjątkiem,
            # i nie ma powodu, żeby przechodził przez ``RelatedObjectDoesNotExist``.
            rule = QualificationRule.objects.filter(stage_id=stage.pk).first()
            if rule is None:
                continue
            # Brak klucza jest błędem wdrożenia, a nie przypadkiem do pominięcia: tryb spoza tej
            # czwórki znaczyłby, że ktoś dołożył wartość do ``QualificationMode`` bez odwzorowania,
            # a cicho pominięty próg to etap, z którego nagle nikt nie przechodzi dalej.
            mode, group_by = MODE_MAP[rule.mode]
            TransitionRule.objects.update_or_create(
                step_id=step.pk,
                position=0,
                defaults={
                    "mode": mode,
                    "group_by": group_by,
                    "min_points": rule.min_points,
                    "top_n": rule.top_n,
                },
            )


def backwards(apps, schema_editor):
    """Cofnięcie **nie kasuje** kroków ani reguł – i to jest świadoma asymetria.

    Wiersze tej migracji nie są przepisaniem cudzych danych (jak backfill właściciela w ``0020``),
    tylko zapisem, którego nikt jeszcze nie czyta: dopóki flaga ``process_editor`` jest wyłączona,
    ich obecność jest dla wyniku bez znaczenia. Za to po dniu pracy w edytorze procesu ta sama
    tabela trzyma już kolejność ustawioną ręcznie przez koordynatora – a ``DELETE`` bez warunku
    skasowałby ją razem z resztą. Odwrotnością, która naprawdę zdejmuje te wiersze, jest cofnięcie
    ``0023``: ono usuwa całe tabele i nie ma jak pomylić wiersza z migracji z wierszem z panelu.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0023_pipeline_step_transition_rule"),
    ]

    operations = [
        # ``elidable=False``: jednorazowe przepisanie produkcyjnego przebiegu na wiersze, a nie
        # krok budowy schematu – ``squashmigrations`` nie ma prawa go zwinąć.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
