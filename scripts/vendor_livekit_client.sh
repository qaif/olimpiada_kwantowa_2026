#!/usr/bin/env bash
# Wgrywa SDK przeglądarkowe LiveKit (`livekit-client`, Apache-2.0) do backend/static/vendor/ – bez CDN.
#
# Użycie (z katalogu repo, na maszynie z dostępem do registry.npmjs.org; wynik się commituje):
#   scripts/vendor_livekit_client.sh            # wersja przypięta niżej
#   LIVEKIT_CLIENT_VERSION=2.x.y scripts/vendor_livekit_client.sh
#
# Co robi i dlaczego tak:
#   1. pobiera metadane wersji z rejestru npm i z nich – a nie z naszej pamięci – bierze adres paczki
#      i jej sumę `dist.integrity` (SHA-512, SRI),
#   2. pobiera paczkę .tgz i SPRAWDZA ją z tą sumą; rozjazd = koniec skryptu, nic nie trafia do repo,
#   3. wypakowuje wyłącznie `dist/livekit-client.umd.js` (paczka UMD – globalny `LivekitClient`,
#      ładowany `<script nonce defer>` przez szablon pokoju) i plik licencji,
#   4. zapisuje obok VERSION (wersja + integrity paczki) i SHA384 pliku UMD (do porównania przy
#      przeglądzie i do ewentualnego atrybutu `integrity`).
# Po wgraniu: `git add backend/static/vendor/livekit-client` i commit z wersją w opisie. Aktualizacja =
# zmiana LIVEKIT_CLIENT_VERSION, ponowny przebieg, test pokoju na serwerze testowym (docs/OPERACJE.md § 28).
set -euo pipefail

VERSION="${LIVEKIT_CLIENT_VERSION:-2.22.3}"
DEST="backend/static/vendor/livekit-client"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[[ -d backend/static ]] || { echo "Uruchom z katalogu głównego repozytorium." >&2; exit 1; }
command -v curl >/dev/null && command -v python3 >/dev/null || { echo "Potrzebne: curl, python3." >&2; exit 1; }

echo "1/4 metadane livekit-client@${VERSION} z registry.npmjs.org"
curl -fsSL "https://registry.npmjs.org/livekit-client/${VERSION}" -o "$WORK/meta.json"
read -r TARBALL INTEGRITY LICENSE < <(python3 - "$WORK/meta.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))
print(meta["dist"]["tarball"], meta["dist"]["integrity"], meta.get("license", "?"))
PY
)
[[ "$LICENSE" == "Apache-2.0" ]] || { echo "Nieoczekiwana licencja: $LICENSE" >&2; exit 1; }
[[ "$TARBALL" == https://registry.npmjs.org/* ]] || { echo "Nieoczekiwany adres paczki: $TARBALL" >&2; exit 1; }

echo "2/4 paczka i suma ${INTEGRITY%%-*}"
curl -fsSL "$TARBALL" -o "$WORK/pkg.tgz"
python3 - "$WORK/pkg.tgz" "$INTEGRITY" <<'PY'
import base64, hashlib, sys
algorithm, expected = sys.argv[2].split("-", 1)
digest = base64.b64encode(hashlib.new(algorithm, open(sys.argv[1], "rb").read()).digest()).decode()
if digest != expected:
    sys.exit(f"Suma paczki NIE zgadza się z rejestrem ({algorithm}).")
print("   suma zgodna")
PY

echo "3/4 wypakowanie dist/livekit-client.umd.js i licencji"
tar -xzf "$WORK/pkg.tgz" -C "$WORK" package/dist/livekit-client.umd.js package/LICENSE
mkdir -p "$DEST"
# Ostatni wiersz paczki to `//# sourceMappingURL=livekit-client.umd.js.map`, a mapy nie dostarczamy:
# ManifestStaticFilesStorage (collectstatic na produkcji) traktuje to jak odwołanie do pliku i bez
# niego kończy się ValueError – `web` nie wstaje. Usuwamy dokładnie ten jeden wiersz (deterministycznie)
# i zapisujemy w VERSION, że plik różni się od paczki; integrity w VERSION dotyczy paczki z npm,
# a SHA384 – pliku po tej zmianie (apps/webinars/tests/test_static.py pilnuje obu reguł).
python3 - "$WORK/package/dist/livekit-client.umd.js" "$DEST/livekit-client.umd.js" <<'PY'
import re, sys
data = open(sys.argv[1], "rb").read()
stripped = re.sub(rb"\n//# sourceMappingURL=[^\n]*\n?\Z", b"\n", data)
if b"sourceMappingURL" in stripped:
    sys.exit("Nieoczekiwane odwołanie do mapy źródeł w środku pliku – sprawdź paczkę ręcznie.")
open(sys.argv[2], "wb").write(stripped)
PY
cp "$WORK/package/LICENSE" "$DEST/LICENSE"

echo "4/4 VERSION i SHA384"
printf 'livekit-client %s\nnpm integrity %s\nlicense %s\n' "$VERSION" "$INTEGRITY" "$LICENSE" > "$DEST/VERSION"
printf '%s\n' "modified: usunięty ostatni wiersz '//# sourceMappingURL=livekit-client.umd.js.map' (mapy nie dostarczamy)" >> "$DEST/VERSION"
python3 - "$DEST/livekit-client.umd.js" > "$DEST/SHA384" <<'PY'
import base64, hashlib, sys
print("sha384-" + base64.b64encode(hashlib.sha384(open(sys.argv[1], "rb").read()).digest()).decode())
PY
echo "Gotowe: $DEST ($(wc -c < "$DEST/livekit-client.umd.js") B). Zacommituj katalog razem z VERSION i SHA384."
