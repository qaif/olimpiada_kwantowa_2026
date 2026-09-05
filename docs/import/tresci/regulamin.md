---
title: "Regulamin"
slug: regulamin
menu_order: 0
source: "wp-content/plugins/quantum-olympiad-core/includes/regulamin.json (render: `class-regulations.php`)"
type: "DocumentPage"
parent: "dokumenty"
seed_order: 12
---

# Regulamin

> **Ta treść jest importowana osobno** (`backend/apps/cms/management/commands/seed_regulamin.py`
> + `DocumentPage`). Poniżej wyłącznie metryka i elementy otoczki, których nie ma w samym
> pliku regulaminu — pełnych 24 paragrafów tu nie powielamy.

## Metryka (z `regulamin.json`)

| Pole | Wartość |
|---|---|
| Tytuł | Regulamin Olimpiady Kwantowej |
| Podtytuł | Ogólnopolski, dwuetapowy konkurs edukacyjny |
| Wersja | 1.0 |
| Data | 18 sierpnia 2026 r. |
| Status | Projekt do zatwierdzenia uchwałą Zarządu Fundacji Quantum AI |

**Zastrzeżenie prawne (wyróżnione czerwoną ramką „Ważny status prawny”):** Olimpiada Kwantowa w opisanym niżej modelu dwóch etapów jest niezależnym konkursem edukacyjnym. Nie jest olimpiadą trójstopniową przeprowadzaną na podstawie rozporządzenia Ministra Edukacji i nie nadaje ustawowych uprawnień laureata lub finalisty, dopóki nie zostanie formalnie objęta właściwym trybem prawnym, a jej regulamin nie zostanie odpowiednio dostosowany.

## Przyciski pobierania (do odwzorowania polem `attachment`)

- „Pobierz regulamin PDF” → `assets/documents/Regulamin-Olimpiady-Kwantowej.pdf`
- „Pobierz wersję DOCX” → `assets/documents/Regulamin-Olimpiady-Kwantowej.docx`

## Struktura: 10 rozdziałów, 24 paragrafy

| Rozdział | Paragrafy |
|---|---|
| I. Postanowienia ogólne | § 1 Nazwa, charakter i Organizator; § 2 Cele Olimpiady |
| II. Uczestnicy | § 3 Warunki udziału; § 4 Rejestracja i komunikacja |
| III. Organy Olimpiady | § 5 Komitet Merytoryczny i Jury; § 6 Komitet Organizacyjny; § 7 Tryb pracy i poufność |
| IV. Zadania i zasady oceniania | § 8 Opracowywanie zadań; § 9 Ocena prac |
| V. Przebieg zawodów | § 10 Struktura Olimpiady; § 11 Etap I — zawody zdalne; § 12 Rozmowa kwalifikacyjna po Etapie I; § 13 Etap II — finał stacjonarny |
| VI. Wyniki, tytuły i odwołania | § 14 Wyniki finału i tytuły; § 15 Ogłaszanie wyników; § 16 Reklamacje i odwołania |
| VII. Uczciwość i bezpieczeństwo | § 17 Samodzielność i naruszenia; § 18 Ochrona małoletnich i zasady zachowania |
| VIII. Dane, prawa autorskie i dokumentacja | § 19 Dane osobowe; § 20 Prawa autorskie; § 21 Dokumentacja |
| IX. Finansowanie i sytuacje nadzwyczajne | § 22 Koszty i nagrody; § 23 Awarie i siła wyższa |
| X. Postanowienia końcowe | § 24 Wejście w życie i zmiany |

> W tytułach § 11 i § 13 plik źródłowy używa dywizu („Etap I - zawody zdalne”). Przy imporcie
> zamienić na półpauzę — **poprawka literowa, oznaczona**.

## Sekcja „Źródła konstrukcji projektu” (dopisywana przez szablon, nie ma jej w JSON-ie)

Regulamin jest autorskim opracowaniem dostosowanym do Olimpiady Kwantowej. Wykorzystano strukturę i dobre praktyki następujących dokumentów:

- [Regulamin Olimpiady Informatycznej — model organizacji, oceny i odwołań](https://www.oi.edu.pl/l/28oi_regulamin/)
- [Rozporządzenie w sprawie organizacji konkursów, turniejów i olimpiad — tekst jednolity](https://eli.gov.pl/api/acts/DU/2020/1036/text.html)
- [Ramowy wzór regulaminu olimpiady MEN, konkurs ofert 2025-2028](https://www.gov.pl/web/edukacja/otwarty-konkurs-ofert-na-realizacje-zadania-publicznego-pn-organizacja-i-przeprowadzenie-olimpiad-przedmiotowych-i-interdyscyplinarnych-w-latach-szkolnych-20252026-20262027-20272028)
- [Dane organizatora — Fundacja Quantum AI](https://www.qaif.org/contact)
