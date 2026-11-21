"""Ustawienia mapy uczestników konkursu (MAP-01).

Sam przełącznik „strona istnieje” jest flagą konkursu (``Competition.feature_flags``,
``participant_map``) – tą samą drogą, co każda inna funkcja włączana per konkurs, więc admin,
testy flag i ``has_feature`` wiedzą o niej bez wyjątków. Tu leży wyłącznie to, czego flaga nie
uniesie: **próg ochrony** (ilu uczestników musi mieć miejscowość, żeby pokazać ją z nazwą).

Brak wiersza znaczy „ustawienia domyślne” – konkurs, którego koordynator nigdy nie otworzył ekranu
mapy, nie ma tu nic i nie musi mieć.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

#: Najniższy dopuszczalny próg ochrony. Domyślna wartość jest zarazem dolną granicą: koordynator
#: może ochronę **zaostrzyć**, ale nie osłabić – miejscowość z jednym czy dwoma uczniami na
#: publicznej stronie wskazywałaby konkretne dzieci w małej szkole.
MIN_TOWN_COUNT = 3
#: Górna granica – sito na literówki („30” zamiast „3”), nie reguła ochrony.
MAX_TOWN_COUNT = 50


class MapSettings(models.Model):
    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="participant_map_settings",
        verbose_name="konkurs",
    )
    min_town_count = models.PositiveSmallIntegerField(
        "próg ochrony (najmniej uczestników w miejscowości)",
        default=MIN_TOWN_COUNT,
        validators=[MinValueValidator(MIN_TOWN_COUNT), MaxValueValidator(MAX_TOWN_COUNT)],
    )
    updated_at = models.DateTimeField("zmienione", auto_now=True)

    class Meta:
        verbose_name = "ustawienia mapy uczestników"
        verbose_name_plural = "ustawienia mapy uczestników"

    def __str__(self) -> str:
        return f"mapa uczestników {self.competition_id}: próg {self.min_town_count}"
