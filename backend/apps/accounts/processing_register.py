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

from dataclasses import dataclass, field, replace
from datetime import date

from apps.competitions.models import DEFAULT_RETENTION_MONTHS

#: Wersja treści rejestru i data jej przyjęcia. Zmieniane ręcznie, razem z treścią niżej.
#: 1.1 (20.09.2026) – przekazywanie przyjętych rozwiązań na skrzynkę organizatora dokłada drogę,
#: którą prace uczestników wychodzą poza serwer, więc jest zmianą materialną, a nie literówką.
#: 1.2 (21.09.2026) – forum uczestników dokłada przetwarzanie, którego w serwisie dotąd nie było:
#: **treść pisaną publicznie przez osoby niepełnoletnie**, podpisaną imieniem i inicjałem nazwiska
#: i czytaną przez innych uczestników. Nowa kategoria danych i nowy krąg odbiorców to zmiana
#: materialna z podręcznikowego przykładu, a nie doprecyzowanie istniejącego wiersza.
#: 1.4 (22.09.2026) – rejestracja pyta o **pełną datę urodzenia** zamiast samego rocznika.
#: Zakres danych o osobie się poszerza (dzień i miesiąc urodzin to klasyczny klucz dopasowania do
#: innych zbiorów), więc jest to zmiana materialna, choć cel przetwarzania zostaje ten sam.
#: 1.5 (22.09.2026) – konta opiekunów szkolnych dostają **własny wiersz** rejestru: rola istniała
#: już wcześniej, ale nie była tu opisana wcale, a od tej zmiany zbiera też zgody (regulamin,
#: RODO) i wchodzi do eksportu i anonimizacji konta. Nowa czynność przetwarzania z własnym kręgiem
#: osób jest z definicji zmianą materialną, nie doprecyzowaniem istniejącego wiersza.
#: 1.6 (23.09.2026) – statystyka pobrań plakatów (``apps.promo``) liczy pobrania z **unikalnych
#: adresów IP** w dowolnym okresie, więc przy każdym pobraniu zostaje pseudonim adresu IP (HMAC
#: z kluczem serwera). Nowa kategoria danych o osobach, które nie mają w serwisie konta, i nowy
#: termin usunięcia – zmiana materialna, a nie doprecyzowanie wiersza „serwis”.
#: 1.7 (24.09.2026, wydanie v0.34.0) – trzy nowe czynności, każda **warunkowa** (jak forum: wiersz
#: wchodzi do rejestru wyłącznie konkursom z włączoną flagą, bo rejestr opisuje przetwarzanie, które
#: naprawdę zachodzi), a numer wersji jest jeden dla całego dokumentu:
#:
#: - zaświadczenie o statusie ucznia (``apps.student_status``, flaga ``student_status_certificate``):
#:   skan dokumentu z pieczątką szkoły, datą urodzenia i podpisem dyrektora oraz decyzja
#:   koordynatora – nowa kategoria danych (obraz dokumentu, dane pracownika szkoły w podpisie)
#:   i własny termin usunięcia pliku,
#: - materiały z warsztatów (``apps.workshop_materials``, flaga ``workshop_materials``) liczą
#:   **unikalnych widzów** materiału, więc przy pierwszym wyświetleniu zostaje pseudonim pary
#:   (materiał, konto) – nowa kategoria danych z własnym terminem usunięcia,
#: - ocena AI (``apps.ai_grading``, flaga ``ai_grading``) przekazuje **prace uczestników** nowemu
#:   podmiotowi przetwarzającemu (Anthropic) poza serwerem organizatora, w tym poza EOG – nowy
#:   odbiorca, nowy cel pomocniczy i przekazanie do państwa trzeciego.
#:
#: Każda z nich osobno byłaby zmianą materialną; wchodzą w jednym wydaniu, więc w jednej wersji.
#: 1.8 (24.09.2026) – ocena AI dostaje **innych dostawców** (OpenAI, Google, Meta obok Anthropic).
#: Odbiorcy wiersza „ocena AI” przestają być stałą: rejestr konkursu wymienia wyłącznie dostawców,
#: którzy mają klucz API **i** potwierdzoną przez organizatora umowę powierzenia – bo tylko do nich
#: mogą trafić prace uczestników. Nowi odbiorcy (każdy poza EOG albo z przetwarzaniem poza EOG) to
#: zmiana materialna, a nie doprecyzowanie.
#: 1.9 (25.09.2026) – powiadomienia e-mail z forum (``apps.forum.notifications``). **Nowego celu nie
#: ma** i dlatego nie ma nowego wiersza: list o odpowiedzi w obserwowanym wątku albo o decyzji
#: moderatora jest częścią celu „sprawna komunikacja w czasie zawodów”, na tej samej podstawie
#: (art. 6 ust. 1 lit. f), z prawem sprzeciwu w postaci wypisu jednym kliknięciem w każdym liście.
#: Wiersz forum dostaje za to nowego **odbiorcę** (dostawca poczty wychodzącej – przez niego idzie
#: temat wątku i sam fakt udziału w rozmowie) i nową **kategorię danych** (obserwowane wątki,
#: ustawienia powiadomień, znaczniki wysyłki). Nowy odbiorca to zmiana materialna – ta sama
#: reguła, co przy wersji 1.8.
#: 1.10 (30.09.2026, zadanie CZ-01) – Wiadomości: rozmowy 1:1 uczestnika z organizatorem i (za
#: decyzją koordynatora) uczestników między sobą. Nowa czynność z własnym celem i nowym kręgiem
#: odbiorców (druga strona rozmowy, moderator w trybach z moderacją i przy zgłoszeniach), w tym
#: opcjonalne szyfrowanie end-to-end rozmów między uczestnikami. Zdanie w wierszu forum „forum nie
#: ma wiadomości prywatnych” zostało doprecyzowane: forum ich nadal nie ma, ale serwis – tak.
#: 1.11 (04.10.2026, zadanie DEL-01) – delegacje krajowe: opiekun drużyny narodowej zakłada konta
#: uczniom swojego kraju. Nowa czynność (warunkowa – wyłącznie konkursy z trybem rejestracji
#: ``DELEGATIONS``) z nowym kręgiem osób (opiekunowie drużyn) i nowym odbiorcą danych uczniów
#: (współopiekunowie tego samego kraju widzą listę drużyny).
REGISTER_VERSION = "1.11"
REGISTER_DATE = date(2026, 10, 4)

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
            (
                "klasa oraz data urodzenia – od wieku zależy zdolność do czynności prawnych, "
                "czyli to, czy udział w zawodach wymaga zgody rodzica albo opiekuna prawnego. "
                "Do 21.09.2026 zbieraliśmy sam rocznik i rozstrzygaliśmy tę kwestię "
                "przybliżeniem na korzyść ochrony małoletniego; pełna data zamienia "
                "przybliżenie na rozstrzygnięcie i nie służy żadnemu innemu celowi"
            ),
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
        key="opiekunowie",
        name="Prowadzenie kont opiekunów szkolnych",
        purpose=(
            "Umożliwienie nauczycielowi wglądu w postęp prac uczniów, którzy sami wskazali jego "
            "adres w swoim profilu, oraz potwierdzenie udziału szkoły w danej edycji. Konto nie "
            "daje dostępu do żadnych danych, dopóki żaden uczeń go nie wskaże."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes organizatora – kontakt ze "
            "szkołą prowadzącą uczniów do olimpiady) oraz art. 6 ust. 1 lit. a RODO dla zgód "
            "wyrażonych przy rejestracji (regulamin, RODO)"
        ),
        subjects="nauczyciele rejestrujący się jako opiekunowie szkolni uczestników",
        categories=[
            "imię i nazwisko",
            "adres e-mail (jest zarazem loginem i kluczem dopasowania do profilu ucznia)",
            "numer telefonu (opcjonalnie)",
            "nazwa szkoły (wolny tekst – opiekun bywa nauczycielem uczniów z kilku placówek)",
            "rodzaj zgody, wersja dokumentu, data wyrażenia i wycofania (regulamin, RODO)",
            "potwierdzenie „szkoła bierze udział w tej edycji” wraz z datą",
        ],
        recipients=[HOSTING_RECIPIENT, MAIL_RECIPIENT],
        retention=(
            "adres istnieje wyłącznie, gdy organizator włączył tę rolę przełącznikiem "
            "`SiteSettings.supervisor_registration_enabled` na danej witrynie; konto nieaktywowane "
            "kasuje kosiarka po 24 godzinach (jak konto uczestnika). Automatyczna retencja "
            "(`/coordinator/retention/`) świadomie **nie obejmuje jeszcze** kont opiekunów – dług "
            "udokumentowany w `apps.accounts.retention` i w § 9.1 podręcznika organizatora; do "
            "czasu jej wdrożenia konto czyści się ręcznie z panelu koordynatora, tak samo jak na "
            "żądanie właściciela (`/account/delete/`)"
        ),
        measures=[
            "aktywacja konta linkiem e-mail, CAPTCHA i limit prób – ten sam komplet, co przy "
            "rejestracji uczestnika",
            "uprawnienie do wglądu w postęp konkretnego ucznia pochodzi wyłącznie od decyzji tego "
            "ucznia (wpisanie adresu opiekuna w profilu) i jest przez niego odwracalne jednym "
            "wyczyszczeniem pola",
            "usunięcie konta (samoobsługowe albo przez koordynatora) scala szkołę, telefon i "
            "zgody z resztą anonimizacji – wycofane zgody i wyczyszczone dane zostają, "
            "potwierdzenia udziału szkoły w edycji zostają jako dokumentacja zawodów",
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
    # Statystyka pobrań plakatów (prośba organizatora z 23.09.2026, ``apps.promo``). Osobny wiersz,
    # a nie kolejna kategoria w „serwisie”: tamten opisuje bezpieczeństwo i analitykę **za zgodą**,
    # a tu przetwarzanie idzie bez zgody, na uzasadnionym interesie, i ma **własny** termin usunięcia
    # egzekwowany zadaniem (``apps.promo.tasks.clear_expired_ip_hashes``). Wiersz jest bezwarunkowy:
    # ekran plakatów nie ma flagi, a pierwszy opublikowany plakat zaczyna przetwarzanie bez udziału
    # operatora.
    _activity(
        key="plakaty",
        name="Statystyka pobrań materiałów promocyjnych (plakatów)",
        purpose=(
            "Policzenie, ile razy i z ilu różnych adresów IP pobrano plakaty i ulotki olimpiady "
            "udostępnione na stronie serwisu – do oceny zasięgu promocji wśród szkół."
        ),
        legal_basis=(
            "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – ocena "
            "skuteczności promocji olimpiady); pobranie plakatu nie wymaga konta ani zgody"
        ),
        subjects="osoby pobierające plakaty ze strony serwisu (głównie nauczyciele i uczniowie)",
        categories=[
            "pseudonim adresu IP: HMAC-SHA256 z kluczem przechowywanym wyłącznie po stronie serwera – "
            "bez samego adresu IP, bez nagłówka przeglądarki i bez powiązania z kontem",
            "data i godzina pobrania oraz wskazanie pobranego pliku",
        ],
        recipients=[
            HOSTING_RECIPIENT,
            "koordynator konkursu – wyłącznie liczby zbiorcze (pobrania i unikalne adresy w oknach "
            "7 dni, 30 dni i od początku); pojedynczych pseudonimów nie widzi nikt w interfejsie",
        ],
        retention=(
            "pseudonim adresu IP – 12 miesięcy od pobrania, po czym jest automatycznie zerowany "
            "(zadanie dzienne); samo zdarzenie pobrania (data, plik) bez pseudonimu zostaje "
            "bezterminowo jako liczba pobrań łącznie"
        ),
        measures=[
            "adres IP nie jest zapisywany w żadnej postaci odwracalnej – do bazy trafia wyłącznie "
            "HMAC z kluczem wyprowadzonym z sekretu aplikacji, którego nie ma w bazie ani w jej kopii",
            "adres klienta brany wyłącznie z połączenia albo z nagłówka zaufanego odwrotnego proxy; "
            "nagłówki podane przez klienta są ignorowane",
            "roboty, podglądy linków i żądania HEAD nie są liczone ani zapisywane",
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
        "powiadomienia e-mail: obserwowane wątki, ustawienia powiadomień konta i znaczniki czasu "
        "wysyłki – bez kopii treści wpisów",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        MAIL_RECIPIENT + " – powiadomienia z forum: temat opublikowanego wątku, liczba nowych "
        "wpisów i uzasadnienie decyzji moderatora dla autora; nigdy treść wpisu",
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
        "forum nie ma wiadomości prywatnych – rozmowa na forum zawsze odbywa się w miejscu, które "
        "widzi moderator; rozmowy 1:1 prowadzi osobny moduł Wiadomości z własnymi zasadami moderacji "
        "(osobny wiersz rejestru)",
        "usunięcie własnej wypowiedzi jest natychmiastowe dla czytelników; wiersz zostaje wyłącznie "
        "po to, żeby zgłoszony wpis nie znikał na żądanie autora",
        "każda decyzja moderatora zostawia wpis w dzienniku zdarzeń **bez kopii treści** wypowiedzi",
        "powiadomienia e-mail są zbiorcze i nie niosą treści wypowiedzi – forum da się przeczytać "
        "wyłącznie po zalogowaniu także wtedy, gdy list trafi do cudzej skrzynki; nic "
        "niepublikowanego nie wychodzi pocztą, a każdy list ma wypis jednym kliknięciem (bez "
        "logowania) – to jest forma prawa sprzeciwu z art. 21 RODO",
    ],
)


#: Czynność **warunkowa**: Wiadomości (zadanie CZ-01, 30.09.2026) – wchodzi do rejestru, gdy moduł
#: jest w konkursie włączony (``ChatSettings.enabled``, domyślnie tak: kanał „napisz do organizatora”
#: działa od razu). Tak jak przy forum, kategorią danych jest tekst pisany przez osoby niepełnoletnie,
#: tylko że tutaj **bez świadków**, więc wśród środków stoi to, kto i kiedy może tę treść przeczytać –
#: i to jest obietnica, którą nadawca czyta nad formularzem (``apps.chat.services.moderator_visible_q``).
CHAT_ACTIVITY = _activity(
    key="wiadomosci",
    name="Wiadomości na platformie (rozmowy 1:1)",
    purpose=(
        "Umożliwienie uczestnikom kontaktu z organizatorem konkursu w sprawach zawodów, a – jeśli "
        "organizator to włączy – rozmów uczestników między sobą, wraz z moderacją tych rozmów "
        "i obsługą zgłoszeń."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora i uczestników – sprawna "
        "komunikacja w sprawach zawodów oraz bezpieczeństwo rozmów osób niepełnoletnich); korzystanie "
        "z wiadomości jest dobrowolne i nie warunkuje udziału w zawodach"
    ),
    subjects="uczestnicy konkursu i koordynatorzy (zespół organizatora), którzy piszą wiadomości",
    categories=[
        "treść wiadomości napisana przez użytkownika (tekst, bez załączników i bez HTML-a) – w rozmowie "
        "szyfrowanej end-to-end wyłącznie szyfrogram, którego serwer nie umie odczytać",
        "metadane: strony rozmowy, data wysłania, stan moderacji, znaczniki odczytu i powiadomień",
        "podpis przy wiadomości: imię i pierwsza litera nazwiska (wobec innych uczestników); organizator "
        "widzi pełne dane uczestnika, jak w całym panelu",
        "zgoda na obecność w katalogu uczestników (imię, inicjał nazwiska, województwo) i blokady innych "
        "uczestników",
        "zgłoszenia wiadomości wraz z tożsamością zgłaszającego; przy wiadomości szyfrowanej – treść "
        "odszyfrowana i przekazana przez zgłaszającego",
        "rozmowy szyfrowane: klucz publiczny i kopia klucza prywatnego zaszyfrowana hasłem, którego "
        "serwer nie zna",
        "ustawienie powiadomień e-mail o nowej wiadomości",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        MAIL_RECIPIENT + " – powiadomienie „masz nową wiadomość” z podpisem nadawcy i odnośnikiem; nigdy "
        "treść wiadomości",
        "druga strona rozmowy; w rozmowie z organizatorem – każdy koordynator konkursu (wspólna skrzynka)",
        "koordynator jako moderator rozmów między uczestnikami – wyłącznie wiadomości wysłane w trybie "
        "z moderacją (premoderacja, postmoderacja) oraz wiadomości zgłoszone; w trybie „bez moderacji” "
        "tylko zgłoszone, a w rozmowie szyfrowanej – tylko treść przekazana przez zgłaszającego",
    ],
    retention=(
        "wiadomości zostają w rozmowie, bo są jej częścią dla drugiej strony; po anonimizacji albo "
        "usunięciu konta znika podpis (zostaje napis „Użytkownik usunięty”), a profil katalogu, klucz "
        "szyfrowania, blokady i ustawienia powiadomień są usuwane"
    ),
    measures=[
        "rozmowy między uczestnikami są domyślnie **wyłączone**; tryb (premoderacja, postmoderacja, bez "
        "moderacji) wybiera organizator, a w czasie etapu przyjmującego rozwiązania obowiązuje "
        "premoderacja niezależnie od ustawienia (rozmowy szyfrowane są wtedy tylko do odczytu)",
        "nad formularzem nadawca czyta, kto może przeczytać jego wiadomość; moderator widzi treść "
        "wyłącznie w tym zakresie – nie ma ekranu przeglądania rozmów uczestników",
        "katalog uczestników jest dobrowolny (zgoda wyrażana przełącznikiem) i pokazuje tylko imię, "
        "inicjał nazwiska i województwo – nigdy adres e-mail, szkołę ani kod publiczny",
        "blokowanie innych uczestników i zgłaszanie wiadomości do organizatora w każdym trybie",
        "opcjonalne szyfrowanie end-to-end (WebCrypto: ECDH P-256, HKDF-SHA-256, AES-GCM; kopia klucza "
        "prywatnego chroniona PBKDF2-SHA-256 z hasłem, którego serwer nie zna) – dopuszczalne tylko "
        "w trybie bez moderacji; kanał organizatora nie jest nigdy szyfrowany",
        "wiadomość jest tekstem: bez załączników i bez HTML-a; listy e-mail nie niosą treści",
        "każda decyzja moderatora zostawia wpis w dzienniku zdarzeń **bez kopii treści** wiadomości",
    ],
)


#: Czynność **warunkowa**: zaświadczenia o statusie ucznia (prośba organizatora z 24.09.2026,
#: flaga ``student_status_certificate``). Obok :data:`ACTIVITIES` z tego samego powodu, co forum:
#: rejestr opisuje przetwarzanie, które **naprawdę zachodzi**, a konkurs bez flagi nie zbiera ani
#: jednego skanu.
#:
#: Skan jest tu ostrożniejszy od pozostałych kategorii z jednego powodu: to obraz **dokumentu**,
#: więc niesie więcej, niż serwis prosi – pieczątkę szkoły, podpis i nazwisko dyrektora albo
#: sekretarza (dane osoby trzeciej, pracownika szkoły), czasem dopiski odręczne. Dlatego wśród
#: środków stoi to, czego ten plik **nie** robi: nie trafia do recenzentów, nie jest przekazywany
#: e-mailem i nie przeżywa ani zastąpienia nowszym, ani terminu retencji edycji.
STUDENT_STATUS_ACTIVITY = _activity(
    key="status-ucznia",
    name="Weryfikacja statusu ucznia (zaświadczenie ze szkoły)",
    purpose=(
        "Potwierdzenie, że uczestnik jest w danym roku szkolnym uczniem szkoły – warunek udziału "
        "wynikający z Regulaminu – oraz możliwość przekazania komitetowi do oceny wyłącznie prac "
        "uczniów z potwierdzonym statusem."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. b RODO (wykonanie umowy – weryfikacja warunków udziału w zawodach "
        "określonych Regulaminem); dane pracownika szkoły w podpisie – art. 6 ust. 1 lit. f RODO "
        "(prawnie uzasadniony interes administratora: wiarygodność dokumentu)"
    ),
    subjects=(
        "uczestnicy konkursu; dyrektorzy i sekretarze szkół podpisujący zaświadczenie (w zakresie "
        "podpisu i pieczątki)"
    ),
    categories=[
        "skan albo zdjęcie zaświadczenia: imię i nazwisko, data urodzenia, nazwa szkoły, klasa, rok "
        "szkolny, pieczątka szkoły, data oraz podpis dyrektora lub sekretarza szkoły",
        "metadane pliku: skrót SHA-256, rozmiar, typ, wynik skanu antywirusowego, data przesłania",
        "decyzja koordynatora: stan (oczekuje, zaakceptowane, odrzucone), powód odrzucenia, osoba "
        "i czas decyzji",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        MAIL_RECIPIENT + " – wyłącznie powiadomienie o decyzji, bez załącznika",
        "koordynator konkursu – jedyna rola, która widzi skan; członkowie komitetu i komisji "
        "odwoławczej dostają wyłącznie paczkę prac zawężoną filtrem, bez skanu i bez danych "
        "osobowych",
    ],
    retention=(
        "plik zastąpiony nowszym – usuwany natychmiast; pozostałe pliki – do upływu okresu "
        "przechowywania danych uczestników edycji (" + PARTICIPANT_RETENTION.split(" (")[0] + "), po "
        "czym usuwane automatycznie (zadanie dobowe); przy usunięciu albo anonimizacji konta – "
        "natychmiast, razem z zapisem decyzji. Zapis decyzji bez pliku zostaje do anonimizacji konta"
    ),
    measures=[
        "funkcja jest domyślnie **wyłączona**; bez świadomej decyzji organizatora nie ma adresu, "
        "pod którym dałoby się przesłać albo obejrzeć skan",
        "plik w prywatnym magazynie, bez publicznego adresu; odczyt wyłącznie przez aplikację, po "
        "uprawnieniu koordynatora tego konkursu, każde otwarcie zapisywane w dzienniku zdarzeń",
        "format rozpoznawany po treści (PDF, JPG, PNG), limit 10 MB, skan antywirusowy przed "
        "udostępnieniem koordynatorowi; plik zainfekowany jest usuwany",
        "nazwa pliku od uczestnika nie jest zapisywana; klucz w magazynie składa się z identyfikatorów "
        "technicznych i skrótu treści",
        "recenzenci i komisja nie mają dostępu do skanu ani do stanu zaświadczenia poszczególnych "
        "osób – filtr paczki działa po stronie serwera, a pliki w paczce zostają anonimowe",
        "dziennik zdarzeń notuje wgranie, podgląd i decyzję bez treści powodu odrzucenia",
    ],
)


#: Czynność **warunkowa**: statystyka oglądania materiałów z warsztatów. Wchodzi do rejestru wyłącznie
#: konkursom z włączonym przełącznikiem ``workshop_materials`` – ten sam powód, co przy forum wyżej:
#: konkurs bez tej funkcji nie zapisuje ani jednego pseudonimu widza.
#:
#: Sama treść materiałów (nagrania, slajdy) **nie** jest tu opisana: to materiały organizatora, nie
#: dane uczestników. Opisana jest wyłącznie statystyka, bo tylko ona dotyka osób, które oglądają.
WORKSHOP_MATERIALS_ACTIVITY = _activity(
    key="materialy-z-warsztatow",
    name="Statystyka wyświetleń materiałów z warsztatów",
    purpose=(
        "Policzenie, ile razy i ile różnych kont otworzyło nagrania i pliki z warsztatów – do oceny, "
        "które materiały są potrzebne uczestnikom i czy warto nagrywać kolejne zajęcia."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – ocena przydatności "
        "materiałów edukacyjnych udostępnianych uczestnikom)"
    ),
    subjects="zalogowani uczestnicy, opiekunowie szkolni i członkowie komitetu oglądający materiały",
    categories=[
        "pseudonim pary (materiał, konto): HMAC-SHA256 z kluczem przechowywanym wyłącznie po stronie "
        "serwera – bez identyfikatora konta, adresu IP i nagłówka przeglądarki; ta sama osoba przy "
        "dwóch materiałach ma dwa niepowiązane pseudonimy",
        "chwila pierwszego wyświetlenia materiału (do terminu usunięcia)",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "koordynator konkursu – wyłącznie liczby zbiorcze przy materiale (wyświetlenia i liczba "
        "różnych widzów); pojedynczych pseudonimów nie widzi nikt w interfejsie",
    ],
    retention=(
        "pseudonim widza – 12 miesięcy od pierwszego wyświetlenia, po czym jest automatycznie "
        "kasowany (zadanie cogodzinne) albo wcześniej razem z materiałem; licznik wyświetleń bez "
        "żadnej informacji o osobie zostaje przy materiale"
    ),
    measures=[
        "funkcja jest domyślnie wyłączona – bez decyzji organizatora nie powstaje ani jeden pseudonim",
        "w bazie nie ma identyfikatora konta przy wyświetleniu – wyłącznie HMAC z kluczem "
        "wyprowadzonym z sekretu aplikacji, którego nie ma w bazie ani w jej kopii",
        "materiał wchodzi do skrótu, więc z tabeli nie da się złożyć historii oglądania jednej osoby",
        "wyświetlenia koordynatora nie są zapisywane",
    ],
)


#: Czynność **warunkowa**: webinary w LiveKit (zadanie WEB-01, przełącznik ``webinars``). Wchodzi
#: do rejestru wyłącznie konkursom z włączonym przełącznikiem – ten sam powód, co forum. Serwer
#: LiveKit jest **własny** (instalacja operatora platformy albo maszyna organizatora), nagrania leżą
#: w prywatnym buckecie platformy – nie ma tu zewnętrznego dostawcy wideo.
WEBINARS_ACTIVITY = _activity(
    key="webinary",
    name="Webinary online (LiveKit)",
    purpose=(
        "Prowadzenie spotkań online organizatora z uczestnikami i komisją (wykłady, konsultacje, "
        "omówienia zadań), lista obecności, zaproszenia i przypomnienia oraz – gdy prowadzący włączy "
        "nagrywanie – udostępnienie nagrań odbiorcom webinaru."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – prowadzenie zajęć "
        "towarzyszących konkursowi); listy – z możliwością wyłączenia jednym przełącznikiem"
    ),
    subjects="uczestnicy, członkowie komisji i goście z linku, którzy wchodzą na webinar",
    categories=[
        "nazwa wyświetlana w pokoju (imię i inicjał nazwiska; gość – nazwa wpisana przez siebie)",
        "pseudonim konta (HMAC – bez identyfikatora i adresu e-mail konta)",
        "czas wejścia i wyjścia z pokoju (lista obecności)",
        "obraz, głos i wiadomości czatu – wyłącznie gdy osoba je włączy; w nagraniu – gdy prowadzący nagrywa",
        "adres e-mail – do zaproszenia i przypomnienia (bez tokenu wejścia)",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "serwer LiveKit operatora platformy (przekazywanie obrazu i dźwięku w czasie rzeczywistym)",
        "koordynatorzy i prowadzący webinaru – lista obecnych i nagrania",
        "YouTube (Google) – wyłącznie gdy koordynator włączy transmisję na żywo",
    ],
    retention=(
        "obraz i dźwięk – nie są przechowywane poza nagraniem; nagrania i lista obecności – "
        "kasowane automatycznie po WEBINAR_RETENTION_DAYS dniach od końca webinaru (domyślnie 365; "
        "wcześniej – na żądanie koordynatora albo przy usunięciu konta); wpisy audytu wejść – jak "
        "pozostały audyt"
    ),
    measures=[
        "funkcja domyślnie wyłączona i wymaga własnego serwera LiveKit",
        "token wejścia powstaje przy każdym wejściu po sprawdzeniu uprawnień, żyje kilka minut "
        "i nie trafia do listów ani do stron",
        "widz nie nadaje obrazu ani dźwięku, dopóki prowadzący nie da mu głosu",
        "nagranie widzą odbiorcy webinaru dopiero po opublikowaniu, przez adres ważny dwie godziny",
        "przed wejściem do pokoju webinaru z nagrywaniem – informacja o nagrywaniu; w trakcie "
        "nagrania – stały znacznik „Trwa nagrywanie”",
        "link dla gości domyślnie wyłączony",
    ],
)


#: Czynność **warunkowa**: nadzór zdalny etapów online (zadanie PROC-01, przełącznik ``proctoring``).
#: Przetwarzanie **wysokiego ryzyka** – obraz osób w większości niepełnoletnich, w ich domach – więc
#: wiersz nazywa wprost minimalizację (bez analizy automatycznej, niska rozdzielczość, nagrywanie
#: domyślnie wyłączone) i odsyła do oceny skutków (DPIA), którą organizator robi przed włączeniem.
PROCTORING_ACTIVITY = _activity(
    key="nadzor-zdalny",
    name="Nadzór zdalny etapów online (LiveKit)",
    purpose=(
        "Zapewnienie samodzielności pracy w etapach rozgrywanych online: podgląd na żywo obrazu z kamery "
        "uczestnika (i – gdy etap tego wymaga – ekranu lub dźwięku) przez osoby nadzorujące, wiadomości "
        "nadzorujących, notatki o incydentach do rozpatrzenia przez komisję, obecność oraz – wyłącznie "
        "gdy koordynator włączy je dla etapu – nagrania obrazu z kamery do celów odwoławczych."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. a RODO (wyraźna zgoda uczestnika, wersjonowana; u osoby niepełnoletniej – "
        "przy potwierdzonej online zgodzie rodzica lub opiekuna prawnego); osoba bez zgody albo bez "
        "kamery może poprosić o inną formę nadzoru (decyzja koordynatora)"
    ),
    subjects="uczestnicy etapów online z włączonym nadzorem (w większości osoby niepełnoletnie)",
    categories=[
        "wizerunek (obraz z kamery na żywo; nagranie – tylko przy włączonym nagrywaniu)",
        "obraz ekranu i głos – wyłącznie gdy etap tego wymaga",
        "zdjęcie dokumentu tożsamości – wyłącznie gdy etap tego wymaga",
        "pseudonim w pokoju (HMAC, osobny na etap), czasy połączeń i zerwań strumienia",
        "wynik sprawdzenia sprzętu (wartości logiczne i rodzina przeglądarki – bez odcisku urządzenia)",
        "wiadomości nadzorujących, notatki o incydentach, obecność, prośby o alternatywę (powód z listy)",
        "wersja i czas zgody, adres IP przy zgodzie",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "serwer LiveKit operatora platformy (przekazywanie obrazu w czasie rzeczywistym, "
        "nagrania przez Egress)",
        "nadzorujący wskazani przez koordynatora: koordynatorzy, członkowie komisji, w olimpiadzie "
        "międzynarodowej opiekun drużyny – wyłącznie uczniów swojej delegacji",
        "komisja odwoławcza – raport incydentów i nagrania (każde odtworzenie w audycie)",
    ],
    retention=(
        "obraz na żywo – nie jest przechowywany; nagrania, zdjęcia dokumentu, dziennik połączeń i "
        "wiadomości – usuwane automatycznie 30 dni po ogłoszeniu wyników i zamknięciu okna reklamacji "
        "(najpóźniej 180 dni po etapie; komisja może wstrzymać usunięcie do wyjaśnienia sprawy); "
        "incydenty, obecność i zgody – jak dokumentacja zawodów (retencja edycji)"
    ),
    measures=[
        "funkcja domyślnie wyłączona, włączana osobno dla każdego etapu; nagrywanie domyślnie wyłączone",
        "brak automatycznej analizy obrazu i śledzenia przeglądarki – decyzje podejmują ludzie",
        "kamera 320×240, 10 kl./s; uczeń nie odbiera obrazu innych uczniów (uprawnienia tokenu)",
        "opiekun drużyny dostaje token wyłącznie do pokoju swojej delegacji",
        "tokeny krótkotrwałe, wystawiane po sprawdzeniu roli przy każdym wejściu",
        "nagrania i zdjęcia w prywatnym buckecie, adres ważny 15 minut, audyt każdego odtworzenia",
        "ocena skutków dla ochrony danych (DPIA) przed pierwszym użyciem – nota w podręczniku organizatora",
    ],
)


#: Czynność **warunkowa**: wchodzi do rejestru wyłącznie konkursom z włączoną oceną AI
#: (przełącznik ``ai_grading``, prośba organizatora z 24.09.2026) – z tego samego powodu, co forum:
#: rejestr opisuje przetwarzanie, które naprawdę zachodzi.
#:
#: Podstawa prawna jest tu **propozycją do zatwierdzenia przez administratora**, a nie
#: rozstrzygnięciem kodu: ocena AI jest narzędziem pomocniczym komitetu (interes administratora
#: w sprawnym i spójnym ocenianiu), a nie warunkiem udziału w zawodach. Otwarte kwestie prawne –
#: umowa powierzenia, przekazanie do państwa trzeciego, retencja po stronie dostawcy, zmiana
#: polityki prywatności i regulaminu – stoją w ``docs/PODRECZNIK-ORGANIZATORA.md`` („Ocena AI”).
AI_GRADING_ACTIVITY = _activity(
    key="ocena_ai",
    name="Pomocnicza ocena prac uczestników przez model językowy (ocena AI)",
    purpose=(
        "Przygotowanie dla członka komitetu niewiążącej sugestii oceny pracy uczestnika "
        "(proponowane punkty, uzasadnienie, lista błędów) przez porównanie jej z rozwiązaniem "
        "wzorcowym i skalą punktacji. Ocenę wystawia wyłącznie człowiek."
    ),
    legal_basis=(
        "art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora – sprawne i spójne "
        "ocenianie prac w zawodach) – do potwierdzenia przez administratora; brak decyzji opartej "
        "wyłącznie na zautomatyzowanym przetwarzaniu w rozumieniu art. 22 RODO"
    ),
    subjects="uczestnicy konkursu, których prace koordynator skierował do oceny AI",
    categories=[
        "treść pracy uczestnika (plik PDF, zdjęcie, kod albo notatnik) – bez imienia, nazwiska, "
        "adresu e-mail, szkoły, kodu uczestnika i nazwy pliku nadanej przez uczestnika",
        "wygenerowana sugestia oceny: proponowane punkty, kryteria z komentarzami, podsumowanie, "
        "lista błędów, deklarowana pewność, znacznik podejrzenia próby manipulacji",
        "metadane przetwarzania: data zlecenia i przekazania, dostawca i model, identyfikator "
        "żądania, zużycie tokenów i szacowany koszt",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        "dostawcy modeli językowych, z którymi organizator potwierdził umowę powierzenia – lista "
        "w rejestrze konkursu (Anthropic, OpenAI, Google, Meta)",
        "członkowie komitetu recenzujący daną pracę i koordynator konkursu",
        "uczestnik – wyłącznie wtedy, gdy koordynator włączy widoczność dla etapu, i dopiero po "
        "ogłoszeniu wyników",
    ],
    retention=(
        "sugestia jest przechowywana razem z pracą, której dotyczy, i znika wraz z nią; przy "
        "anonimizacji konta uczestnika (na żądanie albo po upływie okresu retencji edycji) jest "
        "kasowana od razu. Po stronie dostawcy – zgodnie z warunkami umowy z danym dostawcą (okres "
        "przechowywania danych wejściowych i wyjściowych API, w tym na potrzeby wykrywania nadużyć, "
        "do potwierdzenia przez administratora osobno dla każdego dostawcy)"
    ),
    measures=[
        "funkcja jest domyślnie **wyłączona**; bez przełącznika konkursu, klucza API wpisanego przez "
        "koordynatora i potwierdzonej umowy powierzenia z danym dostawcą żadna praca uczestnika nie "
        "opuszcza serwera (potwierdzenie – z datą i osobą – zostaje w dzienniku zdarzeń)",
        "tryb testowy (praca testowa koordynatora) nie przenosi danych uczestników: wymaga "
        "oświadczenia koordynatora, a plik identyczny z pracą uczestnika jest odrzucany",
        "do dostawcy trafia wyłącznie plik pracy i materiały zadania – bez danych identyfikujących "
        "uczestnika; z odpowiedzi modelu serwer wymazuje imię, nazwisko, adres e-mail i szkołę "
        "autora, gdyby model przepisał je z pracy",
        "klucz API jest zaszyfrowany w bazie, tylko do zapisu (ekran pokazuje cztery ostatnie znaki), "
        "nie trafia do kolejki zadań ani do logów",
        "sugestia nigdy nie zapisuje się jako ocena – recenzent wystawia punkty sam, a przycisk "
        "„wstaw punkty AI” jedynie wypełnia formularz",
        "praca uczestnika jest dla modelu wyłącznie danymi: polecenia zapisane w pracy są ignorowane, "
        "a próba wpłynięcia na ocenę jest zgłaszana recenzentowi",
        "limit wydatków i ogranicznik współbieżności po stronie serwera; każde zlecenie, zmiana klucza "
        "i zmiana widoczności zostawia wpis w dzienniku zdarzeń",
    ],
)


def ai_grading_activity(competition=None) -> ProcessingActivity:
    """Wiersz „ocena AI” **tego** konkursu: odbiorcami są dostawcy z kluczem i potwierdzoną umową.

    Rejestr opisuje przetwarzanie, które naprawdę może zajść. Dostawca bez potwierdzonej umowy
    powierzenia nie dostaje prac uczestników (bramka w ``apps.ai_grading.services``), więc nie jest
    ich odbiorcą – dopisany „na zapas” byłby nieprawdą. Pusta lista to informacja, a nie brak
    informacji: wiersz mówi wtedy wprost, że prace nie opuszczają serwera.
    """
    if competition is None:
        return AI_GRADING_ACTIVITY
    from apps.ai_grading.services import register_recipients

    processors = [
        f"{name} na podstawie umowy powierzenia potwierdzonej przez organizatora; przekazanie do "
        "państwa trzeciego (poza EOG) na podstawie mechanizmu wskazanego w tej umowie (standardowe "
        "klauzule umowne albo decyzja stwierdzająca odpowiedni stopień ochrony)"
        for name in register_recipients(competition)
    ] or [
        "żaden dostawca modelu nie ma jednocześnie klucza API i potwierdzonej umowy powierzenia – "
        "prace uczestników nie opuszczają serwera"
    ]
    base = AI_GRADING_ACTIVITY.recipients
    return replace(AI_GRADING_ACTIVITY, recipients=[base[0], *processors, *base[2:]])


#: Czynność **warunkowa**: delegacje krajowe (DEL-01). Wchodzi do rejestru wyłącznie konkursom z trybem
#: rejestracji ``DELEGATIONS`` – Olimpiada Kwantowa takiego przetwarzania nie prowadzi.
#:
#: Dwie osoby, dwie podstawy: dane opiekuna drużyny przetwarzamy na podstawie jego zgody i umowy
#: (przyjmuje zaproszenie i akceptuje regulamin), a dane ucznia wpisane przez opiekuna – na podstawie
#: prawnie uzasadnionego interesu organizatora do chwili, w której uczeń sam uruchomi konto i złoży
#: zgody. Do tej chwili konto jest nieaktywne i nie bierze udziału w niczym.
DELEGATIONS_ACTIVITY = _activity(
    key="delegacje",
    name="Delegacje krajowe – zgłaszanie uczniów przez opiekunów drużyn narodowych",
    purpose=(
        "Zgłoszenie drużyny kraju do olimpiady międzynarodowej: koordynator zaprasza opiekuna "
        "drużyny, opiekun zakłada konto i zgłasza uczniów swojego kraju, uczniowie uruchamiają "
        "konta i składają zgody sami."
    ),
    legal_basis=(
        "opiekun drużyny – art. 6 ust. 1 lit. b RODO (udział w organizacji zawodów na zasadach "
        "Regulaminu) i lit. a dla zgód wyrażonych przy przyjęciu zaproszenia; uczeń – do uruchomienia "
        "konta art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes organizatora: przyjęcie "
        "zgłoszenia drużyny), od uruchomienia – jak w czynności „Prowadzenie kont uczestników”"
    ),
    subjects="opiekunowie drużyn narodowych; uczniowie zgłoszeni przez opiekunów",
    categories=[
        "opiekun: imię i nazwisko, adres e-mail, kraj delegacji, data przyjęcia zaproszenia, "
        "dowody zgód (regulamin, RODO)",
        "zaproszenie: adres e-mail, skrót tokenu (nie sam token), daty wysłania, ważności, przyjęcia "
        "i cofnięcia",
        "uczeń: imię i nazwisko, adres e-mail, data urodzenia, szkoła, klasa, kraj, opcjonalnie "
        "adres e-mail rodzica, informacja, który opiekun zgłosił ucznia",
    ],
    recipients=[
        HOSTING_RECIPIENT,
        MAIL_RECIPIENT,
        "współopiekunowie drużyny tego samego kraju – widzą imiona i adresy e-mail opiekunów oraz "
        "listę uczniów drużyny; opiekun nie widzi uczniów innych krajów, prac ani ocen",
    ],
    retention=(
        PARTICIPANT_RETENTION
        + " Zaproszenie i wiersz opiekuna znikają razem z kontem opiekuna (usunięcie albo "
        "anonimizacja); ślad zdarzeń zostaje w dzienniku bez adresów."
    ),
    measures=[
        "funkcja działa wyłącznie w konkursie przestawionym na tryb rejestracji przez delegacje",
        "zaproszenie ważne 14 dni, jednorazowe, w bazie wyłącznie skrót SHA-256 tokenu; ponowne "
        "wysłanie unieważnia poprzedni link; przyjęcie wymaga zgodności adresu konta z zaproszeniem",
        "konto ucznia powstaje nieaktywne i bez hasła – hasło i zgody składa uczeń sam",
        "limit uczniów delegacji egzekwowany pod blokadą wiersza, okno rejestracji edycji, "
        "limit żądań per konto",
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
    from apps.ai_grading.models import AI_GRADING_FLAG
    from apps.competitions.logistics import collects_special_needs
    from apps.forum.models import FORUM_FLAG

    activities = ACTIVITIES
    if collects_special_needs(competition):
        activities = (*activities, ONSITE_LOGISTICS_ACTIVITY)
    if competition is not None and competition.has_feature(FORUM_FLAG):
        activities = (*activities, FORUM_ACTIVITY)
    from apps.chat.services import is_enabled as chat_enabled

    if chat_enabled(competition):
        activities = (*activities, CHAT_ACTIVITY)
    from apps.student_status.models import enabled as student_status_enabled

    if student_status_enabled(competition):
        activities = (*activities, STUDENT_STATUS_ACTIVITY)
    if competition is not None and competition.has_feature("workshop_materials"):
        activities = (*activities, WORKSHOP_MATERIALS_ACTIVITY)
    if competition is not None and competition.has_feature(AI_GRADING_FLAG):
        activities = (*activities, ai_grading_activity(competition))
    if competition is not None and competition.has_feature("webinars"):
        activities = (*activities, WEBINARS_ACTIVITY)
    if competition is not None and competition.uses_delegations:
        activities = (*activities, DELEGATIONS_ACTIVITY)
    if competition is not None and competition.has_feature("proctoring"):
        activities = (*activities, PROCTORING_ACTIVITY)
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
