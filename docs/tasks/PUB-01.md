# PUB-01: Planowana publikacja treści zadań

## 0. Cel

ZOZ Etapu I (paragraf 3 ust. 1): treść zadania jest niedostępna przed otwarciem etapu, a system
udostępnia pliki od chwili otwarcia. Koordynator może dodatkowo zaplanować publikację pojedynczego
zadania albo wszystkich zadań etapu na późniejszą chwilę.

## 1. Model

1. `Problem.publish_at` (migracja `competitions.0037`), puste = „razem z otwarciem etapu” – dokładnie
   dotychczasowe zachowanie każdego zadania.
2. Treść jest jawna od **późniejszej** z dwóch chwil: otwarcia etapu (z oknami czasowymi TZ-01 –
   startu okna ucznia) i `publish_at` (`Problem.effective_publish_at`). Źródłem prawdy jest zegar
   serwera przy żądaniu (`Problem.is_published`, `apps.competitions.publication`) – bez zadania
   okresowego.
3. Formularz zadania i formularz zbiorczy przyjmują czas polski (`LocalDateTimeField`), w bazie UTC.
   Chwila przed otwarciem etapu albo po terminie oddania rozwiązań to błąd pola.

## 2. Bramka (serwis, nie szablon)

1. Przed publikacją zadania uczestnik i świat nie dostają ani tytułu, ani PDF-u, ani liczby zadań:
   `ProblemStatementView` (404), `editions/current/` API, strona „Zadania” i API djcms
   (`problems_state`), karty i podpowiedź „wyślij zadanie N” w panelu, archiwum, listy notatników
   i edytora LaTeX, notatnik i edytor zadania (404), tłumaczenie dla ucznia, licznik „oddane X z N”
   opiekuna szkolnego.
2. Upload: `create_submission` odpowiada przed publikacją tak samo, jak dla zadania, którego nie ma
   (404 `PROBLEM_NOT_FOUND`); widok HTMX uploadu – 404 bez karty z tytułem.
3. Koordynator widzi treść zawsze (podgląd PDF i panel). Panel uczestnika podaje wyłącznie chwilę
   najbliższej publikacji („Kolejne zadania zostaną opublikowane: …”), bez liczby i tytułów.
4. Podmiana treści wymaga potwierdzenia dopiero, gdy zadanie jest już jawne (etap otwarty
   i zadanie opublikowane).
5. Cache: strony z treścią zadań nie stoją w pełnostronicowym cache'u (`apps.web.page_cache`), API
   djcms ma `no-store` – moment publikacji widać od pierwszego żądania po nim.

## 3. Panel koordynatora

Kolumna „Publikacja” na liście zadań (opublikowane / zaplanowane, chwila w czasie polskim, „z otwarciem
etapu”), pole „Publikacja treści” w formularzu zadania i sekcja „Publikacja wszystkich zadań”
(`POST /coordinator/stages/<id>/problems/publication/`, audyt `stage.problems_publication_scheduled`).

## 4. Dalszy krok

Powiadomienie uczestników w chwili publikacji wymagałoby zadania okresowego – zamiast niego
organizator planuje komunikat z datą przyszłą (MSG-SCHED-01) albo ogłoszenie na tę samą chwilę.
Delegacje tłumaczące zadania (TR-01) widzą treść w swoim oknie tłumaczeń – niezależnie od `publish_at`.
