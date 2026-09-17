"""Zawężenia, których panel WWW potrzebuje, a których nie da się wyrazić samym ``for_competition``.

Reguła etapu 1 brzmi: **filtr należy do querysetu** (``apps.tenancy.managers``). Ten moduł jest
zbiorem trzech wyjątków od tej reguły – każdy z nich dotyczy modelu, który własnego klucza obcego
do konkursu **nie ma i mieć nie będzie**, więc nie ma gdzie postawić ``competition_path``:

- ``accounts.User`` – konto platformy, wspólne dla wszystkich konkursów (§ 3.4). Drogą do konkursu
  jest ``Membership`` i tę drogę zapisuje ``apps.web.views.coordinator_accounts``,
- ``core.AuditLog`` – ślad audytowy opisuje obiekt **przez parę tekstową** ``(target_type,
  target_id)``, a nie przez klucz obcy. Zawężenie jest więc z konieczności pośrednie: idzie przez
  konkurs **obiektu**, którego wpis dotyczy (§ 3.9),
- ``grading.CommitteeMember`` w roli „puli recenzentów” – sama pula jest liczona przez serwis
  oceniania (``apps.grading.services.reviewer_pool``, wspólny z przydziałem automatycznym), a ten
  jest własnością zadania T3. Zawężamy więc jego **wynik**, zamiast dopisywać drugą definicję
  „aktywnego recenzenta” w panelu.

Wszystko, co ma własną drogę do konkursu, zawęża się w widoku wprost
(``Model.objects.for_competition(request.competition)``) i tutaj nie trafia.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.db.models import CharField, Q
from django.db.models.functions import Cast

#: Typ obiektu audytu, którym jest **sam konkurs**. Nie da się go zawęzić przez manager
#: (``tenancy.Competition`` nie należy do konkursu – jest konkursem), a musi być widoczny:
#: to pod tą nazwą stoją wpisy ``competition.updated` z ekranu „Ustawienia konkursu”.
COMPETITION_TARGET_TYPE = "tenancy.competition"


def reviewer_pool_for(competition) -> list:
    """Aktywni recenzenci **tego** konkursu – lista do list wyboru w panelu koordynatora.

    Definicja „aktywnego recenzenta” zostaje jedna dla całego systemu i mieszka w serwisie
    oceniania: ta sama lista rozstrzyga o przydziale automatycznym, więc druga jej definicja
    w panelu znaczyłaby ekran proponujący osoby, którym serwis i tak odmówi (albo odwrotnie).

    Zawężamy więc **wynik**, a nie zapytanie – i robimy to po ``competition_id``, czyli po kolumnie,
    którą serwis i tak przywiózł razem z wierszem. Ani jednego zapytania więcej: ekran przydziałów
    ma budżet zapytań pilnowany testem (``apps/web/tests/test_coordinator_assignments_ux.py``),
    a lista wyboru recenzentów nie jest miejscem, w którym warto go wydać.

    Wiersze bez konkursu (``competition_id IS NULL``) są widoczne dla każdego konkursu i to jest
    świadome przez jedno wydanie – dokładnie tak, jak w ``apps.accounts.services.participant_for``:
    w wydaniu B kolumna dopiero powstaje, a wydanie D zamyka ją na ``NOT NULL`` i ta gałąź znika.
    """
    from apps.grading.services import reviewer_pool

    if competition is None:
        return []
    return [
        member
        for member in reviewer_pool()
        if getattr(member, "competition_id", None) in (None, competition.pk)
    ]


def for_competition_or_unclaimed(queryset, competition):
    """Wiersze tego konkursu **oraz** wiersze, których nikomu jeszcze nie przypisano.

    Ta sama reguła i ten sam termin ważności, co w ``apps.accounts.services.participant_for``:
    kolumna ``competition`` powstała w wydaniu B jako nullowalna, a domknięcie na ``NOT NULL``
    wchodzi w wydaniu D po kontroli z § 4.4. Przez ten jeden sezon wiersz z ``NULL`` należy
    „do nikogo” i jest widoczny dla każdego konkursu.

    Dlaczego to jest potrzebne, skoro backfill wypełnił kolumnę: bo wypełnił ją **w chwili
    migracji**. Wiersz dopisany poza serwisem (``/admin/``, komenda, import z pliku, dane sprzed
    wdrożenia poprawki) konkursu nie dostanie, a znikanie takich wierszy z panelu byłoby dla
    Olimpiady Kwantowej zmianą widoczną i niezamówioną (§ 0).

    Czego ta funkcja **nie** robi: nie rozluźnia ``for_competition``. Wiersz z **cudzym**
    konkursem jest niewidoczny tak samo, jak był – i tego pilnują testy krzyżowe, bo fabryki
    zawsze wpisują konkurs.
    """
    if competition is None:
        return queryset.none()
    path = queryset.competition_path
    return queryset.filter(Q(**{path: competition}) | Q(**{f"{path}__isnull": True}))


def _scoped_models() -> dict:
    """``{"app.model": klasa}`` dla modeli, które **umieją** się zawęzić do konkursu.

    Introspekcja rejestru aplikacji, a nie lista nazw w kodzie: lista rozjechałaby się z modelami
    przy pierwszym dołożonym kluczu obcym, a objawem byłaby przeglądarka audytu, która po cichu
    przestaje pokazywać wpisy o nowym rodzaju obiektu (albo pokazuje cudze).

    Pytamy ``_default_manager``, bo to jego używa ``Model.objects`` w widokach i to on niesie
    ``for_competition`` niezależnie od tego, czy model dostał manager gotowy
    (``CompetitionScopedManager``), czy złożony (``competition_scoped_manager("…")``).
    """
    found = {}
    for model in django_apps.get_models():
        if hasattr(model._default_manager, "for_competition"):
            found[model._meta.label_lower] = model
    return found


def _competition_path(model) -> str:
    """Ścieżka od modelu do konkursu, tak jak deklaruje ją jego queryset (``competition_path``)."""
    return model._default_manager.get_queryset().competition_path


def audit_scope(competition, viewer) -> Q:
    """Warunek „ten wpis audytowy **nie** dotyczy cudzego konkursu”.

    **Dlaczego to nie jest zwykłe ``for_competition``.** ``core.AuditLog`` nie ma kolumny konkursu
    (§ 3.9 ją przewiduje, ale ``apps/core/`` nie należy do żadnego z zadań T1–T7 – patrz raport),
    a obiekt opisuje parą tekstową ``(target_type, target_id)``. Jedyną dostępną drogą do konkursu
    jest droga **przez obiekt** i jest to droga jednostronna: da się nią stwierdzić, że wpis należy
    do sąsiada, ale nie da się nią stwierdzić, że należy do nas.

    Dlatego reguła jest zapisana jako **wykluczenie**, a nie jako dopuszczenie. Chowamy wpis wtedy
    i tylko wtedy, gdy obiekt, o którym mówi, **istnieje i należy do innego konkursu**. Wszystko
    inne zostaje widoczne i każdy z tych przypadków jest tu świadomy:

    1. obiekt tego konkursu → widać (to jest zwykły przypadek),
    2. **sam konkurs** (``tenancy.competition``) → widać wyłącznie swój. Bez tego przypadku ekran
       „Ustawienia konkursu” zapisywałby ślad, którego autor nie widzi,
    3. obiekt, którego **już nie ma** (skasowane zadanie, wydarzenie, komunikat, konto) → widać.
       To jest powód, dla którego reguła musi być wykluczeniem: ślad po skasowanym obiekcie jest
       dokładnie tym, po co audyt istnieje, a zawężenie „tylko to, co da się dziś przypisać”
       kasowałoby go z ekranu razem z obiektem,
    4. obiekt **platformowy** (``accounts.user``, ``wagtailcore.site``, zgłoszenie do supportu) →
       widać. Tych modeli nie da się przypisać do konkursu, a ich wpisy koordynator czyta dziś –
       schowanie ich byłoby zmianą widoczną dla Olimpiady Kwantowej, czyli złamaniem § 0.

    Cena przypadków 3 i 4 jest jawna: w instalacji wielokonkursowej koordynator widzi wpisy
    o obiektach, których nikomu nie da się przypisać. Zamyka to dopiero kolumna
    ``AuditLog.competition`` z § 3.9, wypełniana w chwili zapisu – i to jest pozycja na wydanie D.

    Superużytkownik dostaje tabelę bez zawężenia: to operator platformy i to on odpowiada za całą
    instalację. Roli w konkursie mu to nie daje – ``has_role`` nie eskaluje ``is_superuser``.

    Koszt: po jednym podzapytaniu na **rodzaj obiektu obecny w tabeli**, w jednym zdaniu SQL.
    Rodzaje czytamy z danych (jedno zapytanie po indeksie ``core_audit_target_idx``), a nie
    z rejestru modeli, bo instalacja używa zwykle kilkunastu z kilkudziesięciu możliwych.
    Przeglądarka audytu jest ekranem raportowym, a nie ścieżką, którą chodzi każdy uczestnik.

    ``Cast`` na ``CharField`` jest konieczny, bo ``target_id`` jest tekstem (klucz bywa i liczbą,
    i UUID-em), a klucz główny modelu – liczbą. Porównanie bez rzutowania jest w PostgreSQL
    błędem typu, a nie cichym „brak wyników”.
    """
    from apps.core.models import AuditLog

    if getattr(viewer, "is_superuser", False):
        return Q()
    if competition is None:
        # Domyślnie zamknięte: „nie wiadomo, o który konkurs chodzi” nie może znaczyć „wszystkie”.
        return Q(pk__in=())
    models = _scoped_models()
    present = set(AuditLog.objects.values_list("target_type", flat=True).distinct())
    # Cudzy konkurs w polu ``target_id``. ``exclude`` bez pierwszego warunku obejmowałoby też
    # wiersze z ``NULL`` (SQL-owe ``NOT (x = 1)`` jest przy ``NULL`` nieznane), a wiersz bez
    # konkursu należy „do nikogo” i chować go nie wolno – patrz wydanie B w § 4.1.
    hidden = Q(target_type=COMPETITION_TARGET_TYPE) & ~Q(target_id=str(competition.pk))
    for label in sorted(present & set(models)):
        model = models[label]
        path = _competition_path(model)
        foreign = (
            model._default_manager.filter(**{f"{path}__isnull": False})
            .exclude(**{path: competition})
            .annotate(_audit_target_id=Cast("pk", CharField(max_length=64)))
            .values("_audit_target_id")
        )
        hidden |= Q(target_type=label, target_id__in=foreign)
    return ~hidden
