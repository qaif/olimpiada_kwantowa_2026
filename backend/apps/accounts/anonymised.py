"""Konto po anonimizacji – jedna reguła rozpoznania, w Pythonie i w zapytaniu.

Skąd ten moduł. Usunięcie konta z footprintem w zawodach (``apps.accounts.profile.anonymise_account``)
**nie kasuje** wiersza ``User`` ani ``Participant``: zostaje pseudonimowy wiersz z kodem publicznym,
bo pod tym kodem uczestnik stoi w ogłoszonych tabelach, odwołaniach i protokole. Adres takiego
konta to ``deleted-<pk>@invalid.<domena>``, a imię i nazwisko są puste. Do 24.09.2026 każda lista
panelu koordynatora pokazywała te wiersze jak zwykłe konta – z adresem „deleted-…” w kolumnie
e-mail – i organizator zgłosił to jako błąd: człowiek, który skorzystał z prawa do usunięcia
danych, nie jest już uczestnikiem, którego się przegląda, sortuje i do którego się pisze.

Dlatego reguła ma **jedno** miejsce i dwie postacie:

- :func:`is_anonymised_email` / :func:`is_anonymised` – dla kodu, który ma już wiersz w ręku
  (retencja, etykieta w szablonie),
- :func:`anonymised_q` – warunek ``Q`` do querysetu, z parametrem ścieżki do konta, bo listy
  panelu wychodzą z różnych modeli (``User``, ``Participant``, ``StageEntry``, ``CommitteeMember``…).
  Managery ``User.objects`` i ``Participant.objects`` mają nad nim skróty ``exclude_anonymised()``
  i ``anonymised()``.

Rozpoznajemy po **domenie adresu**, a nie po pustym imieniu ani po ``is_active=False``:

- puste imię i nazwisko ma też konto, które nigdy ich nie podało (konto z ``/admin/``, konto
  opiekuna sprzed pola nazwiska), i ukrycie go byłoby zgubieniem żywej osoby,
- ``is_active=False`` niesie także „konto zablokowane przez organizatora”, a takie konto ma dane
  i koordynator musi je widzieć, żeby je odblokować,
- domena ``invalid.`` (RFC 2606 rezerwuje ``.invalid``) jest skutkiem, którego nie da się osiągnąć
  inaczej niż anonimizacją: formularz rejestracji ani edycji takiego adresu nie przyjmie jako
  prawdziwego, a list na niego nigdy nie wyjdzie.

Postacie domeny są dwie: stała ``invalid.olimpiadakwantowa.pl`` (wszystkie konta wytarte do tej
pory i konkursy bez własnej marki) oraz ``invalid.<domena konkursu>`` przy włączonej marce
(``apps.accounts.profile.anonymised_email_domain``). Obie zaczynają domenę od ``invalid.``, więc
wystarcza jeden znacznik ``@invalid.`` – ten sam, którym posługiwała się retencja
(``apps.accounts.retention._is_anonymised`` woła teraz tę funkcję).

**Koszt w bazie.** Warunek jest ``LIKE '%@invalid.%'`` na jednej kolumnie ``accounts_user.email``,
którą każda lista osób i tak złącza (pokazuje adres albo nazwisko). Indeksu B-drzewa taki wzorzec
nie użyje – i nie musi: na liście stronicowanej to filtr nakładany na wiersze już zawężone do
konkursu, a nie droga dostępu do tabeli. Świadomie **nie** dokładamy indeksu częściowego ani
kolumny ``is_anonymised``: kolumna byłaby drugą prawdą obok adresu (a adres jest kluczem logowania,
który zmienia wyłącznie ``anonymise_account``), a indeks – migracją na tabeli kont dla zysku, którego
przy kilku tysiącach wierszy nie da się zmierzyć. Gdyby kiedyś był potrzebny, reguła ma jedno
miejsce i zmieni się tutaj.
"""

from __future__ import annotations

from django.db.models import Q

#: Znacznik domeny po anonimizacji. ``@`` z przodu przypina go do **domeny**, a nie do części
#: lokalnej – adres ``invalid.jan@example.com`` jest prawdziwym adresem i ma zostać widoczny.
ANONYMISED_EMAIL_MARKER = "@invalid."

#: Neutralny podpis wiersza, który musi zostać na liście (tabela wyników, odwołanie, protokół),
#: a którego osoba już nie istnieje. Zastępuje adres „deleted-…”, który nic nie mówi, a wygląda
#: jak błąd. Obok stoi zawsze kod publiczny – to on wiąże wiersz z dokumentacją zawodów.
DELETED_ACCOUNT_LABEL = "Konto usunięte"


def is_anonymised_email(email: str | None) -> bool:
    """Czy adres jest adresem konta po anonimizacji. Pusty adres – nie."""
    return bool(email) and ANONYMISED_EMAIL_MARKER in email.lower()


def is_anonymised(user) -> bool:
    """Czy konto przeszło przez anonimizację (własną, koordynatora albo retencyjną).

    ``None`` – nie: brak konta to inna sprawa (``SET_NULL`` po skasowaniu wiersza) i wołający,
    który ją obsługuje, ma własny podpis.
    """
    return user is not None and is_anonymised_email(getattr(user, "email", ""))


def anonymised_q(user_path: str = "") -> Q:
    """Warunek „konto po anonimizacji” dla querysetu dowolnego modelu, który sięga do konta.

    ``user_path`` to ścieżka od modelu querysetu do ``User`` **bez** końcowego ``__``:
    ``""`` dla samego ``User``, ``"user"`` dla ``Participant``, ``"participant__user"`` dla
    ``StageEntry``. Wołający dokleja ``~`` albo używa ``exclude(...)``.

    ``icontains``, a nie ``contains``: ``User.save`` sprowadza adres do małych liter, ale wiersze
    mogą przyjść inną drogą (``update()``, dane sprzed normalizacji), a różnica w koszcie jest żadna.
    """
    prefix = f"{user_path}__" if user_path else ""
    return Q(**{f"{prefix}email__icontains": ANONYMISED_EMAIL_MARKER})


def person_label(user, public_code: str = "") -> str:
    """Podpis osoby na liście, która **musi** pokazać także konta usunięte.

    Konto po anonimizacji dostaje „Konto usunięte” z kodem publicznym (gdy jest), nigdy adresu
    „deleted-…”. Zwykłe konto – imię i nazwisko, a w ich braku adres.
    """
    if is_anonymised(user):
        return f"{DELETED_ACCOUNT_LABEL} ({public_code})" if public_code else DELETED_ACCOUNT_LABEL
    if user is None:
        return DELETED_ACCOUNT_LABEL
    name = f"{user.first_name} {user.last_name}".strip()
    return name or user.email
