---
title: "Rejestracja"
slug: rejestracja
menu_order: 0
source: "wp-content/plugins/quantum-olympiad-core/includes/class-seeder.php"
type: "funkcja aplikacji (`/register/`)"
parent: "biezaca-edycja"
seed_order: 6
---

# Rejestracja

Rejestracja jest bezpłatna. Po utworzeniu konta otrzymasz dostęp do panelu uczestnika.

`[qo_registration]` — shortcode formularza rejestracji.

## Pola formularza rejestracji (z `quantum-olympiad-core.php`)

| Pole | Typ | Wymagane |
|---|---|---|
| Imię | tekst | tak |
| Nazwisko | tekst | tak |
| Adres e-mail | e-mail (zarazem login) | tak |
| Szkoła | tekst | tak |
| Klasa | tekst, maks. 20 znaków | tak |
| Województwo | lista 16 województw | tak |
| Zgoda | checkbox | tak |

**Treść zgody:** „Akceptuję [regulamin], znam [standardy ochrony małoletnich] i zapoznałem/am się z [informacją RODO]. Jeżeli jestem osobą małoletnią, rejestruję się za wiedzą rodzica lub opiekuna prawnego.”

**Komunikat po rejestracji:** „Konto utworzone. Dane dostępowe wysłaliśmy e-mailem.”

**Komunikat błędu:** „Sprawdź dane lub użyj innego adresu e-mail.”

**Komunikat dla zalogowanego:** „Masz już konto. Przejdź do panelu uczestnika.”
