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
# proxy działającej produkcji (docs/UNIWERSALNY-ETAP-1.md § 0).
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
  # render "<EXTRA_DOMAINS>" <plik-wyjściowy> ["<PLATFORM_SUBDOMAINS>"]; zwraca kod wyjścia generatora.
  # Trzeci argument jest **zawsze** przekazywany (choćby pusty), bo zmienna nieustawiona każe
  # generatorowi czytać `.env` – a test ma sprawdzać generator, a nie czyjś plik konfiguracyjny.
  EXTRA_DOMAINS="$1" PLATFORM_SUBDOMAINS="${3:-}" CADDYFILE_OUT="$2" bash "$RENDER" >"$WORK/stdout" 2>"$WORK/stderr"
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

# 11. Wyłączony przełącznik przy obecnych EXTRA_DOMAINS: ani śladu po on-demand TLS.
render "olimpiadafizyczna.pl" "$WORK/sub-off-extra.caddy" ""
! grep -qE 'on_demand|/internal/' "$WORK/sub-off-extra.caddy"
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

# 14. Caddy sam: `caddy validate` i kolejność tras po `caddy adapt` (obraz z docker-compose.yml).
# Pomijane bez Dockera albo przy SKIP_CADDY_VALIDATE=1 – reszta testu nie potrzebuje sieci.
CADDY_IMAGE="${CADDY_IMAGE:-$(sed -n 's/^    image: \(caddy:.*\)$/\1/p' "$ROOT/docker-compose.yml" | head -1)}"
if [ "${SKIP_CADDY_VALIDATE:-0}" != "1" ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  caddy_run() {  # caddy_run <plik> <polecenie caddy…>; plik na stdin, bez montowania (Windows)
    local file="$1"; shift
    MSYS_NO_PATHCONV=1 docker run --rm -i \
      -e SITE_DOMAIN=example.org -e S3_PUBLIC_ADDRESS=example.org:9000 -e ACME_EMAIL=ops@example.org \
      -e MAX_UPLOAD_MB=25 -e MAINTENANCE_BYPASS_TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCD \
      "$CADDY_IMAGE" sh -c "cat > /tmp/Caddyfile && caddy $* --config /tmp/Caddyfile --adapter caddyfile" <"$file"
  }
  for f in "$SRC" "$WORK/sub-extra.caddy"; do
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
  fi
else
  printf 'skip caddy validate/adapt (brak Dockera albo SKIP_CADDY_VALIDATE=1)\n'
fi

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy generatora Caddyfile przeszły.\n'
