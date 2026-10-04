"""Reset hasła z listem wysyłanym w tle – kolejka ``mail``, ``apps.core.tasks.send_mail_task``.

Do v0.35.0 ``POST /password-reset/`` wysyłał list **w żądaniu** (``PasswordResetForm.send_mail``
Django → ``EmailMultiAlternatives.send()``). Skutki były dwa i oba złe:

1. **wolny albo niedostępny MTA trzymał wątek gunicorna** do ``EMAIL_TIMEOUT`` (10 s) – przy
   16 wątkach na instancję wystarczy kilkanaście resetów w minucie awarii relaya, żeby zająć
   wszystkie, a wtedy nie odpowiada nic, także formularz oddawania prac,
2. **czas odpowiedzi zdradzał, czy konto istnieje.** Adres bez konta kończył się po jednym
   zapytaniu do bazy, adres z kontem – po rozmowie SMTP (setki milisekund do sekund). Treść
   i kod odpowiedzi były identyczne (302 na ``/password-reset/sent/``), a enumeracja szła stoperem.

Tutaj list powstaje dokładnie tak samo – te same szablony, ten sam kontekst, ten sam token, bo
wszystko to nadal robi ``PasswordResetForm.save()`` Django – a zmienia się wyłącznie **ostatni
krok**: zamiast ``send()`` gotowe napisy (temat, treść, wersja HTML) idą do ``send_mail_task``
po zatwierdzeniu transakcji. Różnica czasu między adresem z kontem i bez to teraz render trzech
szablonów i jeden zapis do Redisa – pojedyncze milisekundy, poniżej rozrzutu samej sieci.

Z tej klasy korzystają obie drogi wysyłki linku: samoobsługowa (``apps.web.views.public.
PasswordResetView``) i koordynatora (``apps.web.views.coordinator_accounts.
CoordinatorPasswordResetView``), więc list „wysłany przez organizatora” dalej jest bajt w bajt tym
samym listem, który uczestnik wysyła sobie sam.

**Token jedzie przez brokera.** Link z tokenem leży w treści zadania w Redisie (kolejka ``mail``)
do chwili odebrania przez workera – tak samo jak link aktywacyjny (``apps.accounts.activation``).
Redis stoi wyłącznie w sieci ``internal`` compose'a, bez portu na zewnątrz; token jest
jednorazowy i ważny dobę (``PASSWORD_RESET_TIMEOUT``).

**Które konta dostają link (AUTH-01a, 4.10.2026; poprawki po przeglądzie 5.10.2026).**

Reguła stoi w :func:`reset_eligible` i :func:`awaiting_activation`:

- aktywne konto z hasłem – jak w Django,
- aktywne konto **bez** hasła platformy (rejestracja przez Google/Facebooka) – tak, ale tylko gdy
  adres jest potwierdzony **naszą** drogą albo przez dostawcę: ``email_verified_at`` i wpis allauth
  ``EmailAddress(verified=True)`` dla adresu konta (M2). Samo ``email_verified_at`` nie wystarcza,
  bo migracja ``accounts.0010`` wpisała je hurtem kontom sprzed aktywacji, a koordynator może
  włączyć konto bez potwierdzania adresu. Konto z zaproszenia, któremu brakuje wymaganych zgód
  (ręcznie aktywowane przed poprawką H1), nie dostaje linku wcale – inaczej reset byłby drogą do
  panelu z pominięciem zgód. Konto, któremu allauth wyczyścił hasło przy łączeniu z Google,
  wpisu ``verified`` nie ma – loguje się Google'em i ustawia hasło z panelu konta (AUTH-01b),
- konto z rejestracji **przed aktywacją** (nieaktywne, adres niepotwierdzony, bez zaproszenia)
  dostaje zwykły link resetu, a zapisanie nowego hasła **aktywuje** konto (M1,
  ``apps.web.views.public.PasswordResetConfirmView``). Nie wysyłamy linku aktywacyjnego: ten
  uruchomiłby konto z hasłem wpisanym przy rejestracji – a rejestrować się na cudzy adres może
  każdy. Po resecie obowiązuje wyłącznie hasło właściciela skrzynki,
- zaproszony uczeń (import, delegacja) przed przyjęciem zaproszenia – zamiast linku resetu
  ponowione zaproszenie (:func:`send_start_link`, z odstępem i audytem), bo zgody zbiera ekran
  zaproszenia,
- konto zablokowane przez organizatora i konto zanonimizowane – nic (blokada znaczy „nie loguje
  się wcale”, a adres ``deleted-…@invalid.…`` nie ma skrzynki).

Odpowiedź formularza jest we wszystkich tych przypadkach ta sama (302 na ``/password-reset/sent/``).

**Nadawca** jest nadawcą konkursu, z którego przyszło żądanie (``Competition.from_email`` przez
``apps.core.tasks.mail_from``) – tak jak przy listach aktywacyjnych. Do AUTH-01a reset szedł zawsze
od ``DEFAULT_FROM_EMAIL``, więc uczestnik IQO dostawał angielski list od nadawcy Olimpiady Kwantowej.
"""

from __future__ import annotations

import logging
import unicodedata

from django.contrib.auth.forms import PasswordResetForm
from django.db import transaction
from django.template import loader

from .activation import mail_competition, recipient_language, resend_activation
from .models import User

logger = logging.getLogger(__name__)


class QueuedPasswordResetForm(PasswordResetForm):
    """``PasswordResetForm`` Django, który zamiast wysyłać list, kolejkuje go na ``mail``.

    Nadpisane są ``send_mail`` i ``get_users`` (od AUTH-01a: aktywne konta także bez hasła
    platformy, patrz docstring modułu); budowa kontekstu i generowanie tokenu zostają
    w ``save()`` Django bez zmian. Render szablonów odtwarza 1:1 ciało
    ``PasswordResetForm.send_mail`` (temat sklejony do jednej linii, HTML tylko przy podanej
    nazwie szablonu), a ``send_mail_task`` składa z tych napisów ``EmailMultiAlternatives``
    tak samo, jak robił to Django – test ``test_password_reset_queue.py`` porównuje oba listy.
    """

    #: Żądanie, w którym składany jest list – ``PasswordResetForm.send_mail`` go nie dostaje,
    #: więc ``save`` odkłada je tutaj. Potrzebne wyłącznie do pytania „czyje to żądanie”.
    _request = None

    def get_users(self, email):
        """Konta o tym adresie, którym wolno wysłać link (``reset_eligible``, ``awaiting_activation``).

        Kopia ``PasswordResetForm.get_users`` Django z własną regułą zamiast ``is_active``
        i ``has_usable_password()`` (docstring modułu). Porównanie po normalizacji Unicode zostaje
        (``_unicode_ci_compare`` Django jest prywatne, więc trzy linijki są tutaj): zapytanie
        ``iexact`` w bazie i porównanie w Pythonie rozstrzygają się różnie dla znaków spoza ASCII,
        a list ma iść wyłącznie na adres konta.
        """
        candidates = User._default_manager.exclude_anonymised().filter(email__iexact=email)
        wanted = _casefolded(email)
        return (
            user
            for user in candidates
            if _casefolded(user.email) == wanted and (reset_eligible(user) or awaiting_activation(user))
        )

    def save(self, *args, request=None, **kwargs):
        self._request = request
        return super().save(*args, request=request, **kwargs)

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        # Samoobsługa (``/password-reset/``) składa list w żądaniu adresata – aktywny język jest
        # już jego. Reset zlecony z panelu koordynatora powstaje w **cudzym** żądaniu, więc idzie
        # w języku odbiorcy (``apps.accounts.activation.recipient_language``).
        with recipient_language(context.get("user"), request=self._request):
            subject = loader.render_to_string(subject_template_name, context)
            # Temat nie może mieć znaku nowej linii (wstrzyknięcie nagłówka) – ta sama linijka co w Django.
            subject = "".join(subject.splitlines())
            body = loader.render_to_string(email_template_name, context)
            html = (
                loader.render_to_string(html_email_template_name, context)
                if html_email_template_name is not None
                else None
            )
        # ``render_to_string`` oddaje ``SafeString``, a argumenty zadania jadą przez JSON brokera –
        # zwykły napis jest jedynym kształtem, który na pewno przejdzie bez niespodzianek.
        # ``str.__str__``, a nie ``str(...)``: ``SafeString.__str__`` zwraca samego siebie.
        subject, body = str.__str__(subject), str.__str__(body)
        html = str.__str__(html) if html is not None else None
        user_pk = context["user"].pk
        # Nadawca konkursu żądania (kontekst ustawia ``CompetitionMiddleware``), rozstrzygnięty
        # **teraz**, a nie w callbacku – ta sama reguła i z tego samego powodu, co w ``queue_mail``.
        if not from_email:
            from apps.core.tasks import mail_from

            from_email = mail_from(mail_competition())

        def _enqueue() -> None:
            from apps.core.tasks import send_mail_task

            try:
                send_mail_task.delay(subject, body, [to_email], from_email, html_message=html)
            except Exception:  # noqa: BLE001 - patrz niżej
                # Niedostępny broker nie może zamienić odpowiedzi w 500: adres **bez** konta
                # dostałby wtedy 302, a adres z kontem – błąd, czyli dokładnie ta wyrocznia, której
                # ten formularz ma nie być. Django w tym miejscu też połyka błąd wysyłki i loguje
                # go po kluczu konta, a nie po adresie – robimy to samo.
                logger.exception("Nie udało się zakolejkować listu resetu hasła dla konta %s.", user_pk)

        # Po commicie: widok może kiedyś działać w transakcji (``ATOMIC_REQUESTS``, audyt obok),
        # a list z linkiem nie ma prawa wyjść, jeśli operacja, która go wywołała, została wycofana.
        # W trybie autocommit (dziś) Django wywołuje funkcję od razu.
        transaction.on_commit(_enqueue)


def _casefolded(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def invited_without_consents(user) -> bool:
    """Czy konto ma profil z zaproszenia, któremu brakuje wymaganej zgody (także zgody opiekuna).

    Wymagane rodzaje liczy ta sama funkcja, co rejestracja (``consents.required_kinds``: wiek,
    zestaw zgód konkursu ucznia); złożone – niewycofane wpisy ``ConsentRecord`` profilu.
    """
    from .consents import required_kinds
    from .models import ConsentRecord, Participant

    for participant in Participant.objects.filter(user=user, invited_at__isnull=False).select_related(
        "competition"
    ):
        required = set(
            required_kinds(
                participant.birth_date, participant.birth_year, competition=participant.competition
            )
        )
        given = set(
            ConsentRecord.objects.filter(participant=participant, withdrawn_at__isnull=True).values_list(
                "kind", flat=True
            )
        )
        if not required <= given:
            return True
    return False


def reset_eligible(user) -> bool:
    """Czy **aktywnemu** kontu wolno wysłać link resetu – reguła z docstringu modułu."""
    if not user.is_active:
        return False
    if user.has_usable_password():
        return True
    if user.email_verified_at is None:
        return False
    from allauth.account.models import EmailAddress

    if not EmailAddress.objects.filter(user=user, email__iexact=user.email, verified=True).exists():
        return False
    return not invited_without_consents(user)


def awaiting_activation(user) -> bool:
    """Konto z rejestracji przed aktywacją: link resetu, którego zapisanie aktywuje konto (M1).

    Bez kont z zaproszeniem – te dostają zaproszenie (:func:`send_start_link`).
    """
    from .activation import pending_invitation

    if user.is_active or user.email_verified_at is not None:
        return False
    return pending_invitation(user) is None


def send_start_link(email: str, *, request=None) -> bool:
    """Zaproszony uczeń przed przyjęciem zaproszenia – zamiast linku resetu ponowione zaproszenie.

    „Nie pamiętasz hasła?” to pierwsze, co klika uczeń zgłoszony przez opiekuna drużyny albo
    zaimportowany z listy klasowej, gdy list zaproszenia zginął: konta nie da się zalogować, więc
    zakłada, że zapomniał hasła. Zaproszenie (ekran ze zgodami i hasłem) idzie przez
    ``resend_activation`` bez wykonawcy, czyli z odstępem ``INVITATION_RESEND_COOLDOWN``
    i wpisem ``participant.invitation_resent``. Konto z rejestracji przed aktywacją obsługuje
    sam formularz (:func:`awaiting_activation`). Wynik jest dla testów – przeglądarka widzi
    zawsze tę samą stronę.
    """
    from .activation import pending_invitation

    user = (
        User.objects.exclude_anonymised()
        .filter(email__iexact=(email or "").strip(), is_active=False, email_verified_at__isnull=True)
        .first()
    )
    if user is None or pending_invitation(user) is None:
        return False
    try:
        return resend_activation(user.email, request=request)
    except Exception:  # noqa: BLE001 - ta sama wyrocznia, co przy ``send_mail`` wyżej
        # ``queue_mail`` nie połyka błędu brokera, a w autocommicie callback ``on_commit`` biegnie
        # od razu – bez tego konto nieuruchomione kończyłoby się 500, a nieistniejące 302.
        logger.exception("Nie udało się zakolejkować zaproszenia dla konta %s.", user.pk)
        return False
