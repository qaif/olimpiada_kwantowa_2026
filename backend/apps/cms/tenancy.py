"""Do którego konkursu należy ta strona, to żądanie i ta witryna.

Część informacyjna jest jedynym miejscem w serwisie, w którym „konkurs” ma **dwa** źródła i oba
bywają poprawne:

- **żądanie** – ``CompetitionMiddleware`` rozstrzygnęło je po hoście (albo po prefiksie ścieżki)
  i zapisało w ``request.competition``. Odczyt jest darmowy: to jest atrybut, nie zapytanie,
- **drzewo stron** – strona Wagtaila stoi w drzewie jakiejś ``wagtailcore.Site``, a ta witryna ma
  swój konkurs (``Competition.site``, relacja jeden do jednego).

W normalnym przebiegu oba wskazują to samo: ``wagtail.views.serve`` dopasowuje witrynę po hoście
(a pod prefiksem ścieżki – witrynę konkursu z prefiksu, którą podstawia warstwa konkursu, uwaga T43)
i dopiero w jej poddrzewie szuka adresu, więc strona **nie może** przyjść z witryny innej niż ta
z żądania. Różnią się dokładnie tam, gdzie strona jest renderowana poza swoim adresem: w podglądzie
z panelu redakcyjnego i w kodzie wołanym bez żądania (komenda, zadanie, test jednostkowy).

Dlatego ``competition_for_page`` pyta **najpierw żądanie**: to jest ta sama odpowiedź, a kosztuje
zero zapytań – co ma znaczenie, bo pytają o nią strona główna, strona zadań i strona wyników, czyli
najczęściej otwierane adresy serwisu. Dopiero brak żądania (albo żądanie bez rozstrzygniętego
konkursu) schodzi do drzewa stron.

**Wszystko tu jest odporne na ``None``** i to jest decyzja, a nie wygoda. Wołający – ``get_context``
strony, procesor kontekstu, znacznik szablonu – składa ramę serwisu; wyjątek w tym miejscu zamienia
brak konkursu (stan poprawny na świeżej instalacji i pod nieznanym hostem) w pięćsetkę na każdej
stronie naraz. ``None`` przekazany dalej do ``current_edition`` albo ``for_competition`` znaczy
„nie wiadomo, o który konkurs chodzi” i daje **pustą** odpowiedź, a nie cudzą.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

if TYPE_CHECKING:  # pragma: no cover - wyłącznie dla podpowiedzi typów
    from apps.tenancy.models import Competition

logger = logging.getLogger(__name__)


def competition_for_site(site) -> Competition | None:
    """Konkurs witryny albo ``None``.

    Witryna bez konkursu nie jest awarią: w bazie sprzed migracji ``tenancy.0002`` nie ma ani
    jednego konkursu, a operator platformy ma prawo założyć witrynę pomocniczą (staging, podgląd)
    zanim przypisze jej organizatora. Odwrotna relacja ``OneToOne`` podnosi w takim razie
    ``ObjectDoesNotExist`` – i to jest jedyny powód, dla którego ten odczyt nie jest jedną linijką.
    """
    if site is None:
        return None
    try:
        return site.competition
    except ObjectDoesNotExist:
        return None


def competition_for_request(request) -> Competition | None:
    """Konkurs żądania: ``request.competition``, a gdy warstwy nie było – konkurs kontekstu.

    ``getattr``, a nie ``request.competition``: szablon bywa renderowany z żądania złożonego
    ``RequestFactory`` (testy jednostkowe, podgląd strony błędu), a takie żądanie nie przeszło
    przez ``CompetitionMiddleware`` i atrybutu nie ma. Odwrót na zmienną kontekstową obsługuje
    drugą stronę tego samego problemu: kod wołany **po** odpowiedzi albo z zadania, gdzie żądania
    nie ma wcale, a konkurs ustawił wołający przez ``competition_context``.
    """
    competition = getattr(request, "competition", None)
    if competition is not None:
        return competition
    from apps.tenancy.context import current_competition

    return current_competition()


def competition_for_page(page, request=None) -> Competition | None:
    """Konkurs, którego stroną jest ``page``. Najpierw żądanie (za darmo), potem drzewo stron.

    Kolejność jest opisana w docstringu modułu: w przebiegu, który obsługuje czytelnika, oba
    źródła dają tę samą odpowiedź, a żądanie daje ją bez zapytania do bazy.

    **Podgląd z panelu jest wyjątkiem i dlatego jest sprawdzany jawnie.** Żądanie podglądu idzie
    pod domenę, na której redaktor ma otwarty ``/cms/``, a nie pod domenę strony, którą ogląda –
    więc operator platformy z dwoma konkursami zobaczyłby w podglądzie strony konkursu B
    harmonogram konkursu A. ``request.is_preview`` ustawia sam Wagtail, więc rozpoznanie nic nie
    kosztuje, a podgląd pokazuje to, co zobaczy czytelnik pod właściwą domeną.

    Zejście do drzewa kosztuje dwa zapytania (witryna strony i jej konkurs) i dotyczy wyłącznie
    renderowania poza zwykłym żądaniem – podglądu, komendy i testu jednostkowego. Błąd bazy
    kończy się ``None``, bo ta funkcja bywa wołana ze składania ramy serwisu (patrz moduł).
    """
    if request is not None and not getattr(request, "is_preview", False):
        competition = competition_for_request(request)
        if competition is not None:
            return competition
    if page is None:
        return None
    try:
        return competition_for_site(page.get_site())
    except DatabaseError, AttributeError:  # pragma: no cover - baza bez drzewa stron
        logger.warning("Nie udało się ustalić konkursu strony %s.", getattr(page, "pk", "?"))
        return None


def resolve_competition(competition=None) -> Competition | None:
    """Konkurs podany wprost, a bez wskazania – ten „na teraz”.

    Cienka przykrywka na ``apps.competitions.scoping.resolve_competition``, żeby moduły części
    informacyjnej miały **jedno** wejście do reguły „skąd bierze się konkurs, gdy wołający go nie
    podał”, a sama reguła została zapisana raz, w warstwie, która ją zdefiniowała. Kopia tej
    kolejności tutaj znaczyłaby, że komunikat na banerze i praca w panelu mogą w tym samym żądaniu
    należeć do dwóch różnych organizatorów.

    Import jest lokalny, bo ``apps.competitions.scoping`` ciągnie za sobą serwis kont, a ten moduł
    bywa importowany przy składaniu aplikacji.
    """
    from apps.competitions.scoping import resolve_competition as _resolve

    return _resolve(competition)
