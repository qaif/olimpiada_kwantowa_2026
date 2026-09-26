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
# `PLATFORM_SUBDOMAINS=1` – druga, **wyłączona domyślnie** droga: konkurs zakładany z panelu
# koordynatora dostaje adres `<slug>.<SITE_DOMAIN>` bez wdrożenia i bez wpisu w `.env`. Wtedy (i
# tylko wtedy) generator dokłada blok `*.{$SITE_DOMAIN}` z `tls { on_demand }` oraz opcję globalną
# `on_demand_tls { ask … }`. Zarzut z § 2.5 – „on-demand wystawia certyfikat dowolnemu hostowi” –
# znika dopiero razem z endpointem `ask`: to aplikacja rozstrzyga, czy dana nazwa jest domeną
# aktywnego konkursu, więc limit Let's Encrypt (50 certyfikatów na domenę tygodniowo) zużywają
# wyłącznie konkursy, które naprawdę istnieją. Bez tego przełącznika droga przez `EXTRA_DOMAINS`
# zostaje jedyną i wynik generatora jest dokładnie ten, co dotąd.
#
# `DJCMS_ENABLED=1` – wersja porównawcza serwisu na django CMS pod `dj.<SITE_DOMAIN>`
# (docs/tasks/DJ-01.md § 8.8), **wyłączona domyślnie**. Wtedy (i tylko wtedy) generator dokłada na
# końcu pliku blok `dj.{$SITE_DOMAIN}` (proxy do usługi `djcms`, pliki redaktorów z wolumenu
# `djcms_media`, `X-Robots-Tag: noindex`) oraz odmowę `/internal/*` w bloku domeny głównej i w każdym
# bloku aplikacji z `EXTRA_DOMAINS`: pod `/internal/djcms/v1/` aplikacja główna oddaje djcms-owi dane
# zawodów i ten adres nie może odpowiadać z żadnej nazwy publicznej.
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
  # dane zawodów (`/internal/djcms/v1/`, token w nagłówku), i jest wołany po sieci wewnętrznej
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

tmp="$(mktemp "${TMPDIR:-/tmp}/caddyfile.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.sub"' EXIT
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
  WHY1="$why1" WHY2="$why2" WHY3="$why3" awk -v want_opts="$SUBDOMAINS_ON" '
    BEGIN { opts = 0; guard = 0 }
    {
      if (!guard && $0 == "    handle_path /static/* {") {
        print ENVIRON["WHY1"]
        print ENVIRON["WHY2"]
        print ENVIRON["WHY3"]
        print "    handle /internal/* {"
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
    END { if ((want_opts == "1" && !opts) || !guard) exit 3 }
  ' "$tmp" > "$tmp.sub" || {
    echo "render_caddyfile: nie znalazłem kotwic dla PLATFORM_SUBDOMAINS/DJCMS_ENABLED w $SRC – popraw generator razem z plikiem źródłowym" >&2
    exit 1
  }
  cat "$tmp.sub" > "$tmp"
fi

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
    # jest dzisiaj – to jest warunek zadania T6 (§ 6). Wyjątek: `import maintenance` (strona
    # „Prace techniczne”, fragment zdefiniowany w deploy/Caddyfile) – konkurs z EXTRA_DOMAINS
    # ma w czasie przerwy pokazywać to samo co domena główna, a nie pusty błąd 502.
    cat >> "$tmp" <<EOF

# Wygenerowane przez scripts/render_caddyfile.sh z EXTRA_DOMAINS – nie edytuj tego pliku.
$host {
EOF
    # Odmowa `/internal/*` tylko przy włączonym przełączniku (PLATFORM_SUBDOMAINS albo
    # DJCMS_ENABLED): przy wyłączonych ten plik ma być kopią `deploy/Caddyfile` co do bajtu.
    if [ "$GUARD_ON" = "1" ]; then internal_guard >> "$tmp"; fi
    cat >> "$tmp" <<EOF
    import maintenance
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
    encode gzip zstd
    request_body {
        max_size {$MAX_UPLOAD_MB}MB
    }
    handle_path /static/* {
        root * /srv/static
        file_server
    }
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
  # Czego tu świadomie NIE ma:
  # - `import maintenance` – przerwa techniczna głównego serwisu nie wyłącza `dj.`; djcms, gdy
  #   API aplikacji głównej nie odpowiada, sam pokazuje stronę z komunikatem (DJ-01 § 8.3);
  # - `tls { on_demand }` – zwykły certyfikat (HTTP-01) wystawiany przy starcie proxy; rekord DNS
  #   `*.<domena>` już wskazuje serwer (deploy/dns-olimpiadakwantowa.pl.zone);
  # - `/static/*` z wolumenu – statyki djcms serwuje WhiteNoise z obrazu djcms (DJ-01 § 1.1).
  #
  # `/media/*` (pliki wgrane przez redaktorów w filerze) podaje Caddy z wolumenu `djcms_media`
  # zamontowanego do proxy tylko do odczytu przez nakładkę `docker-compose.djcms.yml` – montaż
  # istnieje wyłącznie przy DJCMS_ENABLED=1 (scripts/deploy.sh dopisuje ją do COMPOSE_FILE
  # w .env), więc przy wyłączonym przełączniku konfiguracja `proxy` jest ta sama co przed DJ-01.
  # Plik od redaktora nie może wykonać skryptu w domenie `dj.` (reguła 12 z DJ-01 § 7):
  # `nosniff` dla wszystkich i CSP z `sandbox` dla wszystkich **poza PDF** – plik otwarty wprost
  # dostaje wtedy unikalny, pusty origin bez skryptów. Odwrócona lista (wszystko poza PDF), a nie
  # lista typów aktywnych z DJ-01 § 8.8 (`path /media/*.svg …`), z dwóch powodów:
  # 1. `*` w środku wzorca `path` Caddy'ego nie przechodzi przez `/` (semantyka `path.Match`),
  #    a filer trzyma pliki w podkatalogach (`/media/filer_public/ab/cd/<uuid>/x.svg`) – wzorzec
  #    ze specyfikacji nie pasował do żadnego prawdziwego pliku (sprawdzone na caddy:2.8,
  #    test: scripts/tests/render_caddyfile_test.sh, przypadek z działającym Caddym);
  # 2. typów, które przeglądarka wykona jako dokument, jest więcej niż sześć (XML z arkuszem
  #    XSLT, `.xht`, `.shtml`, `.rdf`, `.mml` …), a obrazy i filmy CSP odpowiedzi nie dotyczy
  #    (osadzone w `<img>`/`<video>` ignorują ją, otwarte wprost wyświetlają się bez skryptów).
  # PDF-a nie: z `sandbox` przeglądarka (Chrome) odmawia wyświetlenia go w swoim podglądzie.
  # `path_regexp` z `(?i)` – `.PDF` też jest PDF-em; `path` i tak nie rozróżnia wielkości liter.
  cat >> "$tmp" <<'EOF'

# Wygenerowane przez scripts/render_caddyfile.sh przy DJCMS_ENABLED=1 – nie edytuj tego pliku.
# Wersja porównawcza na django CMS (docs/tasks/DJ-01.md). Nieindeksowana. Nazwa dosłowna wygrywa
# z blokiem `*.{$SITE_DOMAIN}`; certyfikat zwykły (HTTP-01), rekord DNS `*` już wskazuje serwer.
dj.{$SITE_DOMAIN} {
    encode gzip zstd
    request_body {
        max_size {$MAX_UPLOAD_MB}MB
    }
EOF
  internal_guard >> "$tmp"
  cat >> "$tmp" <<'EOF'
    @dj_active_media {
        path /media/*
        not path_regexp (?i)\.pdf$
    }
    header @dj_active_media Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    handle_path /media/* {
        header Cache-Control "public, max-age=86400"
        root * /srv/djcms-media
        file_server
    }
    handle {
        reverse_proxy djcms:8000 {
            header_up X-Forwarded-Proto {scheme}
            # Adres klienta dla blokady prób logowania (djcms ufa mu tylko z adresu proxy).
            header_up X-Real-IP {remote_host}
        }
    }
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
        X-Robots-Tag "noindex, nofollow, noarchive"
    }
}
EOF
fi

mkdir -p "$(dirname "$OUT")"
cat "$tmp" > "$OUT"
# Podsumowanie rozszerzane tylko o przełączniki włączone – przy wyłączonych linijka jest ta sama
# co dotąd (log wdrożenia wygląda tak, jak wyglądał).
extras=""
[ "$SUBDOMAINS_ON" = "1" ] && extras="$extras, subdomeny platformy: włączone"
[ "$DJCMS_ON" = "1" ] && extras="$extras, dj. (django CMS): włączone"
echo "render_caddyfile: $OUT (domen dodatkowych: $added$extras)"
