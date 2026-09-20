"""Przekazywanie przyjętych rozwiązań na skrzynkę organizatora (prośba organizatora z 20.09.2026).

Po co to jest: komitet chce mieć każdą oddaną pracę **także w poczcie**, a nie wyłącznie w panelu –
bo listę zadań na dany dzień układa się w skrzynce, a nie w przeglądarce. Adresy są ustawieniem
**konkursu** (``tenancy.Competition.submission_forward_emails``), a puste pole znaczy „funkcja
wyłączona”: żaden konkurs nie zaczyna przekazywać prac przez samo wdrożenie.

Cztery reguły, na których to stoi:

- **przekazujemy wyłącznie plik po czystym skanie.** Wejściem jest ``apply_scan_verdict``, czyli
  dokładnie ta sama chwila, w której plik wolno pokazać komitetowi (``AvStatus.CLEAN``). Plik
  zainfekowany, plik z błędem skanu i plik jeszcze nieprzeskanowany nie wychodzą stąd nigdy –
  wysłanie zawartości, której antywirus nie obejrzał, byłoby doręczeniem wirusa do skrzynki
  organizatora naszymi rękami,
- **jeden plik to jeden list i tylko raz.** Pilnuje tego znacznik ``SubmissionFile.forwarded_at``,
  zakładany w transakcji z blokadą wiersza. Każda **nowa wersja** pracy jest nowym plikiem, więc
  jest przekazywana osobno – i o to właśnie chodzi, bo oceniana jest ostatnia wersja,
- **list nigdy nie wywraca przyjęcia pracy ani skanu.** Wysyłka jest zadaniem Celery na kolejce
  ``mail``, a jej zakolejkowanie jest w ``apply_scan_verdict`` opakowane w przechwycenie wyjątku:
  niedostępny MTA ma zostawić wpis w logu workera, a nie zgłoszenie zawieszone w ``SCANNING``,
- **załącznik ma granicę.** Powyżej ``SUBMISSION_FORWARD_MAX_ATTACHMENT_MB`` list idzie bez pliku,
  z jawnym zdaniem o tym w treści i z odnośnikiem do panelu. Bez tej granicy relay odrzucałby
  kopertę po fakcie, a organizator nie dostawałby **nic** – ani pliku, ani informacji.

Etapy treningowe (``StageKind.TRAINING``) są przekazywane **tak samo jak zawody** i jest to decyzja,
a nie przeoczenie: trening służy do przejścia całej ścieżki na prawdziwych zadaniach, więc
organizator, który sprawdza, czy przekazywanie w ogóle działa, ma to zobaczyć właśnie tam – zanim
ruszą eliminacje. Komu przeszkadza, ten odróżni te listy po nazwie etapu w temacie.

Zakres danych w treści jest świadomie wąski i jest to ta sama reguła, co w
``apps.submissions.notifications``: idzie tyle, ile potrzeba do rozpoznania pracy i przypisania jej
osobie (kod publiczny, imię i nazwisko, szkoła, etap, zadanie, wersja, czas, suma kontrolna). Nie
idą punkty, recenzje ani cokolwiek o ocenie – odbiorcą jest administrator danych swoich uczestników
(organizator), a nie strona trzecia, ale skrzynka pocztowa nadal nie jest kanałem zabezpieczonym.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.accounts.activation import absolute_url
from apps.core.models import audit
from apps.core.tasks import mail_from

from .models import AvStatus, SubmissionFile
from .packaging import anonymous_download_name
from .storage import get_submission_storage

logger = logging.getLogger(__name__)

#: Rodzaj powiadomienia w audycie – ta sama konwencja, co w ``apps.submissions.notifications``.
TYPE_SUBMISSION_FORWARDED = "submission.forwarded"

#: Wzorzec tematu. Trzy dane w jednym wierszu skrzynki, bo dokładnie po nich komitet szuka pracy:
#: etap, numer zadania i kod uczestnika. Nazwiska w temacie **nie ma** – temat bywa widoczny na
#: ekranie blokady telefonu i w podglądzie powiadomienia.
FORWARD_SUBJECT_TEMPLATE = "Nowe rozwiązanie: %(stage)s – zadanie %(number)s – %(code)s"


def subject_for(submission, competition=None) -> str:
    """Temat listu razem z prefiksem konkursu (``[Olimpiada Kwantowa] `` dla Konkursu #1).

    Prefiks doklejamy **tutaj**, a nie w wysyłce, i jest to jedyne miejsce w serwisie, które to
    robi. ``django.core.mail.send_mail`` prefiksu nigdy nie używał (robią to wyłącznie
    ``mail_admins``/``mail_managers``), więc żaden dotychczasowy temat się od tego nie zmienia –
    a ten list jest nowy i nie ma odbiorcy, któremu zmieniłaby się reguła w skrzynce. Organizator
    prosił wprost o prefiks w nawiasie kwadratowym, a ``Competition.email_subject_prefix`` jest
    polem, które tę wartość już trzyma; drugie pole na to samo byłoby drugim źródłem prawdy.
    """
    prefix = getattr(competition, "email_subject_prefix", "") or settings.EMAIL_SUBJECT_PREFIX
    stage = submission.entry.stage
    return prefix + (
        FORWARD_SUBJECT_TEMPLATE
        % {
            "stage": stage.display_name,
            "number": submission.problem.number,
            "code": submission.entry.participant.public_code,
        }
    )


def _participant_link(submission, competition=None) -> str:
    """Bezwzględny odnośnik do karty uczestnika w panelu koordynatora.

    Karta uczestnika, a nie „widok pracy”: pojedyncza wersja rozwiązania nie ma w panelu własnego
    adresu, a karta zbiera komplet – dane, wpisy do etapów, wszystkie wersje prac i ich oceny.
    Adres budujemy poza żądaniem (jesteśmy w workerze), więc domenę bierzemy z konkursu.

    ``NoReverseMatch`` nie może wywrócić listu: list z pracą jest wart więcej niż odnośnik do niej.
    """
    try:
        path = reverse("web:coordinator-participant", args=[submission.entry.participant_id])
    except NoReverseMatch:  # pragma: no cover - adres istnieje; zabezpieczenie na wypadek zmiany
        return ""
    return absolute_url(path, None, competition)


def _size_label(size_bytes: int) -> str:
    """Rozmiar pliku dla człowieka: bajty i megabajty naraz, bo oba bywają tu potrzebne."""
    return f"{size_bytes} B ({size_bytes / (1024 * 1024):.1f} MB)"


def max_attachment_bytes() -> int:
    """Granica załącznika w bajtach – czytana z ustawień przy każdym liście, nie przy imporcie.

    Stała modułu utrwalałaby wartość na czas życia procesu workera: zmiana ``.env`` działałaby
    dopiero po restarcie, a test nie miałby jak jej podmienić.
    """
    return int(settings.SUBMISSION_FORWARD_MAX_ATTACHMENT_MB) * 1024 * 1024


def forward_message_body(submission, submission_file, *, attached: bool, competition=None) -> str:
    """Treść listu do organizatora – metryczka pracy i odnośnik do panelu.

    ``attached`` rozstrzyga o jednym zdaniu i to zdanie musi tam być: list bez załącznika i bez
    wyjaśnienia wyglądałby jak list, z którego załącznik zginął po drodze.
    """
    stage = submission.entry.stage
    participant = submission.entry.participant
    user = participant.user
    link = _participant_link(submission, competition)
    lines = [
        "Uczestnik oddał rozwiązanie w serwisie olimpiady.",
        "",
        f"Konkurs: {competition.name if competition is not None else '—'}",
        f"Etap: {stage.display_name}",
        f"Zadanie: {submission.problem.number}. {submission.problem.title}",
        f"Wersja: {submission.version}",
        f"Czas przyjęcia (UTC): {submission.submitted_at.isoformat()}",
        "",
        f"Uczestnik: {participant.public_code}",
        f"Imię i nazwisko: {user.get_full_name() if user is not None else '—'}",
        f"Szkoła: {participant.school}",
        "",
        f"Plik: {submission_file.original_name}",
        f"Rozmiar: {_size_label(submission_file.size_bytes)}",
        f"Suma kontrolna (sha256): {submission_file.sha256}",
    ]
    if attached:
        lines += ["", "Plik jest w załączniku tej wiadomości."]
    else:
        lines += [
            "",
            "Plik jest za duży, żeby dołączyć go do wiadomości "
            f"(granica: {settings.SUBMISSION_FORWARD_MAX_ATTACHMENT_MB} MB). "
            "Pobierzesz go z panelu koordynatora.",
        ]
    if link:
        lines += ["", f"Karta uczestnika w panelu: {link}"]
    lines += [
        "",
        "--",
        "Wiadomość wysłana automatycznie przez serwis olimpiady; prosimy na nią nie odpowiadać.",
    ]
    return "\n".join(lines)


def _attachment(submission, submission_file) -> tuple[str, bytes, str] | None:
    """Treść pliku ze storage albo ``None``, gdy plik przekracza granicę załącznika.

    Czytamy przez tę samą abstrakcję, co pobieranie i paczki ZIP (``get_submission_storage``) –
    worker nie wie, czy po drugiej stronie stoi MinIO, czy katalog na dysku. Odczyt jest **w
    całości do pamięci** i to jest świadome: plik mieści się w granicy załącznika (domyślnie
    20 MB), a ``EmailMessage`` i tak potrzebuje bajtów naraz, żeby zakodować je base64.

    Nazwa załącznika jest budowana z kodu uczestnika, numeru zadania i wersji – ta sama, co przy
    pobraniu pojedynczej pracy z panelu (``packaging.anonymous_download_name``). Dzięki temu plik
    zapisany ze skrzynki i plik pobrany z panelu mają jedną nazwę i dają się zestawić; nazwa
    nadana przez uczestnika stoi w treści listu, gdzie nikomu niczego nie nadpisze.
    """
    if submission_file.size_bytes > max_attachment_bytes():
        return None
    stream = get_submission_storage().open(submission_file.object_key)
    try:
        content = stream.read()
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return anonymous_download_name(submission, submission_file), content, submission_file.mime


def _forwardable(submission_file: SubmissionFile) -> bool:
    """Czy ten plik nadaje się do przekazania: czysty skan i jeszcze nieprzekazany."""
    return submission_file.av_status == AvStatus.CLEAN and submission_file.forwarded_at is None


def enqueue(submission_file: SubmissionFile) -> bool:
    """Kolejkuje przekazanie pliku, o ile jest co i jest dokąd. Zwraca, czy zadanie poszło.

    Warunek konkursu sprawdzamy **tutaj**, a nie dopiero w zadaniu: konkurs bez ani jednego adresu
    to stan domyślny całej instalacji, a zadanie wystawiane dla każdego czystego skanu tylko po to,
    żeby natychmiast stwierdzić „nie ma dokąd”, byłoby ruchem na kolejce ``mail`` proporcjonalnym
    do liczby oddanych prac.
    """
    if not _forwardable(submission_file):
        return False
    competition = submission_file.submission.competition
    if not competition.forward_emails:
        return False

    from .tasks import forward_submission_file

    forward_submission_file.delay(submission_file.pk)
    return True


def forward_file(file_id: int) -> str:
    """Składa i wysyła jeden list z przekazanym rozwiązaniem. Zwraca kod wyniku dla logu i testów.

    Idempotencja jest wzięta z bazy, a nie z kolejki: wiersz pliku blokujemy ``SELECT … FOR
    UPDATE`` i ponownie sprawdzamy ``forwarded_at`` **wewnątrz** blokady. Bez tego dwa zadania
    z tym samym identyfikatorem (ponowienie, które w rzeczywistości się udało, plus jego
    następca) wysłałyby dwa listy z tym samym załącznikiem.

    Blokada obejmuje także samą wysyłkę i to jest wybór, nie niedopatrzenie: alternatywą jest
    oznaczyć plik przed wysłaniem, a wtedy awaria MTA zostawia pracę na zawsze „przekazaną”
    i nieprzekazaną naprawdę. Trzymamy więc jeden wiersz zablokowany przez czas rozmowy z relayem
    – na kolejce ``mail``, czyli tam, gdzie i tak nikt na ten wiersz nie czeka.
    """
    with transaction.atomic():
        submission_file = (
            SubmissionFile.objects.select_for_update(of=("self",))
            .select_related(
                "submission",
                "submission__competition",
                "submission__problem",
                "submission__entry",
                "submission__entry__stage",
                "submission__entry__participant",
                "submission__entry__participant__user",
            )
            .filter(pk=file_id)
            .first()
        )
        if submission_file is None:
            logger.warning("Przekazanie pominięte: SubmissionFile %s już nie istnieje.", file_id)
            return "MISSING"
        if not _forwardable(submission_file):
            logger.info(
                "Przekazanie pominięte: plik %s ma status skanu %s i znacznik %s.",
                file_id,
                submission_file.av_status,
                submission_file.forwarded_at,
            )
            return "SKIPPED"
        submission = submission_file.submission
        competition = submission.competition
        recipients = competition.forward_emails
        if not recipients:
            logger.info("Przekazanie pominięte: konkurs %s nie ma adresów.", competition.pk)
            return "NOT_CONFIGURED"

        attachment = _attachment(submission, submission_file)
        message = EmailMessage(
            subject=subject_for(submission, competition),
            body=forward_message_body(
                submission, submission_file, attached=attachment is not None, competition=competition
            ),
            from_email=mail_from(competition),
            to=recipients,
        )
        if attachment is not None:
            message.attach(*attachment)
        message.send(fail_silently=False)

        submission_file.forwarded_at = timezone.now()
        submission_file.save(update_fields=["forwarded_at"])
        # W audycie zostaje sam **rodzaj** zdarzenia i liczba odbiorców – ani adresów (są daną
        # osobową odbiorcy), ani nazwy pliku (bywa nazwiskiem). Ta sama reguła, co w
        # ``apps.submissions.notifications._send``.
        audit(
            None,
            "notification.sent",
            submission,
            {"type": TYPE_SUBMISSION_FORWARDED, "recipients": len(recipients)},
        )
    logger.info(
        "Przekazano plik %s rozwiązania %s na %s adresów (załącznik: %s).",
        file_id,
        submission.pk,
        len(recipients),
        attachment is not None,
    )
    return "SENT"
