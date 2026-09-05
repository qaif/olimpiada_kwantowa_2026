---
title: "Panel uczestnika"
slug: panel-uczestnika
menu_order: 0
source: "wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php + `quantum-olympiad-core.php`"
type: "funkcja aplikacji (`/me/`)"
parent: "—"
seed_order: 23
---

# Panel uczestnika

Treść strony to sam shortcode `[qo_student_panel]`. Poniżej teksty interfejsu wygenerowane przez wtyczkę
— **do porównania z nowym panelem, nie do przeniesienia jako treść CMS.**

## Sekcje panelu

- **Opublikowane zadania** (lista jak na `/zadania/`)
- **Wyślij rozwiązanie** — wybór zadania, pole „Rozwiązanie w PDF (maks. 10 MB)”, przycisk „Prześlij rozwiązanie”
- **Moje zgłoszenia** — tabela: Zadanie · Kod · Wysłano · Status · Wynik

## Komunikaty

- „Zaloguj się, aby zobaczyć panel.”
- „Wybierz zadanie i plik PDF.”
- „Dozwolony jest wyłącznie plik PDF do 10 MB.”
- „Rozwiązanie zapisane i oznaczone anonimowym kodem QO-…”
- „Nie udało się zapisać pliku. Spróbuj ponownie lub skontaktuj się z administratorem.”

## Zachowanie techniczne

Kod anonimowy w formacie `QO-` + 12 znaków; plik trafia do prywatnego katalogu `uploads/qo-private`
chronionego `.htaccess`; status zgłoszenia: `submitted` → `graded`; „Wynik” to **średnia arytmetyczna**
ocen wszystkich recenzentów (`AVG(points)`, 2 miejsca po przecinku).
