"""Aktywacja konta linkiem z listu oraz potwierdzanie zmiany adresu e-mail.

Dlaczego aktywacja w ogóle (do tej zmiany konta były aktywne od razu, patrz
``docs/BACKLOG.md``): adres e-mail jest u nas loginem **i** jedyną drogą odzyskania konta.
Rejestracja bez potwierdzenia adresu znaczyła, że:

- literówka w adresie daje konto, do którego nie da się wrócić po zapomnianym haśle,
- każdy może założyć konto na cudzy adres i czekać, aż właściciel spróbuje się zarejestrować
  („EMAIL_TAKEN” na własnym adresie) albo aż resetem hasła przejmie je napastnik,
- lista uczestników puchnie od kont z adresami, pod które nie dojdzie ani jedno ogłoszenie
  organizatora.

Czego ten moduł **nie** robi: nie korzysta z weryfikacji adresu wbudowanej w allauth
(``ACCOUNT_EMAIL_VERIFICATION``). Widoki allauth nie są zamontowane (patrz
``apps.web.social_urls``), a włączenie jego przepływu dołożyłoby drugi zestaw szablonów listów,
drugi limit prób i drugą ścieżkę logowania – wszystko poza ``apps.web.throttle``.

Token
-----
``django.core.signing`` (``dumps``/``loads`` stoją na ``TimestampSigner``), sól osobna dla każdego
przeznaczenia. W tokenie jest **pk użytkownika i adres e-mail**, i to wiązanie jest istotne:
token wystawiony na stary adres przestaje pasować, gdy adres się zmieni, więc link z listu
sprzed zmiany nie potwierdza adresu, którego już nie ma. Podpisu nie da się podrobić bez
``SECRET_KEY``, a stanu w bazie token nie potrzebuje – jednorazowość bierze się z tego, że
aktywacja sprawdza ``email_verified_at is None`` (drugie kliknięcie nie ma czego zmienić).

``ACTIVATION_MAX_AGE`` to **doba**: tyle samo żyje nieaktywowane konto. Po tym czasie
``apps.accounts.tasks.purge_unactivated_accounts`` kasuje je z bazy, więc link ważny dłużej
wskazywałby na konto, którego już nie ma. Okno było czterogodzinne (decyzja organizatora z
września 2026: zwolnić adres e-mail do ponownej rejestracji zamiast blokować go kontem-widmem);
22.09.2026 organizator wydłużył je do 24 godzin – cztery godziny mijały, zanim uczeń zdążył
zajrzeć do skrzynki po lekcjach, a ponowna rejestracja od zera zniechęcała.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit
from apps.tenancy import branding

from .models import User

logger = logging.getLogger(__name__)

#: Sole podpisu. Osobne dla każdego przeznaczenia, żeby token aktywacyjny nie dał się użyć jako
#: potwierdzenie zmiany adresu (i odwrotnie) – to dwa różne uprawnienia.
ACTIVATION_SALT = "apps.accounts.activation"
EMAIL_CHANGE_SALT = "apps.accounts.email-change"

#: Ważność linku aktywacyjnego w sekundach – **doba** (decyzja organizatora z 22.09.2026; wcześniej
#: cztery godziny). Ta sama wartość wyznacza życie nieaktywowanego konta
#: (``purge_unactivated_accounts``): gdyby link był ważny dłużej niż konto, kliknięcie kończyłoby
#: się „nieprawidłowy link” bez wyjaśnienia.
ACTIVATION_MAX_AGE = 24 * 3600

#: Ważność linku potwierdzającego zmianę adresu. Dłużej niż aktywacja, bo tu nic nie jest kasowane:
#: do kliknięcia obowiązuje stary adres i konto działa normalnie.
EMAIL_CHANGE_MAX_AGE = 24 * 3600

#: Ile godzin pokazujemy człowiekowi. Jedno miejsce, żeby list, komunikat i README nie rozjechały się
#: z ustawieniem, na które patrzy kod.
ACTIVATION_HOURS = ACTIVATION_MAX_AGE // 3600

#: Tematy listów do uczestnika. Leniwe, bo moduł ładuje się przy starcie procesu, zanim
#: jakikolwiek język jest aktywny; ``queue_mail`` sprowadza je do napisu tuż przed kolejkowaniem
#: (argumenty zadania Celery jadą przez JSON i obiektu leniwego by nie przeniosły).
ACTIVATION_SUBJECT = gettext_lazy("Aktywuj konto – Olimpiada Kwantowa")
EMAIL_CHANGE_SUBJECT = gettext_lazy("Potwierdź nowy adres e-mail – Olimpiada Kwantowa")
EMAIL_CHANGED_NOTICE_SUBJECT = gettext_lazy("Adres e-mail konta został zmieniony – Olimpiada Kwantowa")

#: Te same tematy jako **wzorce** z nazwą konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1).
#: Stała wyżej zostaje odwrotem i jest napisem dosłownym, a nie wzorcem podstawionym nazwą
#: Konkursu #1 – uzasadnienie stoi w ``apps.tenancy.branding``. Który z dwóch wyjdzie na zewnątrz,
#: rozstrzyga wyłącznie ``branding.subject`` (flaga ``competition_branding_in_mail``).
ACTIVATION_SUBJECT_TEMPLATE = gettext_lazy("Aktywuj konto – %(competition)s")
EMAIL_CHANGE_SUBJECT_TEMPLATE = gettext_lazy("Potwierdź nowy adres e-mail – %(competition)s")
EMAIL_CHANGED_NOTICE_SUBJECT_TEMPLATE = gettext_lazy("Adres e-mail konta został zmieniony – %(competition)s")

#: Komunikat po rejestracji. W jednym miejscu, bo wychodzi z trzech ścieżek (formularz WWW,
#: rejestracja komitetu, dokończenie rejestracji społecznościowej bez potwierdzonego adresu).
ACTIVATION_REQUIRED_MESSAGE = (
    "Konto zostało założone. Sprawdź skrzynkę e-mail (także spam) i kliknij link aktywacyjny – "
    "bez tego logowanie nie zadziała. "
    f"Link jest ważny {ACTIVATION_HOURS} godziny – po tym czasie konto zostanie usunięte "
    "i trzeba zarejestrować się ponownie."
)

#: Odpowiedź formularza „wyślij link ponownie”. **Zawsze ta sama**, niezależnie od tego, czy konto
#: istnieje i czy jest już aktywne – inaczej formularz byłby wyszukiwarką kont w serwisie.
RESEND_MESSAGE = (
    "Jeśli konto z tym adresem istnieje i czeka na aktywację, link został wysłany ponownie. "
    "Sprawdź też folder ze spamem."
)

INVALID_TOKEN_MESSAGE = "Link aktywacyjny jest nieprawidłowy albo wygasł."


def _invalid_token() -> DomainError:
    """Jeden komunikat na każdy powód odrzucenia tokenu – bez wskazywania, który to był."""
    return DomainError(INVALID_TOKEN_MESSAGE, "ACTIVATION_INVALID", status.HTTP_400_BAD_REQUEST)


def make_activation_token(user: User) -> str:
    """Token aktywacyjny wiązany z parą (pk, adres e-mail) konta."""
    return signing.dumps({"pk": user.pk, "email": user.email}, salt=ACTIVATION_SALT)


def make_email_change_token(user: User, new_email: str) -> str:
    """Token potwierdzający zmianę adresu: kto zmienia i **na jaki** adres."""
    return signing.dumps(
        {"pk": user.pk, "email": user.email, "new_email": new_email.strip().lower()},
        salt=EMAIL_CHANGE_SALT,
    )


def read_token(token: str, *, salt: str, max_age: int) -> dict:
    """Rozpakowuje podpisany token albo podnosi ``DomainError`` (wygasły, podrobiony, obcięty)."""
    try:
        payload = signing.loads(token, salt=salt, max_age=max_age)
    except signing.BadSignature as exc:  # obejmuje też ``SignatureExpired``
        raise _invalid_token() from exc
    if not isinstance(payload, dict) or not payload.get("pk"):
        raise _invalid_token()
    return payload


def _site_base_url(site) -> str:
    """Bezwzględny adres witryny – schemat ``https`` niezależnie od ``Site.port``.

    Świadome odstępstwo od ``wagtail.models.Site.root_url``, które wyprowadza schemat z portu.
    Port witryny to ``80`` (wpisuje go migracja ``cms.0002``), bo TLS kończy się na Caddym, a nie
    w Django – link ``http://…`` z listu dostałby więc 301 przy każdym kliknięciu, a w kliencie
    pocztowym część odbiorców zobaczyłaby ostrzeżenie o niezabezpieczonym adresie. Port
    niestandardowy (staging, instalacja deweloperska z jawnym portem) zostaje w adresie, bo bez
    niego adres prowadziłby gdzie indziej.
    """
    host = site.hostname
    if site.port not in (80, 443):
        host = f"{host}:{site.port}"
    return f"https://{host}"


def _base_url_without_request(competition) -> str:
    """Podstawa adresu dla wysyłki **spoza** żądania: konkurs → jego witryna → witryna domyślna.

    To jest naprawa istniejącej dziury, nie nowa funkcja. Do tej zmiany odwrotem było
    ``settings.SITE_URL`` – ustawienie, którego **nie definiuje** ani ``config/settings``, ani
    ``.env.example``, ani compose. Skutek: każdy list wysłany z zadania Celery
    (``remind_overdue_reviews``, ``remind_interviews``, powiadomienia o zamknięciu etapu,
    rozstrzygnięcia reklamacji) niósł adres **względny**, czyli link, którego nie da się kliknąć
    w kliencie pocztowym.

    Kolejność źródeł ma uzasadnienie: konkurs zna swoją domenę także wtedy, gdy witryn jest kilka
    (i to jego domena ma być w liście do jego uczestnika); witryna domyślna jest odpowiedzią
    poprawną dla instalacji jednokonkursowej; ``WAGTAILADMIN_BASE_URL`` – dla bazy, w której
    witryny jeszcze nie ma, i jest **zdefiniowane** (``https://{SITE_DOMAIN}``), więc instalacja
    naprawia się sama, bez nowej zmiennej środowiskowej.
    """
    if competition is not None:
        if competition.primary_domain:
            return f"https://{competition.primary_domain}"
        if competition.site_id:
            return _site_base_url(competition.site)
    try:
        from wagtail.models import Site

        site = Site.objects.filter(is_default_site=True).first()
    except Exception:  # noqa: BLE001 - patrz docstring ``absolute_url``: list ma wyjść mimo wszystko
        logger.warning("Nie udało się odczytać witryny domyślnej do linku w liście.", exc_info=True)
        site = None
    if site is not None:
        return _site_base_url(site)
    return (getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "").rstrip("/")


def absolute_url(path: str, request=None, competition=None) -> str:
    """Bezwzględny adres do listu.

    Kolejność źródeł: żądanie (tak buduje linki reset hasła – protokół bierze się
    z ``request.is_secure()``, więc za Caddy z ``SECURE_PROXY_SSL_HEADER`` wychodzi ``https``),
    a poza żądaniem – konkurs: podany wprost albo wzięty z kontekstu
    (``apps.tenancy.context.current_competition``, ustawianego przez warstwę dla żądania i przez
    ``competition_context`` dla zadania). Szczegóły odwrotów: ``_base_url_without_request``.

    Gdy nie ma **żadnego** źródła, zostaje sama ścieżka – niedoskonały link jest lepszy niż
    wywrócona wysyłka, bo operacja (założenie konta) jest już zapisana.
    """
    if request is not None:
        return request.build_absolute_uri(path)
    from apps.tenancy.context import current_competition

    base = _base_url_without_request(competition or current_competition()).rstrip("/")
    return f"{base}{path}" if base else path


def mail_competition(competition=None):
    """Konkurs, którego listem jest ten list: podany wprost albo wzięty z kontekstu.

    Ta sama kolejność źródeł, co w :func:`absolute_url`, i z tego samego powodu: adres w liście
    i marka w jego temacie mają pochodzić z **jednego** konkursu, bo inaczej uczestnik dostałby
    list podpisany jedną olimpiadą z linkiem pod domenę drugiej. Kontekst ustawia warstwa dla
    żądania (``apps.tenancy.middleware``) i ``competition_context`` dla zadania i komendy.

    ``None`` jest odpowiedzią poprawną, a nie awarią: zadanie okresowe i test jednostkowy, które
    konkursu nie mają skąd wziąć, dostają napisy sprzed etapu 2 i nadawcę instalacji. Zgadywanie
    („weź pierwszy konkurs”) podpisałoby list Olimpiady Kwantowej marką cudzego organizatora.
    """
    if competition is not None:
        return competition
    from apps.tenancy.context import current_competition

    return current_competition()


def signature_lines(competition=None) -> list[str]:
    """Trzy wiersze stopki listu: kreska, podpis i zdanie o skrzynce bez odbioru.

    Podpis idzie przez ``branding.signature``, więc przy wyłączonej fladze wraca dzisiejszy,
    **przetłumaczalny** literał. Funkcja, a nie stała: napis ma się przetłumaczyć w chwili
    składania listu, a moduł ładuje się przy starcie procesu, zanim jakikolwiek język jest aktywny.
    """
    return [
        "--",
        branding.signature(competition, fallback=_("Olimpiada Kwantowa")),
        _("Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać."),
    ]


def activation_message(link: str, competition=None) -> str:
    """Treść listu aktywacyjnego. Poza adresem odbiorcy (i tak w nagłówku ``To:``) zero danych osobowych."""
    return "\n".join(
        [
            _("Ktoś – prawdopodobnie Ty – założył konto w serwisie Olimpiady Kwantowej."),
            "",
            _("Aby aktywować konto i móc się zalogować, otwórz poniższy adres:"),
            "",
            link,
            "",
            _(
                "Link jest ważny %(hours)s godziny. Po tym czasie konto zostanie usunięte "
                "i rejestrację trzeba będzie powtórzyć."
            )
            % {"hours": ACTIVATION_HOURS},
            "",
            _(
                "Jeśli to nie Ty zakładałeś konto – zignoruj tę wiadomość. Bez kliknięcia w link "
                "konto nie zostanie aktywowane i zniknie samo."
            ),
            "",
            *signature_lines(competition),
        ]
    )


def email_change_message(link: str, new_email: str, competition=None) -> str:
    """Treść listu na **nowy** adres: dopiero kliknięcie zmienia adres konta."""
    return "\n".join(
        [
            _("Poproszono o zmianę adresu e-mail konta w serwisie Olimpiady Kwantowej na %(email)s.")
            % {"email": new_email},
            "",
            _("Aby potwierdzić nowy adres, otwórz poniższy adres:"),
            "",
            link,
            "",
            _("Do potwierdzenia obowiązuje dotychczasowy adres – logowanie działa bez zmian."),
            _("Link jest ważny 24 godziny."),
            "",
            _("Jeśli to nie Ty prosiłeś o zmianę – zignoruj tę wiadomość."),
            "",
            *signature_lines(competition),
        ]
    )


def email_changed_notice(new_email: str, competition=None) -> str:
    """Treść listu na **stary** adres: ostrzeżenie, nie potwierdzenie.

    Właściciel skrzynki musi dowiedzieć się o przeniesieniu konta także wtedy, gdy sam o nie nie
    prosił – to jedyny sygnał, jaki mu zostaje, jeśli ktoś dostał się do zalogowanej sesji.
    """
    return "\n".join(
        [
            _(
                "Adres e-mail konta w serwisie Olimpiady Kwantowej został zmieniony na %(email)s. "
                "Logowanie tym adresem przestaje działać."
            )
            % {"email": new_email},
            "",
            _("Jeśli to nie Ty dokonałeś zmiany, natychmiast skontaktuj się z organizatorem."),
            "",
            *signature_lines(competition),
        ]
    )


def queue_mail(
    subject: str, message: str, recipient: str, *, competition=None, headers: dict[str, str] | None = None
) -> None:
    """Kolejkuje list **po commicie** – wzorzec z ``apps.competitions.interviews._send_confirmation``.

    Wysyłka jest skutkiem ubocznym rejestracji, a nie jej warunkiem: niedostępny MTA nie może
    zamienić założonego konta w błąd 500, a worker nie może zacząć czytać konta, które w bazie
    jeszcze nie jest zatwierdzone.

    ``competition`` jest **słowem kluczowym z wartością domyślną** i wyznacza wyłącznie kopertę
    listu: nadawcę (``Competition.from_email`` przez ``apps.core.tasks.mail_from``). Temat i podpis
    składa wołający, bo tylko on wie, które zdanie wysyła. Konkurs niepodany wprost bierze się
    z kontekstu (:func:`mail_competition`), więc wołający sprzed etapu 2 – np.
    ``apps.accounts.bulk_registration`` – dostaje nadawcę swojego konkursu bez żadnej zmiany
    w swoim kodzie.

    ``headers`` – dodatkowe nagłówki listu (``List-Unsubscribe`` powiadomień forum). Brak znaczy
    „jak dotąd”: zadanie dostaje wtedy dokładnie te same argumenty, co przed tą zmianą, więc testy
    i listy, które nagłówków nie potrzebują, nie widzą różnicy.
    """
    if not recipient:
        return

    from apps.core.tasks import mail_from

    # Nadawcę rozstrzygamy **teraz**, a nie w callbacku po commicie: kontekst konkursu jest
    # zmienną kontekstową żądania i do chwili commitu może już być posprzątany, a wtedy list
    # wyszedłby od nadawcy instalacji zamiast od organizatora – po cichu i tylko czasami.
    from_email = mail_from(mail_competition(competition))

    # ``str(...)`` **tuż przed** kolejkowaniem: temat bywa obiektem leniwego tłumaczenia
    # (``gettext_lazy``), a argumenty zadania Celery jadą przez JSON – leniwy obiekt nie
    # przeszedłby przez serializację. Sprowadzenie do napisu tutaj utrwala też język, który
    # obowiązywał w chwili operacji, a nie ten, który akurat będzie aktywny w workerze.
    subject_text = str(subject)
    message_text = str(message)

    def _enqueue() -> None:
        from apps.core.tasks import send_mail_task

        if headers:
            send_mail_task.delay(subject_text, message_text, [recipient], from_email, dict(headers))
        else:
            send_mail_task.delay(subject_text, message_text, [recipient], from_email)

    transaction.on_commit(_enqueue)


def send_activation_email(user: User, *, request=None, competition=None) -> None:
    """Kolejkuje list z linkiem aktywacyjnym dla konta."""
    competition = mail_competition(competition)
    link = absolute_url(reverse("web:activate", args=[make_activation_token(user)]), request, competition)
    queue_mail(
        branding.subject(ACTIVATION_SUBJECT_TEMPLATE, ACTIVATION_SUBJECT, competition),
        activation_message(link, competition),
        user.email,
        competition=competition,
    )


def send_email_change_confirmation(user: User, new_email: str, *, request=None, competition=None) -> None:
    """List potwierdzający na nowy adres. Stary adres dostaje osobne powiadomienie po zmianie."""
    competition = mail_competition(competition)
    token = make_email_change_token(user, new_email)
    link = absolute_url(reverse("web:email-change-confirm", args=[token]), request, competition)
    queue_mail(
        branding.subject(EMAIL_CHANGE_SUBJECT_TEMPLATE, EMAIL_CHANGE_SUBJECT, competition),
        email_change_message(link, new_email, competition),
        new_email,
        competition=competition,
    )


def send_email_changed_notice(old_email: str, new_email: str, *, competition=None) -> None:
    competition = mail_competition(competition)
    queue_mail(
        branding.subject(EMAIL_CHANGED_NOTICE_SUBJECT_TEMPLATE, EMAIL_CHANGED_NOTICE_SUBJECT, competition),
        email_changed_notice(new_email, competition),
        old_email,
        competition=competition,
    )


def is_pending_activation(user: User) -> bool:
    """Konto założone, ale jeszcze niepotwierdzone adresem e-mail."""
    return user.email_verified_at is None and not user.is_active


@transaction.atomic
def mark_activated(user: User, *, actor=None, action: str = "account.activated", request=None) -> User:
    """Ustawia ``is_active`` i ``email_verified_at``; zostawia jeden wpis audytowy.

    Jedna funkcja dla obu dróg (link z listu i ręczna aktywacja przez koordynatora) – różni je
    wyłącznie nazwa akcji w audycie i wykonawca. Gdyby każda z nich ustawiała pola sama, jedna
    prędzej czy później zapomniałaby o ``email_verified_at`` i konto byłoby aktywne, a mimo to
    wciąż „oczekujące na aktywację” na liście koordynatora.
    """
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.email_verified_at is not None:
        # Drugie kliknięcie w ten sam link nie jest błędem – konto już jest aktywne.
        return locked
    locked.email_verified_at = timezone.now()
    locked.is_active = True
    locked.save(update_fields=["email_verified_at", "is_active"])
    _record_allauth_verification(locked)
    audit(actor, action, locked, {"email_verified": True}, request=request)
    return locked


def _record_allauth_verification(user: User) -> None:
    """Przepisuje potwierdzenie adresu do tabeli allauth (``EmailAddress.verified``).

    Nie jest to duplikat ``email_verified_at``, tylko zamknięcie dziury opisanej w
    ``docs/SECURITY_CHECKLIST.md`` § 3.2.8: przy łączeniu konta z Google po zweryfikowanym adresie
    allauth **czyści hasło** konta, którego adresu nie zna jako potwierdzonego (``wipe_password``).
    Zachowanie było słuszne, dopóki rejestracja hasłem nie weryfikowała adresu – wtedy konto mogło
    należeć do kogoś, kto wpisał cudzy adres. Od wprowadzenia aktywacji adres jest potwierdzony
    dostępem do skrzynki, więc allauth musi o tym wiedzieć; inaczej uczestnik po pierwszym
    logowaniu Google'em traciłby własne, prawidłowo ustawione hasło.

    Własnego przepływu weryfikacji allauth nadal nie używamy (``ACCOUNT_EMAIL_VERIFICATION="none"``,
    jego widoki nie są zamontowane) – dokładamy wyłącznie **fakt**, do którego on zagląda.
    """
    from allauth.account.models import EmailAddress

    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )


def activate_with_token(token: str, *, request=None) -> User:
    """Aktywuje konto wskazane tokenem. ``DomainError`` przy tokenie złym, wygasłym albo zużytym.

    Adres z tokenu musi zgadzać się z adresem konta: inaczej link wystawiony przed zmianą adresu
    potwierdzałby adres, którego konto już nie używa.
    """
    payload = read_token(token, salt=ACTIVATION_SALT, max_age=ACTIVATION_MAX_AGE)
    user = User.objects.filter(pk=payload["pk"]).first()
    if user is None or user.email != (payload.get("email") or "").strip().lower():
        raise _invalid_token()
    if user.email_verified_at is not None:
        raise DomainError(
            "To konto jest już aktywne – możesz się zalogować.",
            "ALREADY_ACTIVE",
            status.HTTP_400_BAD_REQUEST,
        )
    return mark_activated(user, actor=user, request=request)


def resend_activation(email: str, *, request=None) -> bool:
    """Wysyła link ponownie, jeśli jest po co. Zwraca, czy list poszedł – **nie do pokazania**.

    Wołający pokazuje zawsze ``RESEND_MESSAGE``: odpowiedź zależna od istnienia konta zamieniłaby
    formularz w wyszukiwarkę adresów zarejestrowanych w serwisie. Wartość zwracana jest dla testów
    i dla logu, nie dla przeglądarki.
    """
    normalized = (email or "").strip().lower()
    if not normalized:
        return False
    user = User.objects.filter(email=normalized).first()
    if user is None or user.email_verified_at is not None:
        return False
    send_activation_email(user, request=request)
    return True
