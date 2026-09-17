"""Jak domena zawodów odpowiada na pytanie „o który konkurs chodzi”.

Warstwa izolacji (``apps.tenancy.managers``) umie zawęzić queryset do wskazanego konkursu. Ten
moduł odpowiada na pytanie **poprzedzające**: skąd bierze się ten konkurs, gdy wołający go nie
podał – a nie podaje go większość kodu, bo do etapu 1 nie było czego podawać.

Dwie funkcje, dwa różne zastosowania:

- :func:`scope_to_competition` – dla **odczytu** (managery, serwisy, widoki API),
- :func:`each_competition` – dla **przebiegów wsadowych** (zadania Celery, komendy), które muszą
  obejść wszystkie konkursy po kolei, a każdy list wysłać z adresem i marką właściwego.

Dlaczego to stoi w ``apps/competitions``, a nie w ``apps/tenancy``: ``tenancy`` jest warstwą
platformy i nie ma prawa wiedzieć, że pięć aplikacji domeny zawodów ma wspólną politykę odwrotów.
Zależność idzie w drugą stronę – ``submissions``, ``grading``, ``results`` i ``appeals`` i tak
importują ``competitions`` (etap jest korzeniem ich danych), więc jedno miejsce dla tej reguły
jest tutaj i nie dokłada ani jednej nowej krawędzi w grafie zależności.

**Instalacja bez konkursów.** Odwrót „nie zawężaj” w :func:`scope_to_competition` wygląda na
furtkę i wymaga uzasadnienia. Dotyczy on wyłącznie bazy, w której tabela ``Competition`` jest
**pusta** – czyli stanu sprzed migracji ``tenancy.0002`` albo bazy wyczyszczonej przez test
transakcyjny. W takiej bazie nie ma dwóch organizatorów, więc nie ma między kim przeciekać,
a zachowanie ma być identyczne z tym sprzed etapu 1 (``docs/UNIWERSALNY-ETAP-1.md`` § 0).
Baza z konkursami i **bez** wskazania konkursu zachowuje się odwrotnie: nie widać niczego, bo
„nie wiadomo, o który chodzi” jest w bazie wielokonkursowej pytaniem bez bezpiecznej odpowiedzi.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import models

from apps.tenancy.managers import CompetitionScopedQuerySet

logger = logging.getLogger(__name__)


def competition_scoped_manager(competition_path: str = "competition"):
    """Manager modelu, który dochodzi do konkursu ścieżką ``competition_path``.

    ``apps.tenancy.managers`` trzyma ścieżkę jako **atrybut klasy querysetu**, a nie argument
    wywołania – i słusznie: ścieżka jest własnością modelu, a nie decyzją wołającego (§ 3.5).
    Dosłowne zastosowanie tej reguły znaczyłoby jednak kilkanaście niemal identycznych klas
    querysetu, z których każda niesie jedną linijkę treści. Ta funkcja robi dokładnie to samo –
    zakłada podklasę z ustawionym ``competition_path`` – tylko zapisuje to jednym wierszem przy
    modelu, czyli tam, gdzie tę ścieżkę się czyta.

    Modele, które mają **własne** metody querysetu (``StageEntry``, ``Submission``, ``Review``,
    ``Appeal``), tej funkcji nie używają: deklarują ``competition_path`` wprost w swojej klasie
    querysetu, bo i tak ją piszą.
    """
    name = f"{''.join(part.title() for part in competition_path.split('__'))}ScopedQuerySet"
    queryset_class = type(
        name,
        (CompetitionScopedQuerySet,),
        {
            "competition_path": competition_path,
            "__doc__": f"Queryset zakresowany ścieżką ``{competition_path}``.",
            # Bez tego klasa przedstawiałaby się jako ``abc.<name>`` w tracebacku i w ``repr``.
            "__module__": __name__,
        },
    )
    return models.Manager.from_queryset(queryset_class)()


def resolve_competition(competition=None):
    """Konkurs do zawężenia: podany wprost, z kontekstu żądania albo jedyny w instalacji.

    Kolejność jest ta sama, co w ``apps.accounts.services.default_competition`` – i to właśnie ta
    funkcja jest tu wołana, zamiast powtarzania jej reguły. Jedna definicja „konkursu na teraz”
    dla kont i dla zawodów jest warunkiem tego, żeby uczestnik widziany przez ``participant_for``
    i praca widziana przez ``Submission.objects.for_user`` należały do tego samego organizatora.
    """
    if competition is not None:
        return competition
    # Import lokalny: ``apps.accounts.services`` ciąga za sobą rejestrację, zgody i pocztę, a ten
    # moduł bywa importowany z ``models.py`` przy składaniu aplikacji.
    from apps.accounts.services import default_competition

    return default_competition()


def competition_of(request):
    """Konkurs żądania – jedno wejście dla widoków API domeny zawodów.

    ``rest_framework.request.Request`` deleguje nieznane atrybuty do opakowanego ``HttpRequest``,
    więc jest to dokładnie ten obiekt, który ustawił ``CompetitionMiddleware``. ``getattr``,
    a nie ``request.competition``: widok bywa wołany z ``APIRequestFactory`` bez warstwy, a wtedy
    poprawną odpowiedzią jest „nie wiadomo”, a nie ``AttributeError`` w środku uprawnienia.
    """
    return getattr(request, "competition", None)


def _installation_has_competitions() -> bool:
    """Czy w tej instalacji stoi choć jeden konkurs.

    Pytanie zadawane **tylko** wtedy, gdy konkursu nie udało się rozstrzygnąć – czyli raz na
    żądanie pod nieznanym hostem i ani razu w normalnym przebiegu. Rozróżnia dwa stany, które
    ``default_competition()`` zwija do tego samego ``None``: „konkursów nie ma jeszcze wcale”
    (zachowanie sprzed etapu 1) i „konkursów jest kilka, ale nie wiadomo który” (nic nie widać).
    """
    from apps.tenancy.models import Competition

    return Competition.objects.exists()


def scope_to_competition(queryset, competition=None):
    """Zawęża queryset do konkursu. Jedno miejsce reguły odwrotów dla całej domeny zawodów.

    ``queryset`` musi pochodzić z ``CompetitionScopedQuerySet`` – to on wie, którędy jego model
    dochodzi do konkursu (``competition_path``). Ta funkcja dokłada wyłącznie rozstrzygnięcie
    „czym zawężamy” i trzy jego przypadki brzegowe, opisane w docstringu modułu.
    """
    competition = resolve_competition(competition)
    if competition is not None:
        return queryset.for_competition(competition)
    if _installation_has_competitions():
        # WARNING, a nie wyjątek: wydanie B stoi obok kodu, który konkursu jeszcze nie podaje,
        # a pusta odpowiedź jest widoczna od razu – w przeciwieństwie do cudzych danych.
        logger.warning(
            "Odczyt %s bez wskazania konkursu w instalacji wielokonkursowej – zwracam pustkę.",
            queryset.model._meta.label,
        )
        return queryset.none()
    return queryset


@contextmanager
def _bound(competition) -> Iterator:
    """``competition_context`` dla konkursu i zwykły przebieg dla ``None``.

    ``None`` znaczy „instalacja bez konkursów” i nie wolno go wiązać jako konkursu: wiązanie
    ``None`` zdejmowałoby konkurs ustawiony przez wołającego (komendę, test), czyli zmieniałoby
    stan, którego to zadanie nie dotyczy.
    """
    if competition is None:
        yield None
        return
    from apps.tenancy.context import competition_context

    with competition_context(competition):
        yield competition


def each_competition() -> Iterator:
    """Kolejne konkursy instalacji, każdy **związany** z kontekstem na czas swojej iteracji.

    Wzorzec przebiegu wsadowego (``docs/UNIWERSALNY-ETAP-1.md`` § 6, T3): zadanie okresowe chodzi
    po **wszystkich** konkursach, ale każdą pracę wykonuje w kontekście jednego z nich. Dzięki
    temu ``apps.accounts.activation.absolute_url`` – wołane głęboko, przy składaniu listu – trafia
    w domenę konkursu, którego dotyczy przypomnienie, a nie w witrynę domyślną instalacji.

    Instalacja bez konkursów oddaje **jeden** przebieg z ``None``, czyli dokładnie tyle, ile
    zadanie wykonywało przed etapem 1. Bez tego świeża instalacja przestałaby zamykać etapy.

    Kolejność po ``pk`` jest deterministyczna z rozmysłem: wynik zadania trafia do logu i do
    testów, a przebieg zależny od kolejności wstawiania byłby nieporównywalny między uruchomieniami.
    """
    from apps.tenancy.models import Competition

    competitions = list(Competition.objects.order_by("pk"))
    for competition in competitions or [None]:
        with _bound(competition):
            yield competition
