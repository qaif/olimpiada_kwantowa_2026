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


def stage_items(stages: list) -> tuple[Item, ...]:
    """Jedna pozycja na etap, a pod nią cztery ekrany, po które sięga się w trakcie zawodów.

    Etap w formie rozmowy nie ma zadań (``create_problem`` odmawia), więc zamiast „Zadania”
    dostaje „Rozmowy” – odnośnik do ekranu, na którym da się cokolwiek zrobić. Etap w formie
    testu online z tego samego powodu dostaje „Test online”: nie ma w nim zadań do oddania ani
    terminów rozmów, a cała praca koordynatora toczy się wokół arkusza pytań.
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
        items.append(
            Item(
                stage.display_name,
                ("web:coordinator-stage-edit",),
                (stage.pk,),
                ("coordinator-stage-edit",),
                children=(
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
                ),
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
    return [
        Group("Pulpit", (Item("Co wymaga uwagi", ("web:coordinator",), match=("coordinator",)),)),
        Group("Etapy", stage_items(stages)),
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
        Group(
            "Komunikacja",
            (
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
            ),
        ),
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
