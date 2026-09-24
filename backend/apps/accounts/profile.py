"""Zarządzanie własnym kontem: edycja danych, zmiana adresu e-mail, usunięcie konta (RODO).

Trzy operacje, jedna wspólna zasada: **wykonawcą jest właściciel konta**, więc żadna z nich nie
może być prostym zapisem formularza w widoku. Każda ma skutki poza samym profilem – zmiana adresu
przenosi login, usunięcie dotyka wyników i recenzji – i każda musi zostawić ślad w audycie.

Dlaczego usunięcie konta ma **dwie** drogi (anonimizacja albo skasowanie wiersza): art. 17 RODO
daje prawo do usunięcia danych, ale nie prawo do usunięcia cudzej dokumentacji. Praca oceniona
przez dwóch recenzentów, rozstrzygnięty rozjazd ocen i ogłoszona tabela wyników są dokumentacją
zawodów – protokołem, na którym opierają się kwalifikacje innych uczestników. Skasowanie wiersza
``User`` zabrałoby kaskadą ``StageEntry`` → ``Submission`` → ``Review`` → oceny, czyli wyrwałoby
kartę z protokołu i przesunęłoby progi kwalifikacyjne. Dlatego uczestnik, który brał udział
w zawodach, dostaje **anonimizację**: dane osobowe znikają, a pseudonimowy wiersz (``public_code``)
zostaje. Kto konta faktycznie nie używał – nie ma czego chronić i jego wiersz znika w całości.

``publish_full_name`` gaśnie zawsze: zgoda na publikację nazwiska po usunięciu konta nie ma na czym
się opierać, a tabela, w której nazwisko już stoi, jest zamrożonym snapshotem (``apps.results``) –
jego nie ruszamy, bo to ogłoszony dokument.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .activation import (
    EMAIL_CHANGE_MAX_AGE,
    EMAIL_CHANGE_SALT,
    read_token,
    send_email_change_confirmation,
    send_email_changed_notice,
)
from .models import GROUP_COORDINATOR, CommitteeMember, CommitteeStatus, ConsentRecord, Participant, User
from .phones import normalize_phone

#: Pola, których **wartości** nie trafiają do audytu – tylko informacja, że się zmieniły.
#: ``apps.core.models`` stawia warunek wprost: w ``diff`` nie ma imion, nazwisk, e-maili ani szkół,
#: bo wpisy audytowe czytają też osoby bez prawa do danych osobowych uczestnika. „Co się zmieniło”
#: wystarcza do odtworzenia przebiegu sprawy; „na co” jest w profilu, dla tych, którzy mają dostęp.
#: ``supervisor_email`` jest tu, choć nie jest daną **uczestnika**: to adres osoby trzeciej
#: (nauczyciela), więc do audytu ma iść wyłącznie „zmienił” – tak samo jak przy telefonie.
#: ``birth_date`` dołącza do tej listy razem z wydaniem 0.30.0, a ``birth_year`` **nie**: rocznik
#: nie wskazuje osoby (dzieli uczestników na kilka grup po kilkaset), a pełna data urodzenia jest
#: klasycznym kluczem dopasowania do innych zbiorów i nie ma po co stać w rejestrze zdarzeń.
PERSONAL_FIELDS = frozenset(
    {"first_name", "last_name", "phone", "school", "email", "supervisor_email", "birth_date"}
)

#: Domena adresów po anonimizacji. ``.invalid`` jest zarezerwowana przez RFC 2606 – list na taki
#: adres nie wyjdzie nawet przez pomyłkę, a wiersz nadal spełnia unikalność i format ``EmailField``.
ANONYMISED_EMAIL_DOMAIN = "invalid.olimpiadakwantowa.pl"

#: Nazwa szkoły po anonimizacji. Pole jest w modelu obowiązkowe (idzie do grupowania w wynikach),
#: więc zamiast pustego napisu wstawiamy myślnik – widoczny znak „usunięto”, a nie brak danych.
ANONYMISED_SCHOOL = "—"

#: Rocznik po anonimizacji. Rok urodzenia jest daną osobową (wiek), a pole jest obowiązkowe;
#: 1900 jest wartością jawnie nieprawdziwą, więc nikt jej nie weźmie za dane uczestnika.
ANONYMISED_BIRTH_YEAR = 1900


def anonymised_email_domain(competition=None) -> str:
    """Domena adresu dla **nowej** anonimizacji – z odwrotem na :data:`ANONYMISED_EMAIL_DOMAIN`.

    Adres po anonimizacji jest **daną**, a nie konfiguracją: stoi w kolumnie logowania
    (``USERNAME_FIELD = "email"``) pod więzem ``accounts_user_email_ci_uniq``. Dlatego ta funkcja
    rozstrzyga wyłącznie o adresach nadawanych **od teraz**; kont już zanonimizowanych nie rusza
    żadna migracja danych i nie ruszy – zakaz jest wyrażony wykonalnie w
    ``apps/tenancy/tests/test_branding.py`` (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.4).

    Konkurs #1 zostaje więc przy ``invalid.olimpiadakwantowa.pl`` zarówno w kontach już wytartych,
    jak i w tych, które wytrze jutro: flagi marki nie ma, więc odwrót obowiązuje. Konkurs
    z włączoną marką dostaje ``invalid.<swoja domena>`` – adres w cudzej domenie byłby w jego bazie
    wierszem, którego nikt nie umie wyjaśnić.
    """
    from apps.tenancy.branding import uses_competition_branding

    if not uses_competition_branding(competition):
        return ANONYMISED_EMAIL_DOMAIN
    return f"invalid.{competition.primary_domain}" if competition.primary_domain else ANONYMISED_EMAIL_DOMAIN


def _is_coordinator(user: User) -> bool:
    return user.is_superuser or user.groups.filter(name=GROUP_COORDINATOR).exists()


# --- edycja własnych danych ---------------------------------------------------------------------


def _changed(diff: dict, field: str, before, after) -> None:
    """Dopisuje jedną zmianę do ``diff`` z poszanowaniem zakazu danych osobowych w audycie."""
    if before == after:
        return
    diff[field] = True if field in PERSONAL_FIELDS else {"from": before, "to": after}


def _participant_values(fields: dict) -> dict:
    """Sprawdza pola profilu uczestnika i zwraca wartości gotowe do zapisu.

    Osobno od zapisu, bo najpierw ma przejść **cała** walidacja: wartości sprawdzamy z osobna,
    więc bez tego rozdziału zły numer telefonu zostawiałby zapisane już imię i nazwisko.
    Transakcja by to wycofała, ale poprawność opartą na rollbacku łatwo zgubić przy pierwszym
    refaktorze.

    Zakres zmian wyznacza **obecność klucza** w ``fields``, a nie jego wartość: wołający (formularz
    uczestnika, ``PATCH /api/auth/me/``, ekran koordynatora) przekazuje to, co faktycznie przysłał.
    """
    # Importy lokalne: ``services`` importuje ``activation``, a nie ``profile`` – ale reguły
    # walidacji mieszkają w ``services`` i drugi raz ich tu nie piszemy.
    from .services import _require_birth_date, _require_grade, _require_voivodeship, _resolve_school

    values: dict = {}
    if "first_name" in fields:
        values["first_name"] = (fields["first_name"] or "").strip()
    if "last_name" in fields:
        values["last_name"] = (fields["last_name"] or "").strip()
    if "phone" in fields:
        values["phone"] = normalize_phone(fields["phone"])
    if "district" in fields:
        values["district"] = _require_voivodeship(fields["district"], required=True)
    if "grade" in fields:
        values["grade"] = _require_grade(fields["grade"])
    if "birth_date" in fields:
        # Pusta wartość znaczy „nie znamy dnia urodzin” i jest poprawna: tak wygląda profil sprzed
        # wydania 0.30.0 i tak wolno zapisać ekran koordynatora (``allow_unknown_birth_date``).
        # Rocznika **nie** czyścimy razem z datą – wiersz bez jednego i drugiego nie miałby po
        # czym liczyć wieku, a kolumna rocznika jest ``NOT NULL``.
        values["birth_date"] = _require_birth_date(fields["birth_date"])
    if "birth_year" in fields:
        # Droga wsteczna: klient API sprzed tej zmiany przysyła sam rocznik. Gdy przysłał też datę,
        # rozstrzyga data – rocznik wyliczy z niej ``Participant.save``.
        values["birth_year"] = int(fields["birth_year"])
    if "supervisor_email" in fields:
        # Adres opiekuna szkolnego. Normalizacja jest ta sama, którą stosuje panel opiekuna przy
        # szukaniu swoich uczniów – inaczej adres wpisany wielkimi literami cicho nie dopasowałby
        # się do żadnego konta. Pusty napis jest poprawną wartością i znaczy „nie mam opiekuna”.
        from .supervisors import normalize_supervisor_email

        values["supervisor_email"] = normalize_supervisor_email(fields["supervisor_email"])
    if "school" in fields or "school_id" in fields:
        name, school_obj = _resolve_school(fields.get("school", ""), fields.get("school_id"))
        values["school"] = name
        values["school_ref"] = school_obj
    return values


def _save_participant_values(participant: Participant, values: dict) -> dict:
    """Zapisuje sprawdzone wartości profilu i zwraca ``diff`` do wpisu audytowego.

    Zapis jest oddzielony od walidacji i od samego audytu, bo ten sam komplet pól zapisują dwa
    zdarzenia o różnym znaczeniu: „uczestnik poprawił swoje dane” i „koordynator poprawił dane
    konta”. Wpis audytowy stawia więc wołający – tutaj powstaje wyłącznie lista zmian.
    """
    diff: dict = {}
    user = participant.user
    user_updates = [name for name in ("first_name", "last_name") if name in values]
    for name in user_updates:
        _changed(diff, name, getattr(user, name), values[name])
        setattr(user, name, values[name])
    if user_updates:
        user.save(update_fields=user_updates)

    updates = [
        name
        for name in ("phone", "district", "grade", "birth_date", "birth_year", "school", "supervisor_email")
        if name in values
    ]
    for name in updates:
        _changed(diff, name, getattr(participant, name), values[name])
        setattr(participant, name, values[name])
    if "school_ref" in values:
        chosen = values["school_ref"]
        _changed(diff, "school_ref", participant.school_ref_id, chosen.pk if chosen else None)
        participant.school_ref = chosen
        updates.append("school_ref")
    if updates:
        participant.save(update_fields=updates)
    return diff


@transaction.atomic
def update_participant_profile(
    participant: Participant, *, actor: User, request=None, **fields
) -> Participant:
    """Zapisuje zmiany w profilu uczestnika i zostawia **jeden** wpis audytowy z listą zmian.

    Zakres zmian wyznacza **obecność klucza** w ``fields``, a nie jego wartość: wołający (formularz
    albo ``PATCH /api/auth/me/``) przekazuje to, co faktycznie przysłał. ``public_code`` nie jest
    tu edytowalny i nigdy nie będzie – to identyfikator w ogłoszonych tabelach wyników, więc jego
    zmiana zerwałaby powiązanie między uczestnikiem a opublikowanym wierszem.

    Walidacja jest w ``_participant_values``, a nie w formularzu: te same reguły (zamknięta lista
    województw, klasa 1–5, kształt numeru telefonu, szkoła ze słownika **albo** wolny tekst)
    obowiązują rejestrację, API i ten ekran. Formularz je powtarza wyłącznie po to, żeby błąd
    stanął pod właściwym polem.

    Wpis audytowy jest jeden, bo zdarzeniem jest **zapis formularza**, a nie każde pole osobno.
    Pól nietkniętych w ``diff`` nie ma – inaczej nie dałoby się odróżnić „zmienił województwo”
    od „otworzył formularz i zapisał bez zmian”.
    """
    # Uzupełnienie brakującej daty urodzenia jest **osobnym** zdarzeniem, a nie kolejną pozycją
    # w ``diff``: profile sprzed wydania 0.30.0 znają sam rocznik, a od pełnej daty zależy reguła
    # „zgoda opiekuna dla małoletniego”. Pytanie „od kiedy ten uczestnik ma dokładny wiek i kto go
    # wpisał” pada przy sporze o zgodę i nie może odpowiadać na nie sam napis „zmieniono dane”.
    completing_birth_date = participant.birth_date is None
    diff = _save_participant_values(participant, _participant_values(fields))
    audit(actor, "participant.profile_updated", participant, diff, request=request)
    if completing_birth_date and participant.birth_date is not None:
        # W ``diff`` audytu nie ma samej daty – jest daną osobową (wiek), a wpisy audytowe czyta
        # też ktoś bez prawa do danych uczestnika. Kogo dotyczy, mówi ``target``.
        audit(actor, "participant.birth_date_completed", participant, {}, request=request)
    return participant


@transaction.atomic
def update_own_names(user: User, *, first_name: str, last_name: str, request=None) -> User:
    """Imię i nazwisko dla konta **bez** profilu uczestnika (komitet, koordynator).

    Województwo członka komitetu nie jest tu edytowalne i to jest sedno tego ekranu: na nim opiera
    się reguła konfliktu interesów w przydziale recenzji, więc recenzent, który mógłby je sobie
    przestawić, mógłby też wejść na prace z własnego województwa. Zmiana (i usunięcie – pole jest
    opcjonalne) zostaje u koordynatora (``POST /api/auth/committee/{id}/verify-district/``).
    """
    diff: dict = {}
    _changed(diff, "first_name", user.first_name, (first_name or "").strip())
    _changed(diff, "last_name", user.last_name, (last_name or "").strip())
    user.first_name = (first_name or "").strip()
    user.last_name = (last_name or "").strip()
    user.save(update_fields=["first_name", "last_name"])
    audit(user, "account.profile_updated", user, diff, request=request)
    return user


# --- zmiana adresu e-mail -----------------------------------------------------------------------


def _assert_email_free(email: str, *, exclude_pk: int | None = None) -> str:
    """Adres musi być wolny **bez względu na wielkość liter** (constraint ``accounts_user_email_ci_uniq``)."""
    normalized = (email or "").strip().lower()
    if not normalized:
        raise DomainError("Podaj nowy adres e-mail.", "EMAIL_REQUIRED", status.HTTP_400_BAD_REQUEST)
    taken = User.objects.filter(email__iexact=normalized)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise DomainError(
            "Konto z tym adresem e-mail już istnieje.", "EMAIL_TAKEN", status.HTTP_400_BAD_REQUEST
        )
    return normalized


def request_email_change(user: User, *, new_email: str, request=None) -> str:
    """Wysyła na **nowy** adres link potwierdzający. Do kliknięcia obowiązuje adres dotychczasowy.

    Kolejność jest tu całą treścią zabezpieczenia: gdyby adres zmieniał się od razu po wpisaniu,
    literówka zamykałaby drogę powrotu (login i reset hasła idą przez adres), a przejęta sesja
    pozwalałaby przenieść konto na adres napastnika jednym POST-em. Potwierdzenie na nowym adresie
    dowodzi, że skrzynka istnieje i należy do osoby, która o zmianę poprosiła.
    """
    normalized = _assert_email_free(new_email, exclude_pk=user.pk)
    if normalized == user.email:
        raise DomainError("To już jest adres tego konta.", "EMAIL_UNCHANGED", status.HTTP_400_BAD_REQUEST)
    send_email_change_confirmation(user, normalized, request=request)
    audit(user, "account.email_change_requested", user, {"confirmation_sent": True}, request=request)
    return normalized


@transaction.atomic
def confirm_email_change(token: str, *, request=None) -> User:
    """Zmienia adres konta na podstawie tokenu z listu.

    Token niesie **oba** adresy: stary i nowy. Zgodność starego z tym, co jest w bazie, unieważnia
    linki z wcześniejszych prób – po dwóch kolejnych zmianach nie da się wrócić do adresu z listu,
    który leży w skrzynce od tygodnia. Unikalność sprawdzamy ponownie tutaj: między wysłaniem
    listu a kliknięciem ktoś inny mógł zająć ten adres.
    """
    payload = read_token(token, salt=EMAIL_CHANGE_SALT, max_age=EMAIL_CHANGE_MAX_AGE)
    user = User.objects.select_for_update().filter(pk=payload["pk"]).first()
    new_email = (payload.get("new_email") or "").strip().lower()
    if user is None or not new_email or user.email != (payload.get("email") or "").strip().lower():
        raise DomainError(
            "Link potwierdzający jest nieprawidłowy albo wygasł.",
            "EMAIL_CHANGE_INVALID",
            status.HTTP_400_BAD_REQUEST,
        )
    _assert_email_free(new_email, exclude_pk=user.pk)
    old_email = user.email
    user.email = new_email
    # Nowy adres jest potwierdzony właśnie tym kliknięciem – konto pozostaje aktywne bez drugiego
    # listu. Konto, które czekało na aktywację, przez tę drogę nie przechodzi: formularz zmiany
    # adresu jest za logowaniem.
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email", "email_verified_at"])
    _forget_allauth_addresses(user, old_email)
    audit(user, "account.email_changed", user, {"email": True}, request=request)
    # Stary adres dowiaduje się o przeniesieniu konta – to jedyny sygnał, jaki zostaje właścicielowi
    # skrzynki, jeśli o zmianę nie prosił.
    send_email_changed_notice(old_email, new_email)
    return user


def _forget_allauth_addresses(user: User, old_email: str) -> None:
    """Usuwa wpisy ``allauth.EmailAddress`` ze starym adresem tego konta.

    Wpisy allauth służą wyłącznie dopasowaniu loginu społecznościowego do konta. Zostawiony wpis
    ze starym, „potwierdzonym” adresem znaczyłby, że konto nadal daje się połączyć po adresie,
    którego już nie używa – czyli po adresie, który za chwilę może należeć do kogoś innego.
    """
    from allauth.account.models import EmailAddress

    EmailAddress.objects.filter(user=user, email__iexact=old_email).delete()


# --- usunięcie własnego konta (RODO) ------------------------------------------------------------


def competition_footprint(user: User) -> dict:
    """Co w dokumentacji zawodów odwołuje się do tego konta. Puste = nie ma czego chronić.

    Liczymy zgłoszenia i prace uczestnika oraz recenzje członka komitetu. To dokładnie te obiekty,
    które kaskada ``User.delete()`` zabrałaby ze sobą (``StageEntry`` → ``Submission`` → ``Review``
    → ``FinalGrade``), plus recenzje po stronie komitetu, które są chronione ``PROTECT`` i nie
    dałyby się skasować wcale, plus potwierdzenia opiekuna szkolnego (``SchoolParticipation``) –
    te kaskada zabrałaby dokładnie tak samo, ``SchoolSupervisor`` → ``SchoolParticipation``.
    Potwierdzenie „szkoła bierze udział w tej edycji” jest dla opiekuna tym, czym zgłoszenie jest
    dla uczestnika: oświadczeniem przypiętym do konkretnej edycji, na którym organizator opiera
    listę szkół biorących udział i wystawienie zaświadczeń – zniknięcie go bez śladu byłoby taką
    samą wyrwaną kartą z dokumentacji, jak skasowana praca.

    Liczymy po **wszystkich** profilach tej osoby, nie po profilu jednego konkursu: pytanie brzmi
    „co zabierze skasowanie konta”, a konto jest platformowe i kaskada nie zna granicy konkursu
    (§ 3.3). Zawężenie do konkursu z żądania pokazywałoby uczestnikowi dwóch olimpiad zero prac
    do stracenia w chwili, gdy traci komplet.
    """
    from apps.competitions.models import StageEntry
    from apps.grading.models import Review
    from apps.submissions.models import Submission

    from .models import SchoolParticipation
    from .services import participations_of

    participants = list(participations_of(user).values_list("pk", flat=True))
    member = getattr(user, "committee_member", None)
    supervisor = getattr(user, "school_supervisor", None)
    return {
        "entries": StageEntry.objects.filter(participant_id__in=participants).count(),
        "submissions": Submission.objects.filter(entry__participant_id__in=participants).count(),
        "reviews": Review.objects.filter(reviewer=member).count() if member else 0,
        "school_participations": (
            SchoolParticipation.objects.filter(supervisor=supervisor).count() if supervisor else 0
        ),
    }


def _delete_sessions(user: User) -> int:
    """Kasuje sesje użytkownika. Bez tego ciasteczko z cudzej przeglądarki dalej otwierałoby panel.

    Sesje trzymamy w bazie (domyślny backend Django), a klucz użytkownika jest w **zaszyfrowanej**
    treści sesji – nie ma go w kolumnie, po której dałoby się filtrować. Dlatego przeglądamy
    sesje niewygasłe i dekodujemy je; tabela sesji jest mała (wygasłe czyści ``clearsessions``),
    a operacja zdarza się raz na usunięcie konta.
    """
    from django.contrib.sessions.models import Session

    identifier = str(user.pk)
    stale = [
        session.session_key
        for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator()
        if session.get_decoded().get("_auth_user_id") == identifier
    ]
    if not stale:
        return 0
    return Session.objects.filter(session_key__in=stale).delete()[0]


def _drop_credentials(user: User) -> None:
    """Odbiera kontu wszystkie poświadczenia: hasło, token API, powiązania z dostawcami, sesje."""
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount
    from rest_framework.authtoken.models import Token

    Token.objects.filter(user=user).delete()
    SocialAccount.objects.filter(user=user).delete()
    EmailAddress.objects.filter(user=user).delete()
    _delete_sessions(user)


@transaction.atomic
def anonymise_account(user: User, *, actor: User | None = None, request=None) -> User:
    """Wyciera dane osobowe, zostawiając pseudonimowy wiersz uczestnika i dokumentację zawodów.

    ``actor`` jest opcjonalny i domyślnie jest nim **sam właściciel konta**: to on wnosi żądanie
    usunięcia danych. Koordynator, który kasuje cudze konto z panelu, podaje siebie – inaczej
    wpis audytowy przypisywałby jego decyzję osobie, której konto właśnie zniknęło.

    Co **zostaje** i dlaczego: ``Participant.public_code`` (pod tym kodem uczestnik stoi
    w ogłoszonych tabelach – bez niego wiersz snapshotu przestałby dać się powiązać z odwołaniem
    czy reklamacją), ``district`` (pole ma zamkniętą listę, więc puste nie jest legalną wartością,
    a okręg sam nie identyfikuje osoby) oraz sam fakt udziału: zgłoszenia, prace, oceny.

    Co **znika**: adres e-mail (zastąpiony adresem w domenie ``.invalid``), imię, nazwisko, telefon,
    nazwa szkoły, dowiązanie do rejestru szkół, rocznik, hasło, tokeny, powiązania z Google
    i Facebookiem, sesje. Zgody dostają ``withdrawn_at`` – dowód, że kiedyś obowiązywały, zostaje,
    ale żadna z nich nie jest już podstawą przetwarzania.

    To samo dotyczy profilu **opiekuna szkolnego**, jeśli to konto go ma: szkoła, szkoła z rejestru
    i telefon znikają, a jego zgody (regulamin, RODO złożone przy ``/register/supervisor/``)
    dostają ``withdrawn_at`` tak samo jak zgody uczestnika. Wiersz ``SchoolSupervisor`` sam zostaje
    – tak samo, jak zostaje wiersz ``Participant`` – bo to on niesie ``SchoolParticipation``:
    potwierdzenia „szkoła bierze udział w tej edycji”, na których organizator opiera listę szkół
    i zaświadczenia. Konto bywa uczestnikiem i opiekunem naraz (adres nauczyciela, który sam kiedyś
    startował), więc czyścimy **oba** profile w jednym przebiegu niezależnie od tego, który z nich
    dał powód do anonimizacji zamiast skasowania wiersza (``competition_footprint``) – rozdzielanie
    tego na dwa wywołania zostawiałoby czasem jeden profil z danymi osobowymi przy koncie, którego
    właściciel już zniknął.

    Czyścimy **każdy** profil uczestnika tej osoby, a nie profil jednego konkursu. Anonimizacja
    jest zdarzeniem platformowym: znika adres e-mail, imię, nazwisko i hasło, czyli konto jako
    takie. Zostawienie szkoły i rocznika w profilu drugiej olimpiady dałoby wiersz z danymi
    osobowymi przy koncie, którego właściciel został już wytarty – i to bez żadnej podstawy, bo
    zalogować się do tego drugiego konkursu też się nie da (§ 3.3).

    **Wypowiedzi na forum** (``apps.forum``) zostają, a znika spod nich podpis – i to nie wymaga
    tu ani jednej linijki, bo obie połowy tej reguły są już w tamtym module: ``author`` jest
    ``SET_NULL``, a podpis powstaje w jednym miejscu (``apps.forum.models.display_author``),
    które puste imię czyta tak samo jak skasowane konto i oddaje „Użytkownik usunięty”. Decyzja,
    żeby treści nie kasować, jest decyzją o rozmowie: pod wpisem stoją cudze odpowiedzi, a wycięcie
    akapitu, do którego ktoś się odniósł, zamienia je w bełkot. Sama treść przestaje być powiązana
    z osobą, więc żądanie z art. 17 jest spełnione – a uczestnik, który chce zdjąć konkretny wpis
    **przed** usunięciem konta, ma do tego własny przycisk na forum.
    """
    now = timezone.now()
    from apps.competitions.scoping import resolve_competition

    from .services import participations_of

    participants = list(participations_of(user))

    # Konkurs z kontekstu, odwrotem miękkim: anonimizację wnosi albo właściciel konta (żądanie
    # pod domeną konkursu), albo kosiarka retencji, która chodzi po konkursach z ``each_competition``
    # i każdy z nich wiąże. „Nie wiadomo, czyje to konto” daje dzisiejszą domenę – tę samą, co
    # wszystkie konta wytarte do tej pory.
    user.email = f"deleted-{user.pk}@{anonymised_email_domain(resolve_competition())}"
    user.first_name = ""
    user.last_name = ""
    user.set_unusable_password()
    user.is_active = False
    # ``email_verified_at`` zostaje nietknięte: adres był kiedyś potwierdzony, a wyzerowanie pola
    # wstawiłoby konto na listę „oczekujących na aktywację” w panelu koordynatora i pod kosiarkę
    # nieaktywowanych kont (``apps.accounts.tasks``).
    user.save(update_fields=["email", "first_name", "last_name", "password", "is_active"])

    for participant in participants:
        participant.phone = ""
        participant.school = ANONYMISED_SCHOOL
        participant.school_ref = None
        participant.birth_year = ANONYMISED_BIRTH_YEAR
        # Data urodzenia znika **cała**, a nie „do rocznika”: dzień i miesiąc urodzin same w sobie
        # zawężają krąg osób, a po anonimizacji nie mają czego opisywać. Rocznik zostaje podmieniony
        # na jawnie nieprawdziwy, bo kolumna jest ``NOT NULL`` (patrz ``ANONYMISED_BIRTH_YEAR``).
        participant.birth_date = None
        participant.publish_full_name = False
        participant.save(
            update_fields=[
                "phone",
                "school",
                "school_ref",
                "birth_date",
                "birth_year",
                "publish_full_name",
            ]
        )
    ConsentRecord.objects.filter(participant__in=participants, withdrawn_at__isnull=True).update(
        withdrawn_at=now
    )

    supervisor = getattr(user, "school_supervisor", None)
    if supervisor is not None:
        supervisor.school = ""
        supervisor.school_ref = None
        supervisor.phone = ""
        supervisor.save(update_fields=["school", "school_ref", "phone"])
        ConsentRecord.objects.filter(supervisor=supervisor, withdrawn_at__isnull=True).update(
            withdrawn_at=now
        )

    # Zaświadczenia o statusie ucznia znikają **całe** – wiersze i pliki (``apps.student_status``).
    # Skan z datą urodzenia i podpisem dyrektora szkoły nie jest dokumentacją zawodów, tylko
    # dokumentem tożsamości szkolnej; po anonimizacji nie ma czyj status potwierdzać.
    from apps.student_status.services import erase_for_user

    erase_for_user(user)

    _drop_credentials(user)
    audit(actor or user, "account.anonymised", user, {"user_id": user.pk}, request=request)
    return user


def _erase_account(user: User, *, actor: User | None = None, request=None) -> str:
    """Rdzeń usunięcia konta – wspólny dla żądania właściciela i dla decyzji koordynatora.

    Zwraca ``"anonymised"`` albo ``"deleted"``: która z dwóch dróg, rozstrzyga **ślad w zawodach**,
    a nie to, kto kasuje. Gdyby każde wejście liczyło ten ślad po swojemu, konto z pracą i recenzją
    dałoby się w końcu skasować kaskadą z jednego z nich – a to wyrwana karta z protokołu.
    """
    footprint = competition_footprint(user)
    if any(footprint.values()):
        anonymise_account(user, actor=actor, request=request)
        return "anonymised"
    pk = user.pk
    # Pliki zaświadczeń o statusie ucznia **przed** kaskadą: ``user.delete()`` zabierze wiersze
    # (``Participant`` → ``StudentStatusCertificate``), ale nie wie o obiektach w storage.
    from apps.student_status.services import erase_for_user

    erase_for_user(user)
    _drop_credentials(user)
    # Audyt **przed** skasowaniem wiersza: po ``delete()`` nie ma z czego wziąć ``target_type``,
    # a ``actor`` będący samym kasowanym kontem i tak zgaśnie na ``SET_NULL``. W ``diff`` jest
    # wyłącznie identyfikator – żadnej danej osobowej, bo wpis zostaje w bazie na stałe.
    audit(actor, "account.deleted", user, {"user_id": pk}, request=request)
    user.delete()
    return "deleted"


@transaction.atomic
def delete_own_account(user: User, *, request=None) -> str:
    """Realizuje żądanie usunięcia konta. Zwraca ``"anonymised"`` albo ``"deleted"``.

    Koordynator i superużytkownik tą drogą nie przechodzą: ich konto jest jedynym wejściem do
    prowadzenia edycji (a ``InvitationCode.created_by`` jest na nich ``PROTECT``), więc
    „usuń konto” w panelu byłoby jednym kliknięciem od zablokowania zawodów. Odebranie roli
    i skasowanie konta organizatora jest decyzją administracyjną, nie samoobsługową.
    """
    if _is_coordinator(user):
        raise DomainError(
            "Konta koordynatora nie można usunąć z panelu – skontaktuj się z administratorem serwisu.",
            "COORDINATOR_SELF_DELETE",
            status.HTTP_400_BAD_REQUEST,
        )
    # ``actor=None``: wykonawcą jest konto, które właśnie znika. Wpis o anonimizacji przypisuje
    # sobie wtedy samo konto (``anonymise_account``), a wpis o skasowaniu wiersza nie ma już do
    # czego się odwołać – ``actor`` zgasłby na ``SET_NULL`` sekundę później.
    return _erase_account(user, actor=None, request=request)


@sensitive_variables()
def verify_self_deletion_credentials(user: User, *, password: str = "", email: str = "") -> None:
    """Potwierdzenie tożsamości przed usunięciem konta.

    Konto hasłowe podaje hasło. Konto **bez** użytecznego hasła (zakładane przez Google/Facebooka)
    nie ma czego podać, a wpuszczenie go bez potwierdzenia znaczyłoby, że każda niezamknięta sesja
    w cudzej przeglądarce jest jednym kliknięciem od usunięcia danych. Dlatego tam potwierdzeniem
    jest przepisanie własnego adresu e-mail: nie jest to sekret, ale wymusza świadome działanie
    i wyklucza przypadkowe kliknięcie.
    """
    if user.has_usable_password():
        if not password or not user.check_password(password):
            raise DomainError("Nieprawidłowe hasło.", "INVALID_PASSWORD", status.HTTP_400_BAD_REQUEST)
        return
    if (email or "").strip().lower() != user.email:
        raise DomainError(
            "Przepisz dokładnie adres e-mail swojego konta.",
            "EMAIL_MISMATCH",
            status.HTTP_400_BAD_REQUEST,
        )


# --- konto cudze: edycja i usunięcie przez koordynatora -----------------------------------------
#
# Organizator prowadzi zawody i odbiera telefony w rodzaju „zapisałem się z literówką w adresie”,
# „proszę wykreślić mojego syna z olimpiady”, „nasza szkoła zmieniła nazwę”. Dotąd każda taka
# sprawa kończyła się w ``/admin/``, czyli w narzędziu, które nie zna ani jednej reguły domeny:
# tam zmiana adresu e-mail nie sprząta wpisów allauth, skasowanie konta uczestnika zabiera kaskadą
# jego prace i recenzje (czyli protokół zawodów), a po żadnej z tych operacji nie zostaje wpis
# w audycie razem z resztą historii sprawy. Te dwie funkcje są wejściem koordynatora do tych
# samych reguł, które obowiązują właściciela konta – z jedną różnicą: wykonawcą jest ktoś inny
# niż osoba, której dane się zmieniają, więc każde zdarzenie ma własną akcję w audycie.


#: Konto koordynatora (i superużytkownika) jest z tego ekranu **niewidoczne dla zapisu**: to jedyne
#: wejście do prowadzenia edycji, a ``InvitationCode.created_by`` jest na nim ``PROTECT``. Dwóch
#: koordynatorów mogłoby się nawzajem zablokować jednym kliknięciem, więc zmiana i usunięcie takich
#: kont zostaje decyzją administracyjną (``/admin/``), nie panelową.
COORDINATOR_PROTECTED_MESSAGE = (
    "To konto koordynatora – jego dane zmienia administrator serwisu w panelu /admin/."
)


def _assert_not_coordinator(user: User) -> None:
    if _is_coordinator(user):
        raise DomainError(COORDINATOR_PROTECTED_MESSAGE, "COORDINATOR_PROTECTED", status.HTTP_400_BAD_REQUEST)


def _account_values(fields: dict) -> dict:
    """Sprawdza pola samego konta (bez profilu roli). Klucz nieobecny = pole nietykane."""
    values: dict = {}
    if "first_name" in fields:
        values["first_name"] = (fields["first_name"] or "").strip()
    if "last_name" in fields:
        values["last_name"] = (fields["last_name"] or "").strip()
    if "is_active" in fields:
        values["is_active"] = bool(fields["is_active"])
    return values


def _committee_values(fields: dict) -> dict:
    """Sprawdza pola profilu członka komitetu zmieniane przez koordynatora."""
    from .services import _require_voivodeship

    values: dict = {}
    if "district" in fields:
        # Województwo członka komitetu jest opcjonalne – pusta wartość znaczy „ocenia prace
        # z całego kraju”, a nie „pole niewypełnione”.
        values["district"] = _require_voivodeship(fields["district"], required=False)
    if "is_appeals_committee" in fields:
        values["is_appeals_committee"] = bool(fields["is_appeals_committee"])
    if "status" in fields:
        chosen = (fields["status"] or "").strip()
        if chosen not in CommitteeStatus.values:
            raise DomainError(
                "Nieznany status członka komitetu.", "STATUS_INVALID", status.HTTP_400_BAD_REQUEST
            )
        values["status"] = chosen
    return values


def _save_committee_values(member: CommitteeMember, values: dict, *, actor: User) -> dict:
    """Zapisuje profil komitetu i zwraca ``diff``. Zwrócenie do ACTIVE idzie istniejącą drogą.

    Kolejność nie jest dowolna. Najpierw ``is_appeals_committee``, bo od tej flagi zależy, które
    grupy dostanie konto przy zatwierdzeniu (``reviewer`` czy także ``appeals``) – odwrotna
    kolejność zostawiłaby świeżo zatwierdzonego członka komisji odwoławczej bez jej uprawnień.
    Potem status: PENDING → ACTIVE przechodzi przez ``approve_committee_member``, czyli dokładnie
    tę samą ścieżkę, co przycisk „Zatwierdź” na pulpicie (status, data, kto zatwierdził, grupy).
    Odwieszenie (SUSPENDED → ACTIVE) tamta funkcja świadomie odrzuca, więc tutaj zostaje sam zapis
    statusu plus **ten sam** helper od grup – żeby nie było drugiej definicji „co dostaje recenzent”.

    Zawieszenie nie odbiera grup celowo: prawo do recenzowania rozstrzyga status profilu
    (``accounts.services.active_reviewer_profile``), więc zawieszony recenzent i tak nie wejdzie
    ani na kolejkę, ani na plik – a zdjęte grupy trzeba by przy odwieszeniu zgadywać z powrotem.
    """
    from .services import _grant_reviewer_groups, approve_committee_member

    diff: dict = {}
    if "is_appeals_committee" in values:
        _changed(diff, "is_appeals_committee", member.is_appeals_committee, values["is_appeals_committee"])
        member.is_appeals_committee = values["is_appeals_committee"]
        member.save(update_fields=["is_appeals_committee"])
    target = values.get("status")
    if target is not None and target != member.status:
        _changed(diff, "status", member.status, target)
        if target == CommitteeStatus.ACTIVE and member.status == CommitteeStatus.PENDING:
            member = approve_committee_member(member, actor=actor)
        else:
            member.status = target
            member.save(update_fields=["status"])
            if target == CommitteeStatus.ACTIVE:
                _grant_reviewer_groups(member)
    if "district" in values:
        # Ta sama reguła, co w ``verify_committee_district``: wartość od koordynatora jest
        # z definicji potwierdzona, a wyczyszczenie pola zdejmuje potwierdzenie, bo nie ma już
        # czego potwierdzać. Tamtej funkcji tu nie wołamy, bo dotyczy wyłącznie członka ACTIVE
        # i zostawia własny wpis audytowy – a ten ekran zapisuje całe konto jednym zdarzeniem.
        district = values["district"]
        _changed(diff, "district", member.district, district)
        _changed(diff, "district_verified", member.district_verified, district is not None)
        member.district = district
        member.district_verified = district is not None
        member.save(update_fields=["district", "district_verified"])
    return diff


@transaction.atomic
def update_account_by_coordinator(
    user: User,
    *,
    actor: User,
    request=None,
    account: dict,
    participant: dict | None = None,
    committee: dict | None = None,
) -> User:
    """Zapisuje dane cudzego konta z panelu koordynatora – **jednym** wpisem audytowym.

    Trzy słowniki, bo to trzy różne obiekty (konto, profil uczestnika, profil komitetu), a nie
    jeden płaski formularz: pole ``district`` znaczy co innego w każdym z dwóch profili, a konto
    bez profilu roli nie ma ich wcale. Słownik pominięty (``None``) to profil, którego ekran nie
    pokazywał; klucz nieobecny w słowniku – pole, którego koordynator nie przysłał.

    Adres e-mail zmienia się tu **od razu**, bez listu potwierdzającego, i to jest jedyna różnica
    wobec samoobsługowej zmiany adresu (``request_email_change``). Tam potwierdzenie dowodzi, że
    skrzynka istnieje i należy do proszącego; tutaj dowodem jest decyzja organizatora, który
    zwykle właśnie rozmawia z uczestnikiem przez telefon, bo do skrzynki z literówką nic nie
    dochodzi. Wpisy ``allauth`` ze starym adresem znikają tak samo, jak przy potwierdzeniu
    (``_forget_allauth_addresses``), inaczej konto dałoby się dalej połączyć z Google po adresie,
    który za chwilę może należeć do kogoś innego.

    ``email_verified_at`` zostaje nietknięte: potwierdzeniem adresu był kiedyś list, a wyzerowanie
    pola wstawiłoby konto pod kosiarkę nieaktywowanych rejestracji – czyli poprawka literówki
    kończyłaby się skasowaniem konta po czterech godzinach.

    Wpis audytowy jest jeden (``account.updated_by_coordinator``), bo zdarzeniem jest zapis
    formularza. ``diff`` trzyma się konwencji ``update_participant_profile``: pola osobowe wchodzą
    tam jako samo „zmienione”, bez wartości – wpisy audytowe czyta też ktoś bez prawa do danych
    uczestnika.
    """
    from apps.tenancy.context import current_competition

    from .services import participant_for

    _assert_not_coordinator(user)
    # Najpierw **cała** walidacja – trzech obiektów naraz, więc bez tego rozdziału zły numer
    # telefonu zostawiałby zapisany nowy adres e-mail i zmieniony status komitetu.
    values = _account_values(account)
    if "email" in account:
        values["email"] = _assert_email_free(account["email"], exclude_pk=user.pk)
    # Profil z konkursu, którego panel koordynator ma przed sobą: koordynator olimpiady A nie
    # poprawia szkoły uczestnikowi w olimpiadzie B, nawet jeżeli to jedno konto (§ 3.3).
    profile = participant_for(user, current_competition())
    member = getattr(user, "committee_member", None)
    participant_values = _participant_values(participant) if participant else {}
    committee_values = _committee_values(committee) if committee else {}
    if participant_values and profile is None:
        raise DomainError(
            "To konto nie ma profilu uczestnika.", "NO_PARTICIPANT_PROFILE", status.HTTP_400_BAD_REQUEST
        )
    if committee_values and member is None:
        raise DomainError(
            "To konto nie ma profilu członka komitetu.",
            "NO_COMMITTEE_PROFILE",
            status.HTTP_400_BAD_REQUEST,
        )

    diff: dict = {}
    previous_email = user.email
    updates = [name for name in ("first_name", "last_name", "email", "is_active") if name in values]
    for name in updates:
        _changed(diff, name, getattr(user, name), values[name])
        setattr(user, name, values[name])
    if updates:
        user.save(update_fields=updates)
    if user.email != previous_email:
        _forget_allauth_addresses(user, previous_email)
        # Wprost, bo to inna droga niż ``account.email_changed``: adres nie został potwierdzony
        # kliknięciem w link, tylko zmieniony decyzją organizatora.
        diff["email_changed_without_confirmation"] = True
    if profile is not None and participant_values:
        diff.update(_save_participant_values(profile, participant_values))
    if member is not None and committee_values:
        diff.update(_save_committee_values(member, committee_values, actor=actor))
    audit(actor, "account.updated_by_coordinator", user, diff, request=request)
    return user


@transaction.atomic
def delete_account_by_coordinator(user: User, *, actor: User, request=None) -> str:
    """Usuwa cudze konto z panelu koordynatora. Zwraca ``"anonymised"`` albo ``"deleted"``.

    Skutek jest dokładnie ten sam, co przy żądaniu właściciela (``delete_own_account``), bo pyta
    o to samo: czy konto zostawiło ślad w dokumentacji zawodów. Zmienia się wyłącznie wykonawca,
    więc zdarzenie ma własną akcję w audycie – „kto skasował to konto” jest przy cudzej decyzji
    pytaniem pierwszym, a nie ciekawostką.

    Dwie odmowy. Własnego konta koordynator tą drogą nie skasuje: ekran zarządzania kontami nie ma
    być wyjściem awaryjnym dla samego organizatora, a operacja bez potwierdzenia tożsamości
    (którego ten ekran nie ma – ma je ``/account/delete/``) byłaby jednym kliknięciem od utraty
    dostępu do zawodów. Cudzego konta koordynatora również nie – patrz ``COORDINATOR_PROTECTED``.

    ``diff`` nie zawiera ani jednej danej osobowej: sam skutek i to, czy konto miało ślad
    w zawodach. Wpis zostaje w bazie na stałe, także po skasowaniu wiersza użytkownika.
    """
    if actor is not None and user.pk == actor.pk:
        raise DomainError(
            "Własnego konta nie usuwa się z tego ekranu – służy do tego „Usuń konto” we własnym profilu.",
            "SELF_DELETE",
            status.HTTP_400_BAD_REQUEST,
        )
    _assert_not_coordinator(user)
    had_footprint = any(competition_footprint(user).values())
    result = "anonymised" if had_footprint else "deleted"
    # Audyt **przed** operacją: po ``delete()`` nie ma z czego wziąć ``target_id`` skasowanego konta.
    audit(
        actor,
        "account.deleted_by_coordinator",
        user,
        {"result": result, "had_footprint": had_footprint},
        request=request,
    )
    return _erase_account(user, actor=actor, request=request)
