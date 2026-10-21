# UX-PANEL-01 — panel uczestnika: menu, Start, powiadomienia

Prośba organizatora z 21.10.2026: „ładny panel uczestnika z ładnym menu, czatem, powiadomieniami,
webTEX-em oraz dostępem do notebooków Qiskita”.

## 1. Inwentaryzacja (stan na `origin/main` z 21.10.2026)

| Funkcja | Adres | Flaga / konfiguracja | Warunek widoczności dziś | Gdzie był odnośnik |
|---|---|---|---|---|
| Pulpit („Co teraz”, zakładki Zadania/Wyniki/Reklamacje/Zgody) | `/me/`, `?tab=…` | – | rola uczestnika + profil w konkursie (`ParticipantRequiredMixin`) | pasek konta „Mój panel” |
| Profil (edycja danych, ustawienia listów) | `/me/profile/` | – | jw. | pasek zakładek pulpitu, zwinięte „Twoje dane” |
| Hasło | `/account/password/` | – | zalogowany | profil |
| 2FA | `/account/2fa/` | `TWO_FACTOR_ENABLED` | jw. | profil |
| Kalendarz (+ `.ics`, subskrypcja CAL-01) | `/me/calendar/` | – | uczestnik | pasek zakładek |
| Archiwum zadań | `/me/archive/` | – | uczestnik | pasek zakładek |
| Dyplomy | `/me/certificates/` | – | uczestnik | pasek zakładek |
| Wiadomości (czat CZ-01) | `/me/messages/…` | `ChatSettings.enabled` (domyślnie wł.) | `chat_visible`, licznik `chat_unread` (procesor `roles`, jedno zapytanie `chat.services.nav_state`) | pasek konta, pasek zakładek |
| Ogłoszenia organizatora (CZ-ANN-01) | blok na `/me/` i nad skrzynką | – | widoczne teraz (`chat.announcements.visible_announcements`) | – |
| Forum | `/forum/` | flaga `participant_forum` | `can_use_forum` | pasek konta |
| Powiadomienia | – | – | **brak wspólnego mechanizmu**: forum ma wyłącznie listy e-mail (zbiorcze, `apps.forum.notifications`), czat – licznik nieprzeczytanych rozmów i list e-mail, zaświadczenie / wyniki / reklamacje – listy e-mail (`apps.student_status.notifications`, `apps.submissions.notifications`) | – |
| Edytor LaTeX webTEX (TEX-01) | `/me/stages/<e>/problems/<n>/latex/` | flaga `latex_editor` **i** `WEBTEX_URL` + `WEBTEX_SHARED_KEY` (`webtex.services.available`) | karta zadania (`latex_card_panel`) | tylko karta zadania |
| Notatniki kwantowe (QC-01/02, JupyterLite + Qiskit) | `/me/notebooks/<zadanie>/`, host `NOTEBOOK_LAB_HOST` | flaga `quantum_notebooks`, `NotebookTask` przy zadaniu | karta zadania (`notebook_card_panel`) | tylko karta zadania |
| Materiały z warsztatów | `/warsztaty/materialy/` | flaga `workshop_materials` + opublikowany materiał | `workshop_materials_available` (leniwe, pamięć) | pasek konta, kafel pulpitu |
| Webinary | `/webinars/` | flaga `webinars` + LiveKit (`webinars.services.available`) | jw. | pasek zakładek |
| Zaświadczenie o statusie ucznia | `/me/status-ucznia/` | flaga `student_status_certificate` | kafel pulpitu (`student_status_card_context`) | kafel |
| Najbliższy warsztat | `/warsztaty/` (CMS), kalendarz | strona „warsztaty” | `cms.workshops.upcoming_workshops` (strona główna) | – |
| Zgłoś problem | `/support/new/` | – | zawsze | pasek konta, stopka |

Menu konta było rozrzucone na trzy miejsca (pasek konta w nagłówku motywu, pasek zakładek
z „odnośnikami bocznymi”, kafle), a webTEX i notatniki nie miały wejścia poza kartą zadania.

## 2. Decyzje

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | Menu panelu opisane w Pythonie: `apps/participant_panel/nav.py` (odpowiednik `apps/web/coordinator_nav.py`), renderowane znacznikiem `{% participant_nav %}`. | jedno źródło prawdy; brak adresu w urlconfie = pozycja znika (`NoReverseMatch`); pozycja aktywna z nazwy widoku i `?tab=` |
| D2 | Widoczność pozycji wyłącznie z istniejących warunków (`chat_visible`, `can_use_forum`, `workshop_materials_available`, `webinars.available`, `webtex.services.available`, `notebooks.services.is_enabled`, `student_status.models.enabled`, `twofactor.is_enabled`). Żadnych nowych flag. | spójność z resztą serwisu; menu to nawigacja, nie zabezpieczenie |
| D3 | Desktop (≥ 1024 px): stała kolumna z lewej. Telefon: `<details>` „Menu uczestnika” nad treścią (bez JS). Kolumna **nie** jest przyklejona – przyklejony pasek konta nie może zasłonić fokusu w menu (błąd z A11Y-01). | CSP bez inline JS; WCAG 2.4.11 |
| D4 | Nowa aplikacja `apps/notifications`: model `Notification` (konto, konkurs, rodzaj, parametry, cel jako **nazwa adresu** + argumenty, klucz scalania, przeczytane). Tytuł składany przy wyświetleniu w języku oglądającego. | zasilanie z wielu aplikacji bez dublowania; adres przez `reverse` w żądaniu oglądającego – działa pod prefiksem konkursu (`/iqo/…`) |
| D5 | Źródła: haki jednolinijkowe w miejscach, które już wysyłają list lub zapisują zdarzenie: doręczenie wiadomości czatu (`chat.services._deliver`) i odczyt wątku (`mark_read`), publikacja wpisu na forum (`forum.notifications.post_published`) i decyzja moderatora (`record_decision`), decyzja o zaświadczeniu (`student_status.notifications`), ogłoszenie wyników (`submissions.notifications.notify_results_published`), decyzja komisji (`notify_appeal_decided`), publikacja ogłoszenia organizatora (`chat.announcements.set_published`). Hak nigdy nie przerywa źródła (punkt zapisu + log). | minimalnie w cudzym kodzie |
| D6 | Klucz scalania (`dedupe_key`): kolejna wiadomość w tej samej rozmowie / odpowiedź w tym samym wątku podbija **nieprzeczytane** powiadomienie zamiast dokładać nowe. | dzwonek ma mówić „coś czeka”, a nie liczyć akapity |
| D7 | Ogłoszenie organizatora: rozesłanie do uczestników konkursu po zatwierdzeniu publikacji, a ogłoszenie zaplanowane – przez zadanie beat co 10 min, gdy wejdzie w okno; znacznik `AnnouncementDelivery` (ogłoszenie, `published_at`) pilnuje, że jedna publikacja daje jedno rozesłanie. Świadoma zmiana wobec CZ-ANN-01 § 5 („bez stanu przeczytania”): wiersz powiadomienia jest danymi tej osoby, z 90-dniową retencją, i niesie wyłącznie tytuł ogłoszenia. | prośba organizatora o powiadomienia |
| D8 | Komunikaty e-mail organizatora (`accounts.messaging`) **nie** dają powiadomienia: nie mają widoku w serwisie, do którego mógłby prowadzić odnośnik. Baner `cms.Announcement` – też nie: wisi na każdej stronie. | |
| D9 | Powiadomienia mają tylko uczestnicy (strona `/me/notifications/`); haki pomijają konta bez profilu uczestnika w konkursie (np. koordynator obserwujący wątek). | koordynator ma odznaki w swoim menu |
| D10 | Retencja 90 dni (`NOTIFICATION_RETENTION_DAYS`), zadanie beat `notifications-purge` raz na dobę; anonimizacja konta usuwa wiersze; usunięcie konta – kaskada; eksport danych dostaje sekcję `powiadomienia_w_panelu`. Rejestr czynności 1.25: nowa kategoria w wierszu „konta”. | RODO |
| D11 | Wejścia „Notatniki Qiskit” (`/me/notebooks/`) i „Edytor LaTeX” (`/me/latex/`): listy zadań z etapów, do których uczestnik jest zapisany i które już się zaczęły, z przyciskiem otwarcia. Bramki sprawdza dalej widok docelowy. | zadania są jedynym miejscem, w którym notatnik i edytor istnieją |
| D12 | Pulpit = „Start”: kafle (najbliższy termin z „Co teraz”, zaświadczenie z przyciskiem, najbliższy warsztat z harmonogramu – bez linku do spotkania, materiały, nieprzeczytane wiadomości/powiadomienia, skróty do narzędzi). Pasek zakładek zostaje; jego „odnośniki boczne” przeszły do menu. | |
| D13 | Styl w osobnym pliku `participant_panel/panel.css`, wyłącznie tokeny ról (`--surface`, `--line`, `--brand`, `--accent`…), więc motyw IQO (most tokenów), tryb ciemny i wysoki kontrast działają bez osobnych reguł. | nie psuje panelu koordynatora ani stron publicznych |

## 3. Menu

- **Start**: Pulpit, Powiadomienia (licznik).
- **Zawody**: Zadania i moje prace, Wyniki, Reklamacje, Dyplomy, Archiwum zadań.
- **Nauka**: Kalendarz, Materiały z warsztatów*, Webinary*, Notatniki Qiskit*, Edytor LaTeX*.
- **Wiadomości**: Wiadomości* (licznik), Ogłoszenia (organizatora – kotwica bloku na pulpicie), Forum*, Zgłoś problem.
- **Konto**: Profil i dane, Zgody, Zaświadczenie o statusie ucznia*, Hasło, Weryfikacja dwuetapowa*.

`*` – pozycja warunkowa (D2). Nagłówek menu: dzwonek z liczbą nieprzeczytanych powiadomień.

## 4. Koszt

Strony panelu płacą jedno zapytanie więcej (licznik nieprzeczytanych, zapamiętany na żądaniu),
pulpit – jeszcze jedno (strona „Warsztaty” dla kafla najbliższego warsztatu). Budżet `/me/`
w `apps/core/tests/query_budgets.py`: 44 → 46.

## 5. Poza zakresem

Powiadomienia push / e-mail z panelu (listy już są), powiadomienia dla koordynatora, recenzenta
i opiekuna, przenoszenie ekranów wspólnych z innymi rolami (forum, materiały, webinary,
zgłoszenia) do ramy panelu.
