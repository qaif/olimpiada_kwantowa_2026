"""Zapowiedź najbliższych warsztatów online – wspólna reguła dla strony ``/warsztaty/`` i głównej.

Harmonogram warsztatów jest **treścią redakcyjną**: system zawodów o warsztatach nic nie wie,
bo nie egzekwuje ich terminów. Mieszka więc tam, gdzie go wpisano – w bloku ``schedule`` w treści
strony „Warsztaty” – i to jest jedyne jego źródło. Strona główna nie ma własnej listy warsztatów
do utrzymania: czyta tę samą tabelę i pokazuje z niej trzy najbliższe wiersze.

Dlaczego osobny moduł, a nie metoda ``HomePage``: ta reguła (co znaczy „najbliższe”) jest
odpowiedzią na pytanie o treść strony „Warsztaty”, a nie o układ strony głównej, i musi dać się
przetestować bez budowania drzewa stron. Moduł świadomie **nie importuje** ``apps.cms.models``
– dostaje stronę z zewnątrz – bo ``models`` importuje ten moduł, a w drugą stronę byłby cykl.

Terminarz etapów zawodów to osobna sprawa i osobny moduł (``apps.cms.timeline``): tam daty
pochodzą z bazy i strona nie ma prawa ogłaszać innych niż serwer.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

#: Slug strony z harmonogramem warsztatów. Zapowiedź na stronie głównej szuka jej po slugu, bo
#: tytuł jest redakcyjny (może brzmieć „Warsztaty online”), a adres wisi w komunikatach.
WORKSHOPS_SLUG = "warsztaty"

#: Ile warsztatów pokazuje zapowiedź na stronie głównej. Trzy, bo tyle mieści się w jednym rzędzie
#: kart obok siebie, a zapowiedź ma odpowiadać na „co najbliżej”, nie zastępować całej tabeli.
UPCOMING_LIMIT = 3


def upcoming_workshops(page, *, now=None, limit: int = UPCOMING_LIMIT) -> list[dict]:
    """Najbliższe warsztaty z bloków ``schedule`` w treści strony, od najwcześniejszego.

    ``page`` bywa ``None`` (strony „Warsztaty” jeszcze nie ma albo jest szkicem) – wynikiem jest
    wtedy pusta lista, a nie wyjątek: sekcja na stronie głównej ma po prostu zniknąć.

    Wiersz bez odczytanej daty (``date_value``) zostaje pominięty. To nie jest awaria – termin
    w harmonogramie redakcyjnym bywa nieostry, a zapowiedź może pokazać tylko to, co umie
    uszeregować względem zegara. W tabeli na stronie „Warsztaty” taki wiersz stoi normalnie.

    Granicą jest **dzień**, a nie godzina: w tabeli stoi data i (tekstem) godziny, więc warsztat
    dzisiejszy jest jeszcze „najbliższy” – godziny bywają zresztą podane jako „do potwierdzenia”
    i nie ma czego porównywać.
    """
    if page is None:
        return []
    today = _today(now)
    rows = [
        {
            "topic": row.get("topic", ""),
            "date": row.get("date", ""),
            "date_value": row["date_value"],
            "time": row.get("time", ""),
        }
        for block in page.body
        if block.block_type == "schedule"
        for row in block.value.get("rows", [])
        if isinstance(row.get("date_value"), date) and row["date_value"] >= today
    ]
    rows.sort(key=lambda row: row["date_value"])
    return rows[:limit]


def workshop_rows(page) -> list[dict]:
    """**Wszystkie** warsztaty z odczytaną datą, od najwcześniejszego – dla paska w nagłówku.

    Różnica wobec ``upcoming_workshops`` jest jedna i wynika z pytania, na które odpowiada każda
    z funkcji. Zapowiedź na stronie głównej pyta „co dalej”, więc odcina przeszłość i kończy na
    trzech wierszach. Linia czasu w nagłówku pyta „jak wygląda cała edycja” – minione warsztaty
    są w niej tak samo potrzebne, jak minione etapy, bo to one pokazują, ile drogi już za nami.

    Daty nie parsujemy tutaj: robi to import treści (``legacy_markdown.parse_polish_date``) i to
    on jest miejscem, w którym polszczyzna zamienia się w ``date``. Wiersz z terminem nieostrym
    („do potwierdzenia”) nie ma ``date_value`` i po prostu nie trafia na oś – w tabeli na stronie
    „Warsztaty” stoi normalnie, bo tam jest tekstem, a nie punktem na osi.
    """
    if page is None:
        return []
    rows = [
        {"topic": row.get("topic", ""), "date_value": row["date_value"]}
        for block in page.body
        if block.block_type == "schedule"
        for row in block.value.get("rows", [])
        if isinstance(row.get("date_value"), date)
    ]
    rows.sort(key=lambda row: row["date_value"])
    return rows


def _today(now=None) -> date:
    """Dzisiejszy dzień w strefie serwisu. ``now`` (aware albo naive) dla testów bez freezegunu."""
    if now is None:
        return timezone.localdate()
    if timezone.is_aware(now):
        return timezone.localtime(now).date()
    return now.date()
