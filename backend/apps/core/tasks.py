"""Zadania Celery wspólne dla całego systemu.

Trzy: wysyłka listu, puls kolejki i przebieg watchdoga alertów. Pierwsze idzie na kolejkę ``mail``
(``CELERY_TASK_ROUTES`` w ``config/settings/base.py``), którą worker konsumuje razem z ``default``
i ``scan``; dwa pozostałe na kolejkę domyślną – sensem pulsu jest właśnie sprawdzenie tej domyślnej
drogi, a watchdog nie może zależeć od kolejki, o której awarii ma donieść.

Dlaczego poczta w ogóle jest zadaniem, a nie ``send_mail`` w środku żądania: MTA bywa wolny albo
nieosiągalny, a wysyłka w żądaniu HTTP zamienia wtedy udaną operację (zapis na rozmowę) w błąd
albo w kilkunastosekundowe oczekiwanie z zajętym workerem gunicorna. List jest **skutkiem
ubocznym** zapisu, a nie jego warunkiem, więc jego los nie może decydować o odpowiedzi.

``fail_silently=False`` jest świadome: chcemy błędu w logu workera, a nie cichego zniknięcia
wiadomości. Dla nadawcy (uczestnika) i tak nic się nie zmienia – operacja jest już zapisana.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Klucz w cache'u, pod którym worker zostawia ślad ostatniego przebiegu. Czyta go strona
#: ``/status/`` – to jedyna droga, którą proces WWW może się dowiedzieć, czy kolejka zadań żyje:
#: sam broker odpowiada „jestem”, także wtedy, gdy po drugiej stronie nikt nie konsumuje zadań.
HEARTBEAT_CACHE_KEY = "core:worker-heartbeat"

#: Ile żyje wpis pulsu. Trzykrotność częstotliwości przebiegu (co minutę, ``CELERY_BEAT_SCHEDULE``):
#: jedna zgubiona minuta to jeszcze nie awaria, a po trzech nieudanych przebiegach wpis znika sam
#: i strona statusu mówi „brak pulsu” zamiast pokazywać znacznik sprzed doby.
HEARTBEAT_TTL_SECONDS = 180

#: Ile razy ponawiamy wysyłkę, zanim uznamy ją za przegraną. Cztery próby z rosnącym odstępem
#: przykrywają typową awarię MTA (restart, chwilowy brak DNS, greylisting u odbiorcy); dalsze
#: dobijanie się nic już nie zmieni, a zostawi w kolejce zadanie żyjące godzinami.
MAX_RETRIES = 3


def mail_from(competition=None) -> str | None:
    """Nadawca listów tego konkursu albo ``None`` = nadawca instalacji (``DEFAULT_FROM_EMAIL``).

    Jedno wejście do ``Competition.from_email`` dla całej wysyłki. Pole jest wypełniane
    i edytowalne od etapu 1 (``apps/web/competition_forms.py``), ale do tej zmiany **żadna** droga
    wysyłki go nie czytała – ``send_mail_task`` wpisywał ``DEFAULT_FROM_EMAIL`` na sztywno.
    Puste pole nadal znaczy „weź ustawienie instalacji”, a Konkurs #1 ma tam dokładnie
    ``DEFAULT_FROM_EMAIL`` (migracja ``tenancy.0002``), więc jego listy wychodzą od tego samego
    nadawcy, co dotąd – co jest istotne, bo część filtrów pocztowych traktuje zmianę nadawcy jak
    nowego korespondenta, czyli jak spam.

    Czego ta funkcja **nie** robi: nie dokleja ``Competition.email_subject_prefix`` do tematu.
    Prefiks jest dziś w ustawieniach (``EMAIL_SUBJECT_PREFIX``) i w kolumnie konkursu, ale
    ``django.core.mail.send_mail`` nigdy go nie używał (robią to wyłącznie ``mail_admins``
    i ``mail_managers``) – więc **żaden** dzisiejszy list Olimpiady Kwantowej go nie niesie.
    Doklejenie go tutaj nie byłoby podłączeniem istniejącej konfiguracji, tylko zmianą
    siedemnastu tematów naraz, widoczną dla każdego odbiorcy i dla jego reguł w skrzynce
    (``docs/UNIWERSALNY-ETAP-2.md`` § 0.1 i § 0.2, poz. 4). Prefiks czeka więc na osobną decyzję
    organizatora razem z ``Reply-To`` – a nie wchodzi „przy okazji” podłączania nadawcy.
    """
    return (getattr(competition, "from_email", "") or "").strip() or None


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
)
def send_mail_task(
    self,
    subject: str,
    message: str,
    recipient_list: list[str],
    from_email: str | None = None,
    html_message: str | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    """Wysyła jedną wiadomość tekstową. Zwraca liczbę dostarczonych listów (0 albo 1).

    ``html_message`` (opcjonalne, słowo kluczowe) dokłada wersję HTML jako alternatywę
    ``text/html`` – dokładnie ten kształt, który ``PasswordResetForm.send_mail`` Django składa sam
    (``EmailMultiAlternatives`` + ``attach_alternative``), bo ``django.core.mail.send_mail``
    z ``html_message`` robi to samo. Potrzebuje go list resetu hasła
    (``apps.accounts.password_reset``); wołający sprzed tej zmiany go nie podają i dostają list
    wyłącznie tekstowy, jak dotąd.

    Argumenty są prostymi typami (tekst, lista tekstów), a nie obiektami modeli: treść listu
    powstaje po stronie serwisu, zanim zadanie trafi do kolejki. Dzięki temu worker nie czyta
    bazy i nie zależy od tego, czy obiekt jeszcze istnieje w chwili wysyłki. Z tego samego powodu
    nadawca przychodzi **napisem**, a nie konkursem: wołający odczytuje go z konkursu w chwili
    kolejkowania (:func:`mail_from`), a worker nie musi wiedzieć, że konkursy w ogóle istnieją.

    ``from_email=None`` znaczy „nadawca instalacji” (``DEFAULT_FROM_EMAIL``) i jest wartością
    domyślną, więc zadanie zakolejkowane przez kod sprzed tej zmiany (albo przez wołającego, który
    konkursu nie zna) zachowuje się dokładnie tak, jak zachowywało się dotąd.

    Dlaczego ponowienia: jedyny list z terminem rozmowy jest za ważny, żeby zginąć przez pięć
    sekund niedostępności MTA. Rosnący odstęp (``retry_backoff``) z rozrzutem (``retry_jitter``)
    rozsypuje w czasie stado zadań, które padły naraz z tego samego powodu – bez rozrzutu po
    powrocie MTA wszystkie uderzyłyby w niego w tej samej sekundzie i wywróciły go ponownie.
    Ponawiamy każdy wyjątek, a nie tylko ``SMTPException``: między nami a serwerem poczty jest
    jeszcze DNS, gniazdo i TLS, a każda z tych warstw rzuca czym innym.

    Ścieżka testowa (``CELERY_TASK_ALWAYS_EAGER``) działa bez zmian: w trybie eager Celery
    wykonuje ponowienia synchronicznie i **ignoruje** ``countdown``, więc test nigdy nie czeka;
    po wyczerpaniu prób leci oryginalny wyjątek (``CELERY_TASK_EAGER_PROPAGATES``), a nie
    ``Retry``. Przy backendzie ``locmem`` wysyłka i tak nie zawodzi – testy widzą jedno wywołanie.

    ``headers`` (od 25.09.2026) to dodatkowe nagłówki listu – dziś wyłącznie ``List-Unsubscribe``
    i ``List-Unsubscribe-Post`` powiadomień forum (``apps.forum.notifications``). Słownik napisów,
    a nie obiekt, z tego samego powodu co reszta argumentów: jedzie przez JSON brokera. Bez
    nagłówków list idzie **tą samą** drogą co dotąd (``send_mail``), więc żaden istniejący list
    nie zmienia się ani o bajt.
    """
    if headers:
        # Ten sam kształt, który ``send_mail`` składa dla ``html_message`` (alternatywa
        # ``text/html``), tylko z nagłówkami – oba argumenty są niezależne i mogą przyjść razem.
        email = EmailMultiAlternatives(
            subject,
            message,
            from_email or settings.DEFAULT_FROM_EMAIL,
            list(recipient_list or []),
            headers=dict(headers),
        )
        if html_message:
            email.attach_alternative(html_message, "text/html")
        sent = email.send(fail_silently=False)
    else:
        sent = send_mail(
            subject,
            message,
            from_email or settings.DEFAULT_FROM_EMAIL,
            list(recipient_list or []),
            fail_silently=False,
            html_message=html_message,
        )
    logger.info(
        "Wysłano %s wiadomości do %s odbiorców (próba %s).",
        sent,
        len(recipient_list or []),
        self.request.retries + 1,
    )
    return sent


@shared_task(name="apps.core.tasks.heartbeat")
def heartbeat() -> str:
    """Zostawia w cache'u znacznik czasu ostatniego przebiegu workera. Zwraca go w ISO 8601.

    Po co osobne zadanie, skoro Celery ma ``inspect ping``: tamto pyta workera **synchronicznie**
    przez broker i czeka na odpowiedź, więc strona statusu wisiałaby dokładnie wtedy, gdy worker
    nie żyje – czyli w jedynym przypadku, dla którego ta strona istnieje. Tutaj jest odwrotnie:
    zapis robi worker, a strona wyłącznie czyta gotową wartość z Redisa i porównuje ją z zegarem.

    Ten sam mechanizm sprawdza przy okazji dwie rzeczy naraz, i to jest jego zaletą: żeby wpis
    powstał, musi zadziałać **cała** droga – beat wystawia zadanie, broker je przenosi, worker je
    wykonuje, a cache przyjmuje zapis. Brak pulsu nie mówi, które z tych ogniw padło, ale mówi
    prawdę o tym, co interesuje uczestnika: że oddana praca nie zostanie zeskanowana, a list nie
    wyjdzie.

    Zwracana wartość jest po to, żeby dało się ją zobaczyć w logu workera i w teście – nikt jej
    nie odbiera przez backend wyników (``CELERY_RESULT_BACKEND`` jest wyłączony).
    """
    stamp = timezone.now().isoformat()
    cache.set(HEARTBEAT_CACHE_KEY, stamp, HEARTBEAT_TTL_SECONDS)
    logger.debug("Puls workera: %s", stamp)
    return stamp


@shared_task(name="apps.core.tasks.alerts_check")
def alerts_check() -> int:
    """Przebieg watchdoga: ocena stanu serwisu i listy do dyżurnych. Zwraca liczbę wysłanych listów.

    Reguły są w ``apps.core.alerts`` – tutaj jest wyłącznie opakowanie w zadanie, żeby przebieg
    miał kto wywołać (``CELERY_BEAT_SCHEDULE``, co pięć minut). Ten podział pozwala testować
    regułę bez Celery i uruchomić ją ręcznie z ``manage.py shell`` w trakcie awarii.

    Zadanie **nie ma ponowień** i to jest świadome: kolejny przebieg i tak przyjdzie za pięć
    minut, a ponawiane zadanie alertowe potrafi zdublować list dokładnie wtedy, gdy skrzynka
    dyżurnego jest najbardziej potrzebna. Wyjątek zostaje w logu workera.

    Uwaga na kolejkę: zadanie jedzie kolejką ``default`` (nie ``mail``), choć wysyła listy.
    Wysyłka jest tu synchroniczna właśnie po to, żeby informacja o zapchanej kolejce ``mail``
    nie czekała w kolejce ``mail`` – patrz docstring ``apps.core.alerts``.
    """
    from apps.core.alerts import run

    sent = run()
    if sent:
        logger.warning("Watchdog wysłał %s alertów.", sent)
    return sent


@shared_task(name="apps.core.tasks.captcha_clean")
def captcha_clean() -> int:
    """Usuwa wygasłe wyzwania CAPTCHA (``captcha.CaptchaStore``). Zwraca liczbę skasowanych wierszy.

    Po co to w ogóle sprzątać: ``django-simple-captcha`` zapisuje **jeden wiersz na każde
    wyrenderowanie** obrazka – formularz odrzucony przez inną walidację (hasła się nie zgadzają,
    e-mail zajęty) każe przeglądarce wyrenderować nowe wyzwanie, a stare zostaje w tabeli. Pakiet
    sam niczego nie kasuje (żadnego sygnału ani zadania) – bez tego zadania tabela rośnie
    bezterminowo, proporcjonalnie do ruchu na `/register/` i `/register/committee/`, nie do liczby
    kont, które faktycznie powstały.
    Godzina, nie dzień: wyzwanie wygasa po kilku minutach (``CAPTCHA_TIMEOUT``), więc częstszy
    przebieg trzyma tabelę stale małą zamiast pozwalać jej urosnąć między przebiegami i skasować
    naraz dużo wierszy jednym zapytaniem `DELETE`.

    Liczymy wygasłe wiersze **przed** wywołaniem ``CaptchaStore.remove_expired()`` (metoda pakietu
    nie zwraca liczby skasowanych) – w oknie między liczeniem a kasowaniem może dojść jeszcze jedno
    wygaśnięcie, ale to zadanie jest wyłącznie miarą sprzątania w logu workera, nie rozliczeniem.
    """
    from captcha.models import CaptchaStore

    expired = CaptchaStore.objects.filter(expiration__lt=timezone.now()).count()
    CaptchaStore.remove_expired()
    logger.debug("Sprzątanie CAPTCHY: skasowano %s wygasłych wyzwań.", expired)
    return expired
