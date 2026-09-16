"""Statystyki ogłoszonych etapów i publiczna strona ``/statystyki/``.

Dwie rzeczy trzymamy tu na oku:

- **liczby mają zgadzać się z ogłoszoną tabelą co do sztuki**, bo pochodzą z tego samego,
  zamrożonego snapshotu. Statystyka licząca z żywych ``FinalGrade`` rozjechałaby się z tabelą
  przy pierwszej decyzji komisji podjętej po publikacji,
- **nic osobowego**. Strona jest publiczna i wypisuje wyłącznie liczby; rozbicie na województwa
  pojawia się tylko wtedy, gdy niesie je sam snapshot (czyli przy anonimizacji „kod uczestnika”).
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.competitions.models import QualificationMode, StageEntryStatus, StageKind
from apps.results.models import Anonymization
from apps.results.services import publish_results
from apps.results.statistics import build_statistics, statistics, width_class

from .conftest import graded_entry, make_stage

pytestmark = pytest.mark.django_db


def published_stage(*, anonymization=Anonymization.CODE):
    """Etap z ogłoszoną tabelą: cztery osoby, dwa zadania, próg „minimum 6 punktów”."""
    stage = make_stage(problems=2, mode=QualificationMode.MIN_POINTS, min_points=6)
    graded_entry(stage, [6, 6], district="mazowieckie")  # 12
    graded_entry(stage, [5, 5], district="mazowieckie")  # 10
    graded_entry(stage, [2, 2], district="malopolskie")  # 4
    graded_entry(stage, [0, 0], district="malopolskie")  # 0
    publish_results(stage, None, anonymization)
    return stage


# --- (a) szerokości słupków (CSS bez stylu inline) -----------------------------------------------


def test_bar_widths_snap_to_five_percent_steps():
    assert width_class(0) == "w0"
    assert width_class(42) == "w40"
    assert width_class(43) == "w45"
    assert width_class(100) == "w100"


def test_a_non_zero_share_is_never_invisible():
    """0,1 % zaokrąglone do zera znaczyłoby „nikt” – czyli nieprawdę."""
    assert width_class(0.1) == "w5"


# --- (b) liczby z zamrożonego snapshotu ----------------------------------------------------------


def test_statistics_describe_the_published_table():
    stage = published_stage()

    rows = build_statistics()

    assert len(rows) == 1
    row = rows[0]
    assert row["stage_id"] == stage.pk
    assert row["participants"] == 4
    # 12, 10, 4, 0 → średnia 6,5; mediana (10 + 4) / 2 = 7
    assert row["mean"] == 6.5
    assert row["median"] == 7.0
    assert row["max_total"] == 12
    assert row["qualified"] == 2
    # Próg to **najsłabszy zakwalifikowany**, a nie parametr reguły – jedno zdanie zrozumiałe
    # bez znajomości regulaminu.
    assert row["threshold"] == 10


def test_per_problem_distribution_counts_every_solution():
    published_stage()

    problem = build_statistics()[0]["problems"][0]

    assert problem["number"] == "1"
    assert problem["total"] == 4
    assert {bar["value"]: bar["count"] for bar in problem["bars"]} == {0: 1, 2: 1, 5: 1, 6: 1}


def test_districts_are_counted_when_the_snapshot_carries_them():
    published_stage()

    districts = build_statistics()[0]["districts"]

    # W snapshocie stoi etykieta z diakrytykami, a remis rozstrzyga porządek alfabetyczny
    # po złożonych znakach – „małopolskie” przed „mazowieckie”, jak w każdym polskim spisie.
    assert [(row["label"], row["count"]) for row in districts] == [
        ("małopolskie", 2),
        ("mazowieckie", 2),
    ]


def test_no_districts_when_the_table_does_not_publish_them():
    """Przy inicjałach ze szkołą okręg celowo wypada ze snapshotu – statystyka go nie odtwarza."""
    published_stage(anonymization=Anonymization.INITIALS_SCHOOL)

    assert build_statistics()[0]["districts"] == []


def test_stage_without_publication_is_absent():
    make_stage(problems=1)

    assert build_statistics() == []


def test_empty_table_has_no_mean_instead_of_zero():
    """„Średnia 0 punktów” i „nie ma z czego liczyć” to dwa różne zdania."""
    stage = make_stage(problems=1)
    publish_results(stage, None, Anonymization.CODE)

    row = build_statistics()[0]

    assert row["participants"] == 0
    assert row["mean"] is None
    assert row["median"] is None
    assert row["threshold"] is None


# --- (c) pamięć podręczna -------------------------------------------------------------------------


def test_result_is_cached_for_ten_minutes():
    published_stage()
    first = statistics()

    # Druga publikacja zmienia bazę, ale nie unieważnia wpisu – i to jest zamierzone: strona jest
    # publiczna, a dziesięć minut opóźnienia kosztuje mniej niż liczenie na każde wejście.
    make_stage(problems=1, edition=None)
    assert statistics() == first


# --- (d) strona publiczna -------------------------------------------------------------------------


def test_page_is_public_and_shows_the_numbers():
    stage = published_stage()

    response = Client().get("/statystyki/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Olimpiada w liczbach" in content
    assert "Mediana punktów" in content
    assert f"/results/{stage.pk}/" in content
    # Słupki są CSS-em z zamkniętej listy klas – żadnego ``style="width: …"`` w dokumencie.
    assert "stat-bar__fill--w" in content
    assert 'style="width' not in content


def test_page_without_results_says_so_instead_of_404():
    response = Client().get("/statystyki/")

    assert response.status_code == 200
    assert "Jeszcze nie ma liczb" in response.content.decode()


def test_page_carries_no_personal_data():
    stage = make_stage(problems=1, kind=StageKind.ELIM)
    graded_entry(
        stage,
        [6],
        status=StageEntryStatus.QUALIFIED,
        district="mazowieckie",
        school="XIV LO Warszawa",
    )
    publish_results(stage, None, Anonymization.CODE)

    content = Client().get("/statystyki/").content.decode()

    assert "XIV LO Warszawa" not in content
    assert "OLM-" not in content


def test_footer_links_to_the_statistics():
    """Strona ma być znajdowalna bez znajomości adresu – stąd odnośnik w stopce każdej strony."""
    content = Client().get("/statystyki/").content.decode()

    assert 'href="/statystyki/"' in content
