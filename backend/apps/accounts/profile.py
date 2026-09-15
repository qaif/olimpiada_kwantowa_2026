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
from .models import GROUP_COORDINATOR, ConsentRecord, Participant, User
from .phones import normalize_phone

#: Pola, których **wartości** nie trafiają do audytu – tylko informacja, że się zmieniły.
#: ``apps.core.models`` stawia warunek wprost: w ``diff`` nie ma imion, nazwisk, e-maili ani szkół,
#: bo wpisy audytowe czytają też osoby bez prawa do danych osobowych uczestnika. „Co się zmieniło”
#: wystarcza do odtworzenia przebiegu sprawy; „na co” jest w profilu, dla tych, którzy mają dostęp.
PERSONAL_FIELDS = frozenset({"first_name", "last_name", "phone", "school", "email"})

#: Domena adresów po anonimizacji. ``.invalid`` jest zarezerwowana przez RFC 2606 – list na taki
#: adres nie wyjdzie nawet przez pomyłkę, a wiersz nadal spełnia unikalność i format ``EmailField``.
ANONYMISED_EMAIL_DOMAIN = "invalid.olimpiadakwantowa.pl"

#: Nazwa szkoły po anonimizacji. Pole jest w modelu obowiązkowe (idzie do grupowania w wynikach),
#: więc zamiast pustego napisu wstawiamy myślnik – widoczny znak „usunięto”, a nie brak danych.
ANONYMISED_SCHOOL = "—"

#: Rocznik po anonimizacji. Rok urodzenia jest daną osobową (wiek), a pole jest obowiązkowe;
#: 1900 jest wartością jawnie nieprawdziwą, więc nikt jej nie weźmie za dane uczestnika.
ANONYMISED_BIRTH_YEAR = 1900


def _is_coordinator(user: User) -> bool:
    return user.is_superuser or user.groups.filter(name=GROUP_COORDINATOR).exists()


# --- edycja własnych danych ---------------------------------------------------------------------


def _changed(diff: dict, field: str, before, after) -> None:
    """Dopisuje jedną zmianę do ``diff`` z poszanowaniem zakazu danych osobowych w audycie."""
    if before == after:
        return
    diff[field] = True if field in PERSONAL_FIELDS else {"from": before, "to": after}


@transaction.atomic
def update_participant_profile(
    participant: Participant, *, actor: User, request=None, **fields
) -> Participant:
    """Zapisuje zmiany w profilu uczestnika i zostawia **jeden** wpis audytowy z listą zmian.

    Zakres zmian wyznacza **obecność klucza** w ``fields``, a nie jego wartość: wołający (formularz
    albo ``PATCH /api/auth/me/``) przekazuje to, co faktycznie przysłał. ``public_code`` nie jest
    tu edytowalny i nigdy nie będzie – to identyfikator w ogłoszonych tabelach wyników, więc jego
    zmiana zerwałaby powiązanie między uczestnikiem a opublikowanym wierszem.

    Walidacja jest tu, a nie w formularzu: te same reguły (zamknięta lista województw, klasa 1–5,
    kształt numeru telefonu, szkoła ze słownika **albo** wolny tekst) obowiązują rejestrację,
    API i ten ekran. Formularz je powtarza wyłącznie po to, żeby błąd stanął pod właściwym polem.

    Wpis audytowy jest jeden, bo zdarzeniem jest **zapis formularza**, a nie każde pole osobno.
    Pól nietkniętych w ``diff`` nie ma – inaczej nie dałoby się odróżnić „zmienił województwo”
    od „otworzył formularz i zapisał bez zmian”.
    """
    # Importy lokalne: ``services`` importuje ``activation``, a nie ``profile`` – ale reguły
    # walidacji mieszkają w ``services`` i drugi raz ich tu nie piszemy.
    from .services import _require_grade, _require_voivodeship, _resolve_school

    # Najpierw **cała** walidacja, dopiero potem zapis: wartości sprawdzamy z osobna, więc bez tego
    # rozdziału zły numer telefonu zostawiałby zapisane już imię i nazwisko. Transakcja by to
    # wycofała, ale poprawność opartą na rollbacku łatwo zgubić przy pierwszym refaktorze.
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
    if "birth_year" in fields:
        values["birth_year"] = int(fields["birth_year"])
    if "school" in fields or "school_id" in fields:
        name, school_obj = _resolve_school(fields.get("school", ""), fields.get("school_id"))
        values["school"] = name
        values["school_ref"] = school_obj

    diff: dict = {}
    user = participant.user
    user_updates = [name for name in ("first_name", "last_name") if name in values]
    for name in user_updates:
        _changed(diff, name, getattr(user, name), values[name])
        setattr(user, name, values[name])
    if user_updates:
        user.save(update_fields=user_updates)

    updates = [name for name in ("phone", "district", "grade", "birth_year", "school") if name in values]
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

    audit(actor, "participant.profile_updated", participant, diff, request=request)
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
    dałyby się skasować wcale.
    """
    from apps.competitions.models import StageEntry
    from apps.grading.models import Review
    from apps.submissions.models import Submission

    participant = getattr(user, "participant", None)
    member = getattr(user, "committee_member", None)
    return {
        "entries": StageEntry.objects.filter(participant=participant).count() if participant else 0,
        "submissions": (
            Submission.objects.filter(entry__participant=participant).count() if participant else 0
        ),
        "reviews": Review.objects.filter(reviewer=member).count() if member else 0,
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
def anonymise_account(user: User, *, request=None) -> User:
    """Wyciera dane osobowe, zostawiając pseudonimowy wiersz uczestnika i dokumentację zawodów.

    Co **zostaje** i dlaczego: ``Participant.public_code`` (pod tym kodem uczestnik stoi
    w ogłoszonych tabelach – bez niego wiersz snapshotu przestałby dać się powiązać z odwołaniem
    czy reklamacją), ``district`` (pole ma zamkniętą listę, więc puste nie jest legalną wartością,
    a okręg sam nie identyfikuje osoby) oraz sam fakt udziału: zgłoszenia, prace, oceny.

    Co **znika**: adres e-mail (zastąpiony adresem w domenie ``.invalid``), imię, nazwisko, telefon,
    nazwa szkoły, dowiązanie do rejestru szkół, rocznik, hasło, tokeny, powiązania z Google
    i Facebookiem, sesje. Zgody dostają ``withdrawn_at`` – dowód, że kiedyś obowiązywały, zostaje,
    ale żadna z nich nie jest już podstawą przetwarzania.
    """
    now = timezone.now()
    participant = getattr(user, "participant", None)

    user.email = f"deleted-{user.pk}@{ANONYMISED_EMAIL_DOMAIN}"
    user.first_name = ""
    user.last_name = ""
    user.set_unusable_password()
    user.is_active = False
    # ``email_verified_at`` zostaje nietknięte: adres był kiedyś potwierdzony, a wyzerowanie pola
    # wstawiłoby konto na listę „oczekujących na aktywację” w panelu koordynatora i pod kosiarkę
    # nieaktywowanych kont (``apps.accounts.tasks``).
    user.save(update_fields=["email", "first_name", "last_name", "password", "is_active"])

    if participant is not None:
        participant.phone = ""
        participant.school = ANONYMISED_SCHOOL
        participant.school_ref = None
        participant.birth_year = ANONYMISED_BIRTH_YEAR
        participant.publish_full_name = False
        participant.save(update_fields=["phone", "school", "school_ref", "birth_year", "publish_full_name"])
        ConsentRecord.objects.filter(participant=participant, withdrawn_at__isnull=True).update(
            withdrawn_at=now
        )

    _drop_credentials(user)
    audit(user, "account.anonymised", user, {"user_id": user.pk}, request=request)
    return user


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
    footprint = competition_footprint(user)
    if any(footprint.values()):
        anonymise_account(user, request=request)
        return "anonymised"
    pk = user.pk
    _drop_credentials(user)
    # Audyt **przed** skasowaniem wiersza: po ``delete()`` nie ma z czego wziąć ``target_type``,
    # a ``actor`` i tak zgaśnie na ``SET_NULL``. W ``diff`` jest wyłącznie identyfikator – żadnej
    # danej osobowej, bo wpis zostaje w bazie na stałe.
    audit(None, "account.deleted", user, {"user_id": pk}, request=request)
    user.delete()
    return "deleted"


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
