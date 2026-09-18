"""Tor etapów edycji z jej etapów – to samo, co robi migracja ``competitions.0024``, ale dla edycji
powstającej **po** niej.

Migracja danych wpisuje kroki toru i reguły przejścia edycjom stojącym w bazie w chwili wdrożenia.
Edycja założona później (``manage.py create_competition``, panel) nie ma jak przez nią przejść –
a bez kroków ekran „Przebieg edycji” pokazuje etapy i zero kroków, a reguły przejścia są
nieosiągalne (uwaga T43 z ``docs/UNIWERSALNY-ETAP-2.md`` § 3.1). Ta funkcja domyka tę lukę tym
samym odwzorowaniem, którym posłużyła się migracja: rodzaj etapu → miejsce w kolejce, próg
kwalifikacji → reguła przejścia. Jest idempotentna (``update_or_create`` po kluczu naturalnym),
więc wolno ją wołać ponownie – nie nadpisze pozycji przestawionych ręcznie w panelu, bo tych
kroków już nie tworzy, tylko zostawia (``get_or_create`` dla kroku, który istnieje).

Odwzorowania są **powtórzone**, a nie importowane z modułu migracji: migracja jest zapisem stanu na
dzień wdrożenia i ma prawo zniknąć przy ``squashmigrations``; kod aplikacji nie może na niej stać.
"""

from __future__ import annotations

from apps.competitions.models import (
    PipelineStep,
    QualificationMode,
    QualificationRule,
    Stage,
    StageKind,
    TransitionGroupBy,
    TransitionMode,
    TransitionRule,
)

#: Miejsce w kolejce dla rodzajów, które ją mają – odpowiednik ``STAGE_ORDER``.
POSITION_BY_KIND = {StageKind.ELIM: 1, StageKind.DISTRICT: 2, StageKind.FINAL: 3}
#: Rodzaje poza torem zawodów.
OFF_PIPELINE_KINDS = frozenset({StageKind.TRAINING})
#: Miejsce dla rodzaju spoza kolejki (np. ``ROUND`` założona ręcznie) – na końcu, nie w środku.
UNKNOWN_POSITION = 99
#: Dzisiejsze tryby progu → (tryb reguły przejścia, podział). Jak w ``competitions.0024``.
MODE_MAP = {
    QualificationMode.MIN_POINTS: (TransitionMode.MIN_POINTS, TransitionGroupBy.NONE),
    QualificationMode.TOP_N: (TransitionMode.TOP_N, TransitionGroupBy.NONE),
    QualificationMode.TOP_N_PER_DISTRICT: (TransitionMode.TOP_N_PER_GROUP, TransitionGroupBy.REGION),
    QualificationMode.HYBRID: (TransitionMode.HYBRID, TransitionGroupBy.NONE),
}


def ensure_pipeline(edition) -> list[PipelineStep]:
    """Krok toru dla każdego etapu edycji, który go jeszcze nie ma; zwraca **wszystkie** kroki.

    Etap treningowy dostaje ``off_pipeline=True`` i pozycję 0. Próg kwalifikacji etapu
    (``QualificationRule``) staje się jego pierwszą regułą przejścia – tylko przy tworzeniu kroku,
    żeby nie dublować reguł, które koordynator już ułożył.
    """
    steps: list[PipelineStep] = []
    for stage in Stage.objects.filter(edition=edition).order_by("id"):
        off = stage.kind in OFF_PIPELINE_KINDS
        step, created = PipelineStep.objects.get_or_create(
            stage=stage,
            defaults={
                "edition": edition,
                "position": 0 if off else POSITION_BY_KIND.get(stage.kind, UNKNOWN_POSITION),
                "off_pipeline": off,
            },
        )
        steps.append(step)
        if not created or off:
            continue
        rule = QualificationRule.objects.filter(stage=stage).first()
        if rule is None or rule.mode not in MODE_MAP:
            continue
        mode, group_by = MODE_MAP[rule.mode]
        TransitionRule.objects.get_or_create(
            step=step,
            position=0,
            defaults={
                "mode": mode,
                "group_by": group_by,
                "min_points": rule.min_points,
                "top_n": rule.top_n,
            },
        )
    return steps
