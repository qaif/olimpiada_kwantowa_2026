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
  # render "<EXTRA_DOMAINS>" <plik-wyjściowy>; zwraca kod wyjścia generatora.
  EXTRA_DOMAINS="$1" CADDYFILE_OUT="$2" bash "$RENDER" >"$WORK/stdout" 2>"$WORK/stderr"
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

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy generatora Caddyfile przeszły.\n'
