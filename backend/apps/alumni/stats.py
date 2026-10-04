"""„Gdzie są teraz” – zagregowane statystyki sieci absolwentów z progiem k-anonimowości (§ 7).

Reguły, wszystkie po to, żeby z tabeli nie dało się wskazać osoby:

- źródłem są **wyłącznie** profile ze zgodą (zgoda wymienia statystyki z nazwy),
- komórka mniejsza niż :data:`~apps.alumni.models.K_ANONYMITY` nie jest pokazywana osobno – trafia
  do „inne”; „inne” niezerowe, ale mniejsze od progu, pokazujemy jako „< 5”, a nie liczbą,
- **jeden wymiar na tabelę**: krzyżowanie (uczelnia × rocznik) rozbija grupy poniżej progu, nawet
  gdy każdy wymiar osobno go spełnia,
- sieć mniejsza niż próg nie ma rozkładów w ogóle – sama liczba „3 osoby, z czego 2 na UW” mówi
  o ludziach, a nie o sieci,
- puste pole („nie podano”) jest osobną kategorią i też podlega progowi.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from django.utils.translation import gettext as _

from .achievements import achievements_for
from .models import K_ANONYMITY, AlumniProfile, Interest, Level


@dataclass(frozen=True)
class Row:
    label: str
    count: int | None  # None = „< k”

    @property
    def shown(self) -> str:
        return str(self.count) if self.count is not None else f"< {K_ANONYMITY}"


@dataclass(frozen=True)
class Table:
    title: str
    rows: list[Row]


def _key(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def k_anonymous(counter: Counter, labels: dict[str, str] | None = None, *, k: int = K_ANONYMITY) -> list[Row]:
    """Rozkład z progiem: grupy ≥ k osobno (malejąco), reszta w „inne”.

    Przegląd krytyka (L6): samo „< 5” przy „inne” nie wystarcza, bo przy znanej sumie da się je
    wyliczyć z różnicy (komórka komplementarna). Dlatego do „inne” dokładamy **najmniejsze** pokazane
    grupy, dopóki „inne” nie osiągnie progu – wtedy żadna komórka tabeli nie jest mniejsza niż k,
    a różnica sum niczego nie odsłania. „< k” zostaje wyłącznie wtedy, gdy nie ma czego dołożyć.
    """
    labels = labels or {}
    shown = [(key, count) for key, count in counter.most_common() if count >= k]
    other = sum(count for count in counter.values() if count < k)
    while other and other < k and shown:
        _key_, count = shown.pop()
        other += count
    rows = [Row(labels.get(key) or key or _("nie podano"), count) for key, count in shown]
    if other:
        rows.append(Row(_("inne"), other if other >= k else None))
    return rows


def _rounded(total: int) -> int:
    """Liczebność sieci zaokrąglona do progu – sama dokładna suma bywa drugą połową różnicy (L6)."""
    return int(round(total / K_ANONYMITY) * K_ANONYMITY)


def where_are_they_now(competition) -> dict:
    """Komplet statystyk do ekranu koordynatora. Bez rozkładów, gdy sieć jest mniejsza niż próg."""
    from apps.accounts.countries import country_name

    profiles = list(
        AlumniProfile.objects.for_competition(competition)
        .filter(hidden_at__isnull=True)
        .select_related("participant")
        .order_by("pk")
    )
    total = len(profiles)
    mentors = sum(1 for profile in profiles if profile.mentor_available)
    summary = {
        # Dokładna liczba decyduje o progu, a na ekran idzie zaokrąglona (L6).
        "total": f"~{_rounded(total)}" if total >= K_ANONYMITY else total,
        "mentors": mentors if mentors >= K_ANONYMITY or mentors == 0 else None,
        "k": K_ANONYMITY,
    }
    if total < K_ANONYMITY:
        return {**summary, "tables": [], "too_small": True}

    universities: Counter = Counter()
    university_labels: dict[str, str] = {}
    fields: Counter = Counter()
    field_labels: dict[str, str] = {}
    countries: Counter = Counter()
    interests: Counter = Counter()
    for profile in profiles:
        key = _key(profile.university)
        universities[key] += 1
        university_labels.setdefault(key, " ".join(profile.university.split()))
        key = _key(profile.field_of_study)
        fields[key] += 1
        field_labels.setdefault(key, " ".join(profile.field_of_study.split()))
        countries[profile.country or ""] += 1
        for interest in set(profile.interests or []):
            if interest in Interest.values:
                interests[interest] += 1

    levels: Counter = Counter()
    found = achievements_for([profile.participant for profile in profiles])
    for profile in profiles:
        items = found.get(profile.participant_id, [])
        best = max(items, key=lambda item: item.rank, default=None)
        levels[best.level if best else ""] += 1

    university_labels[""] = _("nie podano")
    field_labels[""] = _("nie podano")
    country_labels = {code: country_name(code) for code in countries if code}
    country_labels[""] = _("nie podano")
    tables = [
        Table(_("Kraj"), k_anonymous(countries, country_labels)),
        Table(_("Uczelnia"), k_anonymous(universities, university_labels)),
        Table(_("Kierunek"), k_anonymous(fields, field_labels)),
        Table(
            _("Zainteresowania (osoba może mieć kilka)"),
            k_anonymous(interests, {key: str(label) for key, label in Interest.choices}),
        ),
        Table(
            _("Najwyższe osiągnięcie"),
            k_anonymous(levels, {key: str(label) for key, label in Level.choices}),
        ),
    ]
    return {**summary, "tables": tables, "too_small": False}
