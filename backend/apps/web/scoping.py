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
(``apps.grading.services.reviewer_pool``, wspólny z przydziałem automatycznym). Od poprawki po
audycie izolacji (01.10.2026) serwis bierze konkurs **sam** – do tego czasu liczył pulę całej
instalacji, a panel zawężał ją dopiero tutaj, na wyniku; przydział automatyczny takiego zawężenia
nie miał. Funkcja niżej jest już tylko skrótem panelu, a nie drugim filtrem.

Wszystko, co ma własną drogę do konkursu, zawęża się w widoku wprost
(``Model.objects.for_competition(request.competition)``) i tutaj nie trafia.
"""

from __future__ import annotations


def reviewer_pool_for(competition) -> list:
    """Aktywni recenzenci **tego** konkursu – lista do list wyboru w panelu koordynatora.

    Definicja „aktywnego recenzenta” zostaje jedna dla całego systemu i mieszka w serwisie
    oceniania: ta sama lista rozstrzyga o przydziale automatycznym, więc druga jej definicja
    w panelu znaczyłaby ekran proponujący osoby, którym serwis i tak odmówi (albo odwrotnie).

    Zawężenie do konkursu robi teraz sam serwis, jednym zapytaniem – tyle samo, ile kosztowała
    pula całej instalacji przefiltrowana tutaj na wyniku, więc budżet zapytań ekranu przydziałów
    (``apps/web/tests/test_coordinator_assignments_ux.py``) się nie zmienia. ``None`` oddaje pustą
    listę – tę samą odpowiedź daje serwis.
    """
    from apps.grading.services import reviewer_pool

    return reviewer_pool(competition)
