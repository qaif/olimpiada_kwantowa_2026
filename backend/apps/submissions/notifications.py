"""Powiadomienia e-mail dla uczestnika: potwierdzenie wysyłki, werdykt skanu, wyniki, reklamacja.

Dlaczego jeden moduł na cztery zdarzenia z trzech aplikacji: to jest **jedna decyzja
organizatora** – „o czym piszemy uczestnikowi i czego mu nie piszemy” – i musi dać się przeczytać
w jednym miejscu. Rozsypana po serwisach rozjechałaby się co do tonu, co do zakresu danych
(potwierdzenie nie może nieść punktów) i co do momentu wysyłki. Serwisy domenowe wołają stąd
jedną funkcję i nie wiedzą nic o treści listu.

Zasady, wspólne z ``apps.accounts.activation``:

- list jest **skutkiem ubocznym** operacji, a nie jej warunkiem. Kolejkujemy przez
  ``transaction.on_commit`` (``activation.queue_mail``), więc niedostępny MTA nie zamienia
  przyjętego rozwiązania w błąd 500, a worker nie czyta wiersza, którego jeszcze nie ma w bazie,
- **milczenie jest domyślne**. Piszemy tylko wtedy, gdy zaszło coś, na co uczestnik ma
  zareagować albo co musi zachować jako dowód. Czysty skan jest ciszą: gdyby szedł list, każdy
  uczestnik dostawałby dwa listy za jedną wysyłkę, a pierwszy przestałby cokolwiek znaczyć,
- **do listu nie trafiają punkty ani cudze dane.** Potwierdzenie wysyłki niesie numer zadania,
  wersję, sumę kontrolną i czas; powiadomienie o wynikach niesie wyłącznie odnośnik do tabeli.
  Skrzynka pocztowa nie jest kanałem zabezpieczonym i nie ma w niej po co trzymać wyniku,
- **konto wyłączone nie dostaje poczty** (``User.is_active``). Konto nieaktywowane albo
  zablokowane przez koordynatora nie jest adresem, pod którym ktokolwiek czeka na wiadomość,
- w audycie zostaje sam **rodzaj** powiadomienia (``notification.sent``). Ani treść, ani adres:
  adres jest daną osobową, a treść da się odtworzyć z rodzaju i z obiektu.
"""

from __future__ import annotations

import logging

from django.urls import reverse

from apps.accounts.activation import absolute_url, queue_mail
from apps.core.models import audit

logger = logging.getLogger(__name__)

#: Rodzaje powiadomień – kody maszynowe do audytu. Nazwy są stabilne, bo po nich szuka się
#: w logu „czy uczestnik dostał list o decyzji komisji”.
TYPE_SUBMISSION_RECEIVED = "submission.received"
TYPE_SUBMISSION_INFECTED = "submission.infected"
TYPE_RESULTS_PUBLISHED = "results.published"
TYPE_APPEAL_DECIDED = "appeal.decided"

SUBMISSION_RECEIVED_SUBJECT = "Rozwiązanie przyjęte – Olimpiada Kwantowa"
SUBMISSION_INFECTED_SUBJECT = "Plik odrzucony przez skan antywirusowy – Olimpiada Kwantowa"
RESULTS_PUBLISHED_SUBJECT = "Wyniki etapu ogłoszone – Olimpiada Kwantowa"
APPEAL_DECIDED_SUBJECT = "Decyzja w sprawie reklamacji – Olimpiada Kwantowa"

#: Stopka każdego listu. Jedna, bo to ten sam nadawca i ta sama skrzynka bez odbioru.
SIGNATURE = (
    "--",
    "Olimpiada Kwantowa",
    "Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać.",
)


def _message(*lines: str) -> str:
    """Składa treść listu razem ze stopką – żeby żaden list nie wyszedł bez podpisu."""
    return "\n".join([*lines, "", *SIGNATURE])


def _recipient(user) -> str:
    """Adres, na który wolno pisać: konto musi istnieć, być aktywne i mieć adres."""
    if user is None or not user.is_active:
        return ""
    return (user.email or "").strip()


def _send(user, subject: str, message: str, *, kind: str, target) -> bool:
    """Kolejkuje jeden list i zostawia w audycie sam rodzaj powiadomienia.

    Zwraca, czy list poszedł – wartość jest dla testów i dla logu, nie dla przeglądarki.
    Audyt powstaje **tylko** przy faktycznej wysyłce: wpis „wysłano” przy koncie zablokowanym
    byłby śladem zdarzenia, które się nie odbyło.
    """
    recipient = _recipient(user)
    if not recipient:
        return False
    queue_mail(subject, message, recipient)
    audit(None, "notification.sent", target, {"type": kind})
    logger.info("Zakolejkowano powiadomienie %s dla obiektu %s.", kind, target.pk)
    return True


# --- (a) potwierdzenie przyjęcia rozwiązania ----------------------------------------------------


def submission_received_message(submission, submission_file) -> str:
    """Treść potwierdzenia: co, która wersja, kiedy i o jakiej sumie kontrolnej.

    Suma sha256 jest w liście celowo. To jedyny dowód, jaki uczestnik ma w ręku na to, **który**
    plik dotarł na serwer: gdyby kiedykolwiek spierał się o treść oddanej pracy, porównanie sumy
    z sumą pliku na jego dysku rozstrzyga sprawę bez wiary komukolwiek na słowo.
    """
    stage = submission.entry.stage
    return _message(
        f"Twoje rozwiązanie zostało przyjęte przez serwis Olimpiady Kwantowej ({stage.display_name}).",
        "",
        f"Zadanie: {submission.problem.number}. {submission.problem.title}",
        f"Wersja: {submission.version}",
        f"Czas przyjęcia (UTC): {submission.submitted_at.isoformat()}",
        f"Suma kontrolna pliku (sha256): {submission_file.sha256}",
        "",
        "Plik trafił na skan antywirusowy. Jeśli skan go odrzuci, dostaniesz osobną wiadomość –"
        " w przeciwnym razie nie piszemy nic więcej i ta wersja idzie do oceny.",
        "",
        "Każda kolejna wysyłka tworzy nową wersję; oceniana jest ostatnia.",
    )


def notify_submission_received(submission, submission_file) -> bool:
    """Potwierdzenie przyjęcia pracy. Woła je ``submissions.services.create_submission``."""
    return _send(
        submission.entry.participant.user,
        SUBMISSION_RECEIVED_SUBJECT,
        submission_received_message(submission, submission_file),
        kind=TYPE_SUBMISSION_RECEIVED,
        target=submission,
    )


# --- (b) werdykt skanu: wyłącznie plik odrzucony -------------------------------------------------


def submission_infected_message(submission, submission_file) -> str:
    """Treść listu o odrzuconym pliku – z jawnym „oddaj jeszcze raz”, póki etap jest otwarty."""
    stage = submission.entry.stage
    return _message(
        "Plik, który wysłałeś do serwisu Olimpiady Kwantowej, został odrzucony przez skan "
        "antywirusowy i nie wejdzie do oceniania.",
        "",
        f"Etap: {stage.display_name}",
        f"Zadanie: {submission.problem.number}. {submission.problem.title}",
        f"Wersja: {submission.version}",
        f"Suma kontrolna pliku (sha256): {submission_file.sha256}",
        "",
        "Najczęstszą przyczyną jest zainfekowany komputer albo plik pobrany z nieznanego źródła, "
        "a nie treść samego rozwiązania.",
        "",
        "Co zrobić: sprawdź komputer programem antywirusowym, wygeneruj plik ponownie i wyślij "
        "go jeszcze raz w panelu uczestnika. Liczy się ostatnia wersja przyjęta przed terminem.",
    )


def notify_submission_infected(submission, submission_file) -> bool:
    """List o odrzuceniu zainfekowanego pliku. Czysty skan jest cichy – patrz nagłówek modułu."""
    return _send(
        submission.entry.participant.user,
        SUBMISSION_INFECTED_SUBJECT,
        submission_infected_message(submission, submission_file),
        kind=TYPE_SUBMISSION_INFECTED,
        target=submission,
    )


# --- (c) ogłoszenie wyników etapu ---------------------------------------------------------------


def results_published_message(stage, link: str, feedback_link: str) -> str:
    """Treść listu o wynikach: dwa odnośniki i ani jednej liczby.

    Punktów w liście nie ma świadomie. Tabela jest w serwisie, za logowaniem albo pod pseudonimem
    – a list wędruje przez serwery, których nie kontrolujemy, i zostaje w skrzynce na lata.
    """
    return _message(
        f"Wyniki etapu „{stage.display_name}” ({stage.edition.year_label}) zostały ogłoszone.",
        "",
        f"Tabela wyników: {link}",
        f"Twoje punkty i komentarze recenzentów: {feedback_link}",
        "",
        "Jeżeli nie zgadzasz się z oceną, reklamację składa się w panelu uczestnika w oknie "
        "reklamacji wyznaczonym dla tego etapu.",
    )


def notify_results_published(publication, *, request=None) -> int:
    """Powiadamia wszystkich uczestników etapu o ogłoszeniu wyników. Zwraca liczbę listów.

    Woła to ``results.services.publish_results`` – po zapisie publikacji, wewnątrz jej transakcji.
    Listy idą przez ``queue_mail``, czyli po commicie: gdyby publikacja się wycofała, nikt nie
    dostanie wiadomości o tabeli, której nie ma.

    Ponowna publikacja tego samego etapu wysyła listy jeszcze raz i to jest zamierzone: ogłoszenie
    poprawionej tabeli jest nową informacją, a cisza po niej byłaby gorsza od powtórzonego listu.

    W audycie stoi jeden wpis na całe ogłoszenie (cel: publikacja), a nie wpis na uczestnika –
    tysiąc wierszy różniących się wyłącznie adresatem nie niesie nic ponad licznik, a każdy z nich
    wskazywałby konkretną osobę.
    """
    # Import lokalny: ``apps.competitions`` nie zależy od ``apps.submissions``, ale ten moduł
    # ładuje się przy rejestracji aplikacji i nie ma po co ciągnąć modeli zawodów na starcie.
    from apps.competitions.models import StageEntry

    stage = publication.stage
    link = absolute_url(reverse("web:results", args=[stage.pk]), request)
    feedback_link = absolute_url(reverse("web:participant-feedback", args=[stage.pk]), request)
    message = results_published_message(stage, link, feedback_link)
    sent = 0
    for entry in StageEntry.objects.filter(stage=stage).select_related("participant__user"):
        user = entry.participant.user
        recipient = _recipient(user)
        if not recipient:
            continue
        queue_mail(RESULTS_PUBLISHED_SUBJECT, message, recipient)
        sent += 1
    if sent:
        audit(None, "notification.sent", publication, {"type": TYPE_RESULTS_PUBLISHED})
    logger.info("Zakolejkowano %s powiadomień o wynikach etapu %s.", sent, stage.pk)
    return sent


# --- (d) decyzja w sprawie reklamacji ------------------------------------------------------------


def appeal_decided_message(appeal, decision, link: str) -> str:
    """Treść listu o rozstrzygnięciu reklamacji – z uzasadnieniem, bo to ono jest tu treścią.

    Nowa punktacja w liście **nie** stoi: decyzja bywa zmianą oceny w obie strony, a liczba
    wyrwana z kontekstu tabeli mówi mniej niż uzasadnienie. Panel pokazuje jedno i drugie.
    """
    submission = appeal.submission
    return _message(
        "Komisja odwoławcza rozstrzygnęła Twoją reklamację w Olimpiadzie Kwantowej.",
        "",
        f"Etap: {submission.entry.stage.display_name}",
        f"Zadanie: {submission.problem.number}. {submission.problem.title}",
        f"Rozstrzygnięcie: {appeal.get_status_display()}",
        "",
        "Uzasadnienie komisji:",
        decision.justification,
        "",
        f"Szczegóły i aktualną punktację znajdziesz w panelu uczestnika: {link}",
    )


def notify_appeal_decided(appeal, decision, *, request=None) -> bool:
    """List o decyzji komisji. Woła to ``appeals.services.decide_appeal``."""
    user = appeal.filed_by.user
    link = absolute_url(reverse("web:me"), request)
    return _send(
        user,
        APPEAL_DECIDED_SUBJECT,
        appeal_decided_message(appeal, decision, link),
        kind=TYPE_APPEAL_DECIDED,
        target=appeal,
    )
