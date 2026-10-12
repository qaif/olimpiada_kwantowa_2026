# shellcheck shell=bash
# Odczyt pliku .env BEZ wykonywania go jako kodu powłoki (audyt bezpieczeństwa 10.10.2026, S17).
#
# Biblioteka – nie uruchamia się jej wprost. Wczytują ją scripts/backup.sh, scripts/backup_verify.sh
# i scripts/restore.sh. Do 10.10.2026 robiły one `set -a; . ./.env`, co miało dwie wady naraz:
#   1. wartość ze spacją bez cudzysłowu – a tak każe ją wpisać sam .env.example
#      (`CERT_SIGN_REASON=Dokument wystawiony przez …`, `EXTRA_DOMAINS=a.pl www.a.pl`) – powłoka
#      czyta jako „zmienna=Dokument, potem polecenie `wystawiony`”: `command not found`, kod 127 pod
#      `set -e`, czyli od tej nocy brak kopii, a `restore.sh` pada w środku awarii;
#   2. .env był WYKONYWANY jako kod roota z crona: `$(…)` albo odwrócony apostrof w wartości (kto
#      wkleił hasło z takim znakiem albo dopisał coś do pliku) uruchamiał polecenie.
# Tutaj każda linijka jest tylko tekstem – tak jak czyta ją docker compose:
#   - `NAZWA=wartość`, opcjonalnie z `export ` na początku; komentarze `#` i puste linie pomijane;
#     ostatnie wystąpienie nazwy wygrywa (jak w compose i jak przy dawnym `source`);
#   - `'…'` – dosłownie, do zamykającego apostrofu (tak wpisuje się np. BACKUP_DRIVE_TOKEN z JSON-em);
#   - `"…"` – do zamykającego cudzysłowu; `\"` i `\\` w środku to cudzysłów i ukośnik;
#   - bez cudzysłowu – wartość do końca linii z obciętymi skrajnymi odstępami, a ` #…` (spacja
#     i krzyżyk) to komentarz; spacje w środku zostają (`Dokument wystawiony przez …`);
#   - CR na końcu (plik zapisany z Windowsa) jest zdejmowany;
#   - `${…}`, `$(…)` i odwrócone apostrofy NIE są rozwijane ani wykonywane – to zwykłe znaki.
# Różnica wobec compose: compose rozwija `${INNA}` w wartości; tutaj nie (wygenerowany przez
# scripts/deploy.sh .env takich odwołań nie ma). Zmienne NIE są eksportowane – skrypty używają ich
# same, a do kontenerów przekazują jawnie (`-e`, plik env 600), więc procesy potomne ich nie dziedziczą.

# Nazwy, których .env nie może podmienić skryptowi: zmienne powłoki i środowiska procesu, od których
# zależy, CO się uruchamia (PATH, IFS, BASH_ENV…) albo gdzie trafiają pliki tymczasowe.
_ENV_DENY_RE='^(PATH|IFS|HOME|SHELL|USER|LOGNAME|PWD|OLDPWD|ENV|BASH_ENV|BASH_[A-Z_]*|LD_[A-Z_]*|PS[0-9]|SHELLOPTS|BASHOPTS|CDPATH|GLOBIGNORE|PROMPT_COMMAND|TMPDIR|UID|EUID|PPID|GROUPS|HOSTNAME|RANDOM|SECONDS|LINENO|FUNCNAME|OPTIND|OPTARG)$'

# _env_value <surowa wartość po pierwszym `=`> – wartość tak, jak przeczyta ją docker compose.
_env_value() {
  local raw="$1" out="" ch i
  # Wiodące odstępy (`NAZWA= wartość`).
  raw="${raw#"${raw%%[![:space:]]*}"}"
  case "$raw" in
    \'*)
      out="${raw#\'}"
      out="${out%%\'*}"
      ;;
    \"*)
      raw="${raw#\"}"
      i=0
      while [ "$i" -lt "${#raw}" ]; do
        ch="${raw:$i:1}"
        if [ "$ch" = '\' ] && [ $((i + 1)) -lt "${#raw}" ]; then
          case "${raw:$((i + 1)):1}" in
            '"' | '\') out="$out${raw:$((i + 1)):1}"; i=$((i + 2)); continue ;;
          esac
        elif [ "$ch" = '"' ]; then
          break
        fi
        out="$out$ch"
        i=$((i + 1))
      done
      ;;
    *)
      # Komentarz w linii: odstęp i `#` (sam `#` bez odstępu jest częścią wartości, jak w compose).
      out="${raw%%[[:space:]]#*}"
      out="${out%"${out##*[![:space:]]}"}"
      ;;
  esac
  printf '%s' "$out"
}

# env_get <NAZWA> [plik=.env] – wartość ostatniego wpisu NAZWA=… (pusty napis, gdy go nie ma).
# Zawsze kod 0: brak wpisu to pusty wynik, a nie błąd (`set -e` w skrypcie wołającym).
env_get() {
  local name="$1" file="${2:-.env}" line=""
  [ -f "$file" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$file" | tail -n 1 || true)"
  [ -n "$line" ] || return 0
  line="${line%$'\r'}"
  _env_value "${line#*=}"
}

# env_load [plik=.env] – każdy wpis NAZWA=wartość z pliku jako zmienna powłoki (nie eksportowana).
# Nazwy spoza [A-Za-z_][A-Za-z0-9_]* i nazwy z _ENV_DENY_RE są pomijane (z ostrzeżeniem na stderr).
# Zmienne lokalne z prefiksem `_el_`: `printf -v` ustawia nazwę w najbliższym zasięgu, więc wpis
# `line=…` w .env nadpisałby zwykłą zmienną lokalną funkcji zamiast ustawić globalną.
env_load() {
  local _el_file="${1:-.env}" _el_line _el_key
  [ -f "$_el_file" ] || return 0
  while IFS= read -r _el_line || [ -n "$_el_line" ]; do
    _el_line="${_el_line%$'\r'}"
    _el_line="${_el_line#"${_el_line%%[![:space:]]*}"}"
    case "$_el_line" in '' | '#'*) continue ;; esac
    case "$_el_line" in
      export[[:space:]]*) _el_line="${_el_line#export}"; _el_line="${_el_line#"${_el_line%%[![:space:]]*}"}" ;;
    esac
    _el_key="${_el_line%%=*}"
    [ "$_el_key" != "$_el_line" ] || continue
    [[ $_el_key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ $_el_key =~ $_ENV_DENY_RE ]]; then
      printf 'UWAGA: %s: pomijam %s (zmienna powłoki, nie konfiguracja)\n' "$_el_file" "$_el_key" >&2
      continue
    fi
    printf -v "$_el_key" '%s' "$(_env_value "${_el_line#*=}")" 2>/dev/null \
      || printf 'UWAGA: %s: nie mogę ustawić %s\n' "$_el_file" "$_el_key" >&2
  done <"$_el_file"
}
