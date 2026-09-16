"""Pokój wideo rozmowy kwalifikacyjnej: nazwa, adres i przypomnienie dzień wcześniej.

Do tej pory link do rozmowy wpisywał koordynator ręcznie, per termin (``InterviewSlot.meeting_url``).
Przy kilkudziesięciu terminach znaczyło to kilkadziesiąt okazji do wklejenia nie tego adresu,
a przy zapomnianym polu – uczestnika, który o godzinie rozmowy czyta w panelu „koordynator poda
link przed rozmową”. Dlatego adres powstaje **sam, w chwili zapisu**, a ręczne pole zostaje jako
nadrzędne: koordynator, który ma własny pokój (stały numer w systemie uczelni, sala z BBB), wpisuje
go i nic go nie nadpisuje.

Co rozstrzyga etap (``Stage``):

- ``video_provider`` – ``none`` (jak dotąd: żadnych linków), ``jitsi`` (publiczna instancja),
  ``custom`` (własna instancja pod adresem z ``video_base_url``). Trzy wartości, bo to trzy różne
  decyzje organizatora, a nie stopnie tej samej,
- ``video_base_url`` – korzeń, do którego dokleja się nazwa pokoju. Domyślnie ``https://meet.jit.si/``.

Nazwa pokoju: ``olimpiada-<edycja>-<prefiks identyfikatora terminu>-<6 losowych znaków>``.

- **edycja i termin** są w nazwie dla człowieka: adres bywa dyktowany przez telefon i przepisywany
  z kartki, a wtedy „olimpiada-2027-…” mówi, czego dotyczy, zanim ktokolwiek kliknie,
- **sześć losowych znaków** to jedyna część, która czyni pokój prywatnym. Publiczna instancja Jitsi
  nie wymaga logowania, więc nazwa pokoju **jest** poświadczeniem – przewidywalna („olimpiada-2027-12”)
  byłaby zaproszeniem dla każdego, kto potrafi liczyć. Dlatego adres nie stoi na liście terminów,
  a wyłącznie przy własnym zapisie uczestnika (tak samo, jak przed tą zmianą),
- **jeden pokój na termin, nie na osobę**: termin może mieć kilka miejsc (``capacity``), a komisja
  musi zastać wszystkich w tym samym pokoju. Adres zapisujemy mimo to na zapisie
  (``InterviewBooking.meeting_url``), bo to zapis jest tym, co uczestnik widzi i co idzie w liście –
  a przeniesienie zapisu na inny termin ma dać inny adres, nie ten sam.

Czego tu **nie ma i nie będzie**: osadzenia wideo na naszej stronie. Ramka z obcej domeny wymagałaby
rozluźnienia ``frame-src`` w CSP (dziś: wyłącznie YouTube i Vimeo dla osadzeń redakcyjnych), a wideo
w ``<iframe>`` prosi przeglądarkę o kamerę i mikrofon **w kontekście naszej domeny**. Link otwiera
pokój u dostawcy i to on pyta o urządzenia – jak powinien.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

logger = logging.getLogger(__name__)

#: Domyślny korzeń adresu pokoju – publiczna instancja Jitsi Meet.
DEFAULT_VIDEO_BASE_URL = "https://meet.jit.si/"

#: Alfabet losowej części nazwy pokoju. Bez znaków mylących przy przepisywaniu (0/O, 1/I/L) –
#: ten sam powód, co przy kodzie publicznym uczestnika (``accounts.PUBLIC_CODE_ALPHABET``).
ROOM_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
ROOM_RANDOM_LENGTH = 6

#: Ile znaków identyfikatora terminu wchodzi do nazwy. Osiem znaków szesnastkowych to cztery
#: miliardy możliwości – w nazwie i tak nie pełnią roli sekretu (od tego jest część losowa),
#: tylko odróżniają terminy tej samej edycji.
SLOT_PREFIX_LENGTH = 8

#: Przestrzeń nazw dla identyfikatora terminu. ``uuid5`` zamiast kolumny w bazie: identyfikator ma
#: być **stały dla terminu** (przeniesienie zapisu musi trafić do tego samego pokoju, co zapis
#: sąsiada), a ``InterviewSlot`` nie ma pola uuid i dokładanie go tylko po to byłoby migracją
#: tabeli po nic. Klucz główny jest stały przez całe życie wiersza, więc skrót z niego też.
SLOT_NAMESPACE = uuid.UUID("2f8f6a1e-5a36-4a3e-9a7e-0f5f0f2c1a44")

#: Sufiks pokoju do sprawdzenia sprzętu. Osobny pokój, a nie „strona testowa dostawcy”: Jitsi nie
#: ma osobnego adresu testu, a **wejście do dowolnego pokoju** zaczyna się właśnie od ekranu
#: z podglądem kamery i wyborem mikrofonu. Wchodząc do „…-test” uczestnik przechodzi dokładnie tę
#: samą ścieżkę uprawnień przeglądarki, co przed rozmową, i nikomu przy tym nie przeszkadza –
#: pokój jest pusty, bo nikt inny nie zna jego nazwy.
PRECHECK_SUFFIX = "-test"

#: Ile godzin przed rozmową wysyłamy przypomnienie. Doba: przebieg jest dzienny (patrz
#: ``apps.competitions.tasks.remind_interviews``), więc „jutro” jest najmniejszą jednostką, jaką
#: ten harmonogram umie obiecać, a przypomnienie na godzinę przed i tak przyszłoby za późno,
#: żeby zdążyć sprawdzić kamerę.
REMINDER_LEAD_HOURS = 24

#: Znaki niedozwolone w nazwie pokoju. Jitsi przyjmuje szeroki zestaw, ale nazwa trafia do URL-a
#: dyktowanego przez telefon – zostawiamy wyłącznie małe litery, cyfry i myślnik.
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


class VideoProvider(models.TextChoices):
    """Skąd bierze się pokój wideo etapu. Zamknięta lista, bo to decyzja organizatora, nie pole tekstowe."""

    NONE = "none", "bez wideo (link wpisuje koordynator)"
    JITSI = "jitsi", "Jitsi Meet"
    CUSTOM = "custom", "własna instancja"


def slugify_ascii(value: str, *, max_length: int = 24) -> str:
    """Oznaczenie edycji sprowadzone do bezpiecznego kawałka adresu, np. „XV (2026/2027)” → ``xv-2026-2027``.

    Własna, minimalna wersja zamiast ``django.utils.text.slugify``: ta ostatnia zostawia znaki
    spoza ASCII przy ``allow_unicode``, a bez niego gubi diakrytyki w sposób zależny od locale.
    Tutaj potrzebujemy wyniku **przewidywalnego**, bo wchodzi do adresu podawanego przez telefon.
    """
    folded = _SLUG_UNSAFE.sub("-", (value or "").lower()).strip("-")
    return folded[:max_length].strip("-") or "edycja"


def slot_prefix(slot) -> str:
    """Stały, krótki identyfikator terminu do nazwy pokoju (skrót ``uuid5`` z klucza głównego)."""
    return uuid.uuid5(SLOT_NAMESPACE, str(slot.pk)).hex[:SLOT_PREFIX_LENGTH]


def random_suffix() -> str:
    """Losowa część nazwy pokoju – jedyne, co czyni go prywatnym."""
    # ``_position`` zamiast ``_``: w tym module ``_`` jest aliasem ``gettext``.
    return "".join(secrets.choice(ROOM_ALPHABET) for _position in range(ROOM_RANDOM_LENGTH))


def room_name(stage, slot) -> str:
    """Nazwa pokoju: ``olimpiada-<edycja>-<termin>-<losowe>``. Każde wywołanie daje nowy sufiks."""
    return "-".join(
        ["olimpiada", slugify_ascii(stage.edition.year_label), slot_prefix(slot), random_suffix()]
    )


def build_meeting_url(stage, slot) -> str:
    """Adres nowego pokoju dla terminu albo pusty napis, gdy etap nie ma dostawcy wideo.

    Pusty wynik jest poprawnym stanem, a nie błędem: etap stacjonarny albo rozmowa telefoniczna
    żadnego pokoju nie potrzebuje, a ``video_provider = none`` to właśnie taki przypadek.
    """
    provider = stage.video_provider or VideoProvider.NONE
    if provider == VideoProvider.NONE:
        return ""
    base = (stage.video_base_url or DEFAULT_VIDEO_BASE_URL).strip()
    if not base:
        base = DEFAULT_VIDEO_BASE_URL
    return f"{base.rstrip('/')}/{room_name(stage, slot)}"


def meeting_url_for_slot(stage, slot) -> str:
    """Adres pokoju, do którego ma trafić **ten** termin – z zachowaniem trzech pierwszeństw.

    1. adres wpisany ręcznie przy terminie (``InterviewSlot.meeting_url``) wygrywa ze wszystkim:
       koordynator, który podał własny pokój, ma dostać własny pokój,
    2. adres już przypisany innemu zapisowi na tym samym terminie – żeby dwie osoby zapisane na
       ten sam termin zastały się nawzajem, a nie każda swój pusty pokój,
    3. dopiero na końcu nowy adres z dostawcy etapu.

    Import modelu jest lokalny, bo ``models`` importuje ten moduł przy definicji pola ``Stage``.
    """
    from .models import InterviewBooking

    if slot.meeting_url:
        return slot.meeting_url
    existing = (
        InterviewBooking.objects.filter(slot=slot)
        .exclude(meeting_url="")
        .values_list("meeting_url", flat=True)
        .first()
    )
    if existing:
        return existing
    return build_meeting_url(stage, slot)


def precheck_url(meeting_url: str) -> str:
    """Adres pustego pokoju „na próbę” – ten sam serwer, nazwa z sufiksem ``-test``.

    Dostawcy wideo nie mają osobnej „strony testu sprzętu”; testem jest ekran powitalny pokoju,
    na którym przeglądarka pyta o kamerę i mikrofon, a użytkownik widzi własny podgląd. Dlatego
    zamiast linkować cudzą stronę pomocy, otwieramy pokój, w którym na pewno nikogo nie ma.
    """
    cleaned = (meeting_url or "").strip()
    return f"{cleaned}{PRECHECK_SUFFIX}" if cleaned else ""


#: Krótka instrukcja przed rozmową. Jedno miejsce dla panelu i dla listu – inaczej zdanie
#: „sprawdź kamerę” istniałoby w dwóch brzmieniach i jedno z nich byłoby nieaktualne.
PRECHECK_TEXT = gettext_lazy(
    "Wejdź na rozmowę z komputera, w przeglądarce Chrome, Edge albo Firefox. "
    "Na kilka minut przed terminem otwórz link testowy i pozwól przeglądarce na dostęp do kamery "
    "i mikrofonu – to ten sam ekran, który zobaczysz przed wejściem do pokoju rozmowy."
)


def reminder_message(stage, booking) -> tuple[str, str]:
    """Temat i treść przypomnienia o jutrzejszej rozmowie. Bez danych osobowych – adresat wie, kim jest."""
    starts = timezone.localtime(booking.slot.starts_at)
    ends = timezone.localtime(booking.slot.ends_at)
    subject = _("Jutro rozmowa kwalifikacyjna: %(stage)s") % {"stage": stage.display_name}
    lines = [
        _("Przypominamy o jutrzejszej rozmowie kwalifikacyjnej (%(stage)s, %(edition)s).")
        % {"stage": stage.display_name, "edition": stage.edition.year_label},
        "",
        _("Termin: %(from)s – %(to)s (czas polski).")
        % {"from": f"{starts:%d.%m.%Y, %H:%M}", "to": f"{ends:%H:%M}"},
    ]
    if booking.slot.note:
        lines.append(_("Oznaczenie: %(note)s") % {"note": booking.slot.note})
    if booking.meeting_url:
        lines.append(_("Link do rozmowy: %(url)s") % {"url": booking.meeting_url})
        lines.append(_("Sprawdź kamerę i mikrofon: %(url)s") % {"url": precheck_url(booking.meeting_url)})
    lines += [
        "",
        str(PRECHECK_TEXT),
        "",
        "--",
        _("Olimpiada Kwantowa"),
        _("Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać."),
    ]
    return subject, "\n".join(lines)


def bookings_to_remind(now=None):
    """Zapisy, którym należy się przypomnienie: rozmowa w ciągu najbliższej doby, list jeszcze nie poszedł.

    Okno jest półotwarte ``[now, now + 24h)`` i nie sięga w przeszłość: o rozmowie, która już się
    zaczęła, nie ma po co przypominać. ``reminder_sent_at`` jest jedynym bezpiecznikiem przed
    drugim listem – przebieg jest dzienny, ale beat po restarcie potrafi puścić zadanie od razu.
    """
    from .models import InterviewBooking

    now = now or timezone.now()
    horizon = now + timedelta(hours=REMINDER_LEAD_HOURS)
    return (
        InterviewBooking.objects.filter(
            reminder_sent_at__isnull=True,
            slot__starts_at__gte=now,
            slot__starts_at__lt=horizon,
        )
        .select_related("slot", "slot__stage", "slot__stage__edition", "entry__participant__user")
        .order_by("slot__starts_at", "id")
    )


def send_interview_reminders(now=None) -> int:
    """Wysyła przypomnienia i znaczy zapisy. Zwraca liczbę listów przekazanych do kolejki.

    Znacznik stawiamy **przed** kolejkowaniem listu i zapisujemy go od razu: przy padzie workera
    wolimy jedno przypomnienie mniej niż dwa te same do tej samej osoby. Kolejka pocztowa ma
    własne ponowienia (``apps.core.tasks.send_mail_task``), więc ryzyko zgubienia listu jest małe.
    """
    from apps.accounts.activation import queue_mail
    from apps.accounts.preferences import language_for

    now = now or timezone.now()
    sent = 0
    for booking in bookings_to_remind(now):
        user = booking.entry.participant.user
        recipient = user.email
        if not recipient:
            continue
        booking.reminder_sent_at = now
        booking.save(update_fields=["reminder_sent_at"])
        # List powstaje w zadaniu w tle, więc żaden język nie jest „naturalnie” aktywny –
        # bierzemy ten zapisany na koncie odbiorcy (``apps.accounts.preferences``).
        with language_for(user):
            subject, message = reminder_message(booking.slot.stage, booking)
        queue_mail(subject, message, recipient)
        sent += 1
    if sent:
        logger.info("Wysłano %s przypomnień o jutrzejszych rozmowach kwalifikacyjnych.", sent)
    return sent
