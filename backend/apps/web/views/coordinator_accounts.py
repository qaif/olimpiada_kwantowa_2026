"""Konta wszystkich ról w panelu koordynatora: lista, edycja, blokada, usunięcie.

Osobny moduł od ``coordinator.py`` z tego samego powodu, co ``coordinator_stages.py``: tam są
akcje (POST → serwis → komunikat → powrót na pulpit), tutaj pełne strony z listą, formularzami
i własnym ekranem potwierdzenia. Wspólne zostają uprawnienia (``CoordinatorRequiredMixin``)
i zasada, że reguła domenowa mieszka w serwisie (``apps.accounts.profile``), a widok wyłącznie
orkiestruje.

Dlaczego te ekrany w ogóle wychodzą z ``/admin/``: organizator odbiera telefony w rodzaju
„zapisałem się z literówką w adresie e-mail” albo „proszę wykreślić moje dziecko z olimpiady”,
a ``/admin/`` nie zna ani jednej reguły tej domeny – zmiana adresu nie sprząta tam wpisów allauth,
skasowanie konta uczestnika zabiera kaskadą jego prace i recenzje (czyli protokół zawodów), a po
żadnej z tych operacji nie zostaje wpis w audycie razem z resztą historii sprawy.

Konta koordynatorów są tu **widoczne, ale nietykalne**: pokazujemy je, bo lista bez nich kłamałaby
o tym, kto ma konto w serwisie, a zapis odrzuca serwis (``COORDINATOR_PROTECTED``) – dwóch
koordynatorów mogłoby się inaczej nawzajem zablokować jednym kliknięciem.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.anonymised import is_anonymised
from apps.accounts.guardian import STATUS_MISSING, STATUS_PENDING, guardian_status
from apps.accounts.models import GROUP_COORDINATOR, Participant, User, Voivodeship
from apps.accounts.profile import (
    competition_footprint,
    delete_account_by_coordinator,
    update_account_by_coordinator,
)
from apps.accounts.services import participant_for
from apps.core.api import DomainError
from apps.core.models import audit
from apps.web.forms import (
    CoordinatorAccountForm,
    CoordinatorCommitteeForm,
    CoordinatorParticipantForm,
    participant_profile_initial,
)
from apps.web.list_controls import ListControls, SortKey
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/accounts.html"
EDIT_TEMPLATE = "web/coordinator/accounts_edit.html"
DELETE_TEMPLATE = "web/coordinator/accounts_delete.html"

#: Ile kont na stronę. Zwykłe stronicowanie Django i zwykłe odnośniki – bez skryptu, bo lista jest
#: narzędziem do odnalezienia **jednego** konta (wyszukiwarka nad tabelą), a nie do przeglądania
#: całej bazy po kolei.
ACCOUNTS_PER_PAGE = 50


def participant_ids(competition):
    """Konta z profilem uczestnika **w tym konkursie** – podzapytanie do filtra roli.

    Podzapytanie, a nie złączenie przez ``participations``: po § 3.3 relacja jest wielokrotna,
    więc ``filter(participations__…)`` w sumie logicznej z pozostałymi warunkami wyszukiwarki
    daje ``LEFT JOIN``, a ten powiela wiersz konta startującego w dwóch olimpiadach. Lista jest
    stronicowana, więc powielony wiersz to nie tylko podwójne nazwisko na ekranie, ale i błędna
    liczba stron.
    """
    if competition is None:
        return Participant.objects.values("user_id")
    return Participant.objects.filter(competition=competition).values("user_id")


def _profile_scope(relation: str, competition) -> dict:
    """Zawężenie profilu roli do konkursu – puste, gdy żądanie konkursu nie zna.

    Żądanie bez konkursu (instalacja, w której host nie wskazuje żadnego) ma zachowywać się
    dokładnie tak, jak przed wielokonkursowością: ``has_role`` schodzi wtedy do grup Django,
    a ten ekran – do profilu bez zakresu.
    """
    return {} if competition is None else {f"{relation}__competition": competition}


#: Filtr roli w adresie → zawężenie zapytania. Cztery pozycje, bo tyle da się rozstrzygnąć
#: bez zaglądania w grupy każdego wiersza z osobna: uczestnik ma profil uczestnika, członek
#: komitetu – profil komitetu, opiekun szkolny – profil opiekuna, a „pozostałe” nie mają żadnego
#: (konta koordynatorów i konta, które nie dokończyły rejestracji).
#:
#: Każdy filtr bierze **konkurs**, bo profil roli należy do konkursu (§ 3.2): filtr „uczestnicy”
#: bez zakresu pokazywałby konta startujące u sąsiada, a filtr „pozostałe” chowałby je jako
#: cudzych uczestników – czyli oba kłamałyby o tym samym koncie, tylko w przeciwne strony.
ROLE_FILTERS = {
    "participant": lambda qs, competition: qs.filter(pk__in=participant_ids(competition)),
    "committee": lambda qs, competition: qs.filter(
        committee_member__isnull=False, **_profile_scope("committee_member", competition)
    ),
    "supervisor": lambda qs, competition: qs.filter(
        school_supervisor__isnull=False, **_profile_scope("school_supervisor", competition)
    ),
    "other": lambda qs, competition: qs.exclude(pk__in=participant_ids(competition)).filter(
        committee_member__isnull=True, school_supervisor__isnull=True
    ),
}

#: Etykiety filtra – kolejność ma znaczenie, bo w tej kolejności stoją odnośniki nad tabelą.
ROLE_CHOICES = (
    ("", "wszystkie"),
    ("participant", "uczestnicy"),
    ("committee", "komitet"),
    ("supervisor", "opiekunowie"),
    ("other", "pozostałe"),
)

ROLE_COORDINATOR = "koordynator"
ROLE_APPEALS = "komisja odwoławcza"
ROLE_COMMITTEE = "członek komitetu"
ROLE_PARTICIPANT = "uczestnik"
ROLE_SUPERVISOR = "opiekun szkolny"
ROLE_NONE = "bez roli"

STATUS_PENDING_ACTIVATION = "nieaktywowane"
STATUS_BLOCKED = "nieaktywne"
STATUS_ACTIVE = "aktywne"

#: Sortowalne kolumny listy kont (``apps.web.list_controls``). Klucz w adresie → pola ORM; nic
#: spoza tej listy nie trafi do ``order_by``. Porządek domyślny to ``email`` rosnąco – ten sam,
#: który lista miała przed wprowadzeniem sortowania.
#:
#: Kolumny spoza tabeli kont sortują po **adnotacjach**, dokładanych przez widok wyłącznie wtedy,
#: gdy lista jest po nich sortowana (``_annotate_for_sort``): ``stan`` – ta sama trójka, co
#: w kolumnie „Stan” (``account_status``), zapisana liczbą w kolejności aktywne → zablokowane →
#: nieaktywowane; kolumny profilu uczestnika – podzapytaniem o profil **tego** konkursu, bo
#: relacja jest wielokrotna (§ 3.3) i złączenie powielałoby wiersze. Kolumny „Rola” nie sortujemy:
#: rolę liczy Python z grup i profili (``account_role``), a odtworzenie tej kolejności
#: rozstrzygania w SQL-u byłoby drugą, rozjeżdżającą się kopią reguły.
ACCOUNT_SORT_KEYS = (
    SortKey("email", "E-mail", ("email",)),
    SortKey("nazwisko", "Imię i nazwisko", ("last_name", "first_name")),
    SortKey("kod", "Kod publiczny", ("sort_kod",)),
    SortKey("stan", "Stan", ("sort_stan",)),
    SortKey("zalozone", "Założone", ("date_joined",), descending_first=True),
)

#: Kolumny listy **uczestników** (``?role=participant`` – pozycja „Uczestnicy” w menu). To ta sama
#: lista kont z filtrem roli (``coordinator_nav``: osobny ekran powtarzałby wyszukiwarkę,
#: stronicowanie i kolumny), ale z kolumnami profilu uczestnika, bo „przeglądanie uczestników” to
#: pytania o szkołę, klasę, województwo, zgodę opiekuna i to, czy ktoś w ogóle coś oddał.
#: Imię i nazwisko są tu osobnymi kolumnami – sortowanie po imieniu to osobne pytanie („Ania z LO 5”).
PARTICIPANT_SORT_KEYS = (
    SortKey("kod", "Kod", ("sort_kod",)),
    SortKey("nazwisko", "Nazwisko", ("last_name", "first_name")),
    SortKey("imie", "Imię", ("first_name", "last_name")),
    SortKey("email", "E-mail", ("email",)),
    SortKey("szkola", "Szkoła", ("sort_szkola",)),
    SortKey("wojewodztwo", "Województwo", ("sort_wojewodztwo",)),
    SortKey("klasa", "Klasa", ("sort_klasa",)),
    SortKey("opiekun", "Zgoda opiekuna", ("sort_opiekun",)),
    SortKey("prace", "Prace", ("sort_prace",), descending_first=True),
    SortKey("stan", "Stan", ("sort_stan",)),
    SortKey("zalozone", "Założone", ("date_joined",), descending_first=True),
)

#: Klucz sortowania → pole profilu uczestnika, po którym sortuje podzapytanie. Województwa tu nie ma
#: – patrz ``VOIVODESHIP_RANK``.
PARTICIPANT_SORT_FIELDS = {
    "kod": "public_code",
    "szkola": "school",
    "klasa": "grade",
    "opiekun": "guardian_consent",
}

#: Litery z ogonkami → litera bazowa z dopiskiem, który stawia je **za** wszystkimi słowami na tę
#: literę bazową („~” jest w ASCII za „z”). Tyle wystarcza, żeby porządek szesnastu nazw był
#: porządkiem polskiego alfabetu: „łódzkie” po „lubuskie”, „śląskie” po „pomorskie”.
_POLISH_LETTERS = {
    "ą": "a~",
    "ć": "c~",
    "ę": "e~",
    "ł": "l~",
    "ń": "n~",
    "ó": "o~",
    "ś": "s~",
    "ź": "z~",
    "ż": "z~~",
}

#: Pozycja województwa w porządku alfabetycznym **nazw**. Kolumna trzyma slug ASCII
#: (``Voivodeship``: ``lodzkie``), a sortowanie po slugu stawiałoby „łódzkie” przed „lubelskie” –
#: organizator czyta nazwy, więc sortujemy po nazwach, liczbą z ``Case`` w podzapytaniu.
VOIVODESHIP_RANK = {
    value: rank
    for rank, (value, _label) in enumerate(
        sorted(Voivodeship.choices, key=lambda choice: "".join(_POLISH_LETTERS.get(c, c) for c in choice[1]))
    )
}


def _works(competition):
    """Prace uczestników bieżącej edycji konkursu – podstawa kolumny „Prace”.

    „Praca” to zadanie, do którego uczestnik cokolwiek oddał (``Count(problem, distinct)``), a nie
    liczba wersji pliku: poprawka oddana trzy razy jest jedną pracą. Bieżąca edycja, bo profil
    uczestnika żyje w konkursie przez wiele lat, a pytanie z listy brzmi „czy w tym roku coś oddał”.
    Konkurs bez edycji bieżącej (albo żądanie bez konkursu) liczy wszystkie prace profilu.
    """
    from apps.competitions.services import current_edition
    from apps.submissions.models import Submission

    works = Submission.objects.all()
    if competition is not None:
        works = works.filter(entry__participant__competition=competition)
        edition = current_edition(competition)
        if edition is not None:
            works = works.filter(entry__stage__edition=edition)
    return works


def _annotate_for_sort(users, controls: ListControls, competition):
    """Adnotacja pod kolumnę sortowaną po wartości spoza tabeli kont – tylko dla wybranej kolumny.

    Tylko dla wybranej, bo każda kosztuje: podzapytanie wykonuje się dla każdego wiersza **przed**
    stronicowaniem (sortuje się cały wynik), a przy porządku domyślnym nie ma po co go liczyć.
    """
    if controls.sort in PARTICIPANT_SORT_FIELDS:
        profile = Participant.objects.filter(user=OuterRef("pk"))
        if competition is not None:
            profile = profile.filter(competition=competition)
        field = PARTICIPANT_SORT_FIELDS[controls.sort]
        users = users.annotate(**{f"sort_{controls.sort}": Subquery(profile.values(field)[:1])})
    elif controls.sort == "wojewodztwo":
        profile = Participant.objects.filter(user=OuterRef("pk"))
        if competition is not None:
            profile = profile.filter(competition=competition)
        rank = Case(
            *(When(district=value, then=Value(position)) for value, position in VOIVODESHIP_RANK.items()),
            default=Value(len(VOIVODESHIP_RANK)),
            output_field=IntegerField(),
        )
        users = users.annotate(
            sort_wojewodztwo=Subquery(
                profile.annotate(rank=rank).values("rank")[:1], output_field=IntegerField()
            )
        )
    elif controls.sort == "prace":
        counted = (
            _works(competition)
            .filter(entry__participant__user=OuterRef("pk"))
            .values("entry__participant__user")
            .annotate(total=Count("problem", distinct=True))
            .values("total")
        )
        users = users.annotate(sort_prace=Coalesce(Subquery(counted, output_field=IntegerField()), Value(0)))
    elif controls.sort == "stan":
        users = users.annotate(
            sort_stan=Case(
                When(email_verified_at__isnull=True, then=Value(2)),
                When(is_active=False, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
    return users


def works_by_participant(participants, competition) -> dict:
    """Liczba prac (``_works``) dla profili z jednej strony listy – jedno zapytanie na stronę."""
    ids = [participant.pk for participant in participants]
    if not ids:
        return {}
    rows = (
        _works(competition)
        .filter(entry__participant__in=ids)
        .values("entry__participant")
        .annotate(total=Count("problem", distinct=True))
    )
    return {row["entry__participant"]: row["total"] for row in rows}


def users_for_competition(competition):
    """Konta, które ten koordynator w ogóle widzi: **jego członkowie i konta niczyje**.

    ``accounts.User`` jest kontem platformy i celowo nie ma kolumny konkursu (§ 3.4): jedna osoba
    startuje w dwóch olimpiadach jednym hasłem i jednym resetem hasła. Drogą do konkursu jest więc
    ``Membership`` – wiersz zapisywany przez ``apps.accounts.services.grant_role`` przy każdej
    drodze nadania roli (rejestracja, import zbiorczy, zaproszenie do komitetu, rejestracja
    opiekuna), czyli przy każdym koncie, które w tym konkursie cokolwiek robi.

    Reguła jest **wykluczeniem**, a nie dopuszczeniem, i to jest jej sedno: chowamy konto wtedy
    i tylko wtedy, gdy należy ono do **innego** konkursu. Konto bez ani jednego członkostwa
    („niczyje”) zostaje widoczne dla każdego koordynatora.

    Dlaczego tak, a nie „tylko moi członkowie”. Konto bez członkostwa jest w tej bazie stanem
    realnym i niepustym: konto operatora platformy, konto założone w ``/admin/``, konto po
    odebraniu wszystkich ról, konto sprzed backfillu z T2 (który wstawia członkostwa z
    ``auth_user_groups``, więc konta **bez grupy** nie dostają żadnego). Każde z nich koordynator
    Olimpiady Kwantowej widzi dziś na swojej liście, a znikanie kont z listy po wdrożeniu byłoby
    zmianą widoczną i niezamówioną (§ 0). Cena – w instalacji wielokonkursowej takie konto widzą
    obaj koordynatorzy – jest jawna i znika razem z ``Membership`` dla każdego konta.

    Co z listy **wypada**: konto należące do cudzego konkursu. Koordynator nie ma go zmieniać,
    blokować ani kasować – to nie są jego dane osobowe do administrowania (§ 8, D5), a lista kont
    jest ekranem, z którego te trzy czynności wychodzą.

    **Drugi dowód własności: profil uczestnika.** Samo członkostwo nie wystarczy, bo wiersz
    ``Membership`` powstaje przy nadaniu roli, a ``Participant`` bywa dopisany inną drogą
    (``/admin/``, import, dane sprzed poprawki) – i takie konto trafiłoby na listę jako „niczyje”
    razem z nazwiskiem, szkołą i kodem publicznym uczestnika **sąsiada**. Konto z profilem
    wyłącznie w cudzym konkursie jest więc wykluczone wprost, a konto z profilem tutaj – widoczne
    nawet bez członkostwa.

    ``__in`` z podzapytaniem, a nie ``JOIN`` przez ``memberships``/``participations``: osoba
    z dwiema rolami w jednym konkursie (recenzent i członek komisji odwoławczej) albo startująca
    w dwóch olimpiadach pojawiłaby się przy złączeniu dwa razy, a ``distinct()`` na liście ze
    stronicowaniem kosztuje sortowanie całego wyniku.
    """
    from apps.accounts.models import Membership

    mine = Membership.objects.for_competition(competition).values("user_id")
    claimed_by_anyone = Membership.objects.values("user_id")
    starts_here = participant_ids(competition)
    starts_elsewhere = (
        Participant.objects.none().values("user_id")
        if competition is None
        else Participant.objects.exclude(competition=competition).values("user_id")
    )
    visible = Q(pk__in=mine) | Q(pk__in=starts_here) | ~Q(pk__in=claimed_by_anyone)
    stranger = Q(pk__in=starts_elsewhere) & ~Q(pk__in=mine) & ~Q(pk__in=starts_here)
    return User.objects.filter(visible).exclude(stranger)


def is_protected(user: User) -> bool:
    """Czy tego konta nie wolno zmieniać z panelu. Ta sama reguła, co w ``accounts.profile``.

    Powtórzona tutaj **wyłącznie po to, żeby ekran o tym powiedział**: przycisków, których serwis
    i tak nie przepuści, nie ma prawa być na stronie. Rozstrzygający pozostaje serwis – widok nie
    jest bramką, tylko informacją.

    Grupy czytamy z ``prefetch_related("groups")``, gdy wołający je dociągnął (lista kont robi to
    dla całej strony): ``groups.filter(...).exists()`` omija bufor prefetchu i na liście dawało dwa
    zapytania na **każdy** wiersz (rola i odznaka „chronione”). Bez prefetchu – jedno zapytanie,
    jak dotąd; ekran edycji ogląda jedno konto.
    """
    if user.is_superuser:
        return True
    prefetched = getattr(user, "_prefetched_objects_cache", {}).get("groups")
    if prefetched is not None:
        return any(group.name == GROUP_COORDINATOR for group in prefetched)
    return user.groups.filter(name=GROUP_COORDINATOR).exists()


def two_factor_feature() -> bool:
    """Czy pokazywać na tym ekranie cokolwiek o drugim składniku (``TWO_FACTOR_ENABLED``).

    Import lokalny w funkcji, a nie na górze modułu: ``apps.accounts.twofactor`` wciąga
    ``cryptography`` i warstwę wymuszającą, a ten moduł jest importowany przy każdym starcie
    procesu razem z mapą adresów.
    """
    from apps.accounts.twofactor import is_enabled

    return is_enabled()


def two_factor_device(user: User):
    """Potwierdzony drugi składnik konta albo ``None`` – wyłącznie do pokazania na ekranie.

    Przy wyłączonej funkcji zwraca ``None`` **niezależnie od zawartości bazy**: konto może mieć
    zapisane urządzenie z czasów, gdy drugi składnik działał (wyłącznik ich nie kasuje), ale
    ekran ma pokazywać stan obowiązujący, a nie historię. Sekcję i tak ukrywa ``two_factor_feature``
    – to jest druga, tańsza bramka, żeby przy wyłączonej funkcji nie było nawet zapytania.
    """
    if not two_factor_feature():
        return None
    from apps.accounts.twofactor import confirmed_device

    return confirmed_device(user)


def profile_here(user: User, relation: str, competition):
    """Profil roli (``committee_member``, ``school_supervisor``) **tego** konkursu albo ``None``.

    Obie relacje są wciąż ``OneToOne``, ale ich wiersz ma od wydania B własny konkurs (§ 3.2),
    więc samo „profil istnieje” przestało znaczyć „profil tutaj”. Porównujemy identyfikator,
    a nie robimy drugiego zapytania: wiersz przyjechał już razem z kontem
    (``select_related``), a ekran listy pokazuje pięćdziesiąt kont naraz.

    Żądanie bez konkursu oddaje profil bez zawężenia – tak samo jak ``has_role`` schodzi wtedy
    do grup Django, czyli do zachowania sprzed wielokonkursowości.
    """
    profile = getattr(user, relation, None)
    if profile is None or competition is None:
        return profile
    return profile if profile.competition_id == competition.pk else None


def account_role(user: User, competition, participant) -> str:
    """Rola konta **w tym konkursie** w jednym słowie – do kolumny listy i do nagłówka edycji.

    Kolejność rozstrzygania nie jest dowolna: koordynator wygrywa, bo to on decyduje o tym, czy
    konto w ogóle da się tu tknąć; potem komitet, bo profil komitetu niesie uprawnienia do cudzych
    prac. Konto z profilem uczestnika i profilem komitetu naraz jest w tym serwisie niemożliwe
    (rejestracje są rozłączne), ale kolejność i tak musi być zapisana, a nie przypadkowa.

    Profil uczestnika przychodzi **z zewnątrz** (``participant_for`` albo mapa policzona na całą
    stronę listy), bo po § 3.3 relacja jest wielokrotna: jedna osoba ma tyle profili, w ilu
    olimpiadach startuje, a ta kolumna mówi o jednej z nich.

    Dlaczego etykiety nie liczy ``roles_for``, choć to ono rozstrzyga o dostępie: bo rozróżnienie
    „członek komitetu” / „komisja odwoławcza” jest faktem **profilu** (``is_appeals_committee``),
    a nie członkostwa, a przy wyłączonym ``memberships_enforced`` ``roles_for`` czyta globalne
    grupy Django – czyli konto z grupą, ale bez profilu (członek komitetu przed zatwierdzeniem),
    dostałoby na liście Olimpiady Kwantowej etykietę, której dziś tam nie ma (§ 0). Zakres
    konkursu wchodzi więc przez profile, a nie przez podmianę ich źródła.
    """
    if is_protected(user):
        return ROLE_COORDINATOR
    member = profile_here(user, "committee_member", competition)
    if member is not None:
        return ROLE_APPEALS if member.is_appeals_committee else ROLE_COMMITTEE
    if participant is not None:
        return ROLE_PARTICIPANT
    # Opiekun szkolny na końcu, bo jego rola jest najsłabsza: nie ocenia, nie startuje i widzi
    # wyłącznie tych uczniów, którzy sami wskazali jego adres.
    if profile_here(user, "school_supervisor", competition) is not None:
        return ROLE_SUPERVISOR
    return ROLE_NONE


def account_status(user: User) -> str:
    """Stan konta widziany przez koordynatora: trzy różne rzeczy, nie dwie.

    ``email_verified_at`` puste znaczy „rejestracja nie została dokończona” – takie konto czeka na
    link i zniknie samo (``apps.accounts.tasks``). ``is_active=False`` przy potwierdzonym adresie
    znaczy co innego: organizator **zablokował** logowanie. Sklejenie obu w „nieaktywne” kazałoby
    zgadywać, czy wysłać link aktywacyjny, czy odblokować konto.
    """
    if user.email_verified_at is None:
        return STATUS_PENDING_ACTIVATION
    if not user.is_active:
        return STATUS_BLOCKED
    return STATUS_ACTIVE


def participants_by_user(users, competition) -> dict:
    """Profile uczestników **tego** konkursu dla kont z jednej strony listy – jedno zapytanie.

    ``select_related("participant")`` przestało istnieć razem z relacją ``OneToOne`` (§ 3.3),
    a ``participant_for`` w pętli po pięćdziesięciu wierszach to pięćdziesiąt zapytań. Mapa
    ``{user_id: profil}`` liczona raz na stronę zostawia ten ekran przy jednym.
    """
    if competition is None:
        rows = Participant.objects.filter(user__in=users)
    else:
        rows = Participant.objects.filter(user__in=users, competition=competition)
    return {row.user_id: row for row in rows}


def account_rows(users, competition, *, with_works: bool = False) -> list[dict]:
    """Wiersze tabeli – wyłącznie prezentacja, żadnej reguły domenowej.

    ``deleted`` – konto po anonimizacji (na liście wyłącznie przy włączonym „Pokaż usunięte
    konta”). Szablon pokazuje wtedy „Konto usunięte” zamiast adresu ``deleted-…@invalid.…``: ten
    adres jest technicznym wypełniaczem kolumny logowania, a nie informacją dla człowieka.

    ``with_works`` – kolumna „Prace” listy uczestników: jedno zapytanie zbiorcze na stronę
    (``works_by_participant``), a nie licznik w każdym wierszu.
    """
    participants = participants_by_user(users, competition)
    works = works_by_participant(participants.values(), competition) if with_works else {}
    return [
        {
            "user": user,
            "deleted": is_anonymised(user),
            "role": account_role(user, competition, participants.get(user.pk)),
            "status": account_status(user),
            # Profil w wierszu, bo szablon nie ma jak dojść do właściwego z samego konta:
            # relacja jest wielokrotna, a ta lista mówi o jednym konkursie.
            "participant": participants.get(user.pk),
            "public_code": getattr(participants.get(user.pk), "public_code", ""),
            "works": works.get(getattr(participants.get(user.pk), "pk", None), 0),
            "protected": is_protected(user),
            # Szkoła opiekuna – wyłącznie **tego** konkursu (``profile_here`` odcina profil
            # sąsiedniej olimpiady), obok etykiety roli. Bez osobnej kolumny: to jest jedyny
            # wiersz, którego dotyczy, a tabela nie ma miejsca na kolumnę pustą dla wszystkich
            # pozostałych ról.
            "supervisor_school": getattr(
                profile_here(user, "school_supervisor", competition), "display_school", ""
            ),
        }
        for user in users
    ]


class CoordinatorAccountsView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/`` – lista wszystkich kont z wyszukiwarką, filtrem roli i sortowaniem.

    Konta usunięte na żądanie (po anonimizacji, ``apps.accounts.anonymised``) są domyślnie
    **schowane**: to już nie są osoby, którymi się administruje, a ich adres „deleted-…” wyglądał
    na liście jak błąd (zgłoszenie organizatora z 24.09.2026). Przełącznik „Pokaż usunięte konta”
    zostaje, bo tego wiersza czasem trzeba – do audytu, do sprawy z odwołaniem, do sprawdzenia,
    czy żądanie usunięcia zostało wykonane. Liczba ukrytych stoi przy przełączniku.
    """

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        role = request.GET.get("role") or ""
        # Lista uczestników (``?role=participant``) ma własne kolumny profilu i własny zestaw
        # kluczy sortowania; porządek domyślny obu to adres e-mail – ten sam, co przed zmianą.
        participant_mode = role == "participant"
        controls = ListControls(
            request, PARTICIPANT_SORT_KEYS if participant_mode else ACCOUNT_SORT_KEYS, default="email"
        )
        # ``select_related`` na profilach komitetu i opiekuna oraz ``prefetch_related`` na grupach:
        # rola stoi w każdym wierszu, więc bez tego strona robiłaby trzy zapytania na konto.
        # Profilu uczestnika tu nie ma – relacja jest wielokrotna (§ 3.3) i wchodzi mapą
        # ``participants_by_user`` liczoną na stronę, a nie złączeniem powielającym wiersze.
        users = (
            users_for_competition(request.competition)
            .select_related("committee_member", "school_supervisor")
            .prefetch_related("groups")
        )
        if role in ROLE_FILTERS:
            users = ROLE_FILTERS[role](users, request.competition)
        if query:
            # Po fragmencie, bo koordynator szuka z pamięci albo ze słuchu („Kowalska, chyba
            # gmail”). Kod publiczny wpada do tego samego pola – jest jedynym identyfikatorem,
            # jaki uczestnik widzi u siebie i podaje przez telefon. Szukamy go **w tym
            # konkursie**: kod z cudzej olimpiady nie jest identyfikatorem, który ten ekran zna.
            matching_codes = participant_ids(request.competition).filter(public_code__icontains=query)
            users = users.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(pk__in=matching_codes)
            )
        # Po wyszukiwaniu i filtrze roli, a przed sortowaniem: licznik ukrytych mówi wtedy
        # o **tej** liście („w tym wyszukiwaniu schowano jedno konto usunięte”).
        users = controls.filter_deleted(users)
        users = controls.order(_annotate_for_sort(users, controls, request.competition))
        paginator = Paginator(users, ACCOUNTS_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))
        # Wyszukiwanie, filtr, sortowanie i przełącznik muszą przeżyć przejście na kolejną stronę –
        # bez tego druga strona pokazywałaby wszystkie konta, i to w innym porządku niż pierwsza.
        # Odnośniki filtra roli niosą ten sam stan, tylko bez ``role`` (``role_query``).
        context = {
            "rows": account_rows(page.object_list, request.competition, with_works=participant_mode),
            "participant_mode": participant_mode,
            "page_obj": page,
            "paginator": paginator,
            "query": query,
            "role": role,
            "role_choices": ROLE_CHOICES,
            "filter_query": controls.page_query,
            "role_query": controls.query_without("role"),
            "controls": controls,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


def _warn_about_a_missing_guardian_consent(request, user: User) -> None:
    """Ostrzega koordynatora, gdy po jego zapisie uczestnik wychodzi na małoletniego bez zgody.

    Powód jest jeden i konkretny: **data urodzenia jest edytowalna z tego ekranu**, a od niej
    zależy podstawa prawna udziału w zawodach. Poprawka „2007 → 2009” zamienia uczestnika
    pełnoletniego w małoletniego bez zgody opiekuna, a bez tego zdania koordynator zobaczyłby
    wyłącznie „dane zostały zapisane” i dowiedziałby się o brakującej zgodzie najwcześniej
    z karty uczestnika, na którą już nie wraca.

    Ostrzeżenie, a nie odmowa: zapis poprawnych danych nie może zależeć od oświadczenia, którego
    koordynator za nikogo nie złoży. Zgodę zbiera uczestnik albo jego opiekun
    (``apps.accounts.guardian``), a ekran ma o tym **przypomnieć**, a nie tego pilnować.

    Stan liczy ``guardian_status``, czyli to samo miejsce, co karta uczestnika i panel uczestnika –
    trzeci rachunek pełnoletności w tym module byłby trzecią odpowiedzią na to samo pytanie.
    """
    participant = participant_for(user, request.competition)
    if participant is None:
        return
    state = guardian_status(participant)["state"]
    if state in (STATUS_MISSING, STATUS_PENDING):
        messages.warning(
            request,
            "Uwaga: uczestnik jest niepełnoletni, brak potwierdzonej zgody opiekuna. "
            "Zgodę składa opiekun prawny – uczestnik prosi o nią ze swojego panelu.",
        )


def _account(competition, pk: int) -> User:
    """Konto z tego konkursu albo 404.

    404, a nie 403: konto cudzego organizatora **nie istnieje** dla tego koordynatora, a kod
    odpowiedzi nie ma prawa potwierdzać, że ktoś o takim identyfikatorze się gdziekolwiek zapisał
    (§ 3.6). Zawężenie idzie z querysetu, więc żaden z pięciu ekranów tego modułu nie może go
    pominąć – wszystkie wołają tę funkcję.
    """
    return get_object_or_404(
        users_for_competition(competition).select_related("committee_member", "school_supervisor"),
        pk=pk,
    )


class CoordinatorAccountEditView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/<pk>/`` – edycja konta i profilu roli w jednym zapisie.

    Formularze profilu powstają wyłącznie wtedy, gdy konto ma dany profil: pusty blok „dane
    uczestnika” przy koncie recenzenta sugerowałby, że da się go tam dopisać – a profil uczestnika
    powstaje przy rejestracji razem ze zgodami i kodem publicznym, więc nie ma jak.

    Konto koordynatora otwiera się tylko do odczytu. Odmowę i tak wydaje serwis, dlatego POST
    na taki adres wraca z jego komunikatem, a nie z ciszą przekierowania.
    """

    def get(self, request, pk: int):
        user = _account(request.competition, pk)
        return self._render(request, user, self._forms(user))

    def post(self, request, pk: int):
        user = _account(request.competition, pk)
        forms = self._forms(user, data=request.POST)
        # ``all(...)`` po liście, a nie w generatorze z krótkim spięciem: każdy formularz ma zostać
        # sprawdzony, żeby błędy stanęły pod polami **wszystkich** bloków naraz.
        if not all([form.is_valid() for form in forms.values()]):
            return self._render(request, user, forms, status=400)
        try:
            update_account_by_coordinator(
                user,
                actor=request.user,
                request=request,
                account=forms["account"].cleaned_data if "account" in forms else {},
                participant=forms["participant"].cleaned_data if "participant" in forms else None,
                committee=forms["committee"].cleaned_data if "committee" in forms else None,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            # Świeży obiekt i świeże formularze: strona ma pokazać stan, który faktycznie
            # obowiązuje, a nie odrzucone wartości z żądania.
            fresh = _account(request.competition, pk)
            return self._render(request, fresh, self._forms(fresh), status=exc.status_code)
        messages.success(request, f"Dane konta {user.email} zostały zapisane.")
        _warn_about_a_missing_guardian_consent(request, user)
        return redirect(reverse("web:coordinator-accounts"))

    def _forms(self, user: User, data=None) -> dict:
        """Formularze, które ten ekran ma pokazać. Konto chronione nie dostaje żadnego."""
        if is_protected(user):
            return {}
        forms = {
            "account": CoordinatorAccountForm(
                data,
                prefix="account",
                initial={
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "is_active": user.is_active,
                },
            )
        }
        # Profil uczestnika **tego** konkursu: blok „dane uczestnika” ma pokazywać szkołę i klasę
        # zgłoszone tutaj, a nie te, które ta sama osoba podała sąsiedniej olimpiadzie.
        participant = participant_for(user, self.request.competition)
        if participant is not None:
            forms["participant"] = CoordinatorParticipantForm(
                data, prefix="participant", initial=participant_profile_initial(participant)
            )
        member = profile_here(user, "committee_member", self.request.competition)
        if member is not None:
            forms["committee"] = CoordinatorCommitteeForm(
                data,
                prefix="committee",
                initial={
                    "status": member.status,
                    "district": member.district or "",
                    "is_appeals_committee": member.is_appeals_committee,
                },
            )
        return forms

    def _render(self, request, user: User, forms: dict, *, status: int = 200):
        participant = participant_for(user, request.competition)
        context = {
            "account": user,
            "role": account_role(user, request.competition, participant),
            "status_label": account_status(user),
            "protected": is_protected(user),
            # Stan zgody opiekuna – do odczytu. Reguła jest jedna dla panelu uczestnika
            # i dla tego ekranu (``apps.accounts.guardian.guardian_status``).
            "guardian": (
                guardian_status(participant) if participant is not None else {"state": "not_required"}
            ),
            "participant": participant,
            "committee": profile_here(user, "committee_member", request.competition),
            # Drugi składnik logowania – do odczytu plus jeden przycisk. Koordynator nie może go
            # tu **włączyć** za kogoś (sekret musi powstać na urządzeniu właściciela), a jedynie
            # zdjąć zgubiony (``CoordinatorTwoFactorResetView``). Przy wyłączonym
            # ``TWO_FACTOR_ENABLED`` cała sekcja znika z ekranu – patrz ``two_factor_feature``.
            "two_factor_enabled": two_factor_feature(),
            "two_factor": two_factor_device(user),
            "account_form": forms.get("account"),
            "participant_form": forms.get("participant"),
            "committee_form": forms.get("committee"),
        }
        return TemplateResponse(request, EDIT_TEMPLATE, context, status=status)


class CoordinatorAccountExportView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/<pk>/export/`` – paczka z danymi cudzego konta (art. 20 RODO).

    Istnieje, bo żądanie przenoszenia danych przychodzi też **poza serwisem**: listem, mailem albo
    przez telefon, od osoby, która akurat nie może się zalogować (a to bywa właśnie treścią jej
    sprawy). Bez tego wejścia organizator odpowiadałby na wniosek z art. 20 zrzutem z bazy robionym
    ręcznie – czyli czymś, czego zakresu nikt nie sprawdza.

    Paczkę buduje ta sama funkcja, co przy własnym eksporcie (``account.send_export``), więc
    zakres danych jest identyczny: ani szerszy, bo koordynator prosi, ani węższy. Różnica jest
    jedna i jest w audycie – ``account.exported_by_coordinator`` zamiast ``account.exported``.

    POST, a nie GET: wydanie cudzych danych jest decyzją organizatora, a nie odczytem strony.
    Limitu częstotliwości tu nie ma i to jest świadome – ogranicza go człowiek, który musi
    kliknąć, a jego kliknięcie zostaje w aktach pod własną nazwą.
    """

    def post(self, request, pk: int):
        from apps.web.views.account import send_export

        user = _account(request.competition, pk)
        return send_export(request, user, actor=request.user)


class CoordinatorTwoFactorResetView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/<pk>/2fa-reset/`` – zdjęcie drugiego składnika z cudzego konta.

    Istnieje dla jednego scenariusza, który zdarza się naprawdę: członek komisji zgubił telefon
    z aplikacją uwierzytelniającą i nie ma przy sobie kartki z kodami zapasowymi. Bez tego wejścia
    jedyną drogą powrotu byłaby zmiana wiersza w bazie przez kogoś z dostępem do serwera – czyli
    czynność, której nikt nie widzi i której nikt nie zliczy.

    Reset **nie zakłada nowego sekretu**: konto zostaje bez drugiego składnika i właściciel
    konfiguruje go sam na nowym urządzeniu. Nowy sekret wysłany kanałem, którym da się go
    przechwycić, byłby zabezpieczeniem tylko z nazwy.

    Ekranu potwierdzenia tu nie ma i to jest różnica wobec usuwania konta: skutek jest odwracalny
    jednym kliknięciem właściciela, a ryzyko pomyłki (zdjęcie ochrony nie temu, komu trzeba)
    pokrywa wpis ``2fa.reset`` w audycie razem z nazwiskiem koordynatora.
    """

    def post(self, request, pk: int):
        from apps.accounts.twofactor import reset_by_coordinator

        # Ten sam wyłącznik, co przy ekranach właściciela konta: przy wyłączonej funkcji adres nie
        # istnieje. Ukrycie samego przycisku nie wystarcza – endpoint zdejmujący zabezpieczenie
        # z cudzego konta nie może zostać osiągalny tylko dlatego, że nie ma do niego odnośnika.
        if not two_factor_feature():
            raise Http404("Logowanie dwuskładnikowe jest wyłączone na tej instalacji.")

        user = _account(request.competition, pk)
        if reset_by_coordinator(user, actor=request.user, request=request):
            messages.success(
                request,
                f"Drugi składnik logowania konta {user.email} został zdjęty. "
                "Właściciel może włączyć go od nowa na swoim profilu.",
            )
        else:
            messages.info(request, f"Konto {user.email} nie miało włączonego drugiego składnika.")
        return redirect(reverse("web:coordinator-account-edit", args=[user.pk]))


class CoordinatorPasswordResetView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/<pk>/password-reset/`` – wysyłka linku do zmiany hasła cudzego konta.

    Odpowiedź na inny telefon niż ten z § powyżej: „zapomniałem hasła, a nie mam już dostępu do
    poczty ze szkoły” albo po prostu „proszę zresetować mi hasło” – organizator ma to załatwić bez
    proszenia uczestnika, żeby sam znalazł link w swojej skrzynce (decyzja organizatora,
    22.09.2026).

    **Koordynator nie ustawia hasła ani nie widzi linku.** Widok woła ten sam formularz Django
    (``PasswordResetForm``) z tymi samymi szablonami listu, co samoobsługowy ``PasswordResetView``
    (``apps.web.views.public``) – list jest bajt w bajt identyczny z tym, który wysyła sobie sam
    uczestnik, a token nigdy nie trafia do odpowiedzi HTTP ani do audytu. Inny efekt tego samego
    kliknięcia (ustawienie hasła wprost przez koordynatora) uczyniłby go posiadaczem hasła, które
    powinno znać wyłącznie właściciel konta.

    Limit ``password_reset`` (scope publicznego formularza) tu **nie obowiązuje**: ogranicza on
    anonima zgadującego cudze adresy, a to żądanie idzie od zalogowanego koordynatora o koncie,
    które już ma otwarte przed sobą – to samo rozróżnienie, co przy eksporcie RODO wyżej.

    Cztery odmowy, każda bez wysyłki i bez wpisu w audycie:

    - **konto jeszcze nie aktywowane** – link do zmiany hasła nie miałby dokąd trafić, dopóki
      adres nie jest potwierdzony; na to jest osobny przycisk („Wyślij link ponownie” na ekranie
      aktywacji),
    - **konto zablokowane** (``is_active=False``) – blokada ma znaczyć „nie loguje się wcale”,
      a nie „loguje się nowym hasłem”,
    - **konto bez hasła platformy** (``has_usable_password()`` fałsz, logowanie wyłącznie przez
      zewnętrznego dostawcę) – nie ma czego resetować,
    - **konto własne koordynatora** – do tego służy „Nie pamiętasz hasła?” na stronie logowania,
      a nie ekran zarządzania cudzymi kontami.
    """

    def post(self, request, pk: int):
        from django.contrib.auth.forms import PasswordResetForm
        from django.contrib.auth.tokens import default_token_generator

        from apps.web.views.public import PasswordResetView as SelfServicePasswordResetView
        from apps.web.views.public import service_name

        user = _account(request.competition, pk)
        edit_url = reverse("web:coordinator-account-edit", args=[user.pk])

        if user.pk == request.user.pk:
            messages.error(
                request,
                "Własnego hasła nie resetuje się z tego ekranu – od tego jest „Nie pamiętasz "
                "hasła?” na stronie logowania.",
            )
            return redirect(edit_url)
        if user.email_verified_at is None:
            messages.error(
                request,
                f"Konto {user.email} nie zostało jeszcze aktywowane – link do zmiany hasła nie ma "
                "dokąd trafić. Wyślij najpierw link aktywacyjny.",
            )
            return redirect(edit_url)
        if not user.is_active:
            messages.error(
                request,
                f"Konto {user.email} jest zablokowane – odblokuj je najpierw "
                "(pole „Konto aktywne” w danych konta).",
            )
            return redirect(edit_url)
        if not user.has_usable_password():
            messages.error(
                request,
                f"Konto {user.email} nie ma hasła platformy (logowanie przez zewnętrznego "
                "dostawcę) – nie ma czego resetować.",
            )
            return redirect(edit_url)

        form = PasswordResetForm(data={"email": user.email})
        if not form.is_valid():
            # Nie powinno się zdarzyć – adres pochodzi z naszej własnej bazy – ale ``form.save()``
            # poniżej milczy przy braku dopasowania, więc jawny błąd jest tu bezpieczniejszy niż
            # cisza, z której nie sposób odróżnić „wysłano” od „nic się nie stało”.
            messages.error(request, f"Nie udało się przygotować linku dla adresu {user.email}.")
            return redirect(edit_url)

        # Te same nazwy szablonów i ten sam kontekst, co w ``PasswordResetView`` – patrz docstring
        # klasy. ``from_email`` pusty sięga po domyślnego nadawcę ustawień, tak jak tam.
        form.save(
            use_https=request.is_secure(),
            token_generator=default_token_generator,
            from_email=None,
            email_template_name=SelfServicePasswordResetView.email_template_name,
            subject_template_name=SelfServicePasswordResetView.subject_template_name,
            html_email_template_name=SelfServicePasswordResetView.html_email_template_name,
            request=request,
            extra_email_context={"site_name": service_name(request)},
        )
        # ``diff`` bez adresu i bez tokenu z tego samego powodu, co przy ``2fa.reset`` – audyt
        # czyta też ktoś bez prawa do danych osobowych.
        audit(request.user, "password.reset_sent", user, {}, request=request)
        messages.success(request, f"Link do zmiany hasła został wysłany na adres {user.email}.")
        return redirect(edit_url)


class CoordinatorAccountDeleteView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/<pk>/delete/`` – potwierdzenie i usunięcie cudzego konta.

    Strona GET **musi** powiedzieć, co się stanie, bo skutek zależy od historii konta: konto ze
    śladem w zawodach jest anonimizowane (dane osobowe znikają, pseudonimowy wiersz w wynikach
    zostaje), a konto bez takiego śladu znika w całości. Ukrycie tej różnicy za jednym przyciskiem
    „usuń” znaczyłoby, że koordynator dowiaduje się o niej po fakcie – od uczestnika, którego
    właśnie wypisał z ogłoszonej tabeli.
    """

    def get(self, request, pk: int):
        return self._render(request, _account(request.competition, pk))

    def post(self, request, pk: int):
        user = _account(request.competition, pk)
        email = user.email
        try:
            result = delete_account_by_coordinator(user, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, _account(request.competition, pk), status=exc.status_code)
        if result == "anonymised":
            messages.success(
                request,
                f"Konto {email} zostało zanonimizowane – dane osobowe usunięte, ślad udziału "
                "w zawodach zostaje.",
            )
        else:
            messages.success(
                request, f"Konto {email} zostało usunięte w całości. Adres zwolnił się do rejestracji."
            )
        return redirect(reverse("web:coordinator-accounts"))

    def _render(self, request, user: User, *, status: int = 200):
        footprint = competition_footprint(user)
        participant = participant_for(user, request.competition)
        context = {
            "account": user,
            "role": account_role(user, request.competition, participant),
            "protected": is_protected(user),
            "is_self": user.pk == request.user.pk,
            "footprint": footprint,
            # ``True`` = zostanie anonimizacja, ``False`` = skasowanie wiersza. Nazwa mówi
            # o skutku, nie o implementacji, bo to ona stoi w treści strony.
            "keeps_pseudonymous_row": any(footprint.values()),
            # Patrz ``apps.web.views.account.AccountDeleteView`` – ten sam podział na dwa
            # niezależne zdania (uczestnik / opiekun szkolny), bo konto bywa oboma naraz.
            "has_participant_footprint": any(footprint[key] for key in ("entries", "submissions", "reviews")),
            "has_supervisor_footprint": bool(footprint.get("school_participations")),
            "participant": participant,
        }
        return TemplateResponse(request, DELETE_TEMPLATE, context, status=status)
