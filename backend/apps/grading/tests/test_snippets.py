"""Szablony komentarzy: parsowanie tekstu, zapis przy zadaniu i widoczność wspólne/własne."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import CommentSnippet
from apps.grading.snippets import (
    MAX_SNIPPETS,
    add_own_snippet,
    delete_own_snippet,
    format_snippet_lines,
    parse_snippet_lines,
    problem_snippets,
    set_problem_snippets,
    snippets_for,
)

pytestmark = pytest.mark.django_db


def test_parsowanie_wierszy_rozdziela_tytul_od_tresci():
    items = parse_snippet_lines("Brak jednostek;Wynik jest poprawny, ale bez jednostek.\n")

    assert items == [{"title": "Brak jednostek", "text": "Wynik jest poprawny, ale bez jednostek."}]


def test_srednik_w_tresci_zostaje_w_tresci():
    """Rozdzielamy raz: tytuł to dwa słowa, a treść wolno pisać ze średnikami."""
    items = parse_snippet_lines("Uzasadnienie;Brakuje kroku; bez niego wynik jest przypadkowy.")

    assert items[0]["text"] == "Brakuje kroku; bez niego wynik jest przypadkowy."


def test_wiersz_bez_srednika_wskazuje_swoj_numer():
    with pytest.raises(ValueError) as error:
        parse_snippet_lines("dobry;ok\nzly wiersz")

    assert "Wiersz 2" in str(error.value)


def test_pusty_tytul_i_pusta_tresc_sa_bledem():
    with pytest.raises(ValueError):
        parse_snippet_lines(";sama treść")
    with pytest.raises(ValueError):
        parse_snippet_lines("sam tytuł;")


def test_format_sprowadza_tresc_do_jednego_wiersza():
    """Jeden szablon to jeden wiersz textarei – inaczej ponowny odczyt rozerwałby zapis."""
    problem = ProblemFactory()
    set_problem_snippets(problem, [{"title": "T", "text": "pierwszy\ndrugi"}])

    assert format_snippet_lines(problem_snippets(problem)) == "T;pierwszy drugi"


def test_zapis_zadania_aktualizuje_w_miejscu_i_kasuje_nadmiarowe():
    problem = ProblemFactory()
    set_problem_snippets(problem, [{"title": "A", "text": "a"}, {"title": "B", "text": "b"}])
    first_id = problem_snippets(problem)[0].pk

    set_problem_snippets(problem, [{"title": "A poprawione", "text": "a"}])

    rows = problem_snippets(problem)
    assert [item.title for item in rows] == ["A poprawione"]
    assert rows[0].pk == first_id


def test_zapis_bez_zmian_nie_tworzy_wpisu_audytowego():
    problem = ProblemFactory()
    items = [{"title": "A", "text": "a"}]
    set_problem_snippets(problem, items)
    before = AuditLog.objects.filter(action="snippet.problem_set").count()

    set_problem_snippets(problem, items)

    assert AuditLog.objects.filter(action="snippet.problem_set").count() == before


def test_zapis_zadania_nie_rusza_prywatnych_szablonow_recenzentow():
    """Koordynator zapisujący zadanie nie może skasować cudzego notatnika."""
    problem = ProblemFactory()
    reviewer = ActiveReviewerFactory()
    add_own_snippet(reviewer, "Moje", "moja treść", problem=problem)

    set_problem_snippets(problem, [])

    assert CommentSnippet.objects.filter(owner=reviewer).count() == 1


def test_widocznosc_najpierw_wspolne_potem_wlasne():
    problem = ProblemFactory()
    reviewer = ActiveReviewerFactory()
    set_problem_snippets(problem, [{"title": "Wspólny", "text": "w"}])
    add_own_snippet(reviewer, "Własny", "wl", problem=problem)

    rows = snippets_for(problem, reviewer)

    assert [item.title for item in rows] == ["Wspólny", "Własny"]


def test_szablon_ogolny_widac_przy_kazdym_zadaniu():
    first = ProblemFactory(number=1)
    second = ProblemFactory(stage=first.stage, number=2)
    reviewer = ActiveReviewerFactory()
    add_own_snippet(reviewer, "Ogólny", "o", problem=None)

    assert [item.title for item in snippets_for(second, reviewer)] == ["Ogólny"]


def test_cudzy_prywatny_szablon_nie_wychodzi():
    problem = ProblemFactory()
    mine = ActiveReviewerFactory()
    theirs = ActiveReviewerFactory()
    add_own_snippet(theirs, "Cudzy", "c", problem=problem)

    assert snippets_for(problem, mine) == []


def test_szablon_zadania_nie_wchodzi_do_innego_zadania():
    first = ProblemFactory(number=1)
    second = ProblemFactory(stage=first.stage, number=2)
    reviewer = ActiveReviewerFactory()
    set_problem_snippets(first, [{"title": "Tylko pierwsze", "text": "x"}])

    assert snippets_for(second, reviewer) == []


def test_pusty_tytul_wlasnego_szablonu_to_odmowa():
    reviewer = ActiveReviewerFactory()

    with pytest.raises(DomainError) as error:
        add_own_snippet(reviewer, "   ", "treść")

    assert error.value.machine_code == "SNIPPET_TITLE_REQUIRED"


def test_limit_wlasnych_szablonow():
    reviewer = ActiveReviewerFactory()
    for index in range(MAX_SNIPPETS):
        add_own_snippet(reviewer, f"T{index}", "treść")

    with pytest.raises(DomainError) as error:
        add_own_snippet(reviewer, "jeszcze jeden", "treść")

    assert error.value.machine_code == "SNIPPET_LIMIT"


def test_kasowanie_cudzego_szablonu_to_odmowa():
    mine = ActiveReviewerFactory()
    theirs = ActiveReviewerFactory()
    snippet = add_own_snippet(theirs, "Cudzy", "c")

    with pytest.raises(DomainError) as error:
        delete_own_snippet(mine, snippet)

    assert error.value.machine_code == "SNIPPET_NOT_OWNED"
    assert CommentSnippet.objects.filter(pk=snippet.pk).exists()


def test_kasowanie_wlasnego_szablonu_zostawia_slad_w_audycie():
    reviewer = ActiveReviewerFactory()
    snippet = add_own_snippet(reviewer, "Mój", "m")

    delete_own_snippet(reviewer, snippet)

    assert not CommentSnippet.objects.filter(pk=snippet.pk).exists()
    assert AuditLog.objects.filter(action="snippet.deleted").exists()


# --- właściciel szablonu (wydanie D) --------------------------------------------------------------


def test_szablon_zadania_nalezy_do_konkursu_tego_zadania(other_competition):
    """Konkurs bierze się z **zadania**, a nie z kontekstu żądania.

    Zapis idzie tu z kontekstu Konkursu #1 (wiąże go autouse z ``conftest``), a zadanie należy do
    konkursu drugiego – i to zadanie ma wygrać. Odwrotna kolejność przepisywałaby szablony do tego
    konkursu, pod którego domeną akurat zalogował się koordynator.
    """
    problem = ProblemFactory(competition=other_competition)

    set_problem_snippets(problem, [{"title": "Jednostki", "text": "Podaj jednostki wyniku."}])

    assert problem_snippets(problem)[0].competition_id == other_competition.pk


def test_wlasny_szablon_ogolny_nalezy_do_konkursu_recenzenta(other_competition):
    """Prywatny szablon bez zadania nie ma innego wskazania niż komitet, w którym go napisano."""
    reviewer = ActiveReviewerFactory(competition=other_competition)

    snippet = add_own_snippet(reviewer, "Mój ogólny", "treść")

    assert snippet.competition_id == other_competition.pk


def test_szablon_ogolny_widac_wylacznie_w_swoim_konkursie(competition, other_competition):
    """Szablon „do wszystkiego” był do wydania D wspólny dla całej instalacji – i to był wyciek."""
    ours = CommentSnippet.objects.create(competition=competition, title="Nasz", text="n")
    CommentSnippet.objects.create(competition=other_competition, title="Obcy", text="o")
    reviewer = ActiveReviewerFactory(competition=competition)

    assert snippets_for(ProblemFactory(competition=competition), reviewer) == [ours]
