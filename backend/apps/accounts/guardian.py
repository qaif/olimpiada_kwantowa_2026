"""Zgoda opiekuna zbierana **online**, zamiast skanem podpisanego formularza.

Skąd ta zmiana. Dotąd zgoda rodzica albo opiekuna prawnego istniała w dwóch postaciach naraz
i żadna nie była dobra: w rejestracji uczestnik zaznaczał oświadczenie „mój opiekun się zgodził”
(``ConsentKind.GUARDIAN``), a obok wisiał wzór do wydrukowania i podpisania
(``apps/cms/fixtures/legacy/zgoda-opiekuna.md``). Pierwsza postać jest oświadczeniem dziecka
o cudzej woli – dowodem nie jest. Druga wymaga drukarki, skanera i drugiego kanału przesyłania
dokumentów z danymi osobowymi. Dlatego dochodzi trzecia, i to ona ma być normalną drogą:

1. uczestnik podaje adres e-mail opiekuna (``Participant.guardian_email``),
2. system wysyła **na ten adres** link podpisany ``django.core.signing`` – ważny czternaście dni,
3. opiekun otwiera stronę, czyta treść zgody w obowiązującej wersji, widzi imię dziecka i szkołę
   (tyle, żeby wiedzieć, czego dotyczy, i nie więcej), zaznacza pole i potwierdza,
4. powstaje ``ConsentRecord`` rodzaju ``GUARDIAN`` z adresem potwierdzającego, adresem IP
   i znacznikiem czasu; projekcja ``Participant.guardian_consent`` idzie na ``True``,
5. uczestnik dostaje list, że zgoda wpłynęła.

Dlaczego token, a nie konto dla opiekuna: opiekun ma w tym systemie dokładnie jedną sprawę
i zakładanie mu konta (z hasłem, aktywacją i prawem do usunięcia danych) byłoby zebraniem
większego zbioru danych niż ten, po który przyszedł. Podpisany link jest jednorazowym
uprawnieniem do jednej czynności i nie zostawia po sobie ani jednego wiersza więcej.

Czternaście dni, a nie cztery godziny jak przy aktywacji konta: po drugiej stronie jest dorosły,
który czyta pocztę raz na kilka dni, a wygaśnięcie linku nie zwalnia tu żadnego zasobu (konto
uczestnika istnieje niezależnie). Link wolno wysłać ponownie w każdej chwili – nowy unieważnia
stary tylko wtedy, gdy zmienił się adres opiekuna, bo adres jest częścią podpisanej treści.

Token **nie** ma stanu w bazie i nie jest jednorazowy w sensie technicznym. Jednorazowość bierze
się z tego, że druga wizyta nie ma czego zmienić: ``confirm`` przy istniejącej aktywnej zgodzie
zwraca ją zamiast dopisywać kolejny wiersz.
"""

from __future__ import annotations

from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit, client_ip
from apps.tenancy import branding

from .activation import absolute_url, queue_mail, signature_lines
from .consents import BY_KIND, ConsentKind, ConsentSource, is_minor, organizer_name, plain_text
from .models import ConsentRecord, Participant

#: Sól podpisu. Osobna od aktywacji konta i od zmiany adresu: to trzecie, zupełnie inne
#: uprawnienie i token jednego z nich nie może zadziałać w miejscu drugiego.
GUARDIAN_SALT = "apps.accounts.guardian-consent"

#: Ważność linku w sekundach – czternaście dni (uzasadnienie w docstringu modułu).
GUARDIAN_MAX_AGE = 14 * 24 * 3600
GUARDIAN_DAYS = GUARDIAN_MAX_AGE // (24 * 3600)

#: Tematy listów. Leniwe, bo moduł ładuje się przy starcie procesu; ``queue_mail`` sprowadza je
#: do napisu tuż przed kolejkowaniem zadania.
GUARDIAN_SUBJECT = gettext_lazy("Prośba o zgodę opiekuna – Olimpiada Kwantowa")
GUARDIAN_CONFIRMED_SUBJECT = gettext_lazy("Zgoda opiekuna została potwierdzona – Olimpiada Kwantowa")

#: Te same tematy jako wzorce z nazwą konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1). Stałe
#: wyżej zostają odwrotem; wybiera między nimi ``apps.tenancy.branding.subject``.
GUARDIAN_SUBJECT_TEMPLATE = gettext_lazy("Prośba o zgodę opiekuna – %(competition)s")
GUARDIAN_CONFIRMED_SUBJECT_TEMPLATE = gettext_lazy("Zgoda opiekuna została potwierdzona – %(competition)s")

INVALID_TOKEN_MESSAGE = gettext_lazy("Link do formularza zgody jest nieprawidłowy albo wygasł.")

#: Stany pokazywane w panelu uczestnika i na ekranie koordynatora. Wartości są kluczami, etykiety
#: składa szablon – ten moduł nie zna języka interfejsu.
STATUS_NOT_REQUIRED = "not_required"
STATUS_MISSING = "missing"
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"


def _invalid_token() -> DomainError:
    """Jeden komunikat na każdy powód odrzucenia – bez wskazywania, który to był."""
    return DomainError(INVALID_TOKEN_MESSAGE, "GUARDIAN_TOKEN_INVALID", status.HTTP_400_BAD_REQUEST)


def requires_guardian_consent(participant: Participant) -> bool:
    """Czy od tego uczestnika wymagamy zgody opiekuna. Reguła jest jedna: ``consents.is_minor``."""
    return is_minor(participant.birth_year)


def confirmed_record(participant: Participant) -> ConsentRecord | None:
    """Najświeższa **aktywna** zgoda opiekuna złożona online albo ``None``.

    Interesują nas wyłącznie wpisy z adresem potwierdzającego: wpis bez ``given_by_email``
    pochodzi ze starego oświadczenia uczestnika („mój opiekun się zgodził”) i nie jest dowodem
    woli opiekuna, więc nie może zamykać sprawy zamiast niego.
    """
    return (
        ConsentRecord.objects.filter(
            participant=participant, kind=ConsentKind.GUARDIAN, withdrawn_at__isnull=True
        )
        .exclude(given_by_email="")
        .first()
    )


def guardian_status(participant: Participant) -> dict:
    """Stan zgody opiekuna: ``{"state": …, "email": …, "record": …}``.

    Trzy stany, dokładnie te, o które pyta uczestnik: nie ma jeszcze adresu opiekuna (``missing``),
    prośba poszła i czekamy (``pending``), zgoda wpłynęła (``confirmed``). Pełnoletni dostaje
    ``not_required`` i nie widzi tej sekcji w ogóle – pytanie o zgodę opiekuna osoby dorosłej
    byłoby po prostu nieprawdziwe.
    """
    if not requires_guardian_consent(participant):
        return {"state": STATUS_NOT_REQUIRED, "email": "", "record": None}
    record = confirmed_record(participant)
    if record is not None:
        return {"state": STATUS_CONFIRMED, "email": record.given_by_email, "record": record}
    email = (participant.guardian_email or "").strip()
    if email:
        return {"state": STATUS_PENDING, "email": email, "record": None}
    return {"state": STATUS_MISSING, "email": "", "record": None}


def make_token(participant: Participant) -> str:
    """Token wiązany z parą (uczestnik, adres opiekuna).

    Adres jest w podpisanej treści, więc link wystawiony na poprzedni adres przestaje działać,
    gdy uczestnik poda inny – a to jest jedyna droga „odwołania” wysłanej prośby.
    """
    return signing.dumps(
        {"pk": participant.pk, "email": (participant.guardian_email or "").strip().lower()},
        salt=GUARDIAN_SALT,
    )


def read_token(token: str) -> Participant:
    """Uczestnik wskazany tokenem. ``DomainError`` przy podpisie złym, wygasłym albo nieaktualnym."""
    try:
        payload = signing.loads(token, salt=GUARDIAN_SALT, max_age=GUARDIAN_MAX_AGE)
    except signing.BadSignature as exc:  # obejmuje ``SignatureExpired``
        raise _invalid_token() from exc
    if not isinstance(payload, dict) or not payload.get("pk"):
        raise _invalid_token()
    # ``competition`` jest w ``select_related``, bo potwierdzenie zgody kończy się listem, a ten
    # czyta z konkursu markę i nadawcę – bez tego doszłoby osobne zapytanie na każdą wizytę.
    participant = (
        Participant.objects.filter(pk=payload["pk"])
        .select_related("user", "school_ref", "competition")
        .first()
    )
    if participant is None:
        raise _invalid_token()
    current = (participant.guardian_email or "").strip().lower()
    if not current or current != (payload.get("email") or "").strip().lower():
        # Adres opiekuna zmieniono po wysłaniu tego listu – stary link ma przestać działać.
        raise _invalid_token()
    return participant


def consent_text() -> str:
    """Treść oświadczenia opiekuna – ta sama, co przy rejestracji, bez znaczników HTML.

    Bierzemy ją z ``apps.accounts.consents``, a nie przepisujemy do szablonu: gdyby brzmienie
    istniało w dwóch miejscach, jedno z nich prędzej czy później byłoby nieaktualne, a wersja
    zapisana w dowodzie odsyłałaby do tekstu, którego nikt nie widział.
    """
    return plain_text(BY_KIND[ConsentKind.GUARDIAN], organizer=organizer_name())


def consent_version() -> str:
    """Wersja dokumentu zgody opiekuna obowiązująca teraz – trafia do ``ConsentRecord``."""
    return BY_KIND[ConsentKind.GUARDIAN].version


def request_message(link: str, first_name: str, school: str, competition=None) -> str:
    """List do opiekuna. Imię i szkoła są w treści, bo bez nich prośba jest nie do zweryfikowania.

    Nazwiska nie ma świadomie: do rozpoznania własnego dziecka wystarczy imię i nazwa szkoły,
    a list idzie na adres podany przez ucznia i może trafić pod zły adres przez literówkę.
    """
    return "\n".join(
        [
            _(
                "Uczennica lub uczeń podał ten adres jako kontakt do rodzica albo opiekuna "
                "prawnego w zgłoszeniu do Olimpiady Kwantowej."
            ),
            "",
            _("Zgłoszenie dotyczy: %(name)s (%(school)s).") % {"name": first_name, "school": school},
            "",
            _(
                "Udział osoby niepełnoletniej wymaga zgody opiekuna. Treść zgody i formularz "
                "potwierdzenia znajdują się pod poniższym adresem:"
            ),
            "",
            link,
            "",
            _("Link jest ważny %(days)s dni.") % {"days": GUARDIAN_DAYS},
            "",
            _(
                "Jeśli nie jesteś opiekunem tej osoby albo nie wyrażasz zgody – zignoruj tę "
                "wiadomość. Bez potwierdzenia zgoda nie zostanie zapisana."
            ),
            "",
            *signature_lines(competition),
        ]
    )


def confirmed_message(guardian_email: str, competition=None) -> str:
    """List do uczestnika po potwierdzeniu. Adres opiekuna jest jego własną daną – wolno go podać."""
    return "\n".join(
        [
            _(
                "Zgoda rodzica lub opiekuna prawnego na Twój udział w Olimpiadzie Kwantowej "
                "została potwierdzona z adresu %(email)s."
            )
            % {"email": guardian_email},
            "",
            _("Stan zgód widzisz po zalogowaniu w panelu uczestnika."),
            "",
            *signature_lines(competition),
        ]
    )


@transaction.atomic
def request_consent(participant: Participant, email: str, *, actor=None, request=None) -> str:
    """Zapisuje adres opiekuna i wysyła na niego prośbę o zgodę. Zwraca ten adres.

    Pusty adres jest odmową, a nie cichym „nic nie rób”: formularz, który przyjmuje pustkę
    i udaje wysyłkę, zostawia uczestnika w przekonaniu, że sprawa jest załatwiona.

    Adres **zapisujemy**, bo z niego wyrasta token i stan „oczekuje” w panelu. Zgody dorosłego
    nie ma po co zbierać – wołający (widok) pyta o to ``requires_guardian_consent`` i nie pokazuje
    formularza, a serwis powtarza regułę, bo POST da się wysłać bez formularza.
    """
    normalized = (email or "").strip().lower()
    if not normalized:
        raise DomainError(
            "Podaj adres e-mail rodzica lub opiekuna prawnego.",
            "GUARDIAN_EMAIL_REQUIRED",
            status.HTTP_400_BAD_REQUEST,
        )
    if not requires_guardian_consent(participant):
        raise DomainError(
            "Zgoda opiekuna nie jest wymagana dla osoby pełnoletniej.",
            "GUARDIAN_NOT_REQUIRED",
            status.HTTP_409_CONFLICT,
        )
    if normalized == (participant.user.email or "").strip().lower():
        # Uczestnik nie może być własnym opiekunem. Bez tej reguły cała ścieżka sprowadzałaby się
        # do kliknięcia we własny link – czyli do tego samego oświadczenia o cudzej woli, od
        # którego ta zmiana odchodzi.
        raise DomainError(
            "Adres opiekuna musi być inny niż Twój własny adres konta.",
            "GUARDIAN_EMAIL_IS_OWN",
            status.HTTP_400_BAD_REQUEST,
        )
    participant.guardian_email = normalized
    participant.save(update_fields=["guardian_email"])
    # Konkurs bierze się z **uczestnika**, a nie z kontekstu żądania: zgoda dotyczy udziału w tym
    # konkursie, w którym uczestnik jest zapisany, i to jego markę ma nieść list do opiekuna.
    competition = participant.competition
    link = absolute_url(reverse("web:guardian-consent", args=[make_token(participant)]), request, competition)
    queue_mail(
        branding.subject(GUARDIAN_SUBJECT_TEMPLATE, GUARDIAN_SUBJECT, competition),
        request_message(
            link,
            participant.user.first_name or "uczestnik/uczestniczka",
            participant.school,
            competition,
        ),
        normalized,
        competition=competition,
    )
    # W ``diff`` nie ma adresu opiekuna: audyt czytają osoby, które nie muszą znać danych
    # kontaktowych rodziny uczestnika. Sam fakt wysyłki wystarczy, żeby wytłumaczyć późniejszy wpis
    # ``participant.guardian_consent_confirmed``.
    audit(
        actor or participant.user,
        "participant.guardian_consent_requested",
        participant,
        {},
        request=request,
    )
    return normalized


@transaction.atomic
def confirm_consent(participant: Participant, *, request=None) -> ConsentRecord:
    """Zapisuje zgodę opiekuna: dowód, projekcja na profilu i list do uczestnika.

    Idempotentne: druga wizyta pod tym samym linkiem zwraca istniejący wpis zamiast dokładać
    kolejny. Rejestr zgód jest rejestrem zdarzeń, ale „kliknąłem dwa razy” zdarzeniem nie jest.

    ``ConsentSource.WEB``, bo zgoda wpłynęła zwykłym formularzem WWW – ``PANEL`` znaczy w tym
    systemie „uczestnik zmienił coś u siebie po zalogowaniu”, a tu nikt nie jest zalogowany.
    """
    existing = confirmed_record(participant)
    if existing is not None:
        return existing
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentKind.GUARDIAN,
        document_version=consent_version(),
        given_at=timezone.now(),
        source=ConsentSource.WEB,
        given_by_email=(participant.guardian_email or "").strip().lower(),
        ip_address=client_ip(request),
    )
    if not participant.guardian_consent:
        participant.guardian_consent = True
        participant.save(update_fields=["guardian_consent"])
    # Aktorem jest ``None`` – potwierdzenie składa osoba spoza systemu i podpisanie go kontem
    # uczestnika byłoby nieprawdą w rejestrze, do którego sięga się właśnie po to pytanie.
    audit(
        None,
        "participant.guardian_consent_confirmed",
        participant,
        {"version": record.document_version},
        request=request,
    )
    competition = participant.competition
    queue_mail(
        branding.subject(GUARDIAN_CONFIRMED_SUBJECT_TEMPLATE, GUARDIAN_CONFIRMED_SUBJECT, competition),
        confirmed_message(record.given_by_email, competition),
        participant.user.email,
        competition=competition,
    )
    return record
