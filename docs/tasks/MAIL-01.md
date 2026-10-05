# MAIL-01: Poczta z domeny konkursu (listy IQO od `@iqo-official.org`)

## 0. Cel i granice

Dziś każdy list platformy wychodzi przez usługę `mail` (boky/postfix 5.1.0 + OpenDKIM, selektor
`olimpiada`) od `noreply@olimpiadakwantowa.pl` – także listy IQO, bo `Competition.from_email` konkursu
`iqo` jest pusty, a `ALLOWED_SENDER_DOMAINS` (PR #67) zawiera jedną domenę. Uczestnik IQO dostaje więc
list „od” polskiej olimpiady. Zadanie pozwala konkursowi wysyłać **z własnej domeny** (`iqo-official.org`)
tym samym relayem, z poprawnym SPF/DKIM/DMARC, i sprząta pocztę zwrotną, która dziś krąży w relayu
(„mail for … loops back to myself”).

Czego zadanie **nie** robi:
- nie zmienia obrazu ani wersji usługi `mail` i nie dodaje zależności Pythona (DNS – biblioteka standardowa),
- nie przyjmuje poczty przychodzącej: MX domeny konkursu zostaje u jej dotychczasowego dostawcy
  (Squarespace / Google Workspace), relay dalej nie ma portu 25 na świat,
- nie ustawia `from_email` sam – robi to operator **po** weryfikacji DNS (`docs/OPERACJE.md` § 49),
- nie blokuje wysyłki z domeny niezweryfikowanej – ostrzega koordynatora (decyzja § 4.3).

## 1. Podpis DKIM wielu domen

Obraz boky/postfix 5.1.0 obsługuje wiele domen natywnie (`/scripts/functions.sh`,
`postfix_setup_dkim`): dla **każdej** domeny z `ALLOWED_SENDER_DOMAINS`, dla której istnieje
`/etc/opendkim/keys/<domena>.private`, dopisuje wiersz do `KeyTable` i `SigningTable`
(`*@<domena>` → `<selektor>._domainkey.<domena>`, `refile:`), a przy `DKIM_AUTOGENERATE=true`
generuje brakujące klucze przy starcie. Druga domena = druga pozycja w `ALLOWED_SENDER_DOMAINS`
i drugi klucz na tym samym wolumenie `mail_dkim`. Selektor wspólny (`DKIM_SELECTOR=olimpiada`).
Compose bez zmian w tej części.

## 2. `scripts/mail_add_domain.sh <domena>` (na serwerze, w `/opt/olimpiada`)

Idempotentny; drugi przebieg niczego nie zmienia i wypisuje te same rekordy.

1. Waliduje nazwę domeny (małe litery, etykiety LDH, kropka, bez `@`), odmawia `SITE_DOMAIN`
   (ta ma rekordy z kroku 7/8 wdrożenia).
2. Dopisuje domenę do `ALLOWED_SENDER_DOMAINS` w `.env` (brak wpisu = `SITE_DOMAIN` + nowa domena;
   wartość w cudzysłowie, bo ma spację). Kopia `.env.bak-mail-<znacznik>` przed zmianą.
3. Generuje klucz **w kontenerze `mail`** tym samym poleceniem, co obraz
   (`opendkim-genkey -b 2048 -h rsa-sha256 -r -v --subdomains -s <selektor> -d <domena>`, właściciel
   `opendkim`, `chmod 400`) – tylko gdy `<domena>.private` nie istnieje. Istniejącego nie nadpisuje nigdy.
4. Odtwarza `mail` (`docker compose up -d mail`), żeby OpenDKIM wczytał nowy wiersz tablic.
   `web`/`worker`/`beat` **nie** – ich restart operator robi świadomie przed ustawieniem nadawcy (OPERACJE § 49).
   `--no-restart` pomija i ten krok.
5. Wypisuje i zapisuje do `mail-dns-<domena>.txt` (chmod 600) rekordy do dodania:
   - **SPF** – sugerowany przez `manage.py check_mail_dns --suggest` (scalenie z istniejącym SPF:
     `ip4:<IP>` dopisany do obecnego rekordu, nigdy drugi rekord `v=spf1`; brak SPF przy MX Google =
     `include:_spf.google.com ~all`); bez działającego `web` – rekord bazowy i instrukcja scalenia,
   - **DKIM** – TXT `<selektor>._domainkey`, jedna wartość sklejona z fragmentów BIND,
   - **DMARC** – `p=none` na start, plan przejścia na `p=quarantine` po 2 tygodniach czystych raportów;
     jeśli domena ma już DMARC – nie dodawać drugiego,
   - zdanie „**MX i pozostałych rekordów nie ruszaj**”.
6. `--check` – zamiast zmian: `opendkim-testkey` w kontenerze `mail` (klucz w DNS = klucz prywatny)
   i `manage.py check_mail_dns <domena> --dkim-public-key …` w `web`.

Zmienne: `COMPOSE` (domyślnie `docker compose`, atrapa w teście), `REMOTE_DIR` (katalog z `.env`,
domyślnie bieżący), `MAIL_PUBLIC_IP`, `DMARC_RUA`, `DKIM_BITS` (2048; 1024 tylko dla panelu, który
nie przyjmie dłuższego TXT).

## 3. Wdrożenie (`scripts/deploy.sh`, krok 7/8)

Klucze leżą na wolumenie `mail_dkim`, którego wdrożenie nie dotyka. Krok 7/8 dodatkowo:
- przed nadpisaniem `mail-dns.txt` porównuje klucz DKIM domeny głównej z zapisanym poprzednio – inny
  klucz (wolumen odtworzony od zera) = głośne ostrzeżenie „zaktualizuj DNS”,
- dla każdej dodatkowej domeny z `ALLOWED_SENDER_DOMAINS` porównuje klucz w kontenerze z wartością
  w `mail-dns-<domena>.txt` i ostrzega przy rozjeździe albo braku pliku.

## 4. Aplikacja (`apps.mail_domains`)

### 4.1. `manage.py check_mail_dns <domena> [...]`
Odpytuje DNS (klient UDP/TCP z EDNS0 na bibliotece standardowej, serwery z `/etc/resolv.conf` albo
`--nameserver`; uruchamiane w `web`, który ma wyjście na świat – `worker` go nie ma):
- **SPF** – ocena `check_host` (RFC 7208) dla IP relaya: `ip4`, `a`, `mx`, `include`, `redirect`,
  `all`, limit 10 zapytań; `pass` = OK. IP: `--ip`, `MAIL_PUBLIC_IP`, inaczej rekord A `mail.<SITE_DOMAIN>`.
- **DKIM** – TXT `<selektor>._domainkey.<domena>` z niepustym `p=`; z `--dkim-public-key` także
  równość z kluczem z kontenera.
- **DMARC** – dokładnie jeden `v=DMARC1` pod `_dmarc`; `p=none` = OK z uwagą o planie.
Wynik zapisuje w `SenderDomain` (domena, `verified`, `checked_at`, `verified_at`, raport JSON;
`--no-save` pomija). Kod wyjścia 1, gdy domena nie przeszła. `--suggest` wypisuje sam sugerowany SPF
(dla skryptu), `--json` – raport maszynowy.

### 4.2. Ostrzeżenie dla koordynatora
`apps.mail_domains.services.sender_warnings(competition)` – lista zdań pokazywana na pulpicie
koordynatora i na ekranie „Ustawienia konkursu”, gdy `from_email` konkursu:
- jest w domenie spoza `ALLOWED_SENDER_DOMAINS` (listy idą od `DEFAULT_FROM_EMAIL`), albo
- jest w domenie bez udanego `check_mail_dns` (brak wiersza / ostatnie sprawdzenie nie przeszło).
Bez ostrzeżeń: pusty `from_email`, domena `DEFAULT_FROM_EMAIL` (instalacji – rekordy z wdrożenia),
`ALLOWED_SENDER_DOMAINS=*` (wariant B: autoryzacją zajmuje się dostawca). Zero zapytań DNS w żądaniu.

### 4.3. Decyzja: ostrzeżenie, nie blokada
`mail_from` nie sprawdza weryfikacji: wiersz `SenderDomain` może się zestarzeć w obie strony, a cicha
zmiana nadawcy w trakcie rejestracji jest gorsza od widocznego ostrzeżenia. Blokadą twardą zostaje
`ALLOWED_SENDER_DOMAINS` (relay odrzuca obcą kopertę, aplikacja wraca do nadawcy instalacji).

## 5. Poczta zwrotna (bounce)

Postfix bez lokalnego doręczania wysyła zawiadomienie o niedoręczeniu na adres koperty
(`noreply@<domena>`) przez MX tej domeny – dla domeny platformy to ten sam serwer (pętla), dla
domeny konkursu – cudzy MX bez takiej skrzynki (podwójne odbicie). Skrypt startowy usługi `mail`
(`deploy/mail/docker-init.d/50-bounces.sh`, montowany do `/docker-init.d/`):
- adresy: `MAIL_BOUNCE_ADDRESSES` (domyślnie `noreply@<każda domena z ALLOWED_SENDER_DOMAINS>`)
  plus `postmaster`, `MAILER-DAEMON` i `double-bounce` w domenie `myhostname`,
- `MAIL_BOUNCE_TARGET=discard` (domyślnie) → `transport_maps` z `discard:`; adres e-mail →
  `virtual_alias_maps` na skrzynkę operatora; zła wartość = ostrzeżenie w logu i `discard`.
Liczba odbić zostaje w logu `mail` (`status=bounced` przy liście pierwotnym).

## 6. Testy

- `apps/mail_domains/tests/`: parser i zapytanie DNS (pakiety złożone ręcznie, kompresja nazw, TXT
  w kilku napisach, NXDOMAIN, TC→TCP), ocena SPF (include, redirect, a/mx, limit zapytań, dwa rekordy),
  sugestia SPF (scalenie, Google MX, brak MX), DKIM (brak, pusty `p=`, rozjazd klucza), DMARC,
  komenda (zapis wiersza, kod wyjścia, `--suggest`, `--no-save`), ostrzeżenia (wszystkie gałęzie §
  4.2) i ich obecność na pulpicie i ekranie ustawień.
- `scripts/tests/mail_add_domain_test.sh` (atrapa `COMPOSE`): walidacja domeny, dopisanie do `.env`
  i idempotencja, generacja klucza tylko przy braku, restart tylko `mail`, treść rekordów,
  `--no-restart`, `--check`.
- `scripts/tests/mail_bounces_test.sh` (atrapy `postconf`/`postmap`): obie wartości celu, wartość zła,
  adresy domyślne i jawne.

## 7. Dokumentacja

`docs/OPERACJE.md` § 49 (krok po kroku dla Squarespace), OPERACJE § 9.7 (odnośnik), `docs/CHANGELOG.md`.
