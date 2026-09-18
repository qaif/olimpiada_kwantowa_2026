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
a konkurs – z domeny, z prefiksu ścieżki albo z aliasu witryny (``apps.tenancy.aliases``,
wielojęzyczność treści, § 1.6.2) – bierze się z **jednego** zapytania z alternatywą, a nie z trzech.
To jest świadome odstępstwo od § 2.3 dokumentu, który proponował osobną, buforowaną
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

from apps.tenancy.aliases import alias_match
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
    # Gałąź aliasów (§ 1.6.2): druga witryna tego samego konkursu w drugim języku treści. Puste
    # ``Q()`` przy wyłączonym ``WAGTAIL_I18N_ENABLED`` nie zostawia w zapytaniu żadnego śladu –
    # ani złączenia, ani warunku – więc instalacja jednojęzyczna pyta dokładnie o to samo, co
    # przed tą zmianą.
    aliases = alias_match(site)
    match |= aliases

    try:
        # Najwyżej dwa wiersze: konkurs witryny (albo jej aliasu – witryna jest albo jednym, albo
        # drugim) i konkurs prefiksu. Pobieramy oba jednym zapytaniem i dopiero tutaj rozstrzygamy
        # pierwszeństwo – inaczej byłyby dwa zapytania, z których drugie prawie zawsze nic nie
        # znajduje.
        found = Competition.objects.filter(match, is_active=True).select_related("site")
        if aliases:
            # Złączenie z aliasami mnoży wiersze konkursu, który ma ich kilka, a limit poniżej
            # liczy wiersze, nie konkursy. ``distinct()`` wchodzi **wyłącznie** razem z tą gałęzią,
            # żeby zapytanie instalacji jednojęzycznej zostało nietknięte.
            found = found.distinct()
        found = list(found[:2])
    except DatabaseError:
        logger.warning("Nie udało się odczytać konkursu dla żądania.", exc_info=True)
        return Resolution(None)

    for competition in found:
        if competition.routing_mode == RoutingMode.PATH and competition.path_prefix == segment:
            return Resolution(competition, segment)
    for competition in found:
        if site is not None and competition.site_id == site.pk:
            return Resolution(competition)
    if aliases:
        return Resolution(_alias_competition(found))
    return Resolution(None)


def _alias_competition(found) -> Competition | None:
    """Konkurs dopasowany **aliasem** witryny – o ile ma włączone tłumaczenia treści.

    Rozstrzygnięcie po wykluczeniu: pętle wyżej odrzuciły dopasowanie po prefiksie i po witrynie
    głównej, więc jedyną alternatywą, która mogła dołożyć ten wiersz do wyniku, jest alias. Pytanie
    bazy drugi raz („czy to na pewno alias”) kosztowałoby zapytanie na każde żądanie pod drugą
    domeną i nie odpowiedziałoby na nic, czego nie wiadomo.

    Flaga konkursu jest tu **drugą** bramką obok ustawienia instalacji: ``WAGTAIL_I18N_ENABLED``
    mówi, że w tej instalacji w ogóle istnieją drzewa w kilku językach, a ``content_translations``
    – że **ten** konkurs je prowadzi. Alias założony przed włączeniem flagi jest konfiguracją
    w toku, a nie działającą drugą domeną; bez tej bramki kolejność dwóch kroków operatora
    decydowałaby o tym, czy strona już odpowiada.
    """
    for competition in found:
        if competition.has_feature("content_translations"):
            return competition
    return None


def resolve_competition(request) -> Competition | None:
    """Konkurs dla żądania albo ``None``, gdy pod tym adresem nie stoi żaden.

    Wejście dla wołających, których prefiks ścieżki nie interesuje (wszystkich poza warstwą).
    """
    return resolve_for_request(request).competition
