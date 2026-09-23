"""Karty strony ``/plakaty/`` z listy plików – jedna karta na grupę, osobna na plik bez grupy.

Prośba organizatora z 23.09.2026: ten sam plakat w kilku plikach (JPG, PDF, PDF ze spadem) ma stać
na **jednej** karcie z jednym podglądem i przyciskiem na każdy plik, a nie jako trzy prawie
identyczne karty obok siebie. Grupą jest napis ``PromoMaterial.group`` – pliki z identyczną,
niepustą grupą tworzą jedną kartę.

Karty składa Python z listy, którą widok już pobrał (``public_materials``, jedno zapytanie
w kolejności koordynatora) – bez drugiego zapytania i bez ``GROUP BY`` w bazie. Kolejność:

- karta stoi tam, gdzie **pierwszy** jej plik na liście koordynatora (strzałki ↑ ↓ na ekranie
  koordynatora przesuwają więc kartę, przesuwając jej pierwszy plik),
- przyciski w karcie idą w kolejności plików na tej samej liście.

Każdy przycisk prowadzi do **własnego** adresu pobrania pliku (``web:poster-download``), więc
statystyki zostają per plik – karta jest wyłącznie sposobem wyświetlenia.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import PromoMaterial


@dataclass
class PosterCard:
    """Jedna karta: nagłówek, podgląd, opis i pliki (przyciski) w kolejności koordynatora."""

    #: Grupa (nagłówek karty) albo pusty napis dla karty pojedynczego pliku.
    group: str
    materials: list[PromoMaterial] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return bool(self.group)

    @property
    def first(self) -> PromoMaterial:
        return self.materials[0]

    @property
    def heading(self) -> str:
        return self.group or self.first.title

    @property
    def preview_material(self) -> PromoMaterial | None:
        """Pierwszy plik karty, który ma podgląd – plik PDF bez miniatury nie zabiera karcie obrazka."""
        return next((material for material in self.materials if material.preview), None)

    @property
    def description(self) -> str:
        """Opis pierwszego pliku, który go ma – żeby opis nie ginął, gdy pierwszy plik go nie ma."""
        return next((material.description for material in self.materials if material.description), "")


def build_cards(materials) -> list[PosterCard]:
    """Karty z listy plików (już w kolejności koordynatora). Bez zapytań do bazy.

    Lista wejściowa pochodzi z ``public_materials(competition)``, więc grupy nie mieszają się
    między konkursami: ten sam napis grupy w dwóch konkursach to dwie niezależne karty na dwóch
    różnych stronach.
    """
    cards: list[PosterCard] = []
    by_group: dict[str, PosterCard] = {}
    for material in materials:
        if not material.group:
            cards.append(PosterCard(group="", materials=[material]))
            continue
        card = by_group.get(material.group)
        if card is None:
            card = by_group[material.group] = PosterCard(group=material.group)
            cards.append(card)
        card.materials.append(material)
    return cards
