"""Menu boczne panelu koordynatora: struktura, adresy i liczniki „do zrobienia”.

Menu jest opisane **w Pythonie**, a nie w szablonie, z trzech powodów:

- adresy bywają **niepewne**. Część ekranów panelu powstaje równolegle (zgłoszenia, ogłoszenia),
  a ``{% url %}`` w szablonie nie ma trybu „jeśli istnieje” – brak wpisu w urlconfie wywracałby
  każdą stronę panelu. Tutaj nazwa adresu jest **listą kandydatów**, a nierozwiązana pozycja
  po prostu znika z menu (``NoReverseMatch``),
- pozycja aktywna wynika z **nazwy widoku**, nie ze ścieżki. Porównywanie ``request.path``
  z adresem zapalałoby „Etapy” także na ekranach, które mają ten sam przedrostek, a nie zapalało
  niczego na ekranach z parametrem w adresie,
- liczniki przy pozycjach („jest 5 spraw”) muszą kosztować **jedno zapytanie agregujące każdy**
  i nie liczyć się od nowa na każdej stronie panelu – stąd wspólna pamięć podręczna na 60 s.

Menu **nie jest zabezpieczeniem**: dostęp rozstrzyga ``CoordinatorRequiredMixin``. Tu decyduje
się wyłącznie o tym, czego nie pokazywać, żeby nie prowadzić do ekranu, którego nie ma.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify

#: Wspólny przedrostek i czas życia liczników. Minuta, bo to rytm pracy koordynatora: badge ma
#: powiedzieć „jest tu coś do zrobienia”, a nie służyć za zegar. Krótszy czas zamieniłby każde
#: wejście na dowolny ekran panelu w cztery zapytania agregujące.
COUNTERS_CACHE_KEY = "web:coordinator-nav-counters"
COUNTERS_CACHE_SECONDS = 60


def counters_cache_key(competition) -> str:
    """Klucz pamięci podręcznej liczników – **z identyfikatorem konkursu**.

    Jeden klucz dla całej instalacji byłby tu wyciekiem, i to najbrzydszego rodzaju: koordynator
    konkursu A, który wszedł do panelu pierwszy, zapisywałby swoje liczby na minutę dla
    koordynatora konkursu B. Nikt by tego nie zauważył (badge to sama liczba), a po kliknięciu
    kolejka byłaby pusta – czyli objawem byłoby „licznik kłamie”, a przyczyną cudze dane.

    ``None`` (host bez konkursu) dostaje własny kubełek, a nie kubełek pierwszego z brzegu.
    """
    return f"{COUNTERS_CACHE_KEY}:{getattr(competition, 'pk', None) or 'none'}"


#: Puste liczniki – wartość awaryjna, gdy baza nie odpowiada. Menu ma się wtedy narysować bez
#: badge'ów, a nie wywrócić stronę, na którą koordynator wszedł właśnie po to, żeby coś naprawić.
EMPTY_COUNTERS: dict[str, int] = {
    "moderation": 0,
    "activations": 0,
    "committee": 0,
    "issues": 0,
    "tickets": 0,
    "forum": 0,
}


@dataclass(frozen=True)
class Item:
    """Pojedyncza pozycja menu.

    ``names`` jest listą kandydatów, a nie jedną nazwą: ekran bywa dopiero dopisywany do
    urlconfa (albo nosi inną nazwę, niż zakładaliśmy), a menu ma to przeżyć bez awarii.

    ``match`` mówi, przy których widokach pozycja świeci się jako aktywna. Wzorzec zakończony
    myślnikiem jest **przedrostkiem** (``coordinator-account-`` łapie edycję i usunięcie konta),
    każdy inny musi się zgadzać **dokładnie**. Rozróżnienie nie jest kosmetyką: nazwy wszystkich
    widoków panelu zaczynają się od ``coordinator``, więc sam przedrostek zapalałby „Pulpit”
    na każdym ekranie naraz.
    """

    label: str
    names: tuple[str, ...]
    args: tuple = ()
    match: tuple[str, ...] = ()
    badge: str = ""
    query: str = ""
    fragment: str = ""
    children: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Group:
    """Nagłówek sekcji menu wraz z pozycjami. Sekcja bez ani jednej pozycji się nie renderuje."""

    label: str
    items: tuple[Item, ...] = field(default_factory=tuple)


def resolve(names: tuple[str, ...], args: tuple = ()) -> str | None:
    """Pierwszy adres z listy kandydatów, który da się złożyć. ``None`` = nie ma takiego ekranu."""
    for name in names:
        try:
            return reverse(name, args=args)
        except NoReverseMatch:
            continue
    return None


def attention_counters(stage_ids: list[int] | None = None, competition=None) -> dict[str, int]:
    """Cztery liczby „czeka na koordynatora”, po jednym zapytaniu agregującym na każdą.

    Wartość jest wspólna dla całego panelu **jednego konkursu** i żyje 60 s
    (``counters_cache_key``): te same liczby stoją w menu na każdej stronie i na kafelkach
    pulpitu, więc liczenie ich przy każdym renderowaniu byłoby czterema zapytaniami za każde
    kliknięcie w panelu.

    Zakresem zgłoszeń o pracach i moderacji jest **bieżąca edycja**: ekrany, na które prowadzą
    badge, też pokazują bieżącą edycję, a licznik obejmujący zeszłoroczne sprawy wskazywałby
    liczbę, której po kliknięciu nie widać. Zgłoszenia do organizatora (``tickets``) są jedynym
    wyjątkiem – przychodzą także od osób bez konta i bez wpisu w jakimkolwiek etapie.

    Licznik aktywacji liczy konta widoczne na liście kont – jedna definicja „czyje to konto”
    dla badge'a, kafelka i kolejki (``apps.web.views.coordinator_accounts.users_for_competition``).
    Dwie definicje znaczyłyby badge prowadzący do kolejki z inną liczbą wierszy.

    ``competition=None`` znaczy „konkurs z kontekstu” – ta sama reguła i ta sama funkcja, co
    w ``current_edition`` (``apps.competitions.scoping.resolve_competition``). Widok podaje
    konkurs wprost (``request.competition``); odwrót jest dla wołających spoza żądania.
    """
    from apps.competitions.scoping import resolve_competition

    try:
        competition = resolve_competition(competition)
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli konkursów
        return dict(EMPTY_COUNTERS)
    key = counters_cache_key(competition)
    cached = cache.get(key)
    if cached is not None:
        return cached
    from apps.accounts.models import CommitteeMember, CommitteeStatus
    from apps.forum.services import moderation_count
    from apps.grading.issues import open_issue_count
    from apps.submissions.models import Submission, SubmissionStatus
    from apps.support.services import open_ticket_count
    from apps.web.views.coordinator_accounts import users_for_competition

    if stage_ids is None:
        stage_ids = [stage.pk for stage in current_stages(competition)]
    try:
        counters = {
            "moderation": Submission.objects.for_competition(competition)
            .filter(status=SubmissionStatus.MODERATION, entry__stage_id__in=stage_ids)
            .count(),
            "activations": users_for_competition(competition)
            .filter(is_active=False, email_verified_at__isnull=True)
            .count(),
            "committee": CommitteeMember.objects.for_competition(competition)
            .filter(status=CommitteeStatus.PENDING)
            .count(),
            "issues": open_issue_count(stage_ids),
            "tickets": open_ticket_count(competition),
            # Konkurs bez forum oddaje zero **bez ani jednego zapytania** (``has_feature`` czyta
            # pole wiersza, który już trzymamy), więc ta pozycja nie zmienia kosztu panelu
            # w konkursie z domyślnymi przełącznikami.
            "forum": moderation_count(competition),
        }
    except DatabaseError:  # pragma: no cover - baza bez migracji
        return dict(EMPTY_COUNTERS)
    cache.set(key, counters, COUNTERS_CACHE_SECONDS)
    return counters


def invalidate_counters(competition=None) -> None:
    """Zapomnij liczniki. Wołane w testach i wszędzie tam, gdzie minuta opóźnienia przeszkadza.

    Bez argumentu czyści kubełek konkursu z kontekstu **oraz** kubełek „bez konkursu”. Wołający
    (test, akcja panelu) zwykle nie wie, ilu konkursów dotyczy jego zmiana, a nadmiarowe
    unieważnienie kosztuje jedno przeliczenie liczników – przeoczone kosztowałoby minutę
    kłamiącego badge'a.
    """
    if competition is not None:
        cache.delete(counters_cache_key(competition))
        return
    from apps.competitions.scoping import resolve_competition

    cache.delete_many([counters_cache_key(None), counters_cache_key(resolve_competition(None))])


def current_stages(competition=None) -> list:
    """Etapy bieżącej edycji **tego konkursu** w kolejności kalendarza.

    Pusta lista, gdy edycji nie ma – i tak samo, gdy nie wiadomo, o który konkurs chodzi:
    ``current_edition`` oddaje wtedy ``None``, bo w bazie wielokonkursowej pierwsza edycja
    z brzegu jest cudza.
    """
    from apps.competitions.models import Stage
    from apps.competitions.services import current_edition

    try:
        edition = current_edition(competition)
        if edition is None:
            return []
        return list(Stage.objects.filter(edition=edition).order_by("opens_at", "id"))
    except DatabaseError:  # pragma: no cover - baza bez migracji
        return []


def focus_stage(stages: list):
    """Etap, o którym „jest teraz mowa” – dla pozycji menu, które bez etapu nie istnieją.

    Kalibracja, podobieństwo, symulacja, dyplomy i skala punktacji dotyczą **jednego** etapu,
    a menu jest jedno. Bierzemy pierwszy etap jeszcze niezamknięty (to ten, w którym trwa praca),
    a gdy wszystkie są zamknięte – ostatni, bo po zamknięciu czyta się właśnie jego wyniki.
    """
    if not stages:
        return None
    for stage in stages:
        if stage.closed_at is None:
            return stage
    return stages[-1]


def stage_items(stages: list, competition=None) -> tuple[Item, ...]:
    """Jedna pozycja na etap, a pod nią cztery ekrany, po które sięga się w trakcie zawodów.

    Etap w formie rozmowy nie ma zadań (``create_problem`` odmawia), więc zamiast „Zadania”
    dostaje „Rozmowy” – odnośnik do ekranu, na którym da się cokolwiek zrobić. Etap w formie
    testu online z tego samego powodu dostaje „Test online”: nie ma w nim zadań do oddania ani
    terminów rozmów, a cała praca koordynatora toczy się wokół arkusza pytań.

    ``competition`` jest tu wyłącznie po to, żeby odczytać przełączniki (etap 2 § 2.1 punkt 1):
    dzieci etapu rosną tylko za flagą, a konkurs z domyślnymi flagami dostaje **te same cztery**
    pozycje, co przed etapem 2. Domyślne ``None`` znaczy „host bez rozstrzygniętego konkursu” –
    wtedy żadnej flagi nie ma, więc i żadnej dodatkowej pozycji. Żadnego zapytania tu nie ma:
    ``has_feature`` czyta pole wiersza, który wołający już trzyma.
    """
    items = []
    for stage in stages:
        if stage.is_interview:
            first = Item(
                "Rozmowy",
                ("web:coordinator-stage-interviews",),
                (stage.pk,),
                ("coordinator-stage-interviews",),
            )
        elif stage.is_quiz:
            first = Item(
                "Test online",
                ("web:coordinator-stage-quiz",),
                (stage.pk,),
                ("coordinator-stage-quiz", "coordinator-stage-quiz-"),
            )
        else:
            first = Item(
                "Zadania",
                ("web:coordinator-stage-problems",),
                (stage.pk,),
                ("coordinator-stage-problems", "coordinator-problem", "coordinator-problem-"),
            )
        children: tuple[Item, ...] = (
            first,
            Item(
                "Przydziały i oceny",
                ("web:coordinator-stage-assignments",),
                (stage.pk,),
                ("coordinator-stage-assignments",),
            ),
            Item(
                "Postęp",
                ("web:coordinator-stage-progress",),
                (stage.pk,),
                ("coordinator-stage-progress",),
            ),
            Item(
                "Wyniki",
                ("web:coordinator-stage-results",),
                (stage.pk,),
                ("coordinator-stage-results",),
            ),
        )
        if competition is not None and competition.has_feature("onsite_logistics"):
            # Logistyka etapu stacjonarnego (§ 1.5.2, T42). Dwa ekrany dotyczą **jednego** etapu –
            # deklaracje przyjazdu i lista obecności – więc stoją jako jego dzieci, a nie jako
            # pozycje sekcji. Bramka jest tą samą bramką, co u widoków: przy wyłączonej fladze
            # oddają 404, a pozycja prowadząca donikąd byłaby gorsza niż jej brak.
            children += (
                Item(
                    "Przyjazdy i potrzeby",
                    ("web:coordinator-stage-logistics",),
                    (stage.pk,),
                    ("coordinator-stage-logistics", "coordinator-stage-logistics-"),
                ),
                Item(
                    "Obecność",
                    ("web:coordinator-stage-attendance",),
                    (stage.pk,),
                    ("coordinator-stage-attendance",),
                ),
            )
        items.append(
            Item(
                stage.display_name,
                ("web:coordinator-stage-edit",),
                (stage.pk,),
                ("coordinator-stage-edit",),
                children=children,
            )
        )
    return tuple(items)


def groups(stages: list, competition=None) -> list[Group]:
    """Pełna struktura menu – jedyne miejsce, w którym zapisany jest podział panelu na sekcje."""
    stage = focus_stage(stages)
    stage_args = (stage.pk,) if stage is not None else ()
    quality: tuple[Item, ...] = (
        Item(
            "Moderacja",
            ("web:coordinator-moderation",),
            match=("coordinator-moderation",),
            badge="moderation",
        ),
        Item(
            "Zgłoszone problemy",
            ("web:coordinator-issues",),
            match=("coordinator-issues", "coordinator-issue-"),
            badge="issues",
        ),
    )
    if stage is not None:
        quality += (
            Item(
                "Kalibracja recenzentów",
                ("web:coordinator-stage-calibration",),
                stage_args,
                ("coordinator-stage-calibration",),
            ),
            Item(
                "Podobieństwo rozwiązań",
                ("web:coordinator-stage-similarity",),
                stage_args,
                ("coordinator-stage-similarity", "coordinator-similarity-"),
            ),
        )
    if stage is not None and competition is not None and competition.has_feature("process_editor"):
        # Punkty z rozmowy (§ 1.2.3, T34) – w „Ocenianiu”, bo to jest **ocena** etapu w formie
        # rozmowy, a nie jego konfiguracja. Warunek etapu jest ten sam, co u kalibracji: ekran
        # dotyczy jednego etapu, a menu jest jedno.
        quality += (
            Item(
                "Punkty z rozmowy",
                ("web:coordinator-stage-interview-scores",),
                stage_args,
                ("coordinator-stage-interview-scores",),
            ),
        )
    if stage is not None and competition is not None and competition.has_feature("reviewer_roles"):
        # Nazwane role recenzenckie etapu (§ 1.2.7, T34). Sekcja „Ocenianie” zgodnie z mapą
        # ekranów (§ 2.2). Wzorzec ``coordinator-reviewer-role-`` łapie czynności na wierszu
        # (zmiana, usunięcie), które adresem stoją poza gałęzią etapu.
        quality += (
            Item(
                "Role recenzenckie",
                ("web:coordinator-stage-reviewer-roles",),
                stage_args,
                ("coordinator-stage-reviewer-roles", "coordinator-reviewer-role-"),
            ),
        )
    if competition is not None and competition.has_feature("ai_grading"):
        # Ocena AI (prośba organizatora z 24.09.2026). W „Ocenianiu”, bo to jest narzędzie
        # komitetu przy ocenie prac, a nie konfiguracja konkursu – mimo że ekran niesie klucz API.
        # Bez warunku etapu: klucz, model i limit wydatków dotyczą całego konkursu, a zlecenia
        # stoją na kartach zadań. Wzorzec ``coordinator-ai-`` łapie też ekran potwierdzenia
        # zlecenia, który adresem wisi pod zadaniem.
        quality += (
            Item(
                "Ocena AI",
                ("web:coordinator-ai-grading",),
                match=("coordinator-ai-grading", "coordinator-ai-"),
            ),
        )
    reports: tuple[Item, ...] = (
        Item(
            "Eksport danych", ("web:coordinator-export",), match=("coordinator-export", "coordinator-export-")
        ),
        Item("Audyt", ("web:coordinator-audit",), match=("coordinator-audit",)),
    )
    settings_items: tuple[Item, ...] = ()
    if competition is not None and competition.has_feature("competition_settings_page"):
        # Pierwsza pozycja sekcji, bo opisuje **konkurs**, a reszta sekcji – jego rocznik.
        # Za przełącznikiem z tego samego powodu, co sam ekran: konkurs z domyślnymi flagami ma
        # mieć menu **bajt w bajt** takie, jak przed wielokonkursowością (§ 0.5, § 6 T5 –
        # „czego nie wolno zmienić: rozwijanych sekcji nawigacji”). Pozycja prowadząca do 404
        # byłaby zresztą gorsza niż jej brak.
        settings_items += (
            Item(
                "Ustawienia konkursu",
                ("web:coordinator-competition",),
                match=("coordinator-competition",),
            ),
        )
    if competition is not None and competition.has_feature("competition_creation"):
        # Zakładanie kolejnego konkursu w subdomenie platformy. **Zaraz pod** „Ustawieniami
        # konkursu”, bo obie pozycje dotyczą konkursu jako całości, a nie jego rocznika — i bo
        # tamta odpowiada na pytanie „jaki jest mój konkurs”, a ta na „jak założyć następny”.
        # Bramką jest **sama flaga konkursu**, tak samo jak przy czternastu pozycjach wyżej, a nie
        # flaga plus ustawienie instalacji (``PLATFORM_SUBDOMAINS``), którego ekran wymaga
        # dodatkowo. Rozdział jest celowy: o tym, co widzi koordynator, ma rozstrzygać jedna
        # rzecz, którą widać w ``feature_flags`` jego konkursu — a włączenie tej flagi na
        # instalacji bez subdomen jest błędem operatora (ekran odpowie wtedy 404), a nie stanem,
        # w którym menu ma się domyślać, co operator naprawdę miał na myśli.
        settings_items += (
            Item(
                "Nowy konkurs",
                ("web:coordinator-competition-new",),
                match=("coordinator-competition-new", "coordinator-competitions"),
            ),
        )
    if competition is not None and competition.has_feature("per_competition_consents"):
        # Zgody konkursu (etap 2 § 2.2, T11). Bramka jest tą samą bramką, co u ekranu: przy
        # wyłączonej fladze widok oddaje 404, więc pozycja w menu prowadziłaby donikąd. Stoi
        # zaraz pod „Ustawieniami konkursu”, bo opisuje **konkurs** – to, o co pyta w formularzu
        # rejestracji – a nie jego rocznik.
        settings_items += (
            Item(
                "Zgody konkursu",
                ("web:coordinator-consents",),
                match=("coordinator-consents", "coordinator-consent-"),
            ),
        )
    if competition is not None and competition.has_feature("custom_regions"):
        # Podział terytorialny konkursu (§ 1.4, T20). W „Ustawieniach”, bo opisuje **konkurs**,
        # a nie jego rocznik: regiony przeżywają edycję. Bramka jest tą samą bramką, co u ekranu –
        # przy wyłączonej fladze widok oddaje 404 i menu prowadziłoby donikąd.
        settings_items += (
            Item(
                "Regiony",
                ("web:coordinator-regions",),
                match=("coordinator-regions", "coordinator-region"),
            ),
        )
    if competition is not None and competition.has_feature("categories"):
        # Kategorie uczestników (§ 1.2.4, T27) – własne progi i osobne rankingi, czyli
        # konfiguracja konkursu, a nie zestawienie. Wzorce są wypisane parami (lista i wiersz,
        # oba z przedrostkiem), bo ``coordinator-categories`` i ``coordinator-category`` są
        # dwiema różnymi nazwami, a wzorzec bez myślnika porównuje się na równość.
        settings_items += (
            Item(
                "Kategorie",
                ("web:coordinator-categories",),
                match=(
                    "coordinator-categories",
                    "coordinator-categories-",
                    "coordinator-category",
                    "coordinator-category-",
                ),
            ),
        )
    settings_items += (
        Item(
            "Rejestracja uczestników",
            ("web:coordinator-registration",),
            match=("coordinator-registration",),
        ),
        Item(
            "Wydarzenia linii czasu",
            ("web:coordinator-events",),
            match=("coordinator-events", "coordinator-event-"),
        ),
        # Klucze API i webhooki. W „Ustawieniach”, a nie w „Raportach”: to jest konfiguracja
        # dostępu systemów zewnętrznych, czyli decyzja o tym, kto co widzi – nie zestawienie.
        Item(
            "Integracje",
            ("web:coordinator-integrations",),
            match=("coordinator-integrations", "coordinator-integrations-"),
        ),
        # Przekazywanie rozwiązań na skrzynkę organizatora (prośba organizatora z 20.09.2026).
        # **Bez flagi**, w odróżnieniu od kilkunastu pozycji wyżej: to nie jest zdolność systemu
        # wielokonkursowego, którą włącza operator, tylko ekran zamówiony wprost przez organizatora
        # Konkursu #1 – i to on ma go zobaczyć bez proszenia kogokolwiek o przestawienie flagi.
        # Zamiast flagi bramką jest samo pole: puste znaczy „nie przekazujemy” i tak zaczyna każdy
        # konkurs, więc dopisanie pozycji nie zmienia ani jednego listu.
        #
        # Stoi zaraz za „Integracjami”, bo odpowiada na to samo pytanie co one: co i dokąd wychodzi
        # z serwisu na zewnątrz.
        Item(
            "Przekazywanie rozwiązań",
            ("web:coordinator-submission-forwarding",),
            match=("coordinator-submission-forwarding",),
        ),
        # Slider sponsorów w menu (uwaga organizatora z 21.09.2026). Zaraz za „Przekazywaniem
        # rozwiązań”, bo obie pozycje doszły tego samego dnia i z tego samego powodu nie mają
        # flagi: to są ekrany zamówione wprost dla Konkursu #1, a nie zdolności systemu
        # wielokonkursowego, które operator włącza konkursowi z osobna.
        Item(
            "Slider sponsorów",
            ("web:coordinator-sponsor-slider",),
            match=("coordinator-sponsor-slider",),
        ),
        # Plakaty do pobrania i statystyki ich pobrań (prośba organizatora z 23.09.2026). Zaraz za
        # sliderem sponsorów, bo obie pozycje dotyczą tego samego – promocji olimpiady na stronie –
        # i z tego samego powodu nie mają flagi: ekran zamówił organizator Konkursu #1. Bramką jest
        # sama treść: bez opublikowanego plakatu strona ``/plakaty/`` i odnośnik w stopce nie istnieją.
        Item(
            "Plakaty do pobrania",
            ("web:coordinator-posters",),
            match=("coordinator-posters", "coordinator-posters-", "coordinator-poster-"),
        ),
    )
    if competition is not None and competition.has_feature("institution_types"):
        # Profil rejestracji (§ 1.3.4, T23) – co wolno wpisać w formularzu zgłoszeniowym. Stoi
        # zaraz pod „Rejestracją uczestników”, bo jest jej drugą stroną: tamta mówi, kiedy
        # rejestracja jest otwarta, ta – o co pyta.
        settings_items += (
            Item(
                "Profil rejestracji",
                ("web:coordinator-registration-profile",),
                match=("coordinator-registration-profile",),
            ),
        )
    if competition is not None and competition.has_feature("custom_school_directory"):
        # Własny słownik placówek organizatora (§ 1.3.3, T23). W „Ustawieniach”, bo to jest
        # wykaz, z którego korzysta formularz rejestracji – konfiguracja, a nie zestawienie.
        settings_items += (
            Item(
                "Słownik placówek",
                ("web:coordinator-institutions",),
                match=(
                    "coordinator-institutions",
                    "coordinator-institutions-",
                    "coordinator-institution",
                    "coordinator-institution-",
                ),
            ),
        )
    if competition is not None and competition.has_feature("onsite_logistics"):
        # Miejsca zawodów (§ 1.5.2, T42) – spis sal i ośrodków przeżywa edycję, więc to jest
        # konfiguracja konkursu, a nie ekran etapu. Same przyjazdy i obecność stoją jako dzieci
        # etapu (``stage_items``), bo dotyczą jednego terminu.
        settings_items += (
            Item(
                "Miejsca zawodów",
                ("web:coordinator-venues",),
                match=("coordinator-venues", "coordinator-venues-", "coordinator-venue"),
            ),
        )
    if stage is not None:
        reports += (
            Item(
                "Symulacja kwalifikacji",
                ("web:coordinator-stage-simulation",),
                stage_args,
                ("coordinator-stage-simulation",),
            ),
            Item(
                "Dyplomy",
                ("web:coordinator-stage-certificates",),
                stage_args,
                # Wzorce są wyliczone, a nie podane przedrostkiem ``coordinator-certificate-``:
                # ten przedrostek łapie także ekrany szablonów, które są osobną pozycją menu,
                # i obie świeciłyby się naraz.
                (
                    "coordinator-stage-certificates",
                    "coordinator-certificate-issue",
                    "coordinator-certificate-download",
                    "coordinator-certificates-all",
                ),
            ),
        )
        settings_items += (
            Item(
                "Skala punktacji",
                ("web:coordinator-stage-scale",),
                stage_args,
                ("coordinator-stage-scale",),
            ),
        )
        if competition is not None and competition.has_feature("weighted_scoring"):
            # Porządek rozstrzygania remisów (§ 1.2.6, T34) – **zaraz pod** „Skalą punktacji”,
            # bo to jest druga połowa tej samej decyzji: tamta mówi, ile punktów, ta – kto jest
            # wyżej przy równej liczbie. Warunek etapu bierze się stąd, że oba ekrany dotyczą
            # jednego etapu.
            settings_items += (
                Item(
                    "Rozstrzyganie remisów",
                    ("web:coordinator-stage-tie-breaks",),
                    stage_args,
                    ("coordinator-stage-tie-breaks", "coordinator-tie-break-"),
                ),
            )
    reports += (
        # Trzy ekrany dyplomów spoza pojedynczego etapu: wygląd dokumentu, obecność na warsztatach
        # i zaświadczenia dla opiekunów. Stoją w „Raportach” obok „Dyplomów”, bo o tę sekcję
        # koordynator zahacza, szukając czegokolwiek związanego z dokumentami – a każdy z nich
        # dotyczy całej edycji, nie etapu, więc w sekcji „Etapy” nie miałby czego dopasować.
        Item(
            "Dyplomy: szablony",
            ("web:coordinator-certificate-templates",),
            match=("coordinator-certificate-templates", "coordinator-certificate-template-"),
        ),
    )
    if competition is not None and competition.has_feature("document_templates"):
        # Teksty dokumentów (etap 2 § 2.2, T13) – **zaraz za** „Dyplomami: szablony”, bo obie
        # pozycje dotyczą tego samego papieru i różnią się tym, czego dotyczą: tamta wyglądem
        # (grafika, tło), ta brzmieniem (tytuł, zdanie główne, linia podpisu). Bramka jest tą
        # samą bramką, co u ekranu: przy wyłączonej fladze widok oddaje 404.
        #
        # Wzorce dopasowania są **dwa i oba dokładne**, a nie jeden przedrostek: ekran rodzaju
        # nazywa się ``coordinator-document`` (rodzaj jedzie w adresie swoją wartością, nie
        # identyfikatorem wiersza), więc przedrostek ``coordinator-document-`` nie łapałby go
        # wcale, a sam ``coordinator-document`` nie łapałby listy ``coordinator-documents``
        # (wzorzec bez myślnika na końcu porównuje się na równość – patrz :class:`Item`).
        reports += (
            Item(
                "Szablony dokumentów",
                ("web:coordinator-documents",),
                match=("coordinator-documents", "coordinator-document"),
            ),
        )
    if competition is not None and competition.has_feature("fees"):
        # Wpisowe (§ 1.5.1, T42) – cennik, należności i dokumenty rozliczeniowe. W „Raportach”
        # zgodnie z mapą ekranów (§ 2.2): koordynator przychodzi tu po zestawienie „kto zapłacił”.
        # Wzorce łapią zarówno listy (``coordinator-fees``, ``coordinator-fees-*``), jak
        # i czynności na jednej należności (``coordinator-fee-*``).
        reports += (
            Item(
                "Wpisowe",
                ("web:coordinator-fees",),
                match=("coordinator-fees", "coordinator-fees-", "coordinator-fee-"),
            ),
        )
    reports += (
        Item(
            "Obecność na warsztatach",
            ("web:coordinator-workshop-attendance",),
            match=("coordinator-workshop-attendance", "coordinator-workshop-certificates"),
        ),
        Item(
            "Zaświadczenia opiekunów",
            ("web:coordinator-supervisors",),
            match=("coordinator-supervisors", "coordinator-supervisor-"),
        ),
        Item("Retencja danych", ("web:coordinator-retention",), match=("coordinator-retention",)),
        Item(
            "Rejestr czynności",
            ("web:coordinator-processing-register",),
            match=("coordinator-processing-register",),
        ),
    )
    communication: tuple[Item, ...] = (
        Item("Komunikaty", ("web:coordinator-messages",), match=("coordinator-messages",)),
        # Ekrany budowane równolegle: kolejka zgłoszeń od ludzi i ogłoszenia serwisu.
        # Nazwa adresu jest tu **przewidywana**, więc każda pozycja ma kilku kandydatów
        # i znika z menu, dopóki żaden z nich nie istnieje w urlconfie.
        Item(
            "Zgłoszenia",
            ("web:coordinator-support", "web:coordinator-tickets"),
            match=("coordinator-support", "coordinator-support-", "coordinator-tickets"),
            badge="tickets",
        ),
        Item(
            "Ogłoszenia",
            ("web:coordinator-announcements", "web:coordinator-announcement-list"),
            match=("coordinator-announcements", "coordinator-announcement-"),
        ),
    )
    if competition is not None and competition.has_feature("participant_forum"):
        # Moderacja forum (prośba organizatora z 21.09.2026). W „Komunikacji”, bo to jest rozmowa
        # z uczestnikami – obok komunikatów i zgłoszeń – a nie zestawienie ani konfiguracja
        # zawodów. Bramka jest tą samą bramką, co u ekranu: przy wyłączonej fladze widoki oddają
        # 404, więc pozycja prowadziłaby donikąd.
        #
        # Wzorzec jest **jeden przedrostek**, bo wszystkie pięć ekranów moderacji nazywa się
        # ``coordinator-forum…`` i mają świecić tę samą pozycję: kolejka, spis wątków, wątek,
        # działy i ustawienia są jedną sprawą rozłożoną na pięć adresów.
        communication += (
            Item(
                "Forum uczestników",
                ("web:coordinator-forum",),
                match=("coordinator-forum", "coordinator-forum-"),
                badge="forum",
            ),
        )
    people_items: tuple[Item, ...] = ()
    if competition is not None and competition.has_feature("team_entries"):
        # Drużyny (§ 1.2.3, T34) – **na końcu** sekcji „Uczestnicy i konta”, bo cztery pozycje
        # przed nią są dzisiejszym menu i mają zostać w tej kolejności co do bajtu (§ 2.1 p. 1).
        people_items += (
            Item(
                "Drużyny",
                ("web:coordinator-teams",),
                match=("coordinator-teams", "coordinator-team", "coordinator-team-"),
            ),
        )
    stage_group: tuple[Item, ...] = stage_items(stages, competition)
    if competition is not None and competition.has_feature("process_editor"):
        # Edytor przebiegu (§ 1.2, T27). „Przebieg edycji” stoi **nad** listą etapów, bo opisuje
        # tor jako całość – to z niego biorą się etapy niżej. „Komponenty etapu” dotyczą etapu
        # w ognisku uwagi i dlatego stoją na końcu sekcji, tak samo jak reszta pozycji etapowych
        # poza sekcją „Etapy”.
        stage_group = (
            Item(
                "Przebieg edycji",
                ("web:coordinator-pipeline",),
                match=("coordinator-pipeline", "coordinator-pipeline-"),
            ),
            *stage_group,
        )
        if stage is not None:
            stage_group += (
                Item(
                    "Komponenty etapu",
                    ("web:coordinator-stage-components",),
                    stage_args,
                    (
                        "coordinator-stage-components",
                        "coordinator-stage-component",
                        "coordinator-stage-component-",
                    ),
                ),
            )
    return [
        Group("Pulpit", (Item("Co wymaga uwagi", ("web:coordinator",), match=("coordinator",)),)),
        Group("Etapy", stage_group),
        Group("Ocenianie", quality),
        Group(
            "Uczestnicy i konta",
            (
                # Listy uczestników i opiekunów to ta sama lista kont z ustawionym filtrem roli –
                # osobny ekran powtarzałby jej wyszukiwarkę, stronicowanie i kolumny.
                Item("Uczestnicy", ("web:coordinator-accounts",), query="role=participant"),
                Item(
                    "Wszystkie konta",
                    ("web:coordinator-accounts",),
                    match=("coordinator-accounts", "coordinator-account-"),
                ),
                Item("Opiekunowie szkolni", ("web:coordinator-accounts",), query="role=supervisor"),
                Item(
                    "Aktywacje",
                    ("web:coordinator-activations",),
                    match=("coordinator-activations",),
                    badge="activations",
                ),
                *people_items,
            ),
        ),
        Group(
            "Komitet",
            (
                # Spis ludzi i karta jednej osoby to gałąź ``members/``; ``committee/`` jest
                # gałęzią **czynności** na członku, więc „Zatwierdzenia i zaproszenia” prowadzi
                # tam, a „Członkowie” – do spisu. Gdyby spisu jeszcze nie było, pozycja spada na
                # ekran zatwierdzeń, bo tam też widać skład.
                Item(
                    "Członkowie",
                    ("web:coordinator-members", "web:coordinator-committee"),
                    match=("coordinator-members", "coordinator-member"),
                ),
                Item(
                    "Zatwierdzenia",
                    ("web:coordinator-committee",),
                    match=("coordinator-committee",),
                    badge="committee",
                ),
                Item("Zaproszenia", ("web:coordinator-committee",), fragment="zaproszenia"),
                Item("Województwa", ("web:coordinator-committee",), fragment="wojewodztwa"),
            ),
        ),
        Group("Komunikacja", communication),
        Group("Raporty", reports),
        Group("Ustawienia", settings_items),
    ]


def _is_active(item: Item, url_name: str, url_kwargs: dict) -> bool:
    """Czy pozycja opisuje ekran, który właśnie oglądamy.

    Dwa warunki naraz: nazwa widoku musi pasować do jednego ze wzorców **i** – gdy pozycja
    dotyczy konkretnego etapu – etap w adresie musi być tym samym etapem. Bez tego drugiego
    warunku wszystkie trzy etapy w menu świeciłyby się jednocześnie.
    """
    matched = any(
        url_name.startswith(pattern) if pattern.endswith("-") else url_name == pattern
        for pattern in item.match
    )
    if not matched:
        return False
    if not item.args:
        return True
    current = url_kwargs.get("stage_id") or url_kwargs.get("pk")
    return current is None or str(current) == str(item.args[0])


def _render_item(item: Item, counters: dict, url_name: str, url_kwargs: dict) -> dict | None:
    """Pozycja gotowa dla szablonu, albo ``None`` – gdy jej adresu nie ma w urlconfie."""
    url = resolve(item.names, item.args)
    if url is None:
        return None
    if item.query:
        url = f"{url}?{item.query}"
    if item.fragment:
        url = f"{url}#{item.fragment}"
    children = [
        rendered
        for child in item.children
        if (rendered := _render_item(child, counters, url_name, url_kwargs)) is not None
    ]
    return {
        "label": item.label,
        "url": url,
        "active": _is_active(item, url_name, url_kwargs),
        # Zero nie jest informacją – badge ma znaczyć „jest tu coś do zrobienia”, więc przy zerze
        # go nie ma. Kropka przy pozycji, pod którą nic nie czeka, uczyłaby ignorować kropki.
        "badge": counters.get(item.badge) or None,
        "children": children,
        "open": any(child["active"] for child in children),
    }


def navigation(request) -> dict:
    """Kontekst menu dla szablonu ``web/coordinator/_nav.html``.

    Wołane raz na stronę panelu (znacznik ``{% coordinator_nav %}``). Zapytania: jedno po etapy
    bieżącej edycji i – raz na minutę – po cztery liczniki.
    """
    match = getattr(request, "resolver_match", None)
    url_name = getattr(match, "url_name", "") or ""
    url_kwargs = getattr(match, "kwargs", None) or {}
    competition = getattr(request, "competition", None)
    stages = current_stages(competition)
    counters = attention_counters([stage.pk for stage in stages], competition)
    rendered = []
    for group in groups(stages, competition):
        items = [
            item
            for raw in group.items
            if (item := _render_item(raw, counters, url_name, url_kwargs)) is not None
        ]
        if items:
            rendered.append(
                {
                    "label": group.label,
                    # Klucz sekcji dla zapamiętanego stanu zwinięcia (localStorage w
                    # ``static/js/coordinator-nav.js``) – z etykiety, bo grupy nie mają
                    # własnych identyfikatorów, a etykieta jest stała między żądaniami.
                    "slug": slugify(group.label),
                    "items": items,
                    # Sekcja z pozycją aktywną jest zawsze rozwinięta – zwinięte menu nie może
                    # ukrywać miejsca, w którym użytkownik właśnie jest.
                    "active": any(item["active"] or item["open"] for item in items),
                    # Suma liczników sekcji – widoczna na zwiniętym nagłówku, żeby zwinięcie
                    # nie chowało spraw czekających na koordynatora.
                    "badge": sum(item["badge"] or 0 for item in items) or None,
                }
            )
    return {
        "nav_groups": rendered,
        "nav_counters": counters,
        "nav_search_url": resolve(("web:coordinator-search",)) or "",
        "nav_query": request.GET.get("q", "") if url_name == "coordinator-search" else "",
    }
