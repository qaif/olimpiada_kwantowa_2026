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
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.views.generic import View

from apps.accounts.guardian import guardian_status
from apps.accounts.models import GROUP_COORDINATOR, User
from apps.accounts.profile import (
    competition_footprint,
    delete_account_by_coordinator,
    update_account_by_coordinator,
)
from apps.core.api import DomainError
from apps.web.forms import (
    CoordinatorAccountForm,
    CoordinatorCommitteeForm,
    CoordinatorParticipantForm,
    participant_profile_initial,
)
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/accounts.html"
EDIT_TEMPLATE = "web/coordinator/accounts_edit.html"
DELETE_TEMPLATE = "web/coordinator/accounts_delete.html"

#: Ile kont na stronę. Zwykłe stronicowanie Django i zwykłe odnośniki – bez skryptu, bo lista jest
#: narzędziem do odnalezienia **jednego** konta (wyszukiwarka nad tabelą), a nie do przeglądania
#: całej bazy po kolei.
ACCOUNTS_PER_PAGE = 50

#: Filtr roli w adresie → zawężenie zapytania. Cztery pozycje, bo tyle da się rozstrzygnąć
#: bez zaglądania w grupy każdego wiersza z osobna: uczestnik ma profil uczestnika, członek
#: komitetu – profil komitetu, opiekun szkolny – profil opiekuna, a „pozostałe” nie mają żadnego
#: (konta koordynatorów i konta, które nie dokończyły rejestracji).
ROLE_FILTERS = {
    "participant": lambda qs: qs.filter(participant__isnull=False),
    "committee": lambda qs: qs.filter(committee_member__isnull=False),
    "supervisor": lambda qs: qs.filter(school_supervisor__isnull=False),
    "other": lambda qs: qs.filter(
        participant__isnull=True, committee_member__isnull=True, school_supervisor__isnull=True
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

    ``__in`` z podzapytaniem, a nie ``JOIN`` przez ``memberships``: osoba z dwiema rolami
    w jednym konkursie (recenzent i członek komisji odwoławczej) pojawiłaby się przy złączeniu
    dwa razy, a ``distinct()`` na liście ze stronicowaniem kosztuje sortowanie całego wyniku.
    """
    from apps.accounts.models import Membership

    mine = Membership.objects.for_competition(competition).values("user_id")
    claimed_by_anyone = Membership.objects.values("user_id")
    return User.objects.filter(Q(pk__in=mine) | ~Q(pk__in=claimed_by_anyone))


def is_protected(user: User) -> bool:
    """Czy tego konta nie wolno zmieniać z panelu. Ta sama reguła, co w ``accounts.profile``.

    Powtórzona tutaj **wyłącznie po to, żeby ekran o tym powiedział**: przycisków, których serwis
    i tak nie przepuści, nie ma prawa być na stronie. Rozstrzygający pozostaje serwis – widok nie
    jest bramką, tylko informacją.
    """
    return user.is_superuser or user.groups.filter(name=GROUP_COORDINATOR).exists()


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


def account_role(user: User) -> str:
    """Rola konta w jednym słowie – do kolumny listy i do nagłówka edycji.

    Kolejność rozstrzygania nie jest dowolna: koordynator wygrywa, bo to on decyduje o tym, czy
    konto w ogóle da się tu tknąć; potem komitet, bo profil komitetu niesie uprawnienia do cudzych
    prac. Konto z profilem uczestnika i profilem komitetu naraz jest w tym serwisie niemożliwe
    (rejestracje są rozłączne), ale kolejność i tak musi być zapisana, a nie przypadkowa.
    """
    if is_protected(user):
        return ROLE_COORDINATOR
    member = getattr(user, "committee_member", None)
    if member is not None:
        return ROLE_APPEALS if member.is_appeals_committee else ROLE_COMMITTEE
    if getattr(user, "participant", None) is not None:
        return ROLE_PARTICIPANT
    # Opiekun szkolny na końcu, bo jego rola jest najsłabsza: nie ocenia, nie startuje i widzi
    # wyłącznie tych uczniów, którzy sami wskazali jego adres.
    if getattr(user, "school_supervisor", None) is not None:
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


def account_rows(users) -> list[dict]:
    """Wiersze tabeli – wyłącznie prezentacja, żadnej reguły domenowej."""
    return [
        {
            "user": user,
            "role": account_role(user),
            "status": account_status(user),
            "public_code": getattr(getattr(user, "participant", None), "public_code", ""),
            "protected": is_protected(user),
        }
        for user in users
    ]


class CoordinatorAccountsView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/`` – lista wszystkich kont z wyszukiwarką i filtrem roli."""

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        role = request.GET.get("role") or ""
        # ``select_related`` na obu profilach i ``prefetch_related`` na grupach: rola i kod
        # publiczny stoją w każdym wierszu, więc bez tego strona robiłaby trzy zapytania na konto.
        users = (
            users_for_competition(request.competition)
            .select_related("participant", "committee_member", "school_supervisor")
            .prefetch_related("groups")
            .order_by("email")
        )
        if role in ROLE_FILTERS:
            users = ROLE_FILTERS[role](users)
        if query:
            # Po fragmencie, bo koordynator szuka z pamięci albo ze słuchu („Kowalska, chyba
            # gmail”). Kod publiczny wpada do tego samego pola – jest jedynym identyfikatorem,
            # jaki uczestnik widzi u siebie i podaje przez telefon.
            users = users.filter(
                Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(participant__public_code__icontains=query)
            )
        paginator = Paginator(users, ACCOUNTS_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))
        # Wyszukiwanie i filtr muszą przeżyć przejście na kolejną stronę – bez tego druga strona
        # wyników pokazywałaby wszystkie konta.
        filters = {name: value for name, value in (("q", query), ("role", role)) if value}
        context = {
            "rows": account_rows(page.object_list),
            "page_obj": page,
            "paginator": paginator,
            "query": query,
            "role": role,
            "role_choices": ROLE_CHOICES,
            "filter_query": urlencode(filters),
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


def _account(competition, pk: int) -> User:
    """Konto z tego konkursu albo 404.

    404, a nie 403: konto cudzego organizatora **nie istnieje** dla tego koordynatora, a kod
    odpowiedzi nie ma prawa potwierdzać, że ktoś o takim identyfikatorze się gdziekolwiek zapisał
    (§ 3.6). Zawężenie idzie z querysetu, więc żaden z pięciu ekranów tego modułu nie może go
    pominąć – wszystkie wołają tę funkcję.
    """
    return get_object_or_404(
        users_for_competition(competition).select_related(
            "participant", "committee_member", "school_supervisor"
        ),
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
        participant = getattr(user, "participant", None)
        if participant is not None:
            forms["participant"] = CoordinatorParticipantForm(
                data, prefix="participant", initial=participant_profile_initial(participant)
            )
        member = getattr(user, "committee_member", None)
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
        participant = getattr(user, "participant", None)
        context = {
            "account": user,
            "role": account_role(user),
            "status_label": account_status(user),
            "protected": is_protected(user),
            # Stan zgody opiekuna – do odczytu. Reguła jest jedna dla panelu uczestnika
            # i dla tego ekranu (``apps.accounts.guardian.guardian_status``).
            "guardian": (
                guardian_status(participant) if participant is not None else {"state": "not_required"}
            ),
            "participant": participant,
            "committee": getattr(user, "committee_member", None),
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
        context = {
            "account": user,
            "role": account_role(user),
            "protected": is_protected(user),
            "is_self": user.pk == request.user.pk,
            "footprint": footprint,
            # ``True`` = zostanie anonimizacja, ``False`` = skasowanie wiersza. Nazwa mówi
            # o skutku, nie o implementacji, bo to ona stoi w treści strony.
            "keeps_pseudonymous_row": any(footprint.values()),
            "participant": getattr(user, "participant", None),
        }
        return TemplateResponse(request, DELETE_TEMPLATE, context, status=status)
