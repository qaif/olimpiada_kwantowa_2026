"""Koszt konwertera Markdown (TR-01b): liniowy skład, ten sam wynik, bramka kosztu w punkcie wejścia.

Bez bazy. Test różnicowy porównuje każde podstawienie z ``re.sub``/``re.finditer`` tego samego
wyrażenia na losowych krótkich tekstach ze znaków znaczników – krótkich, bo tam ``re.sub`` jest
tani, a wszystkie przypadki brzegowe (niedomknięcia, sąsiednie ``**``, końce wierszy) i tak padają.
"""

from __future__ import annotations

import random
import re
import time

import pytest
from django.test import override_settings

from apps.problem_translations import markup

#: Znaki, z których składają się znaczniki, plus litera, cyfra, spacje i twarda spacja (``\s``).
ALPHABET = "**$$\\\\()[]``\n\n  \t\xa0ax1_|-:#>"

#: Wyrażenie separatora tabeli z TR-01 – ten sam język, co nowe ``TABLE_SEPARATOR``.
OLD_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def samples(count: int = 4000, longest: int = 24, seed: int = 20261018):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, longest)))


def tag(match: re.Match) -> str:
    """Zastąpienie, które pokazuje wszystkie grupy – różnica w grupach jest widoczna w wyniku."""
    return "<" + "|".join(str(group) for group in match.groups()) + ">"


@pytest.mark.parametrize(
    ("pattern", "linear"),
    [
        (markup.BOLD, markup._sub_bold),
        (markup.ITALIC, markup._sub_italic),
        (markup.FENCE, markup._sub_fence),
        (markup.DISPLAY_MATH, markup._sub_display_math),
        (markup.INLINE_MATH, markup._sub_inline_math),
    ],
    ids=["bold", "italic", "fence", "display_math", "inline_math"],
)
def test_linear_substitution_equals_re_sub(pattern, linear):
    for text in samples():
        assert linear(text, tag) == pattern.sub(tag, text), repr(text)


def test_linear_pdf_blocks_equal_re_finditer():
    for text in samples():
        expected = [(m.span(), m.groups()) for m in markup.PDF_BLOCK.finditer(text)]
        assert [(m.span(), m.groups()) for m in markup._pdf_matches(text)] == expected, repr(text)


def test_table_separator_describes_the_same_lines():
    rng = random.Random(7)
    for _ in range(20000):
        line = "".join(rng.choice(" \t|-:x") for _ in range(rng.randint(0, 16)))
        assert (markup.TABLE_SEPARATOR.match(line) is None) == (OLD_TABLE_SEPARATOR.match(line) is None), (
            repr(line)
        )


def test_ordinary_markdown_renders_exactly_as_in_tr01():
    # Wynik konwertera sprzed TR-01b, znak w znak (także ``***oba***`` – zagnieżdżenie jak dotąd).
    text = (
        "# Zadanie\n\nTekst **pogrubiony**, *kursywa*, ***oba***, `kod` i $x^2$ oraz \\(y\\).\n\n"
        "$$\\frac{1}{2}$$\n\n```python\nprint(1)\n```\n\n- a\n- **b**\n\n| a | b |\n|---|:--:|\n| 1 | 2 |"
    )
    assert str(markup.render(text)) == (
        "<h2>Zadanie</h2>\n"
        "<p>Tekst <strong>pogrubiony</strong>, <em>kursywa</em>, <strong><em>oba</strong></em>, "
        '<code>kod</code> i <span class="tr-math" data-display="0">x^2</span> oraz '
        '<span class="tr-math" data-display="0">y</span>.</p>\n'
        '<p><span class="tr-math tr-math--display" data-display="1">\\frac{1}{2}</span></p>\n'
        "<p><pre><code>print(1)\n</code></pre></p>\n"
        "<ul><li>a</li><li><strong>b</strong></li></ul>\n"
        '<table class="tr-table"><thead><tr><th>a</th><th>b</th></tr></thead>'
        "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
    )


# --- patologiczne wejścia ---------------------------------------------------------------------------

PATHOLOGICAL = {
    "bold": "**a " * 50_000,
    "italic": "*a " * 50_000,
    "underscores": "_" * 100_000,
    "underscores_x": "_" * 100_000 + "x",
    "brackets": "[" * 100_000,
    "display_brackets": "\\[" * 50_000,
    "parens": "\\(" * 50_000,
    "dollars": "$" * 100_000,
    "dollar_words": "$a " * 50_000,
    "nested_math": "$$a\\[" * 50_000,
    "fences": "```x\n" * 20_000,
    "table_spaces": "|\n" + " " * 200_000 + "x",
    "table_spaces_after_dashes": "|\n|--" + " " * 100_000 + "|" + " " * 100_000 + "x",
    "mixed": "**a *b $c \\(d \\[e ```f\n" * 10_000,
}


#: Górna granica czasu jednego wejścia patologicznego. Kwadratowy konwerter potrzebował na nich
#: minut (200 KB `**a ` ≈ 142 s), liniowy – setnych sekundy; 5 s zostawia zapas na obciążony runner CI
#: (21.10.2026: `nested_math` 1,35 s przy dawnej granicy 1 s) i nadal łapie powrót do kosztu kwadratowego.
TIME_LIMIT_S = 5


@pytest.mark.parametrize("text", PATHOLOGICAL.values(), ids=PATHOLOGICAL.keys())
def test_pathological_input_is_fast_even_without_the_budget(text):
    # Sam konwerter, z pominięciem bramki – dowód, że koszt jest liniowy, a nie tylko odcięty.
    with override_settings(MARKDOWN_COST_BUDGET=10**18):
        started = time.perf_counter()
        markup.render(text)
        markup.plain_blocks(text)
        assert time.perf_counter() - started < TIME_LIMIT_S


@pytest.mark.parametrize("text", PATHOLOGICAL.values(), ids=PATHOLOGICAL.keys())
def test_pathological_input_is_fast_through_the_entry_points(text):
    started = time.perf_counter()
    markup.render(text)
    markup.plain_blocks(text)
    assert time.perf_counter() - started < TIME_LIMIT_S


# --- bramka kosztu ----------------------------------------------------------------------------------


def test_text_over_the_budget_is_escaped_text_without_markup():
    evil = "<b>x</b> " + "**a " * 50_000

    assert markup.render_within_budget(evil) is None
    html = str(markup.render(evil))
    assert html.startswith('<pre class="tr-plain">&lt;b&gt;x&lt;/b&gt; **a **a ')
    assert "<strong>" not in html and "<b>" not in html


def test_budget_comes_from_the_setting():
    text = "**a** *b*"
    assert markup.markdown_cost(text) > 1

    with override_settings(MARKDOWN_COST_BUDGET=1):
        assert markup.render_within_budget(text) is None
        assert str(markup.render(text)) == '<pre class="tr-plain">**a** *b*</pre>'
        assert markup.plain_blocks("# H\n$$x$$") == [("paragraph", "# H $$x$$")]
    assert "<strong>a</strong>" in str(markup.render(text))


def test_ordinary_text_stays_well_under_the_budget():
    chapter = "## Rozdział\n\nTekst **pogrubiony**, *kursywa*, `kod` i $x$.\n\n```python\nprint(1)\n```\n\n"
    assert markup.markdown_cost(chapter * 10_000) < markup.DEFAULT_COST_BUDGET // 5
