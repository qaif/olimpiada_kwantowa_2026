"""Zawężenie, którego panel WWW potrzebuje, a którego nie da się wyrazić samym ``for_competition``.

Reguła etapu 1 brzmi: **filtr należy do querysetu** (``apps.tenancy.managers``). Po wydaniu D
został z tego modułu **jeden** wyjątek, bo dwa poprzednie zamknęły się własnymi kolumnami:

- ``core.AuditLog`` dostał ``competition`` (§ 3.9) razem z metodą ``visible_to`` – przeglądarka
  audytu woła ją wprost (``apps.web.views.coordinator_reports``), więc zawężanie „przez obiekt,
  o którym mówi wpis” zniknęło razem z powodem, dla którego istniało,
- ``accounts.User`` nadal nie ma kolumny konkursu i mieć nie będzie (§ 3.4: jedna osoba startuje
  w dwóch olimpiadach jednym hasłem), ale jego droga – ``Membership`` plus profil uczestnika –
  jest zapisana tam, gdzie jest używana (``apps.web.views.coordinator_accounts``).

Zostaje ``grading.CommitteeMember`` w roli „puli recenzentów”: samą pulę liczy serwis oceniania
(``apps.grading.services.reviewer_pool``, wspólny z przydziałem automatycznym), więc zawężamy jego
**wynik**, zamiast dopisywać drugą definicję „aktywnego recenzenta” w panelu.

Wszystko, co ma własną drogę do konkursu, zawęża się w widoku wprost
(``Model.objects.for_competition(request.competition)``) i tutaj nie trafia.
"""

from __future__ import annotations


def reviewer_pool_for(competition) -> list:
    """Aktywni recenzenci **tego** konkursu – lista do list wyboru w panelu koordynatora.

    Definicja „aktywnego recenzenta” zostaje jedna dla całego systemu i mieszka w serwisie
    oceniania: ta sama lista rozstrzyga o przydziale automatycznym, więc druga jej definicja
    w panelu znaczyłaby ekran proponujący osoby, którym serwis i tak odmówi (albo odwrotnie).

    Zawężamy więc **wynik**, a nie zapytanie – i robimy to po ``competition_id``, czyli po kolumnie,
    którą serwis i tak przywiózł razem z wierszem. Ani jednego zapytania więcej: ekran przydziałów
    ma budżet zapytań pilnowany testem (``apps/web/tests/test_coordinator_assignments_ux.py``),
    a lista wyboru recenzentów nie jest miejscem, w którym warto go wydać.

    Porównanie jest **ścisłe**: wierszy bez konkursu już nie ma, bo wydanie D domknęło kolumnę na
    ``NOT NULL`` (``accounts.0022_competition_not_null``). Tolerancja z wydania B znikła razem
    z powodem, dla którego istniała – zostawiona byłaby wyłącznie luką czekającą na wiersz
    dopisany poza serwisem.
    """
    from apps.grading.services import reviewer_pool

    if competition is None:
        return []
    return [member for member in reviewer_pool() if member.competition_id == competition.pk]
