"""Zgoda ucznia na opiekuna szkolnego, który dopisał się do niego **importem listy**.

Skąd ten moduł (v0.38.7, decyzja właściciela platformy z 2.10.2026: „opiekun szkolny jest
dowiązywany do istniejącego ucznia wyłącznie za jego zgodą”). Reguła z ``apps.accounts.supervisors``
– wgląd w przebieg zawodów ucznia nadaje **uczeń**, wpisując adres nauczyciela w swoim profilu –
miała jeden wyjątek, którego nikt nie nazwał po imieniu: import listy uczniów. Adres zajęty przez
konto uczestnika był w nim „dowiązywany”, czyli ``supervisor_email`` ucznia nadpisywał się adresem
osoby, która wgrała plik. Rejestracja opiekunów jest otwarta, więc każdy, kto założył sobie konto
opiekuna, mógł jednym wierszem CSV dopisać się do **dowolnego** ucznia znanego z adresu e-mail,
zobaczyć w ``/supervisor/`` jego imię i nazwisko, kod publiczny, szkołę, klasę i przebieg etapów –
i po cichu wyrzucić z tej listy prawdziwego nauczyciela. Większość uczestników to osoby
niepełnoletnie.

Teraz import istniejącego konta niczego w profilu nie zapisuje. Zamiast tego uczeń dostaje list:
„nauczyciel <imię i nazwisko, adres> prosi o dopisanie jako opiekun szkolny w <konkurs>” z linkiem
do strony, na której – **zalogowany jako ten uczestnik** – widzi, kto prosi i co ta osoba zobaczy,
i wybiera jednym z dwóch przycisków POST: „Zgadzam się” albo „Nie zgadzam się”. Dopiero zgoda woła
``set_supervisor_email``, czyli tę samą funkcję, którą dotąd wołał import.

Token jest **bezstanowy** (``django.core.signing``, sól osobna od aktywacji, zaproszenia i zgody
opiekuna prawnego – to piąte, inne uprawnienie) i wiąże trzy rzeczy: profil uczestnika, adres
opiekuna, który prosi, oraz adres opiekuna, który uczeń ma **w chwili wysłania prośby**. Trzeci
składnik jest tym, co czyni link jednorazowym bez jednego wiersza w bazie: po zgodzie (albo po tym,
jak uczeń sam zmienił adres w profilu) bieżący adres jest inny niż podpisany i link przestaje
działać. Prośba sprzed tygodnia nie może więc po cichu odwrócić decyzji, którą uczeń podjął
w międzyczasie. Ważność – czternaście dni, jak zaproszenie i zgoda opiekuna prawnego: list
przychodzi znienacka i bywa czytany raz w tygodniu.

Wolumen listów pilnują dwa progi: jedna prośba na parę (uczestnik, opiekun) na dobę – licznik
w cache'u pod kluczem ze skrótu SHA-256, jak w ``apps.web.throttle``, więc w Redisie nie leżą
adresy – oraz limit żądań na widokach importu opiekuna (``SupervisorImportView``).

Czego moduł **nie** robi: nie rusza dowiązań zapisanych przed v0.38.7 (bez migracji danych – jak je
wylistować, mówi ``docs/SECURITY_CHECKLIST.md``) i nie dotyczy kont **zakładanych** importem:
tam zgodą jest przyjęcie zaproszenia (``apps.accounts.bulk_registration``) – konto bez niego
w ogóle nie działa, a uczeń, który je uruchomił, może adres nauczyciela wyczyścić w profilu.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy import branding

from .activation import absolute_url, queue_mail
from .models import Participant, User
from .preferences import language_for
from .supervisors import normalize_supervisor_email, set_supervisor_email

#: Sól podpisu. Osobna od aktywacji, zmiany adresu, zaproszenia i zgody opiekuna prawnego: token
#: jednego z tych uprawnień nie może zadziałać w miejscu drugiego.
SUPERVISOR_CONSENT_SALT = "apps.accounts.supervisor-consent"

#: Ważność linku – czternaście dni (uzasadnienie w docstringu modułu).
SUPERVISOR_CONSENT_MAX_AGE = 14 * 24 * 3600
SUPERVISOR_CONSENT_DAYS = SUPERVISOR_CONSENT_MAX_AGE // (24 * 3600)

#: Najwyżej jedna prośba na parę (uczestnik, opiekun) na dobę. Doba, a nie czternaście dni ważności
#: linku: nauczyciel, który poprawił plik i wgrał go drugi raz tego samego popołudnia, nie ma
#: wysłać uczniowi drugiego listu, ale ten, który po tygodniu milczenia przypomina się w nowym
#: imporcie, ma do tego prawo – uczeń mógł pierwszy list po prostu przeoczyć.
REQUEST_COOLDOWN_SECONDS = 24 * 3600

#: Prefiks kluczy w cache'u – własny, żeby nie kolidować z ``apps.web.throttle``.
CACHE_PREFIX = "supervisor-consent"

#: Kto wywołał prośbę. Wartość jedzie w tokenie i w audycie (bez danych osobowych), a strona
#: zgody mówi na jej podstawie, skąd prośba przyszła: od samego nauczyciela czy z importu
#: organizatora, który wskazał nauczyciela kolumną pliku.
VIA_SUPERVISOR = "supervisor"
VIA_COORDINATOR = "coordinator"
VIA_CHOICES = (VIA_SUPERVISOR, VIA_COORDINATOR)

#: Temat listu. Leniwy, bo moduł ładuje się przy starcie procesu; ``queue_mail`` sprowadza go do
#: napisu tuż przed kolejkowaniem. Wariant z nazwą konkursu wybiera ``apps.tenancy.branding``.
SUPERVISOR_CONSENT_SUBJECT = gettext_lazy("Prośba o zgodę na opiekuna szkolnego – Olimpiada Kwantowa")
SUPERVISOR_CONSENT_SUBJECT_TEMPLATE = gettext_lazy("Prośba o zgodę na opiekuna szkolnego – %(competition)s")

#: Treść listu w dwóch postaciach (tekst i alternatywa HTML bez zasobów zdalnych) – szablony, bo
#: list czyta uczeń i to jest tekst interfejsu, a nie komunikat serwisu.
BODY_TEMPLATE = "registration/supervisor_consent_body.txt"
BODY_HTML_TEMPLATE = "registration/supervisor_consent_body.html"

INVALID_TOKEN_MESSAGE = gettext_lazy(
    "Link z prośbą o zgodę jest nieprawidłowy, wygasł albo jest już nieaktualny."
)
NOT_OWNER_MESSAGE = gettext_lazy(
    "Ta prośba jest adresowana do innego konta. Zaloguj się na konto, na które przyszedł list."
)


@dataclass(frozen=True)
class ConsentRequest:
    """Rozpakowana prośba: kogo dotyczy, kto prosi i co uczeń ma w profilu teraz."""

    participant: Participant
    supervisor_email: str
    current_email: str
    via: str

    @property
    def replaces_current(self) -> bool:
        """Czy zgoda **zastąpi** obecnego opiekuna – strona musi to powiedzieć wprost."""
        return bool(self.current_email)


def _invalid_token() -> DomainError:
    """Jeden komunikat na każdy powód odrzucenia – bez wskazywania, który to był."""
    return DomainError(INVALID_TOKEN_MESSAGE, "SUPERVISOR_CONSENT_INVALID", status.HTTP_400_BAD_REQUEST)


def make_token(participant: Participant, supervisor_email: str, *, via: str) -> str:
    """Token wiązany z trójką (profil, adres proszącego, **bieżący** adres opiekuna w profilu).

    Klucze są jednoliterowe, bo token jedzie w adresie z listu, a każdy znak w nim to znak
    w linku, który klient pocztowy może złamać w pół.
    """
    return signing.dumps(
        {
            "p": participant.pk,
            "s": normalize_supervisor_email(supervisor_email),
            "c": normalize_supervisor_email(participant.supervisor_email),
            "v": via,
        },
        salt=SUPERVISOR_CONSENT_SALT,
    )


def read_token(token: str) -> ConsentRequest:
    """Prośba wskazana tokenem. ``DomainError`` przy podpisie złym, wygasłym albo nieaktualnym.

    Nieaktualny znaczy: uczeń ma dziś w profilu inny adres opiekuna niż w chwili wysłania prośby
    – bo już się zgodził, bo sam wpisał kogoś innego, albo bo usunął opiekuna. W każdym z tych
    przypadków podjął decyzję później niż ta prośba i stary link nie ma prawa jej odwrócić.
    """
    try:
        payload = signing.loads(token, salt=SUPERVISOR_CONSENT_SALT, max_age=SUPERVISOR_CONSENT_MAX_AGE)
    except signing.BadSignature as exc:  # obejmuje ``SignatureExpired``
        raise _invalid_token() from exc
    if not isinstance(payload, dict) or not payload.get("p") or payload.get("v") not in VIA_CHOICES:
        raise _invalid_token()
    requested = normalize_supervisor_email(payload.get("s"))
    if not requested:
        raise _invalid_token()
    participant = Participant.objects.filter(pk=payload["p"]).select_related("user", "competition").first()
    if participant is None:
        raise _invalid_token()
    current = normalize_supervisor_email(participant.supervisor_email)
    if current != normalize_supervisor_email(payload.get("c")) or current == requested:
        raise _invalid_token()
    return ConsentRequest(
        participant=participant, supervisor_email=requested, current_email=current, via=payload["v"]
    )


def ensure_owner(consent: ConsentRequest, user) -> None:
    """Zgodę składa **wyłącznie** właściciel profilu, zalogowany na swoje konto.

    Link z listu nie jest uprawnieniem sam w sobie (inaczej niż przy zgodzie opiekuna prawnego,
    który konta nie ma): uczeń konto ma, a list bywa przekazany dalej albo przeczytany przez kogoś,
    kto ma dostęp do skrzynki, ale nie do konta. Ta sama odmowa obowiązuje opiekuna, który prosi –
    gdyby link otwierał zgodę bez logowania, nauczyciel z kopią listu zgodziłby się sam za ucznia.
    """
    if not getattr(user, "is_authenticated", False) or consent.participant.user_id != user.pk:
        raise DomainError(NOT_OWNER_MESSAGE, "SUPERVISOR_CONSENT_NOT_OWNER", status.HTTP_403_FORBIDDEN)


def requester(email: str) -> dict:
    """Kim jest proszący: imię i nazwisko z **konta opiekuna** (o ile je ma), adres i szkoła.

    Imię i nazwisko bierzemy z konta, a nie z pliku: konto przeszło aktywację adresu, więc nazwisko
    przy tym adresie podał właściciel skrzynki. Adres bez konta opiekuna (import organizatora
    wskazał nauczyciela, który jeszcze się nie zarejestrował) zostaje samym adresem – zmyślanie
    nazwiska z czegokolwiek innego byłoby podpisaniem prośby cudzym imieniem.
    """
    normalized = normalize_supervisor_email(email)
    user = (
        User.objects.filter(email=normalized, school_supervisor__isnull=False)
        .select_related("school_supervisor")
        .first()
        if normalized
        else None
    )
    if user is None:
        return {"name": "", "email": normalized, "school": "", "has_account": False}
    return {
        "name": f"{user.first_name} {user.last_name}".strip(),
        "email": normalized,
        "school": user.school_supervisor.school or "",
        "has_account": True,
    }


def _cooldown_key(participant: Participant, email: str) -> str:
    """Klucz licznika „jedna prośba na dobę” – skrót, a nie adres (ta sama zasada, co throttle)."""
    digest = hashlib.sha256(f"{participant.pk}|{email}".encode()).hexdigest()[:32]
    return f"{CACHE_PREFIX}:{digest}"


def _competition_label(competition) -> str:
    return branding.competition_name(competition) if competition is not None else _("Olimpiada Kwantowa")


def request_consent(
    participant: Participant, supervisor_email: str, *, via: str, actor=None, request=None
) -> bool:
    """Wysyła uczniowi prośbę o zgodę na opiekuna. Zwraca, czy list poszedł – **nie do pokazania**.

    ``False`` znaczy jedno z trzech: adres pusty, uczeń ma już tego opiekuna (sam go wpisał – nie
    ma o co pytać) albo prośba do tej pary poszła w ostatniej dobie. Wołający (import) nie mówi
    nauczycielowi, który z tych przypadków zaszedł, ani nawet **czy** list poszedł: rozróżnienie
    byłoby wyrocznią „ten adres jest uczniem tego konkursu” – czyli dokładnie tym, co podgląd
    importu przestał zdradzać.

    Licznik doby jest zakładany **przed** wysyłką (``cache.add`` – atomowe „ustaw, jeśli nie ma”),
    więc dwa równoległe importy tej samej listy nie wyślą dwóch listów. Cena: gdy transakcja
    importu się wycofa, licznik zostaje, a list nie wychodzi – kolejna prośba do tej pary pójdzie
    dopiero następnego dnia. Odwrotny błąd (dwa listy zamiast jednego) byłby gorszy.
    """
    requested = normalize_supervisor_email(supervisor_email)
    if not requested or requested == normalize_supervisor_email(participant.supervisor_email):
        return False
    if not cache.add(_cooldown_key(participant, requested), 1, REQUEST_COOLDOWN_SECONDS):
        return False
    competition = participant.competition
    link = absolute_url(
        reverse("web:supervisor-consent", args=[make_token(participant, requested, via=via)]),
        request,
        competition,
    )
    who = requester(requested)
    # Prośbę składa nauczyciel albo koordynator importem – w **swoim** żądaniu. List czyta uczeń,
    # więc temat, treść i podpis powstają w języku ucznia (``language_for``).
    with language_for(participant.user, competition):
        context = {
            "first_name": participant.user.first_name,
            "supervisor_name": who["name"],
            "supervisor_email": who["email"],
            "supervisor_school": who["school"],
            "competition_name": _competition_label(competition),
            "via_coordinator": via == VIA_COORDINATOR,
            "link": link,
            "days": SUPERVISOR_CONSENT_DAYS,
            "signature": branding.signature(competition, fallback=_("Olimpiada Kwantowa")),
        }
        subject = str(
            branding.subject(SUPERVISOR_CONSENT_SUBJECT_TEMPLATE, SUPERVISOR_CONSENT_SUBJECT, competition)
        )
        body = render_to_string(BODY_TEMPLATE, context)
        html_body = render_to_string(BODY_HTML_TEMPLATE, context)
    queue_mail(subject, body, participant.user.email, competition=competition, html_message=html_body)
    # W ``diff`` nie ma adresu nauczyciela: adres opiekuna jest daną osobową osoby trzeciej, a audyt
    # czytają też osoby bez prawa do niej (ta sama zasada, co w ``set_supervisor_email``). Kto prosił,
    # mówi ``actor`` – konto, które wgrało plik.
    audit(actor, "participant.supervisor_consent_requested", participant, {"via": via}, request=request)
    return True


@transaction.atomic
def accept(consent: ConsentRequest, *, user, request=None) -> str:
    """Zgoda ucznia: adres proszącego trafia do profilu tą samą funkcją, co wpis w formularzu.

    Profil czytamy ponownie **pod blokadą** i porównujemy z podpisem jeszcze raz: między
    otwarciem strony a kliknięciem uczeń mógł w drugiej karcie zmienić opiekuna, a zgoda wydana na
    stan, którego już nie ma, nie jest zgodą.
    """
    ensure_owner(consent, user)
    participant = Participant.objects.select_for_update().get(pk=consent.participant.pk)
    if normalize_supervisor_email(participant.supervisor_email) != consent.current_email:
        raise _invalid_token()
    set_supervisor_email(participant, consent.supervisor_email, actor=user, request=request)
    # Osobny wpis obok ``participant.supervisor_email_set``: tamten mówi „adres się zmienił”, ten –
    # „zmienił się, bo uczeń zgodził się na prośbę z importu”. Bez adresów, jak wszędzie w audycie.
    audit(
        user,
        "participant.supervisor_consented",
        participant,
        {"via": consent.via, "replaced": consent.replaces_current},
        request=request,
    )
    return consent.supervisor_email


def refuse(consent: ConsentRequest, *, user, request=None) -> None:
    """Odmowa: profil zostaje bez zmian, a w audycie zostaje sam fakt odmowy.

    Wpis jest potrzebny, choć niczego nie zmienia: nauczyciel, który dzwoni do organizatora
    z pytaniem „dlaczego uczeń nie pojawia się na mojej liście”, ma dostać odpowiedź z rejestru,
    a nie z domysłów. Nauczyciel sam o odmowie się nie dowiaduje – lista po prostu się nie zmienia,
    tak samo jak wtedy, gdy uczeń listu nie przeczytał.
    """
    ensure_owner(consent, user)
    audit(
        user,
        "participant.supervisor_consent_refused",
        consent.participant,
        {"via": consent.via},
        request=request,
    )
