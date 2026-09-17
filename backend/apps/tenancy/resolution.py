"""Rozstrzyganie konkursu z żądania: host → ``wagtailcore.Site`` → ``Competition``.

Jedno miejsce, w którym ta reguła jest zapisana. ``Site.find_for_request`` jest już używane przez
``apps.cms.context_processors`` (menu) i ``apps.support.services`` (adres kontaktowy), więc
dopasowanie hosta jest tu **tym samym** dopasowaniem, którym Wagtail wybiera drzewo stron.
Drugiej reguły nie budujemy: rozjazd między „która witryna serwuje strony” a „który konkurs
jest właścicielem danych” byłby wyciekiem między konkursami.

Ścieżka zapasowa dewelopera wychodzi z tego za darmo: ``find_for_request`` przy braku trafienia po
hoście oddaje witrynę domyślną, więc ``localhost``, ``127.0.0.1`` i ``web`` dostają Konkurs #1
dokładnie tak, jak dotąd – bez wpisów w DNS i bez zmiennych środowiskowych.

**Koszt na żądanie: jedno zapytanie.** Witrynę Wagtail i tak odczytuje (i zapamiętuje na żądaniu),
a konkurs – z domeny albo z prefiksu ścieżki – bierze się z **jednego** zapytania z alternatywą,
a nie z dwóch. To jest świadome odstępstwo od § 2.3 dokumentu, który proponował osobną, buforowaną
mapę prefiksów: bufor z czasem życia znaczy, że koszt żądania zależy od tego, kiedy ostatnio
wygasł, a tego nie da się ani przewidzieć w teście liczby zapytań, ani wytłumaczyć przy diagnozie
produkcji. Jedno zapytanie zawsze jest tańsze do zrozumienia niż zero albo dwa zależnie od zegara.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.exceptions import DisallowedHost
from django.db import DatabaseError
from django.db.models import Q
from wagtail.models import Site

from apps.tenancy.models import Competition, RoutingMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    """Wynik rozstrzygania: konkurs oraz prefiks, który trzeba zdjąć z adresu.

    Prefiks jest tu, a nie wyliczany drugi raz w warstwie, bo to **to samo** rozstrzygnięcie:
    konkurs znaleziony po pierwszym segmencie ścieżki i segment, który wobec tego do adresu już
    nie należy. Dwa wyliczenia tej samej rzeczy to dwie okazje do rozjazdu.
    """

    competition: Competition | None
    #: Niepusty wyłącznie w trybie ``PATH`` i wyłącznie wtedy, gdy adres faktycznie go niósł.
    path_prefix: str = ""


def first_path_segment(path_info: str) -> str:
    """Pierwszy segment ścieżki: ``"/fizyczna/me/"`` → ``"fizyczna"``, ``"/"`` → ``""``."""
    return path_info.lstrip("/").split("/", 1)[0]


def resolve_for_request(request) -> Resolution:
    """Konkurs żądania razem z prefiksem do zdjęcia z adresu.

    Pierwszeństwo ma **prefiks ścieżki**, bo jest jawnym wskazaniem w adresie: konkurs czekający
    na własny DNS chodzi pod domeną platformy, więc dopasowanie po hoście oddałoby konkurs
    platformy, a nie ten, o który poprosił adres.

    Błąd bazy nie wywraca żądania: warstwa wyżej ma prawo nie znać konkursu (dokładnie tak, jak
    nie znała go przed tą zmianą), a stronę błędu i tak złoży ten sam mechanizm, co dla każdego
    innego zapytania. Milcząco pusta odpowiedź jest tu lepsza niż 500 na ``/healthz/``.

    ``DisallowedHost`` też kończy się tu pustym wynikiem, a nie wyjątkiem: odpowiedź 400 dla
    żądania z obcym nagłówkiem ``Host`` ma nadal złożyć Django tam, gdzie składała ją przed tą
    zmianą. Rozstrzyganie konkursu nie może być **nowym** miejscem, w którym żądanie się kończy.
    """
    try:
        site = Site.find_for_request(request)
    except (DatabaseError, DisallowedHost):
        logger.warning("Nie udało się rozstrzygnąć witryny żądania.", exc_info=True)
        return Resolution(None)

    segment = first_path_segment(request.path_info)
    match = Q(site=site) if site is not None else Q(pk__in=())
    if segment:
        match |= Q(routing_mode=RoutingMode.PATH, path_prefix=segment)

    try:
        # Najwyżej dwa wiersze: konkurs witryny i konkurs prefiksu. Pobieramy oba jednym
        # zapytaniem i dopiero tutaj rozstrzygamy pierwszeństwo – inaczej byłyby dwa zapytania,
        # z których drugie prawie zawsze nic nie znajduje.
        found = list(Competition.objects.filter(match, is_active=True).select_related("site")[:2])
    except DatabaseError:
        logger.warning("Nie udało się odczytać konkursu dla żądania.", exc_info=True)
        return Resolution(None)

    for competition in found:
        if competition.routing_mode == RoutingMode.PATH and competition.path_prefix == segment:
            return Resolution(competition, segment)
    for competition in found:
        if site is not None and competition.site_id == site.pk:
            return Resolution(competition)
    return Resolution(None)


def resolve_competition(request) -> Competition | None:
    """Konkurs dla żądania albo ``None``, gdy pod tym adresem nie stoi żaden.

    Wejście dla wołających, których prefiks ścieżki nie interesuje (wszystkich poza warstwą).
    """
    return resolve_for_request(request).competition
