"""Wykrywanie podobieństw: normalizacja, wynik pary, zakres porównania i oczyszczanie porównania.

Przedmiotem testów jest to, co decyduje o wartości tego narzędzia: czy podmiana nazw zmiennych
i komentarzy **nie** obniża wyniku (bo tak wygląda przepisana praca), czy zadania oddawane
w PDF-ie w ogóle do niego nie wchodzą, i czy treść cudzego pliku nie może wnieść na stronę
koordynatora ani jednego własnego znacznika HTML.
"""

from io import BytesIO

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory, StageFactory
from apps.core.models import AuditLog
from apps.submissions.models import AvStatus, SubmissionSimilarity, SubmissionStatus
from apps.submissions.similarity import (
    comparable_problems,
    fingerprint_from_text,
    notebook_source,
    recompute_problem,
    recompute_stage,
    sanitise_html,
    score_pair,
    toggle_report,
    tokenize,
)
from apps.submissions.storage import get_submission_storage
from apps.submissions.tests.factories import SubmissionFactory, SubmissionFileFactory, notebook_bytes

pytestmark = pytest.mark.django_db

#: Rozwiązanie „wzorcowe” i jego kopia z podmienionymi nazwami oraz innymi komentarzami –
#: dokładnie taka, jaką oddaje ktoś, kto przepisał cudzy plik i „zatarł ślady”.
ORIGINAL = """
# rozwiązanie zadania 1
import math


def policz_sume(dane):
    wynik = 0
    for element in dane:
        wynik += math.sqrt(element)
    return wynik


print(policz_sume([1, 2, 3]))
"""

RENAMED = """
# moje podejscie, chyba dziala
import math


def suma(lista):
    acc = 0
    for x in lista:
        acc += math.sqrt(x)
    return acc


print(suma([4, 5, 6]))
"""

DIFFERENT = """
import json


class Parser:
    def __init__(self, tekst):
        self.tekst = tekst

    def parsuj(self):
        return json.loads(self.tekst)


def main():
    parser = Parser('{"a": 1}')
    for klucz, wartosc in parser.parsuj().items():
        print(klucz, wartosc)
"""


def stored(entry, problem, content: bytes, *, extension: str = "py"):
    """Rozwiązanie z prawdziwym plikiem w storage testowym i rozszerzeniem widocznym w kluczu."""
    submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.LOCKED)
    submission_file = SubmissionFileFactory(
        submission=submission,
        av_status=AvStatus.CLEAN,
        object_key=f"1/1/{entry.participant.public_code}/{submission.uuid}/{'a' * 64}.{extension}",
    )
    get_submission_storage().put(submission_file.object_key, BytesIO(content), "text/plain")
    return submission


@pytest.fixture
def code_problem():
    stage = StageFactory()
    return ProblemFactory(stage=stage, number=1, allowed_formats=["py", "ipynb"])


def test_normalizacja_zaciera_nazwy_ale_zostawia_slowa_kluczowe():
    """Identyfikatory stają się ``ID``, słowa kluczowe zostają sobą, komentarz znika."""
    tokens = tokenize("def policz(x):  # komentarz\n    return x + 1\n")

    assert "policz" not in tokens
    assert "komentarz" not in tokens
    assert tokens[:2] == ("def", "ID")
    assert "return" in tokens


def test_podmiana_nazw_nie_ratuje_przepisanej_pracy():
    """To jest cały powód istnienia modułu: kształt rozwiązania zostaje ten sam."""
    renamed = score_pair(fingerprint_from_text(1, ORIGINAL), fingerprint_from_text(2, RENAMED))
    unrelated = score_pair(fingerprint_from_text(1, ORIGINAL), fingerprint_from_text(3, DIFFERENT))

    assert renamed > 0.8
    assert unrelated < renamed


def test_notatnik_bierze_tylko_komorki_kodu():
    """Komórki tekstowe i wyniki wykonania nie są rozwiązaniem i nie mogą wpływać na wynik."""
    source = notebook_source(notebook_bytes(output_text="WYNIK-Z-KONSOLI"))

    assert "print('hej')" in source
    assert "WYNIK-Z-KONSOLI" not in source


def test_uszkodzony_notatnik_nie_wywraca_porownania():
    """Uczestnik mógł oddać plik uszkodzony – to nie jest miejsce, w którym się to rozstrzyga."""
    assert notebook_source(b"to nie jest JSON") == ""


def test_porownujemy_tylko_zadania_kodowe():
    """Zadanie na PDF nie ma czego normalizować – nie wchodzi do porównania."""
    stage = StageFactory()
    ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"])
    code = ProblemFactory(stage=stage, number=2, allowed_formats=["ipynb"])

    assert [problem.pk for problem in comparable_problems(stage)] == [code.pk]


def test_zapisujemy_pary_powyzej_progu_w_ustalonej_kolejnosci(code_problem):
    """Para jest nieuporządkowana, ale w bazie ma jedną, powtarzalną postać (mniejszy pk pierwszy)."""
    stage = code_problem.stage
    first = stored(StageEntryFactory(stage=stage), code_problem, ORIGINAL.encode("utf-8"))
    second = stored(StageEntryFactory(stage=stage), code_problem, RENAMED.encode("utf-8"))

    summary = recompute_problem(code_problem)

    assert summary.compared == 1
    assert summary.stored == 1
    pair = SubmissionSimilarity.objects.get()
    assert (pair.submission_a_id, pair.submission_b_id) == (
        min(first.pk, second.pk),
        max(first.pk, second.pk),
    )
    assert pair.score > 0.8
    assert pair.stage_id == stage.pk


def test_prace_niepodobne_nie_trafiaja_do_bazy(code_problem):
    """Próg przechowywania istnieje po to, żeby tabela nie była iloczynem kartezjańskim etapu."""
    stage = code_problem.stage
    stored(StageEntryFactory(stage=stage), code_problem, ORIGINAL.encode("utf-8"))
    stored(StageEntryFactory(stage=stage), code_problem, DIFFERENT.encode("utf-8"))

    recompute_problem(code_problem)

    assert not SubmissionSimilarity.objects.exists()


def test_przeliczenie_zastepuje_poprzednie_ale_zostawia_zgloszenie(code_problem):
    """Wynik obliczenia wolno nadpisać; decyzję człowieka („zgłoszone do komitetu”) – nie."""
    stage = code_problem.stage
    stored(StageEntryFactory(stage=stage), code_problem, ORIGINAL.encode("utf-8"))
    stored(StageEntryFactory(stage=stage), code_problem, RENAMED.encode("utf-8"))
    recompute_problem(code_problem)
    coordinator = CoordinatorFactory()
    toggle_report(SubmissionSimilarity.objects.get(), actor=coordinator)

    recompute_problem(code_problem)

    assert SubmissionSimilarity.objects.count() == 1
    assert SubmissionSimilarity.objects.get().is_reported


def test_zgloszenie_da_sie_cofnac_i_zostawia_slad(code_problem):
    """Para wyjaśniona na posiedzeniu wraca do zwykłej listy, a oba kliknięcia są w audycie."""
    stage = code_problem.stage
    stored(StageEntryFactory(stage=stage), code_problem, ORIGINAL.encode("utf-8"))
    stored(StageEntryFactory(stage=stage), code_problem, RENAMED.encode("utf-8"))
    recompute_problem(code_problem)
    pair = SubmissionSimilarity.objects.get()
    coordinator = CoordinatorFactory()

    assert toggle_report(pair, actor=coordinator) is True
    assert toggle_report(pair, actor=coordinator) is False
    assert AuditLog.objects.filter(action="similarity.reported").exists()
    assert AuditLog.objects.filter(action="similarity.report_withdrawn").exists()


def test_audyt_przeliczenia_ma_same_liczniki(code_problem):
    """Wpis audytowy czytają osoby bez prawa do wiedzy, czyja praca jest do czyjej podobna."""
    stage = code_problem.stage
    stored(StageEntryFactory(stage=stage), code_problem, ORIGINAL.encode("utf-8"))
    stored(StageEntryFactory(stage=stage), code_problem, RENAMED.encode("utf-8"))
    coordinator = CoordinatorFactory()

    result = recompute_stage(stage, actor=coordinator)

    entry = AuditLog.objects.get(action="similarity.recomputed")
    assert set(entry.diff) == {"stage_id", "problems", "compared", "stored", "truncated"}
    assert result["stored"] == 1


def test_porownanie_nie_przepuszcza_obcych_znacznikow():
    """Kod uczestnika trafia na stronę jako HTML – biała lista jest drugą barierą po escapowaniu."""
    cleaned = sanitise_html(
        '<table><tr><td class="diff_add">x</td>'
        '<td onclick="zle()"><script>alert(1)</script>tekst</td></tr></table>'
    )

    assert "<script>" not in cleaned
    assert "onclick" not in cleaned
    assert 'class="diff_add"' in cleaned
    assert "tekst" in cleaned
