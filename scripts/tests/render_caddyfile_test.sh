#!/usr/bin/env bash
# Test generatora konfiguracji Caddy'ego (`scripts/render_caddyfile.sh`).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu):
#   scripts/tests/render_caddyfile_test.sh
#
# Testu nie da się uruchomić w kontenerze `web`: kontekstem budowania obrazu jest `backend/`,
# więc katalogu `scripts/` w obrazie nie ma i być nie powinno. Dlatego jest to test powłoki,
# a nie przypadek pytesta – narzędzie sprawdza się tam, gdzie działa.
#
# Najważniejszy przypadek jest pierwszy: **pusty `EXTRA_DOMAINS` musi dać kopię bajt w bajt**.
# To jest jedyne zabezpieczenie przed tym, żeby dołożenie wielokonkursowości zmieniło konfigurację
# proxy działającej produkcji (docs/UNIWERSALNY-ETAP-1.md § 0). Ta sama gwarancja dla
# `DJCMS_ENABLED` (docs/tasks/DJ-01.md § 8.8) – przypadki 14–18; `DJCMS_PRIMARY` i sekcja tras djcms
# w każdym bloku aplikacji (docs/tasks/DJ-02.md § 3) – 20–27; blok S3 (odmowa API MinIO, nagłówki
# bezpieczeństwa – audyt z 1.10.2026) – 25; przy dostępnym Dockerze (19) także `caddy validate`/
# `adapt`, a na końcu scripts/tests/djcms_routing_test.sh (żywy Caddy, tablica tras) i
# scripts/tests/s3_proxy_test.sh (żywy Caddy z atrapą MinIO, blok S3).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RENDER="$ROOT/scripts/render_caddyfile.sh"
SRC="$ROOT/deploy/Caddyfile"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/render-caddyfile-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

failures=0
check() {
  # check "opis" <kod-wyniku>
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

render() {
  # render "<EXTRA_DOMAINS>" <plik-wyjściowy> ["<PLATFORM_SUBDOMAINS>"] ["<DJCMS_ENABLED>"] ["<DJCMS_PRIMARY>"];
  # zwraca kod wyjścia generatora. Argumenty 3–5 są **zawsze** przekazywane (choćby puste), bo
  # zmienna nieustawiona każe generatorowi czytać `.env` – a test ma sprawdzać generator, a nie
  # czyjś plik konfiguracyjny.
  EXTRA_DOMAINS="$1" PLATFORM_SUBDOMAINS="${3:-}" DJCMS_ENABLED="${4:-}" DJCMS_PRIMARY="${5:-}" LIVEKIT_PROXY="${6:-}" ERRORS_PROXY="${7:-}" CADDYFILE_OUT="$2"     bash "$RENDER" >"$WORK/stdout" 2>"$WORK/stderr"
}

# 1. Pusta lista domen = dzisiejsza konfiguracja, co do bajtu.
render "" "$WORK/empty.caddy"
cmp -s "$SRC" "$WORK/empty.caddy"
check "pusty EXTRA_DOMAINS daje kopię deploy/Caddyfile bajt w bajt" $?

# 2. Bloki źródłowe zostają nietknięte także wtedy, gdy domeny są.
render "olimpiadafizyczna.pl konkurs.example" "$WORK/two.caddy"
head -c "$(wc -c <"$SRC")" "$WORK/two.caddy" | cmp -s - "$SRC"
check "dodanie domen nie rusza ani jednego bajtu bloków źródłowych" $?

for needle in 'olimpiadafizyczna.pl {' 'konkurs.example {' 'reverse_proxy web:8000'; do
  grep -qF "$needle" "$WORK/two.caddy"
  check "wygenerowany plik zawiera „$needle”" $?
done

# Każda domena to dokładnie jeden nowy blok aplikacji (plus ten, który był w źródle).
occurrences=$(grep -c 'reverse_proxy web:8000' "$WORK/two.caddy")
[ "$occurrences" -eq 3 ]
check "trzy bloki aplikacji: domena główna + dwie dodatkowe (jest $occurrences)" $?

# 3. `www.x` obok `x` = przekierowanie 301, a nie druga witryna z tą samą treścią.
render "olimpiadafizyczna.pl www.olimpiadafizyczna.pl" "$WORK/www.caddy"
grep -qF 'redir https://olimpiadafizyczna.pl{uri} permanent' "$WORK/www.caddy"
check "www.<domena> przy obecnej domenie głównej dostaje przekierowanie 301" $?

# 4. `www.x` bez `x` na liście jest zwykłą domeną – nie ma na co przekierowywać.
render "www.samo-www.example" "$WORK/lonely.caddy"
# Szukamy przekierowania **na ten konkretny adres**: blok źródłowy `www.{$SITE_DOMAIN}` ma
# własne `redir` i samo słowo w pliku niczego by nie dowodziło.
grep -qF 'www.samo-www.example {' "$WORK/lonely.caddy" &&
  ! grep -qF 'redir https://samo-www.example' "$WORK/lonely.caddy"
check "samo www.<domena> jest obsługiwane jak zwykła domena" $?

# 5. Nazwa hosta trafia do pliku jako składnia, więc śmieci muszą zatrzymać generator.
for bad in 'zla domena{' 'a.pl}' 'a.pl/sciezka' 'http://a.pl'; do
  render "$bad" "$WORK/bad.caddy"
  status=$?
  [ "$status" -ne 0 ]
  check "generator odmawia dla EXTRA_DOMAINS=„$bad”" $?
done

# 6. Plik wynikowy nie może powstać w wersji częściowej po odmowie (poprzedni zostaje nietknięty).
render "" "$WORK/keep.caddy"
render 'zla domena{' "$WORK/keep.caddy"
cmp -s "$SRC" "$WORK/keep.caddy"
check "odmowa nie nadpisuje wcześniej wygenerowanego pliku" $?

# --- PLATFORM_SUBDOMAINS: konkursy w subdomenach platformy ----------------------------------------
#
# Ten przełącznik dokłada do konfiguracji proxy on-demand TLS i blok `*.{$SITE_DOMAIN}`. Testy niżej
# pilnują dwóch rzeczy naraz: że **wyłączony** nie zmienia ani jednego bajtu (to jest ta sama
# gwarancja, co w punkcie 1, tylko dla drugiego przełącznika) i że **włączony** dokłada dokładnie
# to, co ma – z adresem endpointu zgody włącznie, bo literówka w nim daje proxy, które odmawia
# certyfikatu każdemu konkursowi, i objawia się dopiero na produkcji.

# 7. Wyłączony (wprost, na zero i na pusto) = dzisiejsza konfiguracja, co do bajtu.
for off in "" "0" "false" "off" "no"; do
  render "" "$WORK/sub-off.caddy" "$off"
  cmp -s "$SRC" "$WORK/sub-off.caddy"
  check "PLATFORM_SUBDOMAINS=„$off” daje kopię deploy/Caddyfile bajt w bajt" $?
done

# 8. Wartość, której generator nie rozumie, musi go zatrzymać – „tak” nie może po cichu znaczyć „nie”.
for bad in "tak" "1;rm" "enabled"; do
  render "" "$WORK/sub-bad.caddy" "$bad"
  status=$?
  [ "$status" -ne 0 ]
  check "generator odmawia dla PLATFORM_SUBDOMAINS=„$bad”" $?
done

# 9. Włączony: opcja globalna z **dokładnym** adresem endpointu zgody i blok wieloznaczny.
render "" "$WORK/sub-on.caddy" "1"
check "generator kończy się sukcesem przy PLATFORM_SUBDOMAINS=1" $?

for needle in 'on_demand_tls {' \
              'ask http://web:8000/internal/tls-allowed' \
              '*.{$SITE_DOMAIN} {' \
              'tls {' \
              'on_demand' \
              'handle /internal/* {' \
              'respond 404'; do
  grep -qF "$needle" "$WORK/sub-on.caddy"
  check "przy włączonym przełączniku plik zawiera „$needle”" $?
done

# `true` obok `1` – obie wartości z kontraktu dają ten sam plik.
render "" "$WORK/sub-true.caddy" "true"
cmp -s "$WORK/sub-on.caddy" "$WORK/sub-true.caddy"
check "PLATFORM_SUBDOMAINS=true daje to samo, co =1" $?

# Wszystkie bloki źródłowe zostają — i w tej samej kolejności. Przełącznik ma **dokładać**
# konfigurację, a nie podmieniać; zgubiony blok `meet.` albo `{$S3_PUBLIC_ADDRESS}` to wyłączone
# rozmowy kwalifikacyjne albo martwe odnośniki do plików, jedno i drugie bez komunikatu o błędzie.
grep -n '^[^ #].* {$' "$SRC" | sed 's/^[0-9]*://' > "$WORK/blocks-src.txt"
grep -n '^[^ #].* {$' "$WORK/sub-on.caddy" | sed 's/^[0-9]*://' > "$WORK/blocks-on.txt"
head -n "$(wc -l <"$WORK/blocks-src.txt")" "$WORK/blocks-on.txt" | cmp -s - "$WORK/blocks-src.txt"
check "bloki źródłowe zostają w tej samej kolejności, a nowy dochodzi na końcu" $?
grep -qF '*.{$SITE_DOMAIN} {' "$WORK/blocks-on.txt" &&
  [ "$(wc -l <"$WORK/blocks-on.txt")" -eq "$(( $(wc -l <"$WORK/blocks-src.txt") + 1 ))" ]
check "przełącznik dokłada dokładnie jeden blok serwerowy" $?

# Odmowa `/internal/*` w **każdym** publicznym bloku aplikacji: domena główna + blok wieloznaczny.
occurrences=$(grep -c 'handle /internal/\*' "$WORK/sub-on.caddy")
[ "$occurrences" -eq 2 ]
check "odmowa /internal/* w bloku domeny głównej i w bloku wieloznacznym (jest $occurrences)" $?

# 10. Przełącznik razem z EXTRA_DOMAINS: każdy blok konkursu też odmawia /internal/*.
render "olimpiadafizyczna.pl www.olimpiadafizyczna.pl konkurs.example" "$WORK/sub-extra.caddy" "1"
check "generator kończy się sukcesem przy EXTRA_DOMAINS + PLATFORM_SUBDOMAINS=1" $?

# Trzy bloki aplikacji z odmową: domena główna, olimpiadafizyczna.pl, konkurs.example, blok
# wieloznaczny. `www.olimpiadafizyczna.pl` jest przekierowaniem 301 i żadnej treści nie podaje —
# w Caddym `redir` wykonuje się przed `handle`, więc reguła byłaby tam martwa.
occurrences=$(grep -c 'handle /internal/\*' "$WORK/sub-extra.caddy")
[ "$occurrences" -eq 4 ]
check "odmowa /internal/* w każdym bloku aplikacji, także z EXTRA_DOMAINS (jest $occurrences)" $?
grep -qF 'redir https://olimpiadafizyczna.pl{uri} permanent' "$WORK/sub-extra.caddy"
check "przekierowanie www zostaje przekierowaniem także przy włączonym przełączniku" $?

# Blok wieloznaczny jest ostatni: bloki dosłowne (w tym z EXTRA_DOMAINS) i tak wygrywają, bo Caddy
# dobiera witrynę po najbardziej szczegółowym dopasowaniu hosta, ale kolejność w pliku jest tym,
# co czyta człowiek szukający, „skąd się wziął ten certyfikat”.
tail -n 1 "$(printf '%s' "$WORK/sub-extra.caddy")" | grep -qF '}'
check "plik z EXTRA_DOMAINS i przełącznikiem kończy się domkniętym blokiem" $?
[ "$(grep -n '^\*\.{\$SITE_DOMAIN} {$' "$WORK/sub-extra.caddy" | cut -d: -f1)" -gt \
  "$(grep -n '^konkurs\.example {$' "$WORK/sub-extra.caddy" | cut -d: -f1)" ]
check "blok wieloznaczny stoi za blokami z EXTRA_DOMAINS" $?

# Przypięcie polityki TLS nazw dosłownych z pliku źródłowego (www., domena główna, blok S3, meet.,
# monitor.) – po jednym, zaraz za nagłówkiem bloku; EXTRA_DOMAINS i `*.` bez niego (skutek – § 19).
pinned="$(awk '/^[^ #].* \{$/ { head = $0 } $0 == "        key_type p256" { print head }' "$WORK/sub-extra.caddy" | tr '\n' '|')"
[ "$pinned" = 'www.{$SITE_DOMAIN} {|{$SITE_DOMAIN} {|{$S3_PUBLIC_ADDRESS} {|meet.{$SITE_DOMAIN} {|monitor.{$SITE_DOMAIN} {|' ]
check "PLATFORM_SUBDOMAINS=1: tls { key_type p256 } w www., domenie głównej, S3, meet., monitor. i nigdzie indziej (jest: $pinned)" $?

# Odmowa `/<prefiks>/internal/…` (konkursy pod prefiksem ścieżki) – wyłącznie w bloku domeny głównej.
[ "$(grep -c '^    @internal_prefixed path_regexp \^/\[^/\]+/internal(/\.\*)?\$$' "$WORK/sub-extra.caddy")" -eq 1 ] &&
  awk '$0 == "{$SITE_DOMAIN} {" { m = 1 } m && /@internal_prefixed/ { found = 1 } m && /^}/ { exit } END { exit !found }' "$WORK/sub-extra.caddy"
check "odmowa /<prefiks>/internal/* raz, w bloku domeny głównej" $?

# 11. Wyłączony przełącznik przy obecnych EXTRA_DOMAINS: ani śladu po on-demand TLS.
render "olimpiadafizyczna.pl" "$WORK/sub-off-extra.caddy" ""
! grep -qE 'on_demand|/internal/|key_type' "$WORK/sub-off-extra.caddy"
check "wyłączony przełącznik nie zostawia on-demand TLS ani /internal/* w pliku z EXTRA_DOMAINS" $?

# --- Strona „Prace techniczne” (fragment `(maintenance)`, docs/OPERACJE.md § 20) ------------------
#
# Fragment ma trafić do **każdego** bloku aplikacji (domena główna, EXTRA_DOMAINS, blok `*.`) i do
# żadnego innego: `meet.` (Jitsi), `monitor.` (monitoring ma działać właśnie w czasie przerwy)
# i blok S3 (klient S3 nie zrozumie strony HTML w odpowiedzi na przerwane wgrywanie).

# 12. Źródło: definicja fragmentu i dokładnie jedno `import` – w bloku domeny głównej.
grep -qF '(maintenance) {' "$SRC"
check "deploy/Caddyfile definiuje fragment (maintenance)" $?
occurrences=$(grep -c '^    import maintenance$' "$SRC")
[ "$occurrences" -eq 1 ]
check "deploy/Caddyfile importuje fragment raz – w bloku domeny głównej (jest $occurrences)" $?

block_imports() {
  # block_imports <plik> <dosłowny nagłówek bloku> – drukuje liczbę `import maintenance` w tym bloku.
  awk -v head="$2" '
    $0 == head { inside = 1; n = 0; next }
    inside && /^}/ { print n; inside = 0; found = 1 }
    inside && $0 == "    import maintenance" { n++ }
    END { if (!found) print "brak" }
  ' "$1"
}
[ "$(block_imports "$SRC" '{$SITE_DOMAIN} {')" = "1" ]
check "blok {\$SITE_DOMAIN} importuje stronę prac technicznych" $?
for head in 'meet.{$SITE_DOMAIN} {' 'monitor.{$SITE_DOMAIN} {' '{$S3_PUBLIC_ADDRESS} {' 'www.{$SITE_DOMAIN} {'; do
  [ "$(block_imports "$SRC" "$head")" = "0" ]
  check "blok „$head” NIE importuje strony prac technicznych" $?
done

# Wymagane elementy fragmentu – literówka w którymkolwiek to przerwa bez strony albo bez furtki.
for needle in 'try_files /on' \
              'not path /.well-known/acme-challenge/*' \
              'X-Maintenance-Bypass' \
              'olimpiada_maintenance_bypass' \
              '"{$MAINTENANCE_BYPASS_TOKEN}".size() >= 32' \
              '{http.error.status_code} in [502, 503, 504]' \
              '@maintenance_json path /status.json /healthz /healthz/* /api/*' \
              'respond `{"status":"maintenance","retry_after":60}` 503' \
              'Retry-After "60"' \
              'Cache-Control "no-store"' \
              'status 503'; do
  grep -qF -- "$needle" "$SRC"
  check "fragment (maintenance) zawiera „$needle”" $?
done
# `handle_errors 502 503 504` (lista kodów) w Caddym 2.8 nadpisuje zagnieżdżone matchery – musi
# zostać `handle_errors` bez listy i matcher kodu w środku (komentarz w deploy/Caddyfile).
! grep -qE '^ *handle_errors [0-9]' "$SRC"
check "handle_errors bez listy kodów (błąd Caddy'ego 2.8 z nadpisanymi matcherami)" $?

# 13. Wygenerowane bloki aplikacji: EXTRA_DOMAINS i `*.` też importują stronę, `www.` (301) – nie.
occurrences=$(grep -c '^    import maintenance$' "$WORK/sub-extra.caddy")
[ "$occurrences" -eq 4 ]
check "import maintenance: domena główna + 2 z EXTRA_DOMAINS + blok *. (jest $occurrences)" $?
[ "$(block_imports "$WORK/sub-extra.caddy" 'www.olimpiadafizyczna.pl {')" = "0" ]
check "przekierowanie www z EXTRA_DOMAINS nie importuje strony" $?
occurrences=$(grep -c '^    import maintenance$' "$WORK/two.caddy")
[ "$occurrences" -eq 3 ]
check "bez przełącznika: domena główna + 2 z EXTRA_DOMAINS (jest $occurrences)" $?

# --- DJCMS_ENABLED: wersja porównawcza na django CMS pod dj.<SITE_DOMAIN> (DJ-01 § 8.8) ----------
#
# Ta sama para gwarancji co przy PLATFORM_SUBDOMAINS: **wyłączony** przełącznik nie zmienia ani
# bajtu – i to przy każdej kombinacji pozostałych dwóch zmiennych, bo produkcja może mieć już
# EXTRA_DOMAINS albo subdomeny – a **włączony** dokłada dokładnie blok `dj.` i odmowę /internal/*.

DJ_EXTRA="olimpiadafizyczna.pl www.olimpiadafizyczna.pl konkurs.example"

strip_djcms() {
  # strip_djcms <plik> <1|0> – drukuje plik bez wstawek DJCMS_ENABLED: bez bloku `dj.` (ostatni
  # w pliku, razem z pustą linijką przed jego komentarzem) i bez komentarza nad odmową /internal/*
  # w bloku domeny głównej (jego treść zależy od przełączników). Z drugim argumentem 1 wycina też
  # same odmowy `handle /internal/* { respond 404 }` – dla porównań przy wyłączonych subdomenach,
  # gdzie wszystkie odmowy pochodzą od DJCMS_ENABLED. Zawsze wycina też sekcję tras djcms
  # (DJ-02 § 3) – od znacznika `>>> django CMS` do `<<< django CMS` – z każdego bloku aplikacji.
  awk -v guards="$2" '
    $0 == "# Wygenerowane przez scripts/render_caddyfile.sh przy DJCMS_ENABLED=1 – nie edytuj tego pliku." { cut = 1; exit }
    /^    # >>> django CMS / { sec = 1 }
    sec { if ($0 == "    # <<< django CMS") sec = 0; next }
    pending { print prev; pending = 0 }
    /^    # `\/internal\/\*` jest wyłącznie dla / { skip = 3 }
    skip > 0 { skip--; next }
    guards == "1" && $0 == "    handle /internal/* {" { g = 3 }
    guards == "1" && $0 == "    @internal_prefixed path_regexp ^/[^/]+/internal(/.*)?$" { g = 4 }
    g > 0 { g--; next }
    $0 == "" { prev = $0; pending = 1; next }
    { print }
    END { if (pending && !cut) print prev }
  ' "$1"
}

# 14. Wyłączony (pusto, zero, false, off, no) przy każdej kombinacji EXTRA_DOMAINS × PLATFORM_SUBDOMAINS
#     = wynik bez tej zmiennej w ogóle; a przy pustych pozostałych – kopia deploy/Caddyfile.
for off in "" "0" "false" "off" "no"; do
  render "" "$WORK/dj-off.caddy" "" "$off"
  cmp -s "$SRC" "$WORK/dj-off.caddy"
  check "DJCMS_ENABLED=„$off” daje kopię deploy/Caddyfile bajt w bajt" $?
done
for extra in "" "$DJ_EXTRA"; do
  for sub in "" "1"; do
    render "$extra" "$WORK/dj-ref.caddy" "$sub" ""
    render "$extra" "$WORK/dj-zero.caddy" "$sub" "0"
    cmp -s "$WORK/dj-ref.caddy" "$WORK/dj-zero.caddy" && ! grep -qE 'dj\.|djcms' "$WORK/dj-zero.caddy"
    check "DJCMS_ENABLED=0 nie zmienia bajtu przy EXTRA_DOMAINS=„${extra:+…}” i PLATFORM_SUBDOMAINS=„$sub”" $?
  done
done

# 15. Wartość spoza kontraktu zatrzymuje generator (tak samo jak przy PLATFORM_SUBDOMAINS).
for bad in "tak" "1;rm" "enabled" "2"; do
  render "" "$WORK/dj-bad.caddy" "" "$bad"
  status=$?
  [ "$status" -ne 0 ]
  check "generator odmawia dla DJCMS_ENABLED=„$bad”" $?
done

# 16. Włączony, sam: blok `dj.` jeden i ostatni, odmowa w bloku głównym, reszta nietknięta.
render "" "$WORK/dj-on.caddy" "" "1"
check "generator kończy się sukcesem przy DJCMS_ENABLED=1" $?
grep -qF 'dj. (django CMS): włączone, DJCMS_PRIMARY=0 (preview)' "$WORK/stdout"
check "podsumowanie generatora wymienia dj. i tryb" $?
[ "$(grep -c '^dj\.{\$SITE_DOMAIN} {$' "$WORK/dj-on.caddy")" -eq 1 ]
check "blok dj.{\$SITE_DOMAIN} występuje dokładnie raz" $?
[ "$(grep -E '^[^ #].* \{$' "$WORK/dj-on.caddy" | tail -n 1)" = 'dj.{$SITE_DOMAIN} {' ]
check "blok dj. jest ostatnim blokiem pliku" $?
grep -n '^[^ #].* {$' "$WORK/dj-on.caddy" | sed 's/^[0-9]*://' > "$WORK/blocks-dj.txt"
head -n "$(wc -l <"$WORK/blocks-src.txt")" "$WORK/blocks-dj.txt" | cmp -s - "$WORK/blocks-src.txt" &&
  [ "$(wc -l <"$WORK/blocks-dj.txt")" -eq "$(( $(wc -l <"$WORK/blocks-src.txt") + 1 ))" ]
check "bloki źródłowe zostają w tej samej kolejności, a dochodzi jeden blok (dj.)" $?
! grep -qE 'on_demand' "$WORK/dj-on.caddy"
check "sam DJCMS_ENABLED nie włącza on-demand TLS" $?
strip_djcms "$WORK/dj-on.caddy" 1 | cmp -s - "$SRC"
check "wynik z DJCMS_ENABLED=1 minus wstawki djcms = deploy/Caddyfile co do bajtu" $?

block_body() {
  # block_body <plik> <dosłowny nagłówek bloku> – drukuje wnętrze bloku (bez nagłówka i klamry).
  awk -v head="$2" '$0 == head { inside = 1; next } inside && /^}/ { exit } inside { print }' "$1"
}
guards_in() { block_body "$1" "$2" | grep -c '^    handle /internal/\* {$'; }
[ "$(guards_in "$WORK/dj-on.caddy" '{$SITE_DOMAIN} {')" -eq 1 ]
check "odmowa /internal/* w bloku domeny głównej (dokładnie raz)" $?
[ "$(guards_in "$WORK/dj-on.caddy" 'dj.{$SITE_DOMAIN} {')" -eq 1 ]
check "odmowa /internal/* w bloku dj." $?
# dj. od DJ-02 (D1) = wyłącznie przekierowanie 302 na stronę włączenia podglądu domeny głównej.
block_body "$WORK/dj-on.caddy" 'dj.{$SITE_DOMAIN} {' >"$WORK/dj-block.txt"
printf '%s\n' '    handle /internal/* {' '        respond 404' '    }' '    handle {' \
  '        redir https://{$SITE_DOMAIN}/djcms/preview/?next={uri} 302' '    }' | cmp -s - "$WORK/dj-block.txt"
check "blok dj. (PRIMARY=0): odmowa /internal/* + 302 na https://{\$SITE_DOMAIN}/djcms/preview/?next={uri}" $?

# 17. Włączony razem z EXTRA_DOMAINS: odmowa w każdym bloku aplikacji, przekierowanie www bez niej.
render "$DJ_EXTRA" "$WORK/dj-extra.caddy" "" "1"
check "generator kończy się sukcesem przy EXTRA_DOMAINS + DJCMS_ENABLED=1" $?
for head in 'olimpiadafizyczna.pl {' 'konkurs.example {'; do
  [ "$(guards_in "$WORK/dj-extra.caddy" "$head")" -eq 1 ]
  check "odmowa /internal/* w bloku „$head” z EXTRA_DOMAINS" $?
done
[ "$(guards_in "$WORK/dj-extra.caddy" 'www.olimpiadafizyczna.pl {')" -eq 0 ]
check "przekierowanie www z EXTRA_DOMAINS bez odmowy (redir i tak wykonuje się pierwsze)" $?
occurrences=$(grep -c 'handle /internal/\*' "$WORK/dj-extra.caddy")
[ "$occurrences" -eq 4 ]
check "odmowa /internal/*: domena główna + 2 z EXTRA_DOMAINS + dj. (jest $occurrences)" $?
render "$DJ_EXTRA" "$WORK/dj-extra-off.caddy" "" ""
strip_djcms "$WORK/dj-extra.caddy" 1 | cmp -s - "$WORK/dj-extra-off.caddy"
check "z EXTRA_DOMAINS: wynik włączony minus wstawki djcms = wynik wyłączony" $?

# 18. Oba przełączniki: odmowa w bloku głównym **raz** (jedna wstawka awk), `dj.` za blokiem `*.`.
render "$DJ_EXTRA" "$WORK/dj-both.caddy" "1" "1"
check "generator kończy się sukcesem przy PLATFORM_SUBDOMAINS=1 + DJCMS_ENABLED=1 + EXTRA_DOMAINS" $?
[ "$(guards_in "$WORK/dj-both.caddy" '{$SITE_DOMAIN} {')" -eq 1 ]
check "przy obu przełącznikach odmowa w bloku domeny głównej dokładnie raz" $?
occurrences=$(grep -c 'handle /internal/\*' "$WORK/dj-both.caddy")
[ "$occurrences" -eq 5 ]
check "odmowa /internal/*: główna + 2 z EXTRA_DOMAINS + *. + dj. (jest $occurrences)" $?
[ "$(grep -c 'ask http://web:8000/internal/tls-allowed' "$WORK/dj-both.caddy")" -eq 1 ]
check "opcja on_demand_tls nadal dokładnie raz" $?
[ "$(grep -n '^dj\.{\$SITE_DOMAIN} {$' "$WORK/dj-both.caddy" | cut -d: -f1)" -gt \
  "$(grep -n '^\*\.{\$SITE_DOMAIN} {$' "$WORK/dj-both.caddy" | cut -d: -f1)" ]
check "blok dj. stoi za blokiem wieloznacznym" $?
block_body "$WORK/dj-both.caddy" 'dj.{$SITE_DOMAIN} {' | grep -qx '        key_type p256' &&
  ! block_body "$WORK/dj-on.caddy" 'dj.{$SITE_DOMAIN} {' | grep -q 'key_type'
check "blok dj.: przypięcie polityki TLS przy subdomenach platformy, bez nich – nie" $?
strip_djcms "$WORK/dj-both.caddy" 0 | cmp -s - <(strip_djcms "$WORK/sub-extra.caddy" 0)
check "przy subdomenach: wynik z DJCMS minus wstawki djcms = wynik bez DJCMS" $?
render "$DJ_EXTRA" "$WORK/dj-both-true.caddy" "true" "TRUE"
cmp -s "$WORK/dj-both.caddy" "$WORK/dj-both-true.caddy"
check "DJCMS_ENABLED=TRUE daje to samo, co =1" $?

# --- DJCMS_PRIMARY i sekcja tras djcms (docs/tasks/DJ-02.md § 3) ----------------------------------

ROUTES_ENV="$ROOT/backend/djcms_contract/app_routes.env"
contract() { sed -n "s/^$1='\(.*\)'\$/\1/p" "$ROUTES_ENV" | tail -n 1 | tr -d '\r'; }
C_APP_RE="$(contract APP_RE)"
C_APP_RE_PREFIXED="$(contract APP_RE_PREFIXED)"

# 20. DJCMS_PRIMARY wyłączony (pusto, 0, false, off, no) = ten sam plik; zła wartość = odmowa;
#     PRIMARY=1 bez DJCMS_ENABLED=1 = odmowa (i przy każdej wyłączonej wartości DJCMS_ENABLED).
for off in "0" "false" "off" "no" "OFF"; do
  render "$DJ_EXTRA" "$WORK/pr-off.caddy" "1" "1" "$off"
  cmp -s "$WORK/dj-both.caddy" "$WORK/pr-off.caddy"
  check "DJCMS_PRIMARY=„$off” = DJCMS_PRIMARY pusty (tryb preview)" $?
done
for bad in "tak" "1;rm" "2" "primary"; do
  render "" "$WORK/pr-bad.caddy" "" "1" "$bad"
  status=$?
  [ "$status" -ne 0 ]
  check "generator odmawia dla DJCMS_PRIMARY=„$bad”" $?
done
render "" "$WORK/pr-keep.caddy" "" "" ""
for dj_off in "" "0" "false"; do
  render "" "$WORK/pr-keep.caddy" "" "$dj_off" "1"
  status=$?
  [ "$status" -ne 0 ] && grep -qF 'DJCMS_PRIMARY=1 wymaga DJCMS_ENABLED=1' "$WORK/stderr" && cmp -s "$SRC" "$WORK/pr-keep.caddy"
  check "DJCMS_PRIMARY=1 przy DJCMS_ENABLED=„$dj_off” – odmowa, poprzedni plik nietknięty" $?
done
# Wyłączony DJCMS_ENABLED z jawnym PRIMARY=0 – nadal kopia co do bajtu (kontrakt DJ-01 bez zmian).
render "" "$WORK/pr-zero.caddy" "" "0" "0"
cmp -s "$SRC" "$WORK/pr-zero.caddy"
check "DJCMS_ENABLED=0 + DJCMS_PRIMARY=0 daje kopię deploy/Caddyfile bajt w bajt" $?

# 21. Kontrakt tras: przy wyłączonym przełączniku nie jest nawet czytany; przy włączonym brak albo
#     wartość ze znakiem, który w Caddyfile'u jest składnią – odmowa.
DJCMS_ROUTES_ENV="$WORK/nie-ma.env" render "" "$WORK/ct-off.caddy" "" "" ""
cmp -s "$SRC" "$WORK/ct-off.caddy"
check "bez DJCMS_ENABLED brak kontraktu tras niczego nie zmienia" $?
DJCMS_ROUTES_ENV="$WORK/nie-ma.env" render "" "$WORK/ct-miss.caddy" "" "1" ""
status=$?
[ "$status" -ne 0 ] && grep -qF 'brak kontraktu tras' "$WORK/stderr"
check "DJCMS_ENABLED=1 bez kontraktu tras – odmowa" $?
for bad_re in "^/(?:a|b) c\$" "^/a{2}\$" "^/a\"b" "/bez-kotwicy" "^/a#b" ""; do
  { echo "APP_RE='$bad_re'"; echo "APP_RE_PREFIXED='^/[^/]+/x\$'"; } >"$WORK/bad-routes.env"
  DJCMS_ROUTES_ENV="$WORK/bad-routes.env" render "" "$WORK/ct-bad.caddy" "" "1" ""
  status=$?
  [ "$status" -ne 0 ]
  check "kontrakt z APP_RE=„$bad_re” – odmowa" $?
done

# 22. Sekcja tras w KAŻDYM bloku aplikacji i w żadnym innym; ta sama treść (poza wyrażeniami domeny
#     głównej); wyrażenia wprost z kontraktu (domena główna: APP_RE|APP_RE_PREFIXED i `/<seg>/djcms`).
section_of() {  # section_of <plik> <nagłówek bloku> – sekcja djcms z bloku (bez linijek z wyrażeniami)
  block_body "$1" "$2" | awk '/^    # >>> django CMS / {on=1} on {print} /^    # <<< django CMS$/ {on=0}'
}
[ "$(grep -c '^    # >>> django CMS ' "$WORK/dj-both.caddy")" -eq 4 ] &&
  [ "$(grep -c '^    # <<< django CMS$' "$WORK/dj-both.caddy")" -eq 4 ]
check "sekcja djcms w 4 blokach aplikacji (główna, 2 × EXTRA_DOMAINS, *.)" $?
for head in 'www.{$SITE_DOMAIN} {' 'www.olimpiadafizyczna.pl {' 'meet.{$SITE_DOMAIN} {' 'monitor.{$SITE_DOMAIN} {' \
            '{$S3_PUBLIC_ADDRESS} {' 'dj.{$SITE_DOMAIN} {'; do
  ! block_body "$WORK/dj-both.caddy" "$head" | grep -qE '>>> django CMS|@djcms_|djcms:8000'
  check "blok „$head” bez sekcji djcms" $?
done
section_of "$WORK/dj-both.caddy" 'konkurs.example {' | grep -vE '^    @djcms_(own|app) ' >"$WORK/sec-ref.txt"
[ -s "$WORK/sec-ref.txt" ]
check "sekcja djcms niepusta" $?
for head in '{$SITE_DOMAIN} {' 'olimpiadafizyczna.pl {' '*.{$SITE_DOMAIN} {'; do
  section_of "$WORK/dj-both.caddy" "$head" | grep -vE '^    @djcms_(own|app) ' | cmp -s - "$WORK/sec-ref.txt"
  check "sekcja djcms w bloku „$head” = w bloku konkurs.example (poza wyrażeniami)" $?
done
re_line() { block_body "$1" "$2" | sed -n "s/^    @djcms_$3 path_regexp //p"; }
[ "$(re_line "$WORK/dj-both.caddy" '{$SITE_DOMAIN} {' app)" = "$C_APP_RE|$C_APP_RE_PREFIXED" ] &&
  [ "$(re_line "$WORK/dj-both.caddy" '{$SITE_DOMAIN} {' own)" = '^/(?:[^/]+/)?djcms(?:/.*)?$' ]
check "domena główna: adresy aplikacji = APP_RE|APP_RE_PREFIXED z kontraktu, djcms także pod /<prefiks>/" $?
ok_re=0
for head in 'olimpiadafizyczna.pl {' 'konkurs.example {' '*.{$SITE_DOMAIN} {'; do
  [ "$(re_line "$WORK/dj-both.caddy" "$head" app)" = "$C_APP_RE" ] &&
    [ "$(re_line "$WORK/dj-both.caddy" "$head" own)" = '^/djcms(?:/.*)?$' ] || ok_re=1
done
check "EXTRA_DOMAINS i *.: adresy aplikacji = samo APP_RE (bez prefiksu), djcms tylko /djcms" $ok_re
# Kolejność w bloku: /static/* przed sekcją, sekcja przed domyślnym `handle` (web).
block_body "$WORK/dj-both.caddy" '{$SITE_DOMAIN} {' | awk '
  $0 == "    handle_path /static/* {" { s = NR }
  /^    # >>> django CMS / { a = NR }
  $0 == "    # <<< django CMS" { b = NR }
  $0 == "    handle {" { h = NR }
  END { exit !(s && a && b && h && s < a && a < b && b < h) }'
check "domena główna: /static/* → sekcja djcms → domyślny handle (web)" $?
grep -qF '    request_header -X-Djcms-Mode' "$WORK/sec-ref.txt" &&
  grep -qF '    header @djcms_vary +Vary Cookie' "$WORK/sec-ref.txt" &&
  grep -qxF '    @djcms_vary not path /static/* /djcms/static/* /djcms/media/*' "$WORK/sec-ref.txt" &&
  grep -qxF '        not path_regexp (?i)\.pdf$' "$WORK/sec-ref.txt" &&
  grep -qxF '        root * /srv/djcms-media' "$WORK/sec-ref.txt" &&
  [ "$(grep -c '^            header_up X-Djcms-Mode preview$' "$WORK/sec-ref.txt")" -eq 2 ] &&
  grep -qxF '    @djcms_public expression `{http.request.cookie.djcms_view} == "dj"`' "$WORK/sec-ref.txt"
check "sekcja (PRIMARY=0): nagłówek trybu zdjęty i nadany (preview), Vary: Cookie, media z CSP, ciasteczko dj" $?

# 23. PRIMARY=1: jedyne różnice wobec PRIMARY=0 – tryb w nagłówku, warunek ciasteczka, opis sekcji
#     i cel przekierowania `dj.` (podgląd sprawdza dokładnie konfigurację produkcyjną, D1).
render "$DJ_EXTRA" "$WORK/pr-on.caddy" "1" "1" "1"
check "generator kończy się sukcesem przy DJCMS_PRIMARY=1 (+ subdomeny, EXTRA_DOMAINS)" $?
grep -qF 'DJCMS_PRIMARY=1 (primary)' "$WORK/stdout"
check "podsumowanie generatora podaje tryb primary" $?
diff "$WORK/dj-both.caddy" "$WORK/pr-on.caddy" | grep -E '^[<>]' | sed 's/^\([<>]\) */\1/' | sort | uniq -c \
  | sed 's/^ *//' >"$WORK/pr-diff.txt"
cat >"$WORK/pr-diff-exp.txt" <<'EOF'
4 <# >>> django CMS (docs/tasks/DJ-02.md § 3), DJCMS_PRIMARY=0 – strony publiczne: web, z ciasteczkiem djcms_view=dj – djcms.
4 <@djcms_public expression `{http.request.cookie.djcms_view} == "dj"`
8 <header_up X-Djcms-Mode preview
1 <redir https://{$SITE_DOMAIN}/djcms/preview/?next={uri} 302
4 ># >>> django CMS (docs/tasks/DJ-02.md § 3), DJCMS_PRIMARY=1 – strony publiczne: djcms, z ciasteczkiem djcms_view=wagtail – web.
4 >@djcms_public expression `{http.request.cookie.djcms_view} != "wagtail"`
8 >header_up X-Djcms-Mode primary
1 >redir https://{$SITE_DOMAIN}{uri} 302
EOF
sort -k2 "$WORK/pr-diff-exp.txt" | cmp -s - <(sort -k2 "$WORK/pr-diff.txt")
rc=$?
check "PRIMARY=1 różni się od PRIMARY=0 wyłącznie trybem, warunkiem ciasteczka i celem dj." $rc
[ $rc -eq 0 ] || diff "$WORK/pr-diff-exp.txt" "$WORK/pr-diff.txt" | sed 's/^/     /'
strip_djcms "$WORK/pr-on.caddy" 0 | cmp -s - <(strip_djcms "$WORK/sub-extra.caddy" 0)
check "PRIMARY=1 minus wstawki djcms = wynik bez DJCMS" $?

# 24. Zapis w miejscu (ten sam i-węzeł): `proxy` montuje plik pojedynczo, a nowy plik (mv, sed -i)
#     nie dotarłby do kontenera – `caddy reload` w djcms_switch.sh przeładowałby starą treść.
render "" "$WORK/inode.caddy" "" "1" "0"
if ln "$WORK/inode.caddy" "$WORK/inode-link.caddy" 2>/dev/null; then
  render "" "$WORK/inode.caddy" "" "1" "1"
  grep -qF 'header_up X-Djcms-Mode primary' "$WORK/inode-link.caddy"
  check "generator pisze w miejscu (twarde dowiązanie widzi nową treść)" $?
else
  printf 'skip zapis w miejscu (system plików bez twardych dowiązań)\n'
fi

# 25. Blok S3 (`{$S3_PUBLIC_ADDRESS}`, audyt z 1.10.2026): odmowa API MinIO spod /minio/* poza sondami
#     życia, nagłówki bezpieczeństwa z `defer` (po nagłówkach MinIO), CSP `sandbox` poza PDF-em,
#     a zwykłe ścieżki bucketów – do MinIO. Działanie na żywym Caddym: scripts/tests/s3_proxy_test.sh
#     (wołany niżej, przy dostępnym Dockerze).
block_body "$SRC" '{$S3_PUBLIC_ADDRESS} {' >"$WORK/s3-block.txt"
for needle in '    @s3_minio_api {' \
              '        path /minio /minio/*' \
              '        not path /minio/health/live /minio/health/ready' \
              '    handle @s3_minio_api {' \
              '        respond 404' \
              '        defer' \
              '        X-Content-Type-Options "nosniff"' \
              '        Strict-Transport-Security "max-age=31536000"' \
              '    @s3_active_content not path_regexp (?i)\.pdf$' \
              "        Content-Security-Policy \"default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox\"" \
              '        reverse_proxy minio:9000'; do
  grep -qxF -- "$needle" "$WORK/s3-block.txt"
  check "blok S3 zawiera linijkę „${needle#"${needle%%[! ]*}"}”" $?
done
# Obie dyrektywy `header` z `defer` (bez niego MinIO dokłada własne HSTS z includeSubDomains i CSP).
[ "$(grep -cxF '        defer' "$WORK/s3-block.txt")" -eq 2 ]
check "blok S3: oba bloki header z defer" $?
# Odmowa przed proxy: `handle` z odmową stoi przed `handle` z reverse_proxy (bloki rozłączne,
# wygrywa pierwszy pasujący), a reverse_proxy nie stoi gołe poza `handle`.
awk '$0 == "    handle @s3_minio_api {" { d = NR } $0 == "        reverse_proxy minio:9000" { p = NR } $0 == "    reverse_proxy minio:9000" { bare = 1 }
     END { exit !(d && p && d < p && !bare) }' "$WORK/s3-block.txt"
check "blok S3: odmowa /minio/* przed reverse_proxy, proxy wyłącznie w handle" $?
# Ten sam blok (z tymi samymi regułami) w każdym wariancie generatora – przełączniki go nie ruszają.
for f in "$WORK/sub-extra.caddy" "$WORK/dj-both.caddy" "$WORK/pr-on.caddy"; do
  block_body "$f" '{$S3_PUBLIC_ADDRESS} {' | grep -vE '^    # Zwykły certyfikat |^    tls \{$|^        key_type p256$|^    \}$' \
    | cmp -s - <(grep -vE '^    \}$' "$WORK/s3-block.txt")
  check "blok S3 w ${f##*/} = blok z deploy/Caddyfile (poza przypięciem TLS)" $?
done

# 19. Caddy sam: `caddy validate` i kolejność tras po `caddy adapt` (obraz z docker-compose.yml).
# Pomijane bez Dockera albo przy SKIP_CADDY_VALIDATE=1 – reszta testu nie potrzebuje sieci.
CADDY_IMAGE="${CADDY_IMAGE:-$(sed -n 's/^    image: \(caddy:.*\)$/\1/p' "$ROOT/docker-compose.yml" | head -1)}"
if [ "${SKIP_CADDY_VALIDATE:-0}" != "1" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  caddy_run() {  # caddy_run <plik> <polecenie caddy…>; plik na stdin, bez montowania (Windows)
    local file="$1"; shift
    MSYS_NO_PATHCONV=1 docker run --rm -i \
      -e SITE_DOMAIN=example.org -e S3_PUBLIC_ADDRESS="${CADDY_S3:-example.org:9000}" -e ACME_EMAIL=ops@example.org \
      -e MAX_UPLOAD_MB=25 -e MAINTENANCE_BYPASS_TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCD \
      "$CADDY_IMAGE" sh -c "cat > /tmp/Caddyfile && caddy $* --config /tmp/Caddyfile --adapter caddyfile" <"$file"
  }
  for f in "$SRC" "$WORK/sub-extra.caddy" "$WORK/dj-on.caddy" "$WORK/dj-extra.caddy" "$WORK/dj-both.caddy" "$WORK/pr-on.caddy"; do
    caddy_run "$f" validate >"$WORK/validate.out" 2>&1
    rc=$?   # osobno: podstawienie $(…) w opisie nadpisałoby kod wyniku
    check "caddy validate ($CADDY_IMAGE): ${f##*/}" "$rc"
  done
  caddy_run "$WORK/sub-extra.caddy" adapt 2>/dev/null | grep '^{' >"$WORK/adapt.json"
  # Kolejność w bloku domeny głównej: przerwa (matcher `file` /on) przed `reverse_proxy` i przed
  # /static/*; w trasach błędów – wariant JSON przed stroną HTML.
  # Pierwszy interpreter, który naprawdę działa (na Windows `python3` bywa atrapą ze Sklepu).
  python_bin=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import json' >/dev/null 2>&1; then
      python_bin="$candidate"; break
    fi
  done
  if [ -z "$python_bin" ]; then
    printf 'skip kolejność tras po caddy adapt (brak Pythona)\n'
  else
    "$python_bin" - "$WORK/adapt.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
srv = next(s for s in cfg["apps"]["http"]["servers"].values() if ":443" in s["listen"])
def sub(host, routes):
    for r in routes:
        if any(host in m.get("host", []) for m in r.get("match", [])):
            return r["handle"][0]["routes"]
    raise SystemExit(f"brak bloku {host}")
errors = 0
for host in ("example.org", "olimpiadafizyczna.pl", "konkurs.example"):
    routes = [json.dumps(r.get("match")) + json.dumps(r["handle"]) for r in sub(host, srv["routes"])]
    idx = lambda needle: next((i for i, r in enumerate(routes) if needle in r), None)
    m, p, s = idx('"try_files": ["/on"]'), idx('reverse_proxy'), idx('"/static/*"')
    if None in (m, p, s) or not (m < s < p):
        print(f"FAIL {host}: kolejność przerwa={m} static={s} proxy={p}"); errors += 1
    err = json.dumps(sub(host, srv["errors"]["routes"]))
    if not (0 <= err.find('"/status.json"') < err.find('"templates"')):
        print(f"FAIL {host}: w trasach błędów brak JSON przed stroną HTML"); errors += 1
for host in ("meet.example.org", "monitor.example.org"):
    if "try_files" in json.dumps(sub(host, srv["routes"])):
        print(f"FAIL {host}: ma stronę prac technicznych"); errors += 1
sys.exit(errors)
PY
    check "caddy adapt: przerwa przed /static/* i aplikacją, JSON przed HTML, meet./monitor. bez przerwy" $?

    # Sekcja djcms po `caddy adapt` (oba przełączniki + EXTRA_DOMAINS, oba tryby). Caddy sortuje
    # `handle` o jednej ścieżce po jej długości, więc kolejność zapisu to nie wszystko – sprawdzamy
    # wynik: w KAŻDYM bloku aplikacji przerwa → pliki redaktorów → /internal/* i /static/* →
    # adresy djcms → adresy aplikacji → strony publiczne → domyślny handle (web); nagłówek trybu
    # zdjęty w trasie bez matchera (przed routingiem). `dj.` przed `*.`, w nim odmowa przed 302.
    for f in dj-both pr-on; do
      caddy_run "$WORK/$f.caddy" adapt 2>/dev/null | grep '^{' >"$WORK/adapt-$f.json"
      "$python_bin" - "$WORK/adapt-$f.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
srv = next(s for s in cfg["apps"]["http"]["servers"].values() if ":443" in s["listen"])
def pos(host):
    return next((i for i, r in enumerate(srv["routes"])
                 if any(host in m.get("host", []) for m in r.get("match", []))), None)
errors = 0
for host in ("example.org", "olimpiadafizyczna.pl", "konkurs.example", "*.example.org"):
    routes = srv["routes"][pos(host)]["handle"][0]["routes"]
    txt = [json.dumps(r.get("match")) + "|" + json.dumps(r["handle"]) for r in routes]
    def idx(pred):
        return next((i for i, t in enumerate(txt) if pred(t)), None)
    order = {
        "przerwa": idx(lambda t: '"try_files": ["/on"]' in t),
        "media": idx(lambda t: t.startswith('[{"path": ["/djcms/media/*"]}]')),
        "internal": idx(lambda t: '"/internal/*"' in t and "static_response" in t),
        "static": idx(lambda t: t.startswith('[{"path": ["/static/*"]}]')),
        "djcms": idx(lambda t: '"name": "djcms_own"' in t),
        "app": idx(lambda t: '"name": "djcms_app"' in t),
        "public": idx(lambda t: '"name": "djcms_public"' in t),
        "web": idx(lambda t: t.startswith("null|") and '"web:8000"' in t),
    }
    if None in order.values():
        print(f"FAIL {host}: brak trasy {[k for k, v in order.items() if v is None]}"); errors += 1
        continue
    if not (order["media"] < order["djcms"] < order["app"] < order["public"] < order["web"]
            and order["static"] < order["djcms"] and order["internal"] < order["djcms"]
            and order["przerwa"] < order["media"]):
        print(f"FAIL {host}: kolejność {order}"); errors += 1
    up = lambda k: json.dumps(routes[order[k]]["handle"])
    if '"djcms:8000"' not in up("djcms") or '"djcms:8000"' not in up("public") or '"web:8000"' not in up("app"):
        print(f"FAIL {host}: upstreamy sekcji"); errors += 1
    strip = [t for t in txt if t.startswith("null|") and '"delete": ["X-Djcms-Mode"]' in t]
    if not strip or txt.index(strip[0]) > order["przerwa"]:
        print(f"FAIL {host}: X-Djcms-Mode nie jest zdejmowany przed routingiem"); errors += 1
dj, wild = pos("dj.example.org"), pos("*.example.org")
if dj is None or wild is None or not dj < wild:
    print(f"FAIL kolejność witryn: dj.={dj} *.={wild}"); errors += 1
else:
    routes = [json.dumps(r.get("match")) + json.dumps(r["handle"]) for r in srv["routes"][dj]["handle"][0]["routes"]]
    whole = json.dumps(srv["routes"][dj])
    if len(routes) != 2 or '"/internal/*"' not in routes[0] or '"Location"' not in routes[1] or "302" not in routes[1] or "reverse_proxy" in whole or "try_files" in whole:
        print(f"FAIL dj.: {routes}"); errors += 1
main = json.dumps(srv["routes"][pos("example.org")])
n = main.count('"/internal/*"')
if n != 1:
    print(f"FAIL example.org: odmowa /internal/* {n} razy"); errors += 1
sys.exit(errors)
PY
      check "caddy adapt ($f): w każdym bloku aplikacji media → /internal,/static → djcms → aplikacja → publiczne → web; dj. = odmowa + 302" $?
    done

    # Polityki TLS przy PLATFORM_SUBDOMAINS=1: nazwy dosłowne (www., meet., monitor., s3., dj.,
    # domena główna, EXTRA_DOMAINS) – zwykły certyfikat; on-demand (z `ask`) WYŁĄCZNIE nieznane
    # subdomeny z bloku `*.`. Caddy bierze PIERWSZĄ politykę, której `subjects` pasują do nazwy
    # (wzorzec `*.` = jedna etykieta, brak `subjects` = każda nazwa) – ten sam dobór sprawdza skrypt.
    # Dwa warianty wystawcy: ACME (produkcja: `email`) i `local_certs` (E2E) – przy tym drugim
    # adapter Caddy'ego 2.8 bez przypięcia z render_caddyfile.sh wcinał www./dj./… do polityki
    # domyślnej za `*.` (on-demand, `ask` odmawia – brak certyfikatu). S3 pod `s3.` i pod host:port.
    for f in sub-extra dj-both; do
      awk '{ print } $0 == "    email {$ACME_EMAIL}" { print "    local_certs"; print "    skip_install_trust" }' \
        "$WORK/$f.caddy" >"$WORK/$f-local.caddy"
      for variant in "$f" "$f-local"; do
        for s3 in example.org:9000 s3.example.org; do
          CADDY_S3="$s3" caddy_run "$WORK/$variant.caddy" adapt 2>/dev/null | grep '^{' >"$WORK/tls-$variant.json"
          names="www.example.org meet.example.org monitor.example.org example.org olimpiadafizyczna.pl konkurs.example"
          [ "$s3" = s3.example.org ] && names="$names s3.example.org"
          [ "$f" = dj-both ] && names="$names dj.example.org"
          "$python_bin" - "$WORK/tls-$variant.json" "$names" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
pols = cfg["apps"]["tls"]["automation"]["policies"]
def matches(name, subj):  # certmagic.MatchWildcard: dokładnie albo `*.` = jedna etykieta
    if name == subj:
        return True
    if subj.startswith("*."):
        return "." in name and name.split(".", 1)[1] == subj[2:]
    return False
def policy(name):
    return next((p for p in pols if not p.get("subjects") or any(matches(name, s) for s in p["subjects"])), None)
errors = 0
for name in sys.argv[2].split():
    p = policy(name)
    if p is None or p.get("on_demand"):
        print(f"FAIL {name}: polityka {json.dumps(p)}"); errors += 1
p = policy("nowy-konkurs.example.org")
if p is None or not p.get("on_demand"):
    print(f"FAIL nowy-konkurs.example.org (blok *.): polityka bez on_demand {json.dumps(p)}"); errors += 1
if cfg["apps"]["tls"]["automation"].get("on_demand", {}).get("permission", {}).get("endpoint") != "http://web:8000/internal/tls-allowed":
    print("FAIL on_demand bez endpointu zgody"); errors += 1
sys.exit(errors)
PY
          check "caddy adapt ($variant, S3=$s3): nazwy dosłowne – zwykły certyfikat, on-demand tylko nieznane subdomeny *." $?
        done
      done
    done
  fi

  # Działający Caddy: cała tablica tras § 3 (host × ścieżka × ciasteczko × tryb) na atrapach
  # upstreamów – osobny skrypt, bo jest długi i przydaje się też sam.
  if [ "${SKIP_DJCMS_ROUTING:-0}" != "1" ]; then
    bash "$ROOT/scripts/tests/djcms_routing_test.sh" >"$WORK/routing.out" 2>&1
    rc=$?
    check "scripts/tests/djcms_routing_test.sh (żywy Caddy, tablica tras djcms, $(grep -c '^ok ' "$WORK/routing.out") przypadków)" $rc
    [ $rc -eq 0 ] || grep -vE '^ok ' "$WORK/routing.out" | sed 's/^/     /'
  fi
  # Blok S3 na żywym Caddym z atrapą MinIO (§ 25 wyżej – tu działanie, tam treść pliku).
  if [ "${SKIP_S3_PROXY:-0}" != "1" ]; then
    bash "$ROOT/scripts/tests/s3_proxy_test.sh" >"$WORK/s3proxy.out" 2>&1
    rc=$?
    check "scripts/tests/s3_proxy_test.sh (żywy Caddy, blok S3, $(grep -c '^ok ' "$WORK/s3proxy.out") przypadków)" $rc
    [ $rc -eq 0 ] || grep -vE '^ok ' "$WORK/s3proxy.out" | sed 's/^/     /'
  fi
else
  printf 'skip caddy validate/adapt (brak Dockera albo SKIP_CADDY_VALIDATE=1)\n'
fi

# LIVEKIT_PROXY (zadanie WEB-01): wyłączony = bajt w bajt jak dotąd; włączony = blok `live.` na końcu
# (z przypiętym zwykłym certyfikatem przy subdomenach platformy); wartość spoza listy = błąd.
render "" "$WORK/lk-off.caddy" "" "" "" "0"
cmp -s "$SRC" "$WORK/lk-off.caddy"
check "LIVEKIT_PROXY=0 daje kopię deploy/Caddyfile bajt w bajt" $?
render "" "$WORK/lk-on.caddy" "" "" "" "1"
grep -qF 'live.{$SITE_DOMAIN} {' "$WORK/lk-on.caddy" && grep -qF 'reverse_proxy livekit:7880' "$WORK/lk-on.caddy" \
  && head -c "$(wc -c <"$SRC")" "$WORK/lk-on.caddy" | cmp -s - "$SRC"
check "LIVEKIT_PROXY=1 dokłada blok live. -> livekit:7880 za blokami źródłowymi" $?
render "" "$WORK/lk-sub.caddy" "1" "" "" "1"
awk '/^live\./,/^}/' "$WORK/lk-sub.caddy" | grep -qF 'key_type p256'
check "LIVEKIT_PROXY=1 przy subdomenach: live. ze zwykłym certyfikatem" $?
render "" "$WORK/lk-bad.caddy" "" "" "" "tak"
[ $? -eq 1 ] && grep -qF 'LIVEKIT_PROXY' "$WORK/stderr"
check "LIVEKIT_PROXY=tak zatrzymuje generator z komunikatem" $?

# ERRORS_PROXY (zadanie OPS-02): wyłączony = bajt w bajt jak dotąd; włączony = blok `errors.` na końcu
# (GlitchTip, bez strony prac technicznych, z nagłówkami bezpieczeństwa), przy subdomenach – zwykły
# certyfikat; razem z LIVEKIT_PROXY oba bloki; wartość spoza listy = błąd.
render "" "$WORK/err-off.caddy" "" "" "" "" "0"
cmp -s "$SRC" "$WORK/err-off.caddy"
check "ERRORS_PROXY=0 daje kopię deploy/Caddyfile bajt w bajt" $?
render "" "$WORK/err-on.caddy" "" "" "" "" "1"
awk '/^errors\./,/^}/' "$WORK/err-on.caddy" >"$WORK/err-block.txt"
grep -qxF 'errors.{$SITE_DOMAIN} {' "$WORK/err-block.txt" && grep -qxF '    reverse_proxy glitchtip:8000 {' "$WORK/err-block.txt" \
  && head -c "$(wc -c <"$SRC")" "$WORK/err-on.caddy" | cmp -s - "$SRC"
check "ERRORS_PROXY=1 dokłada blok errors. -> glitchtip:8000 za blokami źródłowymi" $?
for needle in 'Strict-Transport-Security "max-age=31536000"' 'X-Content-Type-Options "nosniff"' 'X-Frame-Options "DENY"' 'max_size 10MB'; do
  grep -qF "$needle" "$WORK/err-block.txt"
  check "blok errors. zawiera „$needle”" $?
done
! grep -qE 'import maintenance|key_type' "$WORK/err-block.txt"
check "blok errors. bez strony prac technicznych i bez przypięcia TLS (subdomeny wyłączone)" $?
render "" "$WORK/err-sub.caddy" "1" "" "" "1" "1"
awk '/^errors\./,/^}/' "$WORK/err-sub.caddy" | grep -qF 'key_type p256' && grep -qF 'live.{$SITE_DOMAIN} {' "$WORK/err-sub.caddy"
check "ERRORS_PROXY=1 przy subdomenach i LiveKit: errors. ze zwykłym certyfikatem, live. obok" $?
render "" "$WORK/err-bad.caddy" "" "" "" "" "tak"
[ $? -eq 1 ] && grep -qF 'ERRORS_PROXY' "$WORK/stderr"
check "ERRORS_PROXY=tak zatrzymuje generator z komunikatem" $?
if [ "${SKIP_CADDY_VALIDATE:-0}" != "1" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for f in "$WORK/err-on.caddy" "$WORK/err-sub.caddy"; do
    caddy_run "$f" validate >"$WORK/validate.out" 2>&1
    rc=$?
    check "caddy validate ($CADDY_IMAGE): ${f##*/}" "$rc"
  done
fi

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy generatora Caddyfile przeszły.\n'
