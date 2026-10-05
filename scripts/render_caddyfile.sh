#!/usr/bin/env bash
# Generuje `caddy/Caddyfile` = `deploy/Caddyfile` + blok serwerowy dla każdej domeny
# z `EXTRA_DOMAINS`. Katalog `caddy/` montuje proxy (CADDY_CONFIG_DIR=./caddy → /etc/caddy).
# Woła go `scripts/proxy_config.sh` (krok 4/8 wdrożenia) – z walidacją w działającym proxy
# i `caddy reload`; ręcznie po zmianie `EXTRA_DOMAINS` w `.env`: `bash scripts/proxy_config.sh update`.
#
# Dlaczego generator, a nie sama konfiguracja Caddy'ego: adres bloku serwerowego jest w Caddyfile'u
# **składnią**, a nie wartością – po zmiennej środowiskowej `{$EXTRA_DOMAINS}` nie da się iterować,
# a wpisanie kilku hostów w jedną zmienną (`a.pl b.pl`) daje jeden blok o adresie złożonym z dwóch
# nazw tylko przypadkiem; przy pustej zmiennej daje blok bez adresu i Caddy odmawia startu.
# Generator jest tańszy niż `on_demand_tls`, które wystawiałoby certyfikat dowolnemu hostowi
# wskazującemu nasz adres IP (docs/UNIWERSALNY-ETAP-1.md § 2.5).
#
# `PLATFORM_SUBDOMAINS=1` – druga, **wyłączona domyślnie** droga: konkurs zakładany z panelu
# koordynatora dostaje adres `<slug>.<SITE_DOMAIN>` bez wdrożenia i bez wpisu w `.env`. Wtedy (i
# tylko wtedy) generator dokłada blok `*.{$SITE_DOMAIN}` z `tls { on_demand }` oraz opcję globalną
# `on_demand_tls { ask … }`. Zarzut z § 2.5 – „on-demand wystawia certyfikat dowolnemu hostowi” –
# znika dopiero razem z endpointem `ask`: to aplikacja rozstrzyga, czy dana nazwa jest domeną
# aktywnego konkursu, więc limit Let's Encrypt (50 certyfikatów na domenę tygodniowo) zużywają
# wyłącznie konkursy, które naprawdę istnieją. Bez tego przełącznika droga przez `EXTRA_DOMAINS`
# zostaje jedyną i wynik generatora jest dokładnie ten, co dotąd.
#
# `DJCMS_ENABLED=1` – serwis na django CMS (docs/tasks/DJ-01.md § 8.8, DJ-02.md § 3), **wyłączony
# domyślnie**. Wtedy (i tylko wtedy) generator dokłada sekcję tras djcms w blokach aplikacji (niżej),
# na końcu pliku blok `dj.{$SITE_DOMAIN}` oraz odmowę `/internal/*` w bloku domeny głównej i w każdym
# bloku aplikacji z `EXTRA_DOMAINS`: pod `/internal/djcms/` aplikacja główna oddaje djcms-owi dane
# zawodów i ten adres nie może odpowiadać z żadnej nazwy publicznej.
#
# DJ-02 (docs/tasks/DJ-02.md § 3) zmienia rolę djcms: z osobnej witryny `dj.` w **pełny serwis
# publiczny** każdego konkursu, na jego prawdziwym hoście. Przy `DJCMS_ENABLED=1` każdy blok
# aplikacji (domena główna, EXTRA_DOMAINS, `*.`) dostaje sekcję tras djcms (`/djcms/media/*` z
# wolumenu, `/djcms/*` do djcms, adresy aplikacji z kontraktu `backend/djcms_contract/app_routes.env`
# do `web`, reszta – strony publiczne – wg `DJCMS_PRIMARY` i ciasteczka `djcms_view`), a blok `dj.`
# już tylko przekierowuje (302) na stronę włączenia podglądu na domenie głównej. `DJCMS_PRIMARY`:
#   0 (domyślnie) – strony publiczne z `web` (Wagtail); z ciasteczkiem `djcms_view=dj` – z djcms
#                   (`X-Djcms-Mode: preview`, noindex po stronie djcms);
#   1             – strony publiczne z djcms (`X-Djcms-Mode: primary`); z `djcms_view=wagtail` – z `web`.
# Przełącza `scripts/djcms_switch.sh on|off` (render + `caddy reload`, bez restartu kontenerów).
# `DJCMS_PRIMARY=1` bez `DJCMS_ENABLED=1` = błąd (kod 1).
#
# `NOTEBOOK_LAB_HOST=<host>` – laboratorium notatników kwantowych na **osobnym hoście** (QC-02,
# docs/tasks/QC-02.md § 3), **puste domyślnie**. Wtedy (i tylko wtedy) każdy blok aplikacji importuje
# `notebook_lab_moved` zamiast `notebook_lab` (ścieżka laboratorium → 302 na host laboratorium,
# `/notebook-starter/*` → 404), a na końcu pliku staje blok hosta laboratorium: wyłącznie pliki
# laboratorium (z wolumenu statycznego, z fragmentem `(notebook_lab)`) i notatnik startowy z `web`.
# Ta sama zmienna w `.env` steruje Django (config/settings/base.py) – jedno źródło prawdy.
#
# Kontrakt, na którym stoi test `scripts/tests/render_caddyfile_test.sh`:
# **przy pustym `EXTRA_DOMAINS` i wyłączonych `PLATFORM_SUBDOMAINS` oraz `DJCMS_ENABLED` wynik jest
# bajt w bajt kopią `deploy/Caddyfile`.** Dzięki temu instalacja jednokonkursowa – czyli dziś
# działająca produkcja – dostaje dokładnie tę konfigurację, którą ma, a nie „taką samą”. Wyłączony
# `DJCMS_ENABLED` nie zmienia też ani bajtu wyniku przy **dowolnych** wartościach dwóch pozostałych
# zmiennych (ten sam test: wynik z włączonym minus wstawki djcms = wynik z wyłączonym).
#
# Użycie:
#   scripts/render_caddyfile.sh                  # EXTRA_DOMAINS ze środowiska albo z ./.env
#   EXTRA_DOMAINS="a.pl www.a.pl" scripts/render_caddyfile.sh
#   PLATFORM_SUBDOMAINS=1 scripts/render_caddyfile.sh
#   DJCMS_ENABLED=1 scripts/render_caddyfile.sh
#   DJCMS_ENABLED=1 DJCMS_PRIMARY=1 scripts/render_caddyfile.sh
#   ERRORS_PROXY=1 scripts/render_caddyfile.sh      # errors.<domena> → GlitchTip (OPS-02)
#   NOTEBOOK_LAB_HOST=lab.olimpiadakwantowa.pl scripts/render_caddyfile.sh
#   CADDYFILE_OUT=/tmp/x scripts/render_caddyfile.sh
#   DJCMS_ROUTES_ENV=/inny/app_routes.env …      # kontrakt tras (domyślnie backend/djcms_contract/)
#
# Plik wynikowy jest zapisywany **w miejscu** (`cat > "$OUT"`, ten sam i-węzeł). Proxy montuje dziś
# katalog `caddy/` i widzi także plik zastąpiony nowym, ale kontener sprzed tej zmiany (montaż
# pojedynczego pliku, docs/OPERACJE.md § 23) – wyłącznie zapis w miejscu.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${CADDYFILE_SRC:-$ROOT/deploy/Caddyfile}"
OUT="${CADDYFILE_OUT:-$ROOT/caddy/Caddyfile}"

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

# Ten sam sposób odczytu co wyżej: nieustawiona zmienna = bierzemy z `.env`, ustawiona (choćby na
# pusto) = wygrywa środowisko. Dzięki temu test powłoki podaje wartość jawnie i nie zależy od tego,
# co ktoś ma w swoim `.env`.
if [ -z "${PLATFORM_SUBDOMAINS+x}" ] && [ -f "$ROOT/.env" ]; then
  PLATFORM_SUBDOMAINS="$(sed -n 's/^PLATFORM_SUBDOMAINS=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
# Przełącznik decyduje o **składni** konfiguracji proxy, a nie o ustawieniu aplikacji, więc wartości,
# której nie rozumiemy, nie wolno zamienić po cichu na „wyłączone”: kto wpisał do `.env` wartość
# „tak”, ma się dowiedzieć teraz, a nie wtedy, gdy koordynator założy konkurs i dostanie adres
# bez certyfikatu.
case "$(printf '%s' "${PLATFORM_SUBDOMAINS:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
  1|true|yes|on)   SUBDOMAINS_ON=1 ;;
  ''|0|false|no|off) SUBDOMAINS_ON=0 ;;
  *)
    echo "render_caddyfile: nie rozumiem PLATFORM_SUBDOMAINS=„${PLATFORM_SUBDOMAINS:-}” (użyj 1/true albo 0/false)" >&2
    exit 1
    ;;
esac

# `DJCMS_ENABLED` – odczyt i walidacja **identyczne** jak `PLATFORM_SUBDOMAINS` wyżej (DJ-01 § 8.8):
# środowisko wygrywa z `.env`, wartość spoza listy zatrzymuje generator. Ta sama zmienna w `.env`
# steruje wdrożeniem (scripts/deploy.sh) – jedno źródło prawdy o tym, czy `dj.` istnieje.
if [ -z "${DJCMS_ENABLED+x}" ] && [ -f "$ROOT/.env" ]; then
  DJCMS_ENABLED="$(sed -n 's/^DJCMS_ENABLED=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
case "$(printf '%s' "${DJCMS_ENABLED:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
  1|true|yes|on)   DJCMS_ON=1 ;;
  ''|0|false|no|off) DJCMS_ON=0 ;;
  *)
    echo "render_caddyfile: nie rozumiem DJCMS_ENABLED=„${DJCMS_ENABLED:-}” (użyj 1/true albo 0/false)" >&2
    exit 1
    ;;
esac

# `DJCMS_PRIMARY` (DJ-02 § 3) – ten sam odczyt i ta sama walidacja. Włączone bez `DJCMS_ENABLED`
# to sprzeczność (strony publiczne do usługi, której nie ma), a nie „wyłączone” – błąd.
if [ -z "${DJCMS_PRIMARY+x}" ] && [ -f "$ROOT/.env" ]; then
  DJCMS_PRIMARY="$(sed -n 's/^DJCMS_PRIMARY=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
case "$(printf '%s' "${DJCMS_PRIMARY:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
  1|true|yes|on)   PRIMARY_ON=1 ;;
  ''|0|false|no|off) PRIMARY_ON=0 ;;
  *)
    echo "render_caddyfile: nie rozumiem DJCMS_PRIMARY=„${DJCMS_PRIMARY:-}” (użyj 1/true albo 0/false)" >&2
    exit 1
    ;;
esac
if [ "$PRIMARY_ON" = "1" ] && [ "$DJCMS_ON" != "1" ]; then
  echo "render_caddyfile: DJCMS_PRIMARY=1 wymaga DJCMS_ENABLED=1 (strony publiczne do djcms, którego nie ma)" >&2
  exit 1
fi

# `LIVEKIT_PROXY` (zadanie WEB-01, docs/OPERACJE.md § 36.2) – sygnalizacja LiveKit na tym samym
# hoście (wariant (b)): blok `live.{$SITE_DOMAIN}` → `livekit:7880`. Ten sam odczyt i ta sama walidacja,
# co przełączniki wyżej. Wyłączony (domyślnie) = wynik bajt w bajt jak dotąd – i żadnego wniosku
# o certyfikat dla `live.` na instalacji bez LiveKit (bez rekordu DNS byłby błędem i zużyciem limitu).
if [ -z "${LIVEKIT_PROXY+x}" ] && [ -f "$ROOT/.env" ]; then
  LIVEKIT_PROXY="$(sed -n 's/^LIVEKIT_PROXY=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
case "$(printf '%s' "${LIVEKIT_PROXY:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
  1|true|yes|on)   LIVEKIT_ON=1 ;;
  ''|0|false|no|off) LIVEKIT_ON=0 ;;
  *)
    echo "render_caddyfile: nie rozumiem LIVEKIT_PROXY=„${LIVEKIT_PROXY:-}” (użyj 1/true albo 0/false)" >&2
    exit 1
    ;;
esac

# `ERRORS_PROXY` (zadanie OPS-02, docs/OPERACJE.md § 44) – GlitchTip (śledzenie błędów, profil compose
# `monitoring`) pod `errors.{$SITE_DOMAIN}` → `glitchtip:8000`. Odczyt i walidacja jak przy LIVEKIT_PROXY.
# Wyłączony (domyślnie) = wynik bajt w bajt jak dotąd i żadnego wniosku o certyfikat dla `errors.`.
if [ -z "${ERRORS_PROXY+x}" ] && [ -f "$ROOT/.env" ]; then
  ERRORS_PROXY="$(sed -n 's/^ERRORS_PROXY=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
case "$(printf '%s' "${ERRORS_PROXY:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
  1|true|yes|on)   ERRORS_ON=1 ;;
  ''|0|false|no|off) ERRORS_ON=0 ;;
  *)
    echo "render_caddyfile: nie rozumiem ERRORS_PROXY=„${ERRORS_PROXY:-}” (użyj 1/true albo 0/false)" >&2
    exit 1
    ;;
esac
# `ERRORS_UI_ALLOW` (opcjonalnie, OPS-02 L2) – adresy/podsieci (spacją), z których wolno otworzyć panel
# GlitchTipa; puste = panel z każdego adresu (za logowaniem GlitchTipa). Wartość trafia do Caddyfile'a
# jako składnia, więc dopuszczamy wyłącznie znaki adresu IP i maski.
if [ -z "${ERRORS_UI_ALLOW+x}" ] && [ -f "$ROOT/.env" ]; then
  ERRORS_UI_ALLOW="$(sed -n 's/^ERRORS_UI_ALLOW=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
ERRORS_UI_ALLOW="$(printf '%s' "${ERRORS_UI_ALLOW:-}" | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//')"
for ip in $ERRORS_UI_ALLOW; do
  if ! [[ $ip =~ ^[0-9A-Fa-f:.]+(/[0-9]{1,3})?$ ]]; then
    echo "render_caddyfile: „$ip” w ERRORS_UI_ALLOW nie jest adresem IP ani podsiecią" >&2
    exit 1
  fi
done

# `NOTEBOOK_LAB_HOST` (QC-02) – ten sam odczyt (środowisko wygrywa z `.env`). To nie przełącznik,
# tylko nazwa hosta, która trafia do pliku jako **składnia** – walidacja niżej, obok EXTRA_DOMAINS.
if [ -z "${NOTEBOOK_LAB_HOST+x}" ] && [ -f "$ROOT/.env" ]; then
  NOTEBOOK_LAB_HOST="$(sed -n 's/^NOTEBOOK_LAB_HOST=//p' "$ROOT/.env" | tail -n 1 | tr -d '\r\042\047')"
fi
# Odstępy zdejmowane tylko z brzegów: „a.pl b.pl” ma zatrzymać walidację, a nie skleić się w jedną nazwę.
LAB_HOST="$(printf '%s' "${NOTEBOOK_LAB_HOST:-}" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
LAB_HOST="${LAB_HOST%.}"

# Kontrakt tras aplikacji (DJ-02 § 6): dwa wyrażenia generowane z urlconfu `web` przez
# `manage.py djcms_routes --write` i commitowane. Potrzebny wyłącznie przy DJCMS_ENABLED=1 – bez
# przełącznika plik nie jest nawet czytany. Czytany `sed`-em, nie `source` (jak `.env`): wartość
# ląduje w konfiguracji proxy jako składnia, więc przed użyciem sprawdzamy, że nie ma w niej znaków,
# które w Caddyfile'u znaczą coś innego niż w wyrażeniu (odstęp kończy token, klamra otwiera blok
# albo symbol zastępczy, cudzysłów/odwrócony apostrof – napis, `#` – komentarz).
ROUTES_ENV="${DJCMS_ROUTES_ENV:-$ROOT/backend/djcms_contract/app_routes.env}"
routes_value() {  # routes_value <NAZWA> – wartość `NAZWA='…'` z kontraktu (ostatnie wystąpienie)
  sed -n "s/^$1='\\(.*\\)'\$/\\1/p" "$ROUTES_ENV" | tail -n 1 | tr -d '\r'
}
if [ "$DJCMS_ON" = "1" ]; then
  [ -f "$ROUTES_ENV" ] || {
    echo "render_caddyfile: brak kontraktu tras $ROUTES_ENV (manage.py djcms_routes --write)" >&2
    exit 1
  }
  APP_RE="$(routes_value APP_RE)"
  APP_RE_PREFIXED="$(routes_value APP_RE_PREFIXED)"
  for name in APP_RE APP_RE_PREFIXED; do
    value="${!name}"
    case "$value" in
      ''|[!^]*|*[[:space:]{}\"\'\`\#\;]*)
        echo "render_caddyfile: $name w $ROUTES_ENV pusty albo z niedozwolonym znakiem – wygeneruj ponownie (djcms_routes --write)" >&2
        exit 1
        ;;
    esac
  done
fi
if [ "$PRIMARY_ON" = "1" ]; then DJCMS_MODE=primary; else DJCMS_MODE=preview; fi

# Odmowa `/internal/*` w blokach aplikacji: potrzebna, gdy pod `/internal/` jest cokolwiek poza
# siecią compose'a do ochrony – zgoda na certyfikat (subdomeny) albo API dla djcms. Jedna zmienna
# dla obu przełączników, więc przy obu włączonych reguła trafia do każdego bloku **raz**.
if [ "$SUBDOMAINS_ON" = "1" ] || [ "$DJCMS_ON" = "1" ]; then GUARD_ON=1; else GUARD_ON=0; fi

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

# Host laboratorium (QC-02): sama nazwa – bez portu (certyfikat HTTP-01 wystawia się na nazwę), co
# najmniej jedna kropka – i nie host serwisu z EXTRA_DOMAINS: dwa bloki o tej samej nazwie to błąd
# Caddy'ego, a host serwisu jako „laboratorium” oddałby kod uczniów originowi z sesją. Zgodności
# z SITE_DOMAIN generator nie zna (to symbol zastępczy) – pilnuje jej `notebooks.E002` w Django.
LAB_HOST_RE='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
if [ -n "$LAB_HOST" ]; then
  if ! [[ $LAB_HOST =~ $LAB_HOST_RE ]]; then
    echo "render_caddyfile: NOTEBOOK_LAB_HOST=„${NOTEBOOK_LAB_HOST}” nie wygląda na nazwę hosta (bez schematu, portu i ścieżki)" >&2
    exit 1
  fi
  for host in $EXTRA_DOMAINS; do
    if [ "$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')" = "$LAB_HOST" ]; then
      echo "render_caddyfile: NOTEBOOK_LAB_HOST=„$LAB_HOST” jest też w EXTRA_DOMAINS – laboratorium musi mieć własny host" >&2
      exit 1
    fi
  done
fi

apex_listed() {
  # Czy „goła” domena hosta `www.x` też jest na liście. Od tego zależy, czy `www.x` dostaje blok
  # aplikacji, czy przekierowanie – patrz niżej.
  local apex="${1#www.}"
  for candidate in $EXTRA_DOMAINS; do
    [ "$candidate" = "$apex" ] && return 0
  done
  return 1
}

internal_guard() {
  # Odmowa dla `/internal/*` na **każdej** publicznej nazwie. Ten adres istnieje wyłącznie po to,
  # żeby Caddy mógł zapytać aplikację o zgodę na certyfikat (`on_demand_tls ask`), a djcms pobrać
  # dane zawodów (`/internal/djcms/v2/`, token w nagłówku), i jest wołany po sieci wewnętrznej
  # compose, pod hostem `web:8000`. Aplikacja odmawia publicznym hostom sama (endpointy odpowiadają
  # tylko na `Host: web:8000`) – to jest druga zapora, nie jedyna.
  #
  # `handle`, a nie samo `respond`: w bloku aplikacji stoi niżej `handle` bez matchera, czyli
  # łapiący wszystko, a w kolejności dyrektyw Caddy'ego `respond` idzie **po** `handle`, więc
  # nigdy by się nie wykonało. Bloki `handle`/`handle_path` są rozłączne i wygrywa pierwszy
  # pasujący w kolejności zapisu – dlatego ta reguła stoi przed nimi wszystkimi.
  cat <<'EOF'
    handle /internal/* {
        respond 404
    }
EOF
}

djcms_section() {
  # djcms_section <wyrażenie adresów aplikacji> <wyrażenie adresów djcms> – sekcja tras djcms
  # (DJ-02 § 3) do bloku aplikacji, między `handle_path /static/*` a domyślnym `handle` (web).
  # **Ta sama** w każdym bloku; różni się tylko wyrażeniami w bloku domeny głównej (konkursy pod
  # prefiksem ścieżki: `/<prefiks>/login/` do web, `/<prefiks>/djcms/…` do djcms). Znaczniki
  # `>>>`/`<<<` na brzegach – po nich test wycina sekcję i porównuje resztę z wynikiem bez djcms.
  #
  # Kolejność `handle` (rozłączne, wygrywa pierwszy): odmowa /internal/* i /static/* (stoją wyżej)
  # → pliki redaktorów → adresy djcms → adresy aplikacji → strony publiczne wg trybu → domyślny
  # `handle` bloku (web). Caddy sortuje `handle` o jednej ścieżce po jej długości, a pozostałe
  # zostawia w kolejności zapisu – kolejność po `caddy adapt` sprawdza render_caddyfile_test.sh,
  # a działanie – djcms_routing_test.sh (żywy Caddy).
  #
  # - `request_header -X-Djcms-Mode`: nagłówek trybu ustawia wyłącznie proxy (`header_up` przy
  #   djcms nadpisuje go i tak); zdjęty z każdego żądania, także tego do `web` (DJ-02 D10).
  # - `Vary: Cookie` (dopisane do `Accept-Encoding` z `encode`): ta sama ścieżka publiczna daje
  #   różną treść zależnie od ciasteczka `djcms_view`. Poza statykami i plikami redaktorów.
  # - Pliki redaktorów (`/djcms/media/*`): Caddy z wolumenu `djcms_media` (nakładka
  #   docker-compose.djcms.yml), `nosniff` z bloku i CSP `sandbox` dla wszystkiego poza PDF-em –
  #   wgrany SVG/HTML otwarty wprost nie wykona skryptu w origin aplikacji (S9; uzasadnienie
  #   odwróconej listy – komentarz przy tej regule w DJ-01h, docs/OPERACJE.md § 22.7).
  # - Ciasteczko `djcms_view` przez `{http.request.cookie.…}` (parser ciasteczek Go), a nie
  #   wyrażeniem na surowym nagłówku `Cookie`: wartość w cudzysłowie i kilka nagłówków `Cookie`
  #   działają tak samo, jak odczyta je djcms. Ciasteczko nie jest granicą bezpieczeństwa.
  local app_re="$1" own_re="$2" public desc
  if [ "$PRIMARY_ON" = "1" ]; then
    public='    @djcms_public expression `{http.request.cookie.djcms_view} != "wagtail"`'
    desc='djcms, z ciasteczkiem djcms_view=wagtail – web'
  else
    public='    @djcms_public expression `{http.request.cookie.djcms_view} == "dj"`'
    desc='web, z ciasteczkiem djcms_view=dj – djcms'
  fi
  printf '%s\n' \
    "    # >>> django CMS (docs/tasks/DJ-02.md § 3), DJCMS_PRIMARY=$PRIMARY_ON – strony publiczne: $desc." \
    '    # Wstawione przez scripts/render_caddyfile.sh przy DJCMS_ENABLED=1 – nie edytuj tego pliku.' \
    '    request_header -X-Djcms-Mode' \
    '    @djcms_vary not path /static/* /djcms/static/* /djcms/media/*' \
    '    header @djcms_vary +Vary Cookie' \
    '    @djcms_active_media {' \
    '        path /djcms/media/*' \
    '        not path_regexp (?i)\.pdf$' \
    '    }' \
    "    header @djcms_active_media Content-Security-Policy \"default-src 'none'; style-src 'unsafe-inline'; sandbox\"" \
    '    handle_path /djcms/media/* {' \
    '        header Cache-Control "public, max-age=86400"' \
    '        root * /srv/djcms-media' \
    '        file_server' \
    '    }' \
    "    @djcms_own path_regexp $own_re" \
    '    handle @djcms_own {' \
    '        reverse_proxy djcms:8000 {' \
    '            header_up X-Forwarded-Proto {scheme}' \
    '            header_up X-Real-IP {remote_host}' \
    "            header_up X-Djcms-Mode $DJCMS_MODE" \
    '        }' \
    '    }' \
    "    @djcms_app path_regexp $app_re" \
    '    handle @djcms_app {' \
    '        reverse_proxy web:8000 {' \
    '            header_up X-Forwarded-Proto {scheme}' \
    '            header_up X-Real-IP {remote_host}' \
    '        }' \
    '    }' \
    "$public" \
    '    handle @djcms_public {' \
    '        reverse_proxy djcms:8000 {' \
    '            header_up X-Forwarded-Proto {scheme}' \
    '            header_up X-Real-IP {remote_host}' \
    "            header_up X-Djcms-Mode $DJCMS_MODE" \
    '        }' \
    '    }' \
    '    # <<< django CMS'
}

# Wyrażenia sekcji: bloki EXTRA_DOMAINS i `*.` – tylko adresy w korzeniu; blok domeny głównej
# (jedyny host z konkursami pod prefiksem ścieżki, DJ-02 D3) – także `/<prefiks>/…`.
if [ "$DJCMS_ON" = "1" ]; then
  OWN_RE='^/djcms(?:/.*)?$'
  OWN_RE_MAIN='^/(?:[^/]+/)?djcms(?:/.*)?$'
  APP_RE_MAIN="$APP_RE|$APP_RE_PREFIXED"
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/caddyfile.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.sub" "$tmp.sec"' EXIT
cat "$SRC" > "$tmp"

if [ "$GUARD_ON" = "1" ]; then
  # Wstawki w pliku źródłowym, zakotwiczone na dosłownych linijkach `deploy/Caddyfile`: odmowa
  # `/internal/*` w bloku domeny głównej (przed `handle_path /static/*`) – przy każdym z dwóch
  # przełączników – i opcja globalna `on_demand_tls` (po `email {$ACME_EMAIL}`) – wyłącznie przy
  # PLATFORM_SUBDOMAINS=1. Brak którejkolwiek potrzebnej kotwicy zatrzymuje generator – cicha
  # zmiana w pliku źródłowym dałaby konfigurację bez pytania o zgodę na certyfikat albo
  # z endpointem wystawionym publicznie, a jedno i drugie wychodzi na jaw dopiero na produkcji.
  #
  # Komentarz nad odmową mówi, **po co** ona jest, więc zależy od przełączników. Wariant samych
  # subdomen jest dosłownie ten sprzed DJ-01 – wyłączony DJCMS_ENABLED nie zmienia ani bajtu.
  if [ "$SUBDOMAINS_ON" = "1" ] && [ "$DJCMS_ON" = "1" ]; then
    why1="    # \`/internal/*\` jest wyłącznie dla sieci compose'a (zgoda na certyfikat, API danych"
    why2="    # dla djcms) i nie ma prawa odpowiadać z nazwy publicznej. Wstawiane przez"
    why3="    # scripts/render_caddyfile.sh przy PLATFORM_SUBDOMAINS=1 i DJCMS_ENABLED=1."
  elif [ "$SUBDOMAINS_ON" = "1" ]; then
    why1="    # \`/internal/*\` jest wyłącznie dla Caddy'ego (pytanie o zgodę na certyfikat)"
    why2="    # i nie ma prawa odpowiadać z nazwy publicznej. Wstawiane przez"
    why3="    # scripts/render_caddyfile.sh przy PLATFORM_SUBDOMAINS=1."
  else
    why1="    # \`/internal/*\` jest wyłącznie dla sieci compose'a (API danych dla djcms)"
    why2="    # i nie ma prawa odpowiadać z nazwy publicznej. Wstawiane przez"
    why3="    # scripts/render_caddyfile.sh przy DJCMS_ENABLED=1."
  fi
  # Komentarze przez ENVIRON, a nie `awk -v`: `-v` interpretuje sekwencje z odwrotnym ukośnikiem.
  #
  # Przy PLATFORM_SUBDOMAINS=1 dochodzi jeszcze przypięcie polityki TLS (`tls_pin` niżej) w blokach
  # nazw dosłownych z pliku źródłowego: `www.`, `meet.`, `monitor.`, `{$S3_PUBLIC_ADDRESS}` (bywa
  # `s3.<domena>`) – plus `dj.` niżej – i w bloku domeny głównej. Powód: adapter
  # Caddyfile'a (2.8, `consolidateAutomationPolicies`) wcina politykę TLS tych nazw do polityki
  # domyślnej (bez `subjects`), gdy obie są identyczne – a polityka domyślna stoi ZA polityką
  # `*.{$SITE_DOMAIN}` z `on_demand`. Caddy bierze pierwszą pasującą politykę, więc `www.`, `dj.`
  # itd. trafiały do on-demand, a `ask` (/internal/tls-allowed) ich odmawia – certyfikatu nie ma.
  # Politykę domyślną adapter tworzy np. przy `local_certs` (E2E; sprawdzone `caddy adapt`); przy
  # samym `email` (produkcja) nazwy dosłowne mają dziś własną politykę, ale tylko z przypadku
  # heurystyki adaptera. `key_type p256` to wartość DOMYŚLNA Caddy'ego (nic nie zmienia
  # w certyfikacie) – wpis wyłącznie odróżnia politykę tych nazw, więc zostaje osobna i stoi
  # PRZED `*.` (adapter sortuje polityki po liczbie nazw: tu ≥ 5, we wzorcu 1). Domena główna też:
  # S3 bywa pod `<domena>:9000` (produkcja), a ta sama nazwa w dwóch blokach z różnymi ustawieniami
  # TLS to błąd konfiguracji. Kontrola: render_caddyfile_test.sh (§ 19, polityki po `caddy adapt`
  # przy ACME i local_certs, S3 pod `s3.` i pod `<domena>:9000`).
  #
  # `force_automate` (DEP-02, Caddy 2.10): od 2.10 Caddy domyślnie NIE zarządza certyfikatem nazwy,
  # którą „pokrywa” zarządzana nazwa wieloznaczna z konfiguracji (caddyserver/caddy#6959) – a blok
  # `*.{$SITE_DOMAIN}` taką nazwą jest, choć z `on_demand` nigdy nie dostaje certyfikatu
  # wieloznacznego (on-demand wystawia na nazwę z SNI, HTTP-01/TLS-ALPN; DNS-01 nie mamy). Bez tej
  # opcji `www.`, `meet.`, `monitor.`, `dj.`, `live.`, `errors.`, `lab.` nie miałyby certyfikatu
  # wcale (ich polityka nie jest on-demand) – sprawdzone na żywym caddy:2.10 w djcms_routing_test.sh.
  # Domena główna i `<domena>:9000` nie są pod `*.`, opcja jest tam bez skutku, ale nieszkodliwa.
  WHY1="$why1" WHY2="$why2" WHY3="$why3" awk -v want_opts="$SUBDOMAINS_ON" '
    function tls_pin() {
      print "    # Zwykły certyfikat (nie on-demand bloku *.) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1."
      print "    tls force_automate {"
      print "        key_type p256"
      print "    }"
      pins++
    }
    BEGIN { opts = 0; guard = 0; pins = 0 }
    {
      if (want_opts == "1" && ($0 == "www.{$SITE_DOMAIN} {" || $0 == "{$SITE_DOMAIN} {" || $0 == "meet.{$SITE_DOMAIN} {" ||
          $0 == "monitor.{$SITE_DOMAIN} {" || $0 == "{$S3_PUBLIC_ADDRESS} {")) {
        print
        tls_pin()
        next
      }
      if (!guard && $0 == "    handle_path /static/* {") {
        print ENVIRON["WHY1"]
        print ENVIRON["WHY2"]
        print ENVIRON["WHY3"]
        print "    handle /internal/* {"
        print "        respond 404"
        print "    }"
        # Konkursy pod prefiksem ścieżki (tylko ten blok): `/<prefiks>/internal/…` trafiałoby do web
        # (kontrakt tras djcms, APP_RE_PREFIXED) – web zdejmuje prefiks już po własnej kontroli
        # `/internal/`, więc chroniłaby wyłącznie bramka hosta. Odmowa także tutaj.
        print "    @internal_prefixed path_regexp ^/[^/]+/internal(/.*)?$"
        print "    handle @internal_prefixed {"
        print "        respond 404"
        print "    }"
        guard = 1
      }
      print
      if (want_opts == "1" && !opts && $0 == "    email {$ACME_EMAIL}") {
        print ""
        print "    # Subdomeny konkursów platformy (PLATFORM_SUBDOMAINS=1, scripts/render_caddyfile.sh)."
        print "    # Certyfikat dla `<slug>.<SITE_DOMAIN>` powstaje przy pierwszym wejściu, ale wyłącznie"
        print "    # za zgodą aplikacji: Caddy dokleja do adresu `?domain=<host>`, 200 = wystawiamy,"
        print "    # cokolwiek innego = nie. Bez `ask` on-demand TLS byłby otwartym generatorem"
        print "    # certyfikatów dla każdego hosta wskazującego nasz adres IP i prostą drogą do"
        print "    # wyczerpania limitu Let'\''s Encrypt (50 certyfikatów na domenę tygodniowo)."
        print "    on_demand_tls {"
        print "        ask http://web:8000/internal/tls-allowed"
        print "    }"
        opts = 1
      }
    }
    END { if ((want_opts == "1" && (!opts || pins != 5)) || !guard) exit 3 }
  ' "$tmp" > "$tmp.sub" || {
    echo "render_caddyfile: nie znalazłem kotwic dla PLATFORM_SUBDOMAINS/DJCMS_ENABLED w $SRC – popraw generator razem z plikiem źródłowym" >&2
    exit 1
  }
  cat "$tmp.sub" > "$tmp"
fi

if [ "$DJCMS_ON" = "1" ]; then
  # Sekcja tras djcms w bloku domeny głównej: przed jego domyślnym `handle` (proxy do web) – kotwica
  # to pierwsza linijka `    handle {` po nagłówku `{$SITE_DOMAIN} {`. Sekcja z pliku (getline),
  # nie z `awk -v`: `-v` interpretuje odwrotne ukośniki, a wyrażenia ich pełno.
  djcms_section "$APP_RE_MAIN" "$OWN_RE_MAIN" > "$tmp.sec"
  awk -v sec="$tmp.sec" '
    BEGIN { main = 0; done = 0 }
    {
      if ($0 == "{$SITE_DOMAIN} {") main = 1
      if (main && !done && $0 == "    handle {") {
        while ((getline line < sec) > 0) print line
        done = 1
      }
      if (main && $0 == "}") main = 0
      print
    }
    END { if (!done) exit 3 }
  ' "$tmp" > "$tmp.sub" || {
    echo "render_caddyfile: nie znalazłem kotwicy sekcji djcms (\`    handle {\` w bloku {\$SITE_DOMAIN}) w $SRC – popraw generator razem z plikiem źródłowym" >&2
    exit 1
  }
  cat "$tmp.sub" > "$tmp"
fi

extra_force_automate() {
  # Bloki z EXTRA_DOMAINS przy PLATFORM_SUBDOMAINS=1: `force_automate` z tego samego powodu co
  # w `tls_pin` wyżej (Caddy 2.10 pomija nazwy pokryte przez `*.{$SITE_DOMAIN}`). Generator nie zna
  # wartości SITE_DOMAIN (to symbol zastępczy Caddy'ego), więc nie wie, która domena z listy jest
  # subdomeną platformy – opcja trafia do każdej; dla domen spoza `*.` jest bez skutku. Bez
  # `key_type`: polityka tych nazw zostaje taka jak dotąd (§ 19 testu generatora).
  [ "$SUBDOMAINS_ON" = "1" ] || return 0
  printf '%s\n' \
    '    # Własny certyfikat także pod blokiem *. (Caddy 2.10) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1.' \
    '    tls force_automate'
}

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
EOF
    extra_force_automate >> "$tmp"
    cat >> "$tmp" <<EOF
    redir https://${host#www.}{uri} permanent
}
EOF
  else
    # Blok aplikacji: ten sam co bloku domeny głównej, łącznie z limitem rozmiaru żądania
    # i nagłówkami bezpieczeństwa. Powtórzony, a nie wyciągnięty do wspólnego fragmentu
    # (`import`), bo blok domeny głównej ma zostać w pliku źródłowym literalnie taki, jaki
    # jest dzisiaj – to jest warunek zadania T6 (§ 6). Wyjątek: `import maintenance` (strona
    # „Prace techniczne”, fragment zdefiniowany w deploy/Caddyfile) – konkurs z EXTRA_DOMAINS
    # ma w czasie przerwy pokazywać to samo co domena główna, a nie pusty błąd 502.
    cat >> "$tmp" <<EOF

# Wygenerowane przez scripts/render_caddyfile.sh z EXTRA_DOMAINS – nie edytuj tego pliku.
$host {
EOF
    extra_force_automate >> "$tmp"
    # Odmowa `/internal/*` tylko przy włączonym przełączniku (PLATFORM_SUBDOMAINS albo
    # DJCMS_ENABLED): przy wyłączonych ten plik ma być kopią `deploy/Caddyfile` co do bajtu.
    if [ "$GUARD_ON" = "1" ]; then internal_guard >> "$tmp"; fi
    cat >> "$tmp" <<EOF
    import maintenance
    import notebook_lab
    encode gzip zstd
    request_body {
        max_size {\$MAX_UPLOAD_MB}MB
    }
    handle_path /static/* {
        root * /srv/static
        file_server
    }
EOF
    # Sekcja tras djcms (DJ-02 § 3) – ta sama co w bloku domeny głównej, bez wariantu prefiksu.
    if [ "$DJCMS_ON" = "1" ]; then djcms_section "$APP_RE" "$OWN_RE" >> "$tmp"; fi
    cat >> "$tmp" <<EOF
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

if [ "$SUBDOMAINS_ON" = "1" ]; then
  # Blok wieloznaczny idzie na koniec pliku, bo kolejność bloków w Caddyfile'u nie ma znaczenia
  # dla wyboru witryny: Caddy dobiera blok po **najbardziej szczegółowym** dopasowaniu nazwy
  # hosta, a nie po tym, który stoi wyżej. Dlatego `www.`, `meet.`, `monitor.`, `s3.`, `mail.`
  # i każda domena z `EXTRA_DOMAINS` zachowują pierwszeństwo – nazwa dosłowna jest zawsze
  # bardziej szczegółowa niż `*.`. Wzorzec obejmuje dokładnie **jedną** etykietę, więc
  # `a.b.<SITE_DOMAIN>` nie pasuje do niego wcale.
  cat >> "$tmp" <<'EOF'

# Wygenerowane przez scripts/render_caddyfile.sh przy PLATFORM_SUBDOMAINS=1 – nie edytuj tego pliku.
# Konkursy zakładane z panelu koordynatora: `<slug>.{$SITE_DOMAIN}` bez wdrożenia i bez wpisu
# w `.env`. Certyfikat powstaje przy pierwszym wejściu (`tls { on_demand }`), a zgody udziela
# aplikacja pod `/internal/tls-allowed` (opcja globalna `on_demand_tls` na początku pliku):
# nazwa bez aktywnego konkursu nie dostaje certyfikatu i nie zużywa limitu Let's Encrypt.
# Wymaga rekordu DNS `*.<domena>` wskazującego ten serwer (deploy/dns-<domena>.md).
*.{$SITE_DOMAIN} {
    tls {
        on_demand
    }
EOF
  internal_guard >> "$tmp"
  cat >> "$tmp" <<'EOF'
    import maintenance
    import notebook_lab
    encode gzip zstd
    request_body {
        max_size {$MAX_UPLOAD_MB}MB
    }
    handle_path /static/* {
        root * /srv/static
        file_server
    }
EOF
  if [ "$DJCMS_ON" = "1" ]; then djcms_section "$APP_RE" "$OWN_RE" >> "$tmp"; fi
  cat >> "$tmp" <<'EOF'
    handle {
        reverse_proxy web:8000 {
            header_up X-Forwarded-Proto {scheme}
            # Adres klienta dla audytu; backend ufa temu nagłówkowi tylko, gdy REMOTE_ADDR jest adresem proxy.
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

if [ "$DJCMS_ON" = "1" ]; then
  # Blok `dj.` na samym końcu pliku – za blokiem `*.`, jeśli jest. Kolejność nie decyduje
  # o wyborze witryny (patrz komentarz przy bloku wieloznacznym): nazwa dosłowna `dj.<domena>`
  # zawsze wygrywa z `*.<domena>`, więc przy obu przełącznikach `dj.` nie trafia do aplikacji
  # głównej jako „konkurs o slugu dj” (slug `dj` jest zresztą zarezerwowany w formularzu konkursu).
  #
  # Od DJ-02 (decyzja D1) `dj.` nie serwuje treści – jest wyłącznie wejściem do podglądu. djcms
  # odpowiada na prawdziwych hostach konkursów (sekcja tras wyżej), a podgląd włącza ciasteczko
  # `djcms_view=dj` **na tym hoście** – ustawia je djcms (`POST /djcms/preview/`, CSRF, HttpOnly,
  # Secure, SameSite=Lax, 8 h), bo ciasteczko ustawione tu, pod `dj.`, i tak nie trafiłoby na
  # inną nazwę (ciasteczka host-only). Dlatego:
  # - DJCMS_PRIMARY=0: 302 na stronę włączenia podglądu domeny głównej, z adresem, na który
  #   ktoś wszedł (`next`) – strona wymienia też wszystkie konkursy z odnośnikami do ich
  #   `/djcms/preview/`. `{uri}` (surowy RequestURI: ścieżka zakodowana tak, jak przyszła,
  #   z zapytaniem), a nie `{path}` (zdekodowana – `%23`/`%26` rozbiłyby adres); Caddy 2.8 nie ma
  #   symbolu zastępczego z kodowaniem do zapytania, więc parametry po pierwszym `&` zapytania
  #   trafiają do widoku osobno – djcms i tak dopuszcza w `next` wyłącznie ścieżkę tego hosta.
  # - DJCMS_PRIMARY=1: 302 na ten sam adres domeny głównej (stare zakładki z czasu porównania).
  # 302, a nie 301: cel zależy od trybu, a przeglądarka zapamiętuje 301 na zawsze.
  # Odmowa `/internal/*` zostaje (S4 – w każdym bloku). `redir` w `handle`, bo samo `redir`
  # wykonuje się przed każdym `handle` i odmowa byłaby martwa. Bez `import maintenance`
  # i bez treści – nie ma czego zasłaniać.
  if [ "$PRIMARY_ON" = "1" ]; then
    dj_target='https://{$SITE_DOMAIN}{uri}'
  else
    dj_target='https://{$SITE_DOMAIN}/djcms/preview/?next={uri}'
  fi
  cat >> "$tmp" <<'EOF'

# Wygenerowane przez scripts/render_caddyfile.sh przy DJCMS_ENABLED=1 – nie edytuj tego pliku.
# Wejście do podglądu serwisu na django CMS (docs/tasks/DJ-02.md D1): wyłącznie przekierowanie
# na domenę główną. Nazwa dosłowna wygrywa z blokiem `*.{$SITE_DOMAIN}`; certyfikat zwykły (HTTP-01).
dj.{$SITE_DOMAIN} {
EOF
  # Przy subdomenach platformy – to samo przypięcie polityki TLS co w `www.`/`meet.` (komentarz przy
  # wstawkach awk wyżej): bez niego `dj.` dostawałby politykę on-demand bloku `*.`, której `ask` odmawia.
  if [ "$SUBDOMAINS_ON" = "1" ]; then
    printf '%s\n' \
      '    # Zwykły certyfikat (nie on-demand bloku *.) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1.' \
      '    tls force_automate {' '        key_type p256' '    }' >> "$tmp"
  fi
  internal_guard >> "$tmp"
  printf '%s
'     '    handle {'     "        redir $dj_target 302"     '    }'     '}' >> "$tmp"
fi

if [ "$LIVEKIT_ON" = "1" ]; then
  cat >> "$tmp" <<'EOF'

# Wygenerowane przez scripts/render_caddyfile.sh przy LIVEKIT_PROXY=1 – nie edytuj tego pliku.
# Sygnalizacja LiveKit (WebSocket i /rtc/validate) dla pokoi webinarów (docs/OPERACJE.md § 36.2).
# Media nie idą przez Caddy: UDP 7882 (multipleksacja) i TCP 7881 prosto do kontenera `livekit`.
live.{$SITE_DOMAIN} {
EOF
  if [ "$SUBDOMAINS_ON" = "1" ]; then
    printf '%s\n' \
      '    # Zwykły certyfikat (nie on-demand bloku *.) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1.' \
      '    tls force_automate {' '        key_type p256' '    }' >> "$tmp"
  fi
  printf '%s\n' \
    '    header {' '        Referrer-Policy no-referrer' '        -Server' '    }' \
    '    reverse_proxy livekit:7880' '}' >> "$tmp"
fi

if [ "$ERRORS_ON" = "1" ]; then
  # GlitchTip (OPS-02). Bez `import maintenance`: zgłoszenia błędów mają przechodzić także w czasie
  # przerwy (wtedy są najcenniejsze), a panel GlitchTipa nie zależy od `web`. Limit żądania 10 MB –
  # koperta zdarzenia z mapą źródeł mieści się z zapasem, a nic większego tu nie przychodzi.
  # Uwierzytelnienie jest po stronie GlitchTipa (konto administratora, samorejestracja wyłączona).
  # CORS dla kopert z przeglądarek (SENTRY_BROWSER=1) obsługuje sam GlitchTip.
  cat >> "$tmp" <<'EOF'

# Wygenerowane przez scripts/render_caddyfile.sh przy ERRORS_PROXY=1 – nie edytuj tego pliku.
# Śledzenie błędów – GlitchTip (profil `monitoring`, docs/OPERACJE.md § 44). Wymaga rekordu DNS
# `errors.<domena>`; dopóki go nie ma, blok tylko czeka na certyfikat.
errors.{$SITE_DOMAIN} {
EOF
  if [ "$SUBDOMAINS_ON" = "1" ]; then
    printf '%s\n' \
      '    # Zwykły certyfikat (nie on-demand bloku *.) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1.' \
      '    tls force_automate {' '        key_type p256' '    }' >> "$tmp"
  fi
  if [ -n "$ERRORS_UI_ALLOW" ]; then
    # Panel GlitchTipa tylko z podanych adresów; przyjmowanie zdarzeń (koperta/store/minidump/raporty
    # CSP) i sonda `/_health/` – z każdego, bo wysyłają przeglądarki uczestników i monitor dostępności.
    # `respond` stoi w kolejności dyrektyw Caddy'ego przed `reverse_proxy`, więc odmowa wygrywa.
    printf '%s\n' \
      '    # Panel tylko z ERRORS_UI_ALLOW (scripts/render_caddyfile.sh) – przyjmowanie zdarzeń z każdego adresu.' \
      '    @errors_ui {' \
      '        not path_regexp ^/api/[0-9]+/(envelope|store|minidump|security)/?$' \
      '        not path /_health/' \
      "        not remote_ip $ERRORS_UI_ALLOW" \
      '    }' \
      '    respond @errors_ui 403' >> "$tmp"
  fi
  printf '%s\n' \
    '    encode gzip zstd' \
    '    request_body {' '        max_size 10MB' '    }' \
    '    reverse_proxy glitchtip:8000 {' \
    '        header_up X-Forwarded-Proto {scheme}' \
    '    }' \
    '    header {' \
    '        Strict-Transport-Security "max-age=31536000"' \
    '        X-Content-Type-Options "nosniff"' \
    '        X-Frame-Options "DENY"' \
    '        Referrer-Policy "same-origin"' \
    '        -Server' \
    '    }' \
    '}' >> "$tmp"
fi

if [ -n "$LAB_HOST" ]; then
  # 1. Bloki aplikacji (domena główna, EXTRA_DOMAINS, `*.` – wszystkie są już w pliku) przestają
  #    podawać laboratorium: każde `import notebook_lab` → `import notebook_lab_moved`. Definicja
  #    fragmentu staje tuż przed `(notebook_lab)` – Caddy rozwija `import` w kolejności pliku, więc
  #    fragment musi być zdefiniowany przed pierwszym użyciem. Brak kotwic zatrzymuje generator.
  #    Ścieżka laboratorium → 302 (nie 404): stare zakładki i otwarte karty trafiają na nowy host;
  #    `/notebook-starter/*` → 404 – notatnik startowy podaje już wyłącznie host laboratorium.
  #    `handle` o dłuższej ścieżce Caddy stawia przed `handle_path /static/*` (sprawdza
  #    render_caddyfile_test.sh po `caddy adapt`).
  LAB_HOST="$LAB_HOST" awk '
    BEGIN { lab = ENVIRON["LAB_HOST"]; defined = 0; moved = 0 }
    $0 == "(notebook_lab) {" && !defined {
      print "# Laboratorium na osobnym hoście (QC-02, NOTEBOOK_LAB_HOST) – wstawione przez scripts/render_caddyfile.sh."
      print "# Bloki aplikacji importują ten fragment zamiast `notebook_lab`: ścieżki laboratorium już nie podają."
      print "(notebook_lab_moved) {"
      print "    handle /static/notebook-lab/* {"
      print "        redir https://" lab "{uri} 302"
      print "    }"
      print "    handle /notebook-starter/* {"
      print "        respond 404"
      print "    }"
      print "}"
      print ""
      defined = 1
    }
    $0 == "    import notebook_lab" { print "    import notebook_lab_moved"; moved++; next }
    { print }
    END { if (!defined || !moved) exit 3 }
  ' "$tmp" > "$tmp.sub" || {
    echo "render_caddyfile: nie znalazłem kotwic NOTEBOOK_LAB_HOST (\`(notebook_lab) {\`, \`    import notebook_lab\`) w $SRC – popraw generator razem z plikiem źródłowym" >&2
    exit 1
  }
  cat "$tmp.sub" > "$tmp"

  # 2. Blok hosta laboratorium – na końcu pliku (nazwa dosłowna i tak wygrywa z `*.`). Wyłącznie:
  #    pliki laboratorium z wolumenu statycznego (`root /srv` + ścieżka `/static/notebook-lab/…` =
  #    `/srv/static/notebook-lab/…`) z nagłówkami fragmentu `(notebook_lab)` – CSP zawężona do ścieżki,
  #    COOP/COEP/CORP; `{scheme}://{hostport}` jest tu originem laboratorium – oraz notatnik startowy
  #    z `web` (podpisany token, bez sesji – QC-02 § 6). Reszta: 404. `Referrer-Policy: strict-origin`
  #    – adres laboratorium niesie token w `?fromURL=`, na zewnątrz wychodzi sam origin, a po nim
  #    serwis rozpoznaje żądania z laboratorium (QC-02 § 4). Poza ścieżką laboratorium CSP `sandbox`.
  #    Bez `import maintenance`: pliki laboratorium nie zależą od `web`.
  {
    printf '\n%s\n' '# Wygenerowane przez scripts/render_caddyfile.sh z NOTEBOOK_LAB_HOST – nie edytuj tego pliku.'
    printf '%s\n' '# Laboratorium notatników kwantowych na osobnym hoście (docs/tasks/QC-02.md § 3).'
    printf '%s {\n' "$LAB_HOST"
    # Przy subdomenach platformy – przypięcie zwykłego certyfikatu jak w `live.`/`dj.` (komentarz przy
    # wstawkach awk wyżej): bez niego `lab.<domena>` trafiałby do polityki on-demand bloku `*.`.
    if [ "$SUBDOMAINS_ON" = "1" ]; then
      printf '%s\n' \
        '    # Zwykły certyfikat (nie on-demand bloku *.) – scripts/render_caddyfile.sh, PLATFORM_SUBDOMAINS=1.' \
        '    tls force_automate {' '        key_type p256' '    }'
    fi
    cat <<'EOF'
    import notebook_lab
    encode gzip zstd
    request_body {
        max_size 1MB
    }
    handle /static/notebook-lab/* {
        header Cache-Control "public, max-age=31536000, immutable"
        root * /srv
        file_server
    }
    handle /notebook-starter/* {
        reverse_proxy web:8000 {
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }
    handle {
        respond 404
    }
    @lab_other not path /static/notebook-lab/*
    header @lab_other Content-Security-Policy "default-src 'none'; frame-ancestors 'none'; sandbox"
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin"
        Cross-Origin-Resource-Policy "same-origin"
    }
}
EOF
  } >> "$tmp"
fi

mkdir -p "$(dirname "$OUT")"
cat "$tmp" > "$OUT"
# Podsumowanie rozszerzane tylko o przełączniki włączone – przy wyłączonych linijka jest ta sama
# co dotąd (log wdrożenia wygląda tak, jak wyglądał).
extras=""
[ "$SUBDOMAINS_ON" = "1" ] && extras="$extras, subdomeny platformy: włączone"
[ "$DJCMS_ON" = "1" ] && extras="$extras, dj. (django CMS): włączone, DJCMS_PRIMARY=$PRIMARY_ON ($DJCMS_MODE)"
[ "$LIVEKIT_ON" = "1" ] && extras="$extras, live. (LiveKit): włączone"
[ "$ERRORS_ON" = "1" ] && extras="$extras, errors. (GlitchTip): włączone"
[ -n "$LAB_HOST" ] && extras="$extras, laboratorium notatników: $LAB_HOST"
echo "render_caddyfile: $OUT (domen dodatkowych: $added$extras)"
