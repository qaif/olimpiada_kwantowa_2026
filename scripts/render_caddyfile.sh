#!/usr/bin/env bash
# Generuje `deploy/Caddyfile.generated` = `deploy/Caddyfile` + blok serwerowy dla każdej domeny
# z `EXTRA_DOMAINS`. Uruchamia go `scripts/deploy.sh` (krok 4/8); ręcznie po każdej zmianie
# `EXTRA_DOMAINS` w `.env`, a potem `docker compose up -d proxy`.
#
# Dlaczego generator, a nie sama konfiguracja Caddy'ego: adres bloku serwerowego jest w Caddyfile'u
# **składnią**, a nie wartością – po zmiennej środowiskowej `{$EXTRA_DOMAINS}` nie da się iterować,
# a wpisanie kilku hostów w jedną zmienną (`a.pl b.pl`) daje jeden blok o adresie złożonym z dwóch
# nazw tylko przypadkiem; przy pustej zmiennej daje blok bez adresu i Caddy odmawia startu.
# Generator jest tańszy niż `on_demand_tls`, które wystawiałoby certyfikat dowolnemu hostowi
# wskazującemu nasz adres IP (docs/UNIWERSALNY-ETAP-1.md § 2.5).
#
# Kontrakt, na którym stoi test `scripts/tests/render_caddyfile_test.sh`:
# **przy pustym `EXTRA_DOMAINS` wynik jest bajt w bajt kopią `deploy/Caddyfile`.** Dzięki temu
# instalacja jednokonkursowa – czyli dziś działająca produkcja – dostaje dokładnie tę konfigurację,
# którą ma, a nie „taką samą”.
#
# Użycie:
#   scripts/render_caddyfile.sh                  # EXTRA_DOMAINS ze środowiska albo z ./.env
#   EXTRA_DOMAINS="a.pl www.a.pl" scripts/render_caddyfile.sh
#   CADDYFILE_OUT=/tmp/x scripts/render_caddyfile.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${CADDYFILE_SRC:-$ROOT/deploy/Caddyfile}"
OUT="${CADDYFILE_OUT:-$ROOT/deploy/Caddyfile.generated}"

# Zmienna nieustawiona (a nie „ustawiona na pusto”) = czytamy `.env`. Rozróżnienie jest potrzebne,
# bo `EXTRA_DOMAINS=` podane jawnie znaczy „wyczyść”, a nie „weź z pliku”.
if [ -z "${EXTRA_DOMAINS+x}" ] && [ -f "$ROOT/.env" ]; then
  # `sed`, a nie `source .env`: plik należy do administratora serwera i bywa w nim wszystko,
  # łącznie ze znakami, które powłoka wykonałaby. Czytamy jedną linijkę, ostatnie wystąpienie
  # wygrywa (tak samo interpretuje ten plik docker compose), zdejmujemy cudzysłowy i CR.
  # `tr -d '\r\042\047'`: CR (plik bywa zapisany z Windowsa) oraz cudzysłów i apostrof.
  EXTRA_DOMAINS="$(sed -n 's/^EXTRA_DOMAINS=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
EXTRA_DOMAINS="${EXTRA_DOMAINS:-}"

[ -f "$SRC" ] || { echo "render_caddyfile: brak pliku źródłowego $SRC" >&2; exit 1; }

# Nazwa hosta trafia do pliku konfiguracyjnego jako **składnia**, więc jest sprawdzana, zanim tam
# trafi: znak `{`, `}` albo nowa linia w `EXTRA_DOMAINS` nie byłby literówką, tylko dopisaniem
# reguły do konfiguracji proxy. Dopuszczamy litery, cyfry, kropkę, myślnik i opcjonalny port.
HOST_RE='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'
for host in $EXTRA_DOMAINS; do
  if ! [[ $host =~ $HOST_RE ]]; then
    echo "render_caddyfile: „$host” nie wygląda na nazwę hosta – popraw EXTRA_DOMAINS" >&2
    exit 1
  fi
done

apex_listed() {
  # Czy „goła” domena hosta `www.x` też jest na liście. Od tego zależy, czy `www.x` dostaje blok
  # aplikacji, czy przekierowanie – patrz niżej.
  local apex="${1#www.}"
  for candidate in $EXTRA_DOMAINS; do
    [ "$candidate" = "$apex" ] && return 0
  done
  return 1
}

tmp="$(mktemp "${TMPDIR:-/tmp}/caddyfile.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cat "$SRC" > "$tmp"

added=0
for host in $EXTRA_DOMAINS; do
  if [ "${host#www.}" != "$host" ] && apex_listed "$host"; then
    # `www.x` przy obecnym `x`: przekierowanie 301, dokładnie jak `www.{$SITE_DOMAIN}` w bloku
    # źródłowym. Nie z estetyki – konkurs ma **jeden** adres kanoniczny, a `www` obsłużone jako
    # osobna witryna nie pasowałoby do `Site.hostname` w Wagtailu i oddałoby pod tą nazwą stronę
    # witryny domyślnej, czyli cudzy konkurs.
    cat >> "$tmp" <<EOF

# Wygenerowane przez scripts/render_caddyfile.sh z EXTRA_DOMAINS – nie edytuj tego pliku.
$host {
    redir https://${host#www.}{uri} permanent
}
EOF
  else
    # Blok aplikacji: ten sam co bloku domeny głównej, łącznie z limitem rozmiaru żądania
    # i nagłówkami bezpieczeństwa. Powtórzony, a nie wyciągnięty do wspólnego fragmentu
    # (`import`), bo blok domeny głównej ma zostać w pliku źródłowym literalnie taki, jaki
    # jest dzisiaj – to jest warunek zadania T6 (§ 6).
    cat >> "$tmp" <<EOF

# Wygenerowane przez scripts/render_caddyfile.sh z EXTRA_DOMAINS – nie edytuj tego pliku.
$host {
    encode gzip zstd
    request_body {
        max_size {\$MAX_UPLOAD_MB}MB
    }
    handle_path /static/* {
        root * /srv/static
        file_server
    }
    handle {
        reverse_proxy web:8000 {
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
    }
}
EOF
  fi
  added=$((added + 1))
done

mkdir -p "$(dirname "$OUT")"
cat "$tmp" > "$OUT"
echo "render_caddyfile: $OUT (domen dodatkowych: $added)"
