"""Pole formularza „punkty”: liczba z przecinkiem albo kropką, najwyżej dwa miejsca po przecinku.

Jedno pole dla każdego formularza, w którym człowiek wpisuje punkty – ocenę recenzji, korektę
koordynatora, nową punktację z reklamacji, punkty z rozmowy, próg kwalifikacji, maksimum zadania.
``forms.IntegerField`` (do wydania 0.35.0) nie przyjąłby 4,25, a ``forms.DecimalField`` bez
lokalizacji odrzuca przecinek – czyli to, co polski użytkownik wpisze najpierw. Parsowanie idzie
przez ``apps.core.points.parse_points``, ten sam czytnik, którego używa serwis, więc formularz
i API nie mogą różnie rozumieć tej samej liczby.

Pole sprawdza **kształt** (liczba, dwa miejsca, granice techniczne), a nie skalę: czy 4,25 jest
dopuszczalną oceną w tym etapie, rozstrzyga ``apps.competitions.scoring.ScoreRule`` w serwisie.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms

from apps.core.points import PointsError, format_points, parse_points

#: Górna granica techniczna – ta sama, którą trzymały pola ``IntegerField(max_value=1000)``.
#: Chroni kolumnę przed przepełnieniem, a nie wyraża skali.
MAX_POINTS_INPUT = Decimal(1000)
#: Górna granica progu punktowego (suma etapu) – mieści się w kolumnie ``TOTAL_MAX_DIGITS``.
THRESHOLD_MAX = Decimal("99999999")


class PointsInput(forms.TextInput):
    """``<input inputmode="decimal">`` – klawiatura numeryczna z przecinkiem na telefonie.

    Typ ``text``, a nie ``number``: przeglądarka z polskim ustawieniem regionalnym bywa w polu
    ``number`` bezradna wobec przecinka (Firefox odrzuca „4,25” jeszcze przed wysłaniem), a to jest
    pole, w które recenzent wpisuje przecinek najpierw. Walidację robi serwer.
    """

    def __init__(self, attrs=None):
        base = {"inputmode": "decimal", "autocomplete": "off"}
        super().__init__({**base, **(attrs or {})})

    def format_value(self, value):
        # Pole tekstowe, więc liczba wraca do niego tak, jak ją pokazujemy – z przecinkiem po
        # polsku („12,5”). Parser przyjmie ją z powrotem bez zmian (``parse_points``).
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value
        return format_points(value)


def score_form_error(form, fallback: str, field: str = "score") -> str:
    """Komunikat odmowy formularza oceny: błąd pola punktów, gdy jest, inaczej ``fallback``.

    Błąd kształtu („najwyżej dwa miejsca po przecinku”, „to nie jest liczba”) ma dojść do człowieka
    dosłownie – ogólne „podaj punkty ze skali” przy wpisanym „4,255” nie mówi, co jest nie tak.
    Puste wymagane pole daje ``fallback``, bo „To pole jest wymagane.” bez nazwy pola nie mówi nic.
    """
    errors = [
        str(message)
        for error in form.errors.as_data().get(field, [])
        if error.code != "required"
        for message in error.messages
    ]
    return " ".join(errors) if errors else fallback


class PointsField(forms.Field):
    """Liczba punktów z formularza → ``Decimal`` (albo ``None`` przy pustym polu niewymaganym)."""

    widget = PointsInput
    default_error_messages = {
        "min_value": "Punkty nie mogą być mniejsze niż %(limit)s.",
        "max_value": "Punkty nie mogą być większe niż %(limit)s.",
    }

    def __init__(self, *, min_value=Decimal(0), max_value=MAX_POINTS_INPUT, **kwargs):
        self.min_value = None if min_value is None else Decimal(min_value)
        self.max_value = None if max_value is None else Decimal(max_value)
        super().__init__(**kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return parse_points(value)
        except PointsError as exc:
            raise forms.ValidationError(str(exc), code="invalid") from exc

    def validate(self, value):
        super().validate(value)
        if value is None:
            return
        if self.min_value is not None and value < self.min_value:
            raise forms.ValidationError(
                self.error_messages["min_value"], code="min_value", params={"limit": self.min_value}
            )
        if self.max_value is not None and value > self.max_value:
            raise forms.ValidationError(
                self.error_messages["max_value"], code="max_value", params={"limit": self.max_value}
            )
