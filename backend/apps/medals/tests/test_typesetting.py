"""Skład tekstu w dowolnym piśmie (``apps.medals.typesetting``) – kierunek, kroje, kształtowanie, odwrót.

PDF-a nie da się tu „obejrzeć”, więc sprawdzamy to, co da się sprawdzić maszynowo: poziomy kierunku,
kolejność wizualną przebiegów, krój każdego przebiegu, osadzenie krojów w pliku (pypdf) i odwrót na
angielski, gdy pisma nie da się złożyć. Wizualnie – patrz ``docs/OPERACJE.md`` (próbka z każdego
języka do obejrzenia przed galą).
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader

from apps.medals import typesetting as t

#: Testy wymagające HarfBuzza (``uharfbuzz`` z ``pyproject.toml``). Obraz CI go ma; kontener bez
#: niego pomija tylko te testy – zachowanie bez kształtowania sprawdzają testy odwrotu niżej.
requires_shaping = pytest.mark.skipif(not t.shaping_available(), reason="brak uharfbuzz w tym środowisku")


def test_reportlab_shaping_internals_are_still_there():
    """Kontrakt z wnętrzem ReportLaba, na którym stoi ``typesetting`` (pin ``uharfbuzz`` w pyproject).

    ``shapeStr``/``ShapedStr``/``ttfonts.uharfbuzz`` nie są publicznym API ReportLaba – gdy aktualizacja
    je zmieni, ten test ma paść pierwszy, zanim zrobi to dyplom w dniu gali.
    """
    import inspect

    from reportlab.pdfbase import ttfonts

    assert hasattr(ttfonts, "uharfbuzz")
    assert callable(ttfonts.shapeStr)
    assert issubclass(ttfonts.ShapedStr, str)
    assert "shapable" in inspect.signature(ttfonts.TTFont.__init__).parameters


@requires_shaping
def test_shaped_text_carries_advances_for_the_width():
    from reportlab.pdfbase.ttfonts import ShapedStr

    line = t.layout_line("स्वर्ण पदक", size=12)

    shaped = line.runs[0].text
    assert isinstance(shaped, ShapedStr)
    assert all(hasattr(item, "x_advance") for item in shaped.__shapeData__)
    assert line.width > 0


@requires_shaping
def test_every_interface_language_has_a_script_and_a_working_pipeline():
    from django.conf import settings

    for code, _label in settings.LANGUAGES:
        support = t.language_support(code)
        assert support.ok, (code, support.reason)


def test_bidi_levels_of_an_arabic_sentence_with_a_year():
    text = "الأولمبياد 2026"
    levels = t.bidi_levels(text, rtl=t.base_is_rtl(text))

    assert levels[0] == 1  # arabski – RTL
    assert levels[-1] == 2  # rok – osadzone LTR w RTL
    assert set(levels[-4:]) == {2}


def test_a_date_in_an_rtl_paragraph_stays_one_number():
    """Reguła W4: kropka między cyframi należy do liczby – „04.10.2026”, a nie „2026.10.04”."""
    text = "تاريخ الإصدار: 04.10.2026"
    levels = t.bidi_levels(text, rtl=True)

    assert set(levels[-10:]) == {2}


def test_lrm_closes_a_url_and_is_not_drawn():
    text = "التحقق: https://iqo.example/dyplomy/ABC/‎"
    levels = t.bidi_levels(text, rtl=True)
    line = t.layout_line(text, size=9)

    assert levels[-2] == 2  # końcowy „/” zostaje przy adresie
    assert line.missing == ()  # LRM nie staje się pytajnikiem


def test_latin_name_in_an_rtl_paragraph_keeps_its_bracket_with_the_country():
    text = "الطالب: Jan Kowalski (Polska)"
    levels = t.bidi_levels(text, rtl=True)

    # Nawias zamykający należy do łacińskiego „(Polska)”, a nie do kierunku akapitu (reguła N0).
    assert levels[-1] == levels[text.index("P")] == 2


@pytest.mark.parametrize(
    ("text", "digits"),
    [("مدرسة ١٢٥", "١٢٥"), ("Gold Medal ٢٠٢٦", "٢٠٢٦"), ("المدرسة ۱۲۵", "۱۲۵")],
)
def test_arabic_indic_and_persian_digits_keep_their_order(text, digits):
    """Cyfry arabsko-indyjskie i perskie są pismem arabskim, ale czyta się je od lewej.

    HarfBuzz zgadłby dla nich kierunek RTL i odwrócił liczbę („٥٢١”) – przebieg parzysty (LTR) ze
    znakami arabskimi idzie więc bez kształtowania, w kolejności logicznej, krojem arabskim.
    """
    line = t.layout_line(text, size=12)

    run = next(run for run in line.runs if any(char in str(run.text) for char in digits))
    assert str(run.text).strip() == digits
    assert run.font.startswith("MedNotoSansArabic")


def test_runs_are_reordered_visually_in_an_rtl_line():
    line = t.layout_line("محمد Jan", size=12)

    # Akapit RTL: łaciński przebieg stoi wizualnie **po lewej**, czyli pierwszy.
    assert line.runs[0].font == "MedDejaVuSans"
    assert line.runs[-1].font == "MedNotoSansArabic"


@pytest.mark.parametrize(
    ("text", "font"),
    [
        ("Złoty medal – Łucja", "MedDejaVuSans"),
        ("Золотая медаль", "MedDejaVuSans"),
        ("الميدالية الذهبية", "MedNotoSansArabic"),
        ("स्वर्ण पदक", "MedNotoSansDevanagari"),
        ("স্বর্ণপদক", "MedNotoSansBengali"),
        ("金牌", "MedDroidSansFallback"),
    ],
)
def test_each_script_gets_its_own_font(text, font):
    line = t.layout_line(text, size=12)

    assert {run.font for run in line.runs} == {font}
    assert line.width > 0
    assert line.missing == ()


@requires_shaping
def test_complex_scripts_are_shaped():
    """Dewanagari z ligaturą spółgłoskową przechodzi przez HarfBuzz (``ShapedStr`` z pozycjami glifów)."""
    from reportlab.pdfbase.ttfonts import ShapedStr

    line = t.layout_line("स्वर्ण", size=12)

    assert isinstance(line.runs[0].text, ShapedStr)


def test_a_character_without_any_font_is_replaced_and_reported():
    line = t.layout_line("A\U0001f9ec", size=12)  # 🧬 – nie ma go w żadnym kroju z repozytorium

    assert line.missing == ("\U0001f9ec",)


def test_long_names_are_shrunk_to_fit():
    line = t.fitted_line("Bardzo " * 40, size=24, max_width=300)

    assert line.size < 24
    assert line.size >= 24 * 0.6


def test_cjk_bold_is_simulated_with_a_stroke():
    line = t.layout_line("金牌", size=12, bold=True)

    assert line.runs[0].fake_bold is True


def test_fonts_are_embedded_in_the_pdf():
    from reportlab.pdfgen import canvas as pdf_canvas

    buffer = BytesIO()
    canvas = pdf_canvas.Canvas(buffer)
    for y, text in enumerate(("Ąę", "عربي", "हिन्दी", "বাংলা", "中文")):
        t.draw_text(canvas, text, x=200, y=700 - 40 * y, size=14)
    canvas.save()

    reader = PdfReader(BytesIO(buffer.getvalue()))
    fonts = reader.pages[0]["/Resources"]["/Font"]
    embedded = set()
    for key in fonts:
        font = fonts[key].get_object()
        if font["/Subtype"] == "/Type1":
            continue  # domyślny Helvetica płótna – nieużyty, bez pliku
        if "/DescendantFonts" in font:
            font = font["/DescendantFonts"][0].get_object()
        # Każdy krój osadzony jako podzbiór TrueType (``FontFile2``) – czytelnik nie potrzebuje
        # zainstalowanych krojów, żeby zobaczyć arabski, dewanagari, bengalski i chiński.
        assert "/FontFile2" in font["/FontDescriptor"]
        embedded.add(str(font["/BaseFont"]).split("+")[-1])
    # ``BaseFont`` niesie nazwę PostScript z pliku kroju (np. ``NotoSansArabic-Regular``).
    for family in ("DejaVuSans", "NotoSansArabic", "NotoSansDevanagari", "NotoSansBengali", "DroidSans"):
        assert any(name.startswith(family) for name in embedded), (family, embedded)


def test_without_harfbuzz_complex_scripts_fall_back_to_english(monkeypatch):
    monkeypatch.setattr(t, "shaping_available", lambda: False)

    assert t.language_support("ar").ok is False
    assert "uharfbuzz" in t.language_support("ar").reason
    assert t.renderable_language("ar") == "en"
    assert t.renderable_language("hi") == "en"
    # Pisma bez kształtowania składają się dalej.
    assert t.renderable_language("zh-hans") == "zh-hans"
    assert t.renderable_language("ru") == "ru"


def test_a_missing_font_file_falls_back_to_english(monkeypatch, tmp_path):
    from dataclasses import replace

    missing = replace(
        t.FACES_BY_SCRIPT[t.BENGALI], regular="MedMissingBengali", regular_path=tmp_path / "x.ttf"
    )
    monkeypatch.setitem(t.FACES_BY_SCRIPT, t.BENGALI, missing)

    assert t.language_support("bn").ok is False
    assert t.renderable_language("bn") == "en"
