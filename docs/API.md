# API integracji — Olimpiada Kwantowa

Dokument dla **programisty systemu zewnętrznego**: kuratorium, uczelni, partnera medialnego,
systemu rekrutacyjnego. Opisuje publiczne API `/api/v1/`, klucze i zakresy, webhooki wraz
z weryfikacją podpisu, limity żądań oraz zasady wersjonowania.

Adres serwisu produkcyjnego: `https://olimpiadakwantowa.pl`. Wszystkie ścieżki niżej są względne
wobec niego. Interaktywny schemat: [`/api/docs/`](https://olimpiadakwantowa.pl/api/docs/) (sekcja
**Integracje**), maszynowo: [`/api/schema/`](https://olimpiadakwantowa.pl/api/schema/).

Czym to API **nie jest**: nie jest kanałem do pobierania prac uczestników ani treści recenzji.
Rozwiązanie jest utworem uczestnika i materiałem oceny — wychodzi z systemu wyłącznie do
recenzenta i do koordynatora. Na zewnątrz idą identyfikatory, kody publiczne, metadane i wyniki,
które i tak zostały ogłoszone.

---

## 1. Uwierzytelnienie

Każde żądanie niesie klucz API organizatora w nagłówku `Authorization`:

```
Authorization: Bearer ok_<prefix>_<secret>
```

Klucz wystawia koordynator w panelu (**Ustawienia → Integracje**). Klucz w postaci jawnej jest
pokazywany **jeden raz, przy wystawieniu** — w bazie leży wyłącznie jego skrót SHA-256. Zgubionego
klucza nie da się odczytać: unieważnia się go i wystawia nowy.

Klucz przekazuje się kanałem, którym przekazuje się hasła. Nie trzymaj go w repozytorium ani
w treści zgłoszenia — przedrostek `ok_` jest rozpoznawany przez skanery sekretów i taki wyciek
kończy się natychmiastowym unieważnieniem.

### Odpowiedzi na problemy z poświadczeniem

| Kod HTTP | `code` | Co znaczy |
|---|---|---|
| 401 | `API_KEY_REQUIRED` | Brak nagłówka `Authorization`. |
| 401 | `INVALID_API_KEY` | Nagłówek jest, ale klucz nie istnieje albo sekret się nie zgadza. |
| 401 | `API_KEY_REVOKED` | Klucz istniał i został unieważniony — poproś organizatora o nowy. |
| 403 | `MISSING_SCOPE` | Klucz działa, ale nie ma zakresu wymaganego przez ten zasób. |
| 404 | `NOT_FOUND` | Zasób nie istnieje **albo** leży poza edycją, do której klucz jest zawężony. |
| 429 | `THROTTLED` | Przekroczony limit żądań na minutę (patrz § 5). |

Każdy błąd ma ten sam kształt, co reszta API platformy:

```json
{ "code": "MISSING_SCOPE", "detail": "Klucz nie ma zakresu „read:results”." }
```

Na `403` i `404` nie da się rozróżnić „nie ma” od „nie dla ciebie” i to jest zamierzone: inaczej
po kodach odpowiedzi dałoby się policzyć etapy edycji, do której klucz nie ma dostępu.

### Zakresy

Klucz niesie zamkniętą listę zakresów. Każdy zasób wymaga dokładnie jednego:

| Zakres | Otwiera |
|---|---|
| `read:participants` | `GET /api/v1/stages/<id>/participants/` — bez danych osobowych |
| `read:participants_pii` | te same wiersze **z** imieniem, nazwiskiem i adresem e-mail |
| `read:results` | `GET /api/v1/stages/<id>/results/` |
| `read:submissions_meta` | `GET /api/v1/stages/<id>/submissions/` |
| `read:stats` | `GET /api/v1/stats/` |
| `write:events` | `POST /api/v1/events/` |

Katalog (`GET /api/v1/`, `/editions/`, `/editions/<id>/stages/`) wymaga jedynie ważnego klucza:
zawiera to, co i tak stoi na publicznych stronach serwisu, a bez niego nie dałoby się ustalić
identyfikatorów do pozostałych zasobów.

**Dane osobowe wymagają dwóch niezależnych zgód**: zakresu `read:participants_pii` *oraz* flagi
„dane osobowe dozwolone” postawionej przy kluczu ręcznie przez koordynatora. Cofnięcie samej flagi
odbiera dostęp do imion i nazwisk natychmiast, bez odbierania zakresu i bez wystawiania nowego
klucza.

### Zawężenie do edycji

Klucz może być przypisany do jednej edycji. Widzi wtedy wyłącznie jej dane, a zasoby innych
edycji odpowiadają `404`. Klucz bez edycji widzi wszystkie — także przyszłe roczniki.

---

## 2. Zasoby `/api/v1/`

Wszystkie listy są stronicowane: `?page=<n>` i `?page_size=<n>` (domyślnie 100, maksymalnie 500).
Odpowiedź ma kształt `{"count": …, "next": …, "previous": …, "results": […]}`.

### 2.1. Co może ten klucz

```bash
curl -s https://olimpiadakwantowa.pl/api/v1/ \
  -H "Authorization: Bearer ok_7f3a9c21_..."
```

```json
{
  "key_prefix": "7f3a9c21",
  "name": "Kuratorium Mazowieckie",
  "edition_id": 4,
  "scopes": ["read:participants", "read:results"],
  "pii_allowed": false,
  "rate_limit_per_minute": 120
}
```

Odpowiedź niesie też słowniki `available_scopes` i `webhook_events` — pełną, aktualną listę
zakresów i zdarzeń wprost z systemu.

### 2.2. Edycje i etapy

```bash
curl -s https://olimpiadakwantowa.pl/api/v1/editions/ -H "Authorization: Bearer $OK_KEY"
curl -s https://olimpiadakwantowa.pl/api/v1/editions/4/stages/?kind=ELIM -H "Authorization: Bearer $OK_KEY"
```

Etap niesie cały kalendarz: `opens_at`, `deadline_at`, `grace_seconds`, `review_deadline_at`,
`appeal_window_opens_at`, `appeal_window_closes_at`, `results_published_at`, `closed_at`
oraz `problem_count`.

### 2.3. Uczestnicy etapu — `read:participants`

```bash
curl -s "https://olimpiadakwantowa.pl/api/v1/stages/12/participants/?voivodeship=mazowieckie&grade=3" \
  -H "Authorization: Bearer $OK_KEY"
```

```json
{
  "count": 341,
  "next": "https://olimpiadakwantowa.pl/api/v1/stages/12/participants/?page=2",
  "previous": null,
  "results": [
    {
      "public_code": "OLM-2026-0042",
      "school": "XIV LO im. Stanisława Staszica w Warszawie",
      "school_city": "Warszawa",
      "voivodeship": "mazowieckie",
      "grade": 3,
      "status": "QUALIFIED",
      "registered_at": "2026-10-02T09:14:51.120943Z"
    }
  ]
}
```

Filtry: `voivodeship`, `status`, `grade`, `school` (fragment nazwy). Z zakresem
`read:participants_pii` **i** zgodą PII każdy wiersz dostaje dodatkowo `first_name`, `last_name`
i `email`. `school_city` jest wypełnione tylko dla szkół z wykazu SIO — szkoła wpisana ręcznie
nie ma miejscowości jako osobnej danej i nie zgadujemy jej.

**Wieku tu nie ma i nie będzie.** Serwis zbiera od uczestnika pełną datę urodzenia (potrzebuje
jej, żeby rozstrzygnąć, czy udział wymaga zgody opiekuna prawnego), ale to API nie wystawia ani
daty, ani rocznika, ani wyliczonego wieku — także z zakresem `read:participants_pii`. Odbiorca
zewnętrzny nie ma celu, dla którego byłyby mu potrzebne, a data urodzenia jest klasycznym kluczem
dopasowania osoby do innych zbiorów. Jeżeli Twój scenariusz naprawdę wymaga wieku, napisz — to
jest rozmowa o podstawie prawnej, a nie o polu w odpowiedzi.

### 2.4. Wyniki etapu — `read:results`

Wyłącznie **ogłoszona, zamrożona** tabela. Etap bez publikacji odpowiada `404`: dla świata na
zewnątrz tabela, której nie ogłoszono, nie istnieje.

```bash
curl -s https://olimpiadakwantowa.pl/api/v1/stages/12/results/ -H "Authorization: Bearer $OK_KEY"
```

```json
{
  "count": 341,
  "next": null,
  "previous": null,
  "stage": {
    "stage_id": 12,
    "edition_id": 4,
    "published_at": "2027-01-15T18:00:00Z",
    "anonymization": "CODE",
    "qualified_only": false,
    "count": 341
  },
  "results": [
    { "rank": 1, "display": "OLM-2026-0042", "points": {"1": 6, "2": 5},
      "total": 11, "qualified": true, "manual": false, "voivodeship": "mazowieckie" }
  ]
}
```

`display` jest gotowym napisem i zależy od trybu anonimizacji wybranego przy publikacji
(kod, inicjały ze szkołą albo pełne nazwisko w finale). API nie widzi tu więcej niż publiczna
strona wyników. Pole `voivodeship` występuje tylko w tabelach anonimizowanych kodem.

`points` i `total` są liczbami JSON: całkowitymi w etapie „tylko wartości ze skali”, a w etapie
z dowolnymi wartościami ocen także ułamkowymi (`{"1": 4.25, "2": 3.5}`, `"total": 7.75`) – patrz
§ 6.2.

### 2.5. Oddane prace — `read:submissions_meta`

Metadane, nigdy pliki:

```bash
curl -s "https://olimpiadakwantowa.pl/api/v1/stages/12/submissions/?problem=1&status=FINAL" \
  -H "Authorization: Bearer $OK_KEY"
```

```json
{ "id": 9412, "participant_code": "OLM-2026-0042", "problem_number": 1,
  "version": 2, "status": "FINAL", "is_late": false,
  "submitted_at": "2026-11-30T21:58:03.412000Z" }
```

### 2.6. Statystyki — `read:stats`

`GET /api/v1/stats/` zwraca te same liczby, co publiczna strona `/statystyki/`: liczbę
uczestników, histogram punktów każdego zadania, średnią, medianę, maksimum, liczbę
zakwalifikowanych, próg i rozkład po województwach. Źródłem jest wyłącznie zamrożony snapshot
publikacji, więc statystyka nie pokaże niczego, czego nie widać w ogłoszonej tabeli. Odpowiedź
jest buforowana na 10 minut.

### 2.7. Dopisanie wydarzenia — `write:events`

Jedyny zapis w tym API. Dodaje pozycję do linii czasu edycji (gala, dzień otwarty, konferencja):

```bash
curl -s -X POST https://olimpiadakwantowa.pl/api/v1/events/ \
  -H "Authorization: Bearer $OK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Gala finałowa", "starts_on": "2027-05-20", "ends_on": "2027-05-20",
       "note": "Aula PW", "url": "https://example.org/gala", "show_on_timeline": true}'
```

`edition_id` jest wymagane tylko dla klucza **niezawężonego** do edycji; bez niego wydarzenie
trafia do edycji bieżącej. Reguły są te same, co w panelu koordynatora: tytuł jest obowiązkowy
(`EVENT_TITLE_REQUIRED`), koniec nie może być przed początkiem (`EVENT_RANGE_INVALID`),
a odnośnik musi być adresem `http(s)://…` albo ścieżką w serwisie (`EVENT_URL_INVALID`).

---

## 3. Webhooki

Zamiast odpytywać nas co minutę, system zewnętrzny może dostać powiadomienie w chwili zdarzenia.
Odbiorcę dodaje koordynator w panelu (**Ustawienia → Integracje**): adres `https://`, lista
zdarzeń i opcjonalne zawężenie do edycji. Sekret podpisu losuje serwis i pokazuje go przy
odbiorcy.

### 3.1. Zdarzenia

| Zdarzenie | Kiedy | Ładunek (`data`) |
|---|---|---|
| `results.published` | ogłoszono wyniki etapu | `publication_id`, `stage_id`, `edition_id`, `anonymization`, `qualified_only`, `rows`, `published_at` |
| `stage.closed` | etap zamknięty (deadline albo koordynator) | `stage_id`, `edition_id`, `closed_at`, `locked_submissions`, `manual` |
| `submission.received` | uczestnik oddał pracę | `submission_id`, `stage_id`, `edition_id`, `problem_number`, `participant_code`, `version`, `is_late`, `submitted_at` |
| `appeal.decided` | komisja rozstrzygnęła reklamację | `appeal_id`, `submission_id`, `stage_id`, `edition_id`, `participant_code`, `status`, `score_changed`, `decided_at` |
| `registration.created` | zarejestrował się uczestnik | `participant_code`, `edition_id`, `voivodeship`, `grade`, `created_at` |

Dodatkowo przycisk **„Wyślij test”** w panelu wysyła zdarzenie `ping` — tą samą drogą i z tym
samym podpisem, co zdarzenie prawdziwe. Nazwa jest świadomie spoza listy powyżej, żeby testu nie
dało się wziąć za prawdziwe ogłoszenie wyników.

W ładunkach **nie ma danych osobowych**: idą identyfikatory i kody publiczne. Kto ma prawo do
szczegółów, dopyta o nie API kluczem z odpowiednim zakresem.

### 3.2. Postać żądania

`POST` na wskazany adres, `Content-Type: application/json`, ciało:

```json
{
  "id": 8123,
  "event": "results.published",
  "created_at": "2027-01-15T18:00:00.512000+00:00",
  "data": { "stage_id": 12, "edition_id": 4, "rows": 341 }
}
```

Nagłówki:

| Nagłówek | Treść |
|---|---|
| `X-Olimpiada-Event` | nazwa zdarzenia (można kierować ruch bez parsowania JSON-a) |
| `X-Olimpiada-Delivery` | identyfikator doręczenia, równy `id` w ciele |
| `X-Olimpiada-Signature` | `t=<unix_ts>,v1=<hex>` — patrz niżej |

Odpowiedz **dowolnym kodem 2xx**, najlepiej od razu po zakolejkowaniu u siebie. Limit czasu po
naszej stronie to 10 sekund.

### 3.3. Weryfikacja podpisu

Podpisem jest HMAC-SHA256 z sekretu odbiorcy, liczony ze znacznika czasu **i** ciała naraz:

```
signed = f"{t}.".encode() + surowe_ciało_żądania
v1     = hmac_sha256(secret, signed).hexdigest()
```

Podpisuj i weryfikuj **surowe bajty** ciała, a nie JSON odtworzony z obiektu: przepisanie go
przez własny serializator zmienia kolejność kluczy i odstępy, czyli zmienia podpis.

Python (Flask):

```python
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300


def verify(raw_body: bytes, header: str, secret: str) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(","))
    timestamp, signature = parts["t"], parts["v1"]
    if abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
        return False  # powtórka nagranego żądania
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/hooks/olimpiada")
def hook():
    if not verify(request.get_data(), request.headers["X-Olimpiada-Signature"], SECRET):
        return "", 401
    enqueue(request.get_json())      # przetwarzaj u siebie, nie w tym żądaniu
    return "", 202
```

Node (Express):

```js
const crypto = require("crypto");
const TOLERANCE_SECONDS = 300;

function verify(rawBody, header, secret) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=")));
  const { t, v1 } = parts;
  if (Math.abs(Date.now() / 1000 - Number(t)) > TOLERANCE_SECONDS) return false;
  const expected = crypto
    .createHmac("sha256", secret)
    .update(Buffer.concat([Buffer.from(`${t}.`), rawBody]))
    .digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(v1));
}

// express.raw() – potrzebne są SUROWE bajty, express.json() je zjada
app.post("/hooks/olimpiada", express.raw({ type: "application/json" }), (req, res) => {
  if (!verify(req.body, req.get("X-Olimpiada-Signature"), SECRET)) return res.sendStatus(401);
  enqueue(JSON.parse(req.body.toString("utf8")));
  res.sendStatus(202);
});
```

### 3.4. Ponowienia, duplikaty i wygaszanie

- doręczenie ma **5 prób**: pierwszą natychmiast, kolejne po 60 s, 2 min, 4 min i 8 min,
- `id` doręczenia jest **stałe** między próbami, tak samo jak treść. Zapamiętaj przyjęte
  identyfikatory i traktuj powtórkę jako duplikat — po awarii sieci to samo zdarzenie potrafi
  dojść dwa razy,
- po **20 nieudanych doręczeniach pod rząd** odbiorca jest wygaszany. Koordynator widzi to
  w panelu, poprawia adres i włącza go z powrotem (licznik porażek startuje wtedy od zera),
- każde doręczenie zostaje w dzienniku razem ze stanem, liczbą prób i ostatnim błędem; z panelu
  da się je ponowić ręcznie.

### 3.5. Webhook przychodzący — potwierdzenia wpłat

Jedyny adres w całym API, pod który przychodzi ruch **do nas**. Służy dostawcy płatności
i istnieje wyłącznie w konkursach pobierających wpisowe; w Olimpiadzie Kwantowej, która jest
bezpłatna, odpowiada **404** — tak samo jak u dostawcy, którego organizator nie założył.

```
POST /api/v1/payments/<dostawca>/
Content-Type: application/json
X-Olimpiada-Signature: t=<unix_ts>,v1=<hex>
```

`<dostawca>` to identyfikator (slug) nadany przez organizatora w panelu; tam też powstaje **sekret
podpisu** — osobny dla każdej pary (konkurs, dostawca) — i tam się go wymienia. Wymiana działa
natychmiast: podpis złożony starym sekretem przestaje być ważny z chwilą zapisania nowego.

**Podpis jest ten sam, co w § 3.3**, tylko w drugą stronę: HMAC-SHA256 z sekretu, liczony ze
znacznika czasu **i surowych bajtów ciała** naraz, tolerancja 300 s. Podpisuj bajty, które
naprawdę wysyłasz — JSON odtworzony z obiektu ma inną kolejność kluczy, czyli inny podpis.

Ciało (wszystkie pola poza `reference` opcjonalne):

```json
{
  "reference": "OLM-WPIS-000123",
  "event_id": "evt_9f3c1a",
  "status": "paid",
  "amount": "49.99",
  "paid_at": "2027-03-01T10:15:00+01:00"
}
```

| Pole | Znaczenie |
|---|---|
| `reference` | **wymagane.** Identyfikator wpłaty, ten sam, który organizator przypisał należności przy zakładaniu płatności. Po nim idzie dopasowanie |
| `event_id` | identyfikator zdarzenia po Twojej stronie. Gdy jest, to on jest kluczem powtórki; gdy go nie ma, kluczem jest `reference` |
| `status` | gdy jest, musi brzmieć `paid`. Każda inna wartość zostaje zapisana jako zdarzenie **niebędące** potwierdzeniem wpłaty |
| `amount` | kwota **wyłącznie do porównania** z należnością. Nie jest księgowana; rozbieżność trafia do audytu organizatora |
| `paid_at` | chwila wpłaty w ISO 8601. Brak znaczy „teraz”. Data bez strefy jest czytana w strefie serwisu |

Pozostałe pola są **pomijane**: danych płatnika (imię, adres, numer rachunku) nie zapisujemy
i nie chcemy ich dostawać. Z ładunku zostaje w bazie wyłącznie jego skrót SHA-256.

Odpowiedzi:

| Kod | Ciało | Kiedy |
|---|---|---|
| `200` | `{"matched": true}` | wpłata zapisana przy należności |
| `200` | `{"matched": false}` | doręczenie przyjęte i zapisane, ale nie stało się wpłatą: nieznany `reference`, `status` inny niż `paid`, należność zwolniona albo umorzona. **Nie ponawiaj** — powód czeka na ekranie organizatora |
| `400` | `{"code": "INVALID_PAYLOAD", …}` | ciało nie jest obiektem JSON, brakuje `reference` albo `paid_at` nie jest datą ISO 8601. Ponowienie nie pomoże |
| `401` | `{"code": "INVALID_SIGNATURE", …}` | brak nagłówka, zły podpis albo podpis spoza okna 300 s. Treść jest ta sama dla wszystkich trzech |
| `404` | `{"code": "NOT_FOUND", …}` | ten konkurs nie pobiera wpisowego albo nie zna tego dostawcy |
| `429` | `{"code": "THROTTLED", …}` | przekroczony limit doręczeń na minutę. Ten adres nie ma klucza API, więc limit z § 5 go nie dotyczy: liczy się per adres nadawcy i wynosi 60/min. Uszanuj `Retry-After` |

Odpowiedź **nie mówi nic ponad `matched`** — ani czyja to należność, ani ile wynosi, ani dlaczego
się nie dopasowała. Powtórzone doręczenie tego samego klucza oddaje tę samą odpowiedź, nie tworzy
drugiej wpłaty i nie zmienia daty pierwszej.

---

## 4. Eksporty z panelu

Nie wszystko musi iść integracją. Koordynator ma w panelu (**Raporty → Eksport danych**) gotowe
pliki dla odbiorców zewnętrznych:

- **lista dla kuratorium** (CSV/XLSX, zawsze jedno województwo): kod, imię, nazwisko, szkoła,
  miejscowość, klasa, wynik, kwalifikacja (wynik ułamkowy w CSV z kropką – § 6.2),
- **protokół etapu** (PDF): tabela wyników z blokiem podpisów Komitetu Sterującego,
- **zrzut edycji** (JSON, `format: "olimpiada.edition.v1"`): struktura zawodów, etapy, zadania,
  wpisy uczestników pod kodami publicznymi i ogłoszone tabele — bez danych osobowych. To jest
  droga do migracji zawodów do innego systemu.

Każde pobranie zostaje w audycie razem z liczbą wierszy.

---

## 5. Limity żądań

Limit jest atrybutem **klucza**, nie serwisu: domyślnie 120 żądań na minutę, koordynator może go
zmienić przy konkretnym kluczu. Okno jest kalendarzową minutą. Po przekroczeniu:

```
HTTP 429 Too Many Requests
Retry-After: 37

{ "code": "THROTTLED", "detail": "…kiedy spróbować ponownie…" }
```

Uszanuj `Retry-After`. Jeśli limit jest dla Twojej integracji za niski, poproś organizatora
o jego podniesienie zamiast rozkładać żądania na kilka kluczy — klucze są rozliczane osobno,
ale obciążenie serwisu nie.

Praktyczne zalecenia: pobieraj przyrostowo (filtry `status`, `problem`, `voivodeship`), używaj
`page_size=500` zamiast pięciu razy `page_size=100` i — tam, gdzie się da — zastąp odpytywanie
webhookiem.

---

## 6. Wersjonowanie i zmiany

- numer wersji jest w **ścieżce**: `/api/v1/`. Da się go wpisać w konfigurację, wkleić do
  zgłoszenia i odczytać z logu proxy,
- w obrębie `v1` **nie zmienimy** znaczenia ani typu istniejącego pola, nie usuniemy pola i nie
  zwęzimy kształtu odpowiedzi,
- **dokładanie** pól do odpowiedzi, nowych zasobów, nowych filtrów i nowych zdarzeń webhooka jest
  zmianą zgodną wstecznie i następuje bez zapowiedzi. Twój klient ma ignorować nieznane pola
  i nieznane nazwy zdarzeń, a nie przewracać się na nich,
- zmiana łamiąca kontrakt to `/api/v2/` wystawione obok `v1`; o wycofaniu starej wersji
  organizator uprzedza z wyprzedzeniem i nie robi tego w trakcie trwającej edycji,
- nazwy zakresów i zdarzeń są częścią kontraktu — aktualną listę zawsze zwraca `GET /api/v1/`.

### 6.2. Punkty dziesiętne (od wydania `v0.35.0`)

Organizator może przełączyć etap w tryb **„dowolna wartość od min do max (co 0,01)”**: ocena może
wtedy mieć do dwóch miejsc po przecinku (np. 4,25), a zadanie – własne maksimum (np. 12,5). Kontrakt
dla **każdego** pola punktów – w `/api/v1/` (`points`, `total`, histogram i próg w statystykach),
w API panelu (`score`, `new_score`, `total_points`, `published_total`) i w zrzucie edycji JSON
(`total_points`, `max_points`, wiersze ogłoszonych tabel):

- **wyjście: liczba JSON, nie tekst.** Ocena całkowita jest liczbą całkowitą (`5` – dokładnie jak
  przed tym wydaniem), ułamkowa – liczbą z najwyżej dwoma miejscami po przecinku (`4.25`, `3.5`).
  Typ pola się nie zmienił (to nadal liczba), zmieniło się tylko to, że w etapie z dowolnymi
  wartościami bywa ułamkowa. Etap „tylko ze skali” – czyli każdy, którego organizator nie
  przełączył – dalej daje same liczby całkowite,
- **rachunek po stronie klienta:** czytaj te liczby jako dziesiętne (`Decimal` w Pythonie,
  `BigDecimal` w Javie, `decimal.js`/tekst w JavaScripcie), a nie przez arytmetykę `float` – suma
  `0.1 + 0.2` w liczbach binarnych nie daje `0.3`. Serwer liczy wszystko w dziesiętnych: sumy są
  dokładne, a suma **ważona** jest zaokrąglana raz, na końcu, **połówka w górę** – do 0,01 w etapie
  z dowolnymi wartościami i do pełnego punktu w etapie „tylko ze skali”,
- **wejście** (pola `score`/`new_score` w API panelu): liczba JSON (`4.25`) albo tekst z kropką
  lub przecinkiem (`"4.25"`, `"4,25"`). Odmowy – zawsze `400` z kodem:
  - `SCORE_INVALID` – to nie jest liczba albo ma więcej niż dwa miejsca po przecinku (`4.255`
    nie jest zaokrąglane, tylko odrzucane),
  - `SCORE_NOT_IN_SCALE` – liczba spoza skali (etap „tylko ze skali”; `4.5` przy skali 0/2/5/6)
    albo spoza zakresu zadania (etap z dowolnymi wartościami; `6.5` przy maksimum 6),
- ocena w API panelu jest w **postaci przechowywanej** – przesuniętej o przesunięcie skali, gdy
  konkurs ma punkty ujemne (`weighted_scoring`); przy skali bez punktów ujemnych to ta sama liczba,
  którą widzi recenzent,
- **rubryka** (`rubric` w `POST …/reviews/{id}/submit/`, `…/revise/` i `PATCH` szkicu; od wersji po
  `v0.35.0`): w etapie z dowolnymi wartościami `points` pozycji rubryki przyjmuje to samo, co `score`
  – liczbę JSON albo tekst z kropką lub przecinkiem, od 0 do maksimum kryterium (bywa ułamkowe),
  najwyżej dwa miejsca po przecinku; w odpowiedzi `rubric[].points` jest liczbą JSON (`2`, `1.75`).
  W etapie „tylko ze skali” – jak dotąd – wyłącznie liczba całkowita. Odmowy: `400 INVALID_RUBRIC`
  (kształt), `400 RUBRIC_POINTS_OUT_OF_RANGE` (poza 0–maksimum kryterium), `400
  RUBRIC_TOTAL_NOT_IN_SCALE` (suma poza skalą albo zakresem zadania),
- powrót etapu z trybu dowolnego do „tylko ze skali” przy istniejących ocenach spoza skali albo
  kryteriach rubryk z ułamkowym maksimum: `409 FREE_VALUES_IN_USE`,
- **eksporty CSV** z panelu (lista dla kuratorium, wyniki i recenzje etapu) zapisują punkty z
  **kropką** dziesiętną (`4.25`) niezależnie od języka interfejsu; XLSX niesie je jako liczby.

### 6.3. Punkty uczestnika dopiero po ogłoszeniu wyników (od wydania `v0.38.7`)

Decyzja właściciela platformy: uczeń widzi oceny dopiero po ostatecznym zatwierdzeniu, czyli po
**ogłoszeniu wyników etapu** (`Stage.results_published_at` – ten sam znacznik, który rozstrzyga
w panelu WWW i w `GET /api/me/results/`; zdjęcie go przez koordynatora, czyli wycofanie ogłoszenia,
znów chowa punkty). Etap treningowy **nie** ma wyjątku – panel też go nie robi. Dotyczy API panelu
uczestnika; klucze odpowiedzi się nie zmieniają, zmieniają się wartości:

| Pole | Przed ogłoszeniem | Po ogłoszeniu |
|---|---|---|
| `GET /api/me/submissions/` (i odpowiedź `201` uploadu) → `final_grade.score` | `null` | punkty, jak dotąd |
| `final_grade.decided_at` | `null` | data decyzji, jak dotąd |
| `final_grade.method` | `"REVIEW"` albo `"APPEAL"` | `"REVIEW"` albo `"APPEAL"` |
| `final_grade.rationale` | uzasadnienie komisji odwoławczej przy `"APPEAL"`, inaczej `null` | bez zmian |
| `appeal.new_score` | `null` | punkty po reklamacji |
| `GET /api/me/appeals/` → `decision.new_score` | `null` | punkty po reklamacji |
| `appeal.status`, `appeal.justification`, `appeal.decided_at` | jak dotąd (to samo pokazuje zakładka „Reklamacje”) | jak dotąd |
| `GET /api/competitions/me/entries/` → `total_points` | `null` (kolumnę zapisuje już podgląd wyników koordynatora) | suma etapu, jak dotąd |
| `… /me/entries/` → `status` | bez zmian – `QUALIFIED`/`NOT_QUALIFIED` nadaje dopiero publikacja | bez zmian |

`final_grade.method` nie oddaje już wewnętrznych trybów (`CONSENSUS`, `THIRD_REVIEW`, `MODERATION`,
`OVERRIDE`) – mówiły o przebiegu oceniania (rozbieżność recenzentów), a nie o pracy. Klient, który
na nich rozgałęział logikę, dostaje jedną wartość `"REVIEW"`; `"APPEAL"` zostaje, bo przy nim
`rationale` jest tekstem pisanym do uczestnika. Obecność `final_grade` (obiekt zamiast `null`)
nadal mówi „praca oceniona” – to samo, co status `GRADED_PROVISIONAL`/`FINAL` i ścieżka statusu
w panelu.

### 6.1. Rejestracja uczestnika (`POST /api/auth/register/participant/`)

To jest API **konta**, a nie integracji — opisujemy je tutaj, bo od wydania `v0.30.0` zmienił się
w nim kształt jednego pola i klient sprzed tej zmiany ma dalej działać bez poprawki.

| Pole | Typ | Uwagi |
|---|---|---|
| `birth_date` | `string` (`RRRR-MM-DD`) | **zalecane.** Pełna data urodzenia. Z niej liczy się
  pełnoletność i wymagalność `guardian_consent`, a także `birth_year` w odpowiedziach |
| `birth_year` | `integer` | zostaje **wyłącznie** dla zgodności wstecznej. Bez `birth_date` wiek
  rozstrzyga się starą, zachowawczą regułą (rok bieżący − rocznik ≤ 18 ⇒ osoba niepełnoletnia) |

Zasady:

- podaj **jedno z dwóch**. Brak obu to `400 BIRTH_DATE_REQUIRED` (chyba że organizator wyłączył
  pytanie o wiek — wtedy uczestnik bez daty jest traktowany jak osoba niepełnoletnia),
- gdy przyślesz oba, rozstrzyga `birth_date` i to z niej wyliczamy `birth_year`. Nie odrzucamy
  żądania, w którym się rozjeżdżają — zapisujemy wartość dokładniejszą,
- zakres: od `1900-01-01` do dnia dzisiejszego włącznie. Poza nim `400 BIRTH_DATE_INVALID`,
- **pełnoletni = ma już za sobą dzień osiemnastych urodzin** (data lokalna Europe/Warsaw;
  urodzony 29 lutego staje się pełnoletni 1 marca). Osoba niepełnoletnia bez
  `guardian_consent: true` dostaje `400 CONSENT_REQUIRED` i **nie powstaje ani konto, ani profil**,
- `PATCH /api/auth/me/` przyjmuje `birth_date` tą samą drogą; `GET /api/auth/me/` zwraca obie
  wartości, a `birth_date` bywa `null` w profilach założonych przed `v0.30.0`.

### 6.1a. CAPTCHA w rejestracji (od wydania `v0.38.5`) — **zmiana niezgodna wstecz**

`POST /api/auth/register/participant/` i `POST /api/auth/register/committee/` wymagają pary
CAPTCHY — tej samej, którą rozwiązuje człowiek w formularzu `/register/`. Do tego wydania JSON
omijał wszystkie warstwy antyspamowe formularza, a każde wywołanie wysyłało link aktywacyjny na
dowolny adres. Klient, który rejestruje konta, musi pokazać człowiekowi obrazek.

1. `GET /captcha/refresh/` z nagłówkiem `X-Requested-With: XMLHttpRequest` (bez niego `404`) zwraca
   `{"key": "<klucz>", "image_url": "/captcha/image/<klucz>/", "audio_url": null}`,
2. człowiek wpisuje wynik działania z obrazka `image_url` (wyzwanie arytmetyczne, np. `3 × 5 =`),
3. do JSON-a rejestracji dochodzą dwa pola:

| Pole | Typ | Uwagi |
|---|---|---|
| `captcha_key` | `string` | `key` z kroku 1 |
| `captcha_value` | `string` | wynik wpisany przez człowieka |

Zasady:

- wyzwanie żyje **10 minut** i jest **jednorazowe**: każda próba je kasuje, także nieudana. Po
  odmowie pobierz nowe wyzwanie — ponowienie z tą samą parą zawsze skończy się tym samym błędem,
- zła, wygasła albo użyta para: `400 CAPTCHA_INVALID`. Brak któregoś z pól: `400` z błędem pola
  (`captcha_key` / `captcha_value`),
- błąd kształtu pozostałych danych (np. brak szkoły) zgłaszany na poziomie serializera **nie**
  zużywa wyzwania; reguły serwisu (`EMAIL_TAKEN`, `CONSENT_REQUIRED`, `WEAK_PASSWORD` …) sprawdzane
  są **po** CAPTCHY, więc wymagają nowej pary,
- limit `register` (10/h z adresu IP, § 5) obowiązuje jak dotąd — CAPTCHA podnosi koszt jednego
  zgłoszenia, limit ogranicza ich liczbę.

### 6.1b. Logowanie `POST /api/auth/login/` — wyłącznie JSON (od wydania `v0.38.5`)

Ciało żądania musi mieć `Content-Type: application/json` (`{"email": …, "password": …}`). Inny typ
treści — `application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain` — dostaje
`415 UNSUPPORTED_MEDIA_TYPE`, zanim hasło zostanie sprawdzone. Powód: endpoint ustawia ciasteczko
sesji, a te trzy typy wysyła zwykły formularz z obcej strony bez wiedzy użytkownika (login CSRF —
zalogowanie przeglądarki ofiary na konto napastnika). JSON z obcego pochodzenia wymaga preflightu
CORS, którego serwis nie przepuszcza. Odpowiedź (`{"token": …}`) i kody błędów bez zmian.

---

## 7. Kontakt

Sprawy integracji prowadzi organizator: formularz `/support/new/` w serwisie albo adres podany
w stopce. Do zgłoszenia dołącz **przedrostek** klucza (osiem znaków, np. `7f3a9c21`), nigdy sam
klucz, oraz identyfikator doręczenia (`X-Olimpiada-Delivery`), jeśli sprawa dotyczy webhooka.

---

## 8. API wewnętrzne serwisu na django CMS (`/internal/djcms/v2/`)

**Nie jest to API integracji** i nie dostanie go żaden system zewnętrzny – ta sekcja jest dla
utrzymującego platformę. Z tego API korzysta wyłącznie serwis publiczny na django CMS (kontener
`djcms`, docs/OPERACJE.md § 22): pobiera listę konkursów platformy (rozstrzyganie hostów), dane
zawodów, których nie ma w swojej bazie (terminy, stany etapów, zadania, wyniki, warsztaty,
partnerzy, komunikaty, rama serwisu), oraz paczkę treści Wagtaila do importu. Pełny kontrakt:
[`docs/tasks/DJ-02.md`](tasks/DJ-02.md) § 4 i § 7 (kształty obiektów wspólnych z DJ-01:
[`docs/tasks/DJ-01.md`](tasks/DJ-01.md) § 3). Kod: `backend/apps/cms/djcms_api/`, klient:
`djcms/apps/live/client.py`.

### 8.1. Dostęp – każda porażka to pusta 404

Adresy `http://web:8000/internal/djcms/v2/…`, osiągalne wyłącznie z sieci compose'a. Bramki,
sprawdzane w tej kolejności (`apps.cms.djcms_api.auth`):

1. **host wewnętrzny** (`web`, `localhost`, `127.0.0.1`) – ta sama reguła co `/internal/tls-allowed`;
   z domeny publicznej adres nie istnieje także z poprawnym tokenem. Caddy dodatkowo odpowiada 404
   na `/internal/*` w każdym bloku publicznym, gdy `DJCMS_ENABLED=1`;
2. **token włączony**: `DJCMS_INTERNAL_TOKEN` w `.env` ma co najmniej 32 znaki (krótszy albo pusty
   wyłącza API w całości; `manage.py check` zgłasza wtedy `cms.W010`);
3. nagłówek **`X-Djcms-Token`** równy tokenowi (porównanie w czasie stałym);
4. metoda **`GET`** – inna też daje 404, a nie 405 (405 zdradzałoby, że adres istnieje).

Odpowiedź przy każdej porażce jest ta sama: `404` bez treści. Tak samo odpowiada każdy nieznany
adres gałęzi `/internal/djcms/…` (bez przekierowania na adres z ukośnikiem) – także slug o złym
kształcie (inny niż `[a-z0-9-]{1,50}`) i dawne adresy `/internal/djcms/v1/…` (§ 8.5).

### 8.2. `GET competitions` – konkursy platformy

Lista **wszystkich** konkursów, także nieaktywnych (`is_active: false` – djcms wygasza wtedy ich
hosty), posortowana po slugu, z danymi potrzebnymi do rozstrzygania hosta tą samą regułą co
`apps.tenancy.resolution.resolve_for_request` (wspólne wektory:
`backend/djcms_contract/resolution_cases.json`, testowane w obu projektach):

```jsonc
{"api_version": 2, "generated_at": "…",
 "platform": {"site_domain": "olimpiadakwantowa.pl", "platform_subdomains": false, "default_slug": "kwantowa"},
 "competitions": [
   {"slug": "kwantowa", "name": "Olimpiada Kwantowa", "short_name": "OK",
    "is_active": true, "is_default": true,
    "routing_mode": "DOMAIN",                // "DOMAIN" | "PATH"
    "path_prefix": "",                       // niepusty tylko przy PATH
    "hosts": ["olimpiadakwantowa.pl"],       // hostname witryny ∪ primary_domain (∪ SITE_DOMAIN dla konkursu
                                             // domyślnego); małe litery, bez portu, rozłączne między konkursami
    "hosts_path_prefixes": true,             // bramka path_prefix_routing konkursu-gospodarza
    "public_base": {"origin": "https://olimpiadakwantowa.pl", "path_prefix": ""},   // albo null
    "site_hostname": "olimpiadakwantowa.pl",
    "has_site_aliases": false,
    "linked_paths": ["/dokumenty/regulamin/", "/faq/", "/harmonogram/", "/warsztaty/"],
    "fingerprint": "<sha256 kanonicznego JSON-u pól powyżej>"}
 ]}
```

**`linked_paths`** – ścieżki stron (względem korzenia witryny konkursu), do których linkuje
**aplikacja**; djcms sprawdza, że pod każdą stoi opublikowana strona (`dj_pages.W003`,
`verify_cutover`). Źródła i reguła:

- strony dokumentów zgód konkursu (`consent_set`) – ścieżka strony dokumentu w drzewie konkursu,
- strona warsztatów i `/warsztaty/` (pasek osi czasu) – gdy konkurs ma stronę warsztatów,
- literały z szablonów aplikacji (`APP_LITERAL_PAGE_PATHS`: `/dokumenty/rodo/`, `/faq/`,
  `/harmonogram/`, `/warsztaty/`) – **poza** konkursem pod prefiksem ścieżki (literał `/faq/` pod
  `/<prefiks>/…` prowadzi do konkursu-gospodarza, więc wymienia go lista gospodarza). Test
  przeszukuje szablony i pada przy literale spoza listy.

**Dokumenty i literały trafiają na listę tylko wtedy, gdy stoi pod nimi opublikowana i publiczna
strona Wagtaila.** Adres bez strony (albo ze stroną nieopublikowaną lub z ograniczonym dostępem,
której eksport nie przenosi) daje dziś 404 w Wagtailu i tak samo odpowie w djcms, więc nie blokuje
przełączenia – np. konkurs założony z szablonu nie ma stron `/faq/` ani `/harmonogram/`.

`fingerprint` pozwala djcms nie zapisywać niczego, gdy konkurs się nie zmienił. Bez danych
osobowych i bez adresów e-mail organizatora.

### 8.3. `GET c/<slug>/<endpoint>` – dane jednego konkursu

| Endpoint | Co oddaje |
|---|---|
| `chrome` | rama serwisu: `competition` (`slug`, `name`, `short_name`, `accent_colour`, `logo {src,width,height}`\|`null`, `favicon {src}`\|`null`), `site` (dane `SiteSettings` + `ga_measurement_id`), edycja, stan rejestracji, odnośniki do aplikacji (logowanie, rejestracja, pomoc, plakaty), komunikaty, slider sponsorów, pasek osi czasu, `seo` (`og_image`, `default_description`) |
| `stages` | edycja, etap „na teraz” i wiersze osi czasu (stany etapów, czy są wyniki) |
| `problems` | zadania etapu bieżącego i treningowego – **pusta lista przed `opens_at`** (ani tytułu, ani adresu PDF) |
| `results` | ogłoszone tabele wyników bieżącej edycji i odnośniki archiwalne |
| `editions` | edycje konkursu (lista wyboru archiwum) |
| `editions/<id>/results` | odnośniki do ogłoszonych tabel jednej edycji; edycja innego konkursu = pusta lista |
| `workshops` | najbliższe warsztaty (≤ 3), wiersze z datą, zapowiedź materiałów (liczba + odnośnik do logowania) oraz – na żywo ze strony „Warsztaty” Wagtaila – `page` (tytuł, wprowadzenie) i `schedules` (pełne tabele w kolejności strony); strona z ograniczonym dostępem = `page: null`, `schedules: []` |
| `partners` | partnerzy ze strony `PartnersPage` konkursu (`live().public()`): `page_path`, `levels` (kolejność poziomów), `partners` (`name`, `level`, `logo`, `url` tylko `http(s)`, `description`, `initials`, `is_wide`) w kolejności strony, `page` (wprowadzenie, zaproszenie do współpracy) albo `null` |
| `export` | paczka treści Wagtaila (`application/zip`, `olimpiada-cms-bundle` **v2**: strony `live()` i publiczne, obrazy, adresy dokumentów + `competition`, `data_pages`, `redirects`) |

Każda odpowiedź JSON ma `api_version` (`2`) i `generated_at` (ISO 8601 z przesunięciem),
nagłówki `Cache-Control: no-store` i `X-Content-Type-Options: nosniff`. Buforuje klient
(60 s świeżo, do 600 s kopia awaryjna przy niedostępnym `web`), nie aplikacja. Każdy widok liczy
dane w kontekście **tego** konkursu i w języku polskim; dwa konkursy nie widzą nawzajem swoich
danych (test S1: żadna odpowiedź `c/A/*` nie zawiera napisu konkursu B).

Adresy aplikacji w odpowiedziach (`/results/5/`, `/register/`) są bezwzględne pod adresem
**tego** konkursu (`public_base` z § 8.2: domena konkursu albo host platformy + `/<prefiks>`;
schemat i port z `DJCMS_MAIN_PUBLIC_URL`, który opisuje konkurs domeny głównej). Statyki
i media – bez prefiksu. Ścieżki stron Wagtaila (`/warsztaty/`) zostają względne, bo po imporcie
istnieją też w djcms; odnośnik o schemacie innym niż `http(s)` staje się pustym napisem.

Błędy **po** bramkach (JSON):

| Kod | `error` | Znaczenie |
|---|---|---|
| 404 | `no-competition` | nie ma konkursu o tym slugu albo jest nieaktywny |
| 503 | `no-public-url` | konkurs nie ma adresu w aplikacji głównej (ani domeny, ani prefiksu z otwartą bramką `path_prefix_routing`) |

djcms pokazuje wtedy komunikat o niedostępności sekcji żywych – nigdy danych innego konkursu.

### 8.4. Dane osobowe – biała lista

Żaden endpoint nie czyta kont ani prac: w odpowiedziach nie ma e-maili, imion, szkół,
identyfikatorów uczestników, ocen ani wpisów do etapów. Wiersz tabeli wyników przechodzi przez
**białą listę** kluczy (`rank`, `display`, `points`, `points_display`, `total`, `total_display`,
`qualified`, `manual`, a gdy są w opublikowanym snapshotcie – `district`, `category`); `display`
to ta sama etykieta, która stoi w publicznej tabeli wyników (kod, inicjały albo nazwisko w finale).
Czegokolwiek spoza listy serializator nie zna, więc nie ma jak tego oddać.

### 8.5. Wersjonowanie

Dopisanie pola nie zmienia wersji. Każda inna zmiana kształtu podnosi `api_version` **i** prefiks
adresu; klient odrzuca odpowiedź z inną wersją niż oczekiwana (sekcje żywe djcms przechodzą wtedy
w tryb degradacji, strona odpowiada 200 z `X-Djcms-Degraded: 1`).

**v1 usunięte (DJ-02k).** API v1 z DJ-01 (`/internal/djcms/v1/{chrome,…,export}` dla jednego
konkursu wybieranego ustawieniem `DJCMS_COMPETITION_SLUG`, paczka v1) odpowiada teraz tą samą
pustą 404 co każdy nieznany adres; `DJCMS_COMPETITION_SLUG` nie jest już czytane. Ciała endpointów
i ich reguły jawności są te same – v2 dołożyło konkurs w ścieżce i pola opisane w § 8.3.
`manage.py export_cms_bundle [--competition SLUG] --output PATH|-` buduje zawsze paczkę v2 (bez
`--competition` – aktywny konkurs witryny domyślnej). Importer djcms (`import_cms_bundle`) przyjmuje
paczki v1 i v2 (kopie sprzed DJ-02).

### 8.6. Token SSO redaktorów (`/cms/` → `/djcms/sso/`)

Nie jest to endpoint API, ale drugi kontrakt między `web` i `djcms` (DJ-02 D6, S11): redaktor
przechodzi z `/cms/` do django CMS jednorazowym tokenem. Wystawia go `web`
(`backend/apps/cms/djcms_sso.py`, widok `/cms/django-cms/` – tylko po `POST` z CSRF, dla konta
z `wagtailadmin.access_admin`), weryfikuje djcms (`djcms/apps/sites/sso.py`). Token jedzie
w polu `token` formularza wysyłanego `POST`-em na `/djcms/sso/` **tego samego** hosta i prefiksu –
nigdy w adresie ani w logu.

```
v1.<B>.<S>
B = base64url(JSON bez dopełnienia „=”)
S = base64url(HMAC-SHA256(DJCMS_SSO_KEY, "olimpiada/djcms-sso/v1." + B))
```

JSON – klucze posortowane, bez odstępów, ASCII:

| Pole | Znaczenie |
|---|---|
| `v` | `1` |
| `aud` / `iss` | `"djcms"` / `"web"` |
| `sub` | id konta w aplikacji głównej (konto w djcms: `web:<sub>`) |
| `email`, `first_name`, `last_name` | dane konta (imię i nazwisko najwyżej 150 znaków) |
| `host` | host żądania – małe litery, bez portu i kropki końcowej |
| `platform` | `true` = wszystkie konkursy (grupa `redakcja:platforma`) |
| `competitions` | `[{"slug", "abilities": ["edit"] \| ["edit", "publish"]}]` – konkursy, których korzeń drzewa stron konto może edytować w `/cms/` (`publish` – także publikować) |
| `nonce` | losowy, jednorazowy (`[A-Za-z0-9_-]{16,64}`) |
| `iat` / `exp` | sekundy epoki; `exp - iat` = 60 |

djcms odrzuca (403, bez logowania) token z innym podpisem, wersją, odbiorcą albo wystawcą, dla
innego hosta, przeterminowany, z `iat` z przyszłości (tolerancja 5 s), z ważnością ponad 60 s,
z użytym już `nonce`, z nieznaną umiejętnością albo wpisem `competitions` innego kształtu, a także
żądanie inne niż `POST` albo z `Origin` różnym od `<schemat>://<host>` żądania. Token nigdy nie niesie roli
superużytkownika. Grupy konta są **zastępowane** listą z tokenu przy każdym wejściu.
Klucz: `DJCMS_SSO_KEY`, ten sam w obu projektach, ≥ 32 znaki, różny od pozostałych sekretów
(`cms.W013`, `dj_sites.W001`); pusty = przejście wyłączone.

Wspólny wektor testowy obu projektów: `backend/djcms_contract/sso_token_cases.json` (klucz
wyłącznie testowy, `now`, dane konta i gotowe tokeny) – `web` sprawdza, że wystawia dokładnie te
tokeny (`test_djcms_sso.py::test_contract_vector_matches_issue_token`), djcms – że je przyjmuje
co do pola i odrzuca dla innego hosta i po terminie (`test_sso.py::test_contract_vector`). Zmiana
formatu = nowy prefiks (`v2.`) i nowy kontekst podpisu w obu projektach naraz.
