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
"""

from __future__ import annotations

import logging

from django.contrib.auth.forms import PasswordResetForm
from django.db import transaction
from django.template import loader

logger = logging.getLogger(__name__)


class QueuedPasswordResetForm(PasswordResetForm):
    """``PasswordResetForm`` Django, który zamiast wysyłać list, kolejkuje go na ``mail``.

    Nadpisana jest **wyłącznie** ``send_mail`` – wybór kont (``get_users``: aktywne, z używalnym
    hasłem, dopasowanie adresu bez względu na wielkość liter), budowa kontekstu i generowanie
    tokenu zostają w ``save()`` Django bez zmian. Render szablonów odtwarza 1:1 ciało
    ``PasswordResetForm.send_mail`` (temat sklejony do jednej linii, HTML tylko przy podanej
    nazwie szablonu), a ``send_mail_task`` składa z tych napisów ``EmailMultiAlternatives``
    tak samo, jak robił to Django – test ``test_password_reset_queue.py`` porównuje oba listy.
    """

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
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
