# MAP-01: Mapa uczestników w sekcji „Dokumenty”

## 0. Cel

Organizator zaakceptował jednorazowo wygenerowaną mapę „Uczestnicy według miast” (21.11.2026) i chce
jej na stałe w serwisie: publiczna strona `/dokumenty/mapa-uczestnikow/` – kafle podsumowania, mapa
Polski z granicami województw i kółkami miejscowości, pełna lista miejscowości z wyszukiwarką.
Dodatkowo (prośba organizatora z tego samego dnia): koordynator włącza mapę, ustawia próg ochrony
i odświeża ją na żądanie; mapa ma też kartę w spisie `/dokumenty/`.

## 1. Strona publiczna

1. `GET /dokumenty/mapa-uczestnikow/` (`web:participant-map`, `apps.participant_map.views`), tytuł
   „Mapa uczestników”, w nagłówku „stan na …” (chwila wyliczenia agregatu).
2. Kafle: uczestnicy, miejscowości (łącznie z tymi pod progiem), szkoły z wykazu SIO, udział
   5 największych miejscowości.
3. Mapa: SVG z serwera – 16 województw (`apps/participant_map/data/wojewodztwa.geojson`), kółka
   miejscowości (pole ∝ liczba uczniów, `geometry.radius`), podpisy 10 największych, legenda.
   Kółko ma `<title>` (podpowiedź przeglądarki bez skryptu).
4. Lista: wszystkie miejscowości od progu w górę (miejsce, nazwa, województwo, słupek, liczba)
   i na końcu „pozostałe miejscowości” województw. Pozycja z punktem to odnośnik `#mu-N` do kółka,
   które `:target` podświetla bez skryptu.
5. Skrypt `participant_map/participant-map.js` (plik statyczny z nonce'em, bez skryptu w dokumencie):
   wyszukiwarka bez polskich znaków (wiersz niesie `data-search` złożony `apps.core.text.fold`),
   podświetlenie kółka i podpis „Miejscowość (województwo): liczba” po kliknięciu w wiersz, wybór
   wiersza po kliknięciu w kółko. Bez skryptu pole wyszukiwania jest ukryte, mapa i lista widoczne.
6. Styl wyłącznie z tokenów `static/css/app.css` (tryb ciemny i wysoki kontrast bez dodatkowych
   reguł) + `statistics.css` (kafle, słupki) + `participant_map/participant-map.css`.
7. Przypisy: źródło danych, próg ochrony, liczba uczestników ze szkołą spoza wykazu, liczba
   miejscowości bez punktu, atrybucja OpenStreetMap (ODbL) i PRG (GUGiK).

## 2. Dane i ochrona

1. Uczestnicy bieżącej edycji konkursu żądania – definicja z lejka i komunikatów (wpis do etapu
   edycji albo konto założone od powstania edycji), bez kont zanonimizowanych
   (`services.edition_participants`).
2. Grupowanie po `school_ref__city_parent` + `school_ref__voivodeship` (gmina szkoły z wykazu SIO,
   dzielnice dużych miast złączone). Szkoła spoza wykazu – wyłącznie w liczbie łącznej.
3. **Próg ochrony**: miejscowość z mniej niż `min_town_count` uczestnikami (domyślnie 3,
   `participant_map.MapSettings`, nie niżej niż 3) nie pojawia się z nazwą ani z kółkiem – jej
   uczniowie trafiają do „pozostałych miejscowości” województwa. Żadnych nazw szkół ani danych osób.
4. Koszt: na zimno pięć zapytań niezależnie od liczby uczestników i miejscowości (edycja, próg,
   sumy, `GROUP BY`, punkty), na ciepło zero – agregat w pamięci podręcznej (`PARTICIPANT_MAP_CACHE_SECONDS`,
   domyślnie 3600 s) pod kluczem konkursu. Budżet strony: `apps/core/tests/query_budgets.py`.
5. Rejestr czynności 1.29: wiersz warunkowy „mapa-uczestnikow” (flaga `participant_map`,
   `apps/participant_map/register.py`) – publikacja zbiorczej statystyki według miejscowości.

## 3. Punkty miejscowości

1. Tabela `schools.TownLocation(city_parent, voivodeship, latitude, longitude)` (migracja
   `schools.0007_town_location`), unikalna para (gmina, województwo).
2. Fixture `backend/apps/schools/fixtures/miejscowosci-wspolrzedne.json` dla **wszystkich** par
   (gmina, województwo) z wykazu szkół – OSM Nominatim, zapytanie strukturalne `city` + `state`
   („województwo …”), `countrycodes=pl`, ogólny User-Agent, 1 zapytanie/s z pamięcią na dysku; każdy
   punkt sprawdzony, czy leży w swoim województwie (`geometry.contains`; kandydat spoza województwa
   odrzucony, brak trafienia – miejscowość pominięta). Plik niesie źródło i licencję (ODbL).
3. Komenda `manage.py seed_town_locations` (idempotentna, tabela = lustro pliku, unieważnia agregaty
   wszystkich konkursów) – przy każdym wdrożeniu, zaraz po `seed_schools` (`scripts/deploy.sh`).
4. Miejscowość bez punktu jest na liście, na mapie nie; licznik „bez punktu na mapie” na stronie
   i nazwy na ekranie koordynatora.

## 4. Granice województw – licencja

Państwowy Rejestr Granic (GUGiK, dane otwarte od 31.07.2020) w postaci GeoJSON z repozytorium
ppatrzyk/polska-geojson (licencja MIT), uproszczone (0,01°). Opis: `apps/participant_map/data/README.md`.

## 5. Flaga, menu i panel

1. Flaga konkursu `participant_map` (domyślnie wyłączona – IQO jest międzynarodowe). Wyłączona: 404,
   brak pozycji w menu i karty w spisie dokumentów. Migracji włączającej nie ma: baza testowa ma
   konkurs `kwantowa` z migracji `tenancy.0002`, a inwarianty wymagają pustych flag Konkursu #1 –
   mapę włącza koordynator w panelu albo operator `manage.py participant_map kwantowa --enable`.
2. Menu „Dokumenty” (Wagtail, `apps.cms.context_processors.participant_map_entry`): pozycja
   „Mapa uczestników” na końcu listy, aktywna na stronie mapy. djcms: `chrome.links.participant_map`
   → ostatnia pozycja w `dj/menu/nav.html`. Spis `/dokumenty/`: karta „Mapa uczestników” pod
   nagłówkiem „Statystyki uczestników” (Wagtail i djcms).
3. Panel koordynatora „Raporty → Mapa uczestników” (`/coordinator/participant-map/`, bez flagi):
   stan agregatu i data ostatniego przeliczenia, przełącznik strony, próg ochrony (3–50), przycisk
   „Odśwież mapę uczestników” (POST, rola sprawdzana w serwisie, limit `participant_map` 30/h na
   konto, audyt `participant_map.refreshed`; zapis ustawień – `participant_map.settings_changed`).
   Odświeżenie przelicza agregat i unieważnia zapisane strony gościa (`apps.web.page_cache`).
4. Kontrakt tras djcms: `dokumenty/mapa-uczestnikow` w `nested_paths`
   (`backend/djcms_contract/app_routes.{json,env}`).

## 6. Wdrożenie

1. Migracje `schools.0007_town_location`, `participant_map.0001_initial` (entrypoint).
2. `seed_town_locations` – sam, w `scripts/deploy.sh`.
3. Włączenie: panel koordynatora albo `docker compose exec -T web python manage.py participant_map
   kwantowa --enable` (od razu przelicza agregat).

## 7. Dalsze kroki

1. Wybór edycji (dziś wyłącznie bieżąca) i porównanie edycji na jednej mapie.
2. Poprawki punktów miejscowości – w fixture'ie (np. gmina o nazwie wspólnej z inną miejscowością
   w tym samym województwie, gdzie geokoder trafił w niewłaściwą).
