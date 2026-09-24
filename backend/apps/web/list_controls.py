"""Sterowanie listą osób w panelu koordynatora: sortowanie kolumn i przełącznik kont usuniętych.

Po co wspólny moduł. Zgłoszenie organizatora z 24.09.2026 miało dwie połowy, obie o tych samych
ekranach: „koordynator widzi skasowanych użytkowników jako ‚deleted’” i „powinien móc sortować
uczestników po różnych polach”. Obie są stanem **listy** zapisanym w adresie (``?sort=…``,
``?usuniete=1``) i obie muszą przeżyć to samo: przejście na kolejną stronę, wyszukiwanie, filtr
roli. Gdyby każdy widok składał te odnośniki sam, pierwsza lista, która zgubi ``sort`` przy
stronicowaniu, pokazałaby na stronie 2 wiersze z zupełnie innego porządku niż na stronie 1.

Dwie zasady bezpieczeństwa i wydajności:

- **żaden napis z adresu nie idzie do ``order_by``**. Adres niesie wyłącznie **klucz** kolumny
  (``nazwisko``, ``email``…), który jest sprawdzany na liście dopuszczonych (:class:`SortKey`);
  pola ORM stoją w kodzie widoku. Nieznany klucz daje porządek domyślny – bez błędu, bez 500,
  bo adres z zakładki sprzed zmiany kolumn nie jest powodem, żeby strona przestała działać,
- **porządek jest stabilny**: po polach kolumny zawsze idzie ``pk`` w tym samym kierunku.
  Bez tego dwa wiersze z tym samym nazwiskiem mogłyby zamienić się miejscami między stroną 1
  i 2 (Postgres nie gwarantuje kolejności remisów), a jeden uczestnik trafiłby na obie strony,
  drugi na żadną. Puste wartości lądują zawsze na końcu (``nulls_last``) – w obu kierunkach
  koordynator szuka wierszy z danymi, a nie pustych klas.

Przełącznik „Pokaż usunięte konta” jest **domyślnie wyłączony**: konta po anonimizacji
(``apps.accounts.anonymised``) są odsiewane z listy, a obok przełącznika stoi ich liczba – jeden
``COUNT`` na tym samym, już przefiltrowanym querysecie, więc liczba mówi o **tej** liście
(„w tym wyszukiwaniu schowaliśmy 3”), a nie o całej bazie.

Moduł nie wie nic o konkretnych ekranach: kolumny i ścieżkę do konta podaje widok. Prezentację
(nagłówek ze strzałką, przycisk przełącznika) dają znaczniki ``apps.web.templatetags.list_controls``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from django.db.models import F, QuerySet

from apps.accounts.anonymised import anonymised_q

#: Nazwa parametru sortowania. Wartość to klucz kolumny, z ``-`` na przedzie dla porządku
#: malejącego (``?sort=-data``) – ten sam zapis, co w ``order_by``, więc czyta się go bez słownika.
SORT_PARAM = "sort"

#: Parametr przełącznika kont usuniętych. Polskie słowo, bo adres widzi i kopiuje organizator.
DELETED_PARAM = "usuniete"

#: Parametr stronicowania – każda zmiana porządku albo przełącznika wraca na stronę pierwszą:
#: „strona 7” innego porządku to przypadkowe pięćdziesiąt wierszy.
PAGE_PARAM = "page"


@dataclass(frozen=True)
class SortKey:
    """Jedna sortowalna kolumna: klucz w adresie, podpis i pola ORM, po których idzie porządek.

    ``fields`` to nazwy pól albo adnotacji w **kierunku rosnącym**; kierunek dokłada
    :meth:`ListControls.order`. Więcej niż jedno pole, gdy sama kolumna ma naturalny remis
    (nazwisko → imię). ``descending_first`` – dla kolumn, które czyta się „od najnowszego” albo
    „od największego” (data rejestracji, liczba prac): pierwsze kliknięcie sortuje wtedy malejąco.
    """

    key: str
    label: str
    fields: tuple[str, ...]
    descending_first: bool = False


class ListControls:
    """Stan listy odczytany z adresu: kolumna sortowania, kierunek, przełącznik kont usuniętych.

    Tworzy go widok, zanim złoży queryset::

        controls = ListControls(request, SORT_KEYS, default="nazwisko")
        rows = controls.filter_deleted(rows, "user")
        rows = controls.order(rows)

    a szablon dostaje obiekt w kontekście i woła znaczniki ``{% sort_header %}``,
    ``{% deleted_toggle %}`` oraz ``controls.page_query`` w odnośnikach stronicowania.

    ``default=None`` znaczy „widok ma własny porządek domyślny i nie jest on żadną z kolumn” –
    wtedy :meth:`order` bez ``?sort=`` oddaje queryset z porządkiem przekazanym w ``fallback``.
    """

    def __init__(
        self,
        request,
        sort_keys: Sequence[SortKey],
        *,
        default: str | None = None,
        default_descending: bool = False,
        deleted_toggle: bool = True,
    ):
        self._params = request.GET.copy()
        self.keys = {sort_key.key: sort_key for sort_key in sort_keys}
        self.columns = tuple(sort_keys)
        self.default = default if default in self.keys else None
        self.default_descending = default_descending
        raw = (self._params.get(SORT_PARAM) or "").strip()
        requested = raw.removeprefix("-")
        if requested in self.keys:
            self.sort = requested
            self.descending = raw.startswith("-")
            self.explicit = True
        else:
            # Klucz spoza listy (literówka, stara zakładka, próba wstrzyknięcia pola) = porządek
            # domyślny. Parametr wypada też z adresów budowanych dalej, żeby nie wędrował po
            # stronicowaniu jako śmieć.
            self.sort = self.default
            self.descending = default_descending if self.default else False
            self.explicit = False
            self._params.pop(SORT_PARAM, None)
        self.deleted_toggle = deleted_toggle
        self.show_deleted = deleted_toggle and self._params.get(DELETED_PARAM) == "1"
        if not self.show_deleted:
            self._params.pop(DELETED_PARAM, None)
        #: Ile kont usuniętych schowała lista. ``None`` – nie liczyliśmy (przełącznik włączony
        #: albo lista przełącznika nie ma); szablon pokazuje wtedy sam przycisk bez liczby.
        self.hidden_deleted: int | None = None

    # --- queryset -------------------------------------------------------------------------------

    def filter_deleted(self, queryset: QuerySet, user_path: str = "", *, count: bool = True) -> QuerySet:
        """Odsiewa konta po anonimizacji, chyba że koordynator włączył „Pokaż usunięte konta”.

        ``user_path`` to ścieżka od modelu listy do ``User`` (``""``, ``"user"``,
        ``"participant__user"``). Wołać **po** wyszukiwaniu i filtrach, a **przed** sortowaniem
        i stronicowaniem – wtedy licznik ukrytych mówi o tej samej liście, którą widać.
        """
        if not self.deleted_toggle or self.show_deleted:
            return queryset
        condition = anonymised_q(user_path)
        if count:
            self.hidden_deleted = queryset.filter(condition).count()
        return queryset.exclude(condition)

    def order(self, queryset: QuerySet, fallback: Iterable[str] = ()) -> QuerySet:
        """Porządek listy: kolumna z adresu (albo domyślna) i ``pk`` w tym samym kierunku.

        Bez kolumny (``default=None`` i brak ``?sort=``) – porządek ``fallback``, czyli dokładnie
        ten, który widok miał przed wprowadzeniem sortowania.
        """
        if self.sort is None:
            return queryset.order_by(*fallback) if fallback else queryset
        expressions = []
        for field in self.keys[self.sort].fields:
            expression = F(field)
            expressions.append(
                expression.desc(nulls_last=True) if self.descending else expression.asc(nulls_last=True)
            )
        expressions.append("-pk" if self.descending else "pk")
        return queryset.order_by(*expressions)

    # --- adresy ---------------------------------------------------------------------------------

    @property
    def sort_value(self) -> str:
        """Bieżący porządek w zapisie adresu (``nazwisko``, ``-data``) – do ukrytego pola formularza.

        Oddaje wyłącznie klucz z listy dopuszczonych, nigdy surowy parametr żądania.
        """
        if self.sort is None:
            return ""
        return f"-{self.sort}" if self.descending else self.sort

    def _query(self, **changes) -> str:
        params = self._params.copy()
        params.pop(PAGE_PARAM, None)
        for name, value in changes.items():
            if value is None:
                params.pop(name, None)
            else:
                params[name] = value
        return params.urlencode()

    @property
    def page_query(self) -> str:
        """Stan listy bez numeru strony – do odnośników „Poprzednia”/„Następna” (bez ``?``).

        Niesie **wszystko**: frazę, filtry widoku, sortowanie i przełącznik – bo wszystko to jest
        w ``request.GET``, a numer strony jako jedyny ma się zmieniać.
        """
        return self._query()

    def sort_url(self, key: str) -> str:
        """Adres po kliknięciu nagłówka kolumny ``key``: ta sama kolumna – odwrócony kierunek."""
        sort_key = self.keys[key]
        if key == self.sort:
            descending = not self.descending
        else:
            descending = sort_key.descending_first
        return "?" + self._query(**{SORT_PARAM: f"-{key}" if descending else key})

    def header(self, key: str) -> dict:
        """Dane jednego nagłówka dla szablonu: podpis, adres, ``aria-sort`` i kierunek strzałki."""
        sort_key = self.keys[key]
        active = key == self.sort
        if active:
            aria_sort = "descending" if self.descending else "ascending"
        else:
            aria_sort = ""
        return {
            "key": key,
            "label": sort_key.label,
            "url": self.sort_url(key),
            "active": active,
            "aria_sort": aria_sort,
            "descending": active and self.descending,
            # Co zrobi kliknięcie – do nazwy dostępnej odnośnika, bo sama strzałka jest ozdobą.
            "next_descending": (not self.descending) if active else sort_key.descending_first,
        }

    @property
    def deleted_url(self) -> str:
        """Adres po kliknięciu przełącznika: odwraca ``?usuniete=1`` i zachowuje resztę stanu."""
        return "?" + self._query(**{DELETED_PARAM: None if self.show_deleted else "1"})

    @property
    def export_query(self) -> str:
        """Stan listy do odnośnika eksportu: filtry i przełącznik, bez sortowania i strony.

        Eksport ma iść za tym, co koordynator widzi (te same konta), ale porządek pliku jest
        porządkiem pliku – arkusz sortuje się sam, a stały porządek wiersza ułatwia porównanie
        dwóch eksportów.
        """
        params = self._params.copy()
        params.pop(PAGE_PARAM, None)
        params.pop(SORT_PARAM, None)
        return params.urlencode()

    def query_without(self, *names: str) -> str:
        """Stan listy bez strony i bez podanych parametrów – pod odnośniki filtrów widoku.

        Filtr roli czy etapu podmienia **swój** parametr, a resztę stanu (frazę, sortowanie,
        przełącznik) ma zostawić: szablon dokleja do wyniku ``&role=…``.
        """
        return self._query(**dict.fromkeys(names))
