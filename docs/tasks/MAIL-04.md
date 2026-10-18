# MAIL-04: Poczta do domen Microsoftu przez zewnętrzny serwer pośredniczący (relay)

## 0. Cel i granice

Outlook.com/Hotmail odrzucają pocztę z adresu `169.58.242.197` błędem `S3150` (blokada całego zakresu
adresów Contabo), a Microsoft odmówił odblokowania („Not qualified for mitigation”, OPERACJE § 49.9).
Uczestnik z adresem `@outlook.com`, `@hotmail.com` itd. nie dostaje więc linku aktywacyjnego ani
żadnego innego listu. Zadanie pozwala kierować **wyłącznie** listy do domen Microsoftu (albo – awaryjnie
– całą pocztę) przez zewnętrzny serwer SMTP z dobrą reputacją. Reszta poczty idzie jak dotąd, wprost do
MX-ów odbiorców.

Dostawca **nie jest wybrany** (Google Workspace `smtp-relay.gmail.com` albo usługa transakcyjna:
Brevo, Mailgun EU, Amazon SES) – konfiguracja jest niezależna od dostawcy, wyłącznie ze zmiennych
środowiskowych usługi `mail`.

Czego zadanie **nie** robi:
- nie zmienia obrazu ani wersji usługi `mail` (boky/postfix v5.1.0-alpine – SASL `cyrus-sasl-login`
  i TLS klienta są w obrazie) i nie dodaje zależności,
- nie przełącza aplikacji na API dostawcy ani na wariant B (`EMAIL_URL` dostawcy) – podpis DKIM, limity
  nadawców (OPS-02) i skrzynka odbić (MAIL-02) zostają w naszym relayu,
- nie rozpoznaje domen hostowanych w Microsoft 365 po MX (szkoły z własną domeną w Exchange Online) –
  `transport_maps` Postfiksa dopasowuje adres odbiorcy, nie jego MX; na taki przypadek jest
  `MAIL_RELAY_DOMAINS` z dopisaną domeną albo `*`,
- nie odbiera odbić z webhooków dostawcy (Brevo/Mailgun/SES przepisują kopertę na własną domenę – § 4).

## 1. Skrypt `deploy/mail/docker-init.d/70-relay.sh`

Uruchamiany przez obraz przy każdym starcie, po `50-bounces.sh` i `60-inbound.sh` (kolejność
alfabetyczna). Ta sama umowa, co tamte: funkcja z ciałem w podpowłoce (skrypt bywa włączany kropką),
**nigdy** nie kończy się błędem – zła konfiguracja = ostrzeżenie w logu i poczta bez zmian.

| Zmienna | Znaczenie |
|---|---|
| `MAIL_RELAY_HOST` | `[host]:port`, `host:port` albo `host` (→ `[host]:587`). Pusty = funkcja wyłączona, skrypt nie wywołuje `postconf` ani razu. Nawiasy zawsze (bez szukania MX hosta relaya). Port 465/2465 (TLS od pierwszego bajtu) – odmowa, bo `smtp:` w transporcie mówi STARTTLS. |
| `MAIL_RELAY_DOMAINS` | Domeny odbiorców kierowane przez relay (spacje/przecinki). Pusty = `outlook.com hotmail.com live.com msn.com outlook.pl hotmail.pl live.pl passport.com`. `*` = cała poczta. |
| `MAIL_RELAY_USER`, `MAIL_RELAY_PASSWORD` | SASL do relaya. Oba puste = bez SASL (uwierzytelnianie adresem IP, np. Google). Tylko jedno z nich albo znak nowej linii/dwukropek w loginie = odmowa. |
| `MAIL_RELAY_TLS_LEVEL` | `encrypt` (domyślnie – TLS wymagany, bez sprawdzania certyfikatu) albo `secure` (TLS + certyfikat z nazwą hosta relaya, `smtp_tls_CAfile` z obrazu). |

Co ustawia:
1. `olimpiada_relay_transport`: `<domena> smtp:[host]:port` (dla `*`: `* smtp:[host]:port` oraz
   `MAIL_INBOUND_FORWARD smtp:` – przekierowana poczta przychodząca ma cudzą kopertę, której relay
   dostawcy nie przyjmie). **Dopisany** do `transport_maps` z `50-bounces.sh`:
   `transport_maps = lmdb:…bounce_transport lmdb:…relay_transport`. Pierwszeństwo adresów
   `capture`/`discard`: Postfix (transport(5)) szuka najpierw pełnego adresu we **wszystkich**
   tablicach, potem domeny, na końcu `*` – więc `noreply@…  virtual:` wygrywa z `*` niezależnie od
   kolejności tablic.
2. `olimpiada_relay_tls`: `[host]:port encrypt` → `smtp_tls_policy_maps` (dopisana). Globalne
   `smtp_tls_security_level=may` dla pozostałych odbiorców bez zmian.
3. `olimpiada_relay_sasl` (+ `.lmdb`, oba `600`): `[host]:port login:hasło` →
   `smtp_sasl_auth_enable=yes`, `smtp_sasl_password_maps` (dopisana),
   `smtp_sasl_security_options=noanonymous`, `smtp_sasl_tls_security_options=noanonymous`. Klucz
   tablicy = cel z transportu, więc hasło idzie wyłącznie do relaya; inne serwery nie dostają AUTH.
   Hasło nie pojawia się w logu ani w argumentach `postconf`. Procesy Postfiksa czyszczą środowisko
   (`import_environment`); gdy obraz włącza skrypt kropką, `MAIL_RELAY_PASSWORD` znika też ze
   środowiska `supervisord`.
4. Kolejność: najpierw TLS i SASL, `transport_maps` na końcu – gdy cokolwiek wcześniej się nie uda,
   poczta nie zostaje skierowana do relaya bez TLS albo bez hasła.

Po ponownym uruchomieniu w tym samym kontenerze (`docker compose restart mail`) skrypt usuwa swoje
tablice z list przed dopisaniem – bez duplikatów. Zmiana zmiennych wymaga `docker compose up -d mail`
(nowy kontener, `/etc/postfix` od zera), więc wyłączenie nie zostawia resztek.

## 2. DKIM, SPF, DMARC

- **DKIM** podpisuje nasz OpenDKIM przy przyjęciu listu na 587 – relay dostaje list już podpisany
  (`d=<domena nadawcy>`, selektor `olimpiada`). Podpis przetrwa, jeżeli dostawca nie zmienia
  podpisanych nagłówków ani treści: **wyłączyć śledzenie otwarć i kliknięć** (Brevo, Mailgun) –
  przepisuje treść. Amazon SES przepisuje `Message-ID` (OpenDKIM go podpisuje), więc tam działa podpis
  SES (Easy DKIM, `d=<domena>`), nie nasz. Każdy z dostawców transakcyjnych dokłada własny podpis
  z kluczem w DNS domeny – DMARC przechodzi przez niego nawet przy złamanym naszym.
- **SPF**: dla Google Workspace koperta zostaje `noreply@<domena>`, więc SPF domeny musi zawierać
  `include:_spf.google.com` (obok `ip4:169.58.242.197`; jeden rekord `v=spf1`, limit 10 zapytań DNS).
  Brevo i SES (domyślnie) używają własnej domeny koperty – SPF domeny bez zmian, DMARC przez DKIM.
  Mailgun – `include:mailgun.org` w SPF domeny wysyłkowej.
- **DMARC** `iqo-official.org` jest ścisły (`adkim=s; aspf=s`): przechodzi DKIM z `d=iqo-official.org`
  (nasz albo dostawcy), SPF tylko przy Google (koperta w tej samej domenie).

Tabela dostawców z krokami – OPERACJE § 49.10.

## 3. Testy

`scripts/tests/mail_relay_test.sh` na atrapach `postconf`/`postmap` (job CI `mail-scripts`):
wyłączony = zero wywołań `postconf`; domyślne domeny; dopisanie do `transport_maps` z `50-bounces.sh`
i idempotencja `smtp_tls_policy_maps`; `*` z wyjątkiem adresu przekierowania; normalizacja hosta;
SASL – plik 600, hasło nigdy w wyjściu ani w argumentach `postconf`; bez SASL; odmowy (port 465, zły
host, jedno z pary login/hasło, nowa linia w haśle, awaria `postmap`) bez zmian w konfiguracji;
`secure` z plikiem CA i bez; włączenie kropką; zmienne w `docker-compose.yml`.

Bez Dockera nie da się tu sprawdzić prawdziwego wyboru transportu przez `trivial-rewrite` ani rozmowy
z relayem – to krok próby na produkcji (OPERACJE § 49.10, „Test”).

## 4. Ryzyka i ograniczenia

- **Odbicia**: Google zachowuje kopertę, więc odbicie od Microsoftu wraca na `noreply@` przez nasz MX
  (MAIL-03), ale je wygenerował Google, nie nasz relay – parser DSN (MAIL-02, `Reporting-MTA`) go nie
  przypisze. Brevo/Mailgun/SES odbijają do siebie (panel/webhook dostawcy). Odmowa samego relaya
  (np. niezweryfikowany nadawca) = lokalne odbicie naszego Postfiksa, jak dotąd.
- **Podmiot przetwarzający**: dostawca relaya dostaje adresy i treść listów do domen z listy – umowa
  powierzenia (DPA) przed włączeniem; rejestr czynności ma już ogólnego odbiorcę „dostawca poczty
  wychodzącej (SMTP)”.
- **Limity dostawcy** (np. Google: 100 odbiorców na transakcję, 10 000 na dobę na konto Workspace
  będące nadawcą koperty) – istotne przy `*`.
