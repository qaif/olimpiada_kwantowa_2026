# MED-01: Medale olimpiady międzynarodowej, dyplomy w języku ucznia i ranking krajów (IQO)

## 0. Cel i granice

Olimpiada międzynarodowa (`iqo`) nagradza jak IPhO/IMO: **złoto, srebro, brąz i wyróżnienie**
(honourable mention) liczone z ostatecznego rankingu etapu, a nie tytuł laureata nadawany decyzją
komitetu. Do tego dyplomy w **języku ucznia** (11 języków interfejsu, także arabski, chiński,
hindi i bengalski), nieoficjalny **ranking krajów** jak na IMO i strona wyników z medalami,
filtrem kraju i eksportem na galę.

Czego zadanie **nie** robi:
- **Olimpiada Kwantowa bez zmian** – tytuł laureata/finalisty, progi kwalifikacji, publikacja
  wyników, skład dotychczasowych dyplomów i ich teksty zostają co do bajtu. Cała funkcja stoi za
  flagą konkursu `medals` (domyślnie **wyłączona**); bez niej nie ma ani adresu (404), ani pozycji
  menu, ani zmiany w tabeli wyników,
- nie zmienia obliczania wyników etapu (`apps/results/services.py`) – medale **czytają** ranking
  (`compute_stage_results`, `ResultsPublication`), niczego w nim nie przestawiają,
- nie tłumaczy nazw krajów (słownik `accounts.Region` jest po angielsku – IQO jest anglojęzyczny).

## 1. Schemat nagród (per etap)

`MedalScheme` – jeden na etap (OneToOne do `competitions.Stage`, czyli per konkurs i edycja), który
koordynator wskazuje jako ranking ostateczny. Pola (domyślnie jak IPhO):

| pole | domyślnie | znaczenie |
|---|---|---|
| `gold_percent` | 8 | złoto – najlepsze 8 % uczestników |
| `silver_percent` | 17 | srebro – **kolejne** 17 % (łącznie 25 %) |
| `bronze_percent` | 25 | brąz – kolejne 25 % (łącznie 50 %) |
| `tie_policy` | `INCLUSIVE` | remis na granicy: `INCLUSIVE` – cała grupa dostaje wyższą nagrodę (jak `TOP_N`), `EXCLUSIVE` – grupa, która przekroczyłaby pulę, dostaje niższą |
| `hm_percent_of_best` | 50 | wyróżnienie: bez medalu i wynik ≥ X % najlepszego wyniku (puste = kryterium wyłączone) |
| `hm_full_solution` | tak | wyróżnienie także za **pełne rozwiązanie** choć jednego zadania |

Reguły obliczenia (czysta funkcja, bez bazy):
- pole `N` = wiersze etapu bez `DISQUALIFIED` (zdyskwalifikowany nie dostaje niczego i nie liczy się
  do puli); pula nagrody = `ceil(N × procent łączny / 100)` – zaokrąglenie w górę, ta sama reguła,
  co `PERCENTILE` w regułach przejścia,
- próg nagrody jest **wartością sumy punktów**, nie miejscem – **ten sam wynik = ta sama nagroda**,
  zawsze (także gdy etap ma kryteria rozstrzygania remisów: te ustawiają miejsca, nie medale),
- zero punktów nie daje ani medalu, ani wyróżnienia (jak „zero nie kwalifikuje”),
- „pełne rozwiązanie” = punkty zadania ≥ maksimum zadania (`competitions.scoring.problem_maximum`);
  etap testowy (bez zadań) tego kryterium nie ma.

**Podgląd** liczy się zawsze (`compute_stage_results(preview=True)`, bez zapisu): progi punktowe,
liczności, rzeczywiste odsetki i tabela z nagrodą wyliczoną, ręczną i ostateczną.

**Ręczna zmiana** (`MedalOverride`) – nagroda inna niż wyliczona dla jednego wpisu, z **obowiązkowym
uzasadnieniem**; dodanie, zmiana i usunięcie idą do audytu (bez treści uzasadnienia w `diff` –
tylko identyfikatory i nagrody). Ekran ostrzega, że uzasadnienie nie powinno zawierać danych
osobowych.

**Zamrożenie** („Ogłoś medale”): wolno dopiero po publikacji wyników etapu (`ResultsPublication`)
i tylko wtedy, gdy bieżąca tabela jest tą ogłoszoną: te same wpisy (`entry_totals`), te same sumy
i te same liczności stanów (zakwalifikowani / niezakwalifikowani / zdyskwalifikowani – z wpisu audytu
`results.qualification_applied` publikacji, bo snapshot stanu wiersza nie przechowuje) – inaczej 409
`RESULTS_CHANGED` („opublikuj wyniki ponownie”). Ręcznej nagrody nie da się wpisać zdyskwalifikowanemu. Zamrożenie zapisuje w schemacie: nagrody per wpis,
progi, publiczną tabelę (podpisy wierszy przez `results.services.build_snapshot` w trybie
anonimizacji publikacji – **te same zgody**, co tabela wyników) i tabelę krajów. Po zamrożeniu
schematu i ręcznych zmian nie da się edytować (409 `AWARDS_FROZEN`). „Odmroź” – z uzasadnieniem,
w audycie, zdejmuje stronę publiczną do ponownego ogłoszenia. Ponowna publikacja wyników po
zamrożeniu nie zmienia medali – panel pokazuje ostrzeżenie o rozjeździe.

## 2. Dokumenty w języku ucznia

- Nowe rodzaje `results.CertificateKind`: `MEDAL_GOLD`, `MEDAL_SILVER`, `MEDAL_BRONZE`,
  `HON_MENTION` (oraz kopie w `tenancy.DocumentKind`). Rejestr, numeracja, kod weryfikacyjny,
  pieczęć, ZIP i strona `/dyplomy/<kod>/` – bez zmian, z `apps.results.certificates`.
  Rodzaje medalowe wystawia **wyłącznie** ekran medali (z zamrożonych nagród) – formularz „Wystaw”
  w dotychczasowych ekranach ich nie oferuje.
- Zaświadczenie o udziale: dotychczasowy rodzaj `UCZESTNIK`; w konkursie z flagą `medals` składa
  się w języku ucznia (ten sam skład, co medale).
- Język dokumentu: zapis ucznia (`UserPreference.language`) zawężony do języków konkursu, inaczej
  język domyślny konkursu – ta sama reguła, co listy (`language_for`). **Zamrażany przy wystawieniu**
  (`CertificateLanguage`), żeby dokument nie zmieniał języka razem z ustawieniami konta.
- Treść: tytuł, zdanie, linia podpisu i stopka przez gettext (katalog `apps/medals/locale`).
  Tekst organizatora z `tenancy.DocumentTemplate` (flaga `document_templates`) obowiązuje
  w **języku domyślnym konkursu** – szablon tekstu jest jednojęzyczny.
- Grafika: `results.CertificateTemplate` (tło, logo, podpisy, układ) – bez zmian.

### 2.1 Pismo i kroje

- Kroje w repozytorium (`apps/medals/fonts/`, z licencjami): DejaVu Sans (łacina, cyrylica,
  grecki – już w `static/fonts`), Noto Sans Arabic / Devanagari / Bengali (SIL OFL 1.1),
  Droid Sans Fallback (Apache 2.0, chiński uproszczony). Osadzane w PDF jako podzbiory.
- Kształtowanie (ligatury arabskie, łączenia i znaki dewanagari/bengalskie) – `uharfbuzz` przez
  `reportlab.pdfbase.ttfonts.shapeStr`; kierunek – własny, mały algorytm dwukierunkowy na poziomie
  przebiegów (klasy `unicodedata.bidirectional`, reguły neutralnych N1/N2, odwracanie L2).
- Dobór kroju **per znak** (pismo → krój, potem pokrycie), więc nazwisko w innym piśmie niż
  język dokumentu też się składa.
- **Język przypinany przy wystawieniu** (`CertificateLanguage`): ten, w którym dokument naprawdę się
  złoży – pismo niedostępne w chwili wystawienia → angielski i ostrzeżenie z numerami w raporcie.
  **Przy pobraniu** dokument przypięty do pisma, którego serwer już nie składa, jest **błędem**
  (`DocumentLanguageUnavailable`, 503, wpis `ERROR`) – nie cichym dokumentem w innym języku; ekran medali
  pokazuje ostrzeżenie. Znak bez kroju → `?` i ostrzeżenie. Ekran medali pokazuje stan potoku dla każdego
  z 11 języków.
- Cyfry arabsko-indyjskie i perskie (pismo arabskie na poziomie LTR) idą bez kształtowania, w kolejności
  logicznej – HarfBuzz zgadywałby dla nich RTL.
- **Dyplom nieaktualny:** dyplom medalowy, którego rodzaj ≠ ogłoszona nagroda (albo medale odmrożone), jest
  oznaczany na `/dyplomy/<kod>/` i nie pojawia się w „Moich dyplomach” (pobranie – 404).

## 3. Ranking krajów (nieoficjalny, jak IMO)

Per kraj (delegacja, a bez niej region uczestnika): liczba uczestników, złoto/srebro/brąz/
wyróżnienia, suma i średnia punktów; miejsce po sumie punktów (remis = to samo miejsce), sortowanie
także po medalach. Publiczna strona `/results/<etap>/countries/` – wyłącznie po zamrożeniu. Wyłącznie
**agregaty**: bez nazwisk, kodów i wyników indywidualnych. **Suma, średnia i miejsce tylko dla kraju
z co najmniej 3 wynikami** (`MIN_COUNTRY_GROUP`; mniej → „—”, liczby medali zostają) – suma drużyny
jedno- albo dwuosobowej jest wynikiem osoby. Przy publikacji „tylko awansujący” sumę liczy się wyłącznie
z wyników nagrodzonych (wyników reszty pola etap nie ogłosił).

## 4. Strona wyników z medalami i eksport na galę

- `/results/<etap>/medals/` (publiczna, po zamrożeniu): miejsce, podpis wiersza z publikacji,
  kraj, suma, odznaka medalu; filtr `?country=`. Kraj przy wierszu tylko wtedy, gdy publikacja jest
  w trybie `CODE` (tam okręg/kraj i tak jest jawny) albo wiersz jest podpisany nazwiskiem za zgodą
  w trybie imiennym (`FULL`/`FULL_ALL`); pozostałe wiersze – także „inicjały i szkoła” – bez kraju
  (podpis + kraj + wynik w małej delegacji wskazuje osobę).
- Odnośnik z `/results/<etap>/` – tylko gdy medale są zamrożone (Olimpiada Kwantowa: bez zmiany).
- Eksport koordynatora (dane osobowe, wpis w audycie): CSV (miejsce, nagroda, imię i nazwisko,
  kraj, szkoła, suma, kod, ręczna zmiana) i PDF na galę (grupy w kolejności wręczania: wyróżnienia,
  brąz, srebro, złoto; w grupie po kraju i nazwisku; nazwiska w dowolnym piśmie).

## 5. Wymagania przekrojowe

Zakres konkursu (`request.competition`, `for_competition`, etap innego konkursu → 404), rola
koordynatora sprawdzana w widoku i w serwisie, audyt każdej czynności, limit POST-ów (scope
`medals`), bez JS inline (filtr kraju to zwykły formularz GET), napisy stron publicznych i dokumentów
przez gettext w 10 katalogach aplikacji, ekrany koordynatora po polsku (I18N-01 § 0). RODO:
czynność „Medale i ranking krajów” w rejestrze (warunkowo, gdy flaga), nagrody i ręczne zmiany
w eksporcie danych konta, kaskada z wpisem przy usunięciu konta.

## 6. Testy

Czysta funkcja nagród (domyślne progi, remisy na każdej granicy w obu politykach, zera,
zdyskwalifikowani, wyróżnienie za procent i za pełne zadanie, małe pola); podgląd, ręczna zmiana
z audytem, zamrożenie (bramka publikacji i rozjazdu sum, blokada edycji, odmrożenie); izolacja
(etap innego konkursu → 404, uczestnik → 403, flaga wyłączona → 404); strona publiczna (zgody,
kraj tylko przy dozwolonych wierszach, filtr), ranking krajów; wystawienie dokumentów, język
ucznia zamrożony, PDF w każdym z 11 języków (kroje osadzone, odwrót przy braku kształtowania);
kwantowa: formularz „Wystaw” bez rodzajów medalowych, brak menu i odnośnika.

## 7. Realizacja (4.10.2026) – gdzie co jest, odstępstwa i luki

Gdzie co jest:
- `backend/apps/medals/`: `models.py` (`MedalScheme`, `MedalOverride`, `CertificateLanguage`, flaga
  `medals`), `awards.py` (czysta arytmetyka nagród i tabeli krajów), `services.py` (czynności, bramki,
  audyt), `typesetting.py` (skład wielopismowy), `documents.py` (treść i skład dyplomu w języku ucznia),
  `exports.py` (CSV, lista na galę), `export.py` (sekcja RODO), `views.py`/`urls.py`/`templates/medals/`,
  `fonts/` (kroje z licencjami i `SOURCES.txt`), `locale/` (10 katalogów).
- Wspólne aplikacje (minimalnie): `results.CertificateKind` + 4 rodzaje (`results.0008`),
  `MANUAL_KIND_CHOICES`/`template_kind_choices`, `AWARD_TITLES` i rejestr składów `register_composer`
  w `results/certificates.py`; `tenancy.DocumentKind` + 4 rodzaje (`tenancy.0015`); flaga
  w `FEATURE_DEFAULTS`; pozycja menu; jeden znacznik w `templates/web/results.html`; czynność w rejestrze
  RODO i sekcja `medale` w eksporcie konta.

Odstępstwa (z powodem):
1. **Schemat jest per etap** (OneToOne do `Stage`), a nie per edycja: etap jest rankingiem, z którego
   liczy się nagrody, a edycja i konkurs wynikają z niego. Koordynator wybiera etap na liście.
2. **Polityka remisu jest ustawieniem** (`INCLUSIVE` domyślnie – jak próg `TOP_N`; `EXCLUSIVE` – „nie
   więcej niż pula”, bliżej reguły IMO). Remis nie jest dzielony w żadnej z nich.
3. **Zaświadczenie o udziale** to dotychczasowy rodzaj `UCZESTNIK`, składany w języku ucznia wyłącznie
   w konkursie z flagą `medals` – bez piątego nowego rodzaju i bez zmiany w Olimpiadzie Kwantowej.
4. **Tekst z `DocumentTemplate`** obowiązuje tylko w języku domyślnym konkursu (szablon tekstu jest
   jednojęzyczny); pozostałe języki – tłumaczenia wbudowane.
5. **Podpisy wierszy publicznej tabeli medali są zamrażane** przy ogłoszeniu (jak snapshot wyników);
   nazwiska na liście na galę – czytane w chwili eksportu.
6. **Dyplom wystawiony przed zmianą nagrody nie jest kasowany** – wiersz rejestru zostaje (rejestr nie ma
   stanu „unieważniony”), ale dokument jest „nieaktualny” (rejestr sprawdzeń `register_currency_check`
   w `results/certificates.py`): strona weryfikacji to mówi, „Moje dyplomy” go nie pokazują, raport
   „Wystaw dokumenty” podaje numer.
9. **Bramka stanów przez audyt:** publikacja nie przechowuje stanu wiersza, a wpis nie ma historii stanów,
   więc ogłoszenie porównuje liczności stanów z wpisem audytu publikacji. Zamiana jednego
   zdyskwalifikowanego na innego przy tych samych licznościach przeszłaby – w praktyce każda zmiana
   dyskwalifikacji przechodzi i tak przez ponowną publikację.
7. **CJK:** Droid Sans Fallback (TrueType, 4 MB) zamiast Noto Sans CJK (kontury CFF – ReportLab ich nie
   osadza); pogrubienie symulowane obrysem.
8. **Kierunek tekstu:** własny podzbiór UBA (W1/W2/W7, N0 dla nawiasów, N1/N2, I1/I2, L2) na poziomie
   linii, bez osadzeń jawnych – `rlbidi` (wymagany przez ReportLab do RTL) nie jest w PyPI.

Znane luki:
- tłumaczenia (37 napisów × 10 języków) są maszynowe – do przeglądu native speakerów przed galą,
- tekst arabski, dewanagari i bengalski w PDF-ie wygląda poprawnie, ale kopiowanie i wyszukiwanie tekstu
  z pliku bywa niedokładne (brak mapy ToUnicode dla ligatur) – ograniczona dostępność dla czytników ekranu,
- nazwy krajów są po angielsku (słownik `Region`), także na dyplomie w innym języku,
- liczby medali kraju jednoosobowego są publiczne (jak IMO) – medal jest ogłoszeniem sam w sobie,
- ponowna publikacja wyników po ogłoszeniu medali nie odmraża ich sama (panel ostrzega),
- opiekun drużyny nie ma osobnego widoku medali swoich uczniów (widzi strony publiczne).
