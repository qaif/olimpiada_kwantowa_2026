#!/usr/bin/env bash
# Test jawnej listy zmiennych środowiska aplikacji w docker-compose.yml (audyt bezpieczeństwa
# 10.10.2026, W6).
#
# Uruchomienie (Git Bash / Linux, z dowolnego katalogu; wymaga `docker compose` i Pythona 3):
#   scripts/tests/compose_env_test.sh
#
# Od 10.10.2026 web, worker i beat NIE dostają całego `.env` (`env_file`), tylko listę `x-app-env`.
# Lista ma dwa sposoby, żeby się zepsuć, i ten test łapie oba:
#   1. ktoś dopisze w backend/ odczyt nowej zmiennej (`env("X")`, `env.int("X")`, `os.environ["X"]`),
#      a nie dopisze jej do compose – na produkcji ustawienie z .env po cichu nie działa (aplikacja
#      bierze wartość domyślną). Przypadek 3;
#   2. ktoś dopisze do listy sekret, którego aplikacja nie czyta (kopie, root MinIO, djcms…) – albo
#      wróci `env_file`. Przypadki 4–6.
# Odczyty zbiera skaner tokenów Pythona (nie `ast`: kod jest w Pythonie 3.14, a na hoście bywa
# starszy interpreter, który składni 3.14 nie sparsuje; tokenizer ją przyjmuje). Konfigurację
# compose'a – `docker compose config` z `.env.example`, jak scripts/tests/compose_profiles_test.sh.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="$ROOT/docker-compose.yml"
ENV_FILE="$ROOT/.env.example"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/compose-env-test.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

if ! docker compose version >/dev/null 2>&1; then
  printf 'NIE WYKONANO: brak `docker compose` w PATH – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi
PY=""
for candidate in python3 python; do
  # `python3` na Windows bywa atrapą ze Sklepu, która nic nie uruchamia – sprawdzamy, że działa.
  if "$candidate" -c 'import sys, tokenize; sys.exit(0)' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  printf 'NIE WYKONANO: brak Pythona 3 w PATH – ten test niczego nie sprawdził.\n' >&2
  exit 2
fi
# Ścieżka dla interpretera z Windows (Git Bash podaje /c/…, Python spod Windows jej nie otworzy).
PYROOT="$(cygpath -m "$ROOT" 2>/dev/null || printf '%s' "$ROOT")"

failures=0
check() {
  if [ "$2" -eq 0 ]; then
    printf 'ok   %s\n' "$1"
  else
    printf 'FAIL %s\n' "$1"
    failures=$((failures + 1))
  fi
}

# Zmienne, które aplikacja czyta, a które CELOWO nie idą do web/worker/beat – z powodem. Każda nowa
# pozycja tutaj to decyzja do opisania, a nie sposób na zielony test.
NOT_PASSED="
MINIO_ROOT_USER          fallback sprzed kont serwisowych S3_*; root MinIO zostaje dla minio, minio-init i kopii
MINIO_ROOT_PASSWORD      jw.
POSTGRES_PASSWORD        hasło jest już w DATABASE_URL (x-app-env)
COORDINATOR_EMAIL        podaje wyłącznie docker compose exec -e (scripts/deploy.sh, krok 6/8)
COORDINATOR_PASSWORD     jw.
"
# Odczyty dynamiczne (nazwa składana w kodzie) – rozwinięte ręcznie. Nowy dynamiczny odczyt = FAIL,
# żeby ktoś spojrzał, jakie nazwy z niego wychodzą.
DYNAMIC='config/settings/base.py f"{prefix}_ACCESS_KEY"|S3_PUBLIC_ACCESS_KEY S3_PRIVATE_ACCESS_KEY
config/settings/base.py f"{prefix}_SECRET_KEY"|S3_PUBLIC_SECRET_KEY S3_PRIVATE_SECRET_KEY
apps/tenancy/setup.py name|'
# Sekrety spoza aplikacji: nie mogą trafić do jej kontenerów pod żadną nazwą z tej listy.
FORBIDDEN_RE='^(BACKUP_[A-Z0-9_]*|REMOTE_[A-Z0-9_]*|MINIO_ROOT_[A-Z_]*|POSTGRES_PASSWORD|REDIS_PASSWORD|DJCMS_SECRET_KEY|DJCMS_DB_PASSWORD|MAINTENANCE_BYPASS_TOKEN|EXTRA_CA_FILE)$'

# --- odczyty w kodzie ------------------------------------------------------------------------------
cat >"$WORK/scan.py" <<'PY'
import io, sys, tokenize, pathlib
root = pathlib.Path(sys.argv[1])
found, dynamic = {}, []
CALLS = {("env",), ("os", "environ", "get"), ("os", "getenv"), ("os", "environ", "setdefault")}
files = sorted(list(root.joinpath("config").rglob("*.py")) + list(root.joinpath("apps").rglob("*.py")))
for path in files:
    rel = path.relative_to(root)
    if "tests" in rel.parts or "migrations" in rel.parts or path.name.startswith("test_") or path.name == "conftest.py":
        continue
    toks = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
            if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    consts = {}
    for i in range(len(toks) - 2):
        if toks[i].type == tokenize.NAME and toks[i + 1].string == "=" and toks[i + 2].type == tokenize.STRING \
                and toks[i + 2].string[:1] in "\"'":
            consts[toks[i].string] = toks[i + 2].string.strip("\"'")
    for i, t in enumerate(toks):
        if t.type != tokenize.NAME or (i and toks[i - 1].string == "."):
            continue
        parts, j = [t.string], i + 1
        while j + 1 < len(toks) and toks[j].string == "." and toks[j + 1].type == tokenize.NAME:
            parts.append(toks[j + 1].string)
            j += 2
        key = tuple(parts)
        if key == ("os", "environ") and j < len(toks) and toks[j].string == "[":
            arg = toks[j + 1]
        elif (key in CALLS or (len(key) == 2 and key[0] == "env")) and j < len(toks) and toks[j].string == "(":
            arg = toks[j + 1]
        else:
            continue
        if arg.type == tokenize.STRING and arg.string[:1] in "\"'":
            found.setdefault(arg.string.strip("\"'"), rel.as_posix())
        elif arg.type == tokenize.NAME and arg.string in consts:
            found.setdefault(consts[arg.string], rel.as_posix())
        else:
            dynamic.append("%s %s" % (rel.as_posix(), arg.string))
for name in sorted(found):
    print("READ", name, found[name])
for item in sorted(set(dynamic)):
    print("DYNAMIC", item)
PY
"$PY" -I "$WORK/scan.py" "$PYROOT/backend" >"$WORK/scan.txt" 2>"$WORK/scan.err"
check "skaner odczytów zmiennych w backend/config i backend/apps" $?
[ -s "$WORK/scan.err" ] && sed 's/^/     /' "$WORK/scan.err"
sed -n 's/^READ \([^ ]*\) .*/\1/p' "$WORK/scan.txt" >"$WORK/reads.txt"
# Entrypoint obrazu (backend/entrypoint.sh): `${NAZWA` i `os.environ["NAZWA"]` z wstawki Pythona.
grep -oE '\$\{[A-Z_][A-Z0-9_]*|os\.environ\["[A-Z_][A-Z0-9_]*"\]' "$ROOT/backend/entrypoint.sh" \
  | sed -E 's/^\$\{//; s/^os\.environ\["//; s/"\]$//' >>"$WORK/reads.txt"
[ "$(wc -l <"$WORK/reads.txt")" -ge 50 ]
check "skaner znalazł odczyty ($(sort -u "$WORK/reads.txt" | wc -l) nazw) – inaczej test niczego by nie sprawdzał" $?

# Odczyty dynamiczne: wyłącznie znane, rozwinięte w DYNAMIC.
unknown=""
while read -r _ file expr; do
  [ -n "${file:-}" ] || continue
  match="$(printf '%s\n' "$DYNAMIC" | awk -F'|' -v k="$file $expr" '$1 == k { print "x" $2 }')"
  if [ -z "$match" ]; then
    unknown="$unknown $file:$expr;"
  else
    for name in ${match#x}; do printf '%s\n' "$name" >>"$WORK/reads.txt"; done
  fi
done < <(grep '^DYNAMIC ' "$WORK/scan.txt")
[ -z "$unknown" ]
check "brak nieznanych odczytów dynamicznych (nazwa składana w kodzie)${unknown:+ – nowe:$unknown dopisz rozwinięcie do DYNAMIC}" $?
# Bez pustych linii i bez CR (Python spod Windows kończy linie CRLF).
tr -d '\r' <"$WORK/reads.txt" | grep . | sort -u >"$WORK/reads.sorted" && mv "$WORK/reads.sorted" "$WORK/reads.txt"

# --- konfiguracja compose'a ------------------------------------------------------------------------
docker compose --env-file "$ENV_FILE" -f "$BASE" config >"$WORK/config.yml" 2>"$WORK/config.err"
check "docker compose config (.env.example)" $?
svc_block() { awk -v h="  $2:" '$0 == h {on=1; next} on && /^  [^ ]/ {on=0} on' "$1"; }
env_keys() {  # env_keys <plik config> <usługa> – nazwy zmiennych z bloku `environment:` usługi
  svc_block "$1" "$2" | awk '/^    environment:$/ {on=1; next} on && /^    [^ ]/ {on=0} on && /^      [A-Za-z_]/ {k=$1; sub(":$", "", k); print k}' | sort -u
}

# 1. Bez `env_file` w kontenerach aplikacji.
for svc in web worker beat; do
  ! svc_block "$WORK/config.yml" "$svc" | grep -qE '^    env_file:'
  check "$svc: bez env_file (pełnego .env)" $?
done

# 2. Ta sama lista w web, worker i beat (worker/beat różnią się tylko wartościami nadpisanymi niżej).
env_keys "$WORK/config.yml" web >"$WORK/web.keys"
for svc in worker beat; do
  env_keys "$WORK/config.yml" "$svc" | cmp -s - "$WORK/web.keys"
  check "$svc: te same nazwy zmiennych co web" $?
done

# 3. Każda zmienna czytana przez aplikację jest przekazana – albo stoi na liście NOT_PASSED.
not_passed="$(printf '%s\n' "$NOT_PASSED" | awk 'NF {print $1}' | sort -u)"
missing="$(comm -23 "$WORK/reads.txt" "$WORK/web.keys" | comm -23 - <(printf '%s\n' "$not_passed") | tr '\n' ' ')"
[ -z "$missing" ]
check "każda zmienna czytana w kodzie jest w x-app-env (docker-compose.yml)${missing:+ – brakuje: $missing}" $?

# 4. Na liście nie ma nic, czego aplikacja nie czyta (martwe wpisy to pierwszy krok do „dopiszmy
#    na wszelki wypadek” – czyli do sekretu, którego kontener nie potrzebuje).
stale="$(comm -13 "$WORK/reads.txt" "$WORK/web.keys" | tr '\n' ' ')"
[ -z "$stale" ]
check "x-app-env bez zmiennych, których aplikacja nie czyta${stale:+ – zbędne: $stale}" $?

# 5. Sekrety spoza aplikacji – w żadnym z trzech kontenerów.
for svc in web worker beat; do
  bad="$(env_keys "$WORK/config.yml" "$svc" | grep -E "$FORBIDDEN_RE" | tr '\n' ' ')"
  [ -z "$bad" ]
  check "$svc: bez sekretów spoza aplikacji (kopie, root MinIO, Redis/Postgres wprost, djcms, przepustka)${bad:+ – są: $bad}" $?
done

# 6. Pozycje NOT_PASSED naprawdę nie są przekazane (lista nie może kłamać).
leaked="$(comm -12 "$WORK/web.keys" <(printf '%s\n' "$not_passed") | tr '\n' ' ')"
[ -z "$leaked" ]
check "zmienne z NOT_PASSED nie trafiają do web${leaked:+ – a trafiają: $leaked}" $?

# 7. Semantyka wpisu bez wartości = `env_file`: wartość z pliku .env (także ze spacją, w cudzysłowie
#    albo bez), a brak linijki = brak zmiennej w kontenerze (`null` w config), a nie pusty napis.
{
  cat "$ENV_FILE"
  echo 'SETUP_TOKEN=token z pliku'
  echo 'ALERT_EMAILS="a@example.org,b@example.org"'
} >"$WORK/env.semantics"
docker compose --env-file "$WORK/env.semantics" -f "$BASE" config >"$WORK/sem.yml" 2>/dev/null
web_env() { svc_block "$1" web | sed -n "s/^      $2: //p"; }
[ "$(web_env "$WORK/sem.yml" SETUP_TOKEN)" = "token z pliku" ] &&
  [ "$(web_env "$WORK/sem.yml" ALERT_EMAILS)" = "a@example.org,b@example.org" ] &&
  [ "$(web_env "$WORK/sem.yml" CERT_SIGN_REASON)" = "Dokument wystawiony przez Olimpiadę Kwantową" ]
check "wartość z .env przechodzi do web (ze spacjami, z cudzysłowem i bez)" $?
[ "$(web_env "$WORK/config.yml" SETUP_TOKEN)" = "null" ] && [ "$(web_env "$WORK/config.yml" DB_POOL_TIMEOUT)" = "null" ]
check "zmienna, której nie ma w .env, nie istnieje w kontenerze (null, nie pusty napis)" $?

if [ "$failures" -ne 0 ]; then
  printf '\n%d test(ów) nie przeszło.\n' "$failures"
  exit 1
fi
printf '\nWszystkie testy środowiska aplikacji w compose przeszły.\n'
