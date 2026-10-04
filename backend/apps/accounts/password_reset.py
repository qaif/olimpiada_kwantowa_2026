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

**Które konta dostają link (AUTH-01a, 4.10.2026).**

- aktywne konto z hasłem – jak w Django,
- aktywne konto **bez** hasła platformy (rejestracja przez Google/Facebooka, hasło wyczyszczone
  przez allauth przy łączeniu po adresie) – też. Django takie konta pomija (``get_users``), a nasze
  ekrany od początku obiecywały, że hasło ustawia się właśnie przez „Nie pamiętasz hasła?”
  (``apps.accounts.services.register_social_participant``, ``apps.accounts.adapters``); bez tej
  zmiany list po cichu nie wychodził. Link potwierdza dostęp do skrzynki – tę samą rzecz, którą
  potwierdza logowanie u dostawcy po zweryfikowanym adresie,
- konto **jeszcze nieuruchomione** (nieaktywne, adres niepotwierdzony) – nie dostaje linku resetu,
  tylko link startowy: zaproszenie ucznia (import, delegacja) albo link aktywacyjny
  (:func:`send_start_link`). Reset nie aktywuje konta sam, bo ominąłby zgody zbierane na ekranie
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
        """Aktywne konta o tym adresie – **także** bez hasła platformy (patrz docstring modułu).

        Kopia ``PasswordResetForm.get_users`` Django bez warunku ``has_usable_password()``.
        Porównanie po normalizacji Unicode zostaje (``_unicode_ci_compare`` Django jest prywatne,
        więc trzy linijki są tutaj): zapytanie ``iexact`` w bazie i porównanie w Pythonie
        rozstrzygają się różnie dla znaków spoza ASCII, a list ma iść wyłącznie na adres konta.
        """
        active_users = User._default_manager.filter(email__iexact=email, is_active=True)
        wanted = _casefolded(email)
        return (user for user in active_users if _casefolded(user.email) == wanted)

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


def send_start_link(email: str, *, request=None) -> bool:
    """Konto jeszcze nieuruchomione – zamiast linku resetu idzie link, który je uruchamia.

    „Nie pamiętasz hasła?” to pierwsze, co klika uczeń zgłoszony przez opiekuna drużyny albo
    zaimportowany z listy klasowej, gdy list zaproszenia zginął: konta nie da się zalogować, więc
    zakłada, że zapomniał hasła. Do AUTH-01a formularz odpowiadał „sprawdź skrzynkę”, a list nie
    wychodził wcale (Django pomija konta nieaktywne). Teraz idzie list, który faktycznie pomoże:
    zaproszenie (ekran ze zgodami i hasłem) albo link aktywacyjny – oba przez
    ``resend_activation``, czyli z tą samą logiką, co formularz „Wyślij link ponownie”.

    Tylko konta **nieaktywne z niepotwierdzonym adresem**: aktywne dostały już link resetu,
    a nieaktywne z potwierdzonym adresem są zablokowane przez organizatora. Konto zanonimizowane
    pomijamy wprost. Wynik jest dla testów – przeglądarka widzi zawsze tę samą stronę.
    """
    user = (
        User.objects.exclude_anonymised()
        .filter(email__iexact=(email or "").strip(), is_active=False, email_verified_at__isnull=True)
        .first()
    )
    if user is None:
        return False
    try:
        return resend_activation(user.email, request=request)
    except Exception:  # noqa: BLE001 - ta sama wyrocznia, co przy ``send_mail`` wyżej
        # ``queue_mail`` nie połyka błędu brokera, a w autocommicie callback ``on_commit`` biegnie
        # od razu – bez tego konto nieuruchomione kończyłoby się 500, a nieistniejące 302.
        logger.exception("Nie udało się zakolejkować linku startowego dla konta %s.", user.pk)
        return False
