# ZOZ-01: Zasady Organizacji Zawodów dla każdego etapu

## 0. Cel

Regulamin (§ 1 ust. 4) przewiduje, że dla każdego etapu wydaje się osobny dokument „Zasady
Organizacji Zawodów” (harmonogram, forma zadań, dozwolone narzędzia, program). Organizator dostarczył
ZOZ Etapu I w wersji 1.0 (12 listopada 2026 r.) i polecił go opublikować. Zadanie daje każdemu etapowi
własny, wersjonowany dokument, którym zarządza koordynator, z publicznym adresem w sekcji „Dokumenty”.

## 1. Model i reguły

1. `competitions.StageRules` (migracja `competitions.0036`, moduł `apps.competitions.stage_rules`):
   etap, wersja (w adresie, unikalna w etapie), data dokumentu, tytuł, status
   (projekt / opublikowany / wycofany), treść HTML, opcjonalny PDF (prywatny storage
   `private_media`, klucz `stage-rules/<uuid>/<nazwa>`), chwila publikacji.
2. Obowiązuje **ostatnio opublikowana** wersja; wcześniejsze opublikowane zostają w historii.
   Wycofanie zdejmuje wersję z serwisu w całości. Projekt można usunąć; opublikowanej wersji nie
   zmienia się z panelu – zmiana to nowa wersja.
3. Treść jest oczyszczana przy zapisie i przy wyświetleniu (lista dozwolonych znaczników struktury
   dokumentu: nagłówki, akapity, listy, tabele, odnośniki `http(s)`/`mailto`/względne; bez skryptów,
   stylów, atrybutów zdarzeń i obrazów). Spis rozdziałów powstaje z nagłówków `<h2 id>`.
4. Każda czynność ma wpis audytu: `stage_rules.created`, `.updated`, `.published`, `.withdrawn`,
   `.deleted` (skrót SHA-256 treści, bez samej treści).
5. Izolacja: etap i wersja szukane przez `for_competition` – cudzy konkurs, projekt i wersja
   wycofana dają 404.

## 2. Adresy

1. Publiczne (adres aplikacji pod stroną „Dokumenty” – `dokumenty/zasady` w `nested_paths` kontraktu
   tras djcms): `/dokumenty/zasady/<etap>/` (wersja obowiązująca), `/dokumenty/zasady/<etap>/wersja/<w>/`
   (historia, ramka „wersja archiwalna”), `/dokumenty/zasady/<etap>/wersja/<w>/pdf/`.
2. Panel koordynatora: `/coordinator/stages/<id>/rules/` (lista wersji, nowa wersja – z pola albo
   z pliku `.html`, „nowa wersja na podstawie”), `/coordinator/stage-rules/<pk>/` (edycja projektu),
   `…/preview/`, `…/pdf/`, `…/publish/`, `…/withdraw/`, `…/delete/`. Pozycja menu „Zasady (ZOZ)”
   pod każdym etapem poza treningiem (bez flagi – dokument wymaga Regulamin).
3. Odnośniki: przy etapie w harmonogramie (blok `stage_timeline`, także w djcms – pole `rules_url`
   w API `stages`), na karcie etapu w panelu uczestnika, w spisie `/dokumenty/` (Wagtail) i w sekcji
   „Zasady pozostałych etapów i wcześniejsze wersje” strony ZOZ.
4. Pełnostronicowy cache (`/dokumenty/` i `/harmonogram/`) jest unieważniany przy każdym zapisie
   wersji.

## 3. Import

`manage.py import_stage_rules --competition <slug> --stage <id|nazwa|rodzaj> --version <w> --date
RRRR-MM-DD --html <plik> [--pdf <plik>] [--title …] [--publish] [--replace-published]` – idempotentna
(drugie uruchomienie z tymi samymi plikami nic nie zmienia; PDF porównywany po SHA-256). Instrukcja
dla produkcji: OPERACJE § 56. Treść ZOZ nie leży w repozytorium – to dokument organizatora.

## 4. Poza zakresem

Stara strona Wagtaila `/dokumenty/zoz/` (projekt ZOZ edycji w wersji 0.2, ukryta, seed
`seed_legacy_content`) zostaje bez zmian – dlatego adres aplikacji to `dokumenty/zasady`, a nie
`dokumenty/zoz` (test `test_seeded_content_has_no_page_under_an_application_address`: żadna strona
z seedów nie może leżeć pod adresem aplikacji). Odnośniki do ZOZ w treści innych dokumentów (Regulamin, RODO) dodaje
redakcja.
