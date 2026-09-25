"""Budżety zapytań ekranów – w jednej tabeli – i czytelny raport, gdy któryś pęknie.

Dwie rzeczy, obie z tego samego powodu: próg zapytań łapie prawdziwe regresje (zapytanie w pętli
po wierszach, relację bez ``select_related``), ale do 25.09.2026 był rozrzucony po pięciu plikach
testów, a jego przekroczenie mówiło tylko „Expected to perform 50 queries or less but 53 were
done (add -v option to show queries)”. W CI (``-q``) zapytań nie było więc widać wcale, a autor
zmiany w stopce serwisu (``templates/base.html``, każda strona) szukał po repozytorium, które
liczby podnieść.

- :data:`QUERY_BUDGETS` – **jedyne** miejsce z sufitami ekranów. Zmiana wspólna dla wszystkich stron
  (menu, stopka, pasek konta) podnosi tu kilka wierszy obok siebie, z jednym komentarzem.
- :func:`explain_queries` – raport dołączany do **każdego** niepowodzenia ``django_assert_num_queries``
  i ``django_assert_max_num_queries`` w suicie (``backend/conftest.py`` nadpisuje obie fikstury):
  najpierw zapytania powtórzone (tak wygląda zapytanie na wiersz), potem pełna lista.

**Jak podnieść budżet świadomie:** najpierw sprawdź w raporcie, czy przyrost nie jest powtórzeniem
(„12× SELECT … WHERE id = ?”). Jeśli nie jest – podnieś wiersz tutaj i dopisz w komentarzu, co go
podniosło i od kiedy.
"""

from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager

import pytest

#: Sufity liczby zapytań na ekranach, mierzone na zimno (``backend/conftest.py`` czyści pamięci
#: podręczne przed każdym testem). „/”, „/me/” i „/coordinator/” mierzy na złotej fiksturze
#: ``apps/tenancy/tests/test_invariants.py`` (tam też: dlaczego próg, a nie równość).
QUERY_BUDGETS: dict[str, int] = {
    # --- inwarianty Konkursu #1 (złota fikstura, ``test_invariants.py``) ----------------------------
    # Zmierzone na złotej fiksturze (wydanie B, po T2 i T3): 29 / 43 / 45. Zapas trzech zapytań
    # jest miejscem na odczyt konkursu, który zakresowanie dokłada w T4/T5 – po tych zadaniach
    # próg wraca do wartości zmierzonej, a nie zostaje „na wszelki wypadek”.
    #
    # +1 od 21.09.2026: slider sponsorów w menu (``apps.cms.sponsor_slider``, procesor kontekstu
    # w ``templates/base.html``, czyli na **każdej** stronie serwisu). Ładunek kosztuje dwa nowe
    # zapytania – ``SiteSettings.for_site`` (włącznik/sekundy/poziomy) i ``PartnersPage…first()``
    # (lista partnerów) – ale na stronie głównej jedno z nich trafia w ustawienia już wczytane
    # przez Wagtaila dla tej samej instancji ``Site`` (``{% get_settings %}`` w tym samym
    # szablonie), więc widoczny przyrost to tu tylko jedno zapytanie. Panel koordynatora
    # i uczestnika (niżej) nie mają tego współdzielenia – tam widać oba.
    #
    # +1 od 22.09.2026: pozycja menu „Dla nauczycieli” (``apps.cms.context_processors.
    # _supervisor_menu_item``) pyta o przełącznik ``SiteSettings.supervisor_registration_enabled``
    # **od razu**, a nie leniwie jak procesor ``apps.web.context_processors.supervisor_registration`` –
    # menu musi znać wynik, żeby wiedzieć, czy w ogóle dołożyć pozycję. Pytanie ma trzydziestosekundową
    # pamięć podręczną na proces (``apps.accounts.supervisors._registration_cache``), więc to jest
    # jedno zapytanie na pół minuty na instalację, nie jedno na żądanie – próg mierzy tu jednak stan
    # zimny (``_reset_panel_counters`` w ``test_invariants.py``), bo inaczej wynik zależałby od
    # tego, co przed tym testem zdążyło wygrzać pamięć w tym samym procesie.
    #
    # +1 od 23.09.2026: odnośnik „Plakaty do pobrania” w stopce **każdej** strony
    # (``apps.promo.availability``, procesor ``promo_materials``) pyta „czy konkurs ma choć jeden
    # opublikowany plakat”. Odpowiedź leży w pamięci podręcznej przez godzinę, jest unieważniana
    # i od razu przeliczana przy każdym zapisie plakatu – w ruchu produkcyjnym to zero zapytań na
    # odsłonę. Próg mierzy jednak stan zimny (``backend/conftest.py`` czyści pamięć przed każdym
    # testem), więc widać tu to jedno ``EXISTS`` pierwszego żądania po zimnym starcie. Że drugie
    # żądanie go już nie płaci, sprawdza ``apps/web/tests/test_posters_public.py``
    # (``test_warm_page_does_not_ask_about_posters``). Ten sam przyrost i ten sam powód przy
    # ``/me/`` i ``/coordinator/`` niżej – stopka jest w ``templates/base.html``.
    "/": 35,
    # 47 = 46 + zapytanie nagłówka CSP o identyfikator GA4, liczone od 21.09.2026 zawsze na zimno
    # (``_reset_panel_counters`` w ``test_invariants.py``). To nie jest nowy koszt strony, tylko
    # koniec zależności pomiaru od kolejności testów.
    # +2 od 21.09.2026: slider sponsorów – ``SiteSettings.for_site`` i ``PartnersPage…first()``,
    # patrz komentarz przy ``"/"`` wyżej.
    # +1 od 23.09.2026: odnośnik „Plakaty do pobrania” w stopce – patrz komentarz przy ``"/"``.
    "/me/": 50,
    # 50 + 1 od 23.09.2026: odnośnik „Plakaty do pobrania” w stopce – patrz komentarz przy ``"/"``.
    "/coordinator/": 51,
    # --- karty i listy panelu koordynatora ---------------------------------------------------------
    # Bezpieczniki „rzędu wielkości” obok asercji o niezmienności kosztu względem danych: sama
    # asercja porównuje dwa pomiary ze sobą, sufit łapie regresję, która podniosła oba naraz.
    "coordinator/member-card": 45,
    "coordinator/members": 30,
    "coordinator/problem-card": 45,
    "coordinator/participant-card": 60,
    # --- rejestracja uczestnika (ścieżka, którą przechodzi każdy) ----------------------------------
    "registration/without-regions": 40,
}


def budget(name: str) -> int:
    """Sufit z tabeli – z komunikatem, gdy ktoś poda nazwę spoza niej."""
    try:
        return QUERY_BUDGETS[name]
    except KeyError:
        raise KeyError(f"Brak budżetu zapytań {name!r} w apps/core/tests/query_budgets.py") from None


_NUMBER = re.compile(r"\b\d+\b")
_STRING = re.compile(r"'(?:[^']|'')*'")
_IN_LIST = re.compile(r"IN \((?:\?, )*\?\)")


def normalized(sql: str) -> str:
    """Zapytanie bez wartości – dwa odczyty różnych wierszy tej samej tabeli dają ten sam napis."""
    return _IN_LIST.sub("IN (…)", _NUMBER.sub("?", _STRING.sub("?", sql)))


def explain_queries(captured: list[dict], *, limit: int | None = None, max_listed: int = 80) -> str:
    """Raport do komunikatu niepowodzenia: powtórzenia na górze, pełna lista pod nimi."""
    lines = []
    if limit is not None:
        lines.append(f"Budżet: {limit}, wykonano: {len(captured)} ({len(captured) - limit:+d}).")
    repeated = [
        (count, sql) for sql, count in Counter(normalized(q["sql"]) for q in captured).items() if count > 1
    ]
    if repeated:
        lines.append("Powtórzone zapytania (tak wygląda zapytanie na wiersz):")
        lines.extend(f"  {count}× {sql[:300]}" for count, sql in sorted(repeated, reverse=True))
    lines.append("Wszystkie zapytania:")
    for number, query in enumerate(captured[:max_listed], start=1):
        lines.append(f"  {number:3d}. {query['sql'][:500]}")
    if len(captured) > max_listed:
        lines.append(f"  … i {len(captured) - max_listed} kolejnych")
    return "\n".join(lines)


@contextmanager
def assert_queries(num: int, *, exact: bool, connection=None, info: str | None = None):
    """Odpowiednik ``django_assert_(max_)num_queries`` z pytest-django z pełnym raportem przy błędzie."""
    from django.db import connection as default_connection
    from django.test.utils import CaptureQueriesContext

    context = CaptureQueriesContext(connection or default_connection)
    with context:
        yield context
    performed = len(context)
    failed = performed != num if exact else performed > num
    if failed:
        expected = f"dokładnie {num}" if exact else f"najwyżej {num}"
        message = f"Oczekiwano {expected} zapytań, wykonano {performed}."
        if info:
            message += f"\n{info}"
        pytest.fail(f"{message}\n{explain_queries(context.captured_queries, limit=None)}")
