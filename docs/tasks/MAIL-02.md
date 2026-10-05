# MAIL-02: Literówki w adresach e-mail i śledzenie odbić (bounce)

## 0. Cel i granice

Z logu relaya `mail` (5.10.2026): `kcadera@o2.plo` – odrzucony przez sam relay („Domain not found”),
kilka adresów `…@gmail.com` – 550 „user not found” od Google. Konto z takim adresem nigdy nie dostaje
linku aktywacyjnego ani żadnego listu, a uczestnik nie wie dlaczego. Zadanie ma dwie części:

1. **zapobiec** – podpowiedź „Czy chodziło Ci o …?” przy wpisywaniu adresu i twarda blokada domen,
   które na pewno nie przyjmują poczty,
2. **wykryć to, co przeszło** – zapis odbić z relaya, baner dla właściciela adresu, lista dla
   koordynatora i wstrzymanie listów nieobowiązkowych na adres, który twardo odbił.

Czego zadanie **nie** robi:
- nie dodaje zależności Pythona (DNS: istniejący klient `apps.mail_domains.dnsquery` na bibliotece
  standardowej; DSN: moduł `email` biblioteki standardowej),
- nie zmienia obrazu ani wersji usługi `mail`, nie otwiera portu 25 na świat i nie rusza restrykcji
  OPS-02 (`mynetworks`, `smtpd_sender_restrictions`, GlitchTip tylko jako `glitchtip@`),
- nie daje workerowi dostępu do gniazda Dockera (odrzucone: czytanie `docker compose logs` – § 2.1),
- nie weryfikuje skrzynki rozmową SMTP (`RCPT TO` bez wysyłki) – duzi dostawcy traktują to jak
  zbieranie adresów, a relay jest jedynym naszym adresem IP do wysyłki,
- nie blokuje adresu z powodu podpowiedzi – podpowiedź da się zawsze odrzucić jednym kliknięciem.

Nowa aplikacja `apps.email_delivery` (INSTALLED_APPS jednym wierszem). Zmiany w aplikacjach
wspólnych są minimalne: pole formularza w kilku formularzach, `essential=False` w kilku wywołaniach
`queue_mail`, jeden znacznik w `base.html`, sekcja eksportu danych, wiersz rejestru.

## 1. Literówki w domenie (formularze)

### 1.1. Algorytm (`apps.email_delivery.typos.suggest`)
- Wejście: adres po walidacji składni Django. Część lokalna **bez zmian** (także `jan+olimp@…`,
  wielkość liter), porównujemy wyłącznie domenę (małe litery, bez kropki końcowej, IDN w postaci
  Unicode – `xn--…` dekodowane przed porównaniem).
- Domena z listy znanych dostawców (`KNOWN_DOMAINS`: polskie – gmail, wp, o2, onet + aliasy Onetu,
  interia + aliasy, op, gazeta, tlen, poczta.fm, vp, go2, …; światowe dla IQO – outlook/hotmail/live/
  msn, yahoo + krajowe, icloud/me/mac, aol, proton, gmx, web.de, mail.ru i aliasy, yandex, qq, 163,
  126, sina, foxmail, naver, daum/hanmail, rediffmail, orange.fr, free.fr, libero.it, seznam.cz,
  ukr.net, uol/bol.com.br, …) → brak podpowiedzi.
- Inaczej najbliższa znana domena w odległości Damerau-Levenshteina (OSA) ≤ 1 dla domen do 6 znaków
  i ≤ 2 dla dłuższych (`gmial.com`, `gamil.com`, `o2.plo`, `wp.p`, `outlok.com`, `yaho.com`). Remis –
  pierwsza na liście (lista jest w kolejności popularności).
- Inaczej literówka w TLD z jawnej mapy (`TLD_TYPOS`: `.plo`, `.pll`, `.lp`, `.ppl`, `.con`, `.cm`,
  `.cmo`, `.ocm`, `.cpm`, `.comm`, `.coom`, `.vom`, `.xom`, `.om`, `.nte`, `.ner`, `.ogr`, …) → ta
  sama domena z poprawnym TLD (`firma.con` → `firma.com`). Mapa jest jawna, a nie „najbliższy TLD”,
  bo `.cm`, `.co`, `.om` to prawdziwe domeny krajowe – na liście jest tylko to, co w praktyce jest
  pomyłką; podpowiedź i tak jest tylko pytaniem.
- Wynik: pełny adres z poprawioną domeną (IDN zostaje w Unicode) albo `None`.

### 1.2. Twarda blokada (`apps.email_delivery.dnscheck.domain_accepts_mail`)
- Domena **nie przyjmuje poczty**, gdy nie ma rekordu MX ani A (NXDOMAIN albo pusta odpowiedź na
  oba pytania) albo ma „null MX” (RFC 7505: `MX 0 .`). Tylko wtedy formularz odrzuca adres.
- Kolejność pytań: MX (wystarczy jeden rekord), potem A. Klient `dnsquery.Resolver` z serwerami
  z `/etc/resolv.conf`, limit 1,5 s na pytanie, jedna próba. IDN → `idna` biblioteki standardowej.
- **Fail-open:** błąd DNS, przekroczony czas, niepoprawna nazwa IDN = „nie wiadomo” = adres
  przechodzi. Lepiej przepuścić literówkę (złapie ją § 2) niż zablokować rejestrację przy awarii DNS.
- Bez zapytań: domeny z `KNOWN_DOMAINS` (wiadomo, że przyjmują), domeny zarezerwowane RFC 2606/6761
  (`.test`, `.example`, `.invalid`, `.localhost`, `.local`, `example.com/net/org`) i wyłączony
  przełącznik `EMAIL_DOMAIN_DNS_CHECK` (domyślnie włączony; `config.settings.test` – wyłączony).
- Pamięć podręczna (cache Django): „przyjmuje” 24 h, „nie przyjmuje” 1 h, „nie wiadomo” 5 min
  (żeby awaria DNS nie kosztowała 3 s na każde wysłanie formularza).
- **Kiedy pytamy** (przegląd PR #98, L3): dopiero gdy **cała** pozostała walidacja formularza przeszła
  (pola, CAPTCHA, pułapka i czas antyspamu, hasła – `fields.EmailDomainCheckMixin._post_clean`).
  Najwyżej 4 pytania naraz w procesie (`threading.BoundedSemaphore`); brak miejsca = „nie wiadomo”.
  Limity żądań widoków (`register`, `password_reset` per konto …) bez zmian.

### 1.3. Pole formularza (`apps.email_delivery.fields.CheckedEmailField`)
Podklasa `forms.EmailField` z własnym widżetem. Kolejność w `clean`:
1. składnia (jak dotąd), 2. twarda blokada § 1.2 – błąd „Domena … nie istnieje albo nie przyjmuje
poczty”, 3. podpowiedź § 1.1 – **jeden raz**: formularz wraca z komunikatem „Sprawdź adres – czy
chodziło Ci o x@gmail.com?” i dwiema czynnościami:
- **„Użyj adresu x@gmail.com”** – pole wyboru `<pole>__accept` (zaznaczone podmienia wartość przy
  następnym wysłaniu; z JS – od razu w polu). Pole wyboru, a nie przycisk wysyłki: przycisk stałby
  w DOM przed przyciskiem formularza i Enter w dowolnym polu po cichu przyjmowałby poprawkę,
- **„Zostaw mój adres”** – ponowne wysłanie tego samego adresu przechodzi (ukryte pole
  `<pole>__keep` z adresem, którego dotyczyła podpowiedź; inny adres = nowe sprawdzenie).

Dlaczego jedna blokada zamiast „zapisz i ostrzeż”: przy rejestracji błędny adres to konto, którego
nie da się aktywować – ostrzeżenie po zapisie przychodzi za późno. Jedno pytanie, na które zawsze
można odpowiedzieć „zostaw”, nie odbiera nikomu prawa do nietypowego adresu.

### 1.4. Podpowiedź w przeglądarce (`email_delivery/email-check.js`)
Plik statyczny z `nonce` w `base.html` (jak `table-scroll.js`; bez inline JS – CSP). Na `blur`
pola z `data-email-check` liczy tę samą podpowiedź (lista domen i mapa TLD przychodzą z serwera
w atrybutach `data-*` – jedno źródło prawdy, test pilnuje zgodności algorytmu na wspólnych
przypadkach) i pokazuje `role="status"` z przyciskiem „Użyj …”. Nieblokująco: nic nie wysyła, nie
wstrzymuje formularza. Obsługuje też przycisk z § 1.3 (podmiana wartości bez wysyłki).

### 1.5. Formularze
Rejestracja uczestnika, rejestracja komitetu, rejestracja opiekuna szkolnego, zmiana adresu
(`new_email`), konto w panelu koordynatora, zaproszenie opiekuna drużyny (DEL-01 – adres, na który
idzie zaproszenie; ekran przyjęcia zaproszenia **nie ma** pola adresu, adres pochodzi z zaproszenia),
uczeń dodawany przez opiekuna drużyny (adres ucznia i rodzica). Adres rodzica w panelu uczestnika
(`/me/guardian/`) – wyłącznie twarda blokada (`suggest_typos=False`): widok jest akcją POST
z przekierowaniem, nie rysuje pola ponownie, więc pytanie bez „zostaw” zablokowałoby adres na zawsze.
Pozostałe pola adresowe (listy wklejane hurtem, import CSV) – bez zmian.

## 2. Odbicia (bounce)

### 2.1. Skąd wiemy o niedoręczeniu – decyzja
Dwie drogi, obie bez czytania logów:

**(a) Odmowa w trakcie wysyłki (relay).** Relay sam odrzuca adres z nieistniejącą domeną
(`reject_unknown_recipient_domain` w obrazie). Aplikacja dostaje to synchronicznie w
`SMTPRecipientsRefused`. Backend `apps.email_delivery.backends.TrackingSMTPBackend` (podklasa
backendu SMTP Django, podstawiana w `MAILERS` przy `EMAIL_BOUNCE_TRACKING`) zapisuje odmowę;
wyjątek jest połykany (bez trzech ponowień, wynik 0 i ostrzeżenie w logu) **tylko** wtedy, gdy
odmowa każdego odbiorcy jest twarda wg tablicy § 2.2. Każda inna (4xx, `554 5.7.1` – polityka relaya,
błąd konfiguracji) leci dalej jak dotąd: ponowienia, log workera, GlitchTip – i **nie** jest
zapisywana jako odbicie adresu (przegląd PR #98, M1). `unknown_address_reject_code` relaya zostaje
domyślny (450): chwilowy NXDOMAIN nie może trwale zgubić listu obowiązkowego (L5), a domeny bez MX/A
łapie formularz (§ 1.2). Odmowa `450 4.1.2 … Domain not found` liczy się jako odbicie miękkie.

**(b) Zawiadomienie o niedoręczeniu (DSN) od relaya.** Po przyjęciu listu relay doręcza go do MX-a
odbiorcy; 550 od Google'a = DSN na `noreply@<domena>`. MAIL-01 kieruje te adresy na `discard`.
Nowa wartość `MAIL_BOUNCE_TARGET=capture` (domyślna w compose) kieruje je agentem `virtual(8)`
Postfiksa do skrzynki Maildir na wolumenie `mail_bounces` (`/var/mail/olimpiada/bounces/`), a
worker co 5 minut ją czyta (`apps.email_delivery.tasks.process_bounce_mailbox`), parsuje DSN
(`multipart/report; report-type=delivery-status`, RFC 3464) i kasuje przetworzone pliki. Podwójne
odbicia (`postmaster@`, `MAILER-DAEMON@`) i nadawcy monitoringu – nadal `discard`.

Odrzucone: (1) czytanie logu `mail` (`docker compose logs`) – wymaga gniazda Dockera w kontenerze
aplikacji (root na hoście) albo nowego kontenera z tym gniazdem; (2) `pipe` do HTTP – zmiana
`master.cf`, sekret do uwierzytelnienia relaya w aplikacji, `curl` w kontenerze relaya i żądanie
HTTP na każde odbicie. Maildir to natywny agent Postfiksa, jeden wolumen i dwa wpisy `postconf`.

Pliki Maildir zapisuje Postfix jako UID/GID `1000` (użytkownik `app` obrazu aplikacji,
`MAIL_BOUNCE_UID`/`MAIL_BOUNCE_GID`), więc worker je czyta i kasuje bez dodatkowych uprawnień.
Fałszywe zawiadomienia z wnętrza sieci compose (przegląd PR #98, M2) – trzy zabezpieczenia naraz:
- relay odrzuca pusty nadawcę koperty od **każdego** klienta SMTP
  (`check_sender_access inline:{ <>=REJECT }` na początku `smtpd_sender_restrictions`; 554 5.7.1).
  Prawdziwe DSN-y generuje demon `bounce` z pominięciem smtpd – sprawdzone na obrazie relaya
  5.10.2026: odmowa `MAIL FROM:<>` z sieci, zawiadomienie lokalne dalej w skrzynce,
- parser przyjmuje tylko DSN z pustym `Return-Path` **i** `Reporting-MTA` = nazwa naszego relaya
  (`MAIL_BOUNCE_REPORTING_MTA`, domyślnie `mail.<SITE_DOMAIN>`),
- czyta wyłącznie części najwyższego poziomu – raport schowany w załączonym liście pierwotnym
  (`message/rfc822`) jest ignorowany.

### 2.2. Klasyfikacja
Wyłącznie po kodzie rozszerzonym, z tablicy – bez zgadywania po treści (przegląd PR #98, L4:
Microsoft odrzuca z powodu polityki kodem `5.0.350` i treścią „mailbox unavailable”):
- **twarde**: `5.1.1`, `5.1.2`, `5.1.3`, `5.1.6`, `5.1.10` (null MX), `5.2.1`, `5.4.4` (domena bez MX/A),
- **miękkie** (liczone, bez skutków): pozostałe kody klasy adresu `x.1.x` (poza nadawcą `x.1.7`/`x.1.8`)
  i skrzynki `x.2.x` – np. `4.1.2`, `5.2.2` (skrzynka pełna),
- **pozostałe** (polityka/spam `x.7.x`, `5.0.x`, protokół, sieć, opóźnienie `4.4.x`, brak kodu) – nie są
  przypisywane adresowi; linia w logu workera z domeną i kodem.

### 2.3. Zapis (`DeliveryStatus`, klucz = adres małymi literami)
Adres, `undeliverable_at` (pierwsze twarde odbicie), `reason` (diagnoza, ≤ 500 znaków), `status_code`,
`source` (`smtp` / `dsn`), `hard_bounces`, `soft_bounces`, `last_soft_bounce_at`, `last_soft_reason`,
`updated_at`. Klucz to adres, a nie konto: odbija też adres rodzica, zaproszenia czy ucznia przed
założeniem konta; z kontem łączy go równość `lower(User.email)` (adres konta jest unikalny bez
względu na wielkość liter). Pole `email_undeliverable_at` z opisu zadania = `DeliveryStatus.undeliverable_at`
– bez zmiany modelu `accounts.User` (zasada: modele wspólne tylko, gdy konieczne).

### 2.4. Reset
- zmiana adresu konta (każda droga: potwierdzenie linkiem, panel koordynatora, anonimizacja) –
  sygnał `pre_save` na `User` kasuje wiersz starego adresu,
- „Mój adres jest poprawny” na banerze (POST, limit żądań, audyt `email.undeliverable_confirmed`),
- koordynator: „Oznacz jako doręczalny” na liście (audyt `email.undeliverable_cleared`),
- usunięcie konta – sygnał `post_delete` kasuje wiersz.

### 2.5. Baner
`{% email_undeliverable_banner %}` w `base.html` obok banera 2FA (poza slotami motywu). Zalogowany
użytkownik z twardo odbitym adresem widzi: „Nie możemy dostarczyć poczty na adres …” + odnośnik do
zmiany adresu + przycisk „Mój adres jest poprawny”. Odczyt przez cache (5 min, unieważniany przy
zapisie) – bez zapytania do bazy na każdej stronie.

### 2.6. Lista koordynatora
`/coordinator/undeliverable-emails/` (+ `?format=csv`): konta **tego konkursu**
(`users_for_competition`) z twardo odbitym adresem – adres, imię i nazwisko, data, kod, powód, liczba
odbić miękkich; czynność „Oznacz jako doręczalny”. Pozycja menu w „Raportach” tylko przy
`EMAIL_BOUNCE_TRACKING` (menu instalacji bez śledzenia – bajt w bajt jak dotąd). Eksport CSV przez
`apps.core.exports` (ochrona przed formułami), audyt `email.undeliverable_exported`.

### 2.7. Wstrzymanie listów nieobowiązkowych
`queue_mail(..., essential=False)` i komunikaty grupowe koordynatora pomijają adres z twardym odbiciem
(wpis w logu, bez błędu). Nieobowiązkowe: powiadomienia forum, czatu, sieci absolwentów (także
zaproszenia), webinarów, komunikaty grupowe. Obowiązkowe (idą zawsze – są też jedyną drogą, którą
właściciel może potwierdzić skrzynkę): aktywacja, reset hasła, zmiana adresu, powiadomienia
bezpieczeństwa, zgody, wyniki, rozmowy, płatności, biuro wsparcia.

### 2.8. RODO
- rejestr czynności: wiersz „Doręczalność poczty” (warunkowy – `EMAIL_BOUNCE_TRACKING`), wersja 1.22,
- eksport danych konta: sekcja `doreczalnosc_poczty` (stan adresu konta),
- retencja: wiersze bez zdarzenia od 365 dni kasuje zadanie dzienne `purge_delivery_statuses`;
  pliki Maildir – kasowane po przetworzeniu, nieczytelne po 7 dniach,
- usunięcie/anonimizacja konta – § 2.4.

## 3. Konfiguracja

- `EMAIL_BOUNCE_TRACKING` (env, domyślnie `False` w aplikacji, `true` w compose) – backend
  zapisujący odmowy, zadanie skrzynki, pozycja menu, wiersz rejestru.
- `MAIL_BOUNCE_MAILDIR` (env, domyślnie `/var/mail-bounces/bounces`) – katalog Maildir w workerze.
- `EMAIL_DOMAIN_DNS_CHECK` (env, domyślnie `True`).
- compose: wolumen `mail_bounces` (mail: `/var/mail/olimpiada`, worker: `/var/mail-bounces`),
  `MAIL_BOUNCE_TARGET` domyślnie `capture`, `MAIL_BOUNCE_REPORTING_MTA: mail.${SITE_DOMAIN}`,
  odmowa pustego nadawcy w `POSTFIX_smtpd_sender_restrictions`; `unknown_address_reject_code` domyślny.

## 4. Testy

`apps/email_delivery/tests/`: podpowiedzi (lista, TLD, IDN, plus, wielkość liter, brak fałszywych
alarmów dla znanych domen), DNS (MX, A, null MX, NXDOMAIN, błąd → fail-open, cache, domeny
zarezerwowane), pole (jedna blokada, „zostaw”, „użyj”, twarda blokada) w każdym z formularzy § 1.5,
JS (`node --test`, wspólne przypadki z Pythonem), parser DSN (Postfix: 5.1.1, 5.4.4, 4.x delayed,
5.7.1; nie-DSN; `Return-Path` niepusty), klasyfikacja, backend (5xx bez ponowień, 4xx ponawiane),
zadanie skrzynki (Maildir w `tmp_path`), mapowanie na konto, baner (pokaz, cache, potwierdzenie),
reset przy zmianie adresu i usunięciu konta, wstrzymanie, lista koordynatora (izolacja konkursów,
CSV, czynność, rola), rejestr, eksport, retencja, komplet tłumaczeń (10 katalogów).
`scripts/tests/mail_bounces_test.sh`: tryb `capture`.

## 5. Dokumentacja
`docs/OPERACJE.md` § 52, `docs/CHANGELOG.md`, podręczniki organizatora, uczestnika i opiekuna drużyny.
