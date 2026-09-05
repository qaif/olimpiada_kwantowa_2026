---
title: "Zadania i archiwum"
slug: zadania
menu_order: 4
source: "wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php"
type: "ProblemsPage"
parent: "—"
seed_order: 10
---

# Zadania i archiwum

## Opublikowane zadania

Wybierz zadanie, aby przeczytać pełną treść. Zalogowani uczestnicy przesyłają rozwiązania w [panelu uczestnika](/panel-uczestnika/).

`[qo_task_archive]` — shortcode listy zadań (typ wpisu `qo_task`, slug `zadanie`).

> Wersja pierwotna (`QO_Seeder::run()`) miała zdanie bez linku: „…przesyłają rozwiązania
> w panelu uczestnika.”; wersja zarządzana (`upgrade_organizer_content()`) dodaje odnośnik.

**Komunikat pustej listy:** „Nie opublikowano jeszcze żadnych zadań.”

**Kafel zadania:** meta „Opublikowane zadanie”, tytuł, zajawka (28 słów), przycisk „Zobacz treść zadania”.

**Pasmo CTA na stronie zadania** (`single-qo_task.php`): „Masz gotowe rozwiązanie?” /
„Zaloguj się do panelu uczestnika i prześlij jeden plik PDF.” / przycisk „Prześlij rozwiązanie”.

## Zadanie demonstracyjne (jedyny wpis `qo_task`)

**Splątanie dwóch kubitów** — Zadanie demonstracyjne. Pełna treść zostanie opublikowana w dniu rozpoczęcia etapu.
