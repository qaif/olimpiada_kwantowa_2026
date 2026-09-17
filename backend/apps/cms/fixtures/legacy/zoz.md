# Zasady Organizacji Zawodów – I edycja 2026/2027

> Wersja robocza (0.2) do akceptacji organizatora – treść nie została jeszcze zatwierdzona przez Fundację Quantum AI ani sprawdzona przez Komitet Merytoryczny. Do tego czasu dokument służy wyłącznie jako projekt: wiążą Regulamin i Harmonogram, a nie ten tekst. Zapisy wymagające decyzji organizatora są wypisane w ostatniej sekcji.

Zasady Organizacji Zawodów (dalej „ZOZ”) I edycji Olimpiady Kwantowej 2026/2027. Dokument powstaje na podstawie § 1 ust. 4 [Regulaminu Olimpiady Kwantowej](/dokumenty/regulamin/): Regulamin określa zasady stałe, a ZOZ – szczegóły jednej edycji, w szczególności harmonogram, formę zadań, wykaz dozwolonych narzędzi, maksymalną liczbę finalistów, progi punktowe oraz literaturę i program merytoryczny.

Zgodnie z § 1 ust. 5 Regulaminu sprzeczność ZOZ z Regulaminem rozstrzyga się **na korzyść Regulaminu**. Dokumenty edycji należy czytać łącznie: [Regulamin](/dokumenty/regulamin/), ZOZ, [Harmonogram](/harmonogram/), [program warsztatów](/warsztaty/), [Politykę RODO](/dokumenty/rodo/) i [Standardy ochrony małoletnich](/dokumenty/standardy-ochrony-maloletnich/).

## § 1. Harmonogram edycji

Wszystkie terminy – otwarcie i zamknięcie każdego etapu, termin recenzji oraz okno reklamacji – określa [Harmonogram na stronie Olimpiady](/harmonogram/).

## § 2. Struktura zawodów i forma poszczególnych etapów

Edycja składa się z trzech etapów o różnej formie:

| Etap | Forma |
|---|---|
| Etap I | zawody zdalne; uczestnik rozwiązuje opublikowane zadania i przesyła prace przez system Olimpiady |
| Etap II | rozmowa kwalifikacyjna online; uczestnik nie przesyła plików, lecz zapisuje się w panelu na jeden z terminów rozmowy |
| Etap III | finał stacjonarny w Krakowie (4–7 czerwca 2027; termin i miejsce rozstrzyga Harmonogram) |

Zawody mają charakter indywidualny (§ 10 ust. 3 Regulaminu). Współpraca przy rozwiązywaniu zadań, zlecanie rozwiązania innej osobie i podszywanie się pod uczestnika są zabronione.

Numeracja etapów odpowiada § 11–13 Regulaminu oraz podpisom etapów w systemie („Etap I”, „Etap II”, „Etap III”). Rozbieżność z § 10 ust. 1 Regulaminu, który opisuje strukturę dwuetapową z rozmową jako procedurą pomocniczą, jest wskazana w ostatniej sekcji jako zapis do rozstrzygnięcia.

## § 3. Forma zadań i dozwolone formaty rozwiązań w Etapie I

Zadania Etapu I są publikowane jako pliki PDF. Treść zadania jest **nieosiągalna przed otwarciem etapu**: pliki z treściami leżą w prywatnym magazynie i system udostępnia je dopiero od chwili otwarcia etapu ogłoszonej w Harmonogramie.

Rozwiązania przesyła się przez panel uczestnika, osobno do każdego zadania. Format rozwiązania jest **parametrem zadania** i jest podany przy zadaniu. System rozpoznaje trzy formaty, w kolejności od najczęstszego:

- **.pdf** – format domyślny i jedyny dopuszczony, o ile treść zadania nie mówi inaczej. Plik musi być prawdziwym dokumentem PDF (rozpoznawanym po nagłówku %PDF-),
- **.ipynb** – notatnik Jupytera w formacie nbformat 4 lub nowszym, przechodzący walidację nbformat. Sumaryczny rozmiar wyników (outputów) zapisanych w notatniku nie może przekroczyć **2 MB**,
- **.py** – plik źródłowy w Pythonie, tekst UTF-8, nie większy niż **1 MB**.

Limit rozmiaru pliku jest ustalany dla każdego zadania osobno. Domyślnie wynosi **20 MB**, a nigdy nie może przekroczyć **100 MB**. Pliku pustego (0 bajtów) system nie przyjmuje.

O przyjęciu pliku decyduje **jego treść**, a nie nazwa ani zadeklarowany typ: plik nazwany rozwiazanie.pdf, który nie jest dokumentem PDF, zostaje odrzucony z komunikatem o niezgodności treści z deklaracją. Każdy przyjęty plik jest skanowany antywirusowo. Wersja uznana za zainfekowaną nie wchodzi do oceniania; do oceny wraca wtedy ostatnia wcześniejsza, nieodrzucona wersja tego samego zadania.

Uczestnik odpowiada za czytelność i kompletność pliku (§ 11 ust. 5 Regulaminu) i po wysłaniu powinien sprawdzić potwierdzenie w panelu. Do upływu terminu wolno **zastąpić rozwiązanie nową wersją** – system przechowuje kolejne wersje, a do oceny wchodzi wyłącznie najnowsza nieodrzucona.

## § 4. Termin oddania rozwiązań i tolerancja techniczna

O zachowaniu terminu decyduje **chwila zarejestrowania kompletnego pliku w systemie**, liczona zegarem serwera (§ 11 ust. 2 Regulaminu). Rozpoczęcie wysyłki przed terminem nie wystarcza – liczy się moment, w którym cały plik został przyjęty.

Okno oddawania rozwiązań jest przedziałem domkniętym od strony otwarcia i **otwartym od strony terminu**: chwila równa terminowi jest już po terminie. Do terminu oddania organizator może dodać jawną **tolerancję techniczną** (liczoną w sekundach, ogłaszaną razem z terminem). Praca przyjęta w tolerancji jest oznaczana w systemie jako oddana po terminie, co jest informacją dla Jury, a nie automatyczną sankcją.

Po zamknięciu etapu najnowsza nadająca się do oceny wersja każdego zadania zostaje zablokowana i od tej chwili nie da się jej podmienić. Rozwiązania przesłane po terminie nie podlegają ocenie, chyba że organizator potwierdzi awarię po swojej stronie i wyznaczy jednolite rozwiązanie dla wszystkich dotkniętych uczestników (§ 11 ust. 6 Regulaminu).

## § 5. Dozwolone narzędzia i źródła w Etapie I

W Etapie I wolno korzystać z podręczników, publikacji naukowych, własnych notatek, kalkulatora i lokalnych narzędzi obliczeniowych – pod warunkiem samodzielnego opracowania rozwiązania i **wskazania wykorzystanych źródeł** (§ 11 ust. 3 Regulaminu).

Zabronione jest korzystanie z usług osób trzecich, z cudzych nieopublikowanych rozwiązań oraz wspólne opracowywanie pracy. Użycie generatywnej sztucznej inteligencji do wytworzenia treści rozwiązania jest dopuszczalne wyłącznie wtedy, gdy treść zadania wyraźnie dopuszcza określone narzędzie i opisuje sposób jego użycia; w takim przypadku uczestnik ma **czytelnie oznaczyć w treści rozwiązania**, co i w jaki sposób zostało w ten sposób wytworzone (§ 11 ust. 4 Regulaminu).

Wykaz narzędzi, materiałów i oprogramowania dopuszczonych w finale jest inny niż w Etapie I i wymaga odrębnej decyzji organizatora – patrz § 12 tego dokumentu.

## § 6. Skala punktowa i ocena prac

Każde zadanie Etapu I jest oceniane w **skali czterostopniowej 0 / 2 / 5 / 6 punktów**, przy maksimum 6 punktów za zadanie:

| Punkty | Znaczenie |
|---|---|
| 0 | brak istotnego postępu |
| 2 | istotny postęp, rozwiązanie niepełne |
| 5 | rozwiązanie pełne z drobnymi usterkami |
| 6 | rozwiązanie pełne i poprawne |

Prace są oznaczane losowym kodem i oceniane bez ujawniania oceniającym imienia, nazwiska, szkoły i danych kontaktowych autora (§ 9 ust. 1 Regulaminu). Uczestnik występuje w systemie oceniania wyłącznie pod kodem w postaci OLM-….

Każda praca trafia do **dwóch niezależnych recenzentów**, którzy oceniają ją osobno i nie widzą ocen ani komentarzy drugiej osoby. Dalszy tryb zależy od wyniku:

1. **oceny zgodne** – ocena zgodna staje się oceną wstępną pracy (tryb „konsensus”), bez udziału osoby rozstrzygającej,
2. **oceny różne** – praca trafia do moderacji. Rozjazd rozstrzyga Przewodniczący Jury albo wyznaczony **trzeci recenzent (rozjemca)**. Rozjemca widzi obie oceny rundy pierwszej wraz z ich uzasadnieniami wewnętrznymi, ale **nie wie, kto je wystawił** – runda druga ma być niezależna, a nie arbitrażem między nazwiskami. Trzecim recenzentem nie może być autor żadnej z ocen rundy pierwszej.

Rozstrzygnięcie rozjazdu zawsze mieści się w skali punktowej etapu – system nie dopuszcza oceny spoza skali ani wartości pośredniej.

Konflikt interesów wyłącza recenzenta z oceniania pracy. Obowiązuje § 5 ust. 6 Regulaminu: członek Jury, którego bezstronność może budzić uzasadnione wątpliwości, ujawnia konflikt i nie bierze udziału w ocenie.

Błąd techniczny albo oczywista omyłka rachunkowa w ocenie podlega sprostowaniu również z urzędu (§ 9 ust. 4 Regulaminu).

## § 7. Informacja zwrotna dla uczestnika

Po opublikowaniu wyników etapu uczestnik widzi w panelu swoją punktację **dla każdego zadania** oraz – jeżeli recenzenci ją przygotowali – komentarz dla uczestnika i publiczne adnotacje do pracy. Do uczestnika nigdy nie trafiają: uzasadnienia wewnętrzne recenzentów, tożsamość recenzenta ani liczba osób, które pracę oceniały.

Punkty w panelu uczestnika są pokazywane **na bieżąco**. Jeżeli komisja zmieniła ocenę po ogłoszeniu tabeli, panel pokazuje obie liczby: wynik aktualny i wynik z ogłoszonej tabeli, wraz z informacją o różnicy.

## § 8. Reklamacje

Reklamację na ocenę wstępną składa się **przez system**, w oknie czasowym reklamacji ogłoszonym w [Harmonogramie](/harmonogram/) dla danego etapu. Zgodnie z § 16 ust. 1 Regulaminu okno to nie może być krótsze niż 3 dni robocze od udostępnienia wyniku wstępnego. Okno otwiera się po terminie oceny i zamyka w dniu podanym w Harmonogramie; po jego zamknięciu system nie przyjmuje już reklamacji.

Zasady formalne:

- reklamację składa się **osobno do każdego rozwiązania** i tylko raz – druga reklamacja na tę samą pracę nie powstaje,
- reklamacja dotyczy wyłącznie rozwiązania z **oceną wstępną**; praca jeszcze oceniana albo już zamknięta reklamacji nie podlega,
- uzasadnienie musi mieć co najmniej **50 znaków** i nie więcej niż **20 000 znaków**; tekst dłuższy jest odrzucany, a nie obcinany, bo obcięcie skasowałoby część odwołania,
- reklamacja wskazuje zadanie lub zdarzenie, kwestionowany element i konkretne uzasadnienie. Sama niezgoda z poziomem trudności, modelem rozwiązania albo opublikowanym kryterium nie wystarcza bez wskazania błędu w jego zastosowaniu (§ 16 ust. 2 Regulaminu).

Reklamację rozpoznaje Komisja Odwoławcza. Osoba, która wystawiła którąkolwiek z ocen tej pracy, **nie może** rozstrzygać reklamacji na nią i nie widzi jej nawet na liście spraw. Komisja może podwyższyć, utrzymać albo obniżyć punktację, jeżeli ponowna ocena ujawni błąd; każda decyzja wymaga uzasadnienia. Odpowiedź przekazuje się co do zasady w ciągu 7 dni roboczych, a decyzja Komisji Odwoławczej w sprawie oceny jest ostateczna w toku Olimpiady (§ 16 ust. 3–4 Regulaminu).

Rozwiązania, na które nie złożono reklamacji, stają się ostateczne automatycznie po zamknięciu okna reklamacji.

Od decyzji o dyskwalifikacji przysługuje odrębna droga – odwołanie do Zarządu Fundacji Quantum AI w terminie 5 dni roboczych (§ 16 ust. 5 Regulaminu).

## § 9. Wyniki etapu, remisy i kwalifikacja

Wynik etapu jest **sumą punktów za zadania**, liczoną z ocen uzgodnionych najnowszych nieodrzuconych wersji rozwiązań. Brak rozwiązania do zadania oznacza 0 punktów za to zadanie. Praca oddana i nieoceniona nie jest liczona jako zero – tabeli wyników nie da się zamknąć, dopóki każda praca etapu nie ma oceny uzgodnionej.

Listę rankingową tworzy się według łącznej liczby punktów, malejąco.

**Remisy.** Ten sam wynik oznacza **tę samą lokatę (ex aequo)**: przy dwóch uczestnikach z najlepszym wynikiem obaj zajmują miejsce 1, a następny – miejsce 3. Kolejność wierszy w obrębie remisu jest techniczna (po kodzie uczestnika) i nie jest informacją o tym, kto był lepszy. Remis na granicy progu rozstrzyga się **na korzyść uczestników**: jeżeli próg opisany jest liczbą miejsc, do następnego etapu przechodzą wszyscy z wynikiem równym wynikowi na ostatnim premiowanym miejscu, choćby było ich więcej niż zakładana liczba miejsc. Reguła ta wymaga potwierdzenia organizatora – patrz ostatnia sekcja.

**Progi kwalifikacji.** Próg do następnego etapu ma jedną z czterech postaci: minimalna liczba punktów, określona liczba najlepszych wyników, określona liczba najlepszych wyników w każdym województwie albo połączenie minimum punktowego z liczbą miejsc. Dla I edycji ustawiony jest próg **minimum punktowego: co najmniej 1 punkt**; Regulamin progów nie podaje i odsyła do tego dokumentu. Pozostałe progi wymagają decyzji Jury.

Wynik 0 punktów nigdy nie kwalifikuje: w progu opisanym liczbą miejsc kandydatem jest wyłącznie ten, kto zdobył choć jeden punkt. Dyskwalifikacja jest decyzją proceduralną, a nie wynikiem punktowym – osoba zdyskwalifikowana nie zajmuje miejsca w progu.

Progi i kwalifikację liczy się **dopiero po zamknięciu okna reklamacji**, nigdy wcześniej: reklamacja może zmienić ocenę, a więc i sumę punktów, a status „zakwalifikowany” raz ogłoszony jest zobowiązaniem wobec uczestnika.

## § 10. Publikacja wyników i ochrona danych uczestników

Publikacja **zamraża** tabelę wyników: to, co jest widoczne, jest kopią z chwili ogłoszenia, a nie widokiem bieżących danych. Ponowna publikacja nadpisuje tę samą tabelę i zostawia ślad w rejestrze zdarzeń.

Tabela publiczna zawiera wyłącznie: lokatę, podpis uczestnika, punkty za poszczególne zadania, sumę i informację o kwalifikacji. Nie zawiera adresu e-mail, roku urodzenia ani identyfikatorów kont. Podpis uczestnika ma jedną z trzech postaci:

- **kod uczestnika** (OLM-…) – postać domyślna; tylko w tym trybie tabela pokazuje dodatkowo województwo,
- **inicjały i szkoła** – dopuszczalne wyłącznie wtedy, gdy z tej szkoły startuje w etapie co najmniej **trzy** osoby. Przy mniejszej grupie para „inicjały + szkoła” wskazywałaby konkretną osobę, więc wiersz spada do kodu,
- **imię i nazwisko** – wyłącznie w wynikach **finału**, wyłącznie przy wyniku będącym wyróżnieniem i wyłącznie za zgodą uczestnika, a dla osoby niepełnoletniej także za zgodą rodzica albo opiekuna prawnego. Brak którejkolwiek z tych przesłanek oznacza wiersz pod kodem.

Zgodę na publikację imienia i nazwiska uczestnik może wyrazić i wycofać samodzielnie w panelu, w sekcji „Twoje zgody”. Jej brak nie wpływa na wynik ani na udział. Szczegóły przetwarzania danych opisuje [Polityka RODO](/dokumenty/rodo/).

## § 11. Etap II – rozmowa kwalifikacyjna online

Etap II jest rozmową kwalifikacyjną prowadzoną **online** i nie przyjmuje plików: próba przesłania rozwiązania w tym etapie jest odrzucana niezależnie od zegara.

Przebieg:

1. do Etapu II przystępują wyłącznie osoby zakwalifikowane po Etapie I,
2. terminy rozmów wyznacza koordynator; każdy termin ma godziny, liczbę miejsc i – gdy komisji jest kilka – oznaczenie komisji. Kilka komisji może rozmawiać równolegle, więc dwa terminy o tych samych godzinach są normalną sytuacją,
3. uczestnik **sam zapisuje się w panelu** na jeden wybrany termin. W etapie przypada na uczestnika dokładnie jedna rozmowa; zmiana terminu jest przeniesieniem zapisu, a nie drugim zapisem,
4. po zapisaniu system wysyła potwierdzenie na adres e-mail konta: etap, data i godziny rozmowy w czasie polskim, oznaczenie komisji i link do spotkania,
5. **link do rozmowy widzi wyłącznie osoba zapisana na dany termin oraz członkowie Jury**. Ten sam link jest w panelu, więc zagubiony list nie odcina od rozmowy,
6. termin można zmienić albo odwołać **do chwili jego rozpoczęcia**. Po rozpoczęciu rozmowy zapis jest zamknięty; po zamknięciu etapu nie działają ani zapisy, ani rezygnacje.

Wszystkie terminy rozmów mieszczą się w oknie etapu ogłoszonym w [Harmonogramie](/harmonogram/) – termin poza tym oknem byłby terminem, którego nie ma w terminarzu.

Zgodnie z § 12 Regulaminu: informację o rozmowie, jej formie, terminie i sposobie identyfikacji przekazuje się co najmniej **7 dni kalendarzowych** wcześniej; rozmowa trwa co do zasady **20–30 minut**; prowadzi ją komisja złożona z co najmniej **trzech** członków Jury; komisja sporządza protokół i przyznaje **od 0 do 20 punktów** według opublikowanych kryteriów (poprawność merytoryczna, tok rozumowania, samodzielność, umiejętność obrony wniosków). Pytania mogą być różne, ale muszą mieć porównywalny zakres i poziom trudności. Zakres rozmowy obejmuje **cały program merytoryczny** edycji, a nie tylko zagadnienia z zadań Etapu I.

Nieusprawiedliwiona nieobecność oznacza rezygnację z dalszego udziału; w udokumentowanym przypadku losowym Jury może wyznaczyć termin dodatkowy. Uczestnik zachowuje prawo do racjonalnego dostosowania warunków rozmowy (§ 3 ust. 6 Regulaminu) – wniosek należy złożyć odpowiednio wcześnie na adres contact@qaif.org.

Sposób wyliczenia wyniku kwalifikacyjnego po Etapie II, o którym mowa w § 12 ust. 5 Regulaminu (80% znormalizowanego wyniku Etapu I i 20% wyniku rozmowy), wymaga decyzji organizatora – patrz ostatnia sekcja.

## § 12. Etap III – finał stacjonarny

Finał odbędzie się **stacjonarnie w Krakowie**, w warunkach kontrolowanej samodzielności, w terminie podanym w [Harmonogramie](/harmonogram/) (4–7 czerwca 2027). Do finału dopuszcza się wyłącznie osoby zakwalifikowane po Etapie II. Przed rozpoczęciem zawodów każdy uczestnik okazuje dokument ze zdjęciem oraz potwierdzenie statusu ucznia (§ 13 ust. 2 Regulaminu).

Finał może obejmować zadania teoretyczne, obliczeniowe, problemowe, programistyczne, analizę danych albo część doświadczalną. **Liczbę sesji, czas ich trwania, wyposażenie stanowisk i zasady oddawania prac ustala organizator i publikuje przed finałem** – te wartości nie są jeszcze rozstrzygnięte i są wypisane w ostatniej sekcji.

Podczas finału wolno korzystać wyłącznie z materiałów, urządzeń i oprogramowania dopuszczonych przez organizatora. Telefony, zegarki komunikujące się z siecią, prywatne nośniki danych i komunikacja z innymi osobami są zabronione (§ 13 ust. 4 Regulaminu).

Nadzór organizacyjny sprawuje komisja finałowa. Spóźnienia, awarie, przerwy i naruszenia są dokumentowane; w razie awarii niezawinionej przez uczestnika Komitet Merytoryczny może przedłużyć jego czas o okres faktycznej przerwy albo zastosować inne proporcjonalne rozwiązanie, odnotowane w protokole.

Osoba, która przystąpi do finału i odda co najmniej jedną pracę podlegającą ocenie, uzyskuje wewnętrzny tytuł **finalisty**, o ile nie zostanie zdyskwalifikowana.

**Wynik Olimpiady ustala się wyłącznie na podstawie punktów z finału** – punkty Etapu I i Etapu II nie są doliczane do klasyfikacji finałowej (§ 14 ust. 1 Regulaminu). Laureatem może zostać finalista sklasyfikowany w pierwszej połowie uczestników finału, którego wynik Jury uzna za wyróżniający; ewentualny dodatkowy minimalny próg punktowy dla laureatów wymaga decyzji organizatora.

Tytuły finalisty i laureata mają charakter **wewnętrzny**: potwierdzają wynik w konkursie Fundacji Quantum AI i nie są równoznaczne z ustawowym tytułem laureata lub finalisty olimpiady przedmiotowej (§ 14 ust. 4 Regulaminu).

**Koszty udziału w finale.** Zgodnie z § 22 ust. 2 Regulaminu informację o pokrywaniu kosztów przejazdu, zakwaterowania i wyżywienia finalistów podaje ZOZ albo zaproszenie na finał, a **brak takiej informacji oznacza, że organizator nie zobowiązał się do ich pokrycia**. Decyzja w tej sprawie nie została jeszcze podjęta i jest wypisana w ostatniej sekcji; do jej ogłoszenia finaliści powinni przyjmować, że koszty dojazdu i pobytu nie są pokrywane.

## § 13. Program merytoryczny i literatura

Program merytoryczny I edycji jest wyznaczony przez **zakres warsztatów przygotowawczych**, prowadzonych online i bezpłatnie od października 2026 do lutego 2027. Zakres tematyczny podaje strona [Warsztaty](/warsztaty/); terminy poszczególnych spotkań są także w [Harmonogramie](/harmonogram/).

Zakres rozmowy w Etapie II oraz zadań finału obejmuje cały program merytoryczny, a nie tylko zagadnienia poruszone w zadaniach Etapu I.

**Wykazu literatury** dla I edycji organizator jeszcze nie ogłosił – patrz ostatnia sekcja. Do jego publikacji zakres wymaganej wiedzy wyznaczają tematy warsztatów podane na stronie [Warsztaty](/warsztaty/).

## § 14. Poufność zadań i rozwiązań

Treść zadania pozostaje poufna do chwili wskazanej przez organizatora (§ 8 ust. 4 Regulaminu). W praktyce oznacza to, że plik z treścią zadania jest nieosiągalny przed otwarciem etapu – także dla osoby, która zna jego adres.

Publikowanie własnego rozwiązania **przed zakończeniem etapu** jest zabronione, jeżeli mogłoby naruszyć równość zawodów. Po oficjalnym zakończeniu etapu uczestnik może rozpowszechniać własne rozwiązanie, chyba że ZOZ określą krótki okres poufności konieczny do przeprowadzenia terminu dodatkowego (§ 20 ust. 3 Regulaminu). Długość takiego okresu dla I edycji nie została ustalona – patrz ostatnia sekcja.

Osoby mające dostęp do treści zadań, kryteriów, danych uczestników i nieopublikowanych wyników są zobowiązane do zachowania poufności (§ 7 ust. 4 Regulaminu). Prace są udostępniane oceniającym pod anonimowym kodem.

## § 15. Samodzielność, sankcje i zgłaszanie nieprawidłowości

Katalog naruszeń, tryb wyjaśnień i katalog sankcji (od ostrzeżenia przez anulowanie punktów za zadanie i wynik etapu po dyskwalifikację z edycji i wykluczenie z kolejnej) określa § 17 Regulaminu. Sankcja ma być proporcjonalna do wagi, skutków i umyślności naruszenia, a przed jej nałożeniem uczestnik ma możliwość przedstawienia wyjaśnień.

W razie uzasadnionego podejrzenia Jury może poprosić uczestnika o wyjaśnienie toku rozwiązania albo przeprowadzić rozmowę weryfikującą samodzielność. Taka rozmowa nie służy poprawianiu wyniku.

Wszystkie osoby działające w imieniu organizatora przestrzegają [Standardów ochrony małoletnich](/dokumenty/standardy-ochrony-maloletnich/). Zagrożenie lub podejrzenie krzywdzenia można zgłosić na adres contact@qaif.org albo pod numerem wskazanym w Standardach; w bezpośrednim zagrożeniu życia lub zdrowia należy dzwonić pod numer **112**.

## § 16. Do rozstrzygnięcia przez organizatora

Poniższe zapisy są wymagane przez Regulamin, ale nie zostały jeszcze przekazane przez organizatora. Do chwili ich ogłoszenia **nie obowiązują** – dokument wymienia je, żeby brak był widoczny, a nie ukryty w ogólnym sformułowaniu.

1. **Maksymalna liczba finalistów** – liczba miejsc w finale (§ 1 ust. 4 i § 12 ust. 5 Regulaminu). Bez niej próg po Etapie II nie może mieć postaci „N najlepszych”.
2. **Progi punktowe poza minimum „co najmniej 1 punkt”** – wartość progu kwalifikacji po Etapie I i po Etapie II oraz wybór postaci progu (minimum punktowe, liczba miejsc, liczba miejsc w województwie albo połączenie minimum z liczbą miejsc). Obecna wartość (≥ 1 pkt) jest wartością początkową, przyjętą wyłącznie po to, żeby zero punktów nie oznaczało kwalifikacji.
3. **Sposób wyliczenia i ogłoszenia wyniku kwalifikacyjnego po Etapie II** – § 12 ust. 5 Regulaminu przewiduje sumę 80% znormalizowanego wyniku Etapu I i 20% wyniku rozmowy. Wymaga rozstrzygnięcia, jak wynik rozmowy (0–20 pkt z protokołu komisji) jest zapisywany i jak liczony jest wynik ważony, bo dziś kwalifikację liczy się z punktów jednego etapu.
4. **Dodatkowy minimalny próg punktowy dla laureatów** – § 14 ust. 2 Regulaminu dopuszcza go, ale nie ustala. Bez decyzji laureatem może zostać każdy finalista z pierwszej połowy klasyfikacji, którego wynik Jury uzna za wyróżniający.
5. **Reguła rozstrzygania remisów** – § 9 ust. 5 i § 14 ust. 3 Regulaminu wymagają jej **opublikowania przed rozpoczęciem etapu**. Projekt proponuje regułę „ex aequo – ta sama lokata”, z remisem na granicy progu rozstrzyganym na korzyść uczestników (§ 9 tego dokumentu); wymaga potwierdzenia, w szczególności co do tytułu zwycięzcy, który przy remisie może przypaść więcej niż jednej osobie.
6. **Wykaz literatury i formalny program merytoryczny** – § 1 ust. 4 i § 5 ust. 3 Regulaminu. Dziś zakres wyznaczają wyłącznie tematy warsztatów.
7. **Wykaz narzędzi, materiałów i oprogramowania dopuszczonych w finale** – § 13 ust. 4 Regulaminu wymaga listy dopuszczeń; bez niej obowiązuje tylko katalog zakazów (telefony, zegarki z łącznością, prywatne nośniki, komunikacja z innymi).
8. **Liczba sesji finału, czas ich trwania, wyposażenie stanowisk i zasady oddawania prac** – § 13 ust. 3 Regulaminu wprost odsyła te parametry do ZOZ.
9. **Koszty przejazdu, zakwaterowania i wyżywienia finalistów** – § 22 ust. 2 Regulaminu: brak informacji oznacza brak zobowiązania organizatora. Decyzja powinna być ogłoszona przed finałem, najpóźniej w zaproszeniu.
10. **Okres poufności rozwiązań po zakończeniu etapu** – § 20 ust. 3 Regulaminu dopuszcza „krótki okres” konieczny do przeprowadzenia terminu dodatkowego. Wymaga podania liczby dni albo jawnej rezygnacji z takiego okresu.
11. **Formaty rozwiązań i limity rozmiaru dla poszczególnych zadań** – lista dopuszczonych rozszerzeń (.pdf, .ipynb, .py) i limit w megabajtach są parametrem każdego zadania. Wartości domyślne (.pdf, 20 MB) wymagają potwierdzenia dla zadań, w których przewidziano część programistyczną.
12. **Tryb uzgadniania ocen rozbieżnych** – § 9 ust. 3 Regulaminu mówi o średniej ocen dwóch jurorów i o dodatkowym jurorze, gdy różnica przekracza 20% maksymalnej liczby punktów za zadanie. System nie liczy średniej: przy ocenach zgodnych utrzymuje ocenę zgodną, a przy różnych kieruje pracę do rozjemcy albo do koordynatora, tak aby wynik zawsze mieścił się w skali 0/2/5/6. Rozbieżność wymaga rozstrzygnięcia – zmianą Regulaminu albo zmianą tego zapisu ZOZ.
13. **Numeracja etapów w Regulaminie** – § 10 ust. 1 opisuje strukturę dwuetapową (Etap I zdalny, Etap II jako stacjonarny finał) i traktuje rozmowę kwalifikacyjną jako procedurę pomocniczą, natomiast § 11–13 opisują trzy etapy, a portal i Harmonogram posługują się numeracją „Etap I / II / III”. Wymaga ujednolicenia.
14. **Nagrywanie rozmów Etapu II** – § 19 ust. 4 Regulaminu dopuszcza nagrywanie dla zapewnienia rozliczalności pod warunkiem uprzedniego poinformowania uczestnika. Wymaga decyzji, czy rozmowy I edycji będą nagrywane.

## § 17. Wersja dokumentu

Wersja: 0.2 (projekt), 17 września 2026 r. Status: projekt do akceptacji organizatora. Dokument nie wchodzi w życie przed zatwierdzeniem przez Fundację Quantum AI; do tego czasu obowiązują wyłącznie [Regulamin](/dokumenty/regulamin/) i ogłoszony [Harmonogram](/harmonogram/).

Zmiany ZOZ ogłasza się na stronie Olimpiady. Po rozpoczęciu rejestracji dopuszczalne są jedynie zmiany konieczne z powodu prawa, bezpieczeństwa albo zdarzeń nadzwyczajnych, które nie pogarszają w sposób nieproporcjonalny sytuacji uczestników (§ 24 ust. 2 Regulaminu).

## Dokumenty powiązane

Dokumenty bieżącej edycji należy czytać łącznie:

- [Regulamin Olimpiady Kwantowej](/dokumenty/regulamin/)
- [Polityka RODO Olimpiady Kwantowej](/dokumenty/rodo/)
- [Zgoda rodzica lub opiekuna prawnego](/dokumenty/zgoda-opiekuna/)
- [Standardy ochrony małoletnich](/dokumenty/standardy-ochrony-maloletnich/)
- [Harmonogram](/harmonogram/)
- [Warsztaty przygotowawcze](/warsztaty/)
