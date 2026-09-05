---
title: "Strona główna"
slug: strona-glowna
menu_order: 0
source: "wp-content/themes/olimpiada-kwantowa/front-page.php"
type: "HomePage"
parent: "—"
seed_order: 0
---

# Strona główna

> Treść zapisana bezpośrednio w szablonie `front-page.php` (nie w bazie WordPressa).
> Sekcje w kolejności występowania na stronie.

## Hero

**Eyebrow:** I edycja · rok szkolny 2026/2027

**Hasło (h1):** Przyszłość ma naturę kwantową.

**Opis:** Ogólnopolska olimpiada dla uczniów szkół ponadpodstawowych, którzy chcą zrozumieć fizykę kwantową i tworzyć technologie jutra.

**CTA:**

| Etykieta | Cel | Wariant |
|---|---|---|
| Zarejestruj się | `/rejestracja/` | przycisk główny (bursztynowy) |
| Jak zacząć? | `/jak-zaczac/` | przycisk pomocniczy (obrys) |

## Aktualności

**Eyebrow:** Na bieżąco · **Nagłówek:** Aktualności · **Link:** „Wszystkie wiadomości →” → `/aktualnosci/`

Lista 3 ostatnich wpisów (`WP_Query`, `posts_per_page => 3`): data `d.m.Y`, tytuł, zajawka skrócona do 22 słów.

Treść zastępcza, gdy brak wpisów (zaszyta w szablonie):

- **21.07.2026 — Startuje Olimpiada Kwantowa** — Wkrótce otworzymy zapisy do pierwszej edycji zawodów.

## Harmonogram I edycji

**Eyebrow:** Najważniejsze daty

| Etap | Data (skrót w kaflu) | Podpis |
|---|---|---|
| Rejestracja | 1 IX | do 15 października 2026 |
| I etap | 7 XI | zawody szkolne |
| II etap | 16 I | zawody okręgowe |
| Finał | 10 IV | Warszawa |

## Dołącz w trzech krokach

**Eyebrow:** Pierwszy raz?

1. **Załóż konto** — Wypełnij formularz ucznia i potwierdź swój adres e-mail.
2. **Rozwiąż zadania** — Pobierz arkusz, przygotuj rozwiązania i prześlij je w systemie.
3. **Sprawdź wynik** — Oceny są anonimowe, a wynik znajdziesz bezpiecznie na swoim koncie.

## Pasmo CTA

**Nagłówek:** Masz pytania przed startem?

**Opis:** Przeczytaj przewodnik uczestnika lub skontaktuj się z Komitetem Głównym.

**CTA:** „Przewodnik” → `/jak-zaczac/`, „Kontakt” → `/kontakt/`

## Partnerzy i patroni

**Eyebrow:** Wspólnie dla nauki · **Nagłówek:** Partnerzy i patroni

Trzy kafle z samą nazwą (bez logotypów, bez linków, bez opisów):

- Ministerstwo Edukacji
- Uniwersytet Kwantowy
- Polskie Towarzystwo Fizyczne

> **Uwaga:** „Uniwersytet Kwantowy” nie jest istniejącą instytucją — kafle są wypełnieniem
> demonstracyjnym. Nazw nie przenosić bez potwierdzenia u organizatora.

## Statystyki

Brak. Stara strona nie zawiera żadnej sekcji liczbowej („X uczestników”, „Y szkół”).
