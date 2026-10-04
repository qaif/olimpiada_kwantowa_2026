# REG-01: Kraje zamiast województw w konkursie międzynarodowym (IQO)

## 0. Cel

Konkurs `iqo` (International Quantum Olympiad, iqo-official.org) ma dzielić uczestników na
**kraje**, a nie na polskie województwa. Konkurs `kwantowa` zostaje **dokładnie** jak dziś
(16 województw, flaga `custom_regions` wyłączona).

Stan dziś (sprawdzone w kodzie 2026-10-04):
- `accounts.Region` (models.py:218) + flaga konkursu `custom_regions` + ekran koordynatora
  „Regiony” (`apps/web/views/coordinator_regions.py`) już istnieją; serwis rejestracji
  (`apps/accounts/services.py::_resolve_region`) przy włączonej fladze zapisuje `region` i kopię
  `district = region.code`.
- **Luka:** formularze HTML zawsze mają `voivodeship_field` (`apps/web/forms.py:84`,
  pola `district` w rejestracji, profilu, imporcie, karcie koordynatora, komisji – linie ~830–1349),
  więc przy włączonej fladze uczestnik nadal wybiera województwo, a regionu konkursu nie da się
  wskazać w UI.
- **Luka:** wyświetlanie: `Participant.get_district_display()` (np. katalog czatu, karta
  uczestnika, eksporty, listy) dla kodu spoza `Voivodeship` pokaże surowy kod.
- `create_competition` zakłada zawsze zestaw startowy „Polska + 16 województw + poza Polską”
  (`apps/accounts/regions.py::default_regions_for`).

## 1. Zakres

1. **Formularze:** przy włączonej `custom_regions` każde pole `district` w formularzach
   uczestnika/komisji/koordynatora/importu zastępuje lista **aktywnych** regionów konkursu
   (`Region.objects.for_competition(c).filter(is_active=True)` w kolejności `position, name`),
   z etykietą zależną od poziomu regionów („Kraj” gdy wszystkie aktywne są `COUNTRY`, inaczej
   „Region”), z pustą pozycją „— wybierz —”, wartością = `region.code`, przekazywaną do serwisu
   jako `region`. Przy wyłączonej fladze – zero zmian (te same pola, ten sam HTML; test
   regresji na `kwantowa`).
2. **Wyświetlanie:** jedna funkcja „nazwa regionu uczestnika” (region.name gdy jest, inaczej
   `get_district_display()`), użyta we wszystkich miejscach UI/eksportów/e-maili, gdzie dziś stoi
   `get_district_display` albo surowe `district` dla uczestnika/komisji (przejrzyj `grep`).
   Nagłówki kolumn/etykiety „Województwo” → zależne od konkursu („Kraj”/„Region”/„Województwo”).
3. **Zestaw krajów:** moduł `apps/accounts/countries.py` z listą ISO 3166-1 alfa-2 (wszystkie
   państwa członkowskie ONZ + obserwatorzy + Tajwan, Kosowo, Hongkong, Makau; nazwy angielskie –
   konkurs jest anglojęzyczny; kod regionu = mała litera alfa-2, np. `de`, `us`, `pl`).
   Komenda `manage.py regions_countries --competition <slug> [--dry-run]`:
   - włącza `custom_regions` w konkursie,
   - zakłada brakujące kraje jako `RegionLevel.COUNTRY`, `parent=None`, `position` alfabetycznie,
     `counts_for_conflict=True`,
   - regiony startowe (16 województw, „poza Polską”) **dezaktywuje**, nie kasuje (PROTECT/historia);
     region `pl` (Polska) zostaje jako kraj (zmienia `name` na „Poland” tylko jeśli nikt go nie
     przemianował – idempotentnie),
   - wypisuje podsumowanie (ile dodano, ile dezaktywowano, ilu uczestników ma region
     dezaktywowany – ich trzeba przypisać ręcznie; dziś w `iqo` uczestników nie ma),
   - idempotentna, z audytem, w jednej transakcji; `--dry-run` wycofuje.
4. **Szablon konkursu:** `create_competition` dostaje opcję `--regions countries|voivodeships`
   (domyślnie `voivodeships` – bez zmiany zachowania) i woła tę samą logikę.
5. **Konflikt interesów / przydział recenzentów / komunikaty grupowe po regionie / filtry panelu**:
   sprawdź, że działają na regionach-krajach (testy dla `iqo`-podobnego konkursu).
6. Dokumentacja: `docs/PODRECZNIK-ORGANIZATORA.md` (regiony/kraje), `docs/OPERACJE.md`
   (komenda), CHANGELOG `[Unreleased]`.

## 2. Testy
- `kwantowa` (flaga off): formularz rejestracji ma listę 16 województw – HTML jak dotąd.
- konkurs z krajami: formularz pokazuje kraje, zapis tworzy `Participant.region` = kraj,
  `district` = kod; walidacja odrzuca kod spoza aktywnych regionów; nazwa kraju w karcie
  uczestnika, katalogu czatu, eksporcie.
- komenda: idempotencja, dry-run, dezaktywacja startowych, brak kasowania, audyt.
- `create_competition --regions countries`.
