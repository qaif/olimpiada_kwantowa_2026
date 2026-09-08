---
title: "Terminarz i harmonogram"
slug: harmonogram
menu_order: 0
source: "wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php"
type: "dane etapów (`competitions.Stage`)"
parent: "biezaca-edycja"
seed_order: 5
---

# Terminarz i harmonogram

## Terminarz 2026/2027

| Wydarzenie | Termin |
|---|---|
| Rejestracja | 1 września – 15 października 2026 |
| I etap | 7 listopada 2026 |
| II etap | 16 stycznia 2027 |
| Finał | 10 kwietnia 2027 |

> Strona główna dodaje do tych dat opisy etapów, których nie ma w tabeli:
> I etap — „zawody szkolne”, II etap — „zawody okręgowe”, Finał — „Warszawa”.

> **To jest zapis treści starej strony, a nie obowiązujący harmonogram.** Organizator zmienił
> finał na **4–7 czerwca 2027, stacjonarnie w Krakowie** i dołożył harmonogram szesnastu
> warsztatów online. Obowiązująca treść: `backend/apps/cms/fixtures/legacy/harmonogram.md`;
> terminy egzekwowane przez system: `competitions.Stage` (`seed_edition_kwantowa`).
