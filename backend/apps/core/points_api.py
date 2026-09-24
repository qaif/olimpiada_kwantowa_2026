"""Pole API „punkty” – liczba JSON na wyjściu, liczba albo tekst z przecinkiem na wejściu.

Kontrakt (``docs/API.md``, wydanie 0.35.0): każde pole punktów w odpowiedzi jest **liczbą JSON** –
całkowitą, gdy ocena jest całkowita (``5``), i z częścią ułamkową o najwyżej dwóch cyfrach, gdy nie
jest (``4.25``). Nie tekstem, jak robi to domyślnie ``serializers.DecimalField`` (``"5.00"``): pole,
które do wydania 0.35.0 było ``IntegerField``, nie może klientom API zmienić typu z liczby na napis.
Klient, który czyta ``score`` jako liczbę, dalej dostaje liczbę; klient, który zakładał liczbę
całkowitą, dostaje ją dla każdego etapu „tylko ze skali” – czyli dla każdego, którego organizator
nie przełączył.

Na wejściu pole przyjmuje liczbę JSON (``4.25``) albo tekst (``"4.25"``, ``"4,25"``) – normalizację
robi ``apps.core.points.parse_points``, ten sam czytnik, którego używa formularz panelu. Czy liczba
jest dopuszczalną oceną, rozstrzyga serwis (``competitions.scoring.ScoreRule``), a nie pole.
"""

from __future__ import annotations

from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .points import PointsError, parse_points, points_json

#: Opis pola w schemacie OpenAPI (``/api/schema/``): liczba o kroku 0,01, a nie domyślny dla pola
#: bez typu „dowolny obiekt”. Tekst z przecinkiem na wejściu jest wygodą, nie kontraktem schematu.
POINTS_SCHEMA = {"type": "number", "multipleOf": 0.01, "example": 4.25}


@extend_schema_field(POINTS_SCHEMA)
class PointsField(serializers.Field):
    """Punkty w API: ``int``/``float`` (≤ 2 miejsca) na wyjściu, ``Decimal`` po walidacji wejścia."""

    default_error_messages = {
        "min_value": "Punkty nie mogą być mniejsze niż {limit}.",
        "max_value": "Punkty nie mogą być większe niż {limit}.",
    }

    def __init__(self, *, min_value=None, max_value=None, **kwargs):
        self.min_value = None if min_value is None else Decimal(min_value)
        self.max_value = None if max_value is None else Decimal(max_value)
        super().__init__(**kwargs)

    def to_representation(self, value):
        return points_json(value)

    def to_internal_value(self, data):
        try:
            value = parse_points(data)
        except PointsError as exc:
            raise serializers.ValidationError(str(exc), code="invalid") from exc
        if self.min_value is not None and value < self.min_value:
            self.fail("min_value", limit=self.min_value)
        if self.max_value is not None and value > self.max_value:
            self.fail("max_value", limit=self.max_value)
        return value


class PointsDictField(serializers.DictField):
    """``{numer zadania: punkty}`` – słownik punktów ze snapshotu albo wiersza wyników."""

    child = PointsField()
