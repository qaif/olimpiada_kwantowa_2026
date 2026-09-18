"""Marka konkursu w napisach, które czyta człowiek: temat listu, podpis pod listem, nazwa kalendarza.

Moduł jest **jednym** miejscem, w którym z nazwy konkursu powstaje napis. Dzisiejsze literały
(„Aktywuj konto – Olimpiada Kwantowa”, podpis „Olimpiada Kwantowa”, ``X-WR-CALNAME``) zostają
w swoich modułach i nie zmieniają się ani o znak; obok nich stają **wzorce**, a ten moduł
rozstrzyga, który z dwóch napisów wyjdzie na zewnątrz (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1).

Trzy reguły, na których stoi cała reszta:

- **Odwrotem jest dosłowny dzisiejszy napis, a nie wzorzec podstawiony nazwą Konkursu #1.**
  Podstawienie dawałoby ten sam temat tylko dopóty, dopóki nikt nie poprawi ``Competition.name``
  w panelu – a wtedy temat listu Olimpiady Kwantowej zmieniłby się bez wdrożenia i bez śladu
  w audycie. Napis w kodzie jest **treścią**, wiersz w bazie jest **konfiguracją**, i to nie są
  te same rzeczy.
- **Brak konkursu znaczy „dzisiaj”.** Zadanie Celery, komenda i test jednostkowe, które nie mają
  skąd wziąć konkursu, dostają napis sprzed etapu 2. Zgadywanie („weź pierwszy konkurs”)
  podpisałoby list Olimpiady Kwantowej marką cudzego organizatora.
- **Flaga jest czytana raz, tutaj** – przez ``Competition.has_feature`` (§ 1.0 (c)). Wołający
  podaje wzorzec i odwrót, a nie pyta o flagę, więc nie ma miejsca, w którym ktoś sprawdziłby ją
  inaczej niż pozostali.

Czego tu **nie ma**: nadawcy listu i prefiksu tematu (``Competition.from_email``,
``email_subject_prefix``). To nie są napisy dla człowieka, tylko koperta – składa je wysyłka
(``apps.core.tasks.send_mail_task``) i nie chodzi ona przez flagę marki, bo oba pola są
konfiguracją instalacji już dziś.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - wyłącznie dla adnotacji, bez kosztu przy imporcie
    from apps.tenancy.models import Competition

#: Przełącznik, za którym stoi **cały** ten moduł. Nazwa jest jedna i jest tutaj, żeby literówka
#: w niej wywracała się w jednym miejscu, a nie w piętnastu wywołaniach ``has_feature``.
BRANDING_FLAG = "competition_branding_in_mail"

#: Podpis pod listem – dzisiejszy literał z ``apps/accounts/activation.py``, ``guardian.py``,
#: ``services.py``, ``competitions/video.py`` i ``grading/reports.py``. Stoi tu jako **odwrót**:
#: wołający przekazuje własny (zwykle przetłumaczony) napis, a ta wartość obowiązuje wtedy, gdy
#: żadnego nie poda.
DEFAULT_SIGNATURE = "Olimpiada Kwantowa"

#: Nazwa kalendarza w kliencie (``X-WR-CALNAME``, ``apps/cms/calendar.py``). Celowo bez
#: tłumaczenia: plik ICS jest dziś w jednym języku i etap 2 tego nie zmienia.
DEFAULT_CALENDAR_NAME = "Olimpiada Kwantowa"


def uses_competition_branding(competition: Competition | None = None) -> bool:
    """Czy napisy tego konkursu mają nieść jego markę, czy dzisiejszy literał.

    **Jedyne** wejście do flagi ``competition_branding_in_mail``. Brak konkursu to nie jest
    „flaga wyłączona przez organizatora”, tylko „nie wiadomo, czyj to list” – a odpowiedź w obu
    wypadkach ma być ta sama: napis sprzed etapu 2.
    """
    return competition is not None and competition.has_feature(BRANDING_FLAG)


def competition_name(competition: Competition) -> str:
    """Nazwa konkursu w napisie dla człowieka: skrócona, a bez niej pełna.

    Ta sama reguła, co w ``Competition.__str__`` – i celowo ta sama, bo temat listu i etykieta
    w panelu mają mówić o konkursie tym samym słowem.
    """
    return competition.short_name or competition.name


def substitutions(competition: Competition, **extra: object) -> dict[str, object]:
    """Wartości podstawień dostępne w każdym wzorcu tego modułu.

    Trzy formy nazwy, bo polskie zdanie ich wymaga: „– Olimpiada Kwantowa” (mianownik),
    „komitetu **Olimpiady Kwantowej**” (dopełniacz) i „udział w **Olimpiadzie Kwantowej**”
    (miejscownik). Odmiana jest **danymi** (``Competition.genitive_name``, ``locative_name``),
    a nie regułą fleksyjną w kodzie – uzasadnienie stoi przy tych polach i przy nazwie
    organizatora w ``apps/accounts/consents.py``.

    ``extra`` dokłada podstawienia własne wołającego (np. ``%(stage)s``); nadpisanie nazwy
    konkursu jest dozwolone celowo – wołający, który zna lepszą odmianę, jest bliżej treści.
    """
    return {
        "competition": competition_name(competition),
        "competition_genitive": competition.genitive,
        "competition_locative": competition.locative,
        **extra,
    }


def branded_text(
    template: object, fallback: object, competition: Competition | None = None, **extra: object
) -> str:
    """Napis ze wzorca z nazwą konkursu albo dzisiejszy literał, gdy flaga jest wyłączona.

    Wspólny trzon :func:`subject`, :func:`signature` i :func:`calendar_name`, wystawiony także
    wprost – bo poza pocztą tą samą drogą idą napisy, które listem nie są (``PRODID`` kalendarza,
    § 1.1.4). Nazywa się „text”, a nie „subject”, żeby nikt nie musiał udawać, że identyfikator
    programu w pliku ICS jest tematem listu.

    ``template`` i ``fallback`` wolno podać jako napis leniwy (``gettext_lazy``) – oba przechodzą
    przez ``str()``, więc tłumaczenie rozwiązuje się tu, w chwili składania, a nie przy imporcie
    modułu z tematami.
    """
    if not uses_competition_branding(competition):
        return str(fallback)
    return str(template) % substitutions(competition, **extra)


def subject(
    template: object, fallback: object, competition: Competition | None = None, **extra: object
) -> str:
    """Temat listu: wzorzec z nazwą konkursu albo dzisiejszy temat, gdy flaga jest wyłączona.

    ``fallback`` jest **dosłownym dzisiejszym tematem** (np. ``ACTIVATION_SUBJECT``), a nie
    wzorcem podstawionym nazwą Konkursu #1 – powód stoi w docstringu modułu. Temat jest częścią
    kontraktu z odbiorcą: ludzie mają na nim reguły w skrzynkach, a organizator filtry
    (``apps/tenancy/tests/test_invariants.py``, ``EXPECTED_SUBJECTS``).
    """
    return branded_text(template, fallback, competition, **extra)


def signature(competition: Competition | None = None, *, fallback: object = DEFAULT_SIGNATURE) -> str:
    """Podpis pod listem – wiersz po ``--``.

    Wzorca nie ma i nie jest potrzebny: podpisem jest **sama nazwa**, więc przy włączonej fladze
    zwracamy ją wprost. ``fallback`` jest osobnym argumentem, bo w czterech z pięciu miejsc napis
    jest dziś przetłumaczalny (``gettext``), a w jednym nie – i to jest stan, który zastajemy,
    a nie coś, co ten moduł ma po drodze ujednolicać.
    """
    if not uses_competition_branding(competition):
        return str(fallback)
    return competition_name(competition)


def calendar_name(competition: Competition | None = None, *, fallback: object = DEFAULT_CALENDAR_NAME) -> str:
    """Nazwa kalendarza pokazywana przez klienta (``X-WR-CALNAME``).

    Za tą samą flagą, co poczta, i z tego samego powodu: jest to napis, który uczestnik widzi
    obok swoich terminów. ``UID`` i ``PRODID`` idą **inną** drogą (§ 1.1.4) – zmiana ``UID``
    znaczyłaby drugie wydarzenie obok starego, więc nie wolno jej powiązać z flagą.
    """
    if not uses_competition_branding(competition):
        return str(fallback)
    return competition_name(competition)
