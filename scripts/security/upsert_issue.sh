#!/usr/bin/env bash
# Jedno zgłoszenie (issue) na jeden raport okresowy (SEC-02, docs/OPERACJE.md § 47.3–47.4).
#
# Użycie (w workflow, z GH_TOKEN o uprawnieniu `issues: write`):
#   scripts/security/upsert_issue.sh <etykieta> <tytuł> <plik-treści> <skrót-wyników> <stan>
#     <stan> = attention  → otwórz zgłoszenie albo zaktualizuj otwarte (komentarz tylko przy ZMIANIE wyników),
#              clean      → jeśli otwarte istnieje: komentarz „czysto” i zamknięcie; inaczej nic.
#
# Dlaczego tak, a nie nowe zgłoszenie co tydzień: raport okresowy z tą samą treścią co tydzień
# uczy wszystkich go ignorować. Jedno otwarte zgłoszenie z aktualną treścią, komentarz (czyli
# powiadomienie) wyłącznie wtedy, gdy wynik się zmienił – skrót wyników siedzi w treści jako
# komentarz HTML i jest porównywany z poprzednim.
set -euo pipefail

label="$1"; title="$2"; body_file="$3"; fingerprint="$4"; state="$5"
run_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"

# Etykieta musi istnieć, zanim `gh issue create --label` jej użyje; `--force` = utwórz albo popraw.
gh label create "$label" --color B60205 --description "Raport okresowy SEC-02 (docs/OPERACJE.md § 47)" --force >/dev/null

number="$(gh issue list --label "$label" --state open --limit 1 --json number --jq '.[0].number // empty')"

if [[ "$state" == clean ]]; then
  if [[ -n "$number" ]]; then
    gh issue comment "$number" --body "Najnowszy przebieg nie ma uwag – zamykam. Raport: $run_url"
    gh issue close "$number"
  fi
  exit 0
fi

full_body="$(mktemp)"
trap 'rm -f "$full_body"' EXIT
{
  cat "$body_file"
  printf '\n---\nOstatni przebieg: %s (%s UTC)\n<!-- sec02-fingerprint: %s -->\n' \
    "$run_url" "$(date -u +'%Y-%m-%d %H:%M')" "$fingerprint"
} > "$full_body"

if [[ -z "$number" ]]; then
  gh issue create --title "$title" --label "$label" --body-file "$full_body"
  exit 0
fi

previous="$(gh issue view "$number" --json body --jq '.body' | sed -n 's/.*<!-- sec02-fingerprint: \([0-9a-f]*\) -->.*/\1/p')"
gh issue edit "$number" --title "$title" --body-file "$full_body" >/dev/null
if [[ "$previous" != "$fingerprint" ]]; then
  gh issue comment "$number" --body "Wyniki się zmieniły – treść zgłoszenia zaktualizowana. Przebieg: $run_url"
fi
