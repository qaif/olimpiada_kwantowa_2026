# Dokumentacja – spis

Opis całego projektu i szybki start: [`../README.md`](../README.md).

## Dla kogo co

| Czytelnik | Dokument | O czym |
|---|---|---|
| Uczestnik | [PODRECZNIK-UCZESTNIKA.md](PODRECZNIK-UCZESTNIKA.md) | konto, wysyłka rozwiązań, wyniki, forum, wiadomości, webinary |
| Opiekun drużyny narodowej | [PODRECZNIK-OPIEKUNA-DRUZYNY.md](PODRECZNIK-OPIEKUNA-DRUZYNY.md) | (po angielsku) rejestracja uczniów, okna czasowe, opłaty, tłumaczenia zadań, logistyka finału |
| Recenzent (komitet) | [PODRECZNIK-RECENZENTA.md](PODRECZNIK-RECENZENTA.md) | kolejka prac, ocena, rozmowy kwalifikacyjne |
| Organizator (koordynator) | [PODRECZNIK-ORGANIZATORA.md](PODRECZNIK-ORGANIZATORA.md) | panel koordynatora: edycja, etapy, komitet, wyniki, RODO; funkcje IQO w § 10a–10m |
| Administrator (instalacja) | [PODRECZNIK-ADMINISTRATORA.md](PODRECZNIK-ADMINISTRATORA.md) | instalacja, `.env`, DNS, poczta, S3, aktualizacje, model bezpieczeństwa |
| Operator (produkcja) | [OPERACJE.md](OPERACJE.md) | kopie i odtwarzanie, monitoring, wdrożenia, incydenty, kroki operatora każdej funkcji; na początku tabela „Funkcje i flagi” |
| Integrator | [API.md](API.md) | API integracji i webhooki |
| Programista | [TESTY.md](TESTY.md), [UI.md](UI.md), [COVERAGE.md](COVERAGE.md) | uruchamianie i pisanie testów, system interfejsu, pokrycie |
| Przegląd bezpieczeństwa | [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) | lista kontrolna z odwołaniami do testów |

## Historia i plany

- [CHANGELOG.md](CHANGELOG.md) – wydania (sekcja na wydanie, najnowsze na górze); nowa zmiana jako
  `## [Unreleased] – …` na górze.
- [BACKLOG.md](BACKLOG.md) – stan prac i dług techniczny.
- [PROJEKT.md](PROJEKT.md) – projekt systemu i proces budowy (pętla agentyczna).
- [UNIWERSALNY-ETAP-1.md](UNIWERSALNY-ETAP-1.md), [UNIWERSALNY-ETAP-2.md](UNIWERSALNY-ETAP-2.md) – plany
  wielokonkursowości i konfiguracji konkursu (lista kontrolna § 0.5, katalog flag § 0.6).
- [LICENCJA-UZASADNIENIE.md](LICENCJA-UZASADNIENIE.md) – dlaczego AGPL-3.0.
- [`tasks/`](tasks/) – specyfikacje zadań (`T-02`…`T-10` – budowa platformy; kody funkcji, np.
  `DEL-01`, `THEME-02`, `PAY-01`). Kod odsyła do nich jako „`<KOD>` § N”.
- [`import/`](import/) – materiały z importu starego serwisu.

## Numery sekcji

Kod i dokumenty odsyłają do siebie numerami („`docs/OPERACJE.md` § 43.5”, „podręcznik organizatora
§ 10m”). Dlatego **numerów istniejących sekcji się nie zmienia**: nowa sekcja dostaje następny wolny
numer (w podręcznikach – następną wolną literę, np. 10n) i staje na swoim miejscu w kolejności.
Test `backend/apps/core/tests/test_docs_section_refs.py` sprawdza, że każde odwołanie „§ N” wskazuje
istniejącą sekcję, a `test_docs_features_table.py` – że tabela „Funkcje i flagi” w OPERACJE zna każdą
flagę z `FEATURE_DEFAULTS`.
