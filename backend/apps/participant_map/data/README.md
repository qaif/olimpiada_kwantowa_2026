# Granice województw dla mapy uczestników (MAP-01)

Plik `wojewodztwa.geojson` – 16 wielokątów województw w WGS 84 (długość, szerokość), właściwość
`slug` to wartość `apps.accounts.models.Voivodeship`. Czyta go `apps/participant_map/geometry.py`
(rzut i ścieżki SVG mapy) oraz kontrola punktów miejscowości (czy punkt leży w swoim województwie).

## Źródło i licencja

- dane pierwotne: **Państwowy Rejestr Granic (PRG)**, Główny Urząd Geodezji i Kartografii –
  zbiór udostępniany od 31.07.2020 bez opłat i do dowolnego ponownego wykorzystania (nowelizacja
  ustawy Prawo geodezyjne i kartograficzne z 16.04.2020 – dane otwarte GUGiK);
  przy publikacji podajemy źródło („Granice województw: Państwowy Rejestr Granic, GUGiK” w przypisie
  strony),
- postać GeoJSON: repozytorium [ppatrzyk/polska-geojson](https://github.com/ppatrzyk/polska-geojson)
  (granice województw przekonwertowane z plików SHP PRG), licencja repozytorium **MIT** – zachowujemy
  informację o pochodzeniu w polach `source` i `license` pliku i w tym opisie.

GADM nie jest tu używany (jego licencja ogranicza użycie do celów niekomercyjnych i nie pozwala na redystrybucję bez zgody) –
tylko PRG przez wskazane repozytorium.

## Przetworzenie

Wykonane jednorazowo przy MAP-01 (skrypt poza repozytorium, opis wystarcza do powtórzenia):

1. nazwy w pliku źródłowym mają zepsute kodowanie (U+FFFD w miejscu polskich liter) – województwa
   dopasowane po polu `id` (1 śląskie, 2 opolskie, 3 wielkopolskie, 4 zachodniopomorskie,
   5 świętokrzyskie, 6 kujawsko-pomorskie, 7 podlaskie, 8 dolnośląskie, 9 podkarpackie,
   10 małopolskie, 11 pomorskie, 12 warmińsko-mazurskie, 13 łódzkie, 14 mazowieckie, 15 lubelskie,
   16 lubuskie) i sprawdzone środkami ciężkości,
2. uproszczenie Douglasa–Peuckera z tolerancją 0,01° (ok. 1 km; na mapie poniżej piksela),
   8192 → 2977 punktów, współrzędne zaokrąglone do 4 miejsc po przecinku.

Granice województw są stałe od 1999 r.; plik nie wymaga odświeżania przy wdrożeniu.
