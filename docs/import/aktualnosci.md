# Aktualności ze starej strony

Źródło: `wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php` (`QO_Seeder::run()`),
pętla `wp_insert_post(['post_type' => 'post', ...])`.

## Uwaga o datach

**Żaden wpis nie ma zapisanej daty publikacji.** Seeder nie ustawia `post_date`, więc WordPress
nadaje datę uruchomienia komendy `wp quantum seed` — data jest artefaktem instalacji, nie decyzją
redakcyjną. Do nowego CMS trzeba przyjąć daty ustalone z organizatorem albo wpisy przygotować
od nowa. Jedyna data widoczna w kodzie to `21.07.2026` w kaflu zastępczym na stronie głównej
(`front-page.php`) — również demonstracyjna.

Wszystkie trzy wpisy to **jednozdaniowe zapowiedzi bez treści właściwej** (`post_content` to jeden
akapit; nie ma zajawki, obrazu wyróżniającego ani kategorii).

## Wpisy

### 1. Otwieramy I edycję Olimpiady Kwantowej

- **Data:** brak (data instalacji)
- **Treść:** Zapraszamy uczniów szkół ponadpodstawowych z całej Polski. Rejestracja rusza 1 września 2026.

### 2. Znamy harmonogram zawodów

- **Data:** brak (data instalacji)
- **Treść:** Sprawdź terminy rejestracji, etapów szkolnych, okręgowych i finału.

### 3. Materiały przygotowawcze już dostępne

- **Data:** brak (data instalacji)
- **Treść:** Rozpocznij przygotowania z wykładami i zestawami ćwiczeń opracowanymi przez komitet.

## Kafel zastępczy na stronie głównej

Wyświetlany tylko wtedy, gdy w bazie nie ma żadnego wpisu (`front-page.php`):

- **21.07.2026 — Startuje Olimpiada Kwantowa** — Wkrótce otworzymy zapisy do pierwszej edycji zawodów.

## Wpis typu `qo_task` (zadanie demonstracyjne)

- **Splątanie dwóch kubitów** — Zadanie demonstracyjne. Pełna treść zostanie opublikowana w dniu rozpoczęcia etapu.

## Rekomendacja

Nie przenosić 1:1. Wpisy 1 i 2 mają sens jako **szablon** komunikatu startowego i komunikatu
o harmonogramie — treść trzeba napisać na nowo z prawdziwymi datami I edycji. Wpis 3 przenieść
dopiero, gdy realnie powstaną materiały przygotowawcze. Zadanie demonstracyjne pominąć.
