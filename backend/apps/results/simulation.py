"""Symulacja progu kwalifikacji: „co by było, gdyby próg wyglądał tak”.

Po co osobny moduł, skoro próg liczy już ``apps.results.services``: tam kwalifikacja jest
**decyzją** – zmienia statusy wpisów, zakłada wpisy w następnym etapie i zostawia ślad w audycie.
Koordynator potrzebuje jednak móc najpierw zobaczyć skutek reguły, zanim ją ogłosi: ilu ludzi
wejdzie, jak rozłożą się po województwach i gdzie wypadnie odcięcie. To jest pytanie, a nie
decyzja, więc ma własne wejście i **niczego nie zapisuje**.

Logika punktacji i progu nie jest tu powtórzona ani o linijkę. Wiersze liczy
``services.compute_stage_results`` (w trybie ``preview``), a to, kto mieści się w progu, rozstrzyga
``services._qualified_entry_ids`` – ta sama funkcja, którą wywoła późniejsze
``apply_qualification``. Gdyby symulacja miała własną kopię reguły, prędzej czy później pokazałaby
inny wynik niż przeliczenie i byłaby gorsza niż jej brak.

Reguła do symulacji jest **niezapisanym** obiektem ``QualificationRule``: model zna już
pierwszeństwo pól (``requires_min_points``/``requires_top_n``) i jego walidację, więc symulacja nie
musi wiedzieć, który tryb czego wymaga. Zapis reguły jest osobną czynnością – ``apply_rule`` –
wołaną wyłącznie wtedy, gdy koordynator naciśnie przycisk.
"""

from __future__ import annotations

import logging
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status as http

from apps.competitions.models import QualificationMode, QualificationRule, Stage, StageEntryStatus
from apps.core.api import DomainError
from apps.core.models import audit
from apps.submissions.models import Submission, SubmissionStatus

from .services import _district_key, _qualified_entry_ids, compute_stage_results, qualified_with_manual

logger = logging.getLogger(__name__)

#: Etykieta grupy dla wierszy bez wpisanego województwa. Pusta nazwa w nagłówku tabeli wyglądałaby
#: jak błąd renderowania, a „brak” jest tu prawdziwą odpowiedzią (profile sprzed listy zamkniętej).
UNKNOWN_DISTRICT_LABEL = "— brak województwa —"


def build_rule(stage: Stage, mode: str, min_points: int | None, top_n: int | None) -> QualificationRule:
    """Niezapisana reguła do symulacji. Sprawdza komplet parametrów trybu, tak jak zapis.

    Walidację robi ``full_clean`` modelu, a nie własny zestaw ``if``-ów: reguła „tryb TOP_N wymaga
    dodatniego top_n” ma jedno miejsce w systemie i jest nim model. Dzięki temu symulacja odrzuca
    dokładnie te same parametry, których nie przyjąłby zapis – nie da się obejrzeć podglądu reguły,
    której potem nie da się zastosować.

    ``ValidationError`` modelu zamieniamy na ``DomainError``, bo tego drugiego widoki panelu już
    się spodziewają (``ActionViewMixin`` robi z niego komunikat, a nie 500). Brakujący parametr
    trybu jest tu **zwykłym stanem ekranu** – koordynator dopiero wybrał tryb i jeszcze nie wpisał
    liczby – a nie awarią, więc nie ma prawa wywrócić strony.
    """
    if mode not in QualificationMode.values:
        raise DomainError(
            f"Nieznany tryb progu kwalifikacji: {mode}.",
            "QUALIFICATION_RULE_INVALID",
            http.HTTP_400_BAD_REQUEST,
        )
    rule = QualificationRule(stage=stage, mode=mode, min_points=min_points, top_n=top_n)
    try:
        # ``exclude`` pomija sprawdzenie unikalności ``stage`` – etap, który ma już regułę,
        # zgłaszałby tu konflikt sam ze sobą, a symulacja świadomie nie zamierza niczego zapisywać.
        rule.full_clean(exclude=["stage"], validate_unique=False)
    except ValidationError as exc:
        raise DomainError(
            " ".join(message for messages in exc.message_dict.values() for message in messages),
            "QUALIFICATION_RULE_INVALID",
            http.HTTP_400_BAD_REQUEST,
        ) from exc
    return rule


def ungraded_count(stage: Stage) -> int:
    """Ile prac etapu nie ma jeszcze oceny końcowej.

    Liczba jest częścią odpowiedzi, a nie ciekawostką: symulacja liczy pracę bez ``FinalGrade``
    jako zero punktów (patrz ``compute_stage_results(preview=True)``), więc bez tej liczby
    koordynator nie wie, na ile wynik jest jeszcze ruchomy. Prace odrzucone przez antywirusa nie
    wchodzą do rachunku – one nigdy nie miały być ocenione.
    """
    return (
        Submission.objects.filter(entry__stage=stage, final_grade__isnull=True)
        .exclude(status=SubmissionStatus.REJECTED_INFECTED)
        .count()
    )


def _district_label(row: dict) -> str:
    return (row.get("district") or "").strip() or UNKNOWN_DISTRICT_LABEL


def simulate(stage: Stage, mode: str, min_points: int | None, top_n: int | None) -> dict:
    """Podgląd kwalifikacji dla zadanej reguły. Nic nie zapisuje, niczego nie ogłasza.

    Zdyskwalifikowani są poza progiem – tak samo, jak w ``apply_qualification``: dyskwalifikacja
    jest decyzją proceduralną, a nie wynikiem punktowym, więc nie może zajmować miejsca w „top N”.
    W zwracanej tabeli zostają, z jawnym ``qualified=False``, bo koordynator ma widzieć komplet
    wpisów etapu, a nie tabelę, z której ktoś zniknął bez wyjaśnienia.

    ``cutoff`` to najniższa suma punktów, która jeszcze się kwalifikuje – wyliczona z wyniku,
    a nie z parametru reguły. Przy ``TOP_N`` parametrem jest liczba miejsc, a nie punkty; przy
    ``TOP_N_PER_DISTRICT`` progów jest tyle, ile województw, więc jedna liczba byłaby wtedy
    zaokrągleniem do najniższego z nich – i dokładnie tak jest opisana na ekranie.
    """
    rule = build_rule(stage, mode, min_points, top_n)
    rows = compute_stage_results(stage, preview=True)
    candidates = [row for row in rows if row["status"] != StageEntryStatus.DISQUALIFIED]
    qualified_ids = _qualified_entry_ids(candidates, rule)
    disqualified_ids = {row["entry_id"] for row in rows} - {row["entry_id"] for row in candidates}
    for row in rows:
        # Decyzja komitetu bije próg także w podglądzie – inaczej symulacja pokazywałaby inny
        # skład niż późniejsze przeliczenie, a to jest ekran, na którym próg się dobiera.
        # Zdyskwalifikowanego nie podnosi nawet ona: dyskwalifikacja jest osobną decyzją i to
        # ona wymaga cofnięcia, a nie obejścia drugą decyzją (tak samo w ``apply_qualification``).
        if row["entry_id"] in disqualified_ids:
            row["qualified"] = False
            continue
        row["qualified"] = qualified_with_manual(row, row["entry_id"] in qualified_ids)

    qualified_rows = [row for row in rows if row["qualified"]]
    totals = [row["total"] for row in qualified_rows]
    by_district = Counter(_district_label(row) for row in qualified_rows)
    entered = Counter(_district_label(row) for row in candidates)
    return {
        "stage": stage,
        "rule": rule,
        "rows": rows,
        "qualified": len(qualified_rows),
        "not_qualified": len(candidates) - len(qualified_rows),
        "disqualified": len(rows) - len(candidates),
        "cutoff": min(totals) if totals else None,
        # Posortowane po nazwie województwa: tabela ma być czytana jak lista, a nie jak ranking
        # regionów. Kolumna „startujących” stoi obok, bo sama liczba zakwalifikowanych nie mówi,
        # czy województwo jest mocne, czy po prostu liczne.
        "districts": [
            {"district": name, "qualified": count, "entered": entered.get(name, 0)}
            for name, count in sorted(by_district.items(), key=lambda item: _district_key(item[0]))
        ],
        "ungraded": ungraded_count(stage),
    }


@transaction.atomic
def apply_rule(
    stage: Stage,
    mode: str,
    min_points: int | None,
    top_n: int | None,
    *,
    actor=None,
    request=None,
) -> QualificationRule:
    """Zapisuje próg kwalifikacji etapu. Sam **nie** przelicza wyników i nikogo nie kwalifikuje.

    Rozdzielenie jest celowe: zapis reguły to ustawienie parametru etapu, a kwalifikacja –
    ogłoszenie wyniku wobec uczestników. Koordynator, który dobrał próg w symulacji, zapisuje go
    tutaj, a przelicza wtedy, kiedy ocenianie jest skończone (przycisk na pulpicie). Gdyby zapis
    od razu kwalifikował, każde majstrowanie przy progu zmieniałoby ludziom status w panelu.

    Do audytu (``stage.rule_updated``) idą wyłącznie parametry reguły – żadnych nazwisk, żadnych
    pseudonimów, nawet liczby zakwalifikowanych (bo ta funkcja niczego nie kwalifikuje).
    """
    rule = build_rule(stage, mode, min_points, top_n)
    existing = QualificationRule.objects.select_for_update().filter(stage=stage).first()
    before = (
        {"mode": existing.mode, "min_points": existing.min_points, "top_n": existing.top_n}
        if existing is not None
        else None
    )
    saved, _created = QualificationRule.objects.update_or_create(
        stage=stage,
        defaults={"mode": rule.mode, "min_points": rule.min_points, "top_n": rule.top_n},
    )
    audit(
        actor,
        "stage.rule_updated",
        stage,
        {
            "before": before,
            "after": {"mode": saved.mode, "min_points": saved.min_points, "top_n": saved.top_n},
        },
        request=request,
    )
    logger.info(
        "Etap %s: próg kwalifikacji ustawiony na %s (min=%s, top=%s)",
        stage.pk,
        saved.mode,
        saved.min_points,
        saved.top_n,
    )
    return saved
