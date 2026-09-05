---
title: "Panel komitetu"
slug: panel-komitetu
menu_order: 0
source: "wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php + `quantum-olympiad-core.php`"
type: "funkcja aplikacji (`/review/`)"
parent: "—"
seed_order: 24
---

# Panel komitetu

Treść strony to sam shortcode `[qo_review_panel]`. Teksty interfejsu — **do porównania, nie do przeniesienia.**

- Nagłówek informacyjny: „Widoczne są wyłącznie anonimowe kody prac. Dane uczestników nie są udostępniane oceniającym.”
- Karta pracy: kod anonimowy, tytuł zadania, odnośnik „Pobierz anonimową pracę PDF”
- Formularz oceny: „Punkty” (liczba **0–100, krok 0,5**), „Komentarz” (textarea), przycisk „Zapisz ocenę”
- Komunikaty: „Panel jest dostępny wyłącznie dla członków komitetu.”, „Punkty muszą mieścić się w zakresie 0–100.”, „Ocena została zapisana.”
- Ograniczenie bazy: jeden rekord oceny na parę (praca, recenzent) — `UNIQUE KEY one_grade`
