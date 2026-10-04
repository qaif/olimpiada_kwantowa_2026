"""Wykres liniowy liczony po stronie serwera i rysowany szablonem jako ``<svg>`` – bez JavaScriptu.

Dlaczego nie biblioteka wykresów w przeglądarce: polityka bezpieczeństwa serwisu nie wpuszcza
skryptów wpisanych w dokument, a panel opiekuna to kilka punktów na krzyż (jedna liczba na edycję
na serię). Geometria tych kilku punktów to kilkanaście linijek arytmetyki – liczymy ją tutaj,
a szablon wypisuje gotowe współrzędne. Kolory stoją w arkuszu (klasy serii), nie w atrybutach:
ciemny motyw i tryb wysokiego kontrastu działają wtedy bez dodatkowej reguły.

Wykres jest **ilustracją**, nie jedynym nośnikiem liczb: te same wartości stoją w tabeli obok,
a ``<svg>`` ma ``role="img"`` z opisem – czytnik ekranu czyta tabelę, a nie łamaną.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

WIDTH = 640
HEIGHT = 260
PAD_LEFT = 48
PAD_RIGHT = 16
PAD_TOP = 16
PAD_BOTTOM = 40
TICKS = 4


def _nice_max(value: Decimal) -> Decimal:
    """Górna granica osi: najbliższa „okrągła” liczba nad maksimum (1, 2, 2,5, 5 × 10ⁿ)."""
    if value <= 0:
        return Decimal(1)
    exponent = Decimal(10) ** (len(str(int(value))) - 1)
    for step in (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10)):
        candidate = (value / (exponent * step)).to_integral_value(rounding=ROUND_CEILING) * exponent * step
        if candidate / (exponent * step) <= TICKS:
            return candidate
    return (value / exponent).to_integral_value(rounding=ROUND_CEILING) * exponent


def line_chart(labels: list[str], series: list[dict]) -> dict | None:
    """Geometria wykresu: osie, podpisy i łamane serii. ``None``, gdy nie ma ani jednej wartości.

    ``series`` to lista ``{"key", "label", "values"}``; ``values`` ma tyle pozycji, ile ``labels``,
    a ``None`` oznacza brak punktu (przerwa w łamanej, a nie zejście do zera).
    """
    numbers = [value for item in series for value in item["values"] if value is not None]
    if not labels or not numbers:
        return None
    top = _nice_max(max(Decimal(value) for value in numbers))
    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM
    count = len(labels)

    def x_of(index: int) -> float:
        if count == 1:
            return PAD_LEFT + plot_width / 2
        return PAD_LEFT + plot_width * index / (count - 1)

    def y_of(value) -> float:
        return PAD_TOP + plot_height * (1 - float(Decimal(value) / top))

    drawn = []
    for item in series:
        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        points = []
        for index, value in enumerate(item["values"]):
            if value is None:
                if current:
                    segments.append(current)
                current = []
                continue
            point = (round(x_of(index), 1), round(y_of(value), 1))
            current.append(point)
            points.append({"x": point[0], "y": point[1], "value": value, "label": labels[index]})
        if current:
            segments.append(current)
        if not points:
            continue
        drawn.append(
            {
                "key": item["key"],
                "label": item["label"],
                "polylines": [
                    " ".join(f"{x},{y}" for x, y in segment) for segment in segments if len(segment) > 1
                ],
                "points": points,
            }
        )
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "plot": {"left": PAD_LEFT, "right": WIDTH - PAD_RIGHT, "top": PAD_TOP, "bottom": HEIGHT - PAD_BOTTOM},
        "x_labels": [
            {"x": round(x_of(index), 1), "y": HEIGHT - PAD_BOTTOM + 18, "label": label}
            for index, label in enumerate(labels)
        ],
        "y_ticks": [
            {"y": round(y_of(top * step / TICKS), 1), "value": top * step / TICKS}
            for step in range(TICKS + 1)
        ],
        "series": drawn,
    }
