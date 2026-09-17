# System interfejsu – `static/css/app.css`

Jeden arkusz, bez frameworka i bez CDN-a (CSP nie dopuszcza ani stylu, ani skryptu inline).
Wszystko, co niżej, jest **globalne**: klasy działają w każdym szablonie, także w panelach
koordynatora, uczestnika i recenzenta. Arkusze dokładane per ekran
(`coordinator.css`, `coordinator-tools.css`, `participant.css`, `reviewer.css`,
`statistics.css`, `upload-preview.css`, `similarity-diff.css`) **nie definiują własnych kolorów,
odstępów ani promieni** – biorą tokeny z `:root`. Dzięki temu tryb wysokiego kontrastu
(`<html data-contrast="high">`) obejmuje je bez jednej dodatkowej reguły.

> **Zasada dla autorów szablonów:** jeżeli komponent jest tu opisany, używaj go zamiast pisać
> własny. Jeżeli czegoś brakuje – zgłoś, dopiszemy tutaj. Nowa klasa w arkuszu panelowym, która
> powiela `.card`, `.badge` albo `.btn`, jest długiem: wypada z trybu kontrastu i z wydruku.

---

## 1. Tokeny

Wszystkie w `:root` (sekcja 2 arkusza). Poza blokami tokenów w arkuszu nie ma kolorów wpisanych
„z ręki”. W szablonach kolorów nie ma w ogóle.

### Kolory – role

| Token | Rola |
| --- | --- |
| `--bg`, `--bg-muted` | tło dokumentu (papier) |
| `--surface`, `--surface-2`, `--surface-inset` | tła kart, pasów, nagłówków tabel |
| `--ink`, `--ink-soft`, `--muted` | tekst: podstawowy, treść komórek, drugoplanowy |
| `--line`, `--line-strong` | kreski: zwykła i wyraźna (obrys kontrolek) |
| `--brand`, `--brand-ink`, `--brand-soft` | granat nagłówka/stopki i tekst na nim |
| `--accent`, `--accent-ink`, `--accent-soft` | czerwień z logotypu **na jasnym tle** |
| `--accent-on-brand`, `--accent-rule` | akcent **na granacie** (tekst / gruba kreska) |
| `--ok`, `--warn`, `--danger`, `--info` + `-ink` / `-soft` | statusy |
| `--neutral-ink`, `--neutral-soft` | status „bez znaczenia” |
| `--link`, `--link-hover`, `--focus` | odnośniki i pierścień fokusu |

Para `X` / `X-ink` / `X-soft` jest stała w całym systemie: `--X-soft` to **tło**, `--X-ink` to
**tekst na tym tle**, `--X` to **kreska/obrys**. Każda z tych par jest policzona na ≥ 4.5:1
(AA dla tekstu) – patrz „Kontrast” niżej. Nie mieszaj: `--ok` jako tekst na `--ok-soft` daje 4.0:1
i jest błędem.

### Odstępy, promienie, cienie, typografia

```
--sp-1 4px   --sp-2 8px   --sp-3 12px   --sp-4 16px   --sp-6 24px   --sp-8 32px   --sp-12 48px
--radius-sm 4px   --radius 8px   --radius-lg 14px
--shadow-sm   --shadow   --shadow-inset
--container 1100px   --reading 68ch
--font-sans (Inter)   --font-serif (Source Serif 4 – nagłówki, liczby KPI, legendy)
```

Nie wpisuj `padding: 14px`. Skala jest po to, żeby dwa panele pisane przez dwie osoby miały ten
sam rytm pionowy.

### Warianty motywu

- **jasny** – domyślny, na nim projektujemy,
- **ciemny** – `@media (prefers-color-scheme: dark)` nadpisuje **tylko tokeny** (plus trzy wyjątki
  komponentowe, opisane komentarzem w arkuszu),
- **wysoki kontrast** – `:root[data-contrast="high"]`, czerń/biel/żółć, każda krawędź 2 px,
  cienie zgaszone, fokus 4 px. Włącza go przełącznik w pasku konta – ikona kółka wypełnionego
  w połowie (`.account-bar__btn--icon`, nazwa dostępna „Wysoki kontrast: włącz/wyłącz”).

Jeżeli twój komponent używa wyłącznie tokenów, we wszystkich trzech wariantach wygląda poprawnie
bez jednej dodatkowej reguły. **To jest cały kontrakt tego systemu.**

---

## 2. Komponenty

### 2.1 Układ strony i nagłówek widoku

```html
<div class="page-head">
  <span class="eyebrow">Panel koordynatora</span>
  <h1>Zgłoszenia</h1>
  <p class="lead">Jedno zdanie: co tu jest i co z tym zrobić.</p>
  <p class="hint">Drobniejsza uwaga techniczna.</p>
</div>

<section class="section">
  <div class="section__head"><h2>Etap I</h2> <a class="btn btn--small btn--secondary" href="…">Eksport</a></div>
  …
</section>
```

Na stronie jest **dokładnie jeden `<h1>`** i stoi w `.page-head`. `.eyebrow` nie jest nagłówkiem –
to nadtytuł, nie używaj go zamiast `<h2>`.

### 2.2 `.layout-2col` – treść plus przyklejona kolumna boczna

```html
<div class="layout-2col">
  <div class="layout-2col__main">…</div>
  <aside class="layout-2col__aside" aria-label="Skróty">…</aside>
</div>
```

Od 1000 px dwie kolumny (`minmax(0, 1fr)` + 20rem), aside `position: sticky`. Poniżej – jedna
kolumna, aside **pod** treścią i bez przyklejania. `minmax(0, …)` jest istotne: bez niego szeroka
tabela w kolumnie rozpycha siatkę i strona przewija się w bok.

Wariant `.layout-2col--aside-first` stawia kolumnę boczną po lewej (filtry). Kolejność w DOM
zostaje czytelna (treść pierwsza) – przestawia ją tylko siatka.

### 2.3 `.panel` – powłoka panelu z nawigacją

```html
<div class="panel">
  <nav class="panel__nav" aria-label="Sekcje panelu">
    <a class="panel__nav-link" href="…" aria-current="page">Przegląd</a>
    <a class="panel__nav-link" href="…">Zgłoszenia <span class="panel__nav-count">12</span></a>
  </nav>
  <div class="panel__main">…</div>
</div>
```

Stan aktywny bierze się z `aria-current="page"` – **nie** z dodatkowej klasy. Jedno źródło prawdy:
to, co słyszy czytnik ekranu, jest tym, co widzi oko. Na wąskim ekranie nawigacja zamienia się
w poziomy pas przewijalny (bez chowania pozycji – dziewięć linków za rozwijaczem to dziewięć
dotknięć zamiast jednego).

**Powłoka jest wspólna, wypełnienie nie.** `.panel` i `.panel__main` układają kolumny; `.panel__nav`
jest tylko miejscem w siatce. Wygląd pasa (pigułki, przewijanie, przyklejenie) dostaje **wyłącznie**
nawigacja, której bezpośrednimi dziećmi są `.panel__nav-link` – selektor `.panel__nav:has(> .panel__nav-link)`.
Panel koordynatora ma w tym miejscu własne menu w `<details>` (`coordinator.css`) i ta reguła go nie
dotyka; gdyby dotykała, rozwinięta lista przycięłaby się do wysokości paska. Innymi słowy: `.panel`
możesz wziąć **bez** brania mojego menu, a siatka i szerokości nadpisują się z twojego arkusza
(ładuje się po `app.css`, więc przy tej samej swoistości wygrywa).

### 2.4 Karty

```html
<div class="card">
  <div class="card__head"><h2 class="card__title">Tytuł</h2><span class="badge badge--ok">otwarte</span></div>
  <p class="card__meta">metryka</p>
  …
  <p class="card__foot">stopka karty</p>
</div>
```

`.card--quiet` (tło `--surface-2`, bez cienia) dla karty, która jest tłem dla innych rzeczy,
a nie samodzielnym obiektem. `.card--accent` – lewa krawędź w akcencie, dla jednej wyróżnionej
karty na widoku. `.cards` / `.cards--wide` to siatka `<ul>` na karty.

### 2.5 Przyciski

```html
<p class="actions">
  <button class="btn btn--primary">Zapisz</button>
  <a class="btn btn--secondary" href="…">Anuluj</a>
  <button class="btn btn--danger btn--small">Usuń</button>
</p>
```

| Klasa | Kiedy |
| --- | --- |
| `.btn--primary` | **jedna** na widok: akcja, po którą człowiek tu przyszedł |
| `.btn--accent` | zaproszenie na stronach publicznych (rejestracja) – nie w panelach |
| `.btn--secondary` | wszystko pozostałe |
| `.btn--danger` | akcja niszcząca lub nieodwracalna |
| `.btn--ghost` | akcja drugiego planu w pasku, wygląda jak odnośnik |
| `.btn--small`, `.btn--block` | modyfikatory rozmiaru |

**Jeden `--primary` na widok.** Trzy przyciski w tym samym kolorze nie mówią, który jest ważny,
a w panelu koordynatora to jest różnica między „wyślij zaproszenia” a „odśwież listę”.
Stan wyłączony: `disabled` albo `aria-disabled="true"` (przy odnośnikach). `.actions` zawija.
`.actions--end` wyrównuje do prawej. `.actions--sticky` przykleja pasek akcji do dołu formularza.

### 2.6 Odznaki (`.badge`)

```html
<span class="badge badge--ok">zakwalifikowany</span>
<span class="badge badge--warn">czeka na ocenę</span>
<span class="badge badge--danger">odrzucone</span>
<span class="badge badge--info">w toku</span>
<span class="badge badge--accent">nowe</span>
<span class="badge badge--neutral">brak</span>
```

Kropka przed tekstem rysuje się przez `::before` (żadnych obrazków, żadnego `<svg>` w każdym
wierszu tabeli). **Odznaka zawsze niesie słowo** – kolor jest wzmocnieniem, nie komunikatem
(WCAG 1.4.1; w trybie kontrastu kolory znikają i zostaje sam tekst).
`.badge--count` to wariant liczbowy (bez kropki, cyfry tabelaryczne).
`.tag` to lżejszy znacznik kategorii – nie status.

### 2.7 `.chip` – filtr / wybór / etykieta zdejmowalna

```html
<ul class="chips">
  <li><a class="chip" href="?status=all" aria-current="page">Wszystkie <span class="chip__count">120</span></a></li>
  <li><a class="chip" href="?status=new">Nowe <span class="chip__count">7</span></a></li>
</ul>
```

Stan wybrany: `aria-current="page"` (odnośnik) albo `aria-pressed="true"` (przycisk) – znów bez
osobnej klasy. `.chip--static` dla czipa, który nie jest kontrolką (etykieta na karcie).
Czip ma 2.25 rem wysokości i `--sp-2` odstępu w `.chips`, więc pole dotyku ma co najmniej 32 px
także na telefonie.

**Czip a odznaka:** czip jest **klikalny** (filtr), odznaka **opisuje** (status). Nie zamieniaj ich.

### 2.8 `.tabs` – zakładki widoku

```html
<nav class="tabs" aria-label="Widok">
  <a class="tab tab--active" href="?tab=lista" aria-current="page">Lista</a>
  <a class="tab" href="?tab=statystyki">Statystyki</a>
</nav>
```

Zakładki są **odnośnikami**, bo zmieniają adres (da się je skopiować, otworzyć w nowej karcie
i wrócić przyciskiem „wstecz”). Jeżeli naprawdę przełączają panel bez ładowania strony, użyj
`<button role="tab">` z pełnym wzorcem ARIA – arkusz stylizuje `[role="tab"][aria-selected="true"]`
tak samo jak `.tab--active`. Pas zakładek przewija się poziomo na wąskim ekranie.

### 2.9 Kafelki KPI

```html
<ul class="kpi">
  <li class="kpi__item"><span class="kpi__value">128</span><span class="kpi__label">zgłoszeń</span></li>
  <li class="kpi__item kpi__item--attention"><span class="kpi__value">7</span><span class="kpi__label">czeka na ocenę</span></li>
  <li class="kpi__item kpi__item--alert"><span class="kpi__value">2</span><span class="kpi__label">po terminie</span></li>
</ul>
```

Liczba przed etykietą i w kroju szeryfowym z `tabular-nums` – kolumna liczb nie skacze przy
odświeżeniu. `.kpi__item--link` robi z kafelka odnośnik (cały kafelek jest polem kliknięcia).
`.kpi__hint` to trzeci wiersz kafelka (np. „+3 od wczoraj”). Maksymalnie sześć kafelków; siódmy
znaczy, że to jest tabela, a nie przegląd.

### 2.10 Tabele

```html
<div class="scroll">
  <table class="table">
    <caption>Zgłoszenia etapu I</caption>
    <thead><tr><th scope="col">Uczestnik</th><th scope="col" class="num">Punkty</th></tr></thead>
    <tbody>
      <tr><th scope="row">K-0142</th><td class="num">38</td></tr>
      <tr><td colspan="2" class="empty">Brak wierszy.</td></tr>
    </tbody>
  </table>
</div>
```

- `.scroll` **zawsze** wokół tabeli: przewija się tabela, a nie strona (na 360 px to jedyne, co
  trzyma stronę bez poziomego suwaka),
- `.scroll--tall` dodaje `max-height: 70vh` i wtedy **nagłówek klei się** wewnątrz ramki
  (`th` ma `position: sticky` – bez ograniczenia wysokości nie ma się do czego kleić),
- `.num` na liczbach: do prawej, `tabular-nums`. `.total` pogrubia sumę,
- pierwsza kolumna jako `<th scope="row">`, kiedy jest nazwą wiersza,
- zebra i `:hover` są w komponencie – nie dopisuj ich w arkuszu panelowym,
- `.table--rank` – wąska pierwsza kolumna na miejsce w rankingu,
- `.table--compact` – gęstszy wiersz dla tabel roboczych (listy plików, log operacji),
- pusty wynik: `<td class="empty">` w tabeli albo `.empty` zamiast całej tabeli (2.12).

### 2.11 Pasek zaznaczenia (`.bulk-bar`)

```html
<form method="post">
  <div class="bulk-bar" data-bulk-bar hidden>
    <p class="bulk-bar__count">Zaznaczono: <strong data-bulk-count>0</strong></p>
    <div class="bulk-bar__actions">
      <button class="btn btn--small btn--secondary" name="op" value="export">Eksportuj</button>
      <button class="btn btn--small btn--danger" name="op" value="remove">Usuń</button>
    </div>
  </div>
  <div class="scroll">…tabela z checkboxami…</div>
</form>
```

Pasek jest **przyklejony do dołu okna** (`position: sticky; bottom: 0`) i stoi w tym samym
`<form>`, co tabela. Musi mieć `aria-live="polite"` na liczniku, żeby zmiana zaznaczenia była
słyszalna. Dopóki nic nie jest zaznaczone – `hidden` (arkusz respektuje `[hidden]` z `!important`,
więc nie da się go przypadkiem „odkryć” regułą układu).

### 2.12 Pusty stan

```html
<div class="empty">
  <span class="empty__title">Brak zgłoszeń</span>
  <p class="empty__text">Zgłoszenia pojawią się tu po otwarciu etapu.</p>
  <p class="empty__actions"><a class="btn btn--small btn--secondary" href="…">Otwórz etap</a></p>
</div>
```

Pusty stan mówi **dlaczego** jest pusto i **co z tym zrobić**. „Brak danych” bez zdania drugiego
i trzeciego wygląda jak awaria.

### 2.13 Formularze

Django renderujące `as_p` trafia w `.form p` i dostaje układ etykieta / pole / pomoc / błąd bez
jednej zmiany w szablonie. Dla pól pisanych ręcznie jest `.field`:

```html
<p class="field">
  <label for="id_email" class="required">E-mail</label>
  <input type="email" id="id_email" name="email"
         aria-describedby="id_email_help id_email_err" aria-invalid="true">
  <span class="field__hint" id="id_email_help">Adres, na który przyjdzie potwierdzenie.</span>
  <span class="field__error" id="id_email_err">Podaj poprawny adres.</span>
</p>
```

- `<label for>` **zawsze**, nawet gdy etykieta jest wizualnie ukryta (`.visually-hidden`),
- `aria-describedby` wskazuje **pomoc i błąd razem**, spacją oddzielone, w tej kolejności –
  czytnik przeczyta najpierw instrukcję, potem powód odrzucenia,
- `aria-invalid="true"` na polu z błędem; klasa `.field--invalid` (albo samo `aria-invalid`)
  koloruje obrys – arkusz łapie oba,
- `.field__error` i Django-we `ul.errorlist` wyglądają tak samo,
- `.form__required-note` nad formularzem wyjaśnia gwiazdkę przy `label.required`,
- grupa pól = `<fieldset>` + `<legend>`; zgody mają własny `.consents` (2.16),
- `.field--inline` stawia kratkę i etykietę w jednym wierszu.

### 2.14 Rozwijane sekcje (`.details`)

```html
<details class="details">
  <summary class="details__summary">Szczegóły decyzji</summary>
  <div class="details__body">…</div>
</details>
```

Natywne `<details>`, bez skryptu: działa bez JavaScriptu, ma obsługę klawiatury i stan
`aria-expanded` od przeglądarki. Własny trójkąt rysuje `::after` (obraca się przy `[open]`,
z poszanowaniem `prefers-reduced-motion`).

### 2.15 Komunikaty

`.msg` + `.msg-success` / `.msg-warning` / `.msg-error` / `.msg-info` (framework messages),
`.notice` / `.notice--warning` dla stałej uwagi na stronie, `.alert--*` jako alias w treści CMS.
Ikona jest w `::before` – bez obrazków.

### 2.16 Pozostałe gotowce

`.stack` (kolumna pól), `.stack-gap`, `.row`, `.meta-list` (dt/dd), `.definitions`,
`.timeline`, `.countdown`, `.status-track` (ścieżka statusu, kształt + słowo, nie tylko kolor),
`.consents` (blok zgód RODO), `.dropzone` (upload), `.code-chip`, `.visually-hidden`, `.mt-0`.

---

## 3. Dostępność – kontrakt

1. **Skip link** – `base.html`, pierwszy element `<body>`, prowadzi do `#tresc`.
2. **Landmarki** – `<header>`, `<nav aria-label="…">` (każda nawigacja ma etykietę, bo jest ich
   na stronie pięć), `<main id="tresc">`, `<footer>`. W panelu dokładasz najwyżej
   `<aside aria-label>` i `<nav class="panel__nav" aria-label>`.
3. **Jeden `<h1>`** na stronę, hierarchia nagłówków bez przeskoków.
4. **`:focus-visible`** – pierścień 3 px w `--focus` jest globalny. Nie zdejmuj go
   (`outline: none` bez zamiennika to natychmiastowa regresja dostępności). Elementy z własnym
   tłem (pasek konta, stopka) mają własny wariant pierścienia – już jest w arkuszu.
5. **Kolor nigdy sam** – status ma słowo, odnośnik w treści ma podkreślenie, krok procedury ma
   kształt kropki.
6. **`prefers-reduced-motion: reduce`** gasi każdą animację i przejście globalnie. Nowa animacja
   nie potrzebuje własnej reguły, ale **nie może** być jedynym nośnikiem informacji.
7. **`[hidden]`** działa naprawdę (`display: none !important`) – używaj go zamiast klasy `.is-hidden`.
8. **Pole dotyku** ≥ 32 px (`.btn--small` ma 2 rem + odstęp `.actions`; `.chip` 2.25 rem).
9. **Tabele**: `scope` na nagłówkach, `<caption>` albo `aria-label` na `<table>`.
10. **`aria-live`** na wszystkim, co zmienia się po HTMX-owym swapie bez przeładowania strony
    (licznik zaznaczenia, status zapisu wersji roboczej).

### Kontrast (policzony na jasnym motywie)

| Para | Kontrast | AA |
| --- | --- | --- |
| `--ink` #171d27 na `--bg` #f6f4ee | 15.4:1 | ✔ |
| `--ink-soft` #3c4453 na `--surface` #fffefb | 9.7:1 | ✔ |
| `--muted` #5d6472 na `--bg` #f6f4ee | 5.4:1 | ✔ |
| biały na `.btn--primary` (`--navy-800` #12233f) | 15.7:1 | ✔ |
| biały na `.btn--accent` (`--red-600` #a10f0f) | 8.1:1 | ✔ |
| biały na `.btn--danger` (`--brick-600` #bd3b26) | 5.5:1 | ✔ |
| `--ok-ink` #1f5c42 na `--ok-soft` #e0efe7 | 6.6:1 | ✔ |
| `--warn-ink` #7a4f0c na `--warn-soft` #f8eed9 | 6.2:1 | ✔ |
| `--danger-ink` #8f2c16 na `--danger-soft` #fbe8e0 | 7.0:1 | ✔ |
| `--info-ink` #1b3153 na `--info-soft` #e4eaf4 | 10.8:1 | ✔ |
| `--neutral-ink` #4a515e na `--neutral-soft` #e9e6dd | 6.4:1 | ✔ |
| `--accent-ink` #6d0303 na `--accent-soft` #f8e5e3 | 10.4:1 | ✔ |
| `--link` #1d4a86 na `--surface` #fffefb | 8.8:1 | ✔ |
| `--accent-on-brand` #ef8a82 na `--brand` #12233f | 6.5:1 | ✔ |
| żółć #ffe500 na czerni (tryb kontrastu) | 16.5:1 | ✔ AAA |

Liczby są policzone wzorem WCAG 2.1 (relatywna luminancja sRGB) – nie „na oko”.

Kombinacja, której **nie** wolno użyć: `--ok` (4.2:1), `--warn` (4.0:1) ani `--danger` (4.6:1)
jako **tekst** na własnym `-soft`. Te tokeny są kreską i obrysem; tekstem jest `-ink`.
`--muted` nie schodzi poniżej `--surface-2` (5.6:1) – na ciemniejszym tle niż papier używaj
`--ink-soft`.

---

## 4. Ekrany wąskie

- każda tabela w `.scroll` – to jest jedyny wymóg, którego złamanie natychmiast widać
  (poziomy suwak na całej stronie),
- `.actions`, `.chips`, `.tabs`, `.kpi` zawijają się albo przewijają same,
- pasek konta na 360 px: przyciski zawijają się i wyrównują do lewej, adres e-mail skraca się
  do 12 znaków z wielokropkiem,
- `.panel__nav` na wąskim ekranie jest poziomym pasem przewijalnym,
- nic w arkuszu nie ma stałej szerokości w pikselach większej niż 360 px; długie słowa
  (kody, adresy, nazwy plików) łamie `overflow-wrap: anywhere` w komórkach tabel i na kartach.

Sprawdzenie: `e2e/check_mobile.py` (360 / 768 / 1280 px, brak przewijania w poziomie) oraz
`e2e/check_a11y.py` (alt, etykiety, jeden `h1`, skip link, kolejność fokusu). Oba chodzą po
adresach publicznych (`/`, `/login/`, `/register/`, `/zadania/`, `/statystyki/`) i wymagają sieci
compose (obraz `e2e` z Playwrightem):

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm -T \
    -e E2E_BASE_URL=http://web:8000 e2e python /e2e/check_mobile.py
```

**Dlaczego nie axe-core.** Wstrzyknięcie `axe.min.js` z CDN-a wymagałoby skryptu inline albo obcego
`script-src` – czyli rozluźnienia CSP na czas testu, więc sprawdzania strony w konfiguracji, która
nigdy nie trafi do ludzi. Zamiast tego `check_a11y.py` sprawdza ręcznie pięć rzeczy, które psują
się najczęściej. To nie jest audyt; to jest siatka na regresje.

**Stan:** oba skrypty są napisane, ale **nie zostały uruchomione** – w tym środowisku nie ma
dostępu do sieci, którego potrzebuje obraz `e2e` (firmowe proxy). Pierwszy przebieg w czystym
środowisku jest jednocześnie ich pierwszym sprawdzeniem; komunikaty są napisane tak, żeby po
takim przebiegu było widać, czy usterka jest w stronie, czy w skrypcie.

---

## 5. Wydruk

`@media print` zdejmuje nawigację, paski, komunikaty i przyciski, a zostawia treść. Poza tym:

- `.scroll` przestaje przewijać (na papierze nie ma czego przewijać), a `thead` powtarza się na
  każdej kartce (`display: table-header-group`),
- odznaki drukują się jako ramka z tekstem (tło znika – drukarka atramentowa i tak by je zjadła),
- strona wyników i weryfikacja dokumentu (`.page-head`, `.meta-list`, `.table--rank`) mają układ
  jednokolumnowy i czarny tekst,
- klasa `.print-only` pokazuje element **wyłącznie** na papierze (nagłówek z adresem strony
  i datą wydruku), `[data-print]` / `.no-print` chowa go z papieru.

---

## 6. Czego brakuje w panelach – lista do przyjęcia przez autorów szablonów

Nie ruszam szablonów paneli (`templates/web/coordinator/`, `.../participant/`, `.../reviewer/`)
ani ich arkuszy. To, co niżej, jest listą rzeczy, które **są już gotowe w `app.css`** i które
warto podmienić u siebie:

1. **Filtry na listach** → `.chips` zamiast rzędu `.btn--secondary`. Przyciski w roli filtra nie
   pokazują, który filtr jest włączony; czip pokazuje (`aria-current`).
2. **Przełączanie widoku** (lista / statystyki / ustawienia) → `.tabs` + `.tab`.
3. **Nawigacja panelu** → `.panel` / `.panel__nav` / `.panel__main`; stan aktywny wyłącznie przez
   `aria-current="page"`.
4. **Kolumna boczna** (filtry, skróty, „co dalej”) → `.layout-2col` – aside przykleja się sam.
5. **Każda tabela w `.scroll`**, długa dodatkowo `.scroll--tall`; liczby w `.num`, sumy w `.total`.
6. **Zaznaczanie wierszy** → `.bulk-bar` zamiast rzędu przycisków nad tabelą; licznik z
   `aria-live="polite"`.
7. **Pusty stan** → `.empty` z `__title` + `__text` + `__actions`, nie sam akapit „brak danych”.
8. **Jeden `.btn--primary` na widok.** W kilku widokach panelu stoi dziś po trzy.
9. **Błędy pól** → `.field__error` + `aria-describedby` + `aria-invalid` (albo po prostu
   `form.as_p`, który daje to samo).
10. **Rozwijane sekcje** → `<details class="details">`, nie `<div>` ze skryptem.
11. **Liczby przeglądowe** → `.kpi`; nie buduj własnych kafelków z `.card`.
12. **Żadnych kolorów w arkuszach panelowych** – wyłącznie tokeny. Kolor wpisany z ręki znika
    w trybie wysokiego kontrastu i psuje wydruk.
13. **Nagłówek widoku** → `.page-head` z jednym `<h1>`; `.eyebrow` jako nadtytuł.
14. **Etykiety nawigacji** – każda `<nav>` w panelu potrzebuje `aria-label`, bo na stronie jest
    ich już pięć i „nawigacja” bez nazwy nie pomaga nikomu.

### Co już jest zrobione dobrze (nie ruszać)

- `base.html` ma skip link jako pierwszy element `<body>`, `<header>`, `<main id="tresc">`,
  `<footer>` i wszystkie `<nav>` z `aria-label`. Nic tu nie brakowało, więc nic nie zmieniałem –
  test `apps/web/tests/test_ui_system.py` pilnuje, żeby to zostało.
- `coordinator.css` bierze wszystkie kolory z tokenów i nie nadpisuje niczego poza szerokością
  kolumny. To jest wzorzec dla arkuszy panelowych.
- Panel koordynatora używa `<details>` zamiast skryptu – tak samo działa `.details` i menu
  dokumentów w nagłówku.

### Uwaga o współwłasności `app.css`

`app.css` jest arkuszem **wspólnym** i w trakcie tej sesji dopisywały do niego także inne osoby
(komponent `.announcements`, sekcja `/faq/`). To działa, dopóki dopisek trzyma się dwóch reguł:
bierze kolory wyłącznie z tokenów i nie nadpisuje istniejącego komponentu. Trzecia reguła jest
nowa: **nowa nazwa klasy nie może być nazwą generyczną**, jeżeli ma należeć do jednego ekranu.
`.announcement` jest w porządku (jest komponentem serwisu), `.tab` na oznaczenie czegoś innego
niż zakładka – nie byłoby.

Praktyczny przykład z tej sesji: powłoka `.panel` / `.panel__nav` / `.panel__main` powstała
równolegle tutaj i w panelu koordynatora. Rozjazdu nie ma, bo generyczny wygląd **pasa** nawigacji
jest zawężony do `.panel__nav:has(> .panel__nav-link)` – panel z własnym menu (`<details>`) bierze
z systemu samą siatkę. Tę sztuczkę warto powtórzyć wszędzie, gdzie nazwa jest wspólna, a wypełnienie
nie.
