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
| `results.published` | ogłoszono wyniki etapu | `publication_id`, `stage_id`, `edition_id`, `anonymization`, `rows`, `published_at` |
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

---

## 7. Kontakt

Sprawy integracji prowadzi organizator: formularz `/support/new/` w serwisie albo adres podany
w stopce. Do zgłoszenia dołącz **przedrostek** klucza (osiem znaków, np. `7f3a9c21`), nigdy sam
klucz, oraz identyfikator doręczenia (`X-Olimpiada-Delivery`), jeśli sprawa dotyczy webhooka.
