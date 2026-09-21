# Inwentarz starej strony Olimpiady Kwantowej (WordPress)

Źródło (tylko do odczytu): `C:\Users\SPCX\Documents\strona oli,mpady kwantowj\`
Zakres: motyw `wp-content/themes/olimpiada-kwantowa/`, wtyczka `wp-content/plugins/quantum-olympiad-core/`,
`README.md`, `scripts/`, `output/`.
Data analizy: 2026-09-06.

## 0. Czym w rzeczywistości jest „stara strona”

To **nie jest zrzut działającego serwisu**, tylko repozytorium z instalatorem: motyw + wtyczka,
która przy komendzie `wp quantum seed` tworzy 25 podstron, 3 wpisy i 1 zadanie demonstracyjne.
Nie ma dumpa bazy, mediów ani śladu treści redagowanych przez człowieka po instalacji.

Konsekwencja dla importu: **wszystko, co widać, jest treścią zaprogramowaną przez autora
instalatora.** README stwierdza wprost, że część treści jest demonstracyjna i wymaga zatwierdzenia
prawnego. Merytorycznie wartościowe są tylko cztery bloki: regulamin (`regulamin.json`),
RODO, standardy ochrony małoletnich i dane organizatora. Reszta to jedno- lub dwuzdaniowe
zaślepki, których przenoszenie 1:1 nie ma sensu.

Trzy z tych bloków przestały być zresztą źródłem: regulamin, RODO i standardy ochrony małoletnich
mają dziś podpisane pliki organizatora i strony portalu są przepisane **z nich**, a nie z instalatora
— patrz sekcja 5.1.

Objętość treści źródłowej: ok. **5,4 tys. słów łącznie**, z czego regulamin ok. 2 970 słów
(importowany osobno), RODO + standardy ochrony małoletnich + „O Olimpiadzie” + „Kontakt”
ok. 1 975 słów, a **cała reszta serwisu — 21 podstron — ok. 450 słów**.

## 1. Lista podstron z priorytetem

Kolumna „menu” = pozycja w `Menu główne` tworzonym przez seeder
(`o-olimpiadzie, jak-zaczac, biezaca-edycja, zadania, komitety, kontakt` — 6 pozycji).
Menu awaryjne w `functions.php` ma dodatkowo `Dokumenty` na 5. pozycji.

### Priorytet A — przenieść, treść ma wartość

| # | Tytuł | Slug | Menu | Cel strony | Plik |
|---|---|---|---|---|---|
| 1 | Strona główna | — | — | hero, aktualności, harmonogram, 3 kroki, CTA, partnerzy | `tresci/strona-glowna.md` |
| 2 | O Olimpiadzie | `o-olimpiadzie` | 1 | po co, dla kogo, kto organizuje | `tresci/o-olimpiadzie.md` |
| 3 | Kontakt | `kontakt` | 6 | dane organizatora + zasady tytułowania maili | `tresci/kontakt.md` |
| 4 | Komitety | `komitety` | 5 | skład Komitetu Merytorycznego i Organizacyjnego | `tresci/komitety.md` |
| 5 | Regulamin | `regulamin` | — | dokument główny (24 §, 10 rozdziałów) | `tresci/regulamin.md` (metryka; treść **importowana osobno**) |
| 6 | RODO | `rodo` | — | klauzula informacyjna, 11 sekcji | `tresci/rodo.md` |
| 7 | Standardy ochrony małoletnich | `standardy-ochrony-maloletnich` | — | 10 sekcji + wersja skrócona dla uczniów | `tresci/standardy-ochrony-maloletnich.md` |
| 8 | Jak zacząć? | `jak-zaczac` | 2 | 5 kroków uczestnika | `tresci/jak-zaczac.md` |
| 9 | Terminarz i harmonogram | `harmonogram` | — | tabela 4 terminów I edycji | `tresci/harmonogram.md` |
| 9a | Warsztaty | `warsztaty` | — | harmonogram warsztatów wydzielony z `harmonogram` (osobna pozycja menu) | `tresci/harmonogram.md` |

### Priorytet B — scalić lub odtworzyć, treść jest zaślepką

| # | Tytuł | Slug | Rekomendacja |
|---|---|---|---|
| 10 | Bieżąca edycja | `biezaca-edycja` | scalić ze stroną główną + `ProblemsPage`; jako osobny węzeł nadrzędny nie jest potrzebna — nowy CMS trzyma etapy w `competitions.Edition/Stage` |
| 11 | Dokumenty | `dokumenty` | zastąpić listą dokumentów; `DocumentPage` w nowym CMS jest dzieckiem `HomePage`, więc poziom pośredni znika |
| 12 | Zadania i archiwum | `zadania` | odwzorować `ProblemsPage` (już istnieje); przenieść tylko teksty pomocnicze |
| 13 | Partnerzy i sponsorzy | `partnerzy` | odtworzyć od zera — jedyna informacja to poziomy „diamentowy, platynowy, złoty” |
| 14 | Dla nauczycieli i materiały | `dla-nauczycieli` | przenieść dwa zdania jako zapowiedź albo odłożyć do czasu, aż będą materiały |
| 15 | Poprzednie edycje | `poprzednie-edycje` | `ArchiveIndexPage` (już istnieje), pusta do czasu zakończenia I edycji |
| 16 | Aktualności edycji | `aktualnosci-edycji` | **scalić** z `aktualnosci` — dwa newsroomy bez powodu |
| 17 | Aktualności | `aktualnosci` | `NewsIndexPage` (już istnieje) |
| 18 | Przepisy | `przepisy` | **scalić** z regulaminem (sekcja „Źródła”) — treść to jedno zdanie |
| 19 | Olimpiady międzynarodowe | `olimpiady-miedzynarodowe` | odłożyć — jeden akapit bez konkretów, temat nieaktualny przed I edycją |

### Priorytet C — pominąć

| # | Tytuł | Slug | Uzasadnienie pominięcia |
|---|---|---|---|
| 20 | Wyniki I etapu | `wyniki-i-etapu` | jedno zdanie „Wyniki zostaną opublikowane…”; nowy portal ma `ResultsPage` + `ResultsPublication` generowane z danych |
| 21 | Wyniki II etapu | `wyniki-ii-etapu` | j.w. |
| 22 | Finaliści i laureaci | `finalisci-laureaci` | j.w. |
| 23 | Galeria | `galeria` | jedno zdanie, zero zdjęć w repozytorium |
| 24 | Rejestracja | `rejestracja` | funkcja aplikacji (`/register/` z kodami zaproszeń), nie strona treściowa; teksty zgody i pola formularza zachowane w `tresci/rejestracja.md` do porównania |
| 25 | Panel uczestnika | `panel-uczestnika` | funkcja aplikacji (`/me/`) |
| 26 | Panel komitetu | `panel-komitetu` | funkcja aplikacji (`/review/`) |

Razem: **26 węzłów** (25 podstron utworzonych przez seeder + strona główna zaszyta w szablonie).

## 2. Mapowanie: stara podstrona → nowa strona CMS

Typy istniejące w `backend/apps/cms/models.py`: `HomePage`, `NewsIndexPage`, `NewsPage`,
`ProblemsPage`, `DocumentPage`, `ArchiveIndexPage`, `ArchiveEditionPage`, `ResultsPage`.
`ContentPage` **jeszcze nie istnieje** — patrz punkt 2a.

| Stara podstrona | Slug | Nowy typ | Rodzic | Uwagi |
|---|---|---|---|---|
| Strona główna (`front-page.php`) | `/` | `HomePage` | Root | hero_title + hero_text; oś czasu z `show_timeline` zastępuje ręczną sekcję harmonogramu |
| O Olimpiadzie | `o-olimpiadzie` | — (sekcja strony głównej) | — | podstrony **nie ma**: treść jest w `HomePage.about_body`, adres przekierowuje na `/#o-olimpiadzie` (README 6.7) |
| Jak zacząć? | `jak-zaczac` | — (sekcja strony głównej) | — | podstrony **nie ma**: dublowała „Jak zacząć w 3 krokach”, adres przekierowuje na `/` |
| Kontakt | `kontakt` | **`ContentPage`** | HomePage | 1:1 |
| Komitety | `komitety` | **`ContentPage`** | HomePage | skład potwierdzony PDF-em organizatora (4.2); strona opublikowana, PDF do pobrania |
| Partnerzy i sponsorzy | `partnerzy` | **`ContentPage`** | HomePage | treść do napisania od nowa |
| Dla nauczycieli | `dla-nauczycieli` | **`ContentPage`** | HomePage | |
| Olimpiady międzynarodowe | `olimpiady-miedzynarodowe` | **`ContentPage`** | HomePage | odłożone |
| Aktualności | `aktualnosci` | `NewsIndexPage` | HomePage | istnieje w `cms.0002_initial_tree` |
| Aktualności edycji | `aktualnosci-edycji` | — | — | scalić z powyższym |
| 3 wpisy blogowe | — | `NewsPage` | NewsIndexPage | treść do napisania od nowa (brak dat) |
| Zadania i archiwum | `zadania` | `ProblemsPage` | HomePage | istnieje |
| Poprzednie edycje | `poprzednie-edycje` | `ArchiveIndexPage` | HomePage | istnieje |
| Wyniki I / II etapu, Finaliści | `wyniki-*`, `finalisci-laureaci` | `ResultsPage` | HomePage | jedna strona zamiast trzech; dane z `ResultsPublication` |
| Regulamin | `regulamin` | `DocumentPage` | HomePage | **import osobny** (`seed_regulamin.py`) |
| RODO | `rodo` | `DocumentPage` | HomePage | treść **przepisana z PDF-u organizatora** (5.1), nie ze starej strony; `status_label`: „Dokument organizatora (Fundacja Quantum AI)” |
| Standardy ochrony małoletnich | `standardy-ochrony-maloletnich` | `DocumentPage` | HomePage | j.w. |
| Dokumenty (węzeł) | `dokumenty` | — | — | zastąpić listą / nawigacją; `DocumentPage` nie może być dzieckiem `ContentPage` |
| Przepisy | `przepisy` | — | — | scalić z regulaminem |
| Terminarz i harmonogram | `harmonogram` | — | — | dane w `competitions.Stage`, prezentacja przez `HomePage.show_timeline` |
| Bieżąca edycja | `biezaca-edycja` | — | — | rozbić na `HomePage` + `ProblemsPage` |
| Rejestracja / Panel uczestnika / Panel komitetu | — | — | — | widoki `apps.web` |
| Galeria | `galeria` | — | — | pominąć |

### 2a. Propozycja: nowy typ `ContentPage`

Osiem podstron nie pasuje do żadnego istniejącego typu — to zwykłe strony redakcyjne
(tekst + nagłówki + listy + okazjonalna tabela lub wyróżniony blok). Proponowany minimalny model,
spójny z konwencjami `cms/models.py`:

- dziedziczy po `CMSPage`;
- `intro = RichTextField(blank=True)` — jak w `NewsIndexPage`/`DocumentPage`;
- `body = StreamField(ArticleStreamBlock())` — ten sam zestaw bloków co `NewsPage`;
- `parent_page_types = ["cms.HomePage", "cms.ContentPage"]` (dopuszczalne zagnieżdżenie
  jednego poziomu, żeby odtworzyć strukturę „Bieżąca edycja → …”, gdyby okazała się potrzebna);
- `subpage_types = ["cms.ContentPage"]`;
- `verbose_name = "strona treści"`, `template = "cms/content_page.html"`;
- dopisać `"cms.ContentPage"` do `HomePage.subpage_types`.

Blok, którego brakuje: stara strona używa trzech wyróżnionych ramek — `legal-box` (dane
rejestrowe), `legal-lead` (lead), `youth-box` (wersja dla uczniów w standardach ochrony
małoletnich). Warto sprawdzić, czy `ArticleStreamBlock`/`DocumentStreamBlock` mają odpowiednik
„ramki informacyjnej”; jeśli nie — jeden blok `callout` z wariantem (`info` / `warning` /
`for-students`) załatwia wszystkie trzy przypadki.

**To jest propozycja dla agenta pracującego w `backend/apps/cms/` — nie zaimplementowana tutaj.**

## 3. Harmonogram i mapowanie na model etapów

### 3.1 Dane ze starej strony

Harmonogram występuje w dwóch miejscach i **są one zgodne co do dat, ale nie co do opisu**:

| Wydarzenie | Data (`/harmonogram/`) | Opis (strona główna) |
|---|---|---|
| Rejestracja | 1 września – 15 października 2026 | „do 15 października 2026” |
| I etap | 7 listopada 2026 | zawody **szkolne** |
| II etap | 16 stycznia 2027 | zawody **okręgowe** |
| Finał | 10 kwietnia 2027 | **Warszawa** |

Rok szkolny edycji: **I edycja, 2026/2027** (hero strony głównej, strona „Bieżąca edycja”).

> **Aktualizacja (wrzesień 2026): finał zmienił termin i miejsce.** Organizator przekazał
> **III etap (finał): 4–7 czerwca 2027, stacjonarnie w Krakowie** — zamiast 10 kwietnia 2027
> w Warszawie. Terminy rejestracji oraz I i II etapu zostają bez zmian. Tabela wyżej jest
> inwentarzem **starej strony** i dlatego zostaje w brzmieniu, jakie tam było; obowiązujące
> terminy stoją w `backend/apps/cms/fixtures/legacy/harmonogram.md` (treść strony) oraz
> w `seed_edition_kwantowa` (`competitions.Stage`, w tym pole `location`). Do potwierdzenia
> zostają **godziny** finału: organizator podał same daty dzienne, a komenda przyjmuje
> 4 czerwca 9:00 – 7 czerwca 18:00.
>
> Organizator przekazał też **harmonogram szesnastu warsztatów online** (10 października 2026 –
> 13 lutego 2027), których stara strona nie miała w ogóle. Stoją na `/harmonogram/` jako tabela
> (blok `schedule`). Jedna pozycja wymaga potwierdzenia: „Podstawy metrologii kwantowej” przyszła
> jako `09/01/2027` — w ciągu sobotnich terminów pasuje **9 stycznia 2027** i tak jest zapisana,
> ale zapis jest niejednoznaczny (1 września 2027 wypadałoby poza cyklem), a godzin nie podano.

### 3.2 Sprzeczność z regulaminem — rozstrzygnięta wersją z 2 września 2026

> **Aktualizacja.** Organizator przekazał regulamin **1.0 z 2 września 2026 r.** (ten sam numer
> wersji, inny dokument) i to on stoi pod `/dokumenty/regulamin/`. Rozstrzyga on spór opisany
> niżej **na rzecz trzech etapów**: podtytuł brzmi „Ogólnopolski, **trzyetapowy** konkurs
> edukacyjny”, a paragrafy etapowe to § 11 „Etap I – zawody zdalne”, § 12 „Etap II – rozmowa”
> i § 13 „Etap III – finał stacjonarny”. Rozmowa kwalifikacyjna przestała być procedurą
> pomocniczą i jest osobnym etapem, po którym kwalifikuje się do finału (80 % wyniku Etapu I
> + 20 % wyniku Etapu II). Zgadza się to z `StageKind = ELIM | DISTRICT | FINAL` i z
> `seed_edition_kwantowa`, więc konfiguracji etapów nie ruszamy.
>
> **Czego nowa wersja nie naprawiła:** § 1 ust. 1 nadal nazywa Olimpiadę konkursem
> „dwuetapowym”, § 10 ust. 1 mówi o „dwóch etapach”, a ramka „Status dokumentu” — o „modelu
> dwóch etapów”. Import nie redaguje treści organizatora, więc te zdania są na stronie takie,
> jakie są w dokumencie; do poprawienia przy najbliższej wersji regulaminu.
>
> Poniższy opis dotyczy **wycofanej** wersji z 18 sierpnia 2026 i zostaje jako zapis stanu
> zastanego przy inwentaryzacji.

Regulamin (`regulamin.json`, wersja 1.0 z 18 sierpnia 2026) opisywał **zupełnie inny model**:

- § 10 ust. 1: „Olimpiada składa się z **dwóch etapów**: Etapu I przeprowadzanego zdalnie
  oraz Etapu II będącego stacjonarnym finałem.”
- § 12: **rozmowa kwalifikacyjna** po Etapie I — wyraźnie „nie stanowi odrębnego etapu”,
  20–30 minut, komisja min. 3 członków Jury, 0–20 pkt, wynik kwalifikacyjny = 80% znormalizowanego
  wyniku Etapu I + 20% rozmowy.
- Podtytuł dokumentu: „Ogólnopolski, **dwuetapowy** konkurs edukacyjny”.
- Zastrzeżenie prawne: w modelu dwóch etapów to **niezależny konkurs**, nie olimpiada
  trójstopniowa wg rozporządzenia MEN, i **nie nadaje ustawowych uprawnień laureata/finalisty**.

Czyli: motyw (3 etapy: szkolny → okręgowy → finał) i regulamin (2 etapy + rozmowa) opisują
dwie różne olimpiady. Nowy portal ma `StageKind = ELIM | DISTRICT | FINAL`, czyli **model
trójstopniowy**, zgodny z motywem, a nie z regulaminem.

### 3.3 Mapowanie na `competitions.Stage`

**Obowiązuje wariant 1.** Regulamin 1.0 z 2 września 2026 r. jest trzyetapowy (3.2), więc wariant 2
zostaje wyłącznie jako zapis rozważanej alternatywy.

**Wariant 1 — trójstopniowy (zgodny z motywem i z modelem nowego portalu):**

| Stara etykieta | `StageKind` | `opens_at` / `deadline_at` |
|---|---|---|
| Rejestracja 1 IX – 15 X 2026 | *(nie etap)* | `Edition` + okno rejestracji / kody zaproszeń |
| I etap — zawody szkolne, 7 XI 2026 | `ELIM` | do ustalenia; stara strona podaje jedną datę, model wymaga `opens_at` < `deadline_at` |
| II etap — zawody okręgowe, 16 I 2027 | `DISTRICT` | j.w. |
| Finał — 10 IV 2027, Warszawa | `FINAL` | j.w. |

**Wariant 2 — dwustopniowy (zgodny z regulaminem):**

| Etap regulaminowy | `StageKind` |
|---|---|
| Etap I — zawody zdalne | `ELIM` |
| Rozmowa kwalifikacyjna (procedura kwalifikacji, nie etap) | `DISTRICT` *albo* poza modelem etapów |
| Etap II — finał stacjonarny | `FINAL` |

Wariant 2 wymusza użycie `DISTRICT` na coś, co regulamin celowo nazywa „nie-etapem”, albo
pozostawienie `DISTRICT` nieużywanego. **Rekomendacja: wariant 1** (model portalu i strony
publicznej są spójne), z konsekwencją: § 10–13 regulaminu trzeba przepisać, zanim regulamin
zostanie zatwierdzony uchwałą Zarządu.

Uwaga techniczna: stara strona podaje **jedną datę na etap**, a `Stage` wymaga pełnej osi czasu
(`opens_at`, `deadline_at`, `grace_seconds`, `review_deadline_at`, `appeal_window_opens_at`,
zamknięcie okna reklamacji), z ograniczeniami spójności w bazie. Brakujących terminów nie da się
wyprowadzić ze starej strony — musi je podać organizator.

## 4. Organizator, komitety, kontakt

### 4.1 Organizator (dane spójne we wszystkich czterech miejscach: about, contact, footer, `regulamin.json`)

| Pole | Wartość |
|---|---|
| Nazwa | Fundacja Quantum AI |
| Adres | ul. Sanocka 9/103, 02-110 Warszawa |
| KRS | 0000808359 |
| NIP | 7010955891 |
| REGON | 384899425 |
| E-mail | contact@qaif.org |
| Telefon | +48 507 982 292 |
| WWW | https://www.qaif.org/ |

Etykieta w pasku górnym: „Organizator: Fundacja Quantum AI”.
Stopka: „© {rok} Fundacja Quantum AI · KRS 0000808359 · NIP 7010955891 · REGON 384899425”.
Opis w stopce: „Rozwijamy ciekawość, samodzielne myślenie i talent naukowy młodych ludzi.”
Opis serwisu (`blogdescription` z `scripts/install.sh`): „Ogólnopolska olimpiada dla uczniów
ciekawych świata kwantów”.

Konwencja tytułowania wiadomości: „RODO – Olimpiada Kwantowa” / „Bezpieczeństwo małoletnich –
Olimpiada Kwantowa”.

**Media społecznościowe: brak.** W całym repozytorium nie ma ani jednego odnośnika do serwisu
społecznościowego. Formularza kontaktowego również nie ma.

### 4.2 Komitety — dane osobowe potwierdzone przez organizatora

> **Potwierdzone.** Organizator przekazał podpisany `Sklad-komitetow-Olimpiady-Kwantowej.pdf`
> (7 września 2026) — obie listy są w nim identyczne z poniższymi, co do osoby i kolejności.
> Strona `/komitety/` jest od tej chwili opublikowana, a PDF wisiał przy niej do pobrania;
> § 6 ust. 3 regulaminu wymaga publikowania aktualnego składu obu komitetów.
> PDF dokładał zakresy odpowiedzialności, których stara strona nie miała — patrz niżej.
>
> **Stan historyczny.** Skład poniżej i sam PDF opisują stan na 7 września 2026. 21 września
> 2026 organizator przekazał nowy, szerszy skład (z tytułami i afiliacjami) wprost do treści
> strony i kazał **zdjąć plik do pobrania** — od tej daty `/dokumenty/komitety/` trzyma listę
> wyłącznie na sobie, a PDF (razem z wyciągiem tekstu w `fixtures/legacy/pdf-text/`) jest
> skasowany z repozytorium; patrz `apps/cms/fixtures/legacy/komitety.md` i `docs/import/assets.md`.

**Komitet Merytoryczny** (pełni w całości funkcję Jury — § 5 ust. 2): Paweł Gora,
Grzegorz Czelusta, Michał Krupiński, Rafał Demkowicz-Dobrzański, Krzysztof Pawłowski,
Krzysztof Kurowski, Piotr Rydlichowski, Adam Wesołowski, Marek Adamczyk, Tomasz Sowiński.

**Komitet Organizacyjny:** Paweł Gora, Michał Kutwin, Tomasz Ćwik, Marcin Sadowski,
Michał Szaniewski, Grzegorz Czelusta.

**Zakresy odpowiedzialności z PDF-u** (stara strona miała w tym miejscu po jednym zdaniu):
Komitet Merytoryczny — „Zadania, kryteria oceniania, anonimowa ocena prac, kwalifikacja
i rozstrzygnięcia Jury”; Komitet Organizacyjny — „Rejestracja, komunikacja, obsługa systemu,
logistyka, miejsce finału i dokumentacja zawodów”.

Strona powtarza także podtytuł PDF-u („Członkowie i zakres odpowiedzialności”) i jego stopkę
„Kontakt z Organizatorem” (adres, e-mail, telefon) — na starej stronie tych dwóch rzeczy nie było.

Paweł Gora i Grzegorz Czelusta figurują w obu komitetach — PDF organizatora powtarza to
podwójne członkostwo, więc jest zamierzone, ale § 6 ust. 2 rozdziela role (Komitet Organizacyjny
nie ingeruje w ocenę merytoryczną): **rozbieżność do rozstrzygnięcia z organizatorem.**

Nadal brak funkcji (przewodniczący, sekretarz), afiliacji, zdjęć i adresów e-mail — a regulamin
odwołuje się do „przewodniczącego Komitetu Merytorycznego” (§ 5 ust. 4).

Uwaga: strona główna kieruje do „Komitetu Głównego”, którego nie ma ani w regulaminie, ani na
stronie Komitetów — **niespójność nazewnicza do usunięcia.**

### 4.3 Partnerzy i sponsorzy

Zero użytecznych danych. Strona `/partnerzy/` to dwa zdania („Partnerzy instytucjonalni, naukowi
oraz sponsorzy diamentowi, platynowi i złoci”), strona główna wyświetla trzy kafle z samymi
nazwami, bez logo i linków: **Ministerstwo Edukacji**, **Uniwersytet Kwantowy** (instytucja
nieistniejąca), **Polskie Towarzystwo Fizyczne**. To wypełnienie demonstracyjne —
**nie przenosić bez potwierdzenia u organizatora**, bo sugeruje patronaty, których może nie być.

## 5. Dokumenty i treści prawne

| Dokument | Status | Miejsce w nowym portalu |
|---|---|---|
| Regulamin Olimpiady Kwantowej, wersja 1.0 z 18.08.2026 | „Projekt do zatwierdzenia uchwałą Zarządu Fundacji Quantum AI”; 10 rozdziałów, 24 §; PDF + DOCX | **importowany osobno** (`seed_regulamin.py` + `DocumentPage`) |
| RODO (klauzula informacyjna), wersja 1.0 z 22.07.2026 | **PDF organizatora** (eksport z 07.09.2026) — 11 sekcji, tabela „Cel \| Podstawa” | `DocumentPage`, treść w `fixtures/legacy/rodo.md` przepisana z PDF-u (5.1) |
| Standardy ochrony małoletnich, wersja 1.0 z 22.07.2026 | **PDF organizatora** (eksport z 07.09.2026) — 10 sekcji + wersja skrócona dla uczniów; § 10 wymaga imiennego wskazania osób odpowiedzialnych uchwałą Zarządu | `DocumentPage`, treść w `fixtures/legacy/standardy-ochrony-maloletnich.md` przepisana z PDF-u (5.1) |
| ZOZ (Zasady Organizacji Zawodów) danej edycji | **nie istnieje** — regulamin odwołuje się do niego w kilkunastu miejscach | do napisania; `DocumentPage` |

### 5.1 Treści prawne przyszły z PDF-ów, a nie ze starej strony

> **Nieaktualne.** README starej strony mówił: „Treści regulaminu, RODO i standardów ochrony
> małoletnich są oznaczone jako demonstracyjne i wymagają zatwierdzenia prawnego.” Zdanie dotyczyło
> treści w instalatorze WordPressa. Organizator przekazał wszystkie trzy dokumenty jako podpisane
> PDF-y (eksport z 7 września 2026 r.), więc strony portalu są przepisane **z PDF-ów**, sekcja po
> sekcji, i nie noszą już ramki „wersja demonstracyjna”.

Co z tego wynika dla trzech stron:

- `/rodo/` i `/standardy-ochrony-maloletnich/` mają metrykę opisującą eksport („Wersja eksport
  z 7 września 2026”, status „Dokument organizatora (Fundacja Quantum AI)”) i ramkę informacyjną
  wskazującą PDF jako wersję źródłową. Numer wersji samego dokumentu („1.0 z 22 lipca 2026 r.”)
  stoi tam, gdzie postawił go organizator — w ostatniej sekcji treści,
- `/komitety/` powtarza PDF w całości: podtytuł, zdanie o Komitecie Merytorycznym pełniącym
  funkcję Jury, oba zakresy odpowiedzialności, szesnaście nazwisk i stopkę „Kontakt
  z Organizatorem”,
- brzmienia nie zmieniano ani w jednym zdaniu. Poprawiono wyłącznie łamanie wierszy z ekstrakcji
  PDF-u; dywiz w roli myślnika („zgoda - art. 6 ust. 1 lit. a”) zostaje taki, jaki jest w PDF-ie
  i w DOCX-ie regulaminu,
- wyciąg tekstu z PDF-ów leży w `backend/apps/cms/fixtures/legacy/pdf-text/` (obok samych PDF-ów,
  bo do kontenera trafia tylko `backend/`). `apps/cms/tests/test_pdf_content.py` porównuje z nim
  strony: komplet tytułów sekcji, długość spisu rozdziałów, pary „Cel | Podstawa” i liczba słów
  (≥ 90 % dokumentu).

Plików do wgrania: **2 unikalne** (regulamin PDF + DOCX) — szczegóły w `assets.md`.
Polityki prywatności / cookies jako osobnego dokumentu nie ma; jej rolę pełni strona RODO.
Deklaracji dostępności również brak.

## 6. Identyfikacja wizualna starej strony (informacyjnie)

Nowy portal ma własny design; poniżej wyłącznie zapis stanu zastanego.

| Element | Wartość |
|---|---|
| Granat (tło nagłówka, stopki) | `--navy: #081b33`, pasek narzędziowy `#04111f`, stopka `#061426` |
| Niebieski akcent / linki / CTA | `--blue: #146ee8` |
| Cyjan (eyebrow, obrys „atomu”) | `--cyan: #34c9f4` |
| Bursztyn (przycisk główny) | `--amber: #ffc857` |
| Tło strony | `--paper: #f4f7fb`, tekst `--ink: #10233e`, uzupełniający `--muted: #5c6b7c` |
| Typografia | `Inter, system-ui, -apple-system, "Segoe UI", sans-serif` — **webfont nie jest wczytywany**, więc faktycznie renderuje się krój systemowy |
| Nagłówki | `clamp()` do 5,2 rem, ujemny tracking (−0,045 em) |
| Kształty | `--radius: 18px`, cień `0 16px 45px rgba(8,27,51,.10)` |
| Logo | brak pliku; znak marki to `•` w kółku z obrysem cyjanowym + napis „OLIMPIADA KWANTOWA” w dwóch wierszach |
| Hero | gradient granat → błękit + radialny rozbłysk cyjanu, dekoracyjne koło z podwójną poświatą |
| Dostępność | `skip-link`, `aria-expanded` na przełączniku menu, `prefers-reduced-motion`, breakpointy 1000 px i 650 px |

## 7. Elementy funkcjonalne — różnice do decyzji

Nowy portal obsługuje rejestrację, zgłoszenia PDF i ocenianie. Poniżej **czym różni się** od
starego rozwiązania — każdy wiersz jest punktem do rozstrzygnięcia, bo część różnic wymaga
zmiany w regulaminie, a nie w kodzie.

| Obszar | Stara strona (WordPress) | Nowy portal | Do decyzji |
|---|---|---|---|
| **Skala ocen** | punkty 0–100, krok 0,5, walidacja `0 ≤ p ≤ 100` | skala `0/2/5/6` na zadanie (`ScoringScale`, domyślna) | regulamin § 9 ust. 2 mówi o „liczbie punktów określonej w treści zadania” — trzeba dopisać skalę wprost |
| **Wynik z wielu ocen** | **średnia arytmetyczna** `AVG(points)` wszystkich recenzentów | **konsensus**: `GradeMethod = CONSENSUS / THIRD_REVIEW / MODERATION / APPEAL` | regulamin § 9 ust. 3 („Wynikiem jest średnia ocen… różnica > 20% → dodatkowy juror”) opisuje stary model — **wymaga przepisania** |
| **Liczba recenzentów** | „co najmniej dwóch, o ile to możliwe” (regulamin); kod nie wymusza | dwóch recenzentów w workflow | uzgodnić brzmienie regulaminu z faktycznym wymuszeniem w kodzie |
| **Liczba etapów** | motyw: 3 (szkolny / okręgowy / finał); regulamin: 2 + rozmowa | `ELIM / DISTRICT / FINAL` | patrz sekcja 3.2 — **kluczowa decyzja** |
| **Rozmowa kwalifikacyjna** | regulamin § 12: 0–20 pkt, wynik 80/20 | brak odpowiednika w modelu | zostawić w regulaminie i obsłużyć poza systemem, czy usunąć? |
| **Rejestracja** | otwarta, samodzielna, e-mail = login, hasło generowane i wysyłane mailem | kody zaproszeń, role, weryfikacja | krok 2 na `/jak-zaczac/` („Załóż konto uczestnika w czasie rejestracji”) do przepisania |
| **Reklamacje / odwołania** | brak jakiejkolwiek obsługi; regulamin § 16 opisuje procedurę | `apps.appeals`, okna reklamacji, `finalize_unappealed` | zweryfikować, czy terminy z § 16 zgadzają się z `appeal_window_*` |
| **Anonimizacja** | kod `QO-` + 12 znaków, katalog `uploads/qo-private` z `.htaccess` | UUID, prywatny bucket MinIO, presigned URL 10 min | bez zmian merytorycznych |
| **Upload** | PDF, maks. 10 MB, walidacja MIME | PDF (+`.ipynb`), ClamAV, deadline egzekwowany serwerowo z `grace_seconds` | regulamin § 11 ust. 2 mówi ogólnie „format wskazany w ZOZ” — spójne |
| **Kryteria oceny** | brak siatki; wolny komentarz recenzenta | adnotacje na PDF (pdf.js) | czy komentarz recenzenta jest widoczny dla uczestnika? Stara strona: **nie** (pokazywała tylko średnią) |
| **Konflikt interesów** | tylko zapis w regulaminie § 5 ust. 5 | `district_verified`, reguła w `accounts/services.py` | bez zmian merytorycznych |
| **Publikacja wyników** | trzy statyczne strony „Wyniki zostaną opublikowane…” | `ResultsPublication` + `ResultsPage` | § 15 regulaminu do sprawdzenia pod kątem zakresu publikowanych danych |

## 8. Punkty do decyzji dla właściciela

1. **Dwa czy trzy etapy? — rozstrzygnięte: trzy (3.2).** Regulamin 1.0 z 2 września 2026 r. jest
   trzyetapowy (§ 11–13) i zgodny z modelem portalu, więc konfiguracji `Stage` nie ruszamy.
   Zostaje **redakcyjna sprzeczność wewnątrz dokumentu**: § 1 ust. 1, § 10 ust. 1 i ramka „Status
   dokumentu” nadal mówią o dwóch etapach. Do poprawienia przy najbliższej wersji regulaminu.
2. **Skala ocen i sposób łączenia ocen.** Regulamin § 9 opisuje średnią z ≥2 ocen z progiem 20%;
   portal ma 0/2/5/6 i konsensus z trzecim recenzentem. Nie da się utrzymać obu.
3. **Status prawny olimpiady.** Zastrzeżenie z `regulamin.json` mówi, że tytuły finalisty
   i laureata są „wewnętrzne” i nie dają uprawnień ustawowych. Czy nowy portal ma komunikować
   to samo (i gdzie), czy trwa procedura objęcia trybem MEN?
4. **Skład komitetów — rozstrzygnięte.** Organizator przekazał 21 września 2026 szerszy skład
   (12 + 7 osób) z tytułami i afiliacjami wprost do treści strony; podwójne członkostwo dwóch osób
   (Paweł Gora, Grzegorz Czelusta) i nazewnictwo („Komitet Główny” nie istnieje ani w regulaminie,
   ani na stronie) są rozstrzygnięte. Tego samego dnia kazał też **zdjąć plik do pobrania** ze
   składem (4.2) — strona jest od tej chwili jedynym miejscem z tą listą.
5. **Partnerzy i patroni — miejsce gotowe, treść do potwierdzenia.** `/partnerzy/` jest
   opublikowana jako `PartnersPage` z pustą listą i sekcją „Zostań partnerem”; poziomy
   współpracy (patronat honorowy, partner instytucjonalny/naukowy, sponsor
   diamentowy/platynowy/złoty, partner medialny) są w modelu. Sekcja na stronie głównej pojawia
   się dopiero z pierwszym wpisem. Do decyzji: czy Ministerstwo Edukacji i PTF to realne
   patronaty, oraz logotypy i progi sponsoringu.
6. **Zatwierdzenie treści prawnych — źródło rozstrzygnięte (5.1).** RODO i standardy ochrony
   małoletnich pochodzą z podpisanych PDF-ów organizatora, więc nie są już „demonstracyjne”.
   Otwarte zostaje to, o co proszą same dokumenty: uchwała Zarządu z punktu 7 i aktualizacja
   przy zmianie procesu (§ 11 polityki RODO).
7. **Osoby odpowiedzialne za ochronę małoletnich** — § 9 i § 10 standardów wymagają wskazania
   ich imiennie uchwałą Zarządu oraz przyjęcia wzoru karty interwencji.
8. **Daty I edycji.** Czy terminy 7 XI 2026 / 16 I 2027 / 10 IV 2027 są nadal aktualne?
   Potrzebne dodatkowo: godziny otwarcia i zamknięcia każdego etapu, terminy recenzji
   i okien reklamacyjnych (stara strona ma tylko po jednej dacie na etap).
9. **ZOZ (Zasady Organizacji Zawodów)** — regulamin odwołuje się do nich kilkanaście razy,
   a dokument nie istnieje. Bez niego regulamin nie jest kompletny (progi, liczba finalistów,
   dozwolone narzędzia, reguły remisów).
10. **Kanały kontaktu.** Jeden adres `contact@qaif.org` obsługuje sprawy ogólne, RODO
    i zgłoszenia dotyczące bezpieczeństwa małoletnich, rozróżniane tylko tematem wiadomości.
    Czy zostawiamy tak, czy wydzielamy adresy?
11. **Logo i identyfikacja.** Nie istnieje żaden plik graficzny — logo, favicon i og:image
    trzeba zaprojektować od zera.
12. **Newsy.** Trzy wpisy demonstracyjne bez dat — przepisać czy zacząć newsroom od zera?
13. **Newsletter dla nauczycieli** — strona zapowiada „zapis do listy mailingowej”; brak
    jakiejkolwiek implementacji i decyzji o narzędziu.
14. **Czy potrzebna jest strona „Olimpiady międzynarodowe”?** Treść mówi o reprezentacji Polski,
    co przed I edycją jest bezprzedmiotowe.

## 9. Usterki starej strony (nie powielać)

- **Zepsute odnośniki wewnętrzne.** Seeder tworzy `harmonogram`, `regulamin`, `rodo`
  i `standardy-ochrony-maloletnich` jako **podstrony** (`post_parent`), więc ich realne adresy to
  `/biezaca-edycja/harmonogram/`, `/dokumenty/regulamin/` itd. Tymczasem stopka, pasek boczny
  (`page.php`), formularz rejestracji i szablon zadania linkują do `/harmonogram/`, `/regulamin/`,
  `/rodo/`, `/standardy-ochrony-maloletnich/` — czyli do adresów, których nie ma. **Wszystkie te
  odnośniki dają 404.**
- **Dwa newsroomy** (`aktualnosci` i `aktualnosci-edycji`) bez rozgraniczenia zakresu.
- **Brak `meta description`** i jakiegokolwiek SEO poza `add_theme_support('title-tag')`;
  brak Open Graph, `robots.txt`, mapy witryny i danych strukturalnych.
- **Menu główne ma 6 pozycji, awaryjne 7** (różnica: „Dokumenty”) — użytkownik widzi inne menu
  zależnie od tego, czy menu zostało przypisane do lokalizacji.
- **„Komitet Główny”** na stronie głównej — organ, którego regulamin nie zna.
- Poprawki literowe do wprowadzenia przy imporcie (oznaczone w plikach treści):
  dywiz zamiast półpauzy w tytułach § 11 i § 13 regulaminu („Etap I - zawody zdalne”).

## 10. Czego nie udało się odczytać

- **Realne daty publikacji wpisów** — seeder nie ustawia `post_date`.
- **Jakiekolwiek treści redagowane po instalacji** — brak dumpu bazy; jeśli na produkcji ktoś
  edytował strony, tych zmian tu nie ma.
- **Dane partnerów i sponsorów** — nazwy, opisy, linki, logotypy: wszystkie nieobecne.
- **Funkcje i afiliacje członków komitetów.**
- **Godziny etapów, terminy recenzji i okien reklamacyjnych.**
- **Treść ZOZ** — dokument nie istnieje.
- **Zawartość plików PDF/DOCX regulaminu** nie była tutaj otwierana (regulamin importowany
  osobno); struktura odczytana z `regulamin.json`, z którego oba pliki są generowane
  (`scripts/build_regulamin.py`).
- **Czy „Ministerstwo Edukacji” i „Polskie Towarzystwo Fizyczne” to realne patronaty** — kontekst
  (obok fikcyjnego „Uniwersytetu Kwantowego”) sugeruje, że nie, ale rozstrzygnąć musi organizator.

## 11. Pliki wynikowe tego importu

- `docs/import/stara-strona-inwentarz.md` — ten dokument
- `docs/import/aktualnosci.md` — wpisy blogowe z uwagą o datach
- `docs/import/assets.md` — pliki binarne do przeniesienia
- `docs/import/tresci/*.md` — 26 plików, pełna treść każdej podstrony
