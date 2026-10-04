#!/usr/bin/env bash
# Wgrywa silnik audytu dostępności axe-core (MPL-2.0) do e2e/vendor/axe-core/ – bez CDN.
#
# Plik służy WYŁĄCZNIE narzędziom testowym (e2e/a11y/, docs/tasks/A11Y-01.md): Playwright wstrzykuje
# go do badanej strony przez protokół DevTools. Nie leży w backend/static, więc collectstatic go nie
# widzi i nigdy nie trafia do przeglądarki użytkownika ani do obrazu `web`.
#
# Użycie (z katalogu repo, na maszynie z dostępem do registry.npmjs.org; wynik się commituje):
#   scripts/vendor_axe_core.sh               # wersja przypięta niżej
#   AXE_CORE_VERSION=4.x.y scripts/vendor_axe_core.sh
#
# Wzorzec: scripts/vendor_livekit_client.sh. Co robi i dlaczego tak:
#   1. pobiera metadane wersji z rejestru npm i z nich – a nie z naszej pamięci – bierze adres paczki,
#      jej sumę `dist.integrity` (SHA-512, SRI) i licencję (musi być MPL-2.0),
#   2. pobiera paczkę .tgz i SPRAWDZA ją z tą sumą; rozjazd = koniec skryptu, nic nie trafia do repo,
#   3. wypakowuje wyłącznie `axe.min.js` i plik licencji (bez mapy źródeł; ewentualny wiersz
#      `sourceMappingURL` jest usuwany deterministycznie),
#   4. zapisuje obok VERSION (wersja + integrity paczki + licencja) i SHA384 pliku (porównanie przy
#      przeglądzie; `e2e/a11y/axe.py` sprawdza go przed każdym przebiegiem).
# Aktualizacja = zmiana AXE_CORE_VERSION, ponowny przebieg, `scripts/a11y.sh` i przegląd różnic
# w e2e/a11y/baseline.json (nowa wersja axe potrafi dołożyć regułę – docs/OPERACJE.md § 50).
set -euo pipefail

VERSION="${AXE_CORE_VERSION:-4.13.0}"
DEST="e2e/vendor/axe-core"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[[ -d e2e ]] || { echo "Uruchom z katalogu głównego repozytorium." >&2; exit 1; }
# Na Windows `python3` bywa atrapą ze Sklepu – wolno wskazać interpreter zmienną PYTHON.
PY="${PYTHON:-python3}"
command -v curl >/dev/null && command -v "$PY" >/dev/null || { echo "Potrzebne: curl, python3 (albo PYTHON=…)." >&2; exit 1; }

echo "1/4 metadane axe-core@${VERSION} z registry.npmjs.org"
curl -fsSL "https://registry.npmjs.org/axe-core/${VERSION}" -o "$WORK/meta.json"
read -r TARBALL INTEGRITY LICENSE < <("$PY" - "$WORK/meta.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
print(meta["dist"]["tarball"], meta["dist"]["integrity"], meta.get("license", "?"))
PY
)
LICENSE="${LICENSE%$'\r'}"
[[ "$LICENSE" == "MPL-2.0" ]] || { echo "Nieoczekiwana licencja: $LICENSE" >&2; exit 1; }
[[ "$TARBALL" == https://registry.npmjs.org/* ]] || { echo "Nieoczekiwany adres paczki: $TARBALL" >&2; exit 1; }
[[ "$INTEGRITY" == sha512-* ]] || { echo "Rejestr nie podał sumy SHA-512: $INTEGRITY" >&2; exit 1; }

echo "2/4 paczka i suma ${INTEGRITY%%-*}"
curl -fsSL "$TARBALL" -o "$WORK/pkg.tgz"
"$PY" - "$WORK/pkg.tgz" "$INTEGRITY" <<'PY'
import base64, hashlib, sys
algorithm, expected = sys.argv[2].strip().split("-", 1)
digest = base64.b64encode(hashlib.new(algorithm, open(sys.argv[1], "rb").read()).digest()).decode()
if digest != expected:
    sys.exit(f"Suma paczki NIE zgadza się z rejestrem ({algorithm}).")
print("   suma zgodna")
PY

echo "3/4 wypakowanie axe.min.js i licencji"
tar -xzf "$WORK/pkg.tgz" -C "$WORK" package/axe.min.js package/LICENSE
mkdir -p "$DEST"
"$PY" - "$WORK/package/axe.min.js" "$DEST/axe.min.js" <<'PY'
import re, sys
data = open(sys.argv[1], "rb").read()
stripped = re.sub(rb"\n//# sourceMappingURL=[^\n]*\n?\Z", b"\n", data)
if b"sourceMappingURL" in stripped:
    sys.exit("Nieoczekiwane odwołanie do mapy źródeł w środku pliku – sprawdź paczkę ręcznie.")
open(sys.argv[2], "wb").write(stripped)
print("   zmodyfikowany" if stripped != data else "   bez zmian względem paczki")
PY
cp "$WORK/package/LICENSE" "$DEST/LICENSE"

echo "4/4 VERSION i SHA384"
printf 'axe-core %s\nnpm integrity %s\nlicense %s\n' "$VERSION" "${INTEGRITY%$'\r'}" "$LICENSE" > "$DEST/VERSION"
"$PY" - "$DEST/axe.min.js" > "$DEST/SHA384" <<'PY'
import base64, hashlib, sys
print("sha384-" + base64.b64encode(hashlib.sha384(open(sys.argv[1], "rb").read()).digest()).decode())
PY
echo "Gotowe: $DEST ($(wc -c < "$DEST/axe.min.js") B). Zacommituj katalog razem z VERSION i SHA384."
