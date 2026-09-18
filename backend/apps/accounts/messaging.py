"""Wysyłka komunikatów organizatora do grup odbiorców (panel koordynatora).

Moduł robi trzy rzeczy i nic poza nimi: rozwiązuje grupę odbiorców na listę adresów, zapisuje
wysyłkę w rejestrze (``MessageBroadcast``) i przekazuje listy do kolejki porcjami.

Dlaczego porcjami, a nie jednym zadaniem na całą wysyłkę: lista uczestników edycji to kilka
tysięcy adresów. Jedno zadanie oznaczałoby jeden proces workera zajęty przez kilkanaście minut,
jeden wyjątek kasujący całą resztę wysyłki i jeden ogromny ładunek w brokerze. Porcja po
``RECIPIENT_CHUNK`` adresów jest krótka, a jej ponowienie nie dubluje listów wysłanych w innych
porcjach.

Dlaczego mimo to **jeden list na odbiorcę**, a nie jedna koperta z wieloma adresatami: lista
adresów w nagłówku ``To:`` pokazałaby każdemu uczestnikowi adresy wszystkich pozostałych. To samo
dotyczy ``Cc``. Kopertę z ``Bcc`` odrzuca część serwerów odbiorczych jako spam, a i tak nie da się
wtedy powiedzieć, do kogo list nie doszedł.

Kogo **nigdy** nie ma na liście odbiorców: kont nieaktywnych i kont, których adresu nikt nie
potwierdził. Konto bez potwierdzonego adresu to najczęściej literówka albo cudzy adres wpisany
przy rejestracji (patrz ``apps.accounts.activation``) – wysyłka do niego jest w najlepszym razie
bezcelowa, a w najgorszym listem do osoby, która o olimpiadzie nigdy nie słyszała.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction
from django.db.models import F, Q

from apps.core.models import audit

from .models import (
    GROUP_REVIEWER,
    BroadcastGroup,
    BroadcastStatus,
    CommitteeMember,
    CommitteeStatus,
    MessageBroadcast,
    User,
    normalize_voivodeship,
)

logger = logging.getLogger(__name__)

#: Ile adresów wchodzi do jednej porcji. Pięćdziesiąt to kompromis: porcja kończy się w kilka
#: sekund (a więc jej ponowienie jest tanie), a tysiąc odbiorców to dwadzieścia zadań, a nie
#: tysiąc – kolejka nie zamienia się w log wysyłki.
RECIPIENT_CHUNK = 50

#: Konto, do którego wolno wysłać komunikat: aktywne i z potwierdzonym adresem e-mail.
DELIVERABLE = Q(is_active=True) & Q(email_verified_at__isnull=False)


def _emails(queryset) -> list[str]:
    """Adresy z zapytania o użytkowników: znormalizowane, bez pustych i bez powtórzeń.

    Powtórzenia są realne, a nie teoretyczne: ta sama osoba bywa i uczestnikiem, i członkiem
    komitetu, a przy grupie zbieranej po wpisach do etapów jeden uczestnik ma ich kilka. Dwa listy
    o tej samej treści pod ten sam adres wyglądają jak awaria serwisu.

    Kolejność jest posortowana, bo od niej zależy podział na porcje – a powtarzalny podział jest
    warunkiem tego, żeby ponowienie wysyłki dało się porównać z pierwotną.
    """
    return sorted({(email or "").strip().lower() for email in queryset if (email or "").strip()})


def parse_address_list(text: str) -> list[str]:
    """Adresy z pola „wklejona lista”: rozdzielone przecinkiem, średnikiem albo końcem wiersza.

    Świadomie liberalnie, bo źródłem takiej listy jest zwykle kopiuj-wklej z arkusza albo
    z klienta pocztowego, a każdy z nich rozdziela adresy inaczej. Walidacja kształtu adresu
    należy do formularza – tutaj rozstrzygamy wyłącznie, gdzie kończy się jeden, a zaczyna drugi.
    """
    separated = text.replace(";", "\n").replace(",", "\n")
    return _emails(part for part in separated.splitlines())


def resolve_recipients(
    group: str,
    *,
    edition=None,
    stage=None,
    district: str | None = None,
    addresses: str = "",
) -> list[str]:
    """Adresy odbiorców dla wybranej grupy. Nieznana grupa to pusta lista, nigdy wyjątek widoku.

    Grupy uczestników idą po ``StageEntry``, a nie po samym istnieniu profilu: „uczestnik bieżącej
    edycji” znaczy „ktoś, kto się do niej zapisał”, a nie „ktoś, kto kiedykolwiek założył konto”.
    Konto założone dwie edycje temu i nieużywane od tamtej pory nie jest adresatem komunikatu
    o terminach tegorocznych zawodów.

    Brak wymaganego kontekstu (edycji przy grupie edycyjnej, etapu przy grupie etapowej) też daje
    pustą listę. Ekran nie wyśle wtedy niczego i powie o tym wprost – to bezpieczniejszy kierunek
    niż domyślanie się, o który etap chodziło.
    """
    if group == BroadcastGroup.CUSTOM:
        return parse_address_list(addresses or "")
    if group == BroadcastGroup.EDITION_PARTICIPANTS:
        if edition is None:
            return []
        return _emails(
            User.objects.filter(DELIVERABLE, participations__stage_entries__stage__edition=edition)
            .values_list("email", flat=True)
            .distinct()
        )
    if group == BroadcastGroup.STAGE_REGISTERED:
        if stage is None:
            return []
        return _emails(
            User.objects.filter(DELIVERABLE, participations__stage_entries__stage=stage)
            .values_list("email", flat=True)
            .distinct()
        )
    if group == BroadcastGroup.STAGE_QUALIFIED:
        if stage is None:
            return []
        from apps.competitions.models import StageEntryStatus

        return _emails(
            User.objects.filter(
                DELIVERABLE,
                participations__stage_entries__stage=stage,
                participations__stage_entries__status=StageEntryStatus.QUALIFIED,
            )
            .values_list("email", flat=True)
            .distinct()
        )
    if group in (BroadcastGroup.COMMITTEE, BroadcastGroup.COMMITTEE_DISTRICT):
        # Ta sama definicja „aktywnego recenzenta”, co w ``apps.grading.services.reviewer_pool``:
        # status ACTIVE **i** grupa ``reviewer``. Komunikat do komitetu nie może trafić do osoby,
        # która zgłosiła się, ale nie została jeszcze zatwierdzona – ona nie jest członkiem.
        members = CommitteeMember.objects.filter(
            status=CommitteeStatus.ACTIVE,
            user__is_active=True,
            user__email_verified_at__isnull=False,
            user__groups__name=GROUP_REVIEWER,
        )
        if group == BroadcastGroup.COMMITTEE_DISTRICT:
            normalized = normalize_voivodeship(district)
            if not normalized:
                return []
            members = members.filter(district=normalized)
        return _emails(members.values_list("user__email", flat=True).distinct())
    return []


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_broadcast_chunk(self, broadcast_id: int, recipients: list[str]) -> int:
    """Przekazuje jedną porcję komunikatu do wysyłki. Zwraca liczbę zakolejkowanych listów.

    Zadanie **nie wysyła** samo – dla każdego adresu woła ``apps.core.tasks.send_mail_task``.
    Dzięki temu ponowienia przy chwilowej awarii MTA zostają tam, gdzie już są opisane i
    przetestowane, a to zadanie odpowiada wyłącznie za rozbicie listy na pojedyncze koperty
    i za dopisanie licznika do rejestru.

    Temat i treść czytamy z rejestru, a nie z argumentów: ładunek zadania to wtedy identyfikator
    i porcja adresów, a nie kopia całego listu powielona przy każdej porcji. Stamtąd bierze się też
    nadawca – z konkursu, do którego należy wysyłka (``MessageBroadcast.competition``). Przez moduł
    marki nie idzie tu nic i nie ma czego: temat i treść komunikatu napisał koordynator, więc jego
    zdania nie są napisem w kodzie, który wolno podmienić na wzorzec.

    Licznik zwiększamy wyrażeniem ``F``, bo porcje idą równolegle – odczyt i zapis w Pythonie
    gubiłby część inkrementów przy dwóch workerach.
    """
    # ``select_related``, bo z konkursu czytamy nadawcę listów – bez tego doszłoby drugie zapytanie
    # na każdą porcję adresów.
    broadcast = MessageBroadcast.objects.filter(pk=broadcast_id).select_related("competition").first()
    if broadcast is None:
        # Rejestr skasowano między zakolejkowaniem a wykonaniem – nie ma czego wysyłać ani
        # czego liczyć. Ponowienie niczego nie naprawi, więc kończymy cicho.
        logger.warning(
            "Komunikat %s nie istnieje – porcja %s adresów pominięta.", broadcast_id, len(recipients)
        )
        return 0

    from apps.core.tasks import mail_from, send_mail_task

    # Nadawcę czytamy raz na porcję: jest własnością konkursu, a nie pojedynczej koperty.
    from_email = mail_from(broadcast.competition)
    queued = 0
    for recipient in recipients:
        send_mail_task.delay(broadcast.subject, broadcast.body, [recipient], from_email)
        queued += 1
    MessageBroadcast.objects.filter(pk=broadcast_id).update(sent_count=F("sent_count") + queued)
    broadcast.refresh_from_db(fields=["sent_count", "recipient_count"])
    if broadcast.sent_count >= broadcast.recipient_count:
        MessageBroadcast.objects.filter(pk=broadcast_id).update(status=BroadcastStatus.SENT)
    logger.info("Komunikat %s: zakolejkowano %s listów.", broadcast_id, queued)
    return queued


def _chunks(recipients: list[str]) -> list[list[str]]:
    return [
        recipients[index : index + RECIPIENT_CHUNK] for index in range(0, len(recipients), RECIPIENT_CHUNK)
    ]


@transaction.atomic
def send_broadcast(
    *,
    group: str,
    subject: str,
    body: str,
    recipients: list[str],
    actor=None,
    request=None,
) -> MessageBroadcast:
    """Zapisuje komunikat w rejestrze i kolejkuje wysyłkę **po commicie**.

    Kolejność jest tu regułą, a nie szczegółem: zadanie czyta temat i treść z rejestru, więc nie
    wolno go zakolejkować, zanim wiersz naprawdę będzie w bazie. ``transaction.on_commit`` daje
    dokładnie tę gwarancję – ten sam wzorzec, co przy liście aktywacyjnym
    (``apps.accounts.activation.queue_mail``).

    Do audytu (``broadcast.sent``) idą grupa i liczniki. Ani adresów, ani treści: audyt czytają
    także osoby bez prawa do danych kontaktowych uczestników, a treść komunikatu jest już
    w rejestrze, do którego wchodzi wyłącznie koordynator.
    """
    # Import lokalny: ``services`` ciągnie za sobą aktywację i zgody, a ten moduł jest ładowany
    # przez Celery przy starcie workera – zależność w drugą stronę zamknęłaby cykl.
    from .services import default_competition

    broadcast = MessageBroadcast.objects.create(
        # Rejestr wysyłek należy do organizatora, który je zrobił; grupy odbiorców są zakresowane
        # jego edycją, więc wiersz bez konkursu nie dałby się odczytać.
        competition=default_competition(),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        group=group,
        subject=subject,
        body=body,
        recipient_count=len(recipients),
        status=BroadcastStatus.QUEUED,
    )
    chunks = _chunks(recipients)

    def _enqueue() -> None:
        for chunk in chunks:
            send_broadcast_chunk.delay(broadcast.pk, chunk)

    transaction.on_commit(_enqueue)
    audit(
        actor,
        "broadcast.sent",
        broadcast,
        {"group": group, "recipients": len(recipients), "chunks": len(chunks)},
        request=request,
    )
    logger.info(
        "Komunikat %s do grupy %s: %s odbiorców w %s porcjach.",
        broadcast.pk,
        group,
        len(recipients),
        len(chunks),
    )
    return broadcast


#: Ile komunikatów pokazujemy na ekranie. Rejestr jest narzędziem do pytania „co ostatnio poszło
#: i do kogo”, a nie archiwum korespondencji – pełną historię ma audyt (``broadcast.sent``).
BROADCAST_HISTORY_LIMIT = 50


def recent_broadcasts(limit: int = BROADCAST_HISTORY_LIMIT) -> list[MessageBroadcast]:
    """Ostatnie komunikaty, od najnowszego – z doczytanym autorem (wiersz pokazuje jego adres)."""
    return list(MessageBroadcast.objects.select_related("created_by")[:limit])
