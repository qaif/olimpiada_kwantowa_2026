# Podręcznik opiekuna szkolnego (nauczyciela)

Krótka instrukcja dla nauczyciela, który prowadzi uczniów w olimpiadzie: jak założyć konto, jak zaprosić
klasę i gdzie widać postęp uczniów. Organizator może ją rozesłać w całości albo wkleić z niej fragmenty
do własnego listu. Opiekun **drużyny narodowej** (olimpiada międzynarodowa) ma osobny przewodnik:
`PODRECZNIK-OPIEKUNA-DRUZYNY.md`.

---

## 1. Konto i skąd biorą się uczniowie

Konto zakładasz pod adresem `/register/supervisor/` (jeśli organizator oferuje tę rolę), potwierdzasz
adres linkiem z listu i logujesz się zwykłym formularzem `/login/` – po zalogowaniu trafiasz na **Start**
panelu nauczyciela.

Uczniów **nie przypisuje Ci organizator**: widzisz tych, którzy sami wpisali Twój adres e-mail w swoim
profilu („Edytuj dane” → „Adres e-mail opiekuna szkolnego”) albo przyjęli Twoje zaproszenie. Każdy uczeń
może to w każdej chwili cofnąć – znika wtedy z Twojej listy.

## 2. Menu panelu

Na komputerze menu stoi w kolumnie z lewej, na telefonie – pod przyciskiem **„Menu nauczyciela”** nad
treścią strony.

- **Start** – kafle: liczba uczniów (z kontem / zaproszonych bez konta), najbliższy termin etapu,
  uczniowie bez pracy w etapie, który teraz przyjmuje prace, najbliższy warsztat i stan potwierdzenia
  udziału szkoły. Kafel z liczbą ma przycisk „Pokaż ich” – otwiera „Postęp klasy” z gotowym filtrem.
- **Uczniowie → Moi uczniowie** – kto przyjął zaproszenie, kto jeszcze nie (przycisk „Wyślij zaproszenie
  ponownie”, najwyżej raz na godzinę na ucznia), kto zapisał się sam.
- **Uczniowie → Zaproś uczniów z listy** – import pliku CSV albo XLSX z listą klasy; podgląd pokazuje stan
  każdego wiersza, zanim cokolwiek zostanie wysłane.
- **Uczniowie → Postęp klasy** – tabela opisana niżej.
- **Materiały i warsztaty** – materiały z warsztatów i plakaty do szkoły (gdy organizator je opublikował)
  oraz kalendarz konkursu do subskrypcji (`.ics`).
- **Szkoła** – potwierdzenie udziału szkoły w edycji (podstawa zaświadczenia dla opiekuna), Twoje
  zaświadczenia do pobrania i – jeśli organizator je włączył – statystyki uczniów i szkoły.
- **Pomoc → Zgłoś problem** i **Konto** (profil, hasło, weryfikacja dwuetapowa).

Wiadomości (czatu) i powiadomień w panelu nauczyciel nie ma – o sprawach organizacyjnych organizator pisze
e-mailem.

## 3. Postęp klasy

Wiersz to uczeń, kolumny to etapy bieżącej edycji (bez treningu). W komórce etapu: stan procedury
(**nic nie oddano / oddane / w ocenie / oceniona / wyniki**), liczba zadań z oddaną pracą („oddane 2 z 3”)
i dzień ostatniego oddania. Gdy w etapie jest kilka prac, stan podpisuje ta **najmniej** zaawansowana –
„w ocenie” znaczy, że przynajmniej jedna praca jeszcze się ocenia.

**Punkty i kwalifikacja** pojawiają się wyłącznie przy etapie z **ogłoszonymi** wynikami – to te same
liczby, które widać w tabeli publicznej. Przed ogłoszeniem nie widać ich nigdzie w panelu. Treści prac,
plików, komentarzy recenzentów ani adresów e-mail uczniów panel nie pokazuje.

Jeśli organizator włączył tę informację dla nauczycieli, tabela ma też kolumny **Zgoda rodzica**
(u niepełnoletnich: jest / brak; u pozostałych „pełnoletni”), **Zaświadczenie** (o statusie ucznia: brak / czeka na weryfikację /
zaakceptowane / odrzucone) i **Warsztaty** (na ilu warsztatach odnotowano obecność).

**Filtry** (nad tabelą): klasa, stan konta, „tylko bez pracy w otwartym etapie”, a przy włączonych
kolumnach formalności – stan zaświadczenia i „tylko bez zgody rodzica”. Pole **Sortuj** ustawia
kolejność (nazwisko, klasa, stan konta, liczba oddanych prac). Kliknij **Pokaż**.

## 4. Eksport klasy

Przyciski **Pobierz CSV** i **Pobierz XLSX** nad tabelą zapisują **to, co widzisz** – te same kolumny,
ten sam filtr i ta sama kolejność. W pliku nie ma adresów e-mail uczniów. Każde pobranie zostaje
w dzienniku zdarzeń serwisu (bez danych uczniów). Plik trzymaj tak, jak inne dane uczniów w szkole –
po zawodach usuń go, jeśli nie jest już potrzebny.

CSV ma średnik jako separator i kodowanie UTF-8 (otwiera się poprawnie w Excelu); liczby punktów z kropką
dziesiętną – kto chce liczb gotowych do sumowania w Excelu, bierze XLSX.

## 5. Twoje dane

Twoje imię, nazwisko, adres, telefon i szkoła służą do prowadzenia konta i zaświadczeń. Konto usuniesz
sam (`/account/delete/`, odnośnik w **Konto → Profil i dane**); potwierdzenia udziału szkoły zostają jako dokumentacja
zawodów.
