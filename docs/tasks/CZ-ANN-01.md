# CZ-ANN-01: Ogłoszenia organizatora w Wiadomościach uczestnika

Prośba organizatora z 8.10.2026, dosłownie: „udostępnij też link do warsztatów na wszystkich kontach
w sekcji wiadomości, także tych, co dopiero się zarejestrują; udostępnianie/wyłączanie tej
wiadomości powinno być dostępne dla koordynatora”. Pilne – pierwsze ogłoszenie (link do warsztatów
Google Meet) ma wyjść tego samego dnia.

## 0. Cel i granice

Jawny wpis organizatora wyświetlany **każdemu zalogowanemu uczestnikowi konkursu** na górze skrzynki
Wiadomości (`/me/messages/`) i na pulpicie uczestnika (`/me/`). Wpis jest czytany **w chwili
wyświetlenia**, a nie rozsyłany per konto – dlatego widzi go także konto zarejestrowane po publikacji.

Czego zadanie **nie** robi:
- ogłoszenie **nie jest wiadomością w rozmowie** (`chat.Message`): rozmowa jest 1:1, bywa szyfrowana
  end-to-end, powstaje dopiero z pierwszą wiadomością – konto założone jutro nie miałoby wątku, do
  którego dałoby się „dosłać” ogłoszenie, a wysłanie kilku tysięcy kopii zapełniłoby skrzynkę zespołu,
- nie zmienia banera komunikatów (`cms.Announcement`, `/coordinator/announcements/`) – to inna
  wiadomość: pasek nad każdą stroną serwisu, także dla niezalogowanych, krótki tekst + jeden odnośnik,
- nie zmienia komunikatów grupowych (`apps/accounts/messaging.py`, `/coordinator/messages/`) ani
  ich szablonów – nad nimi pracuje równolegle inne zadanie,
- nie wysyła e-maili ani powiadomień; nie ma stanu „przeczytane” (ogłoszenie jest jawne i wspólne,
  śledzenie odczytów per konto byłoby nową daną osobową bez potrzeby).

## 1. Model `chat.OrganizerAnnouncement`

| Pole | Typ | Uwagi |
|---|---|---|
| `competition` | FK `tenancy.Competition`, `CASCADE` | ogłoszenie należy do jednego konkursu; manager `competition_scoped_manager` |
| `title` | `CharField(200)` | |
| `body` | `TextField(max 4000)` | **zwykły tekst** – renderowany filtrem `message_body` (escape → `urlize` → `linebreaksbr`) |
| `is_published` | bool, domyślnie `False` | przełącznik koordynatora „Opublikuj / Wyłącz” |
| `published_at` | datetime, puste | chwila ostatniego „Opublikuj” – podstawa kolejności „od najnowszego” |
| `published_from` / `published_until` | datetime, puste | opcjonalne okno; puste = bez ograniczenia |
| `created_by` | FK konta, `SET_NULL` | autor (do panelu i audytu, nie do widoku uczestnika) |
| `created_at`, `updated_at` | datetime | |

Więzy: `published_until > published_from`, gdy oba są wpisane (okno o niedodatniej długości nigdy się
nie otwiera). Indeks `(competition, is_published)`.

**Widoczność** (`apps.chat.announcements.visible_announcements(competition, now)` – jedno miejsce):
`is_published` **i** `published_from` puste albo ≤ teraz **i** `published_until` puste albo > teraz.
Kolejność: od najnowszego – po chwili, w której ogłoszenie pojawiło się uczestnikom
(`max(published_at, published_from)`), potem po `id`. Najwyżej 10 naraz.

## 2. Odbiorcy

- **Uczestnik konkursu** (rola `participant` + profil `Participant` w tym konkursie) – na
  `/me/messages/` (blok „Ogłoszenia organizatora” nad listą rozmów, tylko na ekranie skrzynki, nie
  w otwartym wątku) i na pulpicie `/me/` (pod nagłówkiem „Co teraz”).
- Opiekunowie szkolni, recenzenci, komisja **nie mają** skrzynki Wiadomości (CZ-01 § 0), więc
  ogłoszenia ich nie dotyczą.
- **Niezależnie od rozmów między uczestnikami** (`peer_mode`) – ogłoszenie nie jest rozmową.
- **Flaga modułu** `ChatSettings.enabled` (domyślnie `True`, brak wiersza = włączone): przy wyłączonym
  module skrzynka oddaje 404, ale pulpit `/me/` pokazuje ogłoszenia **zawsze** – organizator, który
  wyłączył rozmowy, nadal może ogłosić link.

## 3. Panel koordynatora `/coordinator/inbox-announcements/`

- Lista (wszystkie ogłoszenia konkursu, najnowsze pierwsze) ze stanem: „widoczne”, „wyłączone”,
  „zaplanowane” (przed `published_from`), „wygasło” (po `published_until`).
- Formularz dodania (nad listą); edycja `/<id>/`; akcje `POST` (CSRF): `/<id>/publish/`,
  `/<id>/unpublish/`, `/<id>/delete/` (z potwierdzeniem `confirmSubmit`, bez JS inline).
- Formularz dodania ma przycisk „Zapisz i opublikuj” obok „Zapisz jako szkic”.
- Tylko koordynator **tego** konkursu (inne role 403, niezalogowany → logowanie); ogłoszenie innego
  konkursu → 404. Ekran działa także przy wyłączonych Wiadomościach (pulpit i tak je pokazuje).
- Limit żądań `POST` – zakres `chat` (per konto).
- Menu: „Komunikacja → Ogłoszenia w Wiadomościach”.

## 4. Serwisy (`apps/chat/announcements.py`)

- `create_announcement(*, competition, title, body, actor, publish=False, published_from=None,
  published_until=None, request=None)`,
- `update_announcement(*, announcement, actor, title, body, published_from, published_until, request=None)`,
- `set_published(*, announcement, actor, published: bool, request=None)`,
- `delete_announcement(*, announcement, actor, request=None)`,
- `publish_announcement(*, competition, title, body, actor)` – skrót „utwórz i opublikuj”, do
  `manage.py shell`.

Każdy serwis sprawdza rolę koordynatora (`actor=None` dozwolone tylko z powłoki – wtedy brak aktora
w audycie). Audyt: `chat.announcement.created`, `.updated`, `.published`, `.unpublished`, `.deleted`
(przedrostek `chat.` jak pozostałe zdarzenia modułu – nazwy `announcement.*` zajmuje już baner
`cms.Announcement`). W `diff` tytuł i okno, bez treści.

## 5. Bezpieczeństwo i RODO

- Treść bez HTML-a od użytkownika: autoescape tytułu, `message_body` dla treści (escape przed
  `urlize`, `rel="nofollow noopener noreferrer"`, `target="_blank"`). Bez inline JS (CSP).
- Izolacja konkursów: `for_competition` w każdym odczycie; konkurs z żądania, nigdy z formularza.
- **RODO:** ogłoszenie jest komunikatem organizatora do wszystkich uczestników; nie zawiera danych
  osobowych uczestników i nie zbiera żadnych (brak odczytów per konto, brak odbiorców per konto).
  Jedyną daną osobową jest autor-koordynator (`created_by`, `SET_NULL`) – ta sama kategoria, co
  autor komunikatu `cms.Announcement` czy szablonu odpowiedzi, objęta istniejącą czynnością
  „administracja konkursem”. Nowy wpis w rejestrze czynności przetwarzania jest zbędny.

## 6. Tłumaczenia

Napisy widoku uczestnika przez gettext (polskie `msgid`), katalog aplikacji
`backend/apps/chat/locale/<lang>/LC_MESSAGES/django.po` dla 10 języków. Panel koordynatora – po
polsku, jak sąsiednie ekrany panelu.

## 7. Testy

- uczestnik widzi opublikowane ogłoszenie na `/me/messages/` i `/me/`, także konto założone po
  publikacji; nie widzi szkicu, wyłączonego, zaplanowanego, wygasłego ani ogłoszenia innego konkursu,
- przy wyłączonych Wiadomościach – `/me/` nadal pokazuje,
- escape HTML (`<script>`) i linkowanie URL-i (`https://meet.google.com/…` → `<a … rel="nofollow noopener noreferrer">`),
- ekran koordynatora: 403 dla innych ról, 404 dla cudzego ogłoszenia, `GET` na akcjach → 405,
  publikacja / wyłączenie / edycja / usunięcie z wpisami audytu,
- `publish_announcement` z powłoki (bez aktora i z aktorem),
- menu koordynatora (`test_coordinator_nav_flags.py`) i budżet zapytań `/me/` (+1).

## 8. Operacje

Pierwsze ogłoszenie z powłoki (po wdrożeniu i migracji `chat.0003`):

```python
from apps.tenancy.models import Competition
from apps.chat.announcements import publish_announcement
c = Competition.objects.get(slug="kwantowa")
publish_announcement(competition=c, title="Warsztaty online", body="Link do warsztatów: https://meet.google.com/…", actor=None)
```
