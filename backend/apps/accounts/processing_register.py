"""Rejestr czynności przetwarzania (art. 30 ust. 1 RODO) – jako dane w kodzie, nie jako dokument.

Administrator ma obowiązek prowadzić rejestr czynności przetwarzania i okazać go organowi
nadzorczemu na żądanie. Zwykle jest to arkusz, który ktoś kiedyś wypełnił i który rozjeżdża się
z systemem przy pierwszej zmianie – bo nic nie łączy wiersza „czas przechowywania” z tym, co
naprawdę robi kod.

Dlatego rejestr stoi **tutaj**: jest zwykłą strukturą pythonową, wersjonowaną razem z aplikacją.
Konsekwencje są dwie i to one są powodem tej decyzji:

- zmiana w systemie, która zmienia przetwarzanie (nowy odbiorca, nowa kategoria danych, inny okres
  retencji), jest zmianą **w tym pliku** i przechodzi przez tę samą recenzję, co kod,
- treść rejestru da się sprawdzić testem – a okres przechowywania czyta się z tego samego miejsca,
  z którego bierze go automat retencji (``apps.accounts.retention``), więc nie ma dwóch odpowiedzi
  na pytanie „jak długo trzymacie te dane”.

Czego tu **nie** ma: danych kontaktowych administratora i inspektora ochrony danych. Te stoją
w ``cms.SiteSettings`` (organizator zmienia je w ``/cms/`` bez wydania aplikacji) i dokłada je
widok, który rejestr renderuje – wpisane tutaj byłyby drugą, rozjeżdżającą się kopią.

Wersja rejestru (``REGISTER_VERSION``) rośnie przy każdej **materialnej** zmianie treści, a nie
przy poprawce literówki: to ona odpowiada na pytanie „czy czytam wersję aktualną” i to ona stoi
w nagłówku eksportu CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from apps.competitions.models import DEFAULT_RETENTION_MONTHS

#: Wersja treści rejestru i data jej przyjęcia. Zmieniane ręcznie, razem z treścią niżej.
#: 1.1 (20.09.2026) – przekazywanie przyjętych rozwiązań na skrzynkę organizatora dokłada drogę,
#: którą prace uczestników wychodzą poza serwer, więc jest zmianą materialną, a nie literówką.
#: 1.2 (21.09.2026) – forum uczestników dokłada przetwarzanie, którego w serwisie dotąd nie było:
#: **treść pisaną publicznie przez osoby niepełnoletnie**, podpisaną imieniem i inicjałem nazwiska
#: i czytaną przez innych uczestników. Nowa kategoria danych i nowy krąg odbiorców to zmiana
#: materialna z podręcznikowego przykładu, a nie doprecyzowanie istniejącego wiersza.
REGISTER_VERSION = "1.3"
REGISTER_DATE = date(2026, 9, 21)

#: Zdanie o okresie przechowywania danych uczestnika. Liczba pochodzi z tego samego miejsca, co
#: domyślna wartość ``Edition.data_retention_months`` – gdyby organizator zmienił ją dla rocznika,
#: rejestr mówi o zasadzie, a ekran ``/coordinator/retention/`` o terminach konkretnych edycji.
PARTICIPANT_RETENTION = (
    f"{DEFAULT_RETENTION_MONTHS} miesięcy od ostatniego deadline'u etapu edycji (ustawienie "
    "edycji, art. 5 ust. 1 lit. e RODO). Po tym terminie konto jest anonimizowane automatycznie: "
    "dane osobowe znikają, zostaje pseudonimowy kod uczestnika i dokumentacja zawodów."
)

#: Odbiorcy wspólni dla większości czynności: dostawca hostingu i poczta wychodząca. Wypisane raz,
#: bo powtórzone w każdym wierszu rozjechałyby się przy zmianie dostawcy.
HOSTING_RECIPIENT = "Contabo GmbH (hosting serwera w Niemczech) – podmiot przetwarzający"
MAIL_RECIPIENT = "dostawca poczty wychodzącej (SMTP) – podmiot przetwarzający"
ANALYTICS_RECIPIENT = (
    "Google Ireland Ltd. (Google Analytics 4) – wyłącznie po zgodzie odwiedzającego; bez zgody "
    "tag działa w trybie odmowy i nie zapisuje identyfikatorów"
)
JITSI_RECIPIENT = (
    "brak odbiorcy zewnętrznego – pokoje wideo działają na własnej instancji Jitsi Meet "
    "organizatora, na tym samym serwerze"
)


@dataclass(frozen=True)
class ProcessingActivity:
    """Jedna czynność przetwarzania w układzie art. 30 ust. 1 RODO.

    Struktura jest zamrożona, bo rejestr jest **dokumentem**: czyta go widok HTML, eksport CSV
    i test, a żaden z nich nie ma prawa go zmienić w locie. Pola odpowiadają literom przepisu,
    a nie wygodzie szablonu – kolumna „kategorie osób” musi dać się wskazać palcem w ustawie.
    """

    key: str
    name: str
    purpose: str
    legal_basis: str
    subjects: str
    categories: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    retention: str = ""
    measures: list[str] = field(default_factory=list)


#: Środki techniczne wspólne dla całego systemu (art. 32 RODO). Wypisane raz i doklejane do każdej
#: czynności, bo nie są właściwością pojedynczego przetwarzania – tak działa cała platforma.
COMMON_MEASURES = (
    "szyfrowanie transportu (HTTPS/TLS, HSTS), ruch wyłącznie przez odwrotne proxy",
    "hasła w postaci skrótu (Django PBKDF2), brak przechowywania tokenów dostawców OAuth",
    "kontrola dostępu oparta na rolach (uczestnik, komitet, komisja odwoławcza, koordynator)",
    "niezmienny dziennik zdarzeń (audit log) bez danych osobowych w treści wpisu",
    "kopia zapasowa bazy i storage'u, odtwarzanie sprawdzane procedurą z README",
)


def _activity(**kwargs) -> ProcessingActivity:
    """Czynność ze wspólnymi środkami technicznymi doklejonymi na końcu jej własnych."""
    measures = [*kwargs.pop("measures", []), *COMMON_MEASURES]
    return ProcessingActivity(measures=measures, **kwargs)


#: Treść rejestru. Kolejność jest kolejnością cyklu życia sprawy uczestnika: konto, zawody, ocena,
#: wyniki i dokumenty, sprawy sporne, a na końcu to, co dotyczy serwisu, a nie zawodów.
ACTIVITIES: tuple[ProcessingActivity, ...] = (
    _activity(
        key="konta",
        name="Prowadzenie kont uczestników olimpiady",
        purpose=(
            "Rejestracja uczestnika w zawodach, potwierdzenie tożsamości szkolnej, kontakt "
            "w sprawach organizacyjnych i umożliwienie logowania do panelu."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO (wykonanie umowy – udział w zawodach na zasadach "
            "Regulaminu) oraz art. 6 ust. 1 lit. a RODO dla zgód dobrowolnych"
        ),
        subjects="uczniowie szkół ponadpodstawowych zgłaszający się do olimpiady",
        categories=[
            "imię i nazwisko",
            "adres e-mail (jest zarazem loginem)",
            "numer telefonu (opcjonalnie)",
            "nazwa szkoły i województwo",
            "klasa i rok urodzenia (bez daty dziennej – zasada minimalizacji)",
            "adres e-mail opiekuna szkolnego (opcjonalnie)",
            "kod publiczny uczestnika (pseudonim nadawany przez system)",
        ],
        recipients=[HOSTING_RECIPIENT, MAIL_RECIPIENT],
        retention=PARTICIPANT_RETENTION,
        measures=[
            "aktywacja konta linkiem e-mail; konto niepotwierdzone jest kasowane po 24 godzinach",
            "limit prób logowania i rejestracji (throttling po adresie IP i po tożsamości)",
        ],
    ),
    _activity(
        key="zgody",
        name="Dowody zgód i zgoda opiekuna osoby małoletniej",
        purpose=(
            "Wykazanie, na co i pod jaką wersją dokumentu uczestnik (albo jego opiekun prawny) "
            "wyraził zgodę – art. 7 ust. 1 RODO."
        ),
        legal_basis="art. 6 ust. 1 lit. c RODO (obowiązek rozliczalności, art. 5 ust. 2 RODO)",
        subjects="uczestnicy oraz rodzice i opiekunowie prawni uczestników małoletnich",
        categories=[
            "rodzaj zgody i wersja dokumentu, którego dotyczy",
            "data wyrażenia i data wycofania",
            "adres e-mail, z którego potwierdzono zgodę opiekuna",
            "adres IP w chwili złożenia oświadczenia",
        ],
        recipients=[HOSTING_RECIPIENT, MAIL_RECIPIENT],
        retention=(
            "przez czas obowiązywania zgody, a po jej wycofaniu – jako dowód rozliczalności "
            "do końca okresu retencji edycji, której dotyczy"
        ),
        measures=[
            "zgoda opiekuna składana pod jednorazowym podpisanym odnośnikiem, bez zakładania konta",
            "wycofanie zgody jest równie proste, co jej udzielenie (art. 7 ust. 3 RODO)",
        ],
    ),
    _activity(
        key="prace",
        name="Przyjmowanie i ocenianie prac konkursowych",
        purpose=(
            "Przeprowadzenie zawodów: przyjęcie rozwiązań, dwie niezależne recenzje, ustalenie "
            "oceny końcowej i kwalifikacji do etapu następnego."
        ),
        legal_basis="art. 6 ust. 1 lit. b RODO (wykonanie umowy – udział w zawodach)",
        subjects="uczestnicy zapisani do etapu",
        categories=[
            "pliki rozwiązań wraz z ich skrótem SHA-256 i numerem wersji",
            "data i godzina oddania pracy, znacznik oddania w tolerancji po deadline",
            "punktacja recenzji, komentarze recenzentów i ocena końcowa",
        ],
        recipients=[
            HOSTING_RECIPIENT,
            "członkowie komitetu oceniającego – ocenianie jest ślepe: recenzent widzi kod "
            "uczestnika, nigdy jego nazwiska",
            # Przekazywanie prac pocztą (``Competition.submission_forward_emails``). Wiersz jest
            # **bezwarunkowy**, w odróżnieniu od logistyki niżej, i to jest świadome: odbiorcą
            # jest sam administrator (organizator) na własnej skrzynce, a nie nowy podmiot, więc
            # rejestr nie zmienia się w zależności od tego, czy pole jest dziś wypełnione. Zdanie
            # mówi wprost o drodze, którą dane wychodzą, bo to ona jest tu faktem do zgłoszenia.
            "skrzynka pocztowa organizatora – jeżeli włączono przekazywanie rozwiązań, każda "
            "przyjęta praca jest po czystym skanie antywirusowym przesyłana na wskazane adresy "
            "komitetu wraz z metryczką (kod, imię i nazwisko, szkoła, etap, zadanie, wersja)",
            MAIL_RECIPIENT,
        ],
        retention=PARTICIPANT_RETENTION,
        measures=[
            "prace w prywatnym storage'u (bucket bez dostępu anonimowego), odnośniki podpisane i wygasające",
            "skan antywirusowy każdego pliku przed udostępnieniem go komitetowi",
            "nazwy plików budowane z kodu uczestnika – nazwisko z nazwy pliku nie przechodzi dalej",
            "przekazywanie prac pocztą jest domyślnie **wyłączone** (puste pole adresów), obejmuje "
            "wyłącznie pliki z czystym skanem i zostawia ślad w dzienniku zdarzeń przy każdej "
            "zmianie adresów",
        ],
    ),
    _activity(
        key="wyniki",
        name="Ogłaszanie wyników i wystawianie dokumentów",
        purpose=(
            "Publikacja tabeli wyników etapu oraz wystawianie dyplomów i zaświadczeń wraz z ich "
            "publiczną weryfikacją po kodzie z dokumentu."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO (wykonanie umowy) oraz art. 6 ust. 1 lit. a RODO "
            "dla publikacji imienia i nazwiska – wyłącznie za odrębną zgodą"
        ),
        subjects="uczestnicy, którym ogłoszono wynik, oraz opiekunowie szkolni otrzymujący podziękowania",
        categories=[
            "kod publiczny uczestnika, punktacja, informacja o kwalifikacji",
            "imię i nazwisko – wyłącznie w finale i wyłącznie przy aktywnej zgodzie na publikację",
            "numer i kod weryfikacyjny wystawionego dokumentu",
        ],
        recipients=[HOSTING_RECIPIENT, "publiczność serwisu (tabela wyników jest jawna)"],
        retention=(
            "ogłoszona tabela wyników jest zamrożonym dokumentem zawodów i zostaje bezterminowo "
            "w postaci pseudonimowej; anonimizacja konta jej nie zmienia"
        ),
        measures=[
            "tabela wyników pochodzi wyłącznie z zamrożonego snapshotu, nie z bieżących danych",
            "strona weryfikacji dokumentu nie podaje nazwiska bez aktywnej zgody na publikację",
        ],
    ),
    _activity(
        key="reklamacje",
        name="Reklamacje i odwołania od oceny",
        purpose="Rozpatrzenie zastrzeżeń uczestnika do oceny pracy przez komisję odwoławczą.",
        legal_basis="art. 6 ust. 1 lit. b RODO (wykonanie umowy – tryb odwoławczy z Regulaminu)",
        subjects="uczestnicy składający reklamację",
        categories=[
            "treść reklamacji i jej uzasadnienie",
            "rozstrzygnięcie komisji wraz z uzasadnieniem i ewentualną nową punktacją",
            "skład komisji rozpatrującej sprawę",
        ],
        recipients=[HOSTING_RECIPIENT, "członkowie komisji odwoławczej"],
        retention=(
            "do końca okresu retencji edycji; konto z nierozstrzygniętą reklamacją nie jest "
            "anonimizowane, dopóki sprawa się nie zakończy"
        ),
        measures=[
            "reguła konfliktu interesów: reklamacji nie rozpatruje autor ocenianej recenzji",
        ],
    ),
    _activity(
        key="rozmowy",
        name="Rozmowy kwalifikacyjne online",
        purpose="Przeprowadzenie etapu w formie rozmowy: zapis na termin i spotkanie wideo.",
        legal_basis="art. 6 ust. 1 lit. b RODO (wykonanie umowy – udział w zawodach)",
        subjects="uczestnicy zakwalifikowani do etapu prowadzonego w formie rozmowy",
        categories=[
            "wybrany termin rozmowy i adres pokoju spotkania",
            "wizerunek i głos w czasie rozmowy (transmisja, bez nagrywania)",
        ],
        recipients=[HOSTING_RECIPIENT, JITSI_RECIPIENT],
        retention=(
            "zapis na termin – do końca okresu retencji edycji; sama rozmowa nie jest nagrywana, "
            "więc nie powstaje żaden plik do przechowywania"
        ),
        measures=[
            "własna instancja Jitsi Meet organizatora – transmisja nie wychodzi do dostawcy obcego",
            "adres pokoju budowany z identyfikatora terminu, nie z danych uczestnika",
        ],
    ),
    _activity(
        key="komitet",
        name="Prowadzenie kont komitetu i komisji odwoławczej",
        purpose=(
            "Powołanie recenzentów i członków komisji odwoławczej, przydział prac do oceny "
            "z wykluczeniem konfliktu interesów."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO (współpraca przy organizacji zawodów) oraz art. 6 ust. 1 "
            "lit. f RODO (prawnie uzasadniony interes – rzetelność oceniania)"
        ),
        subjects="członkowie komitetu oceniającego i komisji odwoławczej",
        categories=[
            "imię, nazwisko i adres e-mail",
            "województwo (podstawa reguły konfliktu interesów)",
            "status członkostwa i data zatwierdzenia",
        ],
        recipients=[HOSTING_RECIPIENT, MAIL_RECIPIENT],
        retention=(
            "przez czas pełnienia funkcji i okres rozliczalności zawodów; konta komitetu nie "
            "podlegają automatycznej retencji uczestników"
        ),
        measures=[
            "wejście do komitetu wyłącznie z jednorazowego kodu zaproszenia (w bazie sam skrót)",
        ],
    ),
    _activity(
        key="zgloszenia",
        name="Obsługa zgłoszeń i pomocy technicznej",
        purpose=(
            "Udzielenie odpowiedzi na zgłoszony problem z kontem, wysyłką pracy, wynikami albo rejestracją."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. b RODO dla zgłoszeń od uczestników oraz art. 6 ust. 1 lit. f RODO "
            "(prawnie uzasadniony interes – obsługa pytań od osób bez konta)"
        ),
        subjects="osoby korzystające z serwisu, w tym osoby bez konta piszące z formularza kontaktowego",
        categories=[
            "treść zgłoszenia i odpowiedzi",
            "adres e-mail nadawcy (przy zgłoszeniu bez konta)",
            "kontekst techniczny: adres strony, przeglądarka, język, kod uczestnika",
        ],
        recipients=[HOSTING_RECIPIENT, MAIL_RECIPIENT],
        retention=(
            "do zakończenia sprawy i przez okres retencji edycji, której dotyczy; zgłoszenia "
            "powiązane z kontem znikają razem z jego anonimizacją"
        ),
        measures=[
            "kontekst techniczny zbierany automatycznie nie zawiera haseł, tokenów ani cookie",
            "zgłoszenie anonimowe chronione tą samą CAPTCHĄ, co formularz rejestracji",
        ],
    ),
    _activity(
        key="serwis",
        name="Utrzymanie serwisu, bezpieczeństwo i statystyka odwiedzin",
        purpose=(
            "Zapewnienie działania i bezpieczeństwa serwisu (dziennik zdarzeń, ochrona przed "
            "nadużyciem formularzy) oraz – za zgodą – statystyka odwiedzin."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes – bezpieczeństwo systemu) "
            "oraz art. 6 ust. 1 lit. a RODO dla analityki"
        ),
        subjects="wszystkie osoby odwiedzające serwis",
        categories=[
            "adres IP i czas zdarzenia w dzienniku audytowym",
            "identyfikatory sesji i tokeny ochrony formularzy (cookie niezbędne)",
            "dane statystyki odwiedzin Google Analytics 4 – wyłącznie po zgodzie",
        ],
        recipients=[HOSTING_RECIPIENT, ANALYTICS_RECIPIENT],
        retention=(
            "dziennik zdarzeń – bezterminowo w postaci bez danych osobowych w treści wpisu; "
            "dane analityczne – zgodnie z ustawieniem usługi Google Analytics 4"
        ),
        measures=[
            "analityka domyślnie wyłączona: bez identyfikatora w ustawieniach serwisu nie wczytuje "
            "się ani jeden skrypt podmiotu trzeciego",
            "tryb zgody (Consent Mode v2): przed kliknięciem zgody nie powstaje cookie analityczne",
            "polityka Content-Security-Policy bez kodu inline, zamknięta lista dostawców osadzeń",
        ],
    ),
)

#: Czynność **warunkowa**: wchodzi do rejestru wyłącznie konkursom, które zbierają potrzeby
#: szczególne przed etapem stacjonarnym (``docs/UNIWERSALNY-ETAP-2.md`` § 1.5.2, decyzja
#: organizatora D21). Konkurs #1 ich nie zbiera, więc jego rejestr nie zmienia się o ani jeden
#: wiersz – i to jest powód, dla którego ta czynność stoi obok :data:`ACTIVITIES`, a nie w nich:
#: rejestr ma opisywać przetwarzanie, które **naprawdę zachodzi**, a nie to, które kod umiałby
#: przeprowadzić. Wpis powstaje razem z modułem, który te dane zbiera, i to jest warunek przyjęcia
#: tamtej zmiany (art. 30 RODO).
ONSITE_LOGISTICS_ACTIVITY = _activity(
    key="logistyka",
    name="Organizacja pobytu na etapie stacjonarnym",
    purpose=(
        "Przygotowanie noclegu, wyżywienia i dostępności miejsca zawodów oraz odnotowanie "
        "obecności uczestnika na etapie odbywającym się na miejscu."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. b RODO (wykonanie umowy – udział w zawodach) oraz – dla potrzeb "
        "szczególnych, które bywają danymi o zdrowiu – art. 9 ust. 2 lit. a RODO (wyraźna zgoda "
        "uczestnika albo jego opiekuna prawnego, wyrażona przez wypełnienie pola nieobowiązkowego)"
    ),
    subjects="uczestnicy zakwalifikowani do etapu odbywającego się na miejscu",
    categories=[
        "data przyjazdu i wyjazdu oraz wskazane miejsce zawodów",
        "rodzaje zgłoszonych potrzeb: nocleg, wyżywienie, dojazd",
        "rodzaje potrzeb szczególnych (dieta, dostępność) i krótka uwaga własna uczestnika – "
        "wyłącznie w konkursie, który zbieranie tych danych świadomie włączył",
        "obecność na etapie wraz z godziną odnotowania",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "koordynator konkursu – uwaga o potrzebach szczególnych nie jest pokazywana recenzentom "
        "ani opiekunom szkolnym i nie wychodzi eksportem integracyjnym ani webhookiem",
        "obiekt noclegowy i firma gastronomiczna – wyłącznie liczby osób oraz zakres diety "
        "niezbędny do przygotowania posiłku, nigdy dokumentacja medyczna",
    ],
    retention=PARTICIPANT_RETENTION,
    measures=[
        "zbieranie potrzeb szczególnych jest domyślnie **wyłączone**; bez świadomej decyzji "
        "organizatora pola nie ma ani w formularzu, ani w eksportach",
        "pole uwag ma limit 500 znaków i etykietę mówiącą wprost, czego nie wpisywać "
        "(diagnozy, nazwy chorób, leki, orzeczenia)",
        "treść uwagi nie trafia do dziennika zdarzeń – audyt notuje wyłącznie fakt jej złożenia",
    ],
)


#: Czynność **warunkowa**: wchodzi do rejestru wyłącznie konkursom, które prowadzą forum
#: uczestników (przełącznik ``participant_forum``, prośba organizatora z 21.09.2026). Obok
#: :data:`ACTIVITIES` z tego samego powodu, co logistyka wyżej: rejestr ma opisywać przetwarzanie,
#: które **naprawdę zachodzi**. Konkurs z wyłączonym forum nie zbiera ani jednego wpisu, więc jego
#: rejestr nie zmienia się o ani jeden wiersz.
#:
#: Wpis jest tu ostrożniejszy niż pozostałe i to jest celowe. Pozostałe czynności opisują dane,
#: które serwis **od kogoś dostaje** w znanym kształcie (imię, szkoła, plik z rozwiązaniem). Tutaj
#: kategorią danych jest **tekst, który człowiek napisze sam**, i nikt z góry nie wie, co w nim
#: będzie – a piszą osoby niepełnoletnie. Dlatego wśród środków stoi to, czego forum **nie ma**
#: (załączników, wiadomości prywatnych, indeksowania), bo brak drogi jest tu skuteczniejszym
#: środkiem niż jakakolwiek kontrola nad treścią, która tą drogą by przyszła.
FORUM_ACTIVITY = _activity(
    key="forum",
    name="Forum uczestników konkursu",
    purpose=(
        "Umożliwienie uczestnikom zadawania pytań o organizację zawodów i rozmowy między sobą "
        "pod nadzorem organizatora, wraz z moderacją tych wypowiedzi."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora i uczestników – "
        "sprawna komunikacja w czasie zawodów oraz bezpieczeństwo rozmowy osób niepełnoletnich); "
        "korzystanie z forum jest dobrowolne i nie warunkuje udziału w zawodach"
    ),
    subjects="uczestnicy konkursu, członkowie komitetu i koordynator, którzy piszą na forum",
    categories=[
        "treść wypowiedzi napisana przez użytkownika (tekst, bez załączników i bez HTML-a)",
        "podpis pod wypowiedzią: imię i pierwsza litera nazwiska – nigdy adres e-mail, szkoła "
        "ani kod publiczny uczestnika",
        "data napisania i data poprawki wypowiedzi",
        "treść zgłoszenia wpisu do moderatora wraz z tożsamością zgłaszającego",
        "decyzja moderacyjna: stan wpisu, osoba i czas decyzji oraz uzasadnienie dla autora",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "pozostali zalogowani uczestnicy tego konkursu oraz członkowie jego komitetu – forum nie "
        "jest publiczne, nie da się go przeczytać bez konta i nie jest indeksowane przez "
        "wyszukiwarki",
        "koordynator konkursu jako moderator – wyłącznie on widzi zgłoszenia wpisów i tożsamość "
        "osoby zgłaszającej",
    ],
    retention=(
        "wypowiedzi zostają w wątku bezterminowo, bo są częścią rozmowy, do której odnoszą się "
        "odpowiedzi innych osób; po anonimizacji albo usunięciu konta znika podpis pod nimi "
        "(zostaje napis „Użytkownik usunięty”), a sama treść przestaje być powiązana z osobą"
    ),
    measures=[
        "forum jest domyślnie **wyłączone**: bez świadomej decyzji organizatora nie istnieje ani "
        "jeden adres, pod którym dałoby się cokolwiek napisać albo przeczytać",
        "czytanie wymaga zalogowania i roli w tym konkursie – wypowiedzi osób niepełnoletnich nie "
        "są dostępne dla nikogo z zewnątrz ani dla wyszukiwarek",
        "podpis powstaje w jednym miejscu w kodzie (``apps.forum.models.display_author``) i jest "
        "imieniem z inicjałem: kod publiczny uczestnika, który jest kluczem anonimowego "
        "oceniania, nie pojawia się na forum w żadnej postaci",
        "moderacja wstępna jest domyślna, a w czasie etapu przyjmującego rozwiązania obowiązuje "
        "**zawsze**, niezależnie od ustawienia konkursu",
        "wypowiedź jest tekstem: forum nie przyjmuje załączników ani HTML-a, więc nie jest drogą "
        "wnoszenia plików do serwisu",
        "forum nie ma wiadomości prywatnych – rozmowa osób niepełnoletnich zawsze odbywa się "
        "w miejscu, które widzi moderator",
        "usunięcie własnej wypowiedzi jest natychmiastowe dla czytelników; wiersz zostaje wyłącznie "
        "po to, żeby zgłoszony wpis nie znikał na żądanie autora",
        "każda decyzja moderatora zostawia wpis w dzienniku zdarzeń **bez kopii treści** wypowiedzi",
    ],
)


def activities_for(competition=None) -> tuple[ProcessingActivity, ...]:
    """Rejestr **tego** konkursu: czynności wspólne plus te, które wynikają z jego konfiguracji.

    Jedno wejście dla ekranu ``/coordinator/processing-register/`` i dla eksportu CSV. Rejestr
    przestał być jedną listą w chwili, w której dwa konkursy w jednej instalacji zaczęły zbierać
    różne dane – a dopisanie czynności „na zapas” byłoby opisaniem przetwarzania, którego u danego
    administratora nie ma.

    ``None`` znaczy „nie wiadomo, o który konkurs chodzi” i daje rejestr podstawowy: to samo, co
    widział czytelnik przed etapem 2.
    """
    from apps.competitions.logistics import collects_special_needs
    from apps.forum.models import FORUM_FLAG

    activities = ACTIVITIES
    if collects_special_needs(competition):
        activities = (*activities, ONSITE_LOGISTICS_ACTIVITY)
    if competition is not None and competition.has_feature(FORUM_FLAG):
        activities = (*activities, FORUM_ACTIVITY)
    return activities


#: Nagłówki eksportu CSV. Kolejność i brzmienie są kontraktem tego pliku – rejestr bywa wklejany
#: do dokumentacji organizatora i do korespondencji z organem nadzorczym.
CSV_HEADERS = (
    "Czynność przetwarzania",
    "Cel",
    "Podstawa prawna",
    "Kategorie osób",
    "Kategorie danych",
    "Odbiorcy",
    "Okres przechowywania",
    "Środki techniczne i organizacyjne",
)


def as_rows(activities: tuple[ProcessingActivity, ...] | None = None) -> list[list[str]]:
    """Rejestr jako wiersze tekstu – materiał eksportu CSV.

    Listy wieloelementowe sklejamy średnikiem, a nie nową linią: plik CSV z wieloliniowymi
    komórkami otwiera się poprawnie w arkuszu, ale przestaje się czytać w terminalu i w diffie,
    a to jest drugi sposób, w jaki ten rejestr bywa oglądany.

    Bez argumentu oddaje rejestr podstawowy (:data:`ACTIVITIES`), czyli dokładnie to, co oddawał
    przed etapem 2. Wołający, który wie, o który konkurs chodzi, podaje wynik :func:`activities_for`
    i dostaje plik opisujący przetwarzanie tego konkursu.
    """
    activities = ACTIVITIES if activities is None else activities
    return [
        [
            activity.name,
            activity.purpose,
            activity.legal_basis,
            activity.subjects,
            "; ".join(activity.categories),
            "; ".join(activity.recipients),
            activity.retention,
            "; ".join(activity.measures),
        ]
        for activity in activities
    ]
