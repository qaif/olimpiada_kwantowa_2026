"""Zadania Celery wspólne dla całego systemu.

Dwa: wysyłka listu i puls kolejki. Pierwsze idzie na kolejkę ``mail`` (``CELERY_TASK_ROUTES``
w ``config/settings/base.py``), którą worker konsumuje razem z ``default`` i ``scan``; drugie
na kolejkę domyślną, bo jego sensem jest właśnie sprawdzenie tej domyślnej drogi.

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
from django.core.mail import send_mail
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


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
)
def send_mail_task(self, subject: str, message: str, recipient_list: list[str]) -> int:
    """Wysyła jedną wiadomość tekstową. Zwraca liczbę dostarczonych listów (0 albo 1).

    Argumenty są prostymi typami (tekst, lista tekstów), a nie obiektami modeli: treść listu
    powstaje po stronie serwisu, zanim zadanie trafi do kolejki. Dzięki temu worker nie czyta
    bazy i nie zależy od tego, czy obiekt jeszcze istnieje w chwili wysyłki.

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
    """
    sent = send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        list(recipient_list or []),
        fail_silently=False,
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
